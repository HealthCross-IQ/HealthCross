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
    """Dispatches to whichever known claims-report layout this PDF matches.

    Different insurers implement their own "DHA-style" claims report with
    different row numbering, date formats, and column counts even though
    the underlying regulatory content is similar - rather than one parser
    trying to handle every variant, each known layout gets its own
    function, selected by a distinguishing marker in the text.
    """
    with pdfplumber.open(file) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)

        if "HEALTH INSURANCE CLAIMS RECORD" in full_text.upper() and "DHA Mandated Format" not in full_text:
            return _parse_format2_from_rows(extract_format2_rows(pdf))

        return _parse_format1_text(full_text)


def parse_claims_report_text(raw_text: str) -> Dict[str, Any]:
    """Parses format-1 (DHA Mandated Format) text directly - kept for
    callers/tests that already have extracted text rather than a PDF file.
    Format 2 needs the PDF's table structure (see parse_claims_report), so
    it isn't reachable through this text-only entry point.
    """
    return _parse_format1_text(raw_text)


def _parse_format1_text(raw_text: str) -> Dict[str, Any]:
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
        "treatment_type_breakdown": [],
        "monthly_paid": [],
    }

    diagnosis_rows: Dict[str, dict] = {}
    counts_rows: Dict[str, dict] = {}
    claims_by_type_numbers: List[List[float]] = []

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
                claims_by_type_numbers.append(numbers)

    if claims_by_type_numbers:
        # Row 14's entries (Direct Billing / Reimbursement) partition every
        # AED in the report, so summing their own IP/OP/Pharmacy/Dental/
        # Optical/Not-Yet-Classified columns gives the whole report's
        # treatment-type split - a cleaner total than summing the
        # per-diagnosis rows, which would only equal the grand total if
        # every diagnosis grouping happened to be captured.
        column_sums = [sum(row[i] for row in claims_by_type_numbers) for i in range(6)]
        result["treatment_type_breakdown"] = [
            {"type": name, "value": value}
            for name, value in zip(
                ["In-Patient", "Out-Patient", "Pharmacy", "Dental", "Optical", "Not Yet Classified"],
                column_sums,
            )
        ]

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


_FORMAT2_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
_FORMAT2_MONTH_CELL_RE = re.compile(r"^(\d{2})/(\d{4})$")


def _format2_parse_date(text: str) -> Optional[date]:
    match = _FORMAT2_DATE_RE.search(text or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d/%m/%Y").date()
    except ValueError:
        return None


def _cell(value: Any) -> str:
    return (value or "").replace("\n", " ").strip()


def _cell_number(value: Any) -> float:
    return _parse_number(_cell(value))


def _find_row_index(rows: List[list], key: str) -> Optional[int]:
    for i, row in enumerate(rows):
        if row and _cell(row[0]) == key:
            return i
    return None


def _find_row(rows: List[list], key: str) -> Optional[list]:
    idx = _find_row_index(rows, key)
    return rows[idx] if idx is not None else None


def extract_format2_rows(pdf: "pdfplumber.PDF") -> List[list]:
    """Flattens every bordered table on every page into one ordered row
    list. This layout's tables render with real cell borders pdfplumber
    can detect, which recovers each row cleanly (including multi-line
    wrapped labels, kept intact as one cell) - far more reliable here than
    parsing the page's plain extracted text, whose reading order badly
    scrambles some wrapped rows in this particular document.
    """
    rows: List[list] = []
    for page in pdf.pages:
        for table in page.find_tables():
            rows.extend(table.extract())
    return rows


def _parse_format2_from_rows(rows: List[list]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "policy_number": None,
        "policy_effective_date": None,
        "policy_expiry_date": None,
        "report_period_start": None,
        "report_period_end": None,
        "report_production_date": None,
        "total_paid": None,
        "incurred_not_reported": None,
        "opening_members": None,
        "closing_members": None,
        "diagnosis_breakdown": [],
        "provider_breakdown": [],
        "claims_by_type": [],
        # This layout's row 13 only splits In Network/Out of Network, not
        # IP/OP/Pharmacy/Dental/Optical like format 1's row 14 does - so
        # there's no reliable source for a treatment-type breakdown here.
        "treatment_type_breakdown": [],
        "monthly_paid": [],
    }

    # Policy number's value sits on its own row, directly below the "1" row.
    idx = _find_row_index(rows, "1")
    if idx is not None and idx + 1 < len(rows):
        match = re.match(r"([A-Za-z0-9]+)", _cell(rows[idx + 1][1]))
        if match:
            result["policy_number"] = match.group(1)

    date_fields = {
        "2a": "policy_effective_date",
        "2b": "policy_expiry_date",
        "3a": "report_period_start",
        "3b": "report_period_end",
        "3c": "report_production_date",
    }
    for key, field in date_fields.items():
        row = _find_row(rows, key)
        if row:
            result[field] = _format2_parse_date(_cell(row[2]))

    row_4a = _find_row(rows, "4a")
    if row_4a:
        result["total_paid"] = _cell_number(row_4a[2])
    row_4c = _find_row(rows, "4c")
    if row_4c:
        result["incurred_not_reported"] = _cell_number(row_4c[2])

    def _population_total(keys: List[str]) -> Optional[int]:
        total = 0.0
        found = False
        for key in keys:
            row = _find_row(rows, key)
            if row:
                total += sum(_cell_number(c) for c in row[2:8])
                found = True
        return int(total) if found else None

    result["opening_members"] = _population_total(["5a", "5b", "5c"])
    result["closing_members"] = _population_total(["6a", "6b", "6c"])

    counts_by_letter: Dict[str, dict] = {}
    for letter in "abcdefghij":
        row = _find_row(rows, f"10{letter}")
        if row:
            counts_by_letter[letter] = {
                "count": int(_cell_number(row[7])),
                "ip_count": int(_cell_number(row[2])),
            }

    for letter in "abcdefghij":
        row = _find_row(rows, f"9{letter}")
        if not row:
            continue
        counts = counts_by_letter.get(letter, {})
        result["diagnosis_breakdown"].append(
            {
                "label": _cell(row[1]),
                "value": _cell_number(row[7]),
                "count": counts.get("count", 0),
                "ip_value": _cell_number(row[2]),
                "ip_count": counts.get("ip_count", 0),
            }
        )

    for letter in "abcdefghij":
        row = _find_row(rows, f"11{letter}")
        if row:
            result["provider_breakdown"].append({"provider": _cell(row[1]), "value": _cell_number(row[7])})

    for key, label in (("13a", "In Network"), ("13b", "Out of Network")):
        row = _find_row(rows, key)
        if row:
            result["claims_by_type"].append({"type": label, "value": _cell_number(row[7])})

    monthly: List[dict] = []
    for letter in "abcdefghijklmnop":
        row = _find_row(rows, f"16{letter}")
        if not row:
            continue
        match = _FORMAT2_MONTH_CELL_RE.match(_cell(row[1]))
        if not match:
            continue
        monthly.append(
            {
                "year": int(match.group(2)),
                "month": [k for k, v in _MONTH_NAMES.items() if v == int(match.group(1))][0].capitalize(),
                "paid": _cell_number(row[5]),
                "partial": False,
            }
        )

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
