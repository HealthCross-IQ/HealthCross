from app.scoring.rules.renewal_bench_dashboard import (
    age_threshold_percentages,
    per_member_premium,
    renewal_increase_reason,
)


def test_per_member_premium_divides_and_handles_missing_inputs():
    assert per_member_premium(4501303, 144) == round(4501303 / 144, 2)
    assert per_member_premium(None, 144) is None
    assert per_member_premium(4501303, None) is None
    assert per_member_premium(4501303, 0) is None


def test_age_threshold_percentages_computes_over_50_60_and_ratio():
    census = (
        [{"age": 30, "gender": "M"}] * 6
        + [{"age": 55, "gender": "F"}] * 3
        + [{"age": 65, "gender": "M"}] * 1
    )
    result = age_threshold_percentages(census)

    assert result["pct_over_50"] == round(4 / 10, 4)
    assert result["pct_over_60"] == round(1 / 10, 4)
    assert result["male_count"] == 7
    assert result["female_count"] == 3
    assert result["male_female_ratio"] == "7:3"


def test_age_threshold_percentages_all_one_gender():
    census = [{"age": 40, "gender": "M"}] * 5
    result = age_threshold_percentages(census)
    assert result["male_female_ratio"] == "5:0"
    assert result["female_count"] == 0


def test_age_threshold_percentages_empty_census():
    result = age_threshold_percentages([])
    assert result["pct_over_50"] is None
    assert result["male_female_ratio"] is None


def test_renewal_increase_reason_mentions_experience_inflation_and_loading():
    drivers = {"claims_experience_pct": 226.2, "inflation_pts": 0.075, "floor_applied": False}
    reason = renewal_increase_reason(drivers)
    assert "claims experience (+226.2%)" in reason
    assert "claim inflation (7.5%)" in reason
    assert "expense & risk loading" in reason


def test_renewal_increase_reason_mentions_house_floor_when_applied():
    drivers = {"claims_experience_pct": 2.0, "inflation_pts": 0.075, "floor_applied": True}
    reason = renewal_increase_reason(drivers)
    assert "house minimum renewal increase" in reason
    assert "expense & risk loading" not in reason
