"""Monthly rate review - app/scoring/rules/rate_review.py."""
from datetime import date

import pytest

from app.scoring.rules.rate_review import (
    DEFAULT_PARAMETERS,
    apply_decisions,
    network_breakdown,
    parameters_with_defaults,
    relation_breakdown,
    review_cells,
    scope_members,
    snapshot_of,
    validate_against_snapshot,
)


def _m(age, gender, claims, premium=5000.0, network="MSH Comprehensive", product="Bronze",
       relation="employee", exposure=1.0, ibnr=0.0):
    return {
        "in_scope": True, "product": product, "network": network, "age": age, "gender": gender,
        "relation": relation, "actual_premium": premium * exposure, "actual_claims": claims,
        "ibnr": ibnr, "earned_premium_fraction": exposure,
    }


def _params(**over):
    p = parameters_with_defaults(None)
    p.update(over)
    return p


def _book():
    # 40 women 26-35 costing 8,000 each on 5,000 premium; 40 men costing
    # 2,000; a Platinum group of 30 at 12,000; four thin infants.
    rows = [_m(30, "F", 8000.0) for _ in range(40)]
    rows += [_m(30, "M", 2000.0) for _ in range(40)]
    rows += [_m(30, "M", 12000.0, premium=7000.0, network="MSH Platinum") for _ in range(30)]
    rows += [_m(0, "F", 9000.0, premium=3000.0) for _ in range(4)]
    return rows


def test_scope_excluding_drops_the_separate_network_and_only_keeps_it():
    book = _book()
    ex = scope_members(book, "Bronze", "excluding", None, ["MSH Platinum"])
    only = scope_members(book, "Bronze", "only", "MSH Platinum")
    assert len(ex) == 84 and all(r["network"] != "MSH Platinum" for r in ex)
    assert len(only) == 30


def test_a_credible_hot_cell_gets_an_increase_priced_to_target_and_capped():
    review = review_cells(_book(), "Bronze", _params(large_claim_cap=None))
    f = next(c for c in review["cells"] if c["age_band"] == "26-35" and c["gender"] == "F")
    assert f["lives"] == 40
    assert f["gross_loss_ratio"] == pytest.approx(1.6)
    # 40 member-years earns sqrt(0.4)=63% credibility; the rest comes
    # from the scope average, so suggested sits between own-cost and
    # scope-cost pricing - but well above the 5,000 charged.
    assert f["suggested_rate"] > 5000 * 1.5
    assert f["recommendation"] == "Increase"
    assert f["capped_change_pct"] <= 100.0
    assert "gross" in f["reason"] and "40 lives" in f["reason"]


def test_a_thin_cell_is_reviewed_not_acted_on():
    review = review_cells(_book(), "Bronze", _params())
    infant = next(c for c in review["cells"] if c["age_band"] == "0-1" and c["gender"] == "F")
    assert infant["lives"] == 4
    assert infant["thin"] is True
    assert infant["recommendation"] == "Review - thin data"
    assert infant["capped_change_pct"] is None


def test_the_large_claim_cap_pools_the_excess_without_hiding_the_real_loss_ratio():
    book = _book()
    book.append(_m(30, "M", 300000.0))  # one catastrophic male claim
    capped = review_cells(book, "Bronze", _params(large_claim_cap=100000.0))
    m = next(c for c in capped["cells"] if c["age_band"] == "26-35" and c["gender"] == "M")
    assert m["capped_members"] == 1
    # The real loss ratio still shows the whole claim...
    assert m["gross_loss_ratio"] == pytest.approx((40 * 2000 + 300000) / (41 * 5000), abs=1e-4)
    # ...while the cost the cell is priced on carries only the capped
    # part plus its share of the pooled excess.
    assert m["pricing_cost_pmpy"] < m["cost_pmpy"]
    assert capped["pooled_load_per_member_year"] == pytest.approx(200000 / 85, rel=1e-3)


def test_a_card_price_is_used_as_the_current_rate_when_the_band_matches():
    card = [{"product": "Bronze", "region": "Dubai", "network": "MSH Comprehensive", "tpa": "MSH",
             "from_age": 26, "to_age": 35, "male_price": 4500.0, "female_price": 6500.0}]
    review = review_cells(_book(), "Bronze", _params(), rate_cards=card)
    f = next(c for c in review["cells"] if c["age_band"] == "26-35" and c["gender"] == "F")
    assert f["card_price"] == 6500.0 and f["current_rate"] == 6500.0 and f["current_rate_basis"] == "card"
    other = next(c for c in review["cells"] if c["age_band"] == "0-1" and c["gender"] == "F")
    assert other["card_price"] is None and other["current_rate_basis"] == "earned premium"


def test_decisions_attach_to_their_cells_and_undecided_cells_say_so():
    review = review_cells(_book(), "Bronze", _params())
    decisions = [
        {"product": "Bronze", "network_scope": "excluding", "network": None, "from_age": 18, "to_age": 35,
         "gender": "F", "action": "increase", "change_pct": 100.0},
        {"product": "Bronze", "network_scope": "only", "network": "MSH Platinum", "from_age": 18, "to_age": 35,
         "gender": None, "action": "increase", "change_pct": 50.0},
    ]
    apply_decisions(review, decisions)
    f = next(c for c in review["cells"] if c["age_band"] == "26-35" and c["gender"] == "F")
    m = next(c for c in review["cells"] if c["age_band"] == "26-35" and c["gender"] == "M")
    assert f["decision_action"] == "increase" and f["decision_change_pct"] == 100.0
    assert f["gross_loss_ratio_after"] == pytest.approx(0.8)
    assert f["rate_after_decision"] == pytest.approx(10000.0)
    # The Platinum decision must not leak onto the excluding table.
    assert m["decision"] is None and m["decision_action"] == "No decision yet"
    assert review["totals"]["premium_after_decisions"] == pytest.approx(review["totals"]["premium"] + 40 * 5000)


def test_network_and_relation_breakdowns_report_the_factor_and_the_split():
    nets = network_breakdown(_book(), "Bronze", 0.265)
    by_name = {n["network"]: n for n in nets}
    assert by_name["MSH Platinum"]["cost_factor"] == pytest.approx(12000 / by_name["MSH Comprehensive"]["cost_pmpy"], rel=1e-3)
    rel = relation_breakdown(scope_members(_book(), "Bronze", "excluding", None, ["MSH Platinum"]), 0.265)
    assert {(r["relation"], r["gender"]) for r in rel} == {("employee", "F"), ("employee", "M")}


def test_validation_reads_this_month_as_a_movement_from_the_last_saved_review():
    params = _params()
    last_review = review_cells(_book(), "Bronze", params)
    last = snapshot_of(last_review, params, date(2026, 7, 31))
    last["data_as_of"] = "2026-07-31"

    grown = _book() + [_m(30, "F", 1000.0) for _ in range(30)]  # 30 cheap women joined
    review = review_cells(grown, "Bronze", params)
    v = validate_against_snapshot(review, last, params, date(2026, 8, 31), today=date(2026, 9, 5))
    assert v["lives_change"] == 30
    assert v["last_review"]["data_as_of"] == "2026-07-31"
    moves = {(m["age_band"], m["gender"]): m for m in v["material_moves"]}
    assert ("26-35", "F") in moves and moves[("26-35", "F")]["points"] < 0
    assert not v["warnings"]


def test_validation_warns_on_stale_or_unchanged_data_and_a_missing_history():
    params = _params()
    review = review_cells(_book(), "Bronze", params)
    v = validate_against_snapshot(review, None, params, date(2026, 6, 30), today=date(2026, 9, 5))
    assert any("days old" in w for w in v["warnings"])
    assert any("No earlier review" in w for w in v["warnings"])
    last = snapshot_of(review, params, date(2026, 8, 31))
    last["data_as_of"] = "2026-08-31"
    v2 = validate_against_snapshot(review, last, params, date(2026, 8, 31), today=date(2026, 9, 5))
    assert any("Same data" in w for w in v2["warnings"])


def test_defaults_fill_in_for_parameters_not_yet_stored():
    p = parameters_with_defaults({"target_loss_ratio": 0.9})
    assert p["target_loss_ratio"] == 0.9
    assert p["max_increase_pct"] == DEFAULT_PARAMETERS["max_increase_pct"]
    assert p["age_bands"] == DEFAULT_PARAMETERS["age_bands"]


def _nat(age, gender, claims, nationality, zone, premium=5000.0, n=1):
    return [dict(_m(age, gender, claims, premium=premium), nationality=nationality, nationality_zone=zone) for _ in range(n)]


def test_nationality_factors_redistribute_inside_the_cell_and_average_to_one():
    from app.scoring.rules.rate_review import nationality_factors
    # 40 Egyptian women costing 16,000 and 60 Indian women costing 6,000
    # in the same cell: cell cost 10,000; Egypt above it, India below.
    members = _nat(30, "F", 16000.0, "EGYPT", "zone_2_middle_east", n=40) + _nat(30, "F", 6000.0, "INDIA", "zone_1_asia", n=60)
    out = nationality_factors(members, _params(large_claim_cap=None))
    by = {r["nationality"]: r for r in out["nationalities"]}
    assert out["cell_cost_pmpy"] == pytest.approx(10000.0)
    assert by["Egypt"]["factor"] > 1.0 > by["India"]["factor"]
    assert by["Egypt"]["named"] and by["India"]["named"]
    # Exposure-weighted average of the factors is exactly 1 - the
    # decision on the cell is not added to a second time.
    weighted = sum(r["factor"] * r["member_years"] for r in out["nationalities"]) / sum(r["member_years"] for r in out["nationalities"])
    assert weighted == pytest.approx(1.0, abs=1e-3)


def test_a_thin_nationality_takes_its_zone_factor_rather_than_its_own():
    from app.scoring.rules.rate_review import nationality_factors
    members = _nat(30, "F", 6000.0, "INDIA", "zone_1_asia", n=60) + _nat(30, "F", 90000.0, "NEPAL", "zone_1_asia", n=2)
    out = nationality_factors(members, _params(large_claim_cap=None))
    by = {r["nationality"]: r for r in out["nationalities"]}
    zone = {z["zone"]: z for z in out["zones"]}
    assert by["Nepal"]["named"] is False
    assert by["Nepal"]["factor"] == pytest.approx(zone["zone_1_asia"]["factor"])


def test_reviewed_price_applies_the_decision_then_the_nationality_factor():
    from app.scoring.rules.rate_review import nationality_factors, reviewed_price_for_census
    params = _params(large_claim_cap=None)
    book = _nat(30, "F", 16000.0, "EGYPT", "zone_2_middle_east", n=40) + _nat(30, "F", 6000.0, "INDIA", "zone_1_asia", n=60)
    review = review_cells(book, "Bronze", params)
    apply_decisions(review, [{"product": "Bronze", "network_scope": "excluding", "network": None, "from_age": 26,
                              "to_age": 35, "gender": "F", "action": "increase", "change_pct": 100.0}])
    factors = {("excluding", "26-35", "F"): nationality_factors(book, params)}
    census = [{"age": 30, "gender": "F", "nationality": "EGYPT", "nationality_zone": "zone_2_middle_east", "network": "MSH Comprehensive", "category": "A"},
              {"age": 30, "gender": "F", "nationality": "INDIA", "nationality_zone": "zone_1_asia", "network": "MSH Comprehensive", "category": "A"},
              {"age": 50, "gender": "M", "nationality": "INDIA", "nationality_zone": "zone_1_asia", "network": "MSH Comprehensive", "category": "A"}]
    out = reviewed_price_for_census(census, {"excluding": review}, factors, params)
    egypt, india, man = out["members"]
    assert egypt["reviewed_rate"] == pytest.approx(10000.0)  # 5,000 x (1 + 100%)
    assert egypt["price"] > india["price"]
    assert egypt["nationality_factor"] > 1 > india["nationality_factor"]
    # Nobody aged 50 on this book: no cell, so not priced and said so.
    assert man["price"] is None and out["unmatched_member_count"] == 1
    assert out["priced_member_count"] == 2
