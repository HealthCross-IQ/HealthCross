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
        upper_text = full_text.upper()

        if "POLICY PERFORMANCE REVIEW" in upper_text:
            # Not a DHA-mandated claims record at all - an insurer-issued
            # analytics dashboard (a Power BI export), covering the same
            # ground at a policy-summary level rather than DHA's regulatory
            # line-item detail. Its per-benefit/diagnosis/age breakdown
            # tables extract unreliably (Power BI's own "Power BI Desktop"
            # watermark text is interleaved character-by-character into
            # nearby headings, and find_tables() returns badly malformed
            # rows for the denser tables) - only the monthly paid-claims
            # figures, review period, and average lives extract cleanly as
            # plain text, so that's all this parser attempts.
            return _parse_ppr_text(full_text)

        if "HEALTH INSURANCE CLAIMS RECORD" in upper_text and "DHA Mandated Format" not in full_text:
            if "DHA CLAIMS REPORT" in upper_text:
                # A fourth real-world variant (a MetLife-issued DHA Claims
                # Report) that also lacks "DHA Mandated Format" and shares
                # format 3's 6-age-band population census, but - unlike
                # format 3 - renders with real, reliably-detected bordered
                # tables (format 3's own file is the one whose plain text
                # extraction is unreadable; this one's isn't), a 6th
                # "Others" claims category alongside IP/OP/Pharmacy/Dental/
                # Optical throughout, and row 17's month given as a bare
                # "01".."12" rather than a name, with row-letters recycled
                # (17a-17g) once the report spans a second calendar year.
                return _parse_format4_from_rows(extract_format2_rows(pdf))
            if "POPULATION CENSUS" in upper_text and "OVER 65" in upper_text:
                # A third real-world variant (seen on a real DAL Group
                # report) that also lacks "DHA Mandated Format" but doesn't
                # share format 2's bordered-table structure or column
                # counts either: its own population census splits by 6 age
                # bands (ending in an "Over 65" column) across 3 gender/
                # marital-status rows rather than a single opening/closing
                # female+male figure, and its member-type/diagnosis/
                # provider/network tables run IP/OP/Total or IP/OP/
                # Pharmacy/Dental/Optical/Total rather than format 1's
                # fixed 7 columns - real, extractable text throughout, so
                # this reads it directly rather than via find_tables().
                return _parse_format3_text(full_text)
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
        "claims_by_member_type_value": [],
        "claims_by_member_type_count": [],
        "monthly_paid": [],
    }

    diagnosis_rows: Dict[str, dict] = {}
    counts_rows: Dict[str, dict] = {}
    claims_by_type_numbers: List[List[float]] = []
    member_type_value_rows: Dict[str, dict] = {}
    member_type_count_rows: Dict[str, dict] = {}
    _TREATMENT_TYPE_NAMES = ["In-Patient", "Out-Patient", "Pharmacy", "Dental", "Optical", "Not Yet Classified"]

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
        elif row_num == "8" and row_letter:
            label, numbers = _split_label_and_seven_numbers(rest)
            if numbers:
                member_type_value_rows[row_letter] = {"label": label, "numbers": numbers}
        elif row_num == "9" and row_letter:
            label, numbers = _split_label_and_seven_numbers(rest)
            if numbers:
                member_type_count_rows[row_letter] = {"label": label, "numbers": numbers}
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
            for name, value in zip(_TREATMENT_TYPE_NAMES, column_sums)
        ]

    def _member_type_rows(raw_rows: Dict[str, dict], cast) -> List[dict]:
        # Row 8/9's own letters (a/b/c/.../"Totals") aren't in a fixed,
        # guaranteed order across every real report, so sort by letter
        # (matching a/b/c.. row lettering) and drop whichever row is the
        # report's own "Totals" line - that's a cross-check value, not a
        # real member-type category, and would otherwise double the real
        # relations' figures if summed together.
        rows = []
        for letter in sorted(raw_rows.keys()):
            entry = raw_rows[letter]
            if entry["label"].strip().lower() == "totals":
                continue
            numbers = entry["numbers"]
            rows.append(
                {
                    "relation": entry["label"],
                    "in_patient": cast(numbers[0]),
                    "out_patient": cast(numbers[1]),
                    "pharmacy": cast(numbers[2]),
                    "dental": cast(numbers[3]),
                    "optical": cast(numbers[4]),
                    "not_yet_classified": cast(numbers[5]),
                    "total": cast(numbers[6]),
                }
            )
        return rows

    result["claims_by_member_type_value"] = _member_type_rows(member_type_value_rows, float)
    result["claims_by_member_type_count"] = _member_type_rows(member_type_count_rows, int)

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
            continue

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


_FORMAT3_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
# A second real date shape ("15-Aug-2025" rather than "15/08/2025").
_FORMAT3_DATE_DASH_RE = re.compile(r"(\d{1,2}-[A-Za-z]{3}-\d{4})")
_FORMAT3_MONTHLY_ROW_RE = re.compile(r"^([A-Za-z]+)\s+\d{1,2}\s+(\d{4})\s+([\d,]+)\s*$")
# A second real export shape for item 17 (seen on a real Arabia Insurance/
# Maxtube report): each row keeps its own month-ending date spelled out in
# full ("31/10/2025") ahead of the year, and its value carries decimals -
# "17a October 31/10/2025 2025 4,219.80" - rather than the bare
# "October 15 2025 4,219" shape _FORMAT3_MONTHLY_ROW_RE expects.
_FORMAT3_MONTHLY_ROW_ALT_RE = re.compile(r"^([A-Za-z]+)\s+\d{2}/\d{2}/\d{4}\s+(\d{4})\s+([\d,]+\.\d{2})\s*$")
# A third real export shape: no day-of-month at all, just "Aug 2025
# 54,977" - a future month still blank in the report (e.g. "Jun 2026"
# with nothing after the year) correctly fails to match and is skipped
# rather than recorded as a zero.
_FORMAT3_MONTHLY_ROW_NO_DAY_RE = re.compile(r"^([A-Za-z]+)\s+(\d{4})\s+([\d,]+\.?\d*)\s*$")
# Real row numbers only go up to 18, always followed by whitespace or
# end-of-line before any letter/rest - unlike the generic _ROW_PREFIX_RE,
# this deliberately does NOT match e.g. "607836-001 / 607836-004 / ..." (a
# policy-number continuation line), which would otherwise be mistaken for
# a bogus new row "607836" and silently dropped instead of being collected
# as part of the real policy number value. group(3) (the rest of the line)
# is None, not "", when there's nothing after the row number/letter.
_FORMAT3_ROW_RE = re.compile(r"^(\d{1,2})([a-z]?)(?:\s+(.*))?$")


def _format3_parse_date(text: str) -> Optional[date]:
    text = text or ""
    match = _FORMAT3_DATE_RE.search(text)
    if match:
        try:
            return datetime.strptime(match.group(1), "%d/%m/%Y").date()
        except ValueError:
            return None
    match = _FORMAT3_DATE_DASH_RE.search(text)
    if match:
        try:
            return datetime.strptime(match.group(1), "%d-%b-%Y").date()
        except ValueError:
            return None
    return None


def _format3_last_number(rest: str) -> Optional[float]:
    nums = re.findall(r"[\d,]+\.?\d*", rest)
    return _parse_number(nums[-1]) if nums else None


def _format3_trailing_numbers(rest: str, count: int):
    """Rows shaped like: <label> <n1> <n2> ... <nCount> - label is
    whatever text remains once the last `count` whitespace-separated
    numeric tokens are peeled off the end.
    """
    tokens = rest.split()
    if len(tokens) < count:
        return rest.strip(), None
    numbers = tokens[-count:]
    if not all(re.fullmatch(r"[\d,]+\.?\d*", t) for t in numbers):
        return rest.strip(), None
    label = " ".join(tokens[:-count]).strip()
    return label, [_parse_number(t) for t in numbers]


def _parse_format3_text(raw_text: str) -> Dict[str, Any]:
    """A third real-world "Health insurance claims record" variant (seen on
    a real DAL Group report) that, like format 2, lacks the literal "DHA
    Mandated Format" marker format 1 relies on, but shares neither format
    2's bordered-table structure nor format 1's fixed 7-trailing-number
    row shape. Its own tables run 3 columns (IP/OP/Total) for diagnosis
    and provider breakdowns, 6 columns (IP/OP/Pharmacy/Dental/Optical/
    Total) for member-type and network breakdowns (no "Not Yet
    Classified" column at all), and its population census splits 3 rows
    (Male/Single Female/Married Female) by 6 age bands each rather than a
    single opening/closing female+male figure - real, extractable text
    throughout, parsed directly by row number rather than via
    find_tables().
    """
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
        "opening_members": None,
        "closing_members": None,
        "diagnosis_breakdown": [],
        "provider_breakdown": [],
        "claims_by_type": [],
        "treatment_type_breakdown": [],
        "claims_by_member_type_value": [],
        "claims_by_member_type_count": [],
        "monthly_paid": [],
    }

    diagnosis_rows: Dict[str, dict] = {}
    counts_rows: Dict[str, dict] = {}
    member_type_value_rows: Dict[str, dict] = {}
    member_type_count_rows: Dict[str, dict] = {}
    network_value_rows: Dict[str, dict] = {}
    population_open: List[List[float]] = []
    population_close: List[List[float]] = []
    policy_number_parts: List[str] = []
    collecting_policy_number = False
    # Row "2 Policy number" only ever appears once, near the top - later
    # prose can start with a bare digit too (e.g. "2 ongoing cancer cases
    # with expected utilization..." under "18 Patient Support Programs"),
    # which must never be mistaken for a second, later occurrence of row 2.
    past_policy_number_section = False

    for line in lines:
        match = _FORMAT3_ROW_RE.match(line)
        if not match:
            if collecting_policy_number:
                policy_number_parts.append(line)
            continue
        row_num, row_letter, rest = match.group(1), match.group(2), match.group(3) or ""

        # Row 2's own value (possibly several policy numbers) sits on the
        # line(s) AFTER the "2 Policy number" label line itself, not on the
        # same line - collect every following non-numbered-row line until
        # the next top-level row ("3 Policy period") starts.
        if row_num == "2" and not row_letter and not past_policy_number_section:
            # "rest" here is just this row's own leftover label text ("2
            # Policy number" - the "Policy number" is the field's name, not
            # part of any real value) whenever there's nothing else on the
            # same line - stripped off so it's never mistaken for (or
            # prepended to) the real value collected from later lines.
            remainder = re.sub(r"(?i)^policy number\s*", "", rest).strip()
            collecting_policy_number = not remainder
            if remainder:
                policy_number_parts.append(remainder)
            continue
        collecting_policy_number = False
        if row_num == "3":
            past_policy_number_section = True

        if row_num == "3" and row_letter == "a":
            result["policy_effective_date"] = _format3_parse_date(rest)
        elif row_num == "3" and row_letter == "b":
            result["policy_expiry_date"] = _format3_parse_date(rest)
        elif row_num == "4" and row_letter == "a":
            result["report_period_start"] = _format3_parse_date(rest)
        elif row_num == "4" and row_letter == "b":
            result["report_period_end"] = _format3_parse_date(rest)
        elif row_num == "4" and row_letter == "c":
            result["report_production_date"] = _format3_parse_date(rest)
        elif row_num == "5" and row_letter == "a":
            result["total_paid"] = _format3_last_number(rest)
        elif row_num == "5" and row_letter == "c":
            result["incurred_not_reported"] = _format3_last_number(rest)
        elif row_num == "6" and row_letter:
            _, numbers = _format3_trailing_numbers(rest, 6)
            if numbers:
                population_open.append(numbers)
        elif row_num == "7" and row_letter:
            _, numbers = _format3_trailing_numbers(rest, 6)
            if numbers:
                population_close.append(numbers)
        elif row_num == "8" and row_letter:
            label, numbers = _format3_trailing_numbers(rest, 6)
            if numbers:
                member_type_value_rows[row_letter] = {"label": label, "numbers": numbers}
        elif row_num == "9" and row_letter:
            label, numbers = _format3_trailing_numbers(rest, 6)
            if numbers:
                member_type_count_rows[row_letter] = {"label": label, "numbers": numbers}
        elif row_num == "10" and row_letter:
            label, numbers = _format3_trailing_numbers(rest, 3)
            if numbers:
                diagnosis_rows[row_letter] = {"label": label, "value": numbers[2], "ip_value": numbers[0]}
        elif row_num == "11" and row_letter:
            _, numbers = _format3_trailing_numbers(rest, 3)
            if numbers:
                counts_rows[row_letter] = {"count": int(numbers[2]), "ip_count": int(numbers[0])}
        elif row_num == "12" and row_letter:
            label, numbers = _format3_trailing_numbers(rest, 3)
            if numbers:
                result["provider_breakdown"].append({"provider": label, "value": numbers[2]})
        elif row_num == "14" and row_letter:
            label, numbers = _format3_trailing_numbers(rest, 6)
            if numbers:
                result["claims_by_type"].append({"type": label, "value": numbers[5]})
                network_value_rows[row_letter] = numbers

    if population_open:
        result["opening_members"] = int(sum(sum(row) for row in population_open))
    if population_close:
        result["closing_members"] = int(sum(sum(row) for row in population_close))

    if network_value_rows:
        # Rows 14a/14b (In network / Out of network) partition every AED
        # in the report by IP/OP/Pharmacy/Dental/Optical - summing them
        # gives the whole report's treatment-type split, same derivation
        # format 1 uses from its own equivalent row. This variant has no
        # "Not Yet Classified" column at all, so that category is always 0.
        _TREATMENT_TYPE_NAMES = ["In-Patient", "Out-Patient", "Pharmacy", "Dental", "Optical"]
        column_sums = [sum(row[i] for row in network_value_rows.values()) for i in range(5)]
        result["treatment_type_breakdown"] = [
            {"type": name, "value": value} for name, value in zip(_TREATMENT_TYPE_NAMES, column_sums)
        ] + [{"type": "Not Yet Classified", "value": 0.0}]

    def _member_type_rows(raw_rows: Dict[str, dict], cast) -> List[dict]:
        rows = []
        for letter in sorted(raw_rows.keys()):
            entry = raw_rows[letter]
            if entry["label"].strip().lower() in ("total", "totals"):
                continue
            numbers = entry["numbers"]
            rows.append(
                {
                    "relation": entry["label"],
                    "in_patient": cast(numbers[0]),
                    "out_patient": cast(numbers[1]),
                    "pharmacy": cast(numbers[2]),
                    "dental": cast(numbers[3]),
                    "optical": cast(numbers[4]),
                    "not_yet_classified": cast(0),
                    "total": cast(numbers[5]),
                }
            )
        return rows

    result["claims_by_member_type_value"] = _member_type_rows(member_type_value_rows, float)
    result["claims_by_member_type_count"] = _member_type_rows(member_type_count_rows, int)

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

    if policy_number_parts:
        result["policy_number"] = " ".join(policy_number_parts).strip()

    monthly: List[dict] = []
    for line in lines:
        row_match = _FORMAT3_ROW_RE.match(line)
        rest = (row_match.group(3) or "") if row_match else ""
        match = _FORMAT3_MONTHLY_ROW_RE.match(rest)
        if match and match.group(1).lower()[:3] in _MONTH_NAMES:
            monthly.append(
                {
                    "year": int(match.group(2)),
                    "month": match.group(1)[:3].title(),
                    "paid": _parse_number(match.group(3)),
                    "partial": False,
                }
            )
            continue
        alt_match = _FORMAT3_MONTHLY_ROW_ALT_RE.match(rest)
        if alt_match and alt_match.group(1).lower()[:3] in _MONTH_NAMES:
            monthly.append(
                {
                    "year": int(alt_match.group(2)),
                    "month": alt_match.group(1)[:3].title(),
                    "paid": _parse_number(alt_match.group(3)),
                    "partial": False,
                }
            )
            continue
        no_day_match = _FORMAT3_MONTHLY_ROW_NO_DAY_RE.match(rest)
        if no_day_match and no_day_match.group(1).lower()[:3] in _MONTH_NAMES:
            monthly.append(
                {
                    "year": int(no_day_match.group(2)),
                    "month": no_day_match.group(1)[:3].title(),
                    "paid": _parse_number(no_day_match.group(3)),
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
        "claims_by_member_type_value": [],
        "claims_by_member_type_count": [],
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

    def _member_type_rows_format2(row_num: int, cast) -> List[dict]:
        # This layout's rows 7/8 (format 1's 8/9) split Inpatient/Outpatient/
        # Pharmacy/Dental/Optical/Totals - one column narrower than format
        # 1's, which also carries a "Not Yet Classified" column this layout
        # doesn't have; left at 0 rather than omitted so both formats'
        # dicts share the same shape for the UI and burning-cost endpoint.
        member_rows = []
        for letter in "abcdefghij":
            row = _find_row(rows, f"{row_num}{letter}")
            if not row or len(row) < 8:
                continue
            label = _cell(row[1])
            if label.strip().lower() == "totals":
                continue
            member_rows.append(
                {
                    "relation": label,
                    "in_patient": cast(_cell_number(row[2])),
                    "out_patient": cast(_cell_number(row[3])),
                    "pharmacy": cast(_cell_number(row[4])),
                    "dental": cast(_cell_number(row[5])),
                    "optical": cast(_cell_number(row[6])),
                    "not_yet_classified": cast(0),
                    "total": cast(_cell_number(row[7])),
                }
            )
        return member_rows

    result["claims_by_member_type_value"] = _member_type_rows_format2(7, float)
    result["claims_by_member_type_count"] = _member_type_rows_format2(8, int)

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


def _parse_format4_from_rows(rows: List[list]) -> Dict[str, Any]:
    """A fourth real-world "Health insurance claims record" layout (seen
    on a real MetLife-issued DHA Claims Report). Row numbering matches
    format 1's (dates at 3a/3b/4a/4b/4c, totals at 5a/5c) rather than
    format 2's, but like format 2 it renders with real, reliably-
    detected bordered tables - unlike format 1's plain-text rows or
    format 3's (a different file whose text extraction badly scrambles
    this same kind of table). Every member-type/diagnosis/provider/
    network table carries an extra 6th "Others" category alongside IP/
    OP/Pharmacy/Dental/Optical - mapped into the existing
    not_yet_classified slot so every format's dict shares the same
    shape, even though "Others" isn't quite the same thing.
    """
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
        "treatment_type_breakdown": [],
        "claims_by_member_type_value": [],
        "claims_by_member_type_count": [],
        "monthly_paid": [],
    }

    row_2 = _find_row(rows, "2")
    if row_2 and len(row_2) > 2:
        result["policy_number"] = _cell(row_2[2]) or None

    date_fields = {
        "3a": "policy_effective_date",
        "3b": "policy_expiry_date",
        "4a": "report_period_start",
        "4b": "report_period_end",
        "4c": "report_production_date",
    }
    for key, field in date_fields.items():
        row = _find_row(rows, key)
        if row and len(row) > 2:
            result[field] = _format2_parse_date(_cell(row[2]))

    row_5a = _find_row(rows, "5a")
    if row_5a and len(row_5a) > 2:
        result["total_paid"] = _cell_number(row_5a[2])
    row_5c = _find_row(rows, "5c")
    if row_5c and len(row_5c) > 2:
        result["incurred_not_reported"] = _cell_number(row_5c[2])

    def _population_total(keys: List[str]) -> Optional[int]:
        total = 0.0
        found = False
        for key in keys:
            row = _find_row(rows, key)
            if row and len(row) > 8 and _cell(row[8]):
                total += _cell_number(row[8])
                found = True
        return int(total) if found else None

    result["opening_members"] = _population_total(["6a", "6b", "6c"])
    result["closing_members"] = _population_total(["7a", "7b", "7c"])

    def _member_type_rows(row_num: int, cast):
        member_rows = []
        for letter in "abc":
            row = _find_row(rows, f"{row_num}{letter}")
            if not row or len(row) < 9:
                continue
            member_rows.append(
                {
                    "relation": _cell(row[1]),
                    "in_patient": cast(_cell_number(row[2])),
                    "out_patient": cast(_cell_number(row[3])),
                    "pharmacy": cast(_cell_number(row[4])),
                    "dental": cast(_cell_number(row[5])),
                    "optical": cast(_cell_number(row[6])),
                    "not_yet_classified": cast(_cell_number(row[7])),
                    "total": cast(_cell_number(row[8])),
                }
            )
        return member_rows

    result["claims_by_member_type_value"] = _member_type_rows(8, float)
    result["claims_by_member_type_count"] = _member_type_rows(9, int)

    row_8d = _find_row(rows, "8d")
    if row_8d and len(row_8d) >= 9:
        result["treatment_type_breakdown"] = [
            {"type": "In-Patient", "value": _cell_number(row_8d[2])},
            {"type": "Out-Patient", "value": _cell_number(row_8d[3])},
            {"type": "Pharmacy", "value": _cell_number(row_8d[4])},
            {"type": "Dental", "value": _cell_number(row_8d[5])},
            {"type": "Optical", "value": _cell_number(row_8d[6])},
            {"type": "Not Yet Classified", "value": _cell_number(row_8d[7])},
        ]

    counts_by_letter: Dict[str, dict] = {}
    for letter in "abcdefghij":
        row = _find_row(rows, f"11{letter}")
        if row and len(row) >= 9:
            counts_by_letter[letter] = {
                "count": int(_cell_number(row[8])),
                "ip_count": int(_cell_number(row[2])),
            }

    for letter in "abcdefghij":
        row = _find_row(rows, f"10{letter}")
        if not row or len(row) < 9:
            continue
        counts = counts_by_letter.get(letter, {})
        result["diagnosis_breakdown"].append(
            {
                "label": _cell(row[1]),
                "value": _cell_number(row[8]),
                "count": counts.get("count", 0),
                "ip_value": _cell_number(row[2]),
                "ip_count": counts.get("ip_count", 0),
            }
        )

    for letter in "abcdefghij":
        row = _find_row(rows, f"12{letter}")
        if row and len(row) >= 9:
            result["provider_breakdown"].append({"provider": _cell(row[1]), "value": _cell_number(row[8])})

    for key, label in (("14a", "In Network"), ("14b", "Out of Network")):
        row = _find_row(rows, key)
        if row and len(row) >= 9:
            result["claims_by_type"].append({"type": label, "value": _cell_number(row[8])})

    # Row 17's own row-letter labels (17a-17g) recycle once the report
    # spans a second calendar year (a 9+ month period almost always
    # does), so this can't be looked up by unique key like every other
    # section here - it walks every row between the "17" and "18"
    # section headers in table order instead.
    monthly: List[dict] = []
    start = _find_row_index(rows, "17")
    end = _find_row_index(rows, "18")
    if start is not None:
        for row in rows[start + 1 : end if end is not None else len(rows)]:
            if not row or len(row) < 6:
                continue
            month_cell = _cell(row[2])
            year_cell = _cell(row[4])
            value_cell = _cell(row[5])
            if not (month_cell.isdigit() and year_cell.isdigit() and value_cell):
                continue
            month_num = int(month_cell)
            if not 1 <= month_num <= 12:
                continue
            month_name = [k for k, v in _MONTH_NAMES.items() if v == month_num][0].capitalize()
            monthly.append(
                {
                    "year": int(year_cell),
                    "month": month_name,
                    "paid": _cell_number(value_cell),
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


_PPR_TITLE_RE = re.compile(r"^(\d+)\s+(.+)$", re.MULTILINE)
_PPR_PERIOD_RE = re.compile(r"Review Period:\s*([A-Za-z]+ \d{1,2} \d{4})\s+to\s+([A-Za-z]+ \d{1,2} \d{4})")
_PPR_KPI_RE = re.compile(r"Paid Claims\s+Average Lives\s+No\. of Claimants\s*\n([\d,]+)\s+(\d+)\s+(\d+)")
_PPR_MONTH_ROW_RE = re.compile(r"(20\d{2})\s+([A-Za-z]{3})\s+([\d,]+)")

# The UAE Dirham's fixed peg to the US Dollar, unchanged since 1997 (not
# a floating rate needing a live lookup) - this document's own notes
# page states its amounts are in US Dollars, unlike every other claims-
# report format here (and the rest of the app), which are AED. Without
# converting, this report's figures would combine with a case's AED
# premium and every other AED-based calculation as if they were AED
# too, silently understating actual claims by a factor of ~3.67x.
_AED_PER_USD = 3.6725


def _ppr_parse_date(text: str) -> Optional[date]:
    try:
        return datetime.strptime(text.strip(), "%B %d %Y").date()
    except ValueError:
        return None


def _parse_ppr_text(full_text: str) -> Dict[str, Any]:
    """An insurer-issued "Policy Performance Review" dashboard (a Power BI
    export) - a policy-summary-level analytics document, not a DHA-
    mandated claims record. Deliberately extracts only what its own text
    layer gives back cleanly: the review period, average lives, total
    paid, and the monthly paid-claims figures (all first_full_months/
    project_annual_claims needs) - not diagnosis/provider/benefit
    breakdowns, whose tables extract too unreliably here to trust (see
    parse_claims_report's dispatch comment). All monetary figures are
    converted from this document's own US Dollars to AED (see
    _AED_PER_USD) so they combine correctly with the rest of the app.
    """
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
        "treatment_type_breakdown": [],
        "claims_by_member_type_value": [],
        "claims_by_member_type_count": [],
        "monthly_paid": [],
    }

    title_match = _PPR_TITLE_RE.search(full_text)
    if title_match:
        result["policy_number"] = title_match.group(1)

    period_match = _PPR_PERIOD_RE.search(full_text)
    if period_match:
        result["report_period_start"] = _ppr_parse_date(period_match.group(1))
        result["report_period_end"] = _ppr_parse_date(period_match.group(2))

    kpi_match = _PPR_KPI_RE.search(full_text)
    if kpi_match:
        result["total_paid"] = round(_parse_number(kpi_match.group(1)) * _AED_PER_USD, 2)
        # No separate opening/closing count in this document - only a
        # single average-lives figure for the whole review period, used
        # for both ends so the per-member normalization degenerates to
        # dividing by that same average either way.
        average_lives = int(kpi_match.group(2))
        result["opening_members"] = average_lives
        result["closing_members"] = average_lives

    month_section_start = full_text.find("Paid Claims by Month")
    month_section_end = full_text.find("* Paid Claims", month_section_start) if month_section_start != -1 else -1
    if month_section_start != -1:
        month_text = full_text[month_section_start : month_section_end if month_section_end != -1 else None]
        monthly = []
        for match in _PPR_MONTH_ROW_RE.finditer(month_text):
            year, month_abbr, value = match.groups()
            if month_abbr.lower() not in _MONTH_NAMES:
                continue
            monthly.append(
                {
                    "year": int(year),
                    "month": month_abbr.title(),
                    "paid": round(_parse_number(value) * _AED_PER_USD, 2),
                    "partial": False,
                }
            )

        if monthly and result["report_period_start"]:
            first = monthly[0]
            month_num = _MONTH_NAMES.get(first["month"].lower())
            if (
                month_num == result["report_period_start"].month
                and first["year"] == result["report_period_start"].year
                and result["report_period_start"].day > 1
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
