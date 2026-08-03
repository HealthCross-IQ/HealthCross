"""Parses HealthCross's own book-wide claims export ("HealthCross Claims" -
one row per claim line across every group/policy currently on book, not a
single case's claims ledger) for Portfolio Analysis
(app/scoring/rules/portfolio_analysis.py).

Same per-claim-line shape as app/ingestion/claims_ledger.py (reuses its
column aliases directly) plus the group/policy identifiers a book-wide
export carries that a single case's own ledger upload doesn't need -
GROUP_NAME/CLIENT_NAME for display, and MSH_POLICY_NUMBER to join against
app/ingestion/portfolio_members.py's own msh_policy_number.

HealthCross's own export is a .xlsb (Excel binary) file - pandas needs
the pyxlsb engine for that specific format, unlike every other ingestion
module's plain .xlsx/.csv.
"""
from typing import Any, BinaryIO, Dict, List

import pandas as pd

from app.ingestion.claims_ledger import CLAIMS_LEDGER_ALIASES
from app.ingestion.column_mapping import map_columns

PORTFOLIO_CLAIMS_ALIASES: Dict[str, List[str]] = {
    **CLAIMS_LEDGER_ALIASES,
    "group_name": ["group_name", "group name"],
    "client_name": ["client_name", "client name"],
    "msh_policy_number": ["msh_policy_number", "msh policy number"],
}


def parse_portfolio_claims(file: BinaryIO, filename: str) -> List[dict]:
    lower_name = filename.lower()
    if lower_name.endswith(".csv"):
        df = pd.read_csv(file)
    elif lower_name.endswith(".xlsb"):
        df = pd.read_excel(file, engine="pyxlsb")
    else:
        df = pd.read_excel(file)

    df = map_columns(df, PORTFOLIO_CLAIMS_ALIASES)

    # Parsed one column at a time (vectorized) rather than value-by-value -
    # with tens of thousands of claim rows across 5 date columns, a
    # per-value pd.to_datetime() call in a Python loop is dramatically
    # slower than parsing each whole column in one vectorized call.
    for date_col in (
        "policy_start_date", "policy_end_date", "member_start_date", "member_end_date", "date_of_treatment",
    ):
        if date_col in df.columns:
            if pd.api.types.is_numeric_dtype(df[date_col]):
                # .xlsb (unlike .xlsx via openpyxl) hands back raw Excel serial
                # date numbers rather than real datetimes - 1899-12-30 is
                # Excel's own epoch (already accounts for its leap-year bug).
                df[date_col] = pd.to_datetime(df[date_col], unit="D", origin="1899-12-30", errors="coerce")
            else:
                df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

    def _date_or_none(value: Any):
        return value.date() if pd.notna(value) else None

    def _str_or_none(value: Any) -> Any:
        return str(value).strip() if pd.notna(value) else None

    def _float_or_none(value: Any) -> Any:
        return float(value) if pd.notna(value) else None

    records = []
    for row in df.to_dict("records"):
        records.append(
            {
                "patient_id": _str_or_none(row.get("patient_id")),
                "claim_id": _str_or_none(row.get("claim_id")),
                "claim_status": _str_or_none(row.get("claim_status")),
                "group_name": _str_or_none(row.get("group_name")),
                "client_name": _str_or_none(row.get("client_name")),
                "msh_policy_number": _str_or_none(row.get("msh_policy_number")),
                "policy_start_date": _date_or_none(row.get("policy_start_date")),
                "policy_end_date": _date_or_none(row.get("policy_end_date")),
                "member_start_date": _date_or_none(row.get("member_start_date")),
                "member_end_date": _date_or_none(row.get("member_end_date")),
                "date_of_treatment": _date_or_none(row.get("date_of_treatment")),
                "relation": _str_or_none(row.get("relation")),
                "ip_op_maternity": _str_or_none(row.get("ip_op_maternity")),
                "medical_category": _str_or_none(row.get("medical_category")),
                "provider_name": _str_or_none(row.get("provider_name")),
                "diagnosis_code": _str_or_none(row.get("diagnosis_code")),
                "diagnosis_description": _str_or_none(row.get("diagnosis_description")),
                "claimed_amount": _float_or_none(row.get("claimed_amount")),
                "final_amount": _float_or_none(row.get("final_amount")),
                "source_filename": filename,
            }
        )
    return records
