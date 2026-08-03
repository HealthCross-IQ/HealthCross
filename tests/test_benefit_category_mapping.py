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
    antenatal_care_covered_from_rows,
    build_standard_summary_from_rows,
    clean_category_value,
    dental_class_coinsurance_from_rows,
    extract_copay_clause,
    healthcross_global_maternity_coinsurance_from_rows,
    looks_like_cigna_globalcare,
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


def test_maps_bupa_transplant_services_wording_onto_organ_transplant():
    assert map_label_to_category("", "Transplant Services") == "Organ Transplant"


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


def test_maternity_inpatient_coinsurance_row_does_not_pollute_maternity_limit():
    # A real Sukoon/Arabyads row ("Inpatient Maternity: 10%", under a
    # "Co-Insurance/Deductible" section) must land in its own Maternity
    # Co-insurance category, not get swallowed by Maternity Annual Limit's
    # greedy bare "maternity" catch-all keyword.
    assert map_label_to_category("Co-Insurance/Deductible", "Inpatient Maternity") == "Maternity Co-insurance"


def test_maternity_complications_becomes_the_maternity_limit_when_no_combined_limit_row():
    # Sukoon/Arabyads never states one combined "Maternity Annual Limit"
    # figure - "Maternity complications" is described as covered "up to
    # indemnity limit", i.e. it IS the real ceiling, so it should win over
    # the smaller itemized Normal Delivery/C-Section amounts rather than
    # being folded into a combined "Normal Delivery: X; C-Section: Y" string.
    rows = [
        {"section": "Maternity & New-Born", "label": "Normal Delivery", "value": "25,000/-"},
        {"section": "Maternity & New-Born", "label": "Medically necessary C-Section", "value": "30,000/-"},
        {"section": "Maternity & New-Born", "label": "Maternity complications", "value": "30,000/-"},
        {"section": "Co-Insurance/Deductible", "label": "Inpatient Maternity", "value": "10%"},
    ]
    summary = build_standard_summary_from_rows(rows)
    assert summary["maternity_limit"] == "30,000/-"
    assert summary["maternity_coinsurance"] == "10%"


def test_network_value_keeps_only_the_first_network_name():
    # Sukoon/Arabyads crams a primary network name, a second narrower one,
    # and a per-category co-insurance breakdown into one cell with no
    # punctuation between them ("Edge CCAD IP: 20% OP...") - only the
    # first word is the actual network name callers want.
    value = "Edge CCAD IP: 20% OP (excluding Pharmacy): 20% Pharmacy: 10% Maternity IP: 10%"
    assert clean_category_value("Network / Provider Tier", value) == "Edge"


def test_cigna_accommodation_bullet_maps_to_room_type():
    # Cigna's own wording bundles the room type into a bullet list under
    # the generic "Hospital charges for:" label ("Accommodation on a
    # private room basis for in-patient treatment") rather than a phrase
    # containing "room accommodation"/"hospital room" at all.
    label = "Hospital charges for: Accommodation on a private room basis for in-patient treatment"
    assert map_label_to_category("In-patient /day case healthcare benefits", label) == "Room Type / Accommodation"


def test_cigna_complications_of_pregnancy_maps_to_maternity_complications():
    assert map_label_to_category("Maternity benefits", "Complications of pregnancy and childbirth") == "Maternity Complications"


def test_cigna_vision_expenses_row_maps_to_optical_annual_limit():
    # Cigna's own "Annual Maximum" row under Vision benefits just covers
    # the eye exam (paid in full) - the real dollar limit for frames/
    # lenses sits on this separate, more specific row instead.
    label = "Expenses for: Prescribed lenses to correct vision, Eyeglass frames"
    assert map_label_to_category("Vision benefits", label) == "Optical Annual Limit"


def test_dental_class_coinsurance_combines_in_class_order():
    rows = [
        {"section": "Dental benefits", "label": "Class one Investigative and Preventative treatment", "value": "NIL co-pay"},
        {"section": "Dental benefits", "label": "Class three Major restorative treatment", "value": "50% co-pay"},
        {"section": "Dental benefits", "label": "Class two Basic restorative treatment", "value": "20% co-pay"},
    ]
    assert dental_class_coinsurance_from_rows(rows) == "Class one: NIL co-pay; Class two: 20% co-pay; Class three: 50% co-pay"


def test_dental_class_coinsurance_returns_none_without_class_rows():
    rows = [{"section": "Dental benefits", "label": "Annual maximum", "value": "USD 3,750"}]
    assert dental_class_coinsurance_from_rows(rows) is None


def test_looks_like_cigna_globalcare_detects_network_column_header():
    # "In-Cigna Healthcare network"/"Out-of-Cigna Healthcare network" is
    # this document family's own network-tier column header, occurring in
    # effectively every Cigna Global Care export.
    rows = [{"section": "", "label": "In-Cigna Healthcare network", "value": "Out-of-Cigna Healthcare network"}]
    assert looks_like_cigna_globalcare(rows) is True
    assert looks_like_cigna_globalcare([{"section": "", "label": "Something else", "value": "Covered"}]) is False


def test_cigna_maternity_coinsurance_defaults_to_nil_when_maternity_is_covered():
    # Cigna Global Care never states a maternity co-insurance figure at
    # all - it's always NIL for this insurer, so it has to be supplied as
    # a known default rather than extracted from a row that doesn't exist.
    rows = [
        {"section": "", "label": "In-Cigna Healthcare network", "value": "Out-of-Cigna Healthcare network"},
        {"section": "Maternity benefits", "label": "Complications of pregnancy and childbirth", "value": "Covered"},
    ]
    summary = build_standard_summary_from_rows(rows)
    assert summary["maternity_coinsurance"] == "NIL"


def test_cigna_oncology_label_maps_to_cancer_treatment():
    # Cigna's own label/section is "Oncology treatment", never a phrase
    # containing "cancer treatment" at all.
    label = "Oncology treatment in-patient and out-patient"
    assert map_label_to_category("Oncology treatment", label) == "Cancer Treatment"


def test_cigna_routine_out_patient_maps_to_antenatal_care():
    # The word "antenatal" only appears in this row's clarification note,
    # never its label ("Routine out-patient") or section ("Maternity
    # benefits") - map_label_to_category never sees the note.
    assert map_label_to_category("Maternity benefits", "Routine out-patient") == "Antenatal Care"


def test_cigna_routine_adult_physical_exam_maps_to_health_check_up():
    assert map_label_to_category("Wellbeing benefits", "Routine adult physical examinations") == "Health Check-up"


def test_cigna_international_emergency_services_maps_to_evacuation_category():
    label = "International emergency services"
    assert map_label_to_category("In-patient /day case healthcare benefits", label) == "Emergency Medical Evacuation & Repatriation"


def test_extract_copay_clause_pulls_trailing_copay_out_of_a_limit_value():
    # Cigna states the co-pay inline in the same cell as the dollar limit
    # ("US $500 per year of insurance Co-pay: NIL") rather than as its own
    # row, so there's no separate row a normal category match could find.
    assert extract_copay_clause("US $ 500 per year of insurance Co-pay: NIL") == "NIL"
    assert extract_copay_clause("Paid in full Co-pay: NIL") == "NIL"
    assert extract_copay_clause("USD 3,750 per Year of Insurance") is None
    assert extract_copay_clause(None) is None


def test_extract_copay_clause_also_pulls_a_trailing_coinsurance_clause():
    # Cigna Smart Care states this as "Co-insurance: 20%" rather than
    # Global Care's "Co-pay: NIL" - same inline-clause shape, different word.
    assert extract_copay_clause("Option 1: AED 1,000 per year of insurance Co-insurance: 20%") == "20%"


def test_cigna_smart_care_accommodation_costs_bullet_maps_to_room_type():
    # Smart Care's own wording is "Accommodation costs for..." rather than
    # Global Care's "Accommodation on a private room basis...".
    label = "Hospital charges for: Accommodation costs for in-patient treatment"
    assert map_label_to_category("In-patient / day case healthcare benefits", label) == "Room Type / Accommodation"


def test_hyphen_wrap_normalization_matches_a_line_wrapped_compound_word():
    # A PDF line wrap inside a hyphenated compound re-extracts as the
    # hyphen followed by a stray space ("work- related") rather than the
    # clean "work-related" a keyword is written against.
    assert map_label_to_category("Other benefits", "Non-emergency work- related injuries") == "Work-related Injuries"


def test_hyphen_wrap_normalization_does_not_break_the_wellness_mammogram_keyword():
    # "wellness - mammogram" legitimately has spaces around its hyphen in
    # the keyword list itself - normalizing "-\s+" to "-" on both sides
    # must not stop this from still matching.
    assert map_label_to_category("", "Wellness - Mammogram screening") == "Health Check-up"


def test_cigna_smart_care_routine_out_patient_coinsurance_maps_to_maternity_coinsurance():
    # Requires the word "routine" - a bare "out-patient co-insurance" also
    # exists elsewhere in the same document as its own generic, unrelated
    # row (the plan's overall out-patient co-insurance).
    assert map_label_to_category("Maternity benefits", "Routine out-patient co-insurance") == "Maternity Co-insurance"
    assert map_label_to_category("Maternity benefits", "Routine out-patient co- insurance") == "Maternity Co-insurance"


def test_generic_out_patient_coinsurance_row_is_not_hijacked_by_maternity():
    assert map_label_to_category("", "Out-patient co-insurance") == "Outpatient Co-insurance/Deductible"


def test_antenatal_care_covered_from_rows_reads_the_pregnancy_note():
    # Cigna Smart Care never gives out-patient antenatal care its own row
    # (that row is claimed by Maternity Co-insurance instead) - the only
    # signal is this distinctive clarification note text on some other row.
    rows = [
        {
            "section": "Maternity benefits",
            "label": "Routine out-patient co-insurance",
            "value": "10%",
            "note": "Pregnancy benefits and services as per DHA mandate",
        },
    ]
    assert antenatal_care_covered_from_rows(rows) == "Covered"


def test_antenatal_care_covered_from_rows_returns_none_without_the_note():
    rows = [{"section": "", "label": "Something else", "value": "Covered", "note": ""}]
    assert antenatal_care_covered_from_rows(rows) is None


def test_healthcross_global_maternity_inpatient_copay_maps_to_maternity_coinsurance():
    # HealthCROSS Global's own label is "Maternity inpatient- Copay",
    # separate from "Maternity Inpatient- Limit" (which is the real
    # in-patient maternity ceiling and rightly stays on Maternity Annual
    # Limit) sitting right next to it in the same section.
    section = "Maternity Benefits (For Married Females):"
    assert map_label_to_category(section, "Maternity Inpatient- Limit") == "Maternity Annual Limit"
    assert map_label_to_category(section, "Maternity inpatient- Copay") == "Maternity Co-insurance"


def test_healthcross_global_maternity_outpatient_deductible_maps_to_maternity_coinsurance():
    # "Maternity outpatient- Limit" is the real antenatal-checkups coverage
    # (rightly Antenatal Care), while "Maternity Outpatient Deductible"
    # sitting right next to it is the co-insurance-equivalent figure, not
    # a second coverage-status row - it must not be swallowed by Antenatal
    # Care's own "maternity outpatient" keyword.
    section = "Maternity Benefits (For Married Females):"
    assert map_label_to_category(section, "Maternity outpatient- Limit") == "Antenatal Care"
    assert map_label_to_category(section, "Maternity Outpatient Deductible") == "Maternity Co-insurance"


def test_healthcross_global_maternity_coinsurance_combines_inpatient_and_outpatient_rows():
    # Neither row alone states a maternity co-insurance percentage - the
    # document only ever gives these two split copay/deductible rows, so
    # both need to be surfaced together rather than one silently winning
    # over the other as the first match found.
    rows = [
        {"section": "Maternity Benefits (For Married Females):", "label": "Maternity Inpatient- Limit", "value": "USD 14,000", "note": ""},
        {"section": "Maternity Benefits (For Married Females):", "label": "Maternity inpatient- Copay", "value": "NIL Copay", "note": ""},
        {"section": "Maternity Benefits (For Married Females):", "label": "Maternity outpatient- Limit", "value": "Covered", "note": ""},
        {"section": "Maternity Benefits (For Married Females):", "label": "Maternity Outpatient Deductible", "value": "NIL Deductible", "note": ""},
    ]
    assert healthcross_global_maternity_coinsurance_from_rows(rows) == "Inpatient Copay: NIL Copay; Outpatient Deductible: NIL Deductible"


def test_healthcross_global_maternity_coinsurance_returns_none_without_those_rows():
    rows = [{"section": "", "label": "Something else", "value": "Covered", "note": ""}]
    assert healthcross_global_maternity_coinsurance_from_rows(rows) is None
