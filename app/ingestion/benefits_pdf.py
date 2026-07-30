"""Parser for insurer table-of-benefits PDFs shaped like Bupa Global/Sukoon's
"Business Health Plan" guide: a benefit label in the page's left margin,
next to a bordered table with one column per plan tier (e.g. Select /
Premier / Elite / Ultimate).

Extraction strategy: pdfplumber's `find_tables()` reliably recovers the
bordered tier-value table (this PDF's benefit values are genuinely
tabular, unlike the surrounding prose, which pdfplumber's plain
`extract_text()` badly fragments across columns for any multi-line
benefit). Each row's LABEL sits outside the table's own bounding box, in
the left-margin region at that row's vertical position - cropping the
page to that region by row bbox is what recovers it, and this works
regardless of which specific insurer or tier names are used, as long as
the same label-column-next-to-bordered-tier-table layout holds.
"""
import re
from typing import Any, BinaryIO, Dict, List, Optional

import pdfplumber

# Maps our standard field name to a label substring (case-insensitive) to
# search for among the benefit rows extracted from the document. The first
# matching row wins.
_FIELD_LABEL_ANCHORS = {
    "area_of_cover": "geographical cover",
    "annual_limit": "overall annual maximum",
    "deductible": "available network in the",
    "pre_existing_chronic_limit": "congenital and hereditary conditions",
    "maternity_limit": "maternity and childbirth cover",
    "dental": "dental",
    "optical": "optical",
    "coinsurance": "available network in the",
    "alternative_or_complementary_treatment": "therapists, complementary medicine",
    "pharmacy_limit_and_coinsurance": "prescribed medicines",
}

_TIER_HEADER_ALIASES = {
    "select": "select",
    "premier": "premier",
    "elite": "elite",
    "ultimate": "ultimate",
    "essential": "essential",
    "standard": "standard",
    "comprehensive": "comprehensive",
    "premium": "premium",
}


def _clean_cell(value: Optional[str]) -> str:
    if not value:
        return ""
    return " ".join(value.replace("\n", " ").split())


def _extract_benefit_rows(pdf: "pdfplumber.PDF") -> Dict[str, Dict[str, str]]:
    """Returns {normalized_label: {tier_name: value_text}} across every page."""
    rows: Dict[str, Dict[str, str]] = {}

    for page in pdf.pages:
        for table in page.find_tables():
            data = table.extract()
            if not data or not data[0]:
                continue
            header = [_clean_cell(c).lower() for c in data[0]]
            if not header or not all(h in _TIER_HEADER_ALIASES for h in header if h):
                continue
            if len([h for h in header if h]) < 2:
                continue  # need at least 2 tiers to be a real tier table

            for row_index, table_row in enumerate(table.rows):
                if row_index == 0:
                    continue  # header row itself
                label_region = page.within_bbox((0, table_row.bbox[1], table.bbox[0], table_row.bbox[3]))
                label = _clean_cell(label_region.extract_text())
                if not label:
                    continue
                key = label.lower().rstrip(":").strip()

                values = data[row_index]
                tier_values = {
                    header[i]: _clean_cell(values[i])
                    for i in range(min(len(header), len(values)))
                    if header[i]
                }
                rows.setdefault(key, {}).update(tier_values)

    return rows


def parse_benefits_pdf(file: BinaryIO, filename: str) -> Dict[str, Dict[str, str]]:
    """Returns {tier_name: {standard_field: value_text}} for every tier found."""
    with pdfplumber.open(file) as pdf:
        rows = _extract_benefit_rows(pdf)
    return _build_tier_summaries(rows)


def _find_matching_label(rows: Dict[str, Dict[str, str]], anchor: str) -> Optional[str]:
    # Prefer an exact label match (e.g. the bare "Dental" limit row) over a
    # substring match, since several unrelated rows can share a keyword
    # (e.g. "...emergency dental treatment..." also contains "dental").
    if anchor in rows:
        return anchor
    return next((label for label in rows if anchor in label), None)


def _build_tier_summaries(rows: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    tiers = sorted({tier for values in rows.values() for tier in values.keys()})
    summaries: Dict[str, Dict[str, str]] = {tier: {} for tier in tiers}

    for field, anchor in _FIELD_LABEL_ANCHORS.items():
        matched_label = _find_matching_label(rows, anchor)
        if not matched_label:
            continue
        for tier in tiers:
            value = rows[matched_label].get(tier)
            if value:
                summaries[tier][field] = value

    return summaries


_USD_NUMBER_RE = re.compile(r"USD\s*([\d,]+)")
_NOT_COVERED_RE = re.compile(r"\bnot covered\b", re.IGNORECASE)


def _first_usd_amount(text: str) -> Optional[float]:
    match = _USD_NUMBER_RE.search(text or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def to_benefit_plan_fields(tier_name: str, summary: Dict[str, str]) -> Dict[str, Any]:
    """Best-effort mapping from a PDF-parsed standard summary onto the
    numeric BenefitPlan columns the scoring engine reads.

    This plan family prices via co-insurance/copay, not a flat deductible,
    so `deductible` and `co_insurance_pct` are left at their defaults
    (0) rather than guessed from free text - the full co-insurance
    description is preserved in `standard_summary` for humans to read, even
    though the engine's numeric scoring can't act on it precisely yet.
    """
    dental_text = summary.get("dental", "")
    optical_text = summary.get("optical", "")
    chronic_text = summary.get("pre_existing_chronic_limit", "")

    return {
        "plan_name": tier_name.title(),
        "annual_limit": _first_usd_amount(summary.get("annual_limit", "")),
        "maternity_covered": bool(summary.get("maternity_limit")) and not _NOT_COVERED_RE.search(summary.get("maternity_limit", "")),
        "maternity_limit": _first_usd_amount(summary.get("maternity_limit", "")),
        "dental_covered": bool(dental_text) and not _NOT_COVERED_RE.search(dental_text),
        "optical_covered": bool(optical_text) and not _NOT_COVERED_RE.search(optical_text),
        "pre_existing_covered": bool(chronic_text) and not _NOT_COVERED_RE.search(chronic_text),
        "chronic_covered": bool(chronic_text) and not _NOT_COVERED_RE.search(chronic_text),
        "source_format": "pdf",
        "standard_summary": summary,
    }
