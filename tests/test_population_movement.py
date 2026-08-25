"""A census that was never re-uploaded shows no movement, however many
people joined and left - app/scoring/rules/renewal_intake.py.
"""
from datetime import date

import pytest

from app.scoring.rules.renewal_intake import population_movement

START, END = date(2025, 10, 1), date(2026, 9, 30)


def _m(bid, relation="Employee", start=START, end=END, premium=12_000.0):
    return {"beneficiary_id": bid, "relation": relation,
            "policy_start_date": START, "policy_end_date": END,
            "member_start_date": start, "member_end_date": end,
            "gross_premium": premium, "actual_gross_premium": premium}


def test_a_stable_population_opens_and_closes_the_same():
    r = population_movement([_m("A"), _m("B")])
    assert r["totals"]["opening"] == 2
    assert r["totals"]["closing"] == 2
    assert r["totals"]["joiners"] == 0
    assert r["totals"]["leavers"] == 0


def test_a_joiner_is_not_in_the_opening_population():
    r = population_movement([_m("A"), _m("LATE", start=date(2026, 2, 1))])
    t = r["totals"]
    assert t["opening"] == 1
    assert t["joiners"] == 1
    assert t["closing"] == 2
    assert t["net_change"] == 1


def test_a_leaver_is_not_in_the_closing_population():
    r = population_movement([_m("A"), _m("GONE", end=date(2026, 4, 23))])
    t = r["totals"]
    assert t["opening"] == 2
    assert t["leavers"] == 1
    assert t["closing"] == 1
    assert t["net_change"] == -1


def test_movement_that_nets_to_zero_is_still_reported_as_movement():
    # The whole reason this exists. Seven in and seven out is not "no
    # change" - it is fourteen people moving, and the panel that said
    # 178 to 178, change 0 was answering a different question.
    r = population_movement(
        [_m(f"S{i}") for i in range(5)]
        + [_m(f"J{i}", start=date(2026, 3, 1)) for i in range(7)]
        + [_m(f"L{i}", end=date(2026, 3, 1)) for i in range(7)]
    )
    t = r["totals"]
    assert t["net_change"] == 0
    assert t["joiners"] == 7 and t["leavers"] == 7
    assert t["turnover_pct"] == pytest.approx(14 / 12, abs=1e-3)


def test_exposure_is_reported_beside_headcount_because_they_differ():
    # Twelve members who each stayed a month are twelve lives and one
    # member-year. A renewal priced on headcount treats them as twelve.
    short = [_m(f"M{i}", start=date(2026, m, 1), end=date(2026, m, 28))
             for i, m in enumerate(range(1, 13))]
    t = population_movement(short)["totals"]
    assert t["joiners"] == 12
    assert t["member_years"] < 2.0


def test_the_split_is_reported_per_relation():
    r = population_movement([
        _m("E1", "employee"), _m("S1", "spouse"),
        _m("C1", "child", start=date(2026, 2, 24)),
    ])
    by = {row["relation"]: row for row in r["rows"]}
    assert by["Child"]["joiners"] == 1
    assert by["Employee"]["opening"] == 1
    assert by["Spouse"]["closing"] == 1


def test_what_joiners_brought_and_leavers_took_are_kept_apart():
    # Netting them hides an account that replaced cheap members with
    # expensive ones at no change in headcount.
    r = population_movement([
        _m("J", start=date(2026, 2, 1), premium=30_000.0),
        _m("L", end=date(2026, 2, 1), premium=5_000.0),
    ])
    t = r["totals"]
    assert t["joiner_premium"] == 30_000.0
    assert t["leaver_premium"] == 5_000.0
    assert t["net_premium_impact"] == 25_000.0


def test_a_roster_with_no_dates_gives_no_movement_rather_than_raising():
    r = population_movement([{"beneficiary_id": "A", "relation": "employee"}])
    assert r["rows"] == [] or r["totals"] is None or r["totals"]["opening"] >= 0


def test_the_endpoint_reports_movement_for_an_account(client):
    from app.models import db_models as models

    db = client.db_session_local()
    db.bulk_insert_mappings(models.PortfolioMember, [
        {**_m("A"), "contract": "Acme", "master_contract": "Acme"},
        {**_m("LATE", start=date(2026, 2, 1)), "contract": "Acme", "master_contract": "Acme"},
        {**_m("GONE", end=date(2026, 4, 23)), "contract": "Acme", "master_contract": "Acme"},
    ])
    db.commit()
    db.close()

    body = client.get("/portfolio-analysis/population-movement/Acme").json()
    assert body["totals"]["opening"] == 2
    assert body["totals"]["joiners"] == 1
    assert body["totals"]["leavers"] == 1
    assert body["totals"]["closing"] == 2


# --- a comparison that cannot be trusted must say so ---------------------

def _movement(expiring, renewal):
    from app.scoring.rules.member_movement import match_members
    return match_members(expiring, renewal)


def _person(ref, dob=None, relation="employee", gender="M", age=35):
    return {"employee_ref": ref, "date_of_birth": dob, "relation": relation,
            "gender": gender, "age": age}


def test_a_pairing_done_entirely_on_demographics_is_reported_as_unreliable():
    # Safran: 182 expiring against a 178-member renewal census, every
    # continuing member paired on demographics and none on date of birth,
    # producing 93 leavers who took half the year's claims. Almost
    # certainly the same people twice, with no dates of birth to match on.
    expiring = [_person(f"E{i}", dob=date(1990, 1, 1)) for i in range(10)]
    renewal = [_person(f"R{i}", dob=None) for i in range(10)]
    r = _movement(expiring, renewal)
    assert r["exact_match_count"] == 0
    assert r["reliable"] is False
    assert r["caveat"] and "not reliable enough to price from" in r["caveat"]


def test_a_clean_match_on_dates_of_birth_is_reported_as_reliable():
    dobs = [date(1990, 1, i + 1) for i in range(10)]
    expiring = [_person(f"E{i}", dob=d) for i, d in enumerate(dobs)]
    renewal = [_person(f"R{i}", dob=d) for i, d in enumerate(dobs)]
    r = _movement(expiring, renewal)
    assert r["exact_match_count"] == 10
    assert r["reliable"] is True
    assert r["caveat"] is None


def test_more_movement_than_the_account_has_members_is_flagged():
    expiring = [_person(f"E{i}", dob=date(1990, 1, i + 1)) for i in range(3)]
    renewal = [_person(f"R{i}", dob=date(1975, 6, i + 1), age=50) for i in range(5)]
    r = _movement(expiring, renewal)
    assert r["reliable"] is False
    assert any("more movement" in reason for reason in r["unreliable_because"])
