"""Tests for app/ingestion/upload_sniffer.py - the best-effort guess at
which upload slot a dropped file belongs in, backing the case workspace's
single drag-drop "Quick upload" zone. A wrong guess here only costs a
dropdown correction in the confirm step, never a silent mis-upload, so
these tests check the guess is reasonable, not perfect.
"""
import io

import pandas as pd

from app.ingestion import upload_sniffer


def _xlsx_bytes(rows: list) -> io.BytesIO:
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf


def test_recognizes_a_census_style_spreadsheet():
    buf = _xlsx_bytes(
        [
            {"Category": "A", "Age": 30, "Gender": "M", "Marital Status": "Single", "Relation": "Employee",
             "Emirates": "Dubai", "Nationality": "India", "Salary": "HSB"},
        ]
    )
    result = upload_sniffer.sniff_upload_kind(buf, "census.xlsx")
    assert result["detected_kind"] == "census"
    assert result["confidence"] == "high"


def test_recognizes_a_claims_ledger_style_spreadsheet():
    buf = _xlsx_bytes(
        [
            {"patient_id": "P1", "claim_id": "C1", "claim status": "Paid Claims", "date_of_treatment": "2025-01-01",
             "diagnosis_code": "J30", "provider_name": "Some Hospital", "final_amount": 500.0,
             "medical_category": "OP", "ip_op_maternity": "OP", "relation": "Employee"},
        ]
    )
    result = upload_sniffer.sniff_upload_kind(buf, "claims_ledger.xlsx")
    assert result["detected_kind"] == "claims-ledger"
    assert result["confidence"] == "high"


def test_a_generic_claims_spreadsheet_is_told_apart_from_a_claims_ledger():
    # app/ingestion/claims.py's own simpler aliases (member id/claim date/
    # billed amount) rather than the ledger's patient_id/diagnosis_code
    # vocabulary - a genuinely different, much sparser sheet shape.
    buf = _xlsx_bytes(
        [
            {"Member ID": "M1", "Claim Date": "2025-01-01", "Service Type": "OP", "Diagnosis": "Flu",
             "Billed Amount": 200.0, "Paid Amount": 180.0, "Policy Year": "2025"},
        ]
    )
    result = upload_sniffer.sniff_upload_kind(buf, "claims.xlsx")
    assert result["detected_kind"] == "claims"


def test_low_confidence_when_too_few_columns_match_anything():
    buf = _xlsx_bytes([{"Notes": "some free text", "Reference": "XYZ"}])
    result = upload_sniffer.sniff_upload_kind(buf, "mystery.xlsx")
    assert result["detected_kind"] is None
    assert result["confidence"] == "low"


def test_unreadable_spreadsheet_reports_low_confidence_rather_than_raising():
    result = upload_sniffer.sniff_upload_kind(io.BytesIO(b"not a real spreadsheet"), "broken.xlsx")
    assert result["detected_kind"] is None
    assert result["confidence"] == "low"


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakePdf:
    def __init__(self, pages_text):
        self.pages = [_FakePage(t) for t in pages_text]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_recognizes_a_dha_claims_report_pdf_by_its_own_header_text(monkeypatch):
    monkeypatch.setattr(
        upload_sniffer.pdfplumber, "open", lambda file: _FakePdf(["Health insurance claims record\nDHA Mandated Format"])
    )
    result = upload_sniffer.sniff_upload_kind(io.BytesIO(b""), "report.pdf")
    assert result["detected_kind"] == "claims"
    assert result["confidence"] == "high"


def test_recognizes_a_category_premium_quote_pdf_by_its_own_header_text(monkeypatch):
    monkeypatch.setattr(
        upload_sniffer.pdfplumber, "open", lambda file: _FakePdf(["Full Category Premium Calculation\nCAT A CAT B"])
    )
    result = upload_sniffer.sniff_upload_kind(io.BytesIO(b""), "quote.pdf")
    assert result["detected_kind"] == "quote"
    assert result["confidence"] == "high"


def test_falls_back_to_benefits_with_low_confidence_for_an_unrecognized_pdf(monkeypatch):
    monkeypatch.setattr(upload_sniffer.pdfplumber, "open", lambda file: _FakePdf(["Some other document entirely"]))
    result = upload_sniffer.sniff_upload_kind(io.BytesIO(b""), "mystery.pdf")
    assert result["detected_kind"] == "benefits"
    assert result["confidence"] == "low"


def test_flags_a_scanned_pdf_as_low_confidence_benefits(monkeypatch):
    monkeypatch.setattr(upload_sniffer.pdfplumber, "open", lambda file: _FakePdf(["", ""]))
    result = upload_sniffer.sniff_upload_kind(io.BytesIO(b""), "scanned.pdf")
    assert result["detected_kind"] == "benefits"
    assert result["confidence"] == "low"


def test_unrecognized_file_extension_returns_no_detected_kind():
    result = upload_sniffer.sniff_upload_kind(io.BytesIO(b"whatever"), "notes.docx")
    assert result["detected_kind"] is None


def test_extension_matching_is_case_insensitive():
    buf = _xlsx_bytes(
        [{"Category": "A", "Age": 30, "Gender": "M", "Marital Status": "Single", "Relation": "Employee", "Nationality": "India"}]
    )
    result = upload_sniffer.sniff_upload_kind(buf, "census.XLSX")
    assert result["detected_kind"] == "census"
