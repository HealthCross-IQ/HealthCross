"""Tests for DELETE /cases/{id} - lets a broker remove a mistaken or
test case (and everything filed under it) straight from the case list.
"""
from app.models import db_models as models


def _create_case(client, **overrides):
    payload = {"broker_name": "AL Himayah", "company_name": "Palazzo Versace Hotel LLC", "industry": "hotel"}
    payload.update(overrides)
    resp = client.post("/cases", json=payload)
    return resp.json()["id"]


def test_delete_case_removes_it(client):
    case_id = _create_case(client)
    resp = client.delete(f"/cases/{case_id}")
    assert resp.status_code == 204

    assert client.get(f"/cases/{case_id}").status_code == 404


def test_delete_case_cascades_to_everything_filed_under_it(client):
    case_id = _create_case(client)
    db = client.db_session_local()
    db.add(models.CensusRecord(case_id=case_id, employee_ref="E1", age=30, gender="M", relation="employee"))
    db.add(models.BenefitPlan(case_id=case_id, role="existing", plan_name="Category 1"))
    db.commit()
    db.close()

    resp = client.delete(f"/cases/{case_id}")
    assert resp.status_code == 204

    db = client.db_session_local()
    assert db.query(models.CensusRecord).filter_by(case_id=case_id).count() == 0
    assert db.query(models.BenefitPlan).filter_by(case_id=case_id).count() == 0
    db.close()


def test_delete_nonexistent_case_returns_404(client):
    resp = client.delete("/cases/999999")
    assert resp.status_code == 404


def test_deleted_case_no_longer_appears_in_the_case_list(client):
    case_id = _create_case(client, company_name="To Be Deleted LLC")
    other_id = _create_case(client, company_name="Keep Me LLC")

    client.delete(f"/cases/{case_id}")

    remaining_ids = [c["id"] for c in client.get("/cases").json()]
    assert case_id not in remaining_ids
    assert other_id in remaining_ids
