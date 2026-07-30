from app.ingestion.benefits_pdf import _build_tier_summaries, _find_matching_label, to_benefit_plan_fields

SAMPLE_ROWS = {
    "overall annual maximum": {
        "select": "USD 1,000,000 each membership year",
        "premier": "USD 4,700,000 each membership year",
    },
    "geographical cover": {
        "select": "Regional Middle East countries only",
        "premier": "Regional Middle East OR Worldwide excluding U.S.",
    },
    "diagnostic tests and treatment services for dental and gums for emergency dental treatment only inside the uae": {
        "select": "Inside the UAE: Paid in full",
        "premier": "Inside the UAE: Paid in full",
    },
    "dental": {
        "select": "Optional cover, if purchased We pay up to USD 840 each membership year",
        "premier": "Optional cover, if purchased We pay up to USD 2,000 each membership year",
    },
    "optical": {
        "select": "Optional cover, if purchased We pay up to USD 425 each membership year",
        "premier": "Optional cover, if purchased We pay up to USD 425 each membership year",
    },
    "maternity and childbirth cover": {
        "select": "We pay up to USD 2,725 per delivery",
        "premier": "We pay up to USD 8,500 per delivery",
    },
    "congenital and hereditary conditions": {
        "select": "We pay up to USD 84,000 maximum benefit for the whole of your lifetime",
        "premier": "We pay up to USD 116,300 maximum benefit for the whole of your lifetime",
    },
    "full health screening": {
        "select": "Not covered",
        "premier": "Not covered",
    },
}


def test_exact_label_match_wins_over_substring_match():
    # "dental" (the real limit row) must win over the unrelated
    # "...emergency dental treatment..." row, which also contains "dental".
    matched = _find_matching_label(SAMPLE_ROWS, "dental")
    assert matched == "dental"


def test_build_tier_summaries_maps_known_fields_per_tier():
    summaries = _build_tier_summaries(SAMPLE_ROWS)

    assert summaries["premier"]["annual_limit"] == "USD 4,700,000 each membership year"
    assert summaries["premier"]["dental"] == "Optional cover, if purchased We pay up to USD 2,000 each membership year"
    assert summaries["premier"]["maternity_limit"] == "We pay up to USD 8,500 per delivery"
    assert summaries["select"]["annual_limit"] == "USD 1,000,000 each membership year"


def test_to_benefit_plan_fields_extracts_numeric_annual_limit():
    summary = _build_tier_summaries(SAMPLE_ROWS)["premier"]
    fields = to_benefit_plan_fields("premier", summary)

    assert fields["annual_limit"] == 4_700_000.0
    assert fields["maternity_limit"] == 8_500.0
    assert fields["maternity_covered"] is True
    assert fields["dental_covered"] is True
    assert fields["optical_covered"] is True
    assert fields["source_format"] == "pdf"
    assert fields["plan_name"] == "Premier"


def test_to_benefit_plan_fields_respects_not_covered():
    fields = to_benefit_plan_fields("select", {"pre_existing_chronic_limit": "Not covered"})
    assert fields["pre_existing_covered"] is False
    assert fields["chronic_covered"] is False
