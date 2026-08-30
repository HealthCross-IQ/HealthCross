from datetime import date

from app.finance.payroll import prorated_monthly_salary


def test_prorated_monthly_salary_mid_month_joiner():
    # August 2026 has 31 days; joined the 24th - worked the 24th through the
    # 31st inclusive, 8 days.
    result = prorated_monthly_salary(40000.0, date(2026, 8, 1), date(2026, 8, 24), None)
    assert result["days_in_month"] == 31
    assert result["days_worked"] == 8
    assert result["is_prorated"] is True
    assert result["amount"] == round((40000.0 / 31) * 8, 2)


def test_prorated_monthly_salary_full_month_no_dates():
    result = prorated_monthly_salary(10000.0, date(2026, 8, 1), None, None)
    assert result["amount"] == 10000.0
    assert result["is_prorated"] is False


def test_prorated_monthly_salary_full_month_started_earlier():
    result = prorated_monthly_salary(10000.0, date(2026, 8, 1), date(2020, 1, 1), None)
    assert result["amount"] == 10000.0
    assert result["is_prorated"] is False


def test_prorated_monthly_salary_mid_month_leaver():
    # Left on the 10th - worked the 1st through the 10th inclusive, 10 days.
    result = prorated_monthly_salary(31000.0, date(2026, 8, 1), date(2020, 1, 1), date(2026, 8, 10))
    assert result["days_worked"] == 10
    assert result["amount"] == round((31000.0 / 31) * 10, 2)


def test_prorated_monthly_salary_joined_and_left_same_month():
    result = prorated_monthly_salary(31000.0, date(2026, 8, 1), date(2026, 8, 5), date(2026, 8, 15))
    assert result["days_worked"] == 11  # 5th through 15th inclusive
    assert result["amount"] == round((31000.0 / 31) * 11, 2)


def test_prorated_monthly_salary_not_employed_that_month():
    # Started the following month - no overlap with August at all.
    result = prorated_monthly_salary(10000.0, date(2026, 8, 1), date(2026, 9, 1), None)
    assert result["amount"] == 0.0
    assert result["days_worked"] == 0

    # Already left before August started.
    result = prorated_monthly_salary(10000.0, date(2026, 8, 1), date(2020, 1, 1), date(2026, 7, 31))
    assert result["amount"] == 0.0
