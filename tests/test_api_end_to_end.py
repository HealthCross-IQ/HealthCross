import io

import pandas as pd


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf.read()


def test_full_underwriting_flow(client):
    resp = client.post(
        "/cases",
        json={
            "broker_name": "Acme Brokers",
            "company_name": "Widgets LLC",
            "industry": "manufacturing",
            "region": "Dubai",
            "employee_count_declared": 4,
        },
    )
    assert resp.status_code == 200
    case_id = resp.json()["id"]

    census_df = pd.DataFrame(
        [
            {"Category": "D", "Gender": "M", "DOB": "1994-02-15", "Marital Status": "Single", "Relation": "Employee", "Nationality": "Indian"},
            {"Category": "D", "Gender": "F", "DOB": "1996-06-20", "Marital Status": "Married", "Relation": "Employee", "Nationality": "Filipino"},
            {"Category": "D", "Gender": "F", "DOB": "1997-01-10", "Marital Status": "Married", "Relation": "Wife", "Nationality": "Filipino"},
            {"Category": "D", "Gender": "F", "DOB": "2023-05-01", "Marital Status": "Single", "Relation": "Daughter", "Nationality": "Filipino"},
        ]
    )
    resp = client.post(
        f"/cases/{case_id}/census",
        files={"file": ("census.xlsx", _xlsx_bytes(census_df), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 4

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
                "Members": 4,
            }
        ]
    )
    resp = client.post(
        f"/cases/{case_id}/benefits",
        files={"file": ("tob.xlsx", _xlsx_bytes(benefits_df), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200

    claims_df = pd.DataFrame(
        [
            {"Member ID": "E1", "Date of Service": "2025-02-01", "Claim Type": "Outpatient", "Paid Amount": 800},
            {"Member ID": "E2", "Date of Service": "2025-06-15", "Claim Type": "Inpatient", "Paid Amount": 12000},
        ]
    )
    resp = client.post(
        f"/cases/{case_id}/claims",
        files={"file": ("claims.xlsx", _xlsx_bytes(claims_df), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200

    resp = client.post(f"/cases/{case_id}/score", json={"estimated_annual_premium": 30000})
    assert resp.status_code == 200
    scorecard = resp.json()
    assert 0 <= scorecard["composite_score"] <= 100
    assert scorecard["risk_tier"] in {"Preferred", "Standard", "Substandard", "Decline/Refer"}
    # Both the married female employee and the married female spouse are 18-40.
    assert scorecard["details"]["demographic"]["maternity_risk_count"] == 2
    assert scorecard["details"]["demographic"]["female_spouse_count"] == 1
    assert scorecard["details"]["demographic"]["favorable_children_count"] == 1
    assert scorecard["details"]["demographic"]["infant_count"] == 0

    resp = client.get(f"/cases/{case_id}/scorecard")
    assert resp.status_code == 200

    resp = client.post(
        f"/cases/{case_id}/outcome",
        json={"bound": True, "final_premium": 31000, "actual_loss_ratio": 0.55},
    )
    assert resp.status_code == 200
    assert resp.json()["profitable"] is True

    resp = client.post("/admin/recalibrate")
    assert resp.status_code == 200
    assert resp.json()["recalibrated"] is False  # not enough outcomes yet

    resp = client.get("/admin/weights")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
