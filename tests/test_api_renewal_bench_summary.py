from datetime import date

import pytest

from app.models import db_models as models

# A renewal is not priced on an assumed loading, so a case under test
# has to state its fee split the way a real one does. These are the
# house defaults - 6.5 + 15 + 6.5 + 5 = the 33% that used to be filled
# in silently - so every figure asserted in this file is unchanged.
HOUSE_FEES = {"tpa_fee_pct": 0.065, "commission_pct": 0.15,
              "hc_fee_pct": 0.065, "qic_fee_pct": 0.05}

def _create_case(client, **overrides):
    payload = {"broker_name": "Broker", "company_name": "Amazonico", "industry": "trading"}
    payload.update(overrides)
    resp = client.post("/cases", json=payload)
    return resp.json()["id"]


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


def _insert_census(client, case_id, ages, relation="employee"):
    db = client.db_session_local()
    for i, age in enumerate(ages):
        db.add(models.CensusRecord(case_id=case_id, employee_ref=f"E{i}", age=age, relation=relation))
    db.commit()
    db.close()


def _base_case(client):
    case_id = _create_case(client)
    client.patch(f"/cases/{case_id}", json={**HOUSE_FEES, "current_annual_premium": 3_000_000})
    _insert_ledger_entries(
        client, case_id, {(2025, 10): 100000, (2025, 11): 100000, (2025, 12): 100000, (2026, 1): 100000}
    )
    _insert_census(client, case_id, ages=[30] * 10 + [40] * 10)
    return case_id


def test_renewal_bench_summary_requires_claims_ledger(client):
    case_id = _create_case(client)
    client.patch(f"/cases/{case_id}", json={**HOUSE_FEES, "current_annual_premium": 3_000_000})
    resp = client.get(f"/cases/{case_id}/renewal-bench-summary")
    assert resp.status_code == 404


def test_renewal_bench_summary_requires_current_premium(client):
    case_id = _create_case(client)
    _insert_ledger_entries(client, case_id, {(2025, 10): 1000, (2025, 11): 2000, (2025, 12): 1500})
    resp = client.get(f"/cases/{case_id}/renewal-bench-summary")
    assert resp.status_code == 400


def test_renewal_bench_summary_kpis_and_breakdown(client):
    case_id = _base_case(client)

    resp = client.get(f"/cases/{case_id}/renewal-bench-summary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["case_identity"]["company_name"] == "Amazonico"
    assert body["case_identity"]["member_count"] == 20
    assert body["case_identity"]["product"] is None  # no benefits uploaded

    # 4 claim lines (Oct-Jan, one of which - Jan - is a trailing partial
    # month excluded from months_used=3), annualized over 3 full months to
    # 16/yr, over 20 members.
    kpis = body["kpis"]
    assert kpis["claim_count"] == 4
    assert kpis["distinct_claimants"] == 4
    assert kpis["claim_frequency"] == 0.8
    assert kpis["avg_claim_severity"] == 100000.0
    assert kpis["claimant_ratio"] == 0.2
    assert kpis["avg_member_age"] == 35.0
    assert kpis["actual_loss_ratio"] == 0.5319

    # Every ledger entry in this fixture is ip_op_maternity="OP".
    breakdown = body["claims_cost_breakdown"]
    assert len(breakdown) == 1
    assert breakdown[0]["encounter_type"] == "Op"
    assert breakdown[0]["pct_of_total"] == 100.0


def test_renewal_bench_summary_drivers_reconcile_to_total_pct(client):
    case_id = _base_case(client)

    resp = client.get(f"/cases/{case_id}/renewal-bench-summary")
    body = resp.json()
    drivers = body["drivers"]

    assert drivers["claims_experience_pct"] == -20.61
    assert drivers["medical_trend_pct"] == 11.19
    assert drivers["census_change_pct"] is None  # no prior census snapshot on this case
    assert drivers["underwriter_adjustment_pct"] == 0.0

    # This account's own experience asks for a decrease, so the house
    # floor decides the ask - and gets its own bar rather than being
    # folded back into experience.
    assert drivers["floor_applied"] is True
    assert drivers["floor_pct"] == 18.42
    assert drivers["total_pct"] == 9.0
    assert round(
        drivers["claims_experience_pct"] + drivers["medical_trend_pct"] + drivers["floor_pct"], 2
    ) == drivers["total_pct"]
    assert drivers["within_authority"] is True

    # And the hero is Method 1's own premium, not a second one beside it.
    # The two used to disagree on the same screen - the bigger the loss
    # ratio, the wider the gap - because this waterfall multiplied the
    # CLAIMS by inflation while the ladder adds it to the LOSS RATIO in
    # points. Checked against the rating endpoint itself, so the two
    # screens an underwriter actually compares must agree.
    assert drivers["recommended_premium"] == drivers["method_1_required_premium"]
    rating = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert drivers["recommended_premium"] == rating["required_premium"]
    assert drivers["total_pct"] == rating["renewal_increase_pct"]


def test_renewal_bench_summary_underwriter_adjustment_is_overridable(client):
    case_id = _base_case(client)

    resp = client.get(
        f"/cases/{case_id}/renewal-bench-summary",
        params={"underwriter_adjustment_pct": -20.0, "authority_threshold_pct": 15.0},
    )
    body = resp.json()
    drivers = body["drivers"]

    assert drivers["underwriter_adjustment_pct"] == -20.0
    # 20 points off Method 1's floored +9% ask, not off an unfloored
    # experience figure the portal never quotes.
    assert drivers["total_pct"] == -11.0
    assert drivers["within_authority"] is False


def test_renewal_bench_summary_claims_trend_is_empty_without_claims_reports(client):
    case_id = _base_case(client)
    resp = client.get(f"/cases/{case_id}/renewal-bench-summary")
    assert resp.json()["claims_trend"] == []


def test_renewal_bench_summary_claims_trend_reflects_real_report_history(client):
    case_id = _base_case(client)
    db = client.db_session_local()
    db.add_all(
        [
            models.ClaimsReport(case_id=case_id, report_period_start=date(2024, 1, 1), total_paid=200_000),
            models.ClaimsReport(case_id=case_id, report_period_start=date(2025, 1, 1), total_paid=260_000),
        ]
    )
    db.commit()
    db.close()

    resp = client.get(f"/cases/{case_id}/renewal-bench-summary")
    trend = resp.json()["claims_trend"]
    assert trend == [
        {"year": 2024, "total_paid": 200_000},
        {"year": 2025, "total_paid": 260_000},
    ]


def test_renewal_bench_summary_flags_a_discrepancy_between_computed_and_current_premium(client):
    case_id = _base_case(client)  # current_annual_premium set to 3,000,000, 20 members
    db = client.db_session_local()
    for m in db.query(models.CensusRecord).filter_by(case_id=case_id).all():
        m.category = "A"
        m.existing_annual_rate = 100_000  # 20 members x 100,000 = 2,000,000 computed total
    db.commit()
    db.close()

    resp = client.get(f"/cases/{case_id}/renewal-bench-summary")
    existing = resp.json()["existing_premium"]

    assert existing["total_existing_premium"] == 2_000_000.0
    assert existing["coverage_pct"] == 100.0
    assert existing["current_annual_premium_on_case"] == 3_000_000.0
    assert existing["discrepancy_pct"] == pytest.approx(-33.33, abs=0.01)
