from datetime import date

from app.scoring.rules.exposed_risk_population import monthly_exposed_risk_population


def _member(member_start=None, member_end=None):
    return {"member_start_date": member_start, "member_end_date": member_end}


def test_full_term_member_contributes_a_full_1_0_every_month():
    census = [_member(date(2025, 1, 1), date(2025, 12, 31))]
    rows = monthly_exposed_risk_population(census, date(2025, 1, 1), date(2025, 3, 31))
    assert [r["erp"] for r in rows] == [1.0, 1.0, 1.0]


def test_member_who_joins_mid_month_contributes_a_partial_fraction():
    # Joined April 16 - April has 30 days, so covered 15 of 30 = 0.5.
    census = [_member(date(2025, 4, 16), date(2025, 12, 31))]
    rows = monthly_exposed_risk_population(census, date(2025, 4, 1), date(2025, 4, 30))
    assert rows == [{"year": 2025, "month": 4, "erp": 0.5}]


def test_member_who_leaves_mid_month_contributes_a_partial_fraction():
    # Left April 15 - covered days 1-15 inclusive = 15 of 30 days = 0.5.
    census = [_member(date(2025, 1, 1), date(2025, 4, 15))]
    rows = monthly_exposed_risk_population(census, date(2025, 4, 1), date(2025, 4, 30))
    assert rows == [{"year": 2025, "month": 4, "erp": 0.5}]


def test_member_not_yet_joined_or_already_left_contributes_zero():
    census = [_member(date(2025, 6, 1), date(2025, 6, 30))]
    rows = monthly_exposed_risk_population(census, date(2025, 4, 1), date(2025, 4, 30))
    assert rows == [{"year": 2025, "month": 4, "erp": 0.0}]


def test_erp_sums_across_all_members():
    census = [
        _member(date(2025, 1, 1), date(2025, 12, 31)),  # full month: 1.0
        _member(date(2025, 4, 16), date(2025, 12, 31)),  # half month: 0.5
    ]
    rows = monthly_exposed_risk_population(census, date(2025, 4, 1), date(2025, 4, 30))
    assert rows == [{"year": 2025, "month": 4, "erp": 1.5}]


def test_missing_member_dates_default_to_full_scheme_coverage():
    census = [_member(None, None)]
    rows = monthly_exposed_risk_population(census, date(2025, 4, 1), date(2025, 4, 30))
    assert rows == [{"year": 2025, "month": 4, "erp": 1.0}]


def test_spans_multiple_months_in_order():
    # Member's own coverage ends Dec 31 2025, mid-way through the queried
    # range - Nov/Dec should be full ERP, Jan/Feb should drop to zero.
    census = [_member(date(2025, 1, 1), date(2025, 12, 31))]
    rows = monthly_exposed_risk_population(census, date(2025, 11, 1), date(2026, 2, 28))
    assert [(r["year"], r["month"], r["erp"]) for r in rows] == [
        (2025, 11, 1.0), (2025, 12, 1.0), (2026, 1, 0.0), (2026, 2, 0.0),
    ]
