"""Generic "Benefit label | Value [| Clarification]" bordered-table parser
for insurers' table-of-benefits PDFs - both international (Cigna's "Global
Care Flexible"/"SmartCare", Allianz/Orient's "Dubai Summit" series, MSH
International's quote-style TOB) and local-market (Sukoon's, in
particular) - feeds the detailed benefits comparison, which needs every
benefit row verbatim rather than the standard 11-field summary the
existing-vs-quoted Summary comparison uses.

These insurers all lay out a benefit label somewhere in the table's own
row (unlike Bupa's, which sits outside the table, and unlike Maxmed's,
which needs word-bucketing to survive wrapped rows) - a real bordered
table with clean row/column lines is enough here, though which column
index the label and value actually land in varies (see
`extract_benefit_rows`' docstring). A single wide cell spanning the whole
row (no value alongside it) is a section banner ("HEALTHCARE BENEFITS",
"MATERNITY", ...) rather than a real benefit row, and is kept as context
for the rows that follow rather than a row itself - as is a row whose
"value" is just the plan/tier name restated (e.g. "Category 1"), which
some local insurers repeat as a mini-header at the top of every section.
"""
import re
from typing import BinaryIO, Dict, List, Optional

import pdfplumber
import pytesseract

_HEADER_ROW_WORDS = {"benefit", "benefits", "benefit limit", "clarifications", "clarification", "limits", "plan"}

# A quote-style PDF (MSH's, in particular) carries a per-member premium
# schedule and an executive-summary/quotation-terms block ahead of the
# actual table of benefits - both are genuine label/value tables by the
# same extraction logic, but they're pricing/admin content, not benefits,
# and would otherwise show up as noise rows (employee names, policy
# currency) in a benefits comparison. This is a generic pattern across
# these insurers' quote documents, not specific to any one of them.
_NON_BENEFIT_SECTION_KEYWORDS = ("premium", "quotation")


def _is_non_benefit_section(section: str) -> bool:
    lowered = section.lower()
    return any(keyword in lowered for keyword in _NON_BENEFIT_SECTION_KEYWORDS)

# Some real insurer PDFs (seen on a Cigna "Global Care Flexible" export) embed
# a font whose character codes don't map to the glyphs actually shown on the
# page - pdfplumber's `extract_text()`/`find_tables()` read the raw codes and
# produce consistent-looking but meaningless text (the same corruption shows
# up whether you read one word or the whole page), while a rendered image of
# the same page is perfectly legible. Ordinary English words essentially
# never appearing is the tell - a real English TOB page this long always
# contains several of these.
_COMMON_WORD_RE = re.compile(r"\b(the|and|covered|benefit|treatment|paid|per|for)\b", re.IGNORECASE)


def _looks_garbled(sample_text: str) -> bool:
    if not sample_text or len(sample_text) < 200:
        return False
    return len(_COMMON_WORD_RE.findall(sample_text)) < 3


def _clamp_bbox(page: "pdfplumber.page.Page", bbox):
    x0, top, x1, bottom = bbox
    x0, top = max(x0, 0), max(top, 0)
    x1, bottom = min(x1, page.width), min(bottom, page.height)
    if x1 <= x0 or bottom <= top:
        return None
    return (x0, top, x1, bottom)


def _has_any_visual_content(page: "pdfplumber.page.Page", bbox) -> bool:
    """Whether a region has anything at all worth rendering an image and
    running OCR over. Curves are included alongside characters since
    that's precisely the signature of the documents this OCR fallback
    exists for (a font whose glyphs are drawn as vector outlines rather
    than real characters - see module docstring) - a region with truly
    nothing in it (no chars, no curves, no rects, no lines) has nothing
    for OCR to find either, and skipping it avoids the cost of rendering
    and OCR-ing every blank cell in documents that simply don't have a
    clarification/note column at all (most of a real row-per-line TOB).
    """
    clamped = _clamp_bbox(page, bbox)
    if clamped is None:
        return False
    cropped = page.within_bbox(clamped)
    return bool(cropped.chars or cropped.curves or cropped.rects or cropped.lines)


def _ocr_bbox(page: "pdfplumber.page.Page", bbox) -> str:
    clamped = _clamp_bbox(page, bbox)
    if clamped is None:
        return ""
    cropped = page.within_bbox(clamped)
    image = cropped.to_image(resolution=200).original
    return _clean(pytesseract.image_to_string(image))


def _text_bbox(page: "pdfplumber.page.Page", bbox) -> str:
    clamped = _clamp_bbox(page, bbox)
    if clamped is None:
        return ""
    return _clean(page.within_bbox(clamped).extract_text())


def _text_then_ocr(page: "pdfplumber.page.Page", bbox) -> str:
    text = _text_bbox(page, bbox)
    if text:
        return text
    if not _has_any_visual_content(page, bbox):
        return ""
    return _ocr_bbox(page, bbox)


def _label_and_note_from_geometry(page: "pdfplumber.page.Page", table_bbox, row_top: float, row_bottom: float, value_x0: float, value_x1: float):
    """Recovers the label/note text either side of a row's one ruled value
    cell. Native text extraction is tried first since it's fast and most
    documents' label/note columns extract fine even when they aren't
    ruled as their own cells - OCR only kicks in for the (rarer) documents
    where that specific text truly isn't extractable (see module docstring).
    """
    # `within_bbox` only keeps characters fully inside the box, so a
    # region flush against the table's own edge clips the first/last
    # glyph of the label and note text - pad both sides by a few points.
    margin = 3
    label_bbox = (table_bbox[0] - margin, row_top, value_x0, row_bottom)
    label = _text_then_ocr(page, label_bbox)
    note_bbox = (value_x1, row_top, table_bbox[2] + margin, row_bottom)
    note = _text_then_ocr(page, note_bbox)
    return label, note


def _rows_via_geometry_ocr(pdf: "pdfplumber.PDF") -> List[Dict[str, str]]:
    """Fallback for a PDF whose embedded text is unreadable (see above).
    pdfplumber's table/row/cell BOUNDING BOXES come from the page's actual
    ruling lines, not the corrupted character codes, so they're still
    accurate - this reads each cell's text by OCR-ing just that cell's
    cropped image instead of trusting `extract_text()`.

    On a real Cigna "Global Care Flexible" export, only the VALUE column is
    actually ruled per row throughout the document - the label and
    clarification columns have no internal row ruling of their own (only
    the shared header row does), so `row.cells` reports them as `None` for
    almost every row. Rather than trusting a per-row label cell that mostly
    doesn't exist, the label/note text is read from the horizontal band
    either side of the one column x-boundary that IS reliable (the value
    cell's own x0/x1), at that row's own y-range. When a run of consecutive
    benefits shares one merged, un-ruled value cell (no horizontal line
    between them), they surface as one combined row rather than being
    split further - coarser than the other insurers' per-benefit rows, but
    still a correct label/value pairing, and workable until a document with
    finer internal ruling turns up.
    """
    rows: List[Dict[str, str]] = []

    for page in pdf.pages:
        for table in page.find_tables():
            for row in table.rows:
                cells = row.cells
                value_cell = next((c for c in cells if c is not None), None)
                if value_cell is None:
                    continue
                value_x0, row_top, value_x1, row_bottom = value_cell[0], row.bbox[1], value_cell[2], row.bbox[3]

                value_text = _ocr_bbox(page, (value_x0, row_top, value_x1, row_bottom))
                if not value_text:
                    continue
                label_text = _ocr_bbox(page, (table.bbox[0], row_top, value_x0, row_bottom))
                if not label_text:
                    continue
                if label_text.lower() in _HEADER_ROW_WORDS or value_text.lower() in _HEADER_ROW_WORDS:
                    continue
                if label_text.lower().startswith("note"):
                    continue  # a clarifying aside, not a distinct benefit line

                note_text = _ocr_bbox(page, (value_x1, row_top, table.bbox[2], row_bottom))
                rows.append({"section": "", "label": label_text, "value": value_text, "note": note_text})

    return _merge_wrapped_label_continuations(rows)


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return " ".join(text.replace("\n", " ").split())


_TIER_VALUE_PREFIXES = ("category", "plan ", "tier ")


def _looks_like_tier_name(value: str) -> bool:
    """A cell whose text is just the plan/tier identifier itself (e.g.
    "Category 1", "Plan 2") rather than an actual benefit value - some
    insurers (Sukoon, among others) repeat this as a mini-header at the
    top of every section within one big table, not only in a single
    document-wide header row.
    """
    return value.strip().lower().startswith(_TIER_VALUE_PREFIXES)


def _contained_in(inner_bbox, outer_bbox, tolerance: float = 1.0) -> bool:
    return (
        inner_bbox[0] >= outer_bbox[0] - tolerance
        and inner_bbox[1] >= outer_bbox[1] - tolerance
        and inner_bbox[2] <= outer_bbox[2] + tolerance
        and inner_bbox[3] <= outer_bbox[3] + tolerance
    )


def _distinct_tables(page: "pdfplumber.page.Page") -> List:
    """Some documents' automatic table detection finds not just the real
    table but also smaller redundant sub-regions nested inside it (the
    same cell's wrapped text re-detected as its own tiny table) - keeping
    both would process the same benefit rows twice, or worse, only the
    fragment. Largest-bbox-first, dropping anything already covered by a
    table already kept, leaves just the real, distinct tables on the page.
    """
    tables = sorted(page.find_tables(), key=lambda t: -(t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1]))
    kept: List = []
    for table in tables:
        if not any(_contained_in(table.bbox, k.bbox) for k in kept):
            kept.append(table)
    return kept


def extract_benefit_rows(file: BinaryIO, filename: str) -> List[Dict[str, str]]:
    """Returns [{"section", "label", "value", "note"}] for every real
    benefit row found, in document order. `section` carries forward the
    most recent full-width banner row seen (empty string before the first
    one). Column-header rows (e.g. "Benefits | Benefit Limit |
    Clarifications") are recognized by their fixed wording and skipped
    rather than surfaced as a bogus benefit row.

    Label and value aren't assumed to sit in fixed column indices - some
    documents (Sukoon's local-market TOB, in particular) alternate between
    a "top-level" pair of columns and an indented "detail" pair depending
    on how nested a row is, with blank spacer columns in between. The
    leftmost populated cell is always the label and the rightmost is
    always the value, whichever actual column indices those land on.
    """
    rows: List[Dict[str, str]] = []
    current_section = ""
    header_plan_value: Optional[str] = None

    with pdfplumber.open(file) as pdf:
        sample_text = " ".join((page.extract_text() or "") for page in pdf.pages[:3])
        if _looks_garbled(sample_text):
            return _rows_via_geometry_ocr(pdf)

        for page in pdf.pages:
            for table in _distinct_tables(page):
                data = table.extract()
                if not data:
                    continue
                table_rows = table.rows
                last_value: Optional[str] = None
                for row_index in range(len(data)):
                    raw_cells = [_clean(c) for c in data[row_index]]
                    non_empty_idx = [i for i, c in enumerate(raw_cells) if c]
                    if not non_empty_idx:
                        continue

                    note = ""
                    if len(non_empty_idx) == 1 and non_empty_idx[0] == 0:
                        # A lone cell in the label column with nothing
                        # alongside it is a section banner, not a benefit
                        # row on its own.
                        current_section = raw_cells[0]
                        last_value = None
                        continue
                    if len(non_empty_idx) == 1:
                        if row_index >= len(table_rows):
                            continue
                        idx = non_empty_idx[0]
                        cells = table_rows[row_index].cells
                        ruled_cell_count = sum(1 for c in cells if c is not None)
                        if ruled_cell_count <= 1:
                            # The label/note columns aren't ruled as their
                            # own cells in this row (some documents only
                            # rule the value column) - recover them from
                            # the geometry either side of the one cell
                            # that IS ruled, rather than discarding a real
                            # benefit row as if it had no label.
                            value_cell = cells[idx] if idx < len(cells) else None
                            if value_cell is None:
                                continue
                            row_top, row_bottom = table_rows[row_index].bbox[1], table_rows[row_index].bbox[3]
                            label, note = _label_and_note_from_geometry(
                                page, table.bbox, row_top, row_bottom, value_cell[0], value_cell[2]
                            )
                            value = raw_cells[idx]
                            if "(cid:" in value:
                                # A handful of glyphs in this font have no
                                # Unicode mapping - pdfplumber falls back
                                # to printing the raw font code instead.
                                # OCR the same cell to recover the text.
                                value = _ocr_bbox(page, (value_cell[0], row_top, value_cell[2], row_bottom))
                            if not label:
                                continue
                        else:
                            # A genuinely multi-column ruled table whose
                            # value cell is just blank on this row - most
                            # commonly a limit that covers several listed
                            # procedures at once (e.g. one dental benefit
                            # limit applying to consultation, x-ray,
                            # extraction, ... each listed as its own row).
                            # Inherit the value from the last row in this
                            # table that actually had one.
                            if last_value is None:
                                continue
                            label = raw_cells[idx]
                            value = last_value
                    else:
                        label, value = raw_cells[non_empty_idx[0]], raw_cells[non_empty_idx[-1]]
                        if not label or not value:
                            continue
                        middle_idx = non_empty_idx[1:-1]
                        note = " ".join(raw_cells[i] for i in middle_idx if raw_cells[i])

                    if label.lower() in _HEADER_ROW_WORDS or value.lower() in _HEADER_ROW_WORDS:
                        if label.lower() in _HEADER_ROW_WORDS and value and header_plan_value is None:
                            # Capture the plan-name column header itself so
                            # documents that repeat a mini "section | plan
                            # name" header on every page can be told apart
                            # from a real benefit row further down.
                            header_plan_value = value
                        continue
                    if header_plan_value is not None and value == header_plan_value:
                        continue
                    if _looks_like_tier_name(value):
                        # A section (or sub-section) banner repeating the
                        # plan/tier name as its own "value" rather than a
                        # real benefit row - e.g. Sukoon's TOB restates
                        # "Category 1" at the top of every section.
                        current_section = label
                        last_value = None
                        continue
                    if _is_non_benefit_section(current_section):
                        continue
                    rows.append({"section": current_section, "label": label, "value": value, "note": note})
                    last_value = value

    return _merge_wrapped_label_continuations(rows)


def _merge_wrapped_label_continuations(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """A label that wraps across two lines (e.g. "Wellness Health Check-
    Up") can surface as two adjacent rows sharing the exact same label,
    each holding one fragment of what's really one continuous value/note
    (e.g. "Preventative examinations and tests are" then, on the next
    detected row, "covered up to 2,500/- pppy. Covered tests..." - the
    actual limit only ever appears in the second fragment). Two
    back-to-back rows for the same section+label are joined into one
    rather than kept as a duplicate with a truncated value.
    """
    merged: List[Dict[str, str]] = []
    for row in rows:
        if merged and merged[-1]["section"] == row["section"] and merged[-1]["label"] == row["label"]:
            merged[-1]["value"] = " ".join(p for p in (merged[-1]["value"], row["value"]) if p)
            merged[-1]["note"] = " ".join(p for p in (merged[-1]["note"], row["note"]) if p)
        else:
            merged.append(dict(row))
    return merged
