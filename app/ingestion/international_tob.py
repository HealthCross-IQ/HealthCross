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



# A lone footnote reference number ("2", "12") sharing the label region with
# an icon-styled label that has no real extractable text of its own (seen on
# a real Cigna "Smart Care" export - the actual label, e.g. "Out-Patient
# Co-insurance", is drawn as a graphic, while just the superscript footnote
# marker next to it is real, separate text) would otherwise win over OCR
# outright since it isn't empty - a real label is never just 1-2 bare
# digits, so this is safe to treat the same as no native text at all.
_BARE_FOOTNOTE_RE = re.compile(r"^\d{1,2}$")


def _text_then_ocr(page: "pdfplumber.page.Page", bbox) -> str:
    text = _text_bbox(page, bbox)
    if text and not _BARE_FOOTNOTE_RE.match(text):
        return text
    if not _has_any_visual_content(page, bbox):
        return text
    return _ocr_bbox(page, bbox) or text


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
    # The label region's own left edge starts from the page's true left
    # margin (0) rather than the detected table's own bbox - some
    # documents' automatic table detection finds only the narrow value
    # column itself as "the table" (one cell per row, no label/note
    # columns even possible within it), so table_bbox[0] would already BE
    # the value column's left edge, cropping the label region down to
    # nothing. Starting from 0 instead is always at least as wide as
    # starting from table_bbox[0] - margin, so it can only find more, not
    # cross into unrelated content, since nothing on these documents sits
    # further left than the row's own real label text.
    margin = 3
    label_bbox = (0, row_top, value_x0, row_bottom)
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
                    if len(raw_cells) > 1 and len(non_empty_idx) == 1 and non_empty_idx[0] == 0:
                        # A lone cell in the label column of a genuinely
                        # multi-column table, with nothing ruled alongside
                        # it, is USUALLY a section banner - but a long
                        # wrapped disclaimer/note paragraph can also end up
                        # alone in column 0 the same way (e.g. a
                        # "Medications not covered include..." aside
                        # within a Dental section). A real banner is short
                        # and title-like; treating a multi-sentence
                        # paragraph as one would wipe out the section/
                        # value context genuine rows further down still
                        # need (and silently drop the very next row if it
                        # depends on an inherited value) - so only
                        # genuinely short text updates the section.
                        #
                        # (This check only applies when the table itself
                        # has more than one column at all - some documents
                        # detect a table that's really just the single
                        # narrow value-column strip on its own, one cell
                        # per row with no label/note columns possible even
                        # in principle; that's a value needing a
                        # reconstructed label below, never a banner.)
                        if len(raw_cells[0].split()) <= 8:
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
                            label = raw_cells[idx]
                            if "co-insurance" in label.lower() or "coinsurance" in label.lower():
                                # A co-insurance sub-header whose own value
                                # is a %, not the section's shared limit -
                                # the real percentage is on the very next
                                # row (its own label is often just the
                                # generic word "Co-insurance"), so inheriting
                                # the limit here would be flatly wrong
                                # (and would wrongly win the category match
                                # before that next, correct row is reached).
                                continue
                            if last_value is None:
                                continue
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
                        elif value.lower() in _HEADER_ROW_WORDS and label.lower() not in _HEADER_ROW_WORDS:
                            # Some documents embed the actual section name
                            # in the header row itself (e.g. "Dental
                            # benefits | Benefit Limit | Clarifications"
                            # repeated at the top of every page's table,
                            # rather than a generic "Benefits" column
                            # title) - without this, the row gets silently
                            # discarded as a header and the section name
                            # never gets tracked at all, so every dental
                            # row that follows loses its section context.
                            current_section = label
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


def extract_multi_tier_rows(file: BinaryIO, filename: str) -> Dict[str, List[Dict[str, str]]]:
    """For documents that lay out several plan tiers side by side in the
    SAME table (e.g. HealthCROSS Global's quote: "Label | Gold - CAT A |
    Gold - CAT B"), rather than one document per tier - returns
    {tier_name: [{"section", "label", "value"}]} for each tier column
    found, so each becomes its own reference plan instead of only the
    last column's values surviving (or worse, the repeated per-section
    mini-header - "Gold - CAT A" / "Gold - CAT B" themselves - being read
    as if they were real benefit values, since a plain leftmost/rightmost
    row reader has no way to know there's more than one value column).

    The tier names are read from the first table's header row and then
    required to repeat identically on every subsequent mini-header within
    the document (each section restates them, "Dental Benefits: | Gold -
    CAT A | Gold - CAT B") - this both confirms the layout guess and
    means a document that ISN'T this multi-tier-per-row shape (a single
    changing header, or none at all) naturally returns nothing rather
    than misreading arbitrary rows as tier columns.
    """
    def _all_rows(pdf: "pdfplumber.PDF") -> List[List[str]]:
        rows: List[List[str]] = []
        for page in pdf.pages:
            for table in _distinct_tables(page):
                data = table.extract()
                if data:
                    rows.extend([_clean(c) for c in raw_row] for raw_row in data)
        return rows

    def _header_candidate(raw_cells: List[str]) -> Optional[List[str]]:
        if not raw_cells or not raw_cells[0]:
            return None
        # A real value row can just as easily have two short matching
        # cells (e.g. "Covered | Covered") as a genuine header can - the
        # label ending in a colon ("Dental Benefits:", "Inpatient &
        # Daycase treatment Benefits:") is what actually marks this as a
        # section title restating the tier names, not a benefit row.
        if not raw_cells[0].rstrip().endswith(":"):
            return None
        other_cells = [c for c in raw_cells[1:] if c]
        looks_like_header = (
            len(other_cells) >= 2
            and len(other_cells) == len(raw_cells) - 1
            and all(len(c.split()) <= 5 for c in other_cells)
        )
        return other_cells if looks_like_header else None

    with pdfplumber.open(file) as pdf:
        all_rows = _all_rows(pdf)

        # A one-off summary table elsewhere in the document (e.g. a
        # premium-calculation table) can ALSO look like a plausible
        # header on a first pass - the real per-section mini-header this
        # layout needs repeats identically many times over (once per
        # benefit section), while an unrelated table's header appears
        # only once or twice, so the most frequent candidate wins.
        candidate_counts: Dict[tuple, int] = {}
        for raw_cells in all_rows:
            candidate = _header_candidate(raw_cells)
            if candidate:
                candidate_counts[tuple(candidate)] = candidate_counts.get(tuple(candidate), 0) + 1
        if not candidate_counts:
            return {}
        tier_names = list(max(candidate_counts, key=candidate_counts.get))
        if candidate_counts[tuple(tier_names)] < 2:
            return {}  # never repeats - not this layout at all

        tier_rows: Dict[str, List[Dict[str, str]]] = {tier: [] for tier in tier_names}
        current_section = ""
        for raw_cells in all_rows:
            if not raw_cells or not raw_cells[0]:
                continue
            other_cells = [c for c in raw_cells[1:] if c]
            # Once the tier names are confirmed, any row restating them
            # exactly enters/labels a new section - not just the
            # colon-suffixed ones pass 1 needed to tell a real header
            # apart from an incidental "Covered | Covered" value row (a
            # row that repeats the tier names verbatim can't be a benefit
            # value in the first place, whatever its own label looks
            # like, since a real value is never literally the tier name).
            if other_cells == tier_names:
                current_section = raw_cells[0]
                continue
            # Nothing establishes a section until the document's own
            # first such header is reached - skips unrelated tables
            # earlier in the document (e.g. a premium-calculation summary)
            # that happen to have the right shape (label + N short cells)
            # but aren't part of this benefit-table layout at all.
            if not current_section:
                continue
            values = raw_cells[1 : 1 + len(tier_names)]
            if len(values) < len(tier_names) or not any(values):
                continue
            label = raw_cells[0]
            for tier, value in zip(tier_names, values):
                if value:
                    tier_rows[tier].append({"section": current_section, "label": label, "value": value, "note": ""})

    return {tier: _merge_wrapped_label_continuations(rows) for tier, rows in tier_rows.items() if rows}
