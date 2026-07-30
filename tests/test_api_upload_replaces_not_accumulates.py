import io

import pandas as pd


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf.read()


def _census_df(n):
    return pd.DataFrame(
        [
            {"Category": "D", "Gender": "M", "DOB": "1994-02-15", "Marital Status": "Single", "Relation": "Employee", "Nationality": "Indian"}
            for _ in range(n)
        ]
    )


def test_reuploading_census_replaces_rather_than_accumulates(client):
    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    resp = client.post(
        f"/cases/{case_id}/census",
        files={"file": ("census.xlsx", _xlsx_bytes(_census_df(10)), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert len(resp.json()) == 10

    # Re-upload the same (or a corrected) census file for the same case.
    resp = client.post(
        f"/cases/{case_id}/census",
        files={"file": ("census.xlsx", _xlsx_bytes(_census_df(10)), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert len(resp.json()) == 10

    case = client.get(f"/cases/{case_id}").json()  # sanity: case itself untouched
    assert case["id"] == case_id


def test_reuploading_benefits_replaces_rather_than_accumulates(client):
    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    benefits_df = pd.DataFrame([{"Plan": "Standard", "Annual Limit": 250000, "Members": 3}])
    for _ in range(3):
        resp = client.post(
            f"/cases/{case_id}/benefits",
            files={"file": ("tob.xlsx", _xlsx_bytes(benefits_df), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert len(resp.json()) == 1

    resp = client.get(f"/cases/{case_id}/benefits-summary")
    assert len(resp.json()) == 1


def test_reuploading_claims_replaces_rather_than_accumulates(client):
    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    claims_df = pd.DataFrame([{"Member ID": "E1", "Date of Service": "2025-02-01", "Claim Type": "Outpatient", "Paid Amount": 800}])
    for _ in range(3):
        resp = client.post(
            f"/cases/{case_id}/claims",
            files={"file": ("claims.xlsx", _xlsx_bytes(claims_df), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert len(resp.json()) == 1
