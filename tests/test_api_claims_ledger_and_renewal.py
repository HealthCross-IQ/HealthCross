from datetime import date

from app.models import db_models as models


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


def test_case_update_sets_business_type_and_current_premium(client):
    case_id = _create_case(client)
    resp = client.patch(f"/cases/{case_id}", json={"business_type": "existing", "current_annual_premium": 3_000_000})
    assert resp.status_code == 200
    body = resp.json()
    assert body["business_type"] == "existing"
    assert body["current_annual_premium"] == 3_000_000


def test_claims_ledger_upload_replaces_not_accumulates(client):
    import io

    import pandas as pd

    case_id = _create_case(client)
    df = pd.DataFrame(
        [
            {
                "PATIENT_ID": "P1", "CLAIM_ID": "C1", "Claim Status": "Paid Claims",
                "POLICY_START_DATE": "2025-10-01", "POLICY_END_DATE": "2026-10-01",
                "DATE_OF_TREATMENT": "2025-11-05", "DIAGNOSIS_CODE": "J209",
                "DIAGNOSIS_DESCRIPTION": "Acute bronchitis", "Final Amount in AED": 500,
            }
        ]
    )
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)

    for _ in range(2):
        resp = client.post(
            f"/cases/{case_id}/claims-ledger",
            files={"file": ("ledger.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1


def test_claims_ledger_analysis_returns_top_patients_and_diagnoses(client):
    case_id = _create_case(client)
    db = client.db_session_local()
    db.add_all(
        [
            models.ClaimsLedgerEntry(
                case_id=case_id, patient_id="P1", claim_id="C1", policy_start_date=date(2025, 10, 1),
                date_of_treatment=date(2025, 10, 5), diagnosis_code="C50", diagnosis_description="Malignant neoplasm of breast",
                ip_op_maternity="IP", final_amount=50000,
            ),
            models.ClaimsLedgerEntry(
                case_id=case_id, patient_id="P2", claim_id="C2", policy_start_date=date(2025, 10, 1),
                date_of_treatment=date(2025, 11, 5), diagnosis_code="Z511", diagnosis_description="Other medical care",
                ip_op_maternity="OP", final_amount=100,
            ),
        ]
    )
    db.commit()
    db.close()

    resp = client.get(f"/cases/{case_id}/claims-ledger-analysis")
    assert resp.status_code == 200
    body = resp.json()
    assert body["top_patients"][0]["patient_id"] == "P1"
    cancer = next(d for d in body["top_diagnoses"] if d["diagnosis_code"] == "C50")
    assert cancer["classification"] == "chronic"
    assert cancer["high_exposure"] is True


def test_claims_ledger_analysis_404_without_upload(client):
    case_id = _create_case(client)
    resp = client.get(f"/cases/{case_id}/claims-ledger-analysis")
    assert resp.status_code == 404


def test_renewal_rating_end_to_end(client):
    case_id = _create_case(client)
    client.patch(f"/cases/{case_id}", json={"current_annual_premium": 3_000_000})

    monthly = {
        (2025, 10): 175385.01, (2025, 11): 155086.56, (2025, 12): 159281.85,
        (2026, 1): 311397.04, (2026, 2): 263984.21, (2026, 3): 433110.85,
        (2026, 4): 258319.43, (2026, 5): 305978.07, (2026, 6): 162401.95,
        (2026, 7): 57287.75,  # trailing partial month - must be excluded
    }
    _insert_ledger_entries(client, case_id, monthly)

    resp = client.get(f"/cases/{case_id}/renewal-rating")
    assert resp.status_code == 200
    body = resp.json()
    assert body["annualized_incurred_claims"] == 2966593.29
    assert body["current_annual_premium"] == 3_000_000.0
    assert len(body["months_used"]) == 9  # July excluded as trailing partial
    assert body["renewal_increase_pct"] > 0


def test_renewal_rating_requires_current_premium(client):
    case_id = _create_case(client)
    _insert_ledger_entries(client, case_id, {(2025, 10): 1000, (2025, 11): 2000, (2025, 12): 1500})
    resp = client.get(f"/cases/{case_id}/renewal-rating")
    assert resp.status_code == 400


def test_renewal_rating_credibility_style_dynamic_assumptions(client):
    case_id = _create_case(client)
    client.patch(f"/cases/{case_id}", json={"current_annual_premium": 3_000_000})
    _insert_ledger_entries(client, case_id, {(2025, 10): 100000, (2025, 11): 100000, (2025, 12): 100000, (2026, 1): 100000})

    default_resp = client.get(f"/cases/{case_id}/renewal-rating")
    lower_loading_resp = client.get(f"/cases/{case_id}/renewal-rating", params={"loading_pct": 0.10})
    assert lower_loading_resp.json()["renewal_increase_pct"] < default_resp.json()["renewal_increase_pct"]
