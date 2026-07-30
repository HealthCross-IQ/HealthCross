"""Parser for insurer quotation documents shaped like the QIC / HealthCROSS
Global "Full Category Premium Calculation" quote: a category premium
summary table (Category / Total Members / Plan / Network Selected / Total
Gross Premium) plus a series of ordinary bordered tables where the benefit
label is the table's OWN first column and the remaining columns are one per
quoted category (e.g. "Gold - CAT A", "Gold - CAT B").

Unlike app/ingestion/benefits_pdf.py's Bupa/Sukoon-style layout, this
insurer's benefit label lives INSIDE the same table as its own first
column, so a plain table extract is enough - no left-margin cropping
needed. Per-category benefit tables are told apart from other tables on the
page (the per-member age-band premium breakdown, the totals row, the
metadata table) by requiring the non-first header cells to contain a
"CAT <letter>" token matching one of the categories found in the premium
summary table.
"""
import re
from typing import Any, BinaryIO, Dict, List, Optional

import pdfplumber

from app.scoring.rules.benefits_summary import build_standard_benefit_summary

# standard field -> label substring (case-insensitive, already lower-cased)
# to search for among this document's benefit rows. Exact match wins over
# substring, same convention as app/ingestion/benefits_pdf.py.
_FIELD_LABEL_ANCHORS = {
    "area_of_cover": "area of cover",
    "annual_limit": "annual policy limit",
    "pre_existing_chronic_limit": "pre-existing & chronic",
    "maternity_limit": "maternity inpatient- limit",
    "dental": "annual dental cover",
    "optical": "annual optical cover",
    "coinsurance": "gp/specialist consultations",
    "alternative_or_complementary_treatment": "complementary and alternative treatments",
    "pharmacy_limit_and_coinsurance": "prescribed drugs & dressings - annual limit",
}

_CAT_TOKEN_RE = re.compile(r"cat\s*([a-z0-9]+)", re.IGNORECASE)
_MONEY_RE = re.compile(r"[\d]{1,3}(?:,\d{3})*(?:\.\d+)?")
_USD_RE = re.compile(r"USD\s*([\d,]+)", re.IGNORECASE)
_NOT_COVERED_RE = re.compile(r"\bnot covered\b", re.IGNORECASE)


def _clean_cell(value: Optional[str]) -> str:
    if not value:
        return ""
    return " ".join(value.replace("\n", " ").split())


def _parse_money(text: str) -> Optional[float]:
    match = _MONEY_RE.search(text or "")
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _first_usd_amount(text: str) -> Optional[float]:
    match = _USD_RE.search(text or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _extract_category_premium_table(pdf: "pdfplumber.PDF") -> List[Dict[str, Any]]:
    for page in pdf.pages:
        for table in page.find_tables():
            data = table.extract()
            if not data or not data[0] or not data[0][0]:
                continue
            if _clean_cell(data[0][0]).lower() != "full category premium calculation":
                continue
            if len(data) < 2:
                continue
            header = [_clean_cell(c).lower() for c in data[1]]

            rows = []
            for raw_row in data[2:]:
                if not raw_row or not raw_row[0]:
                    continue
                row = {header[i]: _clean_cell(raw_row[i]) for i in range(min(len(header), len(raw_row)))}
                members_text = row.get("total members", "")
                rows.append(
                    {
                        "category": row.get("category"),
                        "member_count": int(members_text) if members_text.isdigit() else None,
                        "plan_name": row.get("plan"),
                        "network": row.get("network selected"),
                        "gross_premium": _parse_money(row.get("total gross premium", "")),
                    }
                )
            return rows
    return []


def _extract_benefit_rows(pdf: "pdfplumber.PDF", category_letters: List[str]) -> Dict[str, Dict[str, str]]:
    """Returns {normalized_label: {tier_header_text: value}}."""
    rows: Dict[str, Dict[str, str]] = {}

    for page in pdf.pages:
        for table in page.find_tables():
            data = table.extract()
            if not data or len(data) < 2:
                continue
            header = [_clean_cell(c) for c in data[0]]
            if len(header) < 2:
                continue
            tier_headers = header[1:]
            if not any(tier_headers):
                continue

            matched_tier_headers = set()
            for tier_header in tier_headers:
                match = _CAT_TOKEN_RE.search(tier_header or "")
                if match and match.group(1).upper() in category_letters:
                    matched_tier_headers.add(tier_header)
            if not matched_tier_headers:
                continue  # not a per-category benefit table

            for raw_row in data[1:]:
                if not raw_row or not raw_row[0]:
                    continue
                label = _clean_cell(raw_row[0]).lower().rstrip(":").strip()
                if not label:
                    continue
                values = {
                    header[i + 1]: _clean_cell(raw_row[i + 1])
                    for i in range(len(tier_headers))
                    if header[i + 1] in matched_tier_headers and i + 1 < len(raw_row)
                }
                if values:
                    rows.setdefault(label, {}).update(values)

    return rows


def _find_matching_label(rows: Dict[str, Dict[str, str]], anchor: str) -> Optional[str]:
    if anchor in rows:
        return anchor
    return next((label for label in rows if anchor in label), None)


def parse_quote_pdf(file: BinaryIO, filename: str) -> List[Dict[str, Any]]:
    """Returns one dict per quoted category: category, plan_name, network,
    member_count, gross_premium, annual_limit (parsed numeric), and the
    fixed 10-field standard_summary.
    """
    with pdfplumber.open(file) as pdf:
        categories = _extract_category_premium_table(pdf)
        if not categories:
            raise ValueError("Could not find a 'Full Category Premium Calculation' table in this PDF")

        category_letters = [c["category"].upper() for c in categories if c.get("category")]
        rows = _extract_benefit_rows(pdf, category_letters)

    return _build_category_results(categories, rows)


def _build_category_results(categories: List[Dict[str, Any]], rows: Dict[str, Dict[str, str]]) -> List[Dict[str, Any]]:
    tier_header_by_letter: Dict[str, str] = {}
    for label_values in rows.values():
        for tier_header in label_values:
            match = _CAT_TOKEN_RE.search(tier_header)
            if match:
                tier_header_by_letter.setdefault(match.group(1).upper(), tier_header)

    results = []
    for cat in categories:
        letter = (cat.get("category") or "").upper()
        tier_header = tier_header_by_letter.get(letter)

        plan_details: Dict[str, Any] = {}
        if tier_header:
            for field, anchor in _FIELD_LABEL_ANCHORS.items():
                matched_label = _find_matching_label(rows, anchor)
                if not matched_label:
                    continue
                value = rows[matched_label].get(tier_header)
                if value:
                    plan_details[field] = value

        results.append(
            {
                "category": letter,
                "plan_name": cat.get("plan_name"),
                "network": cat.get("network"),
                "member_count": cat.get("member_count"),
                "gross_premium": cat.get("gross_premium"),
                "annual_limit": _first_usd_amount(plan_details.get("annual_limit", "")),
                "maternity_limit": _first_usd_amount(plan_details.get("maternity_limit", "")),
                "dental_covered": bool(plan_details.get("dental")) and not _NOT_COVERED_RE.search(plan_details.get("dental", "")),
                "optical_covered": bool(plan_details.get("optical")) and not _NOT_COVERED_RE.search(plan_details.get("optical", "")),
                "pre_existing_covered": bool(plan_details.get("pre_existing_chronic_limit"))
                and not _NOT_COVERED_RE.search(plan_details.get("pre_existing_chronic_limit", "")),
                "chronic_covered": bool(plan_details.get("pre_existing_chronic_limit"))
                and not _NOT_COVERED_RE.search(plan_details.get("pre_existing_chronic_limit", "")),
                "standard_summary": build_standard_benefit_summary(plan_details),
            }
        )
    return results
