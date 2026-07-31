"""Generic "Benefit label | Value [| Clarification]" bordered-table parser
for international insurers' table-of-benefits PDFs (Cigna's "Global Care
Flexible", Allianz/Orient's "Dubai Summit" series, MSH International's
quote-style TOB) - feeds the detailed international benefits comparison,
which needs every benefit row verbatim rather than the standard 11-field
summary the existing-vs-quoted Summary comparison uses.

These insurer families all lay out a benefit label as the table's own
first column (unlike Bupa's, which sits outside the table, and unlike
Maxmed's, which needs word-bucketing to survive wrapped rows) - a real
bordered table with clean row/column lines is enough here. A single wide
cell spanning the whole row (no value alongside it) is a section banner
("HEALTHCARE BENEFITS", "MATERNITY", ...) rather than a real benefit row,
and is kept as context for the rows that follow rather than a row itself.
"""
import re
from typing import BinaryIO, Dict, List, Optional

import pdfplumber
import pytesseract

_HEADER_ROW_WORDS = {"benefit", "benefits", "benefit limit", "clarifications", "clarification", "limits"}

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


def _ocr_bbox(page: "pdfplumber.page.Page", bbox) -> str:
    x0, top, x1, bottom = bbox
    x0, top = max(x0, 0), max(top, 0)
    x1, bottom = min(x1, page.width), min(bottom, page.height)
    if x1 <= x0 or bottom <= top:
        return ""
    cropped = page.within_bbox((x0, top, x1, bottom))
    image = cropped.to_image(resolution=200).original
    return _clean(pytesseract.image_to_string(image))


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

    return rows


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return " ".join(text.replace("\n", " ").split())


def extract_benefit_rows(file: BinaryIO, filename: str) -> List[Dict[str, str]]:
    """Returns [{"section", "label", "value", "note"}] for every real
    benefit row found, in document order. `section` carries forward the
    most recent full-width banner row seen (empty string before the first
    one). Column-header rows (e.g. "Benefits | Benefit Limit |
    Clarifications") are recognized by their fixed wording and skipped
    rather than surfaced as a bogus benefit row.
    """
    rows: List[Dict[str, str]] = []
    current_section = ""
    candidate_rows_seen = 0

    with pdfplumber.open(file) as pdf:
        sample_text = " ".join((page.extract_text() or "") for page in pdf.pages[:3])
        if _looks_garbled(sample_text):
            return _rows_via_geometry_ocr(pdf)

        for page in pdf.pages:
            for table in page.find_tables():
                data = table.extract()
                if not data:
                    continue
                for row_index in range(len(data)):
                    raw_cells = [_clean(c) for c in data[row_index]]
                    non_empty = [c for c in raw_cells if c]
                    if not non_empty:
                        continue
                    candidate_rows_seen += 1
                    if len(non_empty) == 1:
                        # A lone cell with no value alongside it is a
                        # section banner, not a benefit row on its own -
                        # UNLESS the populated cell sits in a later column
                        # (the label column came back blank), which means
                        # this is really a value-only row, not a banner.
                        if raw_cells[0]:
                            current_section = non_empty[0]
                            continue
                    label, value = raw_cells[0], raw_cells[1] if len(raw_cells) > 1 else ""
                    if not label or not value:
                        continue
                    if label.lower() in _HEADER_ROW_WORDS or value.lower() in _HEADER_ROW_WORDS:
                        continue
                    if _is_non_benefit_section(current_section):
                        continue
                    note = " ".join(c for c in raw_cells[2:] if c)
                    rows.append({"section": current_section, "label": label, "value": value, "note": note})

        # Some documents have a benefit-label column rendered in a font
        # pdfplumber can't turn into text at all (blank, not garbled) while
        # the value/clarification columns extract fine - native parsing
        # then finds real benefit rows but with no usable label, and (worse)
        # miscounts many of them as section banners. If almost nothing came
        # out despite there being real candidate rows, the label column is
        # unreadable and geometry OCR is the only way to recover it.
        if not rows and candidate_rows_seen > 5:
            return _rows_via_geometry_ocr(pdf)

    return rows
