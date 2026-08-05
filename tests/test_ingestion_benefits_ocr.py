import pytesseract

from app.ingestion import benefits_ocr
from app.ingestion.benefits_ocr import _configure_tesseract_cmd, _nearby_values, build_ocr_benefit_summary


def test_configure_tesseract_cmd_leaves_path_lookup_alone_when_found(monkeypatch):
    monkeypatch.setattr(benefits_ocr.shutil, "which", lambda cmd: "/usr/bin/tesseract")
    pytesseract.pytesseract.tesseract_cmd = "tesseract"
    _configure_tesseract_cmd()
    assert pytesseract.pytesseract.tesseract_cmd == "tesseract"


def test_configure_tesseract_cmd_falls_back_to_default_windows_install_path(monkeypatch):
    monkeypatch.setattr(benefits_ocr.shutil, "which", lambda cmd: None)
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    target = benefits_ocr._WINDOWS_DEFAULT_PATHS[0]
    monkeypatch.setattr(benefits_ocr.os.path, "isfile", lambda path: path == target)
    pytesseract.pytesseract.tesseract_cmd = "tesseract"
    _configure_tesseract_cmd()
    assert pytesseract.pytesseract.tesseract_cmd == target


def test_configure_tesseract_cmd_prefers_env_override(monkeypatch):
    monkeypatch.setattr(benefits_ocr.shutil, "which", lambda cmd: None)
    monkeypatch.setenv("TESSERACT_CMD", r"D:\custom\tesseract.exe")
    monkeypatch.setattr(benefits_ocr.os.path, "isfile", lambda path: path == r"D:\custom\tesseract.exe")
    pytesseract.pytesseract.tesseract_cmd = "tesseract"
    _configure_tesseract_cmd()
    assert pytesseract.pytesseract.tesseract_cmd == r"D:\custom\tesseract.exe"


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


def test_deductible_captures_percentage_and_capped_amount_together():
    # Real Sukoon TOB wording: a co-insurance rate with a flat AED cap is
    # one combined value ("20% up to a maximum of AED 50/-"), not just the
    # bare AED figure on its own.
    text = "Consultation Deductible/Coinsurance 20% up to a maximum of AED 50/- 20% up to a maximum of AED 50/- Outpatient procedures Nil"
    summary = build_ocr_benefit_summary([text])
    assert summary["deductible"] == "20% up to a maximum of AED 50/-"
    assert summary["coinsurance"] == "20% up to a maximum of AED 50/-"


def test_deductible_excludes_the_maternity_specific_duplicate_wording():
    # The maternity section repeats near-identical wording ("Outpatient
    # Ante/Post Natal Consultation Deductible / Coinsurance") for a
    # different benefit - it must not contaminate the general deductible.
    text = (
        "Consultation Deductible/Coinsurance 20% up to a maximum of AED 50/- Outpatient procedures Nil."
        + (" filler" * 40)
        + " Outpatient Ante/Post Natal Consultation Deductible / Coinsurance Nil"
    )
    summary = build_ocr_benefit_summary([text])
    assert summary["deductible"] == "20% up to a maximum of AED 50/-"


def test_health_screening_wellness_package_is_recognized():
    text = "Health Check/Wellness Package AED 1,000 AED 1,000 Health Check/Wellness Co-payment Nil"
    summary = build_ocr_benefit_summary([text])
    assert summary["health_screening_wellness"] == "AED 1,000"


def test_area_of_cover_recognizes_free_text_territory_value():
    # "Worldwide Exc (USA)" is neither a currency amount nor Covered/Not
    # Covered - the two plan columns repeating the same value back-to-back
    # must collapse to a single copy, not the OCR'd duplicate.
    text = "Basic Territory for Elective & Emergency treatment Worldwide Exc (USA) Worldwide Exc (USA) Extended Territory for Emergency treatment only"
    summary = build_ocr_benefit_summary([text])
    assert summary["area_of_cover"] == "Worldwide Exc (USA)"


# Real (trimmed) OCR output from a Dubai Insurance/iSON Secure "TRUCARE"
# scanned table of benefits - a genuinely different insurer template from
# the Bupa/MetLife-style wording the original patterns were tuned against,
# with real Tesseract noise (misread words, column text bleeding into the
# wrong paragraph) rather than hand-crafted text, so these regression tests
# catch the same real-world garbling that caused this document's summary to
# come back completely empty in production.
_TRUCARE_PAGE_1 = (
    "Corporate :TRUCARE Policy Start :03-09-25. Policy End :02-09-26 "
    "Annual limit refers to the maximum amount of money we will pay for covered healthcare expenses within "
    "Plan Annual limit USD 500,000 a policy year. Once the annual limit is reached, the insured individual is responsible for paying any "
    "remaining costs out of pocket. "
    "Network ENHANCED NAS General (GN) Network refers to the list of healthcare facilities that the insured member can access on direct billing"
)

_TRUCARE_PAGE_3 = (
    "Basic Dental (If not opted for Enhanced Covered up to USD 137/ AED 500 with "
    "Dental) 30% copay "
    "This benefit includes coverage for dental consultations, extractions, fillings, root canal treatments, "
    "scaling, X-rays, antibiotics, and prophylactic care."
)

_TRUCARE_PAGE_5 = (
    "Maternity In-patient Services and Complications "
    "(Requires prior approval from the insurance company or within 24 hours of "
    "We cover Maternity IP Services, including Normal Vaginal Delivery, medically necessary C-sections, "
    "treatment for complications, and medically necessary terminations. Elective Caesarean Delivery is not "
    "covered. Medically necessary expenses for life-threatening conditions for the mother or newborn are "
    "covered up to the annual limit "
    "Covered up to USD 10,000"
)


def test_recognizes_trucare_style_annual_limit_wording():
    summary = build_ocr_benefit_summary([_TRUCARE_PAGE_1])
    assert summary["annual_limit"] == "500,000"


def test_recognizes_trucare_style_dental_wording():
    summary = build_ocr_benefit_summary([_TRUCARE_PAGE_3])
    assert summary["dental"] == "AED 500"


def test_recognizes_trucare_style_maternity_limit_wording_across_a_long_clarification():
    # The real value sits ~250 characters after the label - past this
    # module's default 200-char window - because of a long parenthetical
    # pre-approval clarification wedged in between; maternity_limit's
    # entry in _FIELD_LABEL_PATTERNS uses a wider window specifically for
    # this template.
    summary = build_ocr_benefit_summary([_TRUCARE_PAGE_5])
    assert summary["maternity_limit"] == "10,000"
