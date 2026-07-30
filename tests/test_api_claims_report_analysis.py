import io

import pandas as pd

from app.models import db_models as models
from app.scoring.rules.benefits_summary import STANDARD_FIELDS


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf.read()


def _create_case_with_census(client, member_count=212):
    resp = client.post(
        "/cases",
        json={"broker_name": "Broker A", "company_name": "LEGRAND SNC FZE", "industry": "trading"},
    )
    case_id = resp.json()["id"]

    rows = [
        {"Category": "D", "Gender": "M", "DOB": "1994-02-15", "Marital Status": "Single", "Relation": "Employee", "Nationality": "Indian"}
        for _ in range(member_count)
    ]
    census_df = pd.DataFrame(rows)
    resp = client.post(
        f"/cases/{case_id}/census",
        files={"file": ("census.xlsx", _xlsx_bytes(census_df), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    return case_id


def _insert_claims_report(client, case_id, **overrides):
    db = client.db_session_local()
    defaults = dict(
        case_id=case_id,
        policy_number="54773",
        opening_members=161,
        closing_members=227,
        total_paid=1_772_027.0,
        incurred_not_reported=421_387.0,
        diagnosis_breakdown=[
            {"label": "NEOPLASMS", "value": 381126.0, "count": 52, "ip_value": 346113.0, "ip_count": 6},
            {"label": "DENTAL/ORAL DISEASES", "value": 128613.0, "count": 146, "ip_value": 0.0, "ip_count": 0},
        ],
        monthly_paid=[
            {"year": 2025, "month": "Sep", "paid": 8870.0, "partial": True},
            {"year": 2025, "month": "Oct", "paid": 203861.0, "partial": False},
            {"year": 2025, "month": "Nov", "paid": 216391.0, "partial": False},
            {"year": 2025, "month": "Dec", "paid": 175170.0, "partial": False},
            {"year": 2026, "month": "Jan", "paid": 502079.0, "partial": False},
            {"year": 2026, "month": "Feb", "paid": 157146.0, "partial": False},
            {"year": 2026, "month": "Mar", "paid": 155289.0, "partial": False},
        ],
    )
    defaults.update(overrides)
    report = models.ClaimsReport(**defaults)
    db.add(report)
    db.commit()
    db.refresh(report)
    db.close()
    return report


def test_get_claims_report_returns_the_latest_report(client):
    case_id = _create_case_with_census(client)
    _insert_claims_report(client, case_id)

    resp = client.get(f"/cases/{case_id}/claims-report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["opening_members"] == 161
    assert body["closing_members"] == 227
    assert body["total_paid"] == 1_772_027.0


def test_get_claims_report_404_when_none_uploaded(client):
    case_id = _create_case_with_census(client)
    resp = client.get(f"/cases/{case_id}/claims-report")
    assert resp.status_code == 404


def test_claims_projection_matches_the_hand_worked_example(client):
    case_id = _create_case_with_census(client, member_count=212)
    _insert_claims_report(client, case_id)

    resp = client.get(f"/cases/{case_id}/claims-projection")
    assert resp.status_code == 200
    body = resp.json()

    assert body["months_used"] == ["Oct 2025", "Nov 2025", "Dec 2025", "Jan 2026", "Feb 2026", "Mar 2026"]
    assert round(body["final_projected_claims"]) == 4554856
    assert body["opening_members"] == 161
    assert body["closing_members"] == 227


def test_claims_projection_credibility_override_via_query_param(client):
    case_id = _create_case_with_census(client, member_count=212)
    _insert_claims_report(client, case_id)

    default_resp = client.get(f"/cases/{case_id}/claims-projection")
    lower_credibility_resp = client.get(f"/cases/{case_id}/claims-projection", params={"credibility_pct": 0.5})

    assert lower_credibility_resp.json()["assumptions_used"]["credibility_pct"] == 0.5
    assert lower_credibility_resp.json()["final_projected_claims"] < default_resp.json()["final_projected_claims"]


def test_diagnosis_exposure_flags_cancer_as_chronic_and_high_exposure(client):
    case_id = _create_case_with_census(client)
    _insert_claims_report(client, case_id)

    resp = client.get(f"/cases/{case_id}/diagnosis-exposure")
    assert resp.status_code == 200
    rows = resp.json()

    neoplasms = next(r for r in rows if r["label"] == "NEOPLASMS")
    assert neoplasms["classification"] == "chronic"
    assert neoplasms["high_exposure"] is True
    assert "possible_large_or_shock_claim" in neoplasms["flags"]

    dental = next(r for r in rows if r["label"] == "DENTAL/ORAL DISEASES")
    assert dental["classification"] == "non_chronic"
    assert dental["high_exposure"] is False

    # sorted by value descending
    assert rows[0]["label"] == "NEOPLASMS"


def test_benefits_summary_uses_standard_fields(client):
    resp = client.post(
        "/cases",
        json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"},
    )
    case_id = resp.json()["id"]

    benefits_df = pd.DataFrame(
        [
            {
                "Plan": "Standard",
                "Annual Limit": 250000,
                "Room & Board": "Semi-Private",
                "Deductible": 100,
                "Coinsurance %": 90,
                "Area of Cover": "In-Country",
                "Maternity Cover": "Yes",
                "Members": 3,
            }
        ]
    )
    resp = client.post(
        f"/cases/{case_id}/benefits",
        files={"file": ("tob.xlsx", _xlsx_bytes(benefits_df), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200

    resp = client.get(f"/cases/{case_id}/benefits-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert set(body[0]["summary"].keys()) == set(STANDARD_FIELDS)
