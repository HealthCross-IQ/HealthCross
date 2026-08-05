"""Parser for an HC bank account statement export (e.g. the
"Account transactions Statement Report" format: an account-details block -
Account Holder Name / Account Number / Account Type / Currency / statement
period - sits above the real transaction table, rather than a header on
row 1 like most spreadsheets this app parses elsewhere).

Used to confirm a QIC payment marked "Received" on a PaymentTrackerEntry
actually landed in the bank (see app.finance.reconciliation), and as the
source-of-truth for cash-flow reporting.
"""
from typing import Any, BinaryIO, List, Optional

import pandas as pd

_EXPECTED_HEADERS = {"date", "value date", "reference number", "description"}
# The account-details block and the real transaction header are both near
# the top of the sheet on every real export seen - cap the scan rather than
# reading the whole file just to locate one row.
_HEADER_SCAN_ROWS = 30


def _find_header_row(raw: pd.DataFrame) -> Optional[int]:
    for i in range(min(_HEADER_SCAN_ROWS, len(raw))):
        row_values = {str(v).strip().lower() for v in raw.iloc[i].tolist() if pd.notna(v)}
        if _EXPECTED_HEADERS.issubset(row_values):
            return i
    return None


def _find_labeled_value(raw: pd.DataFrame, label: str) -> Optional[str]:
    """Looks up a value from the account-details block, where column A
    holds a label (e.g. "Account Number") and column B holds its value.
    """
    for i in range(min(_HEADER_SCAN_ROWS, len(raw))):
        first_cell = raw.iloc[i, 0]
        if pd.notna(first_cell) and str(first_cell).strip().lower() == label.lower():
            second_cell = raw.iloc[i, 1] if raw.shape[1] > 1 else None
            return str(second_cell).strip() if pd.notna(second_cell) else None
    return None


def _amount(value: Any) -> float:
    """Amount columns come through as either a real number or a string with
    thousands separators and a leading sign (e.g. "-175.00", "297,313.93").
    A blank cell means nothing was posted to that column - 0, not missing.
    """
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _float_or_none(value: Any) -> Any:
    if pd.isna(value):
        return None
    return _amount(value)


def _date_or_none(value: Any):
    if pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    return parsed.date() if pd.notna(parsed) else None


def _str_or_none(value: Any) -> Any:
    if pd.isna(value):
        return None
    return str(value).strip() or None


def parse_bank_statement(file: BinaryIO, filename: str, sheet_name: Any = 0) -> List[dict]:
    if filename.lower().endswith(".csv"):
        raw = pd.read_csv(file, header=None)
    else:
        raw = pd.read_excel(file, sheet_name=sheet_name, header=None)

    header_row = _find_header_row(raw)
    if header_row is None:
        raise ValueError(
            "Could not find the transaction table header (Date / Value Date / "
            "Reference Number / Description) in this bank statement"
        )

    account_number = _find_labeled_value(raw, "Account Number")
    currency = _find_labeled_value(raw, "Account Currency") or "AED"

    headers = [str(v).strip().lower() if pd.notna(v) else "" for v in raw.iloc[header_row].tolist()]
    data = raw.iloc[header_row + 1 :].copy()
    data.columns = headers

    records = []
    for _, row in data.iterrows():
        date_value = row.get("date")
        if pd.isna(date_value):
            continue
        records.append(
            {
                "account_number": account_number,
                "txn_date": _date_or_none(date_value),
                "value_date": _date_or_none(row.get("value date")),
                "reference_number": _str_or_none(row.get("reference number")),
                "description": _str_or_none(row.get("description")),
                "credit_amount": _amount(row.get("credit")),
                "debit_amount": abs(_amount(row.get("debit"))),
                "balance": _float_or_none(row.get("balance")),
                "currency": currency,
            }
        )
    return records
