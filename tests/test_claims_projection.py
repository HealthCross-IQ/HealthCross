import pytest

from app.scoring.rules.claims_projection import (
    ClaimsProjectionAssumptions,
    project_annual_claims,
)


def test_project_annual_claims_matches_legrand_worked_example():
    # Oct-Mar (first 6 full months, excluding the Sep policy-inception stub)
    six_months = [203861, 216391, 175170, 502079, 157146, 155289]

    result = project_annual_claims(
        six_month_paid_claims=six_months,
        opening_members=161,
        closing_members=227,
        current_census_members=212,
    )

    assert result["avg_month"] == pytest.approx(234989.33, abs=1)
    assert result["annualized"] == pytest.approx(2819872, abs=1)
    assert result["with_ibnr"] == pytest.approx(3101859, abs=1)
    assert result["avg_report_members"] == 194.0
    assert result["burning_cost_per_member"] == pytest.approx(15988.96, abs=1)
    assert result["projected_current_group"] == pytest.approx(3389661, abs=10)
    assert result["trended"] == pytest.approx(3643885, abs=10)
    assert result["credible"] == pytest.approx(3279497, abs=10)
    assert result["final_projected_claims"] == pytest.approx(4554856, abs=50)


def test_project_annual_claims_requires_exactly_six_months():
    with pytest.raises(ValueError):
        project_annual_claims([1, 2, 3], 100, 100, 100)


def test_project_annual_claims_requires_positive_member_counts():
    six_months = [1000] * 6
    with pytest.raises(ValueError):
        project_annual_claims(six_months, 0, 100, 100)


def test_custom_assumptions_change_the_result():
    six_months = [100000] * 6
    default_result = project_annual_claims(six_months, 100, 100, 100)
    lighter_load = project_annual_claims(
        six_months, 100, 100, 100,
        assumptions=ClaimsProjectionAssumptions(loading_pct=0.10),
    )
    assert lighter_load["final_projected_claims"] < default_result["final_projected_claims"]
