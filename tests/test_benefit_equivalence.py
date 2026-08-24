"""When two differently-worded benefit values say the same thing -
app/scoring/rules/benefit_equivalence.py.

Every pair below came off a real Cigna Global Care booklet compared
against a real HealthCross quote, where twelve of thirteen rows reported
"review" and only two of them were genuinely different.
"""
import pytest

from app.scoring.rules.benefit_equivalence import (
    INCUMBENT_NETWORK_EQUIVALENTS,
    area_of_cover_key,
    equivalent_areas,
    equivalent_networks,
    equivalent_values,
)
from app.scoring.rules.benefits_comparison import compare_benefit_value, extract_amount_aed


@pytest.mark.parametrize("existing,proposed", [
    ("Covered", "Covered up to Policy Limit"),
    ("Covered", "Covered up to Policy limit"),
    ("Covered", "Fully Covered"),
    ("Covered", "Paid in Full"),
    ("Covered up to Policy Limit", "Annual Limit"),
    ("Covered Co-pay: Nil", "Covered"),
])
def test_cover_at_the_plans_own_limit_is_one_statement_however_it_is_worded(existing, proposed):
    assert equivalent_values(existing, proposed)


@pytest.mark.parametrize("existing,proposed", [
    ("0%", "NIL"),
    ("NIL", "Nil"),
    ("None", "0%"),
    ("-", "NIL"),
])
def test_nil_is_nil_however_it_is_written(existing, proposed):
    assert equivalent_values(existing, proposed)


def test_an_amount_is_never_a_synonym_for_anything():
    # The moment a number is involved, equivalence has no business
    # guessing - that is what the numeric comparison is for.
    assert not equivalent_values("USD 2,000", "Covered")
    assert not equivalent_values("USD 2,000", "USD 3,000")


def test_two_values_that_merely_fail_to_parse_are_not_equivalent():
    # "Unrecognised" is not a family. Treating it as one would match
    # every pair of free-text values the parsers could not read.
    assert not equivalent_values("Refer to policy schedule", "Subject to underwriting")


# --- the member-contribution distinction --------------------------------

def test_a_bare_nil_matches_a_nil_copay_in_a_contribution_field():
    # Pharmacy on the incumbent's document read "NIL"; the proposal read
    # "Annual Limit Co-pay: NIL". Both say the member pays nothing.
    assert equivalent_values("NIL", "Annual Limit Co-pay: NIL", "pharmacy_limit_and_coinsurance")
    assert equivalent_values("0%", "NIL", "coinsurance")


def test_the_same_pair_in_a_limit_field_is_not_a_match():
    # This is the whole point of making it field-aware. In a limit field
    # "NIL" means no cover at all, and collapsing it into "covered up to
    # the plan limit" would report a dropped dental benefit as unchanged
    # - the worst mistake this comparison can make.
    assert not equivalent_values("NIL", "Covered up to Policy Limit", "dental")
    assert not equivalent_values("NIL", "Annual Limit Co-pay: NIL", "dental")


def test_a_real_copay_is_not_a_nil_copay():
    assert not equivalent_values("NIL", "Annual Limit Co-pay: 20%", "coinsurance")


# --- networks between insurers ------------------------------------------

def test_an_incumbents_network_matches_the_healthcross_one_of_equal_breadth():
    assert equivalent_networks("COMPREHENSIVE", "MSH Platinum")


def test_network_equivalence_is_not_reversed_by_accident():
    # The table maps an incumbent's name onto a HealthCross one, not the
    # other way round - "MSH Comprehensive" is a genuinely narrower
    # network than Platinum in HealthCross's own book.
    assert not equivalent_networks("MSH Comprehensive", "MSH Platinum")


def test_an_unmapped_network_pair_still_asks_for_a_human():
    assert not equivalent_networks("Restricted", "MSH Platinum")


def test_the_incumbent_table_is_kept_apart_from_the_books_own_mapping():
    # app/reference/network_type_mapping.py maps HealthCross's OWN
    # NETWORKTYPE values and feeds every burning-cost lookup; it reads
    # "comprehensive" as MSH Comprehensive. The two disagree on purpose
    # because they answer different questions, and merging them would
    # silently reprice the whole book.
    from app.reference.network_type_mapping import map_network_type

    assert map_network_type("comprehensive") == "MSH Comprehensive"
    assert INCUMBENT_NETWORK_EQUIVALENTS["comprehensive"] == "msh platinum"


# --- area of cover ------------------------------------------------------

@pytest.mark.parametrize("value", [
    "Worldwide excluding USA",
    "Worldwide Excluding USA",
    "WW Exc USA",
    "Area II: Worldwide excluding USA",
])
def test_the_same_territory_written_many_ways_is_one_territory(value):
    assert area_of_cover_key(value) == "worldwide_excl_usa"


def test_worldwide_and_worldwide_excluding_usa_are_not_the_same_territory():
    # They differ by the single most expensive country in the world, and
    # "Worldwide excluding USA" contains the word "worldwide" - so a
    # naive check collapses exactly the pair that must never collapse.
    assert area_of_cover_key("Worldwide") == "worldwide"
    assert not equivalent_areas("Worldwide", "Worldwide excluding USA")


def test_the_incumbents_area_matches_the_rate_cards_own_zone():
    assert equivalent_areas("Area II: Worldwide excluding USA", "Worldwide Excluding USA")


def test_an_unreadable_area_is_not_matched_to_anything():
    assert area_of_cover_key("Refer to schedule") is None
    assert not equivalent_areas("Refer to schedule", "Worldwide Excluding USA")


# --- through the comparison itself --------------------------------------

def test_us_200_without_a_dollar_sign_is_still_two_hundred_dollars():
    # OCR returns "US 200" when the "$" renders as a glyph it cannot
    # read; without this the stated optical limit read as no limit.
    assert extract_amount_aed("US 200, Nil") == 200 * 3.6725


def test_a_richer_optical_limit_reads_as_improved_not_as_review():
    result = compare_benefit_value("US 200, Nil", "USD 300 Co-pay: 20%", "optical")
    assert result["direction"] == "improved"


def test_equivalence_only_ever_downgrades_review_never_invents_a_direction():
    # A genuine reduction must survive everything above it.
    assert compare_benefit_value("USD 3,000", "USD 1,000", "dental")["direction"] == "reduced"
    assert compare_benefit_value("Covered", "Not Covered", "dental")["direction"] == "review"
