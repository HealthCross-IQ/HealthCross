"""Tests for app/ingestion/daman_tob.py's column-bucketing and pattern-
matching helpers. The full extract_all_rows() pipeline (real PDF word
positions via pdfplumber) was verified directly against the user's own
real Daman "Uselect Bronze/Silver/Gold without Dental" files rather than
a synthetic fixture - a hand-built PDF can't reliably reproduce the exact
word x/y positions this parser buckets by. These tests cover the smaller,
independently-testable pieces: line-grouping, cell text extraction by
column, and the title/status/boilerplate/section-stop regexes.
"""
from app.ingestion.daman_tob import (
    _BOILERPLATE_RE,
    _STATUS_VALUE_RE,
    _STOP_SECTION_RE,
    _cell_text,
    _lines_from_words,
    _plan_name_from_title,
    looks_like_daman_tob,
)


def _word(text, x0, x1, top):
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 10}


def test_looks_like_daman_tob_matches_the_title_line():
    assert looks_like_daman_tob("Schedule of Benefits (Uselect Bronze without Dental)\nPlan Name ...")
    assert not looks_like_daman_tob("Some other insurer's table of benefits")
    assert not looks_like_daman_tob("")


def test_plan_name_extracted_from_title():
    assert _plan_name_from_title("Schedule of Benefits (Uselect Gold without Dental)") == "Uselect Gold without Dental"
    assert _plan_name_from_title("no title here") is None


def test_lines_from_words_groups_by_vertical_position():
    words = [
        _word("Hello", 10, 40, 100.0),
        _word("World", 45, 80, 100.2),  # same line as "Hello" (top within tolerance)
        _word("Next", 10, 40, 130.0),   # a different line entirely
    ]
    lines = _lines_from_words(words)
    assert len(lines) == 2
    assert {w["text"] for w in lines[0]} == {"Hello", "World"}
    assert {w["text"] for w in lines[1]} == {"Next"}


def test_cell_text_extracts_only_words_within_the_given_x_range_in_order():
    # Deliberately out of x0 order, as pdfplumber can hand back words in an
    # order that doesn't match reading order when top values differ slightly
    # (e.g. a superscript footnote marker) - _cell_text must still emit
    # them left-to-right.
    line = [_word("2", 161.9, 165.7, 249.5), _word("Inpatient", 36.8, 78.0, 249.7), _word("Treatment", 90.9, 130.0, 249.7)]
    assert _cell_text(line, 0, 165.0) == "Inpatient Treatment 2"


def test_cell_text_respects_column_boundaries():
    line = [_word("Label", 10, 50, 100.0), _word("100%", 400, 430, 100.0), _word("50%", 480, 510, 100.0)]
    assert _cell_text(line, 0, 165) == "Label"
    assert _cell_text(line, 380, 470) == "100%"
    assert _cell_text(line, 470, 10_000) == "50%"
    assert _cell_text(line, 0, 165) != "100%"


def test_status_value_regex_splits_label_from_a_bare_covered_status():
    match = _STATUS_VALUE_RE.match("Dental Not Covered")
    assert match.group(1) == "Dental"
    assert match.group(2) == "Not Covered"

    match = _STATUS_VALUE_RE.match("Something Fully Covered")
    assert match.group(1) == "Something"
    assert match.group(2) == "Fully Covered"

    assert _STATUS_VALUE_RE.match("Just a plain sentence.") is None


def test_boilerplate_regex_matches_repeated_page_furniture():
    assert _BOILERPLATE_RE.search("Schedule of Benefits (Uselect Bronze without Dental)")
    assert _BOILERPLATE_RE.search("National Health Insurance Company - Daman (PJSC)")
    assert _BOILERPLATE_RE.match("Doc Ctrl No.: STEMP/60")
    assert not _BOILERPLATE_RE.search("Inpatient & Day Treatment")


def test_stop_section_regex_matches_glossary_and_other_services_markers():
    assert _STOP_SECTION_RE.match("*As Defined By Daman.")
    assert _STOP_SECTION_RE.match("Other Services covered (Through Service Providers Only)")
    # Real benefit text can start a wrapped line with a number too - must
    # not be mistaken for the glossary just because it starts with a digit.
    assert not _STOP_SECTION_RE.match("8 of 1980 concerning the Regulation of Work Relations")
