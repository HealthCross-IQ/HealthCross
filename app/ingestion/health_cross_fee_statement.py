"""Parser for QIC's "Statement of Outstanding" addressed to HealthCross
itself (one export per branch - Customer Code 216331 = Dubai, 293276 = Abu
Dhabi) - what QIC owes HC for policy-linked fees/commission. Like the bank
statement export, a details block (Customer Name/Code/Date) sits above the
real transaction table, so the parser scans for the header row rather than
assuming row 1. A totals block ("Total" / "Net Due to You") sits below the
data and is skipped naturally since those rows carry no Doc No.

IMPORTANT: each row's own "Division" column is a per-*policy* attribute
(which office administers that particular policy) - NOT a reliable signal
for which of the two branch statements (Dubai/Abu Dhabi) the row came
from. The Abu Dhabi statement's own rows can themselves read Division =
"Dubai Branch" if that's the office managing the policy in question. The
statement's true identity is its own "Customer Code" in the header block
(216331 = Dubai, 293276 = Abu Dhabi) - captured once per file as
`statement_customer_code` on every row, so an upload can safely replace
only its own statement's rows without touching the other branch's.
"""
from typing import Any, BinaryIO, List, Optional

import pandas as pd

from app.finance.common import normalize_doc_no

_EXPECTED_HEADERS = {"doc no", "policy no", "assured", "debit lc", "credit lc"}
_HEADER_SCAN_ROWS = 30


def _find_header_row(raw: pd.DataFrame) -> Optional[int]:
    for i in range(min(_HEADER_SCAN_ROWS, len(raw))):
        row_values = {str(v).strip().lower() for v in raw.iloc[i].tolist() if pd.notna(v)}
        if _EXPECTED_HEADERS.issubset(row_values):
            return i
    return None


def _find_labeled_value(raw: pd.DataFrame, label: str, scan_rows: int) -> Optional[str]:
    """Looks up a value from the details block above the header, where
    column A holds a label (e.g. "Customer Code") and column B its value.
    """
    for i in range(min(scan_rows, len(raw))):
        first_cell = raw.iloc[i, 0]
        if pd.notna(first_cell) and str(first_cell).strip().lower() == label.lower():
            second_cell = raw.iloc[i, 1] if raw.shape[1] > 1 else None
            return str(second_cell).strip() if pd.notna(second_cell) else None
    return None


def _str_or_none(value: Any) -> Any:
    if pd.isna(value):
        return None
    text = str(value).strip()
    # QIC uses "-" as its own placeholder for "not applicable" on this
    # export (Claim No, Chassis No, Invoice No, and non-policy rows all use
    # it) - treat it the same as a blank cell.
    if not text or text == "-":
        return None
    return text


def _amount(value: Any) -> float:
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text == "-":
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _date_or_none(value: Any):
    if pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    return parsed.date() if pd.notna(parsed) else None


def parse_health_cross_fee_statement(file: BinaryIO, filename: str, sheet_name: Any = 0) -> List[dict]:
    if filename.lower().endswith(".csv"):
        raw = pd.read_csv(file, header=None)
    else:
        raw = pd.read_excel(file, sheet_name=sheet_name, header=None)

    header_row = _find_header_row(raw)
    if header_row is None:
        raise ValueError(
            "Could not find the transaction table header (Doc No / Policy No / Assured / "
            "Debit LC / Credit LC) in this HealthCross Fee Statement"
        )

    customer_code = _find_labeled_value(raw, "Customer Code", header_row)

    headers = [str(v).strip().lower() if pd.notna(v) else "" for v in raw.iloc[header_row].tolist()]
    data = raw.iloc[header_row + 1 :].copy()
    data.columns = headers

    records = []
    for _, row in data.iterrows():
        doc_no_raw = row.get("doc no")
        # The totals block ("Total" / "Net Due to You") and any blank
        # trailing rows carry no Doc No - stop importing at that point.
        if pd.isna(doc_no_raw):
            continue
        records.append(
            {
                "doc_no": normalize_doc_no(doc_no_raw),
                "doc_no_raw": _str_or_none(doc_no_raw),
                "doc_date": _date_or_none(row.get("doc date")),
                "due_date": _date_or_none(row.get("due date")),
                "policy_no": _str_or_none(row.get("policy no")),
                "assured_name": _str_or_none(row.get("assured")),
                "invoice_no": _str_or_none(row.get("invoice no")),
                "debit_amount": _amount(row.get("debit lc")),
                "credit_amount": _amount(row.get("credit lc")),
                "transaction_type": _str_or_none(row.get("transaction type")),
                "division": _str_or_none(row.get("division")),
                "statement_customer_code": customer_code,
                "policy_from_date": _date_or_none(row.get("policy from date")),
                "policy_to_date": _date_or_none(row.get("policy to date")),
                "age_band": _str_or_none(row.get("age band")),
            }
        )
    return records
