"""Monthly Exposed Risk Population (ERP) - the actuarial way of counting
"how many members were really covered" in a given calendar month, instead
of a flat headcount snapshot.

A census carries two distinct pairs of dates per member (see
app/ingestion/census.py): the scheme's own fixed policy_start_date/
policy_end_date (the same on every row) and each individual member's own
member_start_date/member_end_date, which falls short of the scheme's if
they joined late or left early. For each calendar month, each member
contributes the fraction of that month they were actually covered
(covered_days / days_in_month) rather than a flat 1 - summed across all
members, that gives the month's ERP, the correct denominator for a
per-member-per-month burning cost figure (claims that month / ERP that
month) instead of dividing by a static census count that ignores mid-term
joiners and leavers.
"""
import calendar
from datetime import date as _date
from typing import List, Optional, Tuple


def _month_bounds(year: int, month: int) -> Tuple[_date, _date]:
    last_day = calendar.monthrange(year, month)[1]
    return _date(year, month, 1), _date(year, month, last_day)


def _member_month_fraction(
    member_start: Optional[_date],
    member_end: Optional[_date],
    month_start: _date,
    month_end: _date,
) -> float:
    effective_start = max(member_start, month_start) if member_start else month_start
    effective_end = min(member_end, month_end) if member_end else month_end
    if effective_end < effective_start:
        return 0.0
    days_in_month = (month_end - month_start).days + 1
    covered_days = (effective_end - effective_start).days + 1
    return max(0.0, min(1.0, covered_days / days_in_month))


def _month_range(start: _date, end: _date) -> List[Tuple[int, int]]:
    months = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def monthly_exposed_risk_population(census: List[dict], policy_start: _date, policy_end: _date) -> List[dict]:
    """Returns [{year, month, erp}] for every calendar month in
    [policy_start, policy_end], where erp is the sum, across every census
    member, of that member's own covered-days-in-month / days-in-month
    (using their own member_start_date/member_end_date, falling back to
    the scheme's policy_start/policy_end when a member's own dates are
    missing - i.e. assumed covered for the full scheme term).
    """
    rows = []
    for year, month in _month_range(policy_start, policy_end):
        month_start, month_end = _month_bounds(year, month)
        erp = sum(
            _member_month_fraction(m.get("member_start_date"), m.get("member_end_date"), month_start, month_end)
            for m in census
        )
        rows.append({"year": year, "month": month, "erp": round(erp, 2)})
    return rows
