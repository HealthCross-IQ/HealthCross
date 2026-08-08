from datetime import date

from app.finance.cash_flow import forecast_expenses, monthly_cash_flow


def test_monthly_cash_flow_groups_by_receive_month_and_accumulates():
    tracker_entries = [
        {"hc_payment_status": "Received", "payment_receive_date": date(2026, 3, 6), "total_value": 1000.0},
        {"hc_payment_status": "Received", "payment_receive_date": date(2026, 3, 20), "total_value": 500.0},
        {"hc_payment_status": "Received", "payment_receive_date": date(2026, 4, 6), "total_value": 200.0},
        # Not received - excluded
        {"hc_payment_status": "Outstanding", "payment_receive_date": None, "total_value": 999.0},
        # Wrong year - excluded
        {"hc_payment_status": "Received", "payment_receive_date": date(2025, 3, 6), "total_value": 999.0},
    ]
    expenses = [
        {"period": date(2026, 3, 1), "amount": 300.0},
        {"period": date(2026, 4, 1), "amount": 100.0},
    ]

    result = monthly_cash_flow(tracker_entries, expenses, 2026)

    by_period = {m["period"]: m for m in result["months"]}
    assert by_period["2026-03"]["inflow"] == 1500.0
    assert by_period["2026-03"]["outflow"] == 300.0
    assert by_period["2026-03"]["net"] == 1200.0
    assert by_period["2026-04"]["cumulative"] == 1200.0 + (200.0 - 100.0)
    assert result["total_inflow"] == 1700.0
    assert result["total_outflow"] == 400.0


def test_forecast_expenses_uses_actuals_then_run_rate_and_trailing_average():
    employees = [{"monthly_salary": 10000.0, "is_active": True}, {"monthly_salary": 5000.0, "is_active": False}]
    recurring = [
        {"default_amount": 5000.0, "expense_type": "fixed", "is_active": True},
        {"default_amount": None, "expense_type": "variable", "is_active": True},
    ]
    expenses = [
        {"period": date(2026, 5, 1), "amount": 200.0, "expense_type": "variable"},
        {"period": date(2026, 6, 1), "amount": 400.0, "expense_type": "variable"},
    ]

    result = forecast_expenses(expenses, employees, recurring, year=2026, as_of=date(2026, 7, 5), trailing_months=3)

    by_period = {m["period"]: m for m in result["months"]}
    # Inactive employee's salary is excluded from the fixed run-rate
    assert result["assumptions"]["fixed_monthly_run_rate"] == 15000.0
    # A month with real ExpenseEntry rows reports the actual, not the projection
    assert by_period["2026-05"]["is_actual"] is True
    assert by_period["2026-05"]["variable"] == 200.0
    # A month with no recorded rows projects fixed run-rate + trailing variable avg
    assert by_period["2026-08"]["is_actual"] is False
    assert by_period["2026-08"]["fixed"] == 15000.0
    assert by_period["2026-08"]["variable"] == result["assumptions"]["trailing_variable_monthly_avg"]
    assert result["assumptions"]["trailing_variable_monthly_avg"] == 300.0  # avg(200, 400)
