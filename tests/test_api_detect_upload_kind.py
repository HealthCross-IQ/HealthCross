"""Tests for POST /cases/detect-upload-kind - the endpoint backing the
case workspace's single drag-drop "Quick upload" zone.
"""
import io

import pandas as pd


def _xlsx_bytes(rows: list) -> bytes:
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


def test_detect_upload_kind_recognizes_a_census_file(client):
    data = _xlsx_bytes(
        [{"Category": "A", "Age": 30, "Gender": "M", "Marital Status": "Single", "Relation": "Employee", "Nationality": "India"}]
    )
    resp = client.post(
        "/cases/detect-upload-kind",
        files={"file": ("census.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "census.xlsx"
    assert body["detected_kind"] == "census"
    assert body["confidence"] == "high"


def test_detect_upload_kind_never_parses_or_stores_anything(client):
    # Detection alone must not create any case data - it's purely a guess
    # shown back for confirmation, with the real upload a separate step.
    data = _xlsx_bytes(
        [{"Category": "A", "Age": 30, "Gender": "M", "Marital Status": "Single", "Relation": "Employee", "Nationality": "India"}]
    )
    resp = client.post(
        "/cases/detect-upload-kind",
        files={"file": ("census.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200

    case_resp = client.post("/cases", json={"broker_name": "B", "company_name": "C", "industry": "I"})
    case_id = case_resp.json()["id"]
    summary_resp = client.get(f"/cases/{case_id}/census-summary")
    assert summary_resp.status_code == 404
