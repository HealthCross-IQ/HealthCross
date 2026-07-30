import io

import pandas as pd


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf.read()


def test_census_summary_endpoint_returns_breakdown(client):
    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    rows = [
        {"Category": "D", "Gender": "M", "DOB": "1994-02-15", "Marital Status": "Single", "Relation": "Employee", "Nationality": "Indian"},
        {"Category": "D", "Gender": "F", "DOB": "1994-02-15", "Marital Status": "Married", "Relation": "Employee", "Nationality": "British"},
    ]
    census_df = pd.DataFrame(rows)
    resp = client.post(
        f"/cases/{case_id}/census",
        files={"file": ("census.xlsx", _xlsx_bytes(census_df), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200

    resp = client.get(f"/cases/{case_id}/census-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_members"] == 2
    assert body["gender_counts"] == {"M": 1, "F": 1, "Other": 0}


def test_census_summary_404_without_census(client):
    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    resp = client.get(f"/cases/{case_id}/census-summary")
    assert resp.status_code == 404
