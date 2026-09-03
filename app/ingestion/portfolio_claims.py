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


#: A sheet must map at least this many of the expected claim-line columns
#: to be treated as the claims data rather than a pivot/summary sheet. The
#: real export's data sheet maps nearly all of them; its pivot summaries
#: map zero or one, so anything in between is ambiguous enough to reject.
_MIN_MAPPED_CLAIM_COLUMNS = 5

#: Without these, a "claims" sheet can't drive any per-claim analysis at
#: all - a summary sheet that happens to carry a few matching header names
#: still isn't the claim-line data.
_REQUIRED_CLAIM_COLUMNS = ("final_amount", "date_of_treatment")


def _mapped_claim_column_count(sheet_df: pd.DataFrame) -> int:
    """How many expected claim-line fields this sheet's own headers
    actually resolve to (see PORTFOLIO_CLAIMS_ALIASES). 0 for a pivot
    summary whose real headers sit below a title row - those parse into
    "Unnamed: N" columns that match no alias."""
    if sheet_df.empty:
        return 0
    mapped = map_columns(sheet_df, PORTFOLIO_CLAIMS_ALIASES)
    present = set(mapped.columns) & set(PORTFOLIO_CLAIMS_ALIASES)
    if not all(col in present for col in _REQUIRED_CLAIM_COLUMNS):
        return 0
    return len(present)


def _best_claims_sheet(sheets: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Whichever sheet actually holds the per-claim-line data - see the
    comment in parse_portfolio_claims for why "first non-empty" isn't
    good enough on a real multi-sheet export. Falls back to the first
    non-empty sheet when nothing qualifies, so a plain single-sheet
    upload (and its own error handling downstream) behaves as before."""
    scored = [
        (_mapped_claim_column_count(sheet_df), len(sheet_df), sheet_df)
        for sheet_df in sheets.values()
    ]
    qualifying = [s for s in scored if s[0] >= _MIN_MAPPED_CLAIM_COLUMNS]
    if qualifying:
        return max(qualifying, key=lambda s: (s[0], s[1]))[2]
    return next((sheet_df for sheet_df in sheets.values() if not sheet_df.empty), pd.DataFrame())


def parse_portfolio_claims(file: BinaryIO, filename: str) -> List[dict]:
    lower_name = filename.lower()
    if lower_name.endswith(".csv"):
        df = pd.read_csv(file)
    else:
        # calamine (a Rust reader) parses this book-wide export - tens of
        # thousands of rows across 50+ columns - roughly twice as fast as
        # openpyxl/pyxlsb (measured on a real ~80k-row export: ~10s vs
        # ~20s), which noticeably matters here since the whole upload
        # request blocks until parsing finishes. It also already returns
        # proper datetime values for a .xlsb's date columns rather than
        # raw Excel serial numbers, so the is_numeric_dtype fallback below
        # is now just a safety net, not the normal path for this format.
        #
        # A real export ships several sheets alongside the claim-line data:
        # pivot summaries ("Premium", "Claims", "LOSS RATIO", "Loading"),
        # a partial extract ("Detail1"), and the full per-claim-line sheet
        # ("DATA"). Picking the first non-empty sheet grabbed a pivot
        # summary whose headers sit below a title row, so every aliased
        # column mapped to nothing and each row parsed to all-None WITHOUT
        # raising - silent garbage. Pick by CONTENT instead: whichever
        # sheet maps the most expected claim-line columns wins, ties broken
        # by row count so the full export beats a partial extract of it.
        sheets = pd.read_excel(file, engine="calamine", sheet_name=None)
        df = _best_claims_sheet(sheets)

    df = map_columns(df, PORTFOLIO_CLAIMS_ALIASES)

    # Parsed one column at a time (vectorized) rather than value-by-value -
    # with tens of thousands of claim rows across 5 date columns, a
    # per-value pd.to_datetime() call in a Python loop is dramatically
    # slower than parsing each whole column in one vectorized call.
    for date_col in (
        "policy_start_date", "policy_end_date", "member_start_date", "member_end_date", "date_of_treatment",
        "date_reception",
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
                "date_reception": _date_or_none(row.get("date_reception")),
                "relation": _str_or_none(row.get("relation")),
                "ip_op_maternity": _str_or_none(row.get("ip_op_maternity")),
                "medical_category": _str_or_none(row.get("medical_category")),
                "medical_act": _str_or_none(row.get("medical_act")),
                "provider_name": _str_or_none(row.get("provider_name")),
                "diagnosis_code": _str_or_none(row.get("diagnosis_code")),
                "diagnosis_description": _str_or_none(row.get("diagnosis_description")),
                "claimed_amount": _float_or_none(row.get("claimed_amount")),
                "final_amount": _float_or_none(row.get("final_amount")),
                "source_filename": filename,
            }
        )
    return records
