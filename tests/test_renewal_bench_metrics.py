import pytest

from app.scoring.rules.renewal_bench_metrics import (
    case_claim_kpis,
    census_change_pct_from_snapshots,
    existing_premium_breakdown,
    renewal_drivers,
)


def test_case_claim_kpis_computes_frequency_severity_and_claimant_ratio():
    claims = [{"patient_id": f"P{i % 5}", "final_amount": 5000} for i in range(10)]
    result = case_claim_kpis(claims, census_member_count=20, census_ages=[30, 40, 50], months_count=6)

    assert result["claim_count"] == 10
    assert result["distinct_claimants"] == 5
    # 10 claims over 6 observed months, annualized to 20/yr, over 20 members.
    assert result["claim_frequency"] == 1.0
    assert result["avg_claim_severity"] == 5000.0
    assert result["claimant_ratio"] == 0.25
    assert result["avg_member_age"] == 40.0


def test_case_claim_kpis_handles_zero_members_without_dividing_by_zero():
    claims = [{"patient_id": "P1", "final_amount": 5000}]
    result = case_claim_kpis(claims, census_member_count=0, census_ages=[], months_count=6)

    assert result["claim_frequency"] is None
    assert result["claimant_ratio"] is None
    assert result["avg_member_age"] is None
    assert result["avg_claim_severity"] == 5000.0  # doesn't depend on member count


def test_census_change_pct_from_snapshots_matches_the_census_movement_premium_impact():
    snapshots = [
        {"relation": "employee", "member_count": 98},
        {"relation": "spouse", "member_count": 24},
        {"relation": "child", "member_count": 30},
    ]
    current_counts = {"employee": 102, "spouse": 30, "child": 33}

    result = census_change_pct_from_snapshots(snapshots, current_counts, current_annual_premium=3_000_000)

    # avg premium/member = 3,000,000 / 152 = 19,736.84; net +13 members ->
    # +AED 256,578.9 impact -> /3,000,000 * 100 = 8.55%.
    assert result == 8.55


def test_census_change_pct_from_snapshots_returns_none_without_prior_snapshot():
    assert census_change_pct_from_snapshots([], {"employee": 10}, 3_000_000) is None


def test_census_change_pct_from_snapshots_returns_none_without_current_premium():
    snapshots = [{"relation": "employee", "member_count": 10}]
    assert census_change_pct_from_snapshots(snapshots, {"employee": 10}, None) is None


def _ladder(loss_ratio, expiring=500_000.0, loading=0.30, inflation=0.075, floor=None):
    from app.scoring.rules.renewal_rating import (
        MINIMUM_RENEWAL_INCREASE_PCT, renewal_from_loss_ratio,
    )
    return renewal_from_loss_ratio(
        loss_ratio, expiring, inflation, loading,
        minimum_increase_pct=MINIMUM_RENEWAL_INCREASE_PCT if floor is None else floor)


def test_renewal_drivers_are_a_decomposition_of_method_1_not_a_second_price():
    # The whole reason this function was rewritten. It used to multiply
    # the CLAIMS by inflation while the ladder adds inflation to the LOSS
    # RATIO in points, so the Renewal Bench hero and Method 1 quoted
    # different premiums for the same account on the same screen - and
    # the gap widened with the loss ratio, so the worse the account, the
    # further the headline drifted from what the house actually quotes.
    for loss_ratio in (0.40, 0.80, 1.30, 2.486):
        drivers = renewal_drivers(loss_ratio=loss_ratio, expiring_annual_premium=991_265.0,
                                  loading_pct=0.215)
        ladder = _ladder(loss_ratio, expiring=991_265.0, loading=0.215)
        assert drivers["recommended_premium"] == ladder["required_premium"]
        assert drivers["total_pct"] == pytest.approx(ladder["renewal_increase_pct"], abs=0.01)


def test_renewal_drivers_reconciles_exactly_to_total_pct():
    result = renewal_drivers(
        loss_ratio=0.784,           # 392,000 of claims against 500,000 of premium
        expiring_annual_premium=500_000,
        loading_pct=0.30,
        census_change_pct=4.3,
        underwriter_adjustment_pct=-2.0,
    )

    # Experience alone: 78.4% / 0.70 = 112.0% of expiring.
    assert result["claims_experience_pct"] == 12.0
    # With trend: (78.4% + 7.5) / 0.70 = 122.71%, so trend adds 10.71 -
    # NOT the 8.4 the old multiply-the-claims version reported.
    assert result["medical_trend_pct"] == 10.71
    assert result["census_change_pct"] == 4.3
    assert result["underwriter_adjustment_pct"] == -2.0

    # Every component must still sum to total_pct - the whole point of an
    # additive waterfall is that the reader can add it up.
    assert round(
        result["claims_experience_pct"]
        + result["medical_trend_pct"]
        + result["floor_pct"]
        + result["census_change_pct"]
        + result["underwriter_adjustment_pct"],
        2,
    ) == pytest.approx(result["total_pct"], abs=0.01)
    assert result["within_authority"] is True


def test_the_house_floor_gets_its_own_bar_rather_than_hiding_in_experience():
    # An account whose own experience asks for less than the floor. "This
    # account needs 9%" and "this account needs 2% and the house floor is
    # 9%" are different conversations, and one bar cannot tell them apart.
    result = renewal_drivers(loss_ratio=0.40, expiring_annual_premium=500_000,
                             loading_pct=0.30)
    assert result["floor_applied"] is True
    assert result["floor_pct"] > 0
    assert result["total_pct"] == pytest.approx(9.0, abs=0.01)
    # And the experience underneath it is still reported honestly.
    assert result["claims_experience_pct"] < 0


def test_a_floor_that_does_not_bite_contributes_nothing():
    result = renewal_drivers(loss_ratio=1.30, expiring_annual_premium=500_000,
                             loading_pct=0.30)
    assert result["floor_applied"] is False
    assert result["floor_pct"] == 0.0


def test_renewal_drivers_treats_missing_census_change_as_zero_but_keeps_it_reported_as_none():
    result = renewal_drivers(
        loss_ratio=0.784,
        expiring_annual_premium=500_000,
        loading_pct=0.30,
        census_change_pct=None,
        underwriter_adjustment_pct=0.0,
    )

    assert result["census_change_pct"] is None
    assert result["total_pct"] == pytest.approx(22.71, abs=0.01)


def test_renewal_drivers_flags_outside_authority_when_adjustment_exceeds_threshold():
    result = renewal_drivers(
        loss_ratio=0.784,
        expiring_annual_premium=500_000,
        loading_pct=0.30,
        underwriter_adjustment_pct=-20.0,
        authority_threshold_pct=15.0,
    )

    assert result["within_authority"] is False


def test_renewal_drivers_carry_the_method_1_figure_they_were_applied_to():
    # So a screen can show what the adjustments moved the price FROM.
    result = renewal_drivers(loss_ratio=1.30, expiring_annual_premium=500_000,
                             loading_pct=0.30, underwriter_adjustment_pct=-5.0)
    ladder = _ladder(1.30)
    assert result["method_1_required_premium"] == ladder["required_premium"]
    assert result["recommended_premium"] == pytest.approx(
        ladder["required_premium"] - 500_000 * 0.05, abs=0.01)


def test_renewal_drivers_rejects_non_positive_premium():
    with pytest.raises(ValueError):
        renewal_drivers(loss_ratio=0.80, expiring_annual_premium=0, loading_pct=0.30)


def test_renewal_drivers_rejects_loading_pct_out_of_range():
    with pytest.raises(ValueError):
        renewal_drivers(loss_ratio=0.80, expiring_annual_premium=500_000, loading_pct=1.0)


def test_existing_premium_breakdown_sums_rates_by_category():
    members = [
        {"category": "A", "existing_annual_rate": 10_000},
        {"category": "A", "existing_annual_rate": 12_000},
        {"category": "B", "existing_annual_rate": 8_000},
        {"category": "B", "existing_annual_rate": None},
        {"category": None, "existing_annual_rate": 5_000},
    ]
    result = existing_premium_breakdown(members)

    assert result["total_members"] == 5
    assert result["rated_members"] == 4
    assert result["coverage_pct"] == 80.0
    assert result["total_existing_premium"] == 35_000.0
    assert result["categories"] == [
        {"category": "A", "member_count": 2, "rated_member_count": 2, "total_premium": 22_000.0, "avg_rate": 11_000.0},
        {"category": "B", "member_count": 2, "rated_member_count": 1, "total_premium": 8_000.0, "avg_rate": 8_000.0},
        {"category": "Unspecified", "member_count": 1, "rated_member_count": 1, "total_premium": 5_000.0, "avg_rate": 5_000.0},
    ]


def test_existing_premium_breakdown_handles_empty_census():
    result = existing_premium_breakdown([])
    assert result["total_members"] == 0
    assert result["coverage_pct"] is None
    assert result["total_existing_premium"] == 0.0
    assert result["categories"] == []


def test_existing_premium_breakdown_handles_no_rates_set_at_all():
    members = [{"category": "A", "existing_annual_rate": None}, {"category": "A", "existing_annual_rate": None}]
    result = existing_premium_breakdown(members)
    assert result["rated_members"] == 0
    assert result["coverage_pct"] == 0.0
    assert result["total_existing_premium"] == 0.0
    assert result["categories"][0]["avg_rate"] is None
