from app.scoring.rules.benefits_summary import (
    NOT_SPECIFIED,
    STANDARD_FIELDS,
    build_standard_benefit_summary,
    format_benefit_summary_markdown,
)


def test_build_standard_benefit_summary_uses_all_ten_fields():
    plan_details = {
        "area_of_cover": "Worldwide excl. U.S.",
        "annual_limit": "USD 4,700,000",
        "maternity_limit": "USD 8,500 per delivery",
    }
    summary = build_standard_benefit_summary(plan_details)
    assert set(summary.keys()) == set(STANDARD_FIELDS)
    assert summary["area_of_cover"] == "Worldwide excl. U.S."
    assert summary["annual_limit"] == "USD 4,700,000"


def test_missing_fields_default_to_not_specified_rather_than_disappearing():
    summary = build_standard_benefit_summary({"annual_limit": "USD 1,000,000"})
    assert summary["deductible"] == NOT_SPECIFIED
    assert summary["dental"] == NOT_SPECIFIED
    assert summary["pharmacy_limit_and_coinsurance"] == NOT_SPECIFIED


def test_format_benefit_summary_markdown_includes_plan_name_and_all_rows():
    markdown = format_benefit_summary_markdown("Premier", {"annual_limit": "USD 4,700,000"})
    assert "### Premier" in markdown
    assert "Annual Limit" in markdown
    assert "USD 4,700,000" in markdown
    assert "Pharmacy Limit & Coinsurance" in markdown
