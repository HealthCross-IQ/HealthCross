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
    monkeypatch.setattr(routes_cases, "parse_benefit_tables_only", lambda file, filename: {})
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


def test_benefits_upload_uses_cat_style_parser_before_text_fallback(client, monkeypatch):
    """A real existing-benefits document from the QIC/HealthCROSS Global
    family (label-as-first-column, "CAT <letter>" tier headers, no premium
    table) must be parsed properly via parse_benefit_tables_only rather
    than falling all the way through to the much less precise text scan.
    """
    monkeypatch.setattr(routes_cases, "is_scanned_pdf", lambda file: False)
    monkeypatch.setattr(routes_cases, "parse_benefits_pdf", lambda file, filename: {})
    monkeypatch.setattr(
        routes_cases,
        "parse_benefit_tables_only",
        lambda file, filename: {
            "SILVER - CAT A": {
                "category": "A",
                "network": "MSH Platinum",
                "annual_limit": 1_000_000.0,
                "maternity_limit": 6_800.0,
                "dental_covered": False,
                "optical_covered": True,
                "pre_existing_covered": True,
                "chronic_covered": True,
                "standard_summary": {"annual_limit": "USD 1,000,000", "optical": "USD 300"},
            }
        },
    )

    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    resp = client.post(
        f"/cases/{case_id}/benefits",
        files={"file": ("existing_tob.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["plan_name"] == "SILVER - CAT A"
    assert body[0]["category"] == "A"
    assert body[0]["network_type"] == "MSH Platinum"
    assert body[0]["source_format"] == "pdf-cat-style"
    assert body[0]["standard_summary"]["annual_limit"] == "USD 1,000,000"
