"""Parser for a "labeled 3-column row" table-of-benefits PDF - one insurer
category (tier) per FILE rather than one tier per column, e.g. MaxHealth/
MaxMed's "MAXMED Neuron <TIER> GROUP" documents. Distinct from both the
Bupa-style layout (tier columns next to an outside-the-table label) and the
QIC/HealthCROSS CAT-style layout (several categories as columns in one
table) this codebase already handles: here every page has its own bordered
3-column table (Benefit label | Value | Clarification/description), grouped
under full-width section-banner rows ("INPATIENT BENEFIT", "DENTAL", ...)
that carry no data of their own, and there's no per-category column at all
because the whole document only ever describes ONE category.

Extraction strategy: the label/value/description column x-boundaries are
fixed for the whole document, but a benefit's value doesn't always line up
with a single pdfplumber "row" - `find_tables()`'s rows are really just the
VALUE column's own ruling, and the label/description text often visually
wraps across more than one such row with no ruling of its own (seen on a
real "Chiropractic, Ayurveda, Homeopathy, / Osteopathy & Acupuncture" label
split across two rows, with "AED 3,000" only appearing on the second). Both
`extract_text()` and `within_bbox()`-per-row lose or garble this wrapped
text at the row boundary, so each row is read by bucketing every WORD on
the page into a column/row cell using the word's own vertical midpoint
(`crop()` duplicates boundary-straddling words into both rows; strict
`within_bbox()` can drop them entirely if their bbox straddles the line).
A row with no value is never a complete benefit line on its own, so it's
folded into whichever later row supplies the value.
"""
import re
from typing import Any, BinaryIO, Dict, List, Optional, Tuple

import pdfplumber

_HEADER_TITLE = "table of benefits"
_HEADER_FIELD_ANCHORS = {
    "area_of_cover": "area of coverage",
    "network": "network",
    "annual_limit": "annual aggregate limit",
}

# Maps our standard field name to a list of candidate label substrings
# (case-insensitive, tried in order) to search for among this file's
# extracted benefit rows. The first matching row wins.
_FIELD_LABEL_ANCHORS: Dict[str, List[str]] = {
    "deductible": ["deductible"],
    "pre_existing_chronic_limit": ["pre-existing, critical illness and chronic", "pre-existing & chronic"],
    "maternity_limit": ["maternity in-patient limit", "maternity inpatient limit", "maternity in patient limit"],
    "dental": ["dental limit"],
    "optical": ["lenses and annual eye exam", "annual optical cover", "maximum optical cover"],
    "coinsurance": ["out patient services co-payment", "in patient services co-payment"],
    "alternative_or_complementary_treatment": ["chiropractic", "alternative medicine co-payment", "alternative treatment"],
    "pharmacy_limit_and_coinsurance": ["pharmaceuticals/drugs sublimit", "prescribed drugs & dressings - annual limit"],
    "health_screening_wellness": ["health check/wellness package", "health check wellness package", "wellness package"],
}

_CATEGORY_IN_FILENAME_RE = re.compile(r"category[\s_-]*([a-z])(?![a-z])", re.IGNORECASE)
_NOT_COVERED_RE = re.compile(r"\bnot covered\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"([\d,]+(?:\.\d+)?)")


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return " ".join(text.replace("\n", " ").split())


def _words_to_text(words: List[dict]) -> str:
    if not words:
        return ""
    words = sorted(words, key=lambda w: (round(w["top"], 1), w["x0"]))
    lines: List[List[dict]] = []
    for w in words:
        if lines and abs(w["top"] - lines[-1][-1]["top"]) < 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    return "\n".join(" ".join(x["text"] for x in sorted(line, key=lambda x: x["x0"])) for line in lines)


def _column_bounds(pdf: "pdfplumber.PDF") -> Optional[Tuple[float, float, float, float]]:
    """Finds the (label_left, label_right/value_left, value_right/desc_left,
    desc_right) x-boundaries from the first fully 3-cell-populated table row
    found anywhere in the document - these are constant for the whole file.
    """
    for page in pdf.pages:
        for table in page.find_tables():
            for row in table.rows:
                if len(row.cells) == 3 and all(c is not None for c in row.cells):
                    label_cell, value_cell, desc_cell = row.cells
                    return (label_cell[0], label_cell[2], desc_cell[0], desc_cell[2])
    return None


def _is_header_row(row, x_label_l: float, x_desc_r: float) -> bool:
    """A full-width single-cell row (e.g. "INPATIENT BENEFIT") rather than a
    genuine label/value/description data row.
    """
    cell0 = row.cells[0] if row.cells else None
    if cell0 is None or len(row.cells) < 2 or row.cells[1] is not None:
        return cell0 is not None and (cell0[2] - cell0[0]) > (x_desc_r - x_label_l) * 0.9
    return False


def _extract_rows(pdf: "pdfplumber.PDF") -> List[Dict[str, str]]:
    x_bounds = _column_bounds(pdf)
    if not x_bounds:
        return []
    x_label_l, x_value_l, x_desc_l, x_desc_r = x_bounds

    fragments: List[Tuple[str, str, str]] = []
    for page in pdf.pages:
        words = page.extract_words()
        page_bottom = page.height
        for table in page.find_tables():
            for row in table.rows:
                if _is_header_row(row, x_label_l, x_desc_r):
                    continue
                y0, y1 = max(row.bbox[1], 0), min(row.bbox[3], page_bottom)
                if y1 <= y0:
                    continue

                def _in_row(w, x0, x1):
                    mid = (w["top"] + w["bottom"]) / 2
                    return y0 <= mid < y1 and x0 <= w["x0"] < x1

                label = _clean(_words_to_text([w for w in words if _in_row(w, x_label_l, x_value_l)]))
                value = _clean(_words_to_text([w for w in words if _in_row(w, x_value_l, x_desc_l)]))
                desc = _clean(_words_to_text([w for w in words if _in_row(w, x_desc_l, x_desc_r)]))
                if not label and not value and not desc:
                    continue
                fragments.append((label, value, desc))

    # A row with no value is never a complete benefit line on its own - its
    # label/description are folded forward into the next row that has one.
    rows: List[Dict[str, str]] = []
    pending_label: List[str] = []
    pending_desc: List[str] = []
    for label, value, desc in fragments:
        if not value:
            if label:
                pending_label.append(label)
            if desc:
                pending_desc.append(desc)
            continue
        full_label = " ".join(pending_label + ([label] if label else []))
        full_desc = " ".join(pending_desc + ([desc] if desc else []))
        pending_label, pending_desc = [], []
        if full_label:
            rows.append({"label": full_label, "value": value, "description": full_desc})

    return rows


def _find_matching_row(rows: List[Dict[str, str]], anchors: List[str]) -> Optional[Dict[str, str]]:
    """Tries each anchor in order (most specific first); within one anchor,
    an exact label match wins over a substring match, but a later, more
    specific anchor is never passed over just because an earlier, more
    generic anchor also happens to match a different row exactly.
    """
    lowered = [(r, r["label"].lower()) for r in rows]
    for anchor in anchors:
        for row, label_lower in lowered:
            if label_lower == anchor:
                return row
        for row, label_lower in lowered:
            if anchor in label_lower:
                return row
    return None


def _header_fields(pdf: "pdfplumber.PDF") -> Optional[Dict[str, str]]:
    if not pdf.pages:
        return None
    page = pdf.pages[0]
    tables = page.find_tables()
    top_bound = tables[0].bbox[1] if tables else page.height
    header_text = page.within_bbox((0, 0, page.width, top_bound)).extract_text() or ""
    lines = [line.strip() for line in header_text.split("\n") if line.strip()]
    if not lines or _HEADER_TITLE not in lines[0].lower():
        return None

    fields: Dict[str, str] = {}
    if len(lines) > 1:
        fields["plan_name"] = lines[1]
    for field, anchor in _HEADER_FIELD_ANCHORS.items():
        for line in lines:
            if line.lower().startswith(anchor):
                fields[field] = line[len(anchor):].strip(" :")
                break
    return fields


def _category_from_filename(filename: str) -> Optional[str]:
    match = _CATEGORY_IN_FILENAME_RE.search(filename)
    return match.group(1).upper() if match else None


def _first_number(text: str) -> Optional[float]:
    match = _NUMBER_RE.search(text or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_labeled_row_benefits_pdf(file: BinaryIO, filename: str) -> Optional[Dict[str, Any]]:
    """Returns None if this file doesn't look like this document family (no
    "TABLE OF BENEFITS" header block recognized) so callers can fall through
    to the next parser rather than reporting a false, empty result.
    """
    with pdfplumber.open(file) as pdf:
        header = _header_fields(pdf)
        if header is None:
            return None
        rows = _extract_rows(pdf)

    standard_summary: Dict[str, str] = {}
    if header.get("area_of_cover"):
        standard_summary["area_of_cover"] = header["area_of_cover"]
    if header.get("annual_limit"):
        standard_summary["annual_limit"] = header["annual_limit"]
    for field, anchors in _FIELD_LABEL_ANCHORS.items():
        matched = _find_matching_row(rows, anchors)
        if matched:
            standard_summary[field] = matched["value"]

    dental_text = standard_summary.get("dental", "")
    optical_text = standard_summary.get("optical", "")
    maternity_text = standard_summary.get("maternity_limit", "")
    chronic_text = standard_summary.get("pre_existing_chronic_limit", "")

    return {
        "plan_name": header.get("plan_name") or "Base Plan",
        "category": _category_from_filename(filename),
        "network": header.get("network"),
        "annual_limit": _first_number(header.get("annual_limit", "")),
        "maternity_limit": _first_number(maternity_text),
        "maternity_covered": bool(maternity_text) and not _NOT_COVERED_RE.search(maternity_text),
        "dental_covered": bool(dental_text) and not _NOT_COVERED_RE.search(dental_text),
        "optical_covered": bool(optical_text) and not _NOT_COVERED_RE.search(optical_text),
        "pre_existing_covered": bool(chronic_text) and not _NOT_COVERED_RE.search(chronic_text),
        "chronic_covered": bool(chronic_text) and not _NOT_COVERED_RE.search(chronic_text),
        "standard_summary": standard_summary,
    }
