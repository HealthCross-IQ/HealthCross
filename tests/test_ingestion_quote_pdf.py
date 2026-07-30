# Fixture values below are taken directly from the real QIC/HealthCROSS
# Global quote for Palazzo Versace Hotel (CAT A: 54 members / AED 918,950,
# CAT B: 88 members / AED 1,328,222) - this is the exact shape
# _extract_category_premium_table/_extract_benefit_rows produce from that
# real document, captured here so the pure logic is testable without
# committing the source PDF.
from app.ingestion.quote_pdf import _build_category_results, _find_matching_label

CATEGORIES = [
    {"category": "A", "member_count": 54, "plan_name": "Gold", "network": "MSH Platinum", "gross_premium": 918950.0},
    {"category": "B", "member_count": 88, "plan_name": "Gold", "network": "MSH Platinum", "gross_premium": 1328222.0},
]

ROWS = {
    "annual policy limit": {"Gold - CAT A": "USD 1,000,000", "Gold - CAT B": "USD 750,000"},
    "area of cover": {"Gold - CAT A": "Worldwide Excluding USA", "Gold - CAT B": "Worldwide Excluding USA"},
    "pre-existing & chronic conditions": {"Gold - CAT A": "Covered up to Policy Limit", "Gold - CAT B": "Covered up to Policy Limit"},
    "maternity inpatient- limit": {"Gold - CAT A": "USD 6,800", "Gold - CAT B": "USD 6,800"},
    "annual dental cover": {"Gold - CAT A": "USD 3,000", "Gold - CAT B": "USD 1,000"},
    "annual optical cover": {"Gold - CAT A": "USD 500", "Gold - CAT B": "Not Covered"},
    "gp/specialist consultations": {"Gold - CAT A": "20% MAX AED 50", "Gold - CAT B": "20% MAX AED 50"},
    "complementary and alternative treatments:ayurveda,homeopathy,podiatry,chiropractic,osteopathy,acupuncture & traditional chinese medicine": {
        "Gold - CAT A": "USD 1,000",
        "Gold - CAT B": "USD 1,000",
    },
    "prescribed drugs & dressings - annual limit": {"Gold - CAT A": "Annual Limit", "Gold - CAT B": "Annual Limit"},
    "prescribed drugs & dressings - copay": {"Gold - CAT A": "NIL", "Gold - CAT B": "NIL"},
}


def test_exact_maternity_label_wins_over_nothing_else_matching():
    assert _find_matching_label(ROWS, "maternity inpatient- limit") == "maternity inpatient- limit"


def test_annual_limit_anchor_does_not_confuse_dental_or_optical():
    matched = _find_matching_label(ROWS, "annual dental cover")
    assert matched == "annual dental cover"
    assert ROWS[matched]["Gold - CAT B"] == "USD 1,000"


def test_build_category_results_matches_real_palazzo_quote_figures():
    results = _build_category_results(CATEGORIES, ROWS)
    assert len(results) == 2

    cat_a, cat_b = results
    assert cat_a["category"] == "A"
    assert cat_a["member_count"] == 54
    assert cat_a["gross_premium"] == 918950.0
    assert cat_a["annual_limit"] == 1_000_000.0
    assert cat_a["maternity_limit"] == 6_800.0
    assert cat_a["optical_covered"] is True
    assert cat_a["standard_summary"]["dental"] == "USD 3,000"

    assert cat_b["category"] == "B"
    assert cat_b["gross_premium"] == 1_328_222.0
    assert cat_b["annual_limit"] == 750_000.0
    assert cat_b["optical_covered"] is False
    assert cat_b["standard_summary"]["optical"] == "Not Covered"


def test_prescribed_drugs_annual_limit_not_confused_with_copay_row():
    results = _build_category_results(CATEGORIES, ROWS)
    # both rows share the "prescribed drugs & dressings" prefix; the
    # anchor is the full "...- annual limit" string, so it must not pick
    # up the copay row's "NIL" value instead.
    assert results[0]["standard_summary"]["pharmacy_limit_and_coinsurance"] == "Annual Limit"
