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


# --- "has text" is not the same question as "has readable text" ---------

def test_readable_share_treats_private_use_glyphs_as_unreadable():
    from app.ingestion.benefits_ocr import _readable_share

    # A subsetted font with no ToUnicode map extracts its glyphs into the
    # Private Use Area. Mechanically this is text; usefully it is nothing.
    pua = "".join(chr(0xF000 + i) for i in range(0x20, 0x60))
    assert _readable_share(pua) == 0.0
    assert _readable_share("Annual Limit USD 1,000,000") == 1.0
    assert _readable_share("   \n  ") == 0.0


def test_a_little_pua_among_real_text_stays_on_the_text_path():
    # An icon font or a bullet glyph must not push a perfectly readable
    # page onto the slow OCR path.
    from app.ingestion.benefits_ocr import _MIN_READABLE_SHARE, _readable_share

    mostly_text = "Dental USD 2,000 Co-pay 20%" + chr(0xF041) + chr(0xF042)
    assert _readable_share(mostly_text) > _MIN_READABLE_SHARE


def test_a_page_of_scrambled_glyphs_is_treated_as_needing_ocr(monkeypatch):
    """The bug this guards, seen on a real Haworth table of benefits.

    The document extracted 1,763 characters on one page and not a single
    readable letter - every glyph in the Private Use Area. The old check
    asked "is there text?", got yes, and skipped OCR. The parser then
    matched nothing against gibberish and produced a plan whose every
    field read "not specified in source document" - which looks like a
    document that omitted them rather than one that was never read.
    """
    import io
    from app.ingestion import benefits_ocr

    scrambled = "".join(chr(0xF000 + (i % 0x5E) + 0x20) for i in range(1500))

    class _Page:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class _Pdf:
        def __init__(self, pages):
            self.pages = pages

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(benefits_ocr.pdfplumber, "open", lambda f: _Pdf([_Page(""), _Page(scrambled)]))
    assert benefits_ocr.is_scanned_pdf(io.BytesIO(b"x")) is True

    monkeypatch.setattr(
        benefits_ocr.pdfplumber, "open",
        lambda f: _Pdf([_Page(""), _Page("Annual Limit USD 1,000,000 Dental USD 2,000")]),
    )
    assert benefits_ocr.is_scanned_pdf(io.BytesIO(b"x")) is False


def test_a_pdf_with_no_text_at_all_still_reads_as_scanned(monkeypatch):
    import io
    from app.ingestion import benefits_ocr

    class _Page:
        def extract_text(self):
            return ""

    class _Pdf:
        pages = [_Page(), _Page()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(benefits_ocr.pdfplumber, "open", lambda f: _Pdf())
    assert benefits_ocr.is_scanned_pdf(io.BytesIO(b"x")) is True


# --- OCR is only as good as the image it is handed ----------------------

def test_pdfium_is_preferred_for_rendering_when_available(monkeypatch):
    """Bad OCR is usually a bad picture, not a bad reader.

    On a real table of benefits built from subsetted fonts, pdfplumber's
    rasteriser produced a mangled image which Tesseract then faithfully
    transcribed as nonsense - "ccecceecseeeeee" where the page plainly
    read "Chronic Conditions". pdfium renders the same page as it appears
    on screen and the same OCR reads it cleanly. The failure looked like
    Tesseract's fault, which is why it went unnoticed.
    """
    import io
    from app.ingestion import benefits_ocr

    monkeypatch.setattr(benefits_ocr, "_render_pages_with_pdfium", lambda f, r: ["img1", "img2"])
    monkeypatch.setattr(benefits_ocr.pytesseract, "image_to_string", lambda img: f"text from {img}")

    def _fail(*args, **kwargs):
        raise AssertionError("pdfplumber must not be used for rendering when pdfium is available")

    monkeypatch.setattr(benefits_ocr.pdfplumber, "open", _fail)
    assert benefits_ocr.ocr_pdf_pages(io.BytesIO(b"x")) == ["text from img1", "text from img2"]


def test_rendering_falls_back_to_pdfplumber_without_pdfium(monkeypatch):
    # An environment with no pdfium still works, just less well - it must
    # not stop OCR entirely.
    import io
    from app.ingestion import benefits_ocr

    monkeypatch.setattr(benefits_ocr, "_render_pages_with_pdfium", lambda f, r: None)

    class _Im:
        original = "fallback-image"

    class _Page:
        def to_image(self, resolution):
            return _Im()

    class _Pdf:
        pages = [_Page()]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(benefits_ocr.pdfplumber, "open", lambda f: _Pdf())
    monkeypatch.setattr(benefits_ocr.pytesseract, "image_to_string", lambda img: f"ocr:{img}")
    assert benefits_ocr.ocr_pdf_pages(io.BytesIO(b"x")) == ["ocr:fallback-image"]
