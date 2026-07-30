# Fixture rows below are taken directly from real screenshots of an
# existing/incumbent HealthCROSS Global "SILVER - CAT A" benefits document
# (a different real-world upload than the Palazzo QIC quote in
# test_ingestion_quote_pdf.py) - no premium table at all, and several field
# labels worded slightly differently than the quote ("Maternity Inpatient
# Limit" with no hyphen, "Annual Maximum Optical Cover" instead of "Annual
# Optical Cover", a bare "Alternative treatments:..." instead of
# "Complementary and Alternative treatments:...").
from unittest.mock import patch

from app.ingestion import quote_pdf

ROWS = {
    "annual policy limit": {"SILVER - CAT A": "USD 1,000,000"},
    "area of cover": {"SILVER - CAT A": "Worldwide Excluding. USA"},
    "pre-existing & chronic conditions": {"SILVER - CAT A": "Covered up to policy limit"},
    "medical network": {"SILVER - CAT A": "MSH Platinum"},
    "gp/specialist consultations": {"SILVER - CAT A": "20% up to AED 50"},
    "laboratory ,radiology and pathology tests copay": {"SILVER - CAT A": "NIL"},
    "alternative treatments: ayurvedic, homeopathy, acupuncture & traditional chinese medicine": {"SILVER - CAT A": "USD 500"},
    "maternity inpatient limit": {"SILVER - CAT A": "USD 6,800"},
    "annual maximum optical cover": {"SILVER - CAT A": "USD 300"},
}


def test_maternity_matches_no_hyphen_wording_variant():
    assert quote_pdf._find_matching_label(ROWS, quote_pdf._FIELD_LABEL_ANCHORS["maternity_limit"]) == "maternity inpatient limit"


def test_optical_matches_annual_maximum_wording_variant():
    assert quote_pdf._find_matching_label(ROWS, quote_pdf._FIELD_LABEL_ANCHORS["optical"]) == "annual maximum optical cover"


def test_alternative_treatment_matches_bare_wording_without_complementary_prefix():
    matched = quote_pdf._find_matching_label(ROWS, quote_pdf._FIELD_LABEL_ANCHORS["alternative_or_complementary_treatment"])
    assert matched is not None
    assert "alternative treatment" in matched


def test_parse_benefit_tables_only_self_discovers_tier_and_reads_all_fields():
    with patch.object(quote_pdf, "_extract_benefit_rows", return_value=ROWS):
        with patch.object(quote_pdf.pdfplumber, "open") as mock_open:
            mock_open.return_value.__enter__.return_value = object()
            result = quote_pdf.parse_benefit_tables_only(b"", "existing.pdf")

    assert "SILVER - CAT A" in result
    plan = result["SILVER - CAT A"]
    assert plan["category"] == "A"
    assert plan["network"] == "MSH Platinum"
    assert plan["annual_limit"] == 1_000_000.0
    assert plan["maternity_limit"] == 6_800.0
    assert plan["optical_covered"] is True
    assert plan["standard_summary"]["optical"] == "USD 300"
    assert plan["standard_summary"]["coinsurance"] == "20% up to AED 50"
    assert plan["standard_summary"]["alternative_or_complementary_treatment"] == "USD 500"


def test_parse_benefit_tables_only_returns_empty_when_no_cat_style_table_found():
    with patch.object(quote_pdf, "_extract_benefit_rows", return_value={}):
        with patch.object(quote_pdf.pdfplumber, "open") as mock_open:
            mock_open.return_value.__enter__.return_value = object()
            result = quote_pdf.parse_benefit_tables_only(b"", "unrelated.pdf")
    assert result == {}
