from datetime import date

from app.finance.end_of_service import compute_end_of_service_gratuity


def test_less_than_one_year_not_eligible():
    result = compute_end_of_service_gratuity(date(2026, 1, 1), 10000.0, date(2026, 6, 1))
    assert result["is_eligible"] is False
    assert result["gratuity_amount"] == 0.0


def test_between_one_and_five_years_uses_21_day_tier():
    # Exactly 3 years of service at 9000/month basic salary.
    result = compute_end_of_service_gratuity(date(2023, 1, 1), 9000.0, date(2026, 1, 1))
    assert result["is_eligible"] is True
    assert result["years_of_service"] == 3.0027
    daily_wage = 9000.0 / 30.0
    expected = round(3.0027 * 21 * daily_wage, 2)
    assert result["gratuity_amount"] == expected


def test_beyond_five_years_blends_21_and_30_day_tiers():
    # 7 years of service: 5 years at 21 days, 2 years at 30 days.
    result = compute_end_of_service_gratuity(date(2019, 1, 1), 12000.0, date(2026, 1, 1))
    daily_wage = 12000.0 / 30.0
    expected = round(5.0 * 21 * daily_wage + (result["years_of_service"] - 5.0) * 30 * daily_wage, 2)
    assert result["gratuity_amount"] == expected


def test_gratuity_capped_at_two_years_salary():
    # An extreme tenure (30 years) at a low salary would otherwise exceed
    # the two-years-of-salary cap.
    result = compute_end_of_service_gratuity(date(1996, 1, 1), 3000.0, date(2026, 1, 1))
    assert result["gratuity_amount"] == 24 * 3000.0


def test_missing_start_date_or_salary_not_eligible():
    assert compute_end_of_service_gratuity(None, 10000.0, date(2026, 1, 1))["is_eligible"] is False
    assert compute_end_of_service_gratuity(date(2020, 1, 1), None, date(2026, 1, 1))["is_eligible"] is False


def test_calc_date_before_start_date_not_eligible():
    result = compute_end_of_service_gratuity(date(2026, 6, 1), 10000.0, date(2026, 1, 1))
    assert result["is_eligible"] is False
    assert result["gratuity_amount"] == 0.0
