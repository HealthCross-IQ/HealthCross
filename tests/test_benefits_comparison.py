import pytest

from app.scoring.rules.benefits_comparison import compare_benefit_summaries, compare_benefit_value


def test_annual_limit_reduction_across_currencies():
    # Real Palazzo Versace figures: existing AED 5,520,000 vs quoted CAT A USD 1,000,000
    result = compare_benefit_value("AED 5,520,000", "USD 1,000,000")
    assert result["direction"] == "reduced"
    assert result["existing_amount_aed"] == 5_520_000.0
    assert result["quoted_amount_aed"] == pytest.approx(3_672_500.0)
    assert result["pct_change"] == pytest.approx(-33.5, abs=0.1)


def test_maternity_improvement_across_currencies():
    # Real Palazzo Versace CAT B figures: existing AED 11,000 vs quoted USD 6,800
    result = compare_benefit_value("AED 11,000", "USD 6,800")
    assert result["direction"] == "improved"
    assert result["pct_change"] == pytest.approx(127.0, abs=0.5)


def test_identical_covered_text_is_same():
    result = compare_benefit_value("Covered", "Covered")
    assert result["direction"] == "same"
    assert result["existing_amount_aed"] is None


def test_not_covered_vs_covered_is_flagged_for_review_not_guessed():
    result = compare_benefit_value("Covered", "Not Covered")
    assert result["direction"] == "review"


def test_unparseable_value_is_flagged_for_review_rather_than_guessed():
    result = compare_benefit_value("Not specified in source document", "USD 500")
    assert result["direction"] == "review"
    assert result["quoted_amount_aed"] is None  # only compares when BOTH sides parse


def test_compare_benefit_summaries_covers_every_standard_field():
    existing = {"annual_limit": "AED 5,520,000", "dental": "AED 13,800"}
    quoted = {"annual_limit": "USD 1,000,000", "dental": "USD 3,000"}
    result = compare_benefit_summaries(existing, quoted)
    assert result["annual_limit"]["direction"] == "reduced"
    assert result["dental"]["direction"] == "reduced"
    assert result["area_of_cover"]["direction"] == "review"  # neither side supplied


def test_compare_benefit_summaries_includes_health_screening_wellness():
    existing = {"health_screening_wellness": "AED 1,000"}
    quoted = {"health_screening_wellness": "AED 1,500"}
    result = compare_benefit_summaries(existing, quoted)
    assert result["health_screening_wellness"]["direction"] == "improved"
