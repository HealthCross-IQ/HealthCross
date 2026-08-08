"""UAE end-of-service gratuity (EOSB) for HC's payroll roster.

Rules follow UAE Federal Decree-Law No. 33 of 2021 (effective Feb 2022),
which unified gratuity entitlement for limited and unlimited contracts: an
employee who completes at least one year of continuous service is entitled
to 21 days' basic salary per year of service for the first five years, and
30 days' basic salary per year beyond that, pro-rated for any partial final
year, capped at two years' total salary.

`monthly_salary` on the Employee roster is treated as the basic salary for
this calculation - if HC's payroll figure includes allowances, the legal
basic-salary component may differ and the estimate should be adjusted
accordingly.
"""
from datetime import date
from typing import Optional

DAYS_PER_YEAR_TIER1 = 21  # years 1-5 of service
DAYS_PER_YEAR_TIER2 = 30  # years beyond 5
GRATUITY_CAP_MONTHS_OF_SALARY = 24  # two years' total salary


def compute_end_of_service_gratuity(
    start_date: Optional[date],
    monthly_salary: Optional[float],
    calc_date: date,
) -> dict:
    """`calc_date` is the employee's end_date if they've left, otherwise
    "today" (an ongoing accrual estimate) - the caller resolves which, so
    this stays a pure function of its inputs.
    """
    if not start_date or not monthly_salary or calc_date <= start_date:
        return {"years_of_service": 0.0, "gratuity_amount": 0.0, "is_eligible": False}

    years_of_service = round((calc_date - start_date).days / 365.0, 4)
    if years_of_service < 1:
        return {"years_of_service": years_of_service, "gratuity_amount": 0.0, "is_eligible": False}

    daily_wage = monthly_salary / 30.0
    tier1_years = min(years_of_service, 5.0)
    tier2_years = max(years_of_service - 5.0, 0.0)
    gratuity = tier1_years * DAYS_PER_YEAR_TIER1 * daily_wage + tier2_years * DAYS_PER_YEAR_TIER2 * daily_wage
    gratuity = min(gratuity, GRATUITY_CAP_MONTHS_OF_SALARY * monthly_salary)

    return {
        "years_of_service": years_of_service,
        "gratuity_amount": round(gratuity, 2),
        "is_eligible": True,
    }
