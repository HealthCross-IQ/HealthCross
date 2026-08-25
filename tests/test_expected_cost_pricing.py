"""Expected-cost pricing - app/scoring/rules/expected_cost_pricing.py.
Pricing from what members cost, not from a 0-100 score.
"""
import pytest

from app.scoring.rules.burning_cost_cube import burning_cost_cube
from app.scoring.rules.expected_cost_pricing import (
    DEFAULT_TREND_PCT,
    price_by_category,
    price_census_at_expected_cost,
    renewal_premium_from_experience,
)

RATE_CARDS = [
    {"product_name": "Gold", "from_age": 0, "to_age": 17, "male_price": 1000, "female_price": 1000},
    {"product_name": "Gold", "from_age": 18, "to_age": 45, "male_price": 2000, "female_price": 2500},
]


def _r(claims, exposure=1.0, **over):
    base = {
        "in_scope": True, "product": "Gold", "network": "GN", "age": 30, "gender": "M",
        "relation": "employee", "nationality_zone": "Zone 3",
        "actual_claims": claims, "earned_premium_fraction": exposure,
    }
    base.update(over)
    return base


@pytest.fixture()
def cube():
    # 200 identical members at AED 2,000 - fully credible, so the cell
    # prices at its own experience with no blending to reason around.
    return burning_cost_cube([_r(2000.0) for _ in range(200)], RATE_CARDS)


def test_loading_is_a_gross_up_not_a_markup(cube):
    # premium x (1 - loading) is what funds claims, so the premium that
    # funds 2000 at 33% is 2000/0.67 = 2985, not 2000 x 1.33 = 2660.
    # Marking up under-collects, and by more the larger the loading.
    priced = price_census_at_expected_cost([_r(0)], cube, trend_pct=0.0, loading_pct=0.33)
    assert priced["risk_premium"] == pytest.approx(2000.0)
    assert priced["gross_premium"] == pytest.approx(2985.07, abs=0.01)
    assert priced["gross_premium"] * (1 - 0.33) == pytest.approx(2000.0, abs=0.01)


def test_trend_lifts_the_price_off_historic_cost(cube):
    priced = price_census_at_expected_cost([_r(0)], cube, trend_pct=0.10, loading_pct=0.0)
    assert priced["risk_premium"] == pytest.approx(2200.0)


def test_industry_does_not_move_the_price_while_the_factor_is_switched_off(cube):
    # The multipliers were opening guesses, and the two they were most
    # confidently wrong about - education and professional services,
    # priced at a discount - are the two the underwriting view calls
    # risky. A factor that moves the price the wrong way is worse than
    # no factor, so it rates neutral until there are real numbers.
    priced = price_census_at_expected_cost(
        [_r(0)], cube, industry="construction", trend_pct=0.0, loading_pct=0.5
    )
    assert priced["industry_factor"] == 1.0
    assert priced["risk_premium"] == pytest.approx(2000.0)
    assert priced["gross_premium"] == pytest.approx(4000.0)


def test_the_industry_arithmetic_still_works_when_it_is_switched_back_on(cube, monkeypatch):
    # Switching the factor off must not quietly rot the code that
    # applies it - the table is meant to come back once there are house
    # numbers for it. It applies before the gross-up, so the expense
    # loading is charged on the loaded risk rather than the bare one.
    monkeypatch.setattr("app.scoring.rules.industry.INDUSTRY_RATING_ENABLED", True)
    priced = price_census_at_expected_cost(
        [_r(0)], cube, industry="construction", trend_pct=0.0, loading_pct=0.5
    )
    assert priced["industry_factor"] == 1.35
    assert priced["risk_premium"] == pytest.approx(2700.0)
    assert priced["gross_premium"] == pytest.approx(5400.0)


def test_non_recurring_claims_come_out_of_the_base(cube):
    priced = price_census_at_expected_cost(
        [_r(0), _r(0)], cube, trend_pct=0.0, loading_pct=0.0, non_recurring_claims=1500.0
    )
    assert priced["expected_claims"] == pytest.approx(4000.0)
    assert priced["risk_premium"] == pytest.approx(2500.0)


def test_non_recurring_larger_than_the_base_floors_at_zero_not_negative(cube):
    priced = price_census_at_expected_cost(
        [_r(0)], cube, trend_pct=0.0, loading_pct=0.0, non_recurring_claims=99_999.0
    )
    assert priced["risk_premium"] == 0.0


def test_the_build_up_names_every_step_that_moved_the_price(cube, monkeypatch):
    monkeypatch.setattr("app.scoring.rules.industry.INDUSTRY_RATING_ENABLED", True)
    priced = price_census_at_expected_cost(
        [_r(0)], cube, industry="construction", trend_pct=0.10, loading_pct=0.33
    )
    labels = [s["label"] for s in priced["build_up"]]
    assert labels[0].startswith("Expected claims")
    assert any("Industry" in l for l in labels)
    assert any("trend" in l.lower() for l in labels)
    assert labels[-1].startswith("Gross up")
    # Each step carries the running total, so the last one is the price.
    assert priced["build_up"][-1]["amount"] == priced["gross_premium"]


def test_a_step_that_changes_nothing_is_left_out_of_the_build_up(cube):
    # Which is also why a switched-off industry leaves no trace on the
    # build-up: the price sheet should not carry a line that did nothing.
    priced = price_census_at_expected_cost(
        [_r(0)], cube, industry="construction", trend_pct=0.10, loading_pct=0.0
    )
    labels = [s["label"] for s in priced["build_up"]]
    assert not any("Industry" in l for l in labels)
    assert not any("non-recurring" in l.lower() for l in labels)


def test_book_relativity_replaces_the_score_with_a_statement_about_money(cube):
    # A case of book-average members prices at 1.0; a costlier mix above it.
    average = price_census_at_expected_cost([_r(0)], cube, trend_pct=0.0, loading_pct=0.0)
    assert average["book_relativity"] == pytest.approx(1.0, abs=1e-3)


def test_a_loading_of_one_hundred_percent_is_refused(cube):
    with pytest.raises(ValueError):
        price_census_at_expected_cost([_r(0)], cube, loading_pct=1.0)


def test_credibility_and_fallbacks_are_reported_with_the_price(cube):
    priced = price_census_at_expected_cost(
        [_r(0), _r(0, nationality_zone="Zone 9")], cube, trend_pct=0.0, loading_pct=0.0
    )
    assert priced["fallback_member_count"] == 1
    assert 0.0 < priced["weighted_credibility"] <= 1.0


def test_category_pricing_adds_back_to_the_case_total(cube):
    census = [_r(0, category="A"), _r(0, category="A"), _r(0, category="B")]
    result = price_by_category(cube=cube, census=census, default_loading_pct=0.25, trend_pct=0.0)
    assert [c["category"] for c in result["categories"]] == ["A", "B"]
    assert sum(c["gross_premium"] for c in result["categories"]) == pytest.approx(result["case_gross_premium"])
    assert result["member_count"] == 3


def test_members_with_no_category_are_priced_not_dropped(cube):
    result = price_by_category(cube=cube, census=[_r(0)], default_loading_pct=0.0, trend_pct=0.0)
    assert result["categories"][0]["category"] == "Unspecified"
    assert result["case_gross_premium"] > 0


# --- renewal pricing from an account's own experience -------------------

def test_renewal_price_builds_from_continuing_claims_only():
    # The BIC BRED shape: continuing members' incurred, IBNR, a completed
    # maternity out, a maternity provision back in, trend, then loading.
    priced = renewal_premium_from_experience(
        continuing_incurred=152_694.0,
        elapsed_days=359,
        loading_pct=0.33,
        trend_pct=0.10,
        non_recurring_claims=38_203.0,
        forward_provision=10_000.0,
        member_count=18,
    )
    assert priced["risk_premium"] == pytest.approx(147_463.7, abs=1.0)
    assert priced["gross_premium"] == pytest.approx(220_095.0, abs=2.0)
    assert priced["premium_per_member"] == pytest.approx(12_227.5, abs=1.0)


def test_the_two_judgement_inputs_stay_separate_lines_rather_than_netting():
    # Netting a 38,203 removal against a 10,000 provision into one
    # -28,203 adjustment gives the same total and hides the reasoning.
    priced = renewal_premium_from_experience(
        continuing_incurred=100_000.0, loading_pct=0.0, trend_pct=0.0,
        non_recurring_claims=38_203.0, forward_provision=10_000.0,
    )
    labels = [s["label"] for s in priced["build_up"]]
    assert "Less non-recurring" in labels
    assert "Forward provision" in labels
    assert priced["risk_premium"] == pytest.approx(71_797.0)


def test_renewal_price_with_no_adjustments_is_just_trend_and_loading():
    priced = renewal_premium_from_experience(
        continuing_incurred=100_000.0, loading_pct=0.2, trend_pct=0.0
    )
    assert priced["gross_premium"] == pytest.approx(125_000.0)
    assert [s["label"] for s in priced["build_up"]] == [
        "Continuing members' incurred claims", "Medical trend", "Gross up for loading",
    ]


def test_default_trend_is_exposed_rather_than_hidden():
    assert DEFAULT_TREND_PCT == 0.10
    priced = renewal_premium_from_experience(continuing_incurred=100_000.0, loading_pct=0.0)
    assert priced["trend_pct"] == 0.10
    assert priced["risk_premium"] == pytest.approx(110_000.0)
