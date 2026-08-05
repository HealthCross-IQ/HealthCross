"""Two computed finance reports, both pure functions over plain dicts (see
app/finance/reconciliation.py for the same convention and why):

1. monthly_cash_flow - actual monthly HC-fee inflow (PaymentTrackerEntry
   rows HC has actually collected) vs. actual monthly outflow
   (ExpenseEntry rows), for a given calendar year.
2. forecast_expenses - a month with real ExpenseEntry rows reports its
   actuals as-is; a month with none projects fixed costs from the active
   Employee/RecurringExpense run-rate and variable costs from a trailing
   average of recent actuals - documented, explainable assumptions rather
   than a black-box projection, matching how the rest of this codebase
   (e.g. claims_projection.py's burning-cost method) always states its
   forecasting method plainly.
"""
from collections import defaultdict
from datetime import date
from typing import Dict, List

MONTHS = [f"{m:02d}" for m in range(1, 13)]


def _period_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def monthly_cash_flow(tracker_entries: List[dict], expense_entries: List[dict], year: int) -> dict:
    """Inflow is grouped by payment_receive_date's month, not
    invoice_raised_period - the latter is free text (e.g. "Raised Mar 26")
    and not reliably parseable to a calendar month.
    """
    inflow_by_month: Dict[str, float] = defaultdict(float)
    for entry in tracker_entries:
        status = (entry.get("hc_payment_status") or "").strip().lower()
        receive_date = entry.get("payment_receive_date")
        if not status.startswith("received") or receive_date is None or receive_date.year != year:
            continue
        inflow_by_month[_period_key(receive_date)] += entry.get("total_value") or 0

    outflow_by_month: Dict[str, float] = defaultdict(float)
    for expense in expense_entries:
        period = expense.get("period")
        if period is None or period.year != year:
            continue
        outflow_by_month[_period_key(period)] += expense.get("amount") or 0

    months = []
    cumulative = 0.0
    total_inflow = total_outflow = 0.0
    for m in MONTHS:
        key = f"{year}-{m}"
        inflow = round(inflow_by_month.get(key, 0.0), 2)
        outflow = round(outflow_by_month.get(key, 0.0), 2)
        net = round(inflow - outflow, 2)
        cumulative = round(cumulative + net, 2)
        total_inflow += inflow
        total_outflow += outflow
        months.append({"period": key, "inflow": inflow, "outflow": outflow, "net": net, "cumulative": cumulative})

    return {
        "year": year,
        "months": months,
        "total_inflow": round(total_inflow, 2),
        "total_outflow": round(total_outflow, 2),
        "total_net": round(total_inflow - total_outflow, 2),
    }


def forecast_expenses(
    expense_entries: List[dict],
    employees: List[dict],
    recurring_expenses: List[dict],
    year: int,
    as_of: date,
    trailing_months: int = 3,
) -> dict:
    actual_by_month: Dict[str, Dict[str, float]] = defaultdict(lambda: {"fixed": 0.0, "variable": 0.0})
    for expense in expense_entries:
        period = expense.get("period")
        if period is None:
            continue
        bucket = "variable" if expense.get("expense_type") == "variable" else "fixed"
        actual_by_month[_period_key(period)][bucket] += expense.get("amount") or 0

    fixed_run_rate = sum(e.get("monthly_salary") or 0 for e in employees if e.get("is_active", True))
    fixed_run_rate += sum(
        r.get("default_amount") or 0
        for r in recurring_expenses
        if r.get("is_active", True) and r.get("expense_type") == "fixed"
    )

    # Trailing average of variable spend from actual months strictly before
    # `as_of`, most-recent-first, so the average never looks ahead at the
    # very month it's being used to forecast.
    variable_history = []
    for m in range(1, 13):
        month_date = date(year, m, 1)
        if month_date >= date(as_of.year, as_of.month, 1):
            break
        key = _period_key(month_date)
        if key in actual_by_month:
            variable_history.append(actual_by_month[key]["variable"])
    recent = variable_history[-trailing_months:]
    trailing_variable = round(sum(recent) / len(recent), 2) if recent else 0.0

    months = []
    total_actual = total_forecast = 0.0
    for m in range(1, 13):
        key = f"{year}-{m:02d}"
        is_actual = key in actual_by_month
        if is_actual:
            fixed = round(actual_by_month[key]["fixed"], 2)
            variable = round(actual_by_month[key]["variable"], 2)
        else:
            fixed = round(fixed_run_rate, 2)
            variable = trailing_variable
        total = round(fixed + variable, 2)
        if is_actual:
            total_actual += total
        else:
            total_forecast += total
        months.append({"period": key, "fixed": fixed, "variable": variable, "total": total, "is_actual": is_actual})

    return {
        "year": year,
        "months": months,
        "total_actual": round(total_actual, 2),
        "total_forecast": round(total_forecast, 2),
        "assumptions": {
            "fixed_monthly_run_rate": round(fixed_run_rate, 2),
            "trailing_variable_monthly_avg": trailing_variable,
            "trailing_months_used": len(recent),
            "as_of": as_of.isoformat(),
        },
    }
