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


def test_renewal_drivers_reconciles_exactly_to_total_pct():
    result = renewal_drivers(
        annualized_incurred_claims=392_000,
        trended_claims=392_000 * 1.075,
        current_annual_premium=500_000,
        loading_pct=0.30,
        census_change_pct=4.3,
        underwriter_adjustment_pct=-2.0,
    )

    assert result["claims_experience_pct"] == 12.0
    assert result["medical_trend_pct"] == 8.4
    assert result["census_change_pct"] == 4.3
    assert result["underwriter_adjustment_pct"] == -2.0
    assert result["total_pct"] == 22.7
    # The four components must sum exactly to total_pct - the whole point
    # of the additive design (a mockup-matching waterfall must reconcile).
    assert round(
        result["claims_experience_pct"]
        + result["medical_trend_pct"]
        + result["census_change_pct"]
        + result["underwriter_adjustment_pct"],
        2,
    ) == result["total_pct"]
    assert result["recommended_premium"] == 613_500.0
    assert result["within_authority"] is True


def test_renewal_drivers_treats_missing_census_change_as_zero_but_keeps_it_reported_as_none():
    result = renewal_drivers(
        annualized_incurred_claims=392_000,
        trended_claims=392_000 * 1.075,
        current_annual_premium=500_000,
        loading_pct=0.30,
        census_change_pct=None,
        underwriter_adjustment_pct=0.0,
    )

    assert result["census_change_pct"] is None
    assert result["total_pct"] == 20.4  # claims_experience + medical_trend only


def test_renewal_drivers_flags_outside_authority_when_adjustment_exceeds_threshold():
    result = renewal_drivers(
        annualized_incurred_claims=392_000,
        trended_claims=392_000 * 1.075,
        current_annual_premium=500_000,
        loading_pct=0.30,
        underwriter_adjustment_pct=-20.0,
        authority_threshold_pct=15.0,
    )

    assert result["within_authority"] is False


def test_renewal_drivers_rejects_non_positive_premium():
    import pytest

    with pytest.raises(ValueError):
        renewal_drivers(100_000, 107_500, 0, 0.30)


def test_renewal_drivers_rejects_loading_pct_out_of_range():
    import pytest

    with pytest.raises(ValueError):
        renewal_drivers(100_000, 107_500, 500_000, 1.0)


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
