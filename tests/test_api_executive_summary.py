from datetime import date

from app.models import db_models as models

# A renewal is not priced on an assumed loading, so a case under test
# has to state its fee split the way a real one does. These are the
# house defaults, so every figure asserted in this file is unchanged.
HOUSE_FEES = {"tpa_fee_pct": 0.065, "commission_pct": 0.15,
              "hc_fee_pct": 0.065, "qic_fee_pct": 0.05}

def _create_case(client, **overrides):
    payload = {"broker_name": "Broker", "company_name": "Amazonico", "industry": "trading"}
    payload.update(overrides)
    resp = client.post("/cases", json=payload)
    return resp.json()["id"]


def _add_census(client, case_id, count=10, relation="employee", gender="M", age=30):
    db = client.db_session_local()
    db.add_all(
        [
            models.CensusRecord(case_id=case_id, age=age, gender=gender, marital_status="single", relation=relation)
            for _ in range(count)
        ]
    )
    db.commit()
    db.close()


def _insert_ledger_entries(client, case_id, months_and_amounts, policy_start=date(2025, 10, 1)):
    db = client.db_session_local()
    entry_id = 0
    for (year, month), amount in months_and_amounts.items():
        entry_id += 1
        db.add(
            models.ClaimsLedgerEntry(
                case_id=case_id,
                patient_id=f"P{entry_id}",
                claim_id=f"C{entry_id}",
                claim_status="Paid Claims",
                policy_start_date=policy_start,
                policy_end_date=date(policy_start.year + 1, policy_start.month, policy_start.day),
                date_of_treatment=date(year, month, 10),
                ip_op_maternity="OP",
                diagnosis_code="J209",
                diagnosis_description="Acute bronchitis",
                final_amount=amount,
            )
        )
    db.commit()
    db.close()


def _insert_claims_report(client, case_id, **overrides):
    db = client.db_session_local()
    defaults = dict(
        case_id=case_id,
        policy_number="54773",
        opening_members=100,
        closing_members=120,
        total_paid=900_000.0,
        monthly_paid=[
            {"year": 2025, "month": "Oct", "paid": 8870.0, "partial": True},
            {"year": 2025, "month": "Nov", "paid": 70000.0, "partial": False},
            {"year": 2025, "month": "Dec", "paid": 72000.0, "partial": False},
            {"year": 2026, "month": "Jan", "paid": 75000.0, "partial": False},
            {"year": 2026, "month": "Feb", "paid": 71000.0, "partial": False},
            {"year": 2026, "month": "Mar", "paid": 73000.0, "partial": False},
            {"year": 2026, "month": "Apr", "paid": 74000.0, "partial": False},
        ],
    )
    defaults.update(overrides)
    report = models.ClaimsReport(**defaults)
    db.add(report)
    db.commit()
    db.refresh(report)
    db.close()
    return report


def test_executive_summary_404s_for_missing_case(client):
    resp = client.get("/cases/999999/executive-summary")
    assert resp.status_code == 404


def test_executive_summary_bare_case_returns_none_for_every_optional_section(client):
    case_id = _create_case(client, company_name="Bare Co")
    resp = client.get(f"/cases/{case_id}/executive-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["case"]["company_name"] == "Bare Co"
    assert body["account_type"] == "new_business"
    assert body["census_summary"] is None
    assert body["premium"] == {"current_annual_premium": None, "target_premium": None, "premium_per_member": None}
    assert body["renewal"] is None
    assert body["benchmark"] is None
    assert body["burning_cost"] is None
    assert body["new_business_benchmark"] is None


def test_executive_summary_renewal_account(client):
    case_id = _create_case(client, company_name="Renewal Co")
    client.patch(f"/cases/{case_id}", json={**HOUSE_FEES, "current_annual_premium": 1_000_000})
    _add_census(client, case_id, count=10)
    _insert_ledger_entries(client, case_id, {(2025, 10): 80000, (2025, 11): 80000, (2025, 12): 80000})
    _insert_claims_report(
        client, case_id, report_period_start=date(2025, 10, 1), report_period_end=date(2026, 4, 21)
    )

    resp = client.get(f"/cases/{case_id}/executive-summary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["account_type"] == "renewal"
    assert body["census_summary"]["total_members"] == 10
    assert body["premium"]["current_annual_premium"] == 1_000_000.0
    assert body["premium"]["premium_per_member"] == 100_000.0

    assert body["renewal"] is not None
    assert body["renewal"]["current_annual_premium"] == 1_000_000.0
    assert body["benchmark"] is not None
    assert body["benchmark"]["comparable_case_count"] == 0

    assert body["burning_cost"] is not None
    assert body["burning_cost"]["latest_per_member"] > 0
    assert body["burning_cost"]["latest_report_period"] == "2025-10-01 to 2026-04-21"
    assert body["burning_cost"]["prior_per_member"] is None
    assert body["burning_cost"]["change_pct"] is None

    assert body["new_business_benchmark"] is None


def test_executive_summary_burning_cost_trend_uses_latest_and_prior_report(client):
    case_id = _create_case(client, company_name="Two Year Co")
    client.patch(f"/cases/{case_id}", json={**HOUSE_FEES, "current_annual_premium": 1_000_000})
    _add_census(client, case_id, count=10)
    _insert_ledger_entries(client, case_id, {(2025, 10): 80000, (2025, 11): 80000, (2025, 12): 80000})
    _insert_claims_report(
        client, case_id,
        report_period_start=date(2024, 10, 1), report_period_end=date(2025, 9, 30),
        opening_members=80, closing_members=90, total_paid=600_000.0,
    )
    _insert_claims_report(
        client, case_id,
        report_period_start=date(2025, 10, 1), report_period_end=date(2026, 4, 21),
        opening_members=100, closing_members=120, total_paid=900_000.0,
    )

    resp = client.get(f"/cases/{case_id}/executive-summary")
    body = resp.json()

    bc = body["burning_cost"]
    assert bc["prior_per_member"] is not None
    assert bc["latest_report_period"] == "2025-10-01 to 2026-04-21"
    assert bc["change_pct"] == round((bc["latest_per_member"] / bc["prior_per_member"] - 1) * 100, 1)


def test_executive_summary_new_business_account_benchmarks_against_the_book(client):
    other_case = _create_case(client, company_name="Existing Book Case")
    client.patch(f"/cases/{other_case}", json={**HOUSE_FEES, "current_annual_premium": 500_000})
    _add_census(client, other_case, count=10)  # 50,000/member

    case_id = _create_case(client, company_name="New Biz Co")
    client.patch(f"/cases/{case_id}", json={"target_premium": 600_000})
    _add_census(client, case_id, count=10)  # 60,000/member

    resp = client.get(f"/cases/{case_id}/executive-summary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["account_type"] == "new_business"
    assert body["premium"]["target_premium"] == 600_000.0
    assert body["premium"]["premium_per_member"] == 60_000.0
    assert body["renewal"] is None
    assert body["burning_cost"] is None

    nbb = body["new_business_benchmark"]
    assert nbb is not None
    assert nbb["premium_per_member"] == 60_000.0
    assert nbb["book_median_premium_per_member"] == 50_000.0
    assert nbb["comparable_case_count"] == 1
    assert nbb["percentile"] == 100.0
    assert nbb["low_credibility"] is True
