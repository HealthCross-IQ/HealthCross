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
    monkeypatch.setattr(routes_cases, "parse_labeled_row_benefits_pdf", lambda file, filename: None)
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


def test_benefits_append_mode_keeps_other_categories_when_uploading_one_category_per_file(client, monkeypatch):
    """Some insurers ship each category's table of benefits as its own
    separate PDF rather than one combined document. mode=append must let a
    second file's category be added alongside the first's, rather than the
    default replace-everything behavior wiping category A out the moment
    category B is uploaded.
    """
    monkeypatch.setattr(routes_cases, "is_scanned_pdf", lambda file: False)
    monkeypatch.setattr(routes_cases, "parse_benefits_pdf", lambda file, filename: {})

    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    monkeypatch.setattr(
        routes_cases,
        "parse_benefit_tables_only",
        lambda file, filename: {"SILVER - CAT A": {"category": "A", "network": "MSH Platinum", "annual_limit": 1_000_000.0}},
    )
    resp = client.post(
        f"/cases/{case_id}/benefits?mode=append",
        files={"file": ("cat_a.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    monkeypatch.setattr(
        routes_cases,
        "parse_benefit_tables_only",
        lambda file, filename: {"SILVER - CAT B": {"category": "B", "network": "MSH Gold", "annual_limit": 500_000.0}},
    )
    resp = client.post(
        f"/cases/{case_id}/benefits?mode=append",
        files={"file": ("cat_b.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    db = client.db_session_local()
    existing_plans = db.query(routes_cases.models.BenefitPlan).filter_by(case_id=case_id, role="existing").all()
    db.close()
    assert {p.category for p in existing_plans} == {"A", "B"}


def test_benefits_append_mode_replaces_only_the_reuploaded_category(client, monkeypatch):
    monkeypatch.setattr(routes_cases, "is_scanned_pdf", lambda file: False)
    monkeypatch.setattr(routes_cases, "parse_benefits_pdf", lambda file, filename: {})

    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    monkeypatch.setattr(
        routes_cases,
        "parse_benefit_tables_only",
        lambda file, filename: {"SILVER - CAT A": {"category": "A", "network": "MSH Platinum", "annual_limit": 1_000_000.0}},
    )
    client.post(
        f"/cases/{case_id}/benefits?mode=append",
        files={"file": ("cat_a.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    monkeypatch.setattr(
        routes_cases,
        "parse_benefit_tables_only",
        lambda file, filename: {"SILVER - CAT B": {"category": "B", "network": "MSH Gold", "annual_limit": 500_000.0}},
    )
    client.post(
        f"/cases/{case_id}/benefits?mode=append",
        files={"file": ("cat_b.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )

    # Corrected category A file, re-uploaded - should replace ONLY category A.
    monkeypatch.setattr(
        routes_cases,
        "parse_benefit_tables_only",
        lambda file, filename: {"SILVER - CAT A (corrected)": {"category": "A", "network": "MSH Platinum", "annual_limit": 1_200_000.0}},
    )
    resp = client.post(
        f"/cases/{case_id}/benefits?mode=append",
        files={"file": ("cat_a_v2.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert resp.status_code == 200

    db = client.db_session_local()
    existing_plans = db.query(routes_cases.models.BenefitPlan).filter_by(case_id=case_id, role="existing").all()
    db.close()
    plans = {p.category: p for p in existing_plans}
    assert set(plans.keys()) == {"A", "B"}
    assert plans["A"].plan_name == "SILVER - CAT A (corrected)"


def test_benefits_append_mode_infers_category_from_filename_when_document_has_none(client, monkeypatch):
    """A real-world case: four insurer documents (e.g. Cigna's own
    "SmartCare Plan 1/2/3" tier naming, or a bespoke "CAT VIP") whose
    content never literally says "Category A/B/C/D" at all - only the
    filename the broker gave each file ("Category_B.pdf") carries the
    letter. Without this fallback, every such upload lands with no
    category and all four end up indistinguishable in the Benefits tab.
    """
    monkeypatch.setattr(routes_cases, "is_scanned_pdf", lambda file: False)
    monkeypatch.setattr(routes_cases, "parse_benefits_pdf", lambda file, filename: {})
    monkeypatch.setattr(routes_cases, "parse_benefit_tables_only", lambda file, filename: {})
    monkeypatch.setattr(routes_cases, "parse_labeled_row_benefits_pdf", lambda file, filename: None)
    monkeypatch.setattr(routes_cases, "extract_generic_benefit_rows", lambda file, filename: [])
    monkeypatch.setattr(
        routes_cases,
        "parse_benefits_pdf_text_fallback",
        lambda file, filename: {"summary": {}, "raw_text": "no table structure recognized"},
    )

    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    for fname in ["Category_B.pdf", "Categroy_A.pdf", "random_notes.pdf"]:
        resp = client.post(
            f"/cases/{case_id}/benefits?mode=append",
            files={"file": (fname, io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
        )
        assert resp.status_code == 200

    db = client.db_session_local()
    existing_plans = db.query(routes_cases.models.BenefitPlan).filter_by(case_id=case_id, role="existing").all()
    db.close()
    by_category = {p.category: p for p in existing_plans}
    assert by_category["B"].source_format == "pdf-text-fallback"
    assert by_category["A"].source_format == "pdf-text-fallback"
    # A filename that itself carries no category letter (e.g. an unrelated
    # cover note attached in the same batch) is left uncategorized rather
    # than guessed at - it just doesn't get folded into A or B.
    assert None in by_category


def test_benefits_append_mode_renames_a_generic_fallback_plan_to_its_own_category(client, monkeypatch):
    """A generic fallback parser's own placeholder name ("Base Plan") is
    the same for every such upload - once a real category letter is known
    (even via the filename fallback above), the plan name should say
    which category it is rather than leaving every upload indistinguishable.
    """
    monkeypatch.setattr(routes_cases, "is_scanned_pdf", lambda file: False)
    monkeypatch.setattr(routes_cases, "parse_benefits_pdf", lambda file, filename: {})
    monkeypatch.setattr(routes_cases, "parse_benefit_tables_only", lambda file, filename: {})
    monkeypatch.setattr(routes_cases, "parse_labeled_row_benefits_pdf", lambda file, filename: None)
    monkeypatch.setattr(
        routes_cases,
        "extract_generic_benefit_rows",
        lambda file, filename: [{"label": "Annual Maximum", "value": "USD 1,000,000", "description": ""}],
    )

    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    resp = client.post(
        f"/cases/{case_id}/benefits?mode=append",
        files={"file": ("Category_B.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["category"] == "B"
    assert body[0]["plan_name"] == "Category B"


def test_benefits_upload_with_explicit_category_replaces_a_manually_added_plan_for_it(client, monkeypatch):
    """Regression test for the real duplicate-category bug: a category
    added by hand via POST .../benefits/manual (e.g. before OCR/parsing
    could read a scanned document) must be replaced, not duplicated, once
    the underwriter later uploads the real file for that same category and
    tells the system explicitly which category it's for.
    """
    monkeypatch.setattr(routes_cases, "is_scanned_pdf", lambda file: False)
    monkeypatch.setattr(routes_cases, "parse_benefits_pdf", lambda file, filename: {})
    monkeypatch.setattr(routes_cases, "parse_benefit_tables_only", lambda file, filename: {})
    monkeypatch.setattr(routes_cases, "parse_labeled_row_benefits_pdf", lambda file, filename: None)
    monkeypatch.setattr(
        routes_cases,
        "extract_generic_benefit_rows",
        lambda file, filename: [{"label": "Annual Maximum", "value": "USD 1,000,000", "description": ""}],
    )

    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    resp = client.post(f"/cases/{case_id}/benefits/manual", json={"plan_name": "Category A", "category": "A"})
    assert resp.status_code == 200

    resp = client.post(
        f"/cases/{case_id}/benefits?mode=append&category=A",
        files={"file": ("some_random_filename.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert resp.status_code == 200

    resp = client.get(f"/cases/{case_id}/benefits-summary")
    plans = resp.json()
    assert len(plans) == 1  # replaced, not duplicated
    assert plans[0]["category"] == "A"
    assert plans[0]["summary"]["annual_limit"] == "USD 1,000,000"


def test_benefits_upload_explicit_category_overrides_filename_inference(client, monkeypatch):
    monkeypatch.setattr(routes_cases, "is_scanned_pdf", lambda file: False)
    monkeypatch.setattr(routes_cases, "parse_benefits_pdf", lambda file, filename: {})
    monkeypatch.setattr(routes_cases, "parse_benefit_tables_only", lambda file, filename: {})
    monkeypatch.setattr(routes_cases, "parse_labeled_row_benefits_pdf", lambda file, filename: None)
    monkeypatch.setattr(routes_cases, "extract_generic_benefit_rows", lambda file, filename: [])
    monkeypatch.setattr(
        routes_cases,
        "parse_benefits_pdf_text_fallback",
        lambda file, filename: {"summary": {}, "raw_text": "no table structure recognized"},
    )

    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    resp = client.post(
        f"/cases/{case_id}/benefits?mode=append&category=D",
        files={"file": ("Category_B.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert resp.status_code == 200
    assert resp.json()[0]["category"] == "D"
