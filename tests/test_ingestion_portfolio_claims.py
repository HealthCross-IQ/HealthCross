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
            return real_read_excel(f, engine="calamine")

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
