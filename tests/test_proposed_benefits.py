"""The client's existing cover beside what HealthCross is proposing -
app/scoring/rules/proposed_benefits.py.

The whole point of this module is that both sides have to be phrased
alike. A comparison between "USD 2,000 Co-pay: 20%" and a pair of
dropdown values is not a comparison anyone can read.
"""
from app.scoring.rules.proposed_benefits import (
    FIELDS_WITHOUT_VARIANTS,
    base_option_by_variant,
    proposed_benefit_rows,
    proposed_benefit_summary,
)

# A rate card names, for each variant, the option a member gets when
# nobody touches the dropdown - the "base" direction.
RATES = [
    {"variant_name": "Dental Limit", "option_value": "USD 1,000", "direction": "base"},
    {"variant_name": "Dental Copay", "option_value": "20%", "direction": "base"},
    {"variant_name": "Annual Limit", "option_value": "USD 500,000", "direction": "base"},
    {"variant_name": "Dental Limit", "option_value": "USD 3,000", "direction": "up"},
]


def test_limit_and_copay_are_written_as_one_benefit_line():
    summary = proposed_benefit_summary({"Dental Limit": "USD 3,000", "Dental Copay": "10%"}, RATES)
    assert summary["dental"] == "USD 3,000 Co-pay: 10%"


def test_an_untouched_dropdown_resolves_to_its_base_option_not_to_blank():
    # A variant left alone is not "no cover" - it is whatever the base
    # option carries, and rendering it blank would show the proposal as
    # offering nothing where it offers the base level, reading as a
    # reduction against any incumbent who covers it at all.
    summary = proposed_benefit_summary({"Dental Limit": "USD 3,000"}, RATES)
    assert summary["dental"] == "USD 3,000 Co-pay: 20%"


def test_a_copay_only_line_reads_as_the_copay_itself():
    # OP Copay is the whole benefit, not a qualifier on a missing limit.
    summary = proposed_benefit_summary({"OP Copay": "20%"}, RATES)
    assert summary["coinsurance"] == "20%"


def test_base_options_are_matched_despite_spelling_and_case_drift():
    base = base_option_by_variant([
        {"variant_name": " dental  LIMIT ", "option_value": "USD 1,000", "direction": "Base"},
    ])
    assert base["dental limit"] == "USD 1,000"


# --- the side-by-side table --------------------------------------------

EXISTING = {
    "network": "Premium",
    "annual_limit": "USD 1,000,000",
    "dental": "USD 2,000 Co-pay: 20%",
}


def test_every_standard_field_gets_a_row_even_with_one_side_missing():
    from app.scoring.rules.benefits_summary import STANDARD_FIELDS

    rows = proposed_benefit_rows(EXISTING, {"Dental Limit": "USD 3,000"}, RATES)
    assert [r["field"] for r in rows] == STANDARD_FIELDS


def test_a_benefit_the_incumbent_document_never_mentions_is_visibly_absent():
    # "The TOB says nothing about optical" is itself worth seeing - it is
    # where a proposal is most likely to be adding cover nobody priced.
    rows = {r["field"]: r for r in proposed_benefit_rows(EXISTING, {}, RATES)}
    assert rows["optical"]["existing"] is None


def test_a_richer_limit_is_reported_as_an_improvement():
    rows = {r["field"]: r for r in proposed_benefit_rows(EXISTING, {"Dental Limit": "USD 3,000"}, RATES)}
    assert rows["dental"]["direction"] == "improved"


def test_a_field_with_no_dropdown_behind_it_says_so_rather_than_reading_as_unoffered():
    rows = {r["field"]: r for r in proposed_benefit_rows(EXISTING, {}, RATES)}
    for field in FIELDS_WITHOUT_VARIANTS:
        assert rows[field]["priced_as_variant"] is False
    assert rows["dental"]["priced_as_variant"] is True


def test_network_is_filled_from_the_case_rather_than_left_blank():
    # Network is priced as a dimension of the rate card, not as a benefit
    # dropdown - so it has no variant to read, but it is still decided
    # and still part of the proposal. Left blank, the one field that
    # frames every limit under it reads as something HealthCross did not
    # offer.
    rows = {
        r["field"]: r
        for r in proposed_benefit_rows(EXISTING, {}, RATES, proposed_overrides={"network": "MSH Platinum"})
    }
    assert rows["network"]["existing"] == "Premium"
    assert rows["network"]["proposed"] == "MSH Platinum"


def test_an_empty_override_does_not_blank_out_a_value_that_was_resolved():
    rows = {
        r["field"]: r
        for r in proposed_benefit_rows(
            EXISTING, {"Dental Limit": "USD 3,000"}, RATES, proposed_overrides={"dental": None}
        )
    }
    assert rows["dental"]["proposed"] == "USD 3,000 Co-pay: 20%"
