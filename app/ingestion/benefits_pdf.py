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

from app.ingestion.benefits_ocr import build_ocr_benefit_summary

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
    "health_screening_wellness": "wellness",
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


# Some rows' explanation text states a DIFFERENT percentage schedule per
# tier group within the same shared cell (Bupa's dental co-insurance, in
# particular: "For Business Select and Business Premier: ... For Business
# Elite and Business Ultimate: ...") rather than one value for every tier.
_TIER_GROUP_HEADER_RE = re.compile(r"for\s+business\s+([a-z]+(?:\s+and\s+business\s+[a-z]+)*)\s*:", re.IGNORECASE)

# A percentage-of-treatment-type line ("100 percent of preventive
# treatment...") - split on the start of each one rather than matched whole,
# since these run on with no sentence-ending punctuation between them
# (only the last one ends in a period) and a lazy "any character" match
# has no other reliable stopping point. The negative lookbehind keeps a
# multi-digit number ("100") from being treated as if it also matched
# starting from its own trailing digits ("00 percent of...").
_PERCENT_LINE_START_RE = re.compile(r"(?<!\d)\d+\s*percent of", re.IGNORECASE)
_PERCENT_LINE_TRAILING_BOILERPLATE = (
    "Follow up on", "Note:", "Dental and optical", "Please note", "Pre-authorisation",
)


def _split_note_by_tier(note: str, tiers: List[str]) -> Dict[str, str]:
    markers = list(_TIER_GROUP_HEADER_RE.finditer(note or ""))
    if not markers:
        return {tier: note for tier in tiers}

    result: Dict[str, str] = {}
    for i, marker in enumerate(markers):
        start = marker.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(note)
        segment = note[start:end].strip()
        named_tiers = {t.strip() for t in marker.group(1).lower().replace("business", "").split("and") if t.strip()}
        for tier in tiers:
            if tier.lower() in named_tiers:
                result[tier] = segment
    for tier in tiers:
        result.setdefault(tier, note)  # not named in any group - the note applies to it as a whole
    return result


def _percent_lines(note: str) -> List[str]:
    if not note:
        return []
    parts = re.split(f"(?={_PERCENT_LINE_START_RE.pattern})", note, flags=re.IGNORECASE)
    lines = [p.strip().rstrip(".").strip() for p in parts if _PERCENT_LINE_START_RE.match(p.strip())]
    cleaned = []
    for line in lines:
        for boilerplate in _PERCENT_LINE_TRAILING_BOILERPLATE:
            idx = line.find(boilerplate)
            if idx != -1:
                line = line[:idx].strip()
        if line:
            cleaned.append(line)
    return cleaned


def _dental_coinsurance_from_note(note: str) -> Optional[str]:
    lines = _percent_lines(note)
    return "; ".join(lines) if lines else None


_OPTICAL_PERCENT_RE = re.compile(r"(\d+)\s*percent of eligible costs", re.IGNORECASE)


def _optical_coinsurance_from_note(note: str) -> Optional[str]:
    """The note states the percentage of costs COVERED (e.g. "75 percent of
    eligible costs..."), not the member's co-insurance share - this is the
    complement of that figure (100% - 75% = 25% co-insurance)."""
    match = _OPTICAL_PERCENT_RE.search(note or "")
    if not match:
        return None
    return f"{100 - float(match.group(1)):.0f}%"


# Some documents (Bupa's, in particular) state extra covered conditions as
# a plain prose bullet list elsewhere in the document - "In addition to the
# benefits detailed in the 'Table of Benefits' above, the following
# benefits are also covered under this health plan: Chronic conditions -
# any treatment for ... is covered. Pre-existing conditions - any treatment
# for ... is covered." - rather than as a row in the tier-value table at
# all, so the ordinary table-row extraction never sees them. These apply
# uniformly to every tier (no tier breakdown is given in this bullet list).
_ALSO_COVERED_BULLET_RE = re.compile(r"([A-Z][a-zA-Z /\-]{3,60}?)\s*[–-]\s*any treatment[^.]*?is covered")


def _also_covered_bullets(pdf: "pdfplumber.PDF") -> List[str]:
    full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return [m.group(1).strip() for m in _ALSO_COVERED_BULLET_RE.finditer(full_text)]


def _extract_benefit_rows(pdf: "pdfplumber.PDF") -> Dict[str, Dict[str, str]]:
    """Returns {normalized_label: {tier_name: value_text}} across every page."""
    rows, _ = _extract_benefit_rows_with_notes(pdf)
    return rows


def _extract_benefit_rows_with_notes(
    pdf: "pdfplumber.PDF",
) -> "tuple[Dict[str, Dict[str, str]], Dict[str, str]]":
    """Same as `_extract_benefit_rows`, but also returns {normalized_label:
    note_text} - the free-text "Explanation of benefits" column that sits
    to the RIGHT of the tier-value table (this document's own explanation
    column doesn't parse as part of the same bordered table pdfplumber
    detects for the tier values themselves, same as how the label to the
    LEFT of it needs its own separate region crop). Most rows' explanation
    is generic filler, but some (e.g. dental/optical's co-insurance
    percentage breakdown) carry real structured detail no other column
    states at all.
    """
    rows: Dict[str, Dict[str, str]] = {}
    notes: Dict[str, str] = {}

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

                if key not in notes:
                    note_region = page.within_bbox((table.bbox[2], table_row.bbox[1], page.width, table_row.bbox[3]))
                    note = _clean_cell(note_region.extract_text())
                    if note:
                        notes[key] = note

    return rows, notes


def parse_benefits_pdf(file: BinaryIO, filename: str) -> Dict[str, Dict[str, str]]:
    """Returns {tier_name: {standard_field: value_text}} for every tier found."""
    with pdfplumber.open(file) as pdf:
        rows = _extract_benefit_rows(pdf)
    return _build_tier_summaries(rows)


def extract_all_rows_by_tier(file: BinaryIO, filename: str) -> Dict[str, List[Dict[str, str]]]:
    """Returns {tier_name: [{"label", "value"}]} for EVERY benefit row found,
    not just the standard 11-field summary - used by the detailed
    international benefits comparison, which wants every row verbatim.
    """
    with pdfplumber.open(file) as pdf:
        rows, notes = _extract_benefit_rows_with_notes(pdf)
        also_covered = _also_covered_bullets(pdf)

    tiers = sorted({tier for values in rows.values() for tier in values.keys()})
    by_tier: Dict[str, List[Dict[str, str]]] = {tier: [] for tier in tiers}
    for label, tier_values in rows.items():
        for tier, value in tier_values.items():
            if value:
                by_tier[tier].append({"label": label.title(), "value": value})

    # The dental/optical LIMIT rows only ever get the currency amount - the
    # co-insurance percentage schedule lives entirely in each row's own
    # explanation-column note, never as a value in the tier-value table
    # itself, so it needs its own synthetic row per tier rather than
    # relying on the generic label/value loop above to have found it.
    for label, category_label in (("dental", "Dental Co-insurance"), ("optical", "Optical Co-insurance")):
        note = notes.get(label)
        if not note:
            continue
        per_tier_note = _split_note_by_tier(note, tiers)
        for tier in tiers:
            coinsurance = (
                _dental_coinsurance_from_note(per_tier_note[tier])
                if label == "dental"
                else _optical_coinsurance_from_note(per_tier_note[tier])
            )
            if coinsurance:
                by_tier[tier].append({"label": category_label, "value": coinsurance})

    # Bullet-list "also covered" benefits (e.g. chronic/pre-existing
    # conditions) stated once in prose, uniformly for every tier.
    for label in also_covered:
        for tier in tiers:
            by_tier[tier].append({"label": label.title(), "value": "Covered"})

    # The "Mental health conditions:" section states its own limit as a
    # generic "In-patient / day-case treatment" / "Out-patient treatment"
    # pair (the same bare wording other sections could in principle use),
    # rather than a label that itself says "psychiatric" or "mental
    # health" - nothing in the row-by-row extraction above ties it back to
    # that section at all (this document's structure never preserves
    # which section a row belongs to). "out-patienttreatment" only occurs
    # once in the whole document, uniquely identifying this row, and the
    # broker wants the out-patient figure specifically, not the in-patient
    # one - so it becomes its own synthetic "Psychiatric Treatment" row.
    psychiatric_row = rows.get("out-patienttreatment")
    if psychiatric_row:
        for tier, value in psychiatric_row.items():
            if value:
                by_tier[tier].append({"label": "Psychiatric Treatment", "value": value})

    return by_tier


def parse_benefits_pdf_text_fallback(file: BinaryIO, filename: str) -> Dict[str, Any]:
    """Fallback for a table-of-benefits PDF that has real extractable text
    (so it isn't a scan needing OCR) but whose tables aren't recoverable by
    `parse_benefits_pdf()` above - e.g. a layout that uses whitespace
    alignment rather than actual bordered table lines, which pdfplumber's
    line-based `find_tables()` can't reconstruct into a clean grid (seen on
    a real Sukoon "renewal" TOB with 4 tier columns, where every page
    fragmented into dozens of single-cell pseudo-tables instead of one
    clean tier table).

    Rather than a second bespoke column-splitting parser - fragile here,
    since values range from a plain number to multi-line prose like "In-
    patient: -Limit 8,000/- pppy..." with no reliable column delimiter in
    the extracted text - this reuses the same label-anchored nearby-value
    scan built for OCR (app/ingestion/benefits_ocr.py's
    build_ocr_benefit_summary), just fed this PDF's real extracted text
    directly instead of an OCR'd image. That function already reports
    every distinct value found near a label rather than guessing one, which
    is exactly the right behavior here too: a label with 4 real per-category
    values (not a low-confidence OCR artifact) will still show all 4 with
    the "verify" note, since the text stream doesn't reveal which value
    belongs to which category once the table structure is lost.
    """
    with pdfplumber.open(file) as pdf:
        pages_text = [page.extract_text() or "" for page in pdf.pages]

    return {
        "summary": build_ocr_benefit_summary(pages_text),
        "raw_text": "\n\n".join(f"--- page {i + 1} ---\n{text}" for i, text in enumerate(pages_text)),
    }


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
