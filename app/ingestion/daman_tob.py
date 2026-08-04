"""Parser for Daman's own "Schedule of Benefits" table-of-benefits PDF
layout (e.g. "Uselect Bronze/Silver/Gold without Dental") - one plan/tier
per FILE (like app/ingestion/labeled_row_benefits_pdf.py's Maxmed-style
documents), not one tier per column like Bupa's layout. Distinct from
every other document family this codebase handles: most benefit rows here
carry TWO values side by side - a Network % and a Non-network % - rather
than one flat value or one value per tier column.

Extraction strategy: pdfplumber's own extract_text() reorders/fragments
this document around page breaks and footnote markers (seen directly on
the real Bronze/Silver/Gold files - a qualifying note for one row can end
up interleaved before the NEXT row's own label). Like
labeled_row_benefits_pdf.py, this instead reads every WORD on the page by
its own x/y position and buckets it into a column: a label column on the
left, then either one flat value column (the handful of meta fields at
the top of page 1 - Plan Name, Annual Benefit Limit, Territorial Limit,
Network, Pre-existing conditions) or two value columns - Network and
Non-network - for every benefit line item once the first "<Section> ...
Network Non-network" header row is reached.
"""
import re
from typing import Any, BinaryIO, Dict, List, Optional

import pdfplumber

_TITLE_RE = re.compile(r"schedule of benefits\s*\(([^)]+)\)", re.IGNORECASE)
_STATUS_VALUE_RE = re.compile(r"^(.*?)\s+((?:not\s+)?(?:fully\s+)?covered\.?)$", re.IGNORECASE)

# Boilerplate repeated on every page (page title, insurer letterhead/footer)
# - never real benefit content, so dropped outright rather than folded into
# a neighboring row's label.
_BOILERPLATE_RE = re.compile(r"schedule of benefits|national health insurance company|^doc ctrl no", re.IGNORECASE)
# Marks the end of real per-row benefit data: the "Other Services covered"
# bullet list (service names with no Network/Non-network % of their own)
# and the trailing numbered footnote glossary ("*As Defined By Daman."
# followed by "1 Please note: ...", etc. - real prose definitions for
# footnote markers glued onto earlier labels/values, not rows of their
# own) both follow it. Some real files (seen on one of three real Gold/
# Silver/Bronze documents) have a corrupted font encoding in this trailing
# region that extracts as scrambled single-letter garbage rather than
# real text, so this is treated as a hard stop rather than trying to keep
# parsing rows out of it. A plain "starts with a digit" check isn't safe
# for finding the glossary directly - real benefit text can start a
# wrapped line with a number too (e.g. "...Federal Law No. 8 of 1980...").
_STOP_SECTION_RE = re.compile(r"^\*as defined by|^other services covered", re.IGNORECASE)

# Column x-boundaries, fixed across every real Daman "Schedule of
# Benefits" file seen so far (same template, same page layout).
_LABEL_COL_RIGHT = 165.0
_NETWORK_COL_LEFT = 380.0
_NON_NETWORK_COL_LEFT = 470.0


def _clean(text: str) -> str:
    return " ".join((text or "").replace("\n", " ").split())


def _lines_from_words(words: List[dict]) -> List[List[dict]]:
    words = sorted(words, key=lambda w: (round(w["top"], 1), w["x0"]))
    lines: List[List[dict]] = []
    for w in words:
        if lines and abs(w["top"] - lines[-1][-1]["top"]) < 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    return lines


def _cell_text(line: List[dict], x0: float, x1: float) -> str:
    cell_words = [w for w in sorted(line, key=lambda w: w["x0"]) if x0 <= w["x0"] < x1]
    return _clean(" ".join(w["text"] for w in cell_words))


def looks_like_daman_tob(first_page_text: str) -> bool:
    return bool(_TITLE_RE.search(first_page_text or ""))


def _plan_name_from_title(first_page_text: str) -> Optional[str]:
    match = _TITLE_RE.search(first_page_text or "")
    return match.group(1).strip() if match else None


def extract_all_rows(file: BinaryIO, filename: str) -> Optional[Dict[str, Any]]:
    """Returns {"plan_name", "rows": [{"section", "label", "value"}]} for
    every row found (the top meta fields plus every Network/Non-network
    benefit line item, each combined into one descriptive value string),
    or None if this file doesn't look like a Daman "Schedule of Benefits"
    document at all, so callers can fall through to the next parser.
    """
    with pdfplumber.open(file) as pdf:
        if not pdf.pages:
            return None
        first_page_text = pdf.pages[0].extract_text() or ""
        if not looks_like_daman_tob(first_page_text):
            return None

        plan_name = _plan_name_from_title(first_page_text) or filename

        rows: List[Dict[str, str]] = []
        current_section = ""
        in_two_column_section = False
        pending_label_parts: List[str] = []

        reached_glossary = False
        for page in pdf.pages:
            if reached_glossary:
                break
            for line in _lines_from_words(page.extract_words()):
                full_line_text = _cell_text(line, -10_000, 10_000)
                if _STOP_SECTION_RE.match(full_line_text):
                    # The trailing numbered footnote glossary - real prose
                    # definitions for markers glued onto earlier labels/
                    # values, not benefit rows of their own. Nothing after
                    # this point (on this page or any later one) is real
                    # benefit content.
                    reached_glossary = True
                    break
                if _BOILERPLATE_RE.search(full_line_text):
                    # The repeated page title or insurer letterhead/footer -
                    # dropped outright, never folded into a neighboring
                    # row's label.
                    continue

                label_text = _cell_text(line, 0, _LABEL_COL_RIGHT)
                network_marker = _cell_text(line, _NETWORK_COL_LEFT, _NON_NETWORK_COL_LEFT)
                non_network_marker = _cell_text(line, _NON_NETWORK_COL_LEFT, 10_000)

                # A "<Section> ... Network Non-network" header marks the
                # start (or restart, for a later section) of the
                # two-column benefit rows below it - never a row of its own.
                if network_marker.lower().startswith("network") and non_network_marker.lower().startswith("non-network"):
                    in_two_column_section = True
                    current_section = label_text or current_section
                    pending_label_parts = []
                    continue

                if not in_two_column_section:
                    meta_value = _cell_text(line, _LABEL_COL_RIGHT, _NETWORK_COL_LEFT)
                    if label_text and meta_value:
                        rows.append({"section": "", "label": label_text, "value": meta_value})
                    elif meta_value and rows:
                        # A wrapped continuation line (value text only, no
                        # new label) - belongs to the previous meta field.
                        rows[-1]["value"] = _clean(rows[-1]["value"] + " " + meta_value)
                    continue

                # A benefit row's own label/note text can wrap wide enough
                # to spill past the meta-field boundary (165) that only
                # matters before any section starts (e.g. "(including
                # consultation up to AED 3,000 Per Person per Policy
                # Year)") - once in a two-column section, everything up to
                # the Network column is label territory, not just the
                # narrower meta-value column.
                wide_label_text = _cell_text(line, 0, _NETWORK_COL_LEFT)

                # A label-only line stating just a status ("Dental Not
                # Covered") rather than a Network/Non-network % pair - a
                # complete row on its own, not folded into the next row's
                # label (it isn't a qualifier/footnote for it).
                if not network_marker and not non_network_marker:
                    status_match = _STATUS_VALUE_RE.match(wide_label_text)
                    if status_match and status_match.group(1):
                        rows.append({"section": current_section, "label": status_match.group(1).strip(), "value": status_match.group(2)})
                        pending_label_parts = []
                        continue
                    # A parenthetical line with NOTHING already pending is a
                    # trailing qualifier/sub-limit on the row that was just
                    # completed (its position relative to the value line
                    # varies row to row - sometimes before, sometimes after,
                    # e.g. "Alternative Medicine3,10" / [value] / "(including
                    # consultation up to AED 2,000...)") - attached to that
                    # row directly rather than discarded or risked getting
                    # glued onto an unrelated row that hasn't started yet.
                    if wide_label_text.startswith("(") and not pending_label_parts and rows:
                        rows[-1]["label"] = _clean(rows[-1]["label"] + " " + wide_label_text)
                        continue
                    if wide_label_text:
                        pending_label_parts.append(wide_label_text)
                    continue

                full_label = " ".join(pending_label_parts + ([wide_label_text] if wide_label_text else []))
                pending_label_parts = []
                if not full_label:
                    continue
                value = (
                    f"{network_marker} (Network) / {non_network_marker} (Non-network)"
                    if non_network_marker
                    else network_marker
                )
                rows.append({"section": current_section, "label": full_label, "value": value})

    return {"plan_name": plan_name, "rows": rows}
