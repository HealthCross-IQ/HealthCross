"""Tests for app/ingestion/portfolio_claims.py - parses HealthCross's own
book-wide claims export. Uses a small synthetic .xlsx spreadsheet (same
column shape as the real .xlsb export) since pyxlsb can only read .xlsb
files, not write them - the xlsb-specific engine dispatch is verified
separately via monkeypatching rather than a real binary fixture.
"""
import openpyxl
import pytest

from app.ingestion.portfolio_claims import parse_portfolio_claims

HEADER = [
    "PATIENT_ID", "CLAIM_ID", "Claim Status", "GROUP_NAME", "CLIENT_NAME", "MSH_POLICY_NUMBER",
    "POLICY_START_DATE", "POLICY_END_DATE", "Member Start Date", "Member End Date",
    "DATE_OF_TREATMENT", "RELATION", "IP_OP_MATERNITY", "MEDICAL_CATEGORY", "PROVIDER_NAME",
    "DIAGNOSIS_CODE", "DIAGNOSIS_DESCRIPTION", "Claimed Amount AED", "Final Amount in AED",
]


@pytest.fixture()
def claims_xlsx(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADER)
    ws.append([
        "ACM0001", "CLM1", "Paid Claims", "Acme Sub LLC", "Acme Holdings", "QC1-ACM-A",
        "2025-01-01", "2026-01-01", "2025-01-01", "2026-01-01",
        "2025-06-01", "Main Insured", "OP", "PHARMACY", "Some Pharmacy",
        "J309", "Allergic rhinitis", 100.0, 90.0,
    ])
    ws.append([
        "ACM0001", "CLM2", "Paid Claims", "Acme Sub LLC", "Acme Holdings", "QC1-ACM-A",
        "2025-01-01", "2026-01-01", "2025-01-01", "2026-01-01",
        "2025-07-01", "Main Insured", "IP", "SURGERY", "Some Hospital",
        "K358", "Appendicitis", 5000.0, 4800.0,
    ])
    path = tmp_path / "claims.xlsx"
    wb.save(path)
    return path


def test_parses_claim_fields(claims_xlsx):
    with open(claims_xlsx, "rb") as f:
        rows = parse_portfolio_claims(f, "claims.xlsx")
    assert len(rows) == 2
    first = rows[0]
    assert first["patient_id"] == "ACM0001"
    assert first["group_name"] == "Acme Sub LLC"
    assert first["client_name"] == "Acme Holdings"
    assert first["msh_policy_number"] == "QC1-ACM-A"
    assert first["final_amount"] == 90.0
    assert first["date_of_treatment"].isoformat() == "2025-06-01"


def test_reads_xlsb_files_with_the_calamine_engine(monkeypatch, claims_xlsx):
    import pandas as pd

    captured = {}
    real_read_excel = pd.read_excel

    def _spy_read_excel(file, **kwargs):
        captured["engine"] = kwargs.get("engine")
        # Reuse the real xlsx fixture's content - only the engine kwarg
        # actually matters for this test, not real xlsb bytes.
        with open(claims_xlsx, "rb") as f:
            return real_read_excel(f, **kwargs)

    monkeypatch.setattr(pd, "read_excel", _spy_read_excel)
    with open(claims_xlsx, "rb") as f:
        parse_portfolio_claims(f, "claims.xlsb")
    assert captured["engine"] == "calamine"


def test_reads_xlsx_with_the_calamine_engine(monkeypatch, claims_xlsx):
    import pandas as pd

    captured = {}
    real_read_excel = pd.read_excel

    def _spy_read_excel(file, **kwargs):
        captured["engine"] = kwargs.get("engine")
        return real_read_excel(file, **kwargs)

    monkeypatch.setattr(pd, "read_excel", _spy_read_excel)
    with open(claims_xlsx, "rb") as f:
        parse_portfolio_claims(f, "claims.xlsx")
    assert captured["engine"] == "calamine"


@pytest.fixture()
def claims_xlsx_with_leading_blank_sheet(tmp_path):
    # A real HealthCross export shipped exactly this shape once: a
    # completely blank "Sheet1" ahead of the actual data, which now lives
    # on a separately named "Sheet 1" - reading only the first sheet
    # silently parsed 0 rows instead of erroring (see parse_portfolio_claims).
    wb = openpyxl.Workbook()
    blank = wb.active
    blank.title = "Sheet1"
    data = wb.create_sheet("Sheet 1")
    data.append(HEADER)
    data.append([
        "ACM0001", "CLM1", "Paid Claims", "Acme Sub LLC", "Acme Holdings", "QC1-ACM-A",
        "2025-01-01", "2026-01-01", "2025-01-01", "2026-01-01",
        "2025-06-01", "Main Insured", "OP", "PHARMACY", "Some Pharmacy",
        "J309", "Allergic rhinitis", 100.0, 90.0,
    ])
    path = tmp_path / "claims_with_blank_sheet.xlsx"
    wb.save(path)
    return path


def test_skips_a_leading_blank_sheet_to_find_the_real_data(claims_xlsx_with_leading_blank_sheet):
    with open(claims_xlsx_with_leading_blank_sheet, "rb") as f:
        rows = parse_portfolio_claims(f, "claims.xlsx")
    assert len(rows) == 1
    assert rows[0]["patient_id"] == "ACM0001"


def test_recognizes_the_contract_wording_for_amount_columns(tmp_path):
    # A newer HealthCross export renamed "Claimed Amount AED"/"Final Amount
    # in AED" to "Claimed Amount Contract"/"Final Amount Contract" (still
    # AED throughout - its own CONTRACT_CURRENCY column confirms that) -
    # without this alias claimed_amount/final_amount silently came back
    # None for every single row instead of erroring.
    header = [h if h not in ("Claimed Amount AED", "Final Amount in AED") else
               {"Claimed Amount AED": "Claimed Amount Contract", "Final Amount in AED": "Final Amount Contract"}[h]
               for h in HEADER]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    ws.append([
        "ACM0001", "CLM1", "Paid Claims", "Acme Sub LLC", "Acme Holdings", "QC1-ACM-A",
        "2025-01-01", "2026-01-01", "2025-01-01", "2026-01-01",
        "2025-06-01", "Main Insured", "OP", "PHARMACY", "Some Pharmacy",
        "J309", "Allergic rhinitis", 100.0, 90.0,
    ])
    path = tmp_path / "claims_contract_wording.xlsx"
    wb.save(path)

    with open(path, "rb") as f:
        rows = parse_portfolio_claims(f, "claims.xlsx")
    assert rows[0]["claimed_amount"] == 100.0
    assert rows[0]["final_amount"] == 90.0
