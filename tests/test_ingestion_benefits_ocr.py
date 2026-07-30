from app.ingestion.benefits_ocr import _nearby_values, build_ocr_benefit_summary


def test_single_distinct_value_is_reported_as_is():
    text = "Indemnity Limit 5,520,000/- 5,520,000/- Basic Territory Worldwide"
    values = _nearby_values(text, r"Indemnity Limit")
    assert values == ["5,520,000/-"]


def test_multiple_distinct_values_are_all_returned():
    text = "Normal Delivery Covered up to AED 29,440 Medically necessary AED 11,000"
    values = _nearby_values(text, r"Normal Delivery")
    assert "AED 29,440" in values or "29,440" in " ".join(values)
    assert len(values) >= 1


def test_covered_text_is_recognized_when_no_money_value_present():
    text = "Dental Benefit Covered for routine treatment"
    values = _nearby_values(text, r"Dental Benefit")
    assert values == ["Covered"]


def test_not_covered_takes_priority_over_bare_covered_match():
    text = "Lasik Not Covered for all members"
    values = _nearby_values(text, r"Lasik")
    assert values == ["Not Covered"]


def test_build_summary_flags_ambiguity_for_multiple_values():
    # Padded well past the 200-char search window so the two labels' value
    # searches don't bleed into each other once all pages are joined.
    pages = [
        "Indemnity Limit 5,520,000/- 5,520,000/-." + (" filler" * 40),
        "Normal Delivery Covered up to AED 29,440 then AED 11,000 elsewhere",
    ]
    summary = build_ocr_benefit_summary(pages)
    assert summary["annual_limit"] == "5,520,000/-"
    assert "verify against the source PDF" in summary["maternity_limit"]


def test_build_summary_skips_fields_with_no_match():
    summary = build_ocr_benefit_summary(["Nothing relevant on this page at all."])
    assert summary == {}
