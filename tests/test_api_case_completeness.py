"""Tests for GET /cases/{id}/completeness - the at-a-glance status
checklist backing the case workspace's "Case status" card, so a broker
doesn't have to click into every tab just to see what's still missing.
"""

from app.models import db_models as models


def _create_case(client, **overrides):
    payload = {"broker_name": "AL Himayah", "company_name": "Palazzo Versace Hotel LLC", "industry": "hotel"}
    payload.update(overrides)
    resp = client.post("/cases", json=payload)
    return resp.json()["id"]


def test_completeness_is_all_false_for_a_brand_new_case(client):
    case_id = _create_case(client)
    resp = client.get(f"/cases/{case_id}/completeness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["census_count"] == 0
    assert body["has_census"] is False
    assert body["has_benefits"] is False
    assert body["has_claims"] is False
    assert body["has_claims_ledger"] is False
    assert body["has_quote"] is False
    assert body["has_scorecard"] is False
    assert body["ready_to_score"] is False
    assert body["latest_risk_tier"] is None


def test_completeness_reflects_uploaded_census_and_benefits(client):
    case_id = _create_case(client)
    db = client.db_session_local()
    db.add(models.CensusRecord(case_id=case_id, employee_ref="E1", age=30, gender="M", relation="employee"))
    db.add(models.CensusRecord(case_id=case_id, employee_ref="E2", age=28, gender="F", relation="spouse"))
    db.add(models.BenefitPlan(case_id=case_id, role="existing", plan_name="Category 1"))
    db.commit()
    db.close()

    resp = client.get(f"/cases/{case_id}/completeness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["census_count"] == 2
    assert body["has_census"] is True
    assert body["existing_benefit_plan_count"] == 1
    assert body["has_benefits"] is True
    assert body["ready_to_score"] is True
    # A quoted-role plan is a separate thing (the quote), not counted here.
    assert body["has_quote"] is False


def test_completeness_counts_quote_claims_ledger_and_scorecard_separately(client):
    case_id = _create_case(client)
    db = client.db_session_local()
    db.add(models.BenefitPlan(case_id=case_id, role="quoted", plan_name="Gold - CAT A", category="A"))
    db.add(models.ClaimsLedgerEntry(case_id=case_id, patient_id="P1", final_amount=100.0))
    db.add(
        models.Scorecard(
            case_id=case_id,
            weight_set_id=1,
            demographic_risk=0.1,
            claims_experience_risk=0.1,
            benefit_richness_risk=0.1,
            industry_risk=0.1,
            credibility_factor=1.0,
            composite_score=0.5,
            risk_tier="Standard",
            suggested_loading_pct=0.1,
            details={},
        )
    )
    db.commit()
    db.close()

    resp = client.get(f"/cases/{case_id}/completeness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_quote"] is True
    assert body["quoted_benefit_plan_count"] == 1
    assert body["has_claims_ledger"] is True
    assert body["claims_ledger_entry_count"] == 1
    assert body["has_scorecard"] is True
    assert body["scorecard_count"] == 1
    assert body["latest_risk_tier"] == "Standard"
    # Still not ready to score without both census AND benefits.
    assert body["ready_to_score"] is False


def test_completeness_404s_for_a_missing_case(client):
    resp = client.get("/cases/999999/completeness")
    assert resp.status_code == 404
