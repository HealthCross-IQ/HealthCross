import io

from app.api import routes_cases


def test_benefits_upload_falls_back_to_text_scan_when_no_tier_table_found(client, monkeypatch):
    """A PDF with real extractable text but no bordered tier table
    pdfplumber can recover (e.g. the real Sukoon "renewal" TOB, which uses
    whitespace-aligned columns rather than table lines) must not just
    fail - it should fall back to the same label-anchored nearby-value
    scan used for OCR, run against the real text instead of an image.
    """
    monkeypatch.setattr(routes_cases, "is_scanned_pdf", lambda file: False)
    monkeypatch.setattr(routes_cases, "parse_benefits_pdf", lambda file, filename: {})
    monkeypatch.setattr(
        routes_cases,
        "parse_benefits_pdf_text_fallback",
        lambda file, filename: {
            "summary": {"annual_limit": "1,000,000/- / 750,000/- / 400,000/- / 250,000/- (multiple values found near this label - verify against the source PDF)"},
            "raw_text": "--- page 1 ---\nCategory A Category B Category C Category D\nIndemnity Limit 1,000,000/- 750,000/- 400,000/- 250,000/-",
        },
    )

    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    resp = client.post(
        f"/cases/{case_id}/benefits",
        files={"file": ("renewal_tob.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["source_format"] == "pdf-text-fallback"
    assert "multiple values found" in body[0]["standard_summary"]["annual_limit"]
    assert body[0]["raw_ocr_text"].startswith("--- page 1 ---")
