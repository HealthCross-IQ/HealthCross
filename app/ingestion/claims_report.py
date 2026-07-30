"""Parser for the DHA (Dubai Health Authority) Mandated Format health
insurance claims report.

This is a regulatory template - its row numbering and labels are fixed by
DHA rule, not chosen by any one insurer, so a row-number-keyed parser holds
up across different insurers' exports rather than being tied to one
broker's layout.

These reports are real, selectable PDF text (not scanned images). Most rows
extract cleanly, but the monthly-claims-paid section specifically tends to
have PDF-rendering-inserted whitespace inside its numbers (e.g. "2 03,861"
for "203,861") - that whitespace gets stripped only within that row's own
captured number, not globally, since a blanket whitespace-between-digits
fix would just as easily merge two genuinely separate numbers on a normal
row (e.g. two adjacent columns "1,772,027 0") into one.
"""
import re
from datetime import date, datetime
from typing import Any, BinaryIO, Dict, List, Optional

import pdfplumber

_DATE_RE = re.compile(r"(\d{1,2}\s+\w{3}\s+\d{4})")
_MONTH_ROW_RE = re.compile(r"^(20\d{2})\s+([A-Za-z]{3})\s+([\d,\s]+?)\s*$")
_ROW_PREFIX_RE = re.compile(r"^(\d+)([a-z]?)\s*(.*)$")

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_number(token: str) -> float:
    # Strips ALL whitespace, not just the ends - safe here because this only
    # ever runs on a single already-isolated numeric token (e.g. one row's
    # trailing 7 columns, or one month's value), never on a whole line where
    # stray internal whitespace could just as easily separate two distinct
    # numbers rather than split one.
    token = re.sub(r"\s+", "", token).replace(",", "")
    if not token or token == "-":
        return 0.0
    try:
        return float(token)
    except ValueError:
        return 0.0


def _parse_date(text: str) -> Optional[date]:
    match = _DATE_RE.search(text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d %b %Y").date()
    except ValueError:
        return None


def _split_label_and_seven_numbers(rest: str):
    """Rows shaped like: <label> <IP> <OP> <Pharmacy> <Dental> <Optical> <NotYetClassified> <Total>"""
    tokens = rest.split()
    if len(tokens) < 7:
        return rest.strip(), None
    numbers = tokens[-7:]
    label = " ".join(tokens[:-7]).strip()
    return label, [_parse_number(t) for t in numbers]


def parse_claims_report(file: BinaryIO, filename: str) -> Dict[str, Any]:
    with pdfplumber.open(file) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return parse_claims_report_text(full_text)


def parse_claims_report_text(raw_text: str) -> Dict[str, Any]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    result: Dict[str, Any] = {
        "policy_number": None,
        "policy_effective_date": None,
        "policy_expiry_date": None,
        "report_period_start": None,
        "report_period_end": None,
        "report_production_date": None,
        "total_paid": None,
        "incurred_not_reported": None,
        "opening_female": None,
        "opening_male": None,
        "closing_female": None,
        "closing_male": None,
        "opening_members": None,
        "closing_members": None,
        "diagnosis_breakdown": [],
        "provider_breakdown": [],
        "claims_by_type": [],
        "monthly_paid": [],
    }

    diagnosis_rows: Dict[str, dict] = {}
    counts_rows: Dict[str, dict] = {}

    for line in lines:
        match = _ROW_PREFIX_RE.match(line)
        if not match:
            continue
        row_num, row_letter, rest = match.group(1), match.group(2), match.group(3)
        key = f"{row_num}{row_letter}"

        if key == "2":
            # rest also carries an adjacent "Group ID / Claims Paid" info box
            # (e.g. "...54773 54775 1,772,027.07"); the policy number is the
            # first bare all-digit token, not the largest/first number.
            for token in rest.split():
                if token.isdigit():
                    result["policy_number"] = token
                    break
        elif key == "3a":
            result["policy_effective_date"] = _parse_date(rest)
        elif key == "3b":
            result["policy_expiry_date"] = _parse_date(rest)
        elif key == "4a":
            result["report_period_start"] = _parse_date(rest)
        elif key == "4b":
            result["report_period_end"] = _parse_date(rest)
        elif key == "4c":
            result["report_production_date"] = _parse_date(rest)
        elif key == "5a":
            nums = re.findall(r"[\d,]+\.?\d*", rest)
            if nums:
                result["total_paid"] = _parse_number(nums[0])
        elif key == "5c":
            nums = re.findall(r"[\d,]+\.?\d*", rest)
            if nums:
                result["incurred_not_reported"] = _parse_number(nums[-1])
        elif key == "6a":
            nums = re.findall(r"[\d,]+", rest)
            if nums:
                result["opening_female"] = int(_parse_number(nums[-1]))
        elif key == "6b":
            nums = re.findall(r"[\d,]+", rest)
            if nums:
                result["opening_male"] = int(_parse_number(nums[-1]))
        elif key == "7a":
            nums = re.findall(r"[\d,]+", rest)
            if nums:
                result["closing_female"] = int(_parse_number(nums[-1]))
        elif key == "7b":
            nums = re.findall(r"[\d,]+", rest)
            if nums:
                result["closing_male"] = int(_parse_number(nums[-1]))
        elif row_num == "10" and row_letter:
            label, numbers = _split_label_and_seven_numbers(rest)
            if numbers:
                diagnosis_rows[row_letter] = {"label": label, "value": numbers[6], "ip_value": numbers[0]}
        elif row_num == "11" and row_letter:
            _, numbers = _split_label_and_seven_numbers(rest)
            if numbers:
                counts_rows[row_letter] = {"count": int(numbers[6]), "ip_count": int(numbers[0])}
        elif row_num == "12" and row_letter:
            label, numbers = _split_label_and_seven_numbers(rest)
            if numbers:
                result["provider_breakdown"].append({"provider": label, "value": numbers[6]})
        elif row_num == "14" and row_letter:
            label, numbers = _split_label_and_seven_numbers(rest)
            if numbers:
                result["claims_by_type"].append({"type": label, "value": numbers[6]})

    for letter, entry in diagnosis_rows.items():
        counts = counts_rows.get(letter, {})
        result["diagnosis_breakdown"].append(
            {
                "label": entry["label"],
                "value": entry["value"],
                "count": counts.get("count", 0),
                "ip_value": entry["ip_value"],
                "ip_count": counts.get("ip_count", 0),
            }
        )

    if result["opening_female"] is not None and result["opening_male"] is not None:
        result["opening_members"] = result["opening_female"] + result["opening_male"]
    if result["closing_female"] is not None and result["closing_male"] is not None:
        result["closing_members"] = result["closing_female"] + result["closing_male"]

    monthly: List[dict] = []
    for line in lines:
        match = _MONTH_ROW_RE.match(line)
        if match and match.group(2).lower() in _MONTH_NAMES:
            monthly.append(
                {
                    "year": int(match.group(1)),
                    "month": match.group(2),
                    "paid": _parse_number(match.group(3)),
                    "partial": False,
                }
            )

    # Flag the first month as partial if the policy didn't start on the 1st -
    # a stub month understates the true monthly run-rate if averaged as-is.
    if monthly and result["policy_effective_date"]:
        first = monthly[0]
        month_num = _MONTH_NAMES.get(first["month"].lower())
        if (
            month_num == result["policy_effective_date"].month
            and first["year"] == result["policy_effective_date"].year
            and result["policy_effective_date"].day > 1
        ):
            first["partial"] = True

    result["monthly_paid"] = monthly
    return result


def first_full_months(monthly_paid: List[dict], count: int = 6) -> List[float]:
    """Return the paid-claims values for the first `count` non-partial months.

    This is the standing input to the burning-cost projection
    (app/scoring/rules/claims_projection.py) - it always skips any month
    flagged partial (a policy-inception stub) rather than averaging it in.
    """
    full_months = [m for m in monthly_paid if not m.get("partial")]
    if len(full_months) < count:
        raise ValueError(f"Need at least {count} full months of claims data, found {len(full_months)}.")
    return [m["paid"] for m in full_months[:count]]
