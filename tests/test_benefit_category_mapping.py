"""Tests for the fixed 36-category benefit mapping
(app/reference/benefit_category_mapping.py) - agreed with the underwriting
team to replace the earlier verbatim-label comparison, which showed a
plan as missing a benefit just because its own wording didn't match
another plan's exact label.
"""
from app.reference.benefit_category_mapping import (
    CATEGORIES,
    DISPLAY_ORDER,
    MATCH_ORDER,
    build_standard_summary_from_rows,
    map_label_to_category,
)


def test_display_and_match_order_cover_every_category_exactly_once():
    assert set(DISPLAY_ORDER) == set(MATCH_ORDER) == set(CATEGORIES.keys())
    assert len(DISPLAY_ORDER) == len(set(DISPLAY_ORDER)) == 36
    assert len(MATCH_ORDER) == len(set(MATCH_ORDER)) == 36


def test_maps_differently_worded_annual_maximum_labels_onto_one_category():
    # Bupa, Cigna, and Sukoon each word the same benefit differently - all
    # three must resolve to the same canonical category.
    assert map_label_to_category("", "Overall Annual Maximum") == "Annual/Indemnity Maximum"
    assert map_label_to_category("", "Plan Annual Maximum") == "Annual/Indemnity Maximum"
    assert map_label_to_category("", "Indemnity Limit") == "Annual/Indemnity Maximum"


def test_area_of_cover_wins_over_emergency_treatment_keyword_collision():
    # A real label like Sukoon's "Basic Territory for Elective & Emergency
    # treatment" contains both an area-of-cover signal and the words
    # "elective"/"emergency treatment" - it's genuinely about area of
    # cover, so that must win even though "emergency treatment" also
    # matches its own category's keywords.
    assert map_label_to_category("", "Basic Territory for Elective & Emergency treatment") == "Area of Cover"


def test_section_text_resolves_a_row_whose_own_label_has_no_signal():
    # Sukoon's dental sub-items (X-ray, Tooth Extraction, ...) never say
    # "dental" in their own label - only the section banner does.
    assert map_label_to_category("Dental Benefit (Limits are inclusive of coinsurance) *1", "X-ray") == "Dental Annual Limit"


def test_unmatched_label_returns_none():
    assert map_label_to_category("", "Some Insurer-Specific Niche Benefit") is None


def test_build_standard_summary_maps_first_match_per_field():
    rows = [
        {"section": "", "label": "Area of Cover", "value": "Worldwide"},
        {"section": "", "label": "Overall Annual Maximum", "value": "AED 1,000,000"},
        {"section": "Dental Benefit", "label": "Dental Consultation", "value": "AED 5,000"},
        {"section": "", "label": "Not A Real Category", "value": "irrelevant"},
    ]
    summary = build_standard_summary_from_rows(rows)
    assert summary["area_of_cover"] == "Worldwide"
    assert summary["annual_limit"] == "AED 1,000,000"
    assert summary["dental"] == "AED 5,000"
    assert "maternity_limit" not in summary  # no matching row - left for the caller's default fill-in
