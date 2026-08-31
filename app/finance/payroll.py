"""Pro-rates an Employee's monthly_salary for a calendar month they didn't
work in full - joined partway through (start_date falls inside the month),
left partway through (end_date falls inside the month), or both at once for
a very short tenure. A full month worked (or an employee with no
start_date/end_date recorded, since that's not enough to prorate against)
gets the full monthly_salary, unchanged.
"""
import calendar
from datetime import date
from typing import Optional


def prorated_monthly_salary(
    monthly_salary: float,
    period_start: date,
    start_date: Optional[date],
    end_date: Optional[date],
) -> dict:
    """`period_start` is the 1st of the target month (matching
    ExpenseEntry.period's own first-of-month convention). Returns the
    amount to pay for that month, plus how many of the month's days that
    covers - `is_prorated` is False (full month, unchanged amount) whenever
    the employee's own start/end dates don't further restrict them within
    this particular month.
    """
    days_in_month = calendar.monthrange(period_start.year, period_start.month)[1]
    period_end = date(period_start.year, period_start.month, days_in_month)

    work_start = max(period_start, start_date) if start_date else period_start
    work_end = min(period_end, end_date) if end_date else period_end

    if work_start > work_end:
        return {"amount": 0.0, "days_worked": 0, "days_in_month": days_in_month, "is_prorated": True}

    days_worked = (work_end - work_start).days + 1
    if days_worked >= days_in_month:
        return {"amount": round(monthly_salary, 2), "days_worked": days_in_month, "days_in_month": days_in_month, "is_prorated": False}

    amount = round((monthly_salary / days_in_month) * days_worked, 2)
    return {"amount": amount, "days_worked": days_worked, "days_in_month": days_in_month, "is_prorated": True}
