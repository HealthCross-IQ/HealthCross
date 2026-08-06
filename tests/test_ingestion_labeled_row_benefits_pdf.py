"""Tests against real "MAXMED Neuron" table-of-benefits PDFs (one category
per file, no premium table) - see app/ingestion/labeled_row_benefits_pdf.py
for why this needed its own parser distinct from the Bupa-style and
QIC/HealthCROSS CAT-style layouts already handled elsewhere.
"""
from pathlib import Path

import pytest

from app.ingestion.labeled_row_benefits_pdf import (
    _category_from_filename,
    _find_matching_row,
    parse_labeled_row_benefits_pdf,
)

FIXTURES = Path(__file__).parent / "fixtures"
GOLD_CATEGORY_A = FIXTURES / "Table_of_Benefits_Maxmed_Neuron_Gold_Group_Category_A.pdf"
BRONZE_CATEGORY_B = FIXTURES / "Table_of_Benefits_Maxmed_Neuron_Bronze_Group_Category_B.pdf"
CIGNA_SMARTCARE = FIXTURES / "Table_of_Benefits_Cigna_SmartCare_Annexure1.pdf"


def test_category_from_filename_handles_trailing_characters():
    assert _category_from_filename("Table_of_Benefits_Maxmed_Neuron_Gold_Group_Category_A_1.pdf") == "A"
    assert _category_from_filename("Table_of_Benefits_Maxmed_Neuron_Bronze_Group_Category_B_1.pdf") == "B"
    assert _category_from_filename("no_category_here.pdf") is None


def test_find_matching_row_prefers_more_specific_earlier_anchor_over_a_generic_later_one():
    rows = [
        {"label": "Chiropractic, Ayurveda, Homeopathy, Osteopathy & Acupuncture", "value": "AED 3,000", "description": ""},
        {"label": "Alternative Medicine Co-payment", "value": "20%", "description": ""},
    ]
    matched = _find_matching_row(rows, ["chiropractic", "alternative medicine co-payment", "alternative treatment"])
    assert matched["value"] == "AED 3,000"


@pytest.mark.parametrize("path,expected_category,expected_network,expected_plan_name", [
    (GOLD_CATEGORY_A, "A", "Neuron General Plus", "MAXMED Neuron GOLD GROUP"),
    (BRONZE_CATEGORY_B, "B", "Neuron General", "MAXMED Neuron BRONZE GROUP"),
])
def test_parses_real_maxmed_neuron_document(path, expected_category, expected_network, expected_plan_name):
    with open(path, "rb") as f:
        result = parse_labeled_row_benefits_pdf(f, path.name)

    assert result is not None
    assert result["plan_name"] == expected_plan_name
    assert result["category"] == expected_category
    assert result["network"] == expected_network
    assert result["annual_limit"] == 1_000_000.0
    assert result["maternity_limit"] == 20_000.0
    assert result["maternity_covered"] is True
    assert result["dental_covered"] is True
    assert result["optical_covered"] is True
    assert result["pre_existing_covered"] is True

    summary = result["standard_summary"]
    assert summary["area_of_cover"] == "Worldwide excl. USA"
    assert summary["annual_limit"] == "AED 1,000,000"
    assert summary["deductible"] == "20% max. of AED 50"
    assert summary["pre_existing_chronic_limit"] == "AED 150,000"
    assert summary["maternity_limit"] == "AED 20,000"
    assert summary["dental"] == "AED 3,500"
    assert summary["optical"] == "AED 1,000"
    assert summary["alternative_or_complementary_treatment"] == "AED 3,000"
    assert summary["health_screening_wellness"] == "AED 1,000"


def test_returns_none_for_a_document_without_the_table_of_benefits_header(monkeypatch):
    """An unrelated PDF (no "TABLE OF BENEFITS" header block) must not be
    misidentified as this layout - callers rely on None to fall through to
    the next parser in the chain (see /cases/{id}/benefits' fallback order).
    """
    from app.ingestion import labeled_row_benefits_pdf

    class _FakePage:
        width = 595
        height = 842

        def find_tables(self):
            return []

        def within_bbox(self, bbox):
            return self

        def extract_text(self):
            return "Some Other Document\nUnrelated content"

    class _FakePdf:
        pages = [_FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(labeled_row_benefits_pdf.pdfplumber, "open", lambda file: _FakePdf())

    result = parse_labeled_row_benefits_pdf(b"", "unrelated.pdf")
    assert result is None


def test_returns_none_for_a_cigna_smartcare_document_despite_a_coincidental_header_and_anchor_match():
    """Regression test: this real Cigna "Schedule 3 - Table of Benefits and
    Exclusions" document has an unrelated narrative/clarifications layout,
    but its title line coincidentally contains "table of benefits" (passing
    _header_fields' check) and one of its 85+ real benefit rows
    coincidentally matches the "alternative treatment" anchor - previously
    enough to wrongly claim this document, producing a near-empty
    standard_summary (just that one field) instead of falling through to
    the generic label/value/description table parser
    (app/ingestion/international_tob.py), which handles this document
    family correctly. _MIN_MATCHED_ANCHOR_FIELDS now requires several
    matched fields, not just one, before accepting a match.
    """
    with open(CIGNA_SMARTCARE, "rb") as f:
        result = parse_labeled_row_benefits_pdf(f, CIGNA_SMARTCARE.name)
    assert result is None


def test_does_not_crash_when_the_first_table_starts_at_the_very_top_of_the_page(monkeypatch):
    """Regression test: a real document's first table can start at a
    y-coordinate that comes back as a tiny negative number (e.g.
    -3.05e-05) from PDF coordinate rounding rather than a clean 0 -
    pdfplumber's within_bbox rejects both a negative-height AND an
    exact-zero-height crop outright, so this document's header region
    (genuinely nonexistent when a table starts at the very top) must be
    skipped rather than crashing the whole upload with a raw ValueError.
    """
    from app.ingestion import labeled_row_benefits_pdf

    class _FakeTable:
        bbox = (0, 0, 595.2, -3.0517500022142485e-05)

    class _FakePage:
        width = 595.2
        height = 842

        def find_tables(self):
            return [_FakeTable()]

        def within_bbox(self, bbox):
            if bbox[3] - bbox[1] <= 0:
                raise ValueError(f"{bbox} has a negative width or height.")
            return self

        def extract_text(self):
            return "Some Other Document\nUnrelated content"

    class _FakePdf:
        pages = [_FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(labeled_row_benefits_pdf.pdfplumber, "open", lambda file: _FakePdf())

    result = parse_labeled_row_benefits_pdf(b"", "unrelated.pdf")
    assert result is None
