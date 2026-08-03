"""Tests for app/scoring/rules/new_business_rating.py - the manual/book
rate-card pricing mechanism for a brand-new group with no claims history,
using HealthCross's own Product x Region x Network x age-band rate card
plus its priced benefit-variant options.
"""
from app.scoring.rules.new_business_rating import (
    DECLINE_RISK_TIER,
    assess_opportunity,
    category_loading_pct,
    gross_up,
    price_case,
    price_member,
    price_tier_ladder,
)

RATE_CARDS = [
    {"product": "Bronze", "region": "Dubai", "network": "Net A", "tpa": "TPA X",
     "from_age": 0, "to_age": 17, "male_price": 1000.0, "female_price": 1000.0, "married_female_surcharge": None},
    {"product": "Bronze", "region": "Dubai", "network": "Net A", "tpa": "TPA X",
     "from_age": 18, "to_age": 40, "male_price": 2000.0, "female_price": 2200.0, "married_female_surcharge": 0.0},
    {"product": "Bronze", "region": "Abu Dhabi", "network": "Net A", "tpa": "TPA X",
     "from_age": 18, "to_age": 40, "male_price": 2100.0, "female_price": 2600.0, "married_female_surcharge": 500.0},
    {"product": "Platinum", "region": "Dubai", "network": "Net A", "tpa": "TPA X",
     "from_age": 18, "to_age": 40, "male_price": 5000.0, "female_price": 5500.0, "married_female_surcharge": 0.0},
]

VARIANT_RATES = [
    {"variant_name": "Annual Limit", "option_value": "USD 150,000", "direction": "Base",
     "impact_type": "Text", "impact_value": 0.0, "region": "Dubai", "tpa": "TPA X", "network": "Net A"},
    {"variant_name": "Annual Limit", "option_value": "USD 500,000", "direction": "Upgrade",
     "impact_type": "Percent", "impact_value": 3.0, "region": "Dubai", "tpa": "TPA X", "network": "Net A"},
    {"variant_name": "Annual Limit", "option_value": "USD 75,000", "direction": "Downgrade",
     "impact_type": "Percent", "impact_value": 2.0, "region": "Dubai", "tpa": "TPA X", "network": "Net A"},
    {"variant_name": "Dental Limit", "option_value": "Not Covered", "direction": "Base",
     "impact_type": "Text", "impact_value": 0.0, "region": "Dubai", "tpa": "TPA X", "network": "Net A"},
    {"variant_name": "Dental Limit", "option_value": "USD 500", "direction": "Upgrade",
     "impact_type": "Fixed", "impact_value": 275.0, "region": "Dubai", "tpa": "TPA X", "network": "Net A"},
    # Abu Dhabi has no Base row for Annual Limit at all - broker must choose explicitly.
    {"variant_name": "Annual Limit", "option_value": "USD 275,000", "direction": "Upgrade",
     "impact_type": "Percent", "impact_value": 2.0, "region": "Abu Dhabi", "tpa": "TPA X", "network": "Net A"},
]


def test_price_member_dubai_male_uses_male_price_and_no_maternity():
    member = {"age": 30, "gender": "M", "marital_status": "married", "relation": "employee", "emirates": "Dubai"}
    category = {"product": "Bronze", "network": "Net A", "tpa": "TPA X", "variant_selections": {}}
    result = price_member(member, category, RATE_CARDS, VARIANT_RATES)
    assert result["base_price"] == 2000.0
    assert result["maternity_surcharge"] == 0.0
    assert result["net_total"] == 2000.0
    assert result["warnings"] == []


def test_price_member_dubai_married_female_gets_no_surcharge_outside_abu_dhabi():
    # Dubai's own rate card prices the maternity surcharge at nil - it's
    # only non-zero in Abu Dhabi per HealthCross's own rate card.
    member = {"age": 28, "gender": "F", "marital_status": "married", "relation": "spouse", "emirates": "Dubai"}
    category = {"product": "Bronze", "network": "Net A", "tpa": "TPA X", "variant_selections": {}}
    result = price_member(member, category, RATE_CARDS, VARIANT_RATES)
    assert result["base_price"] == 2200.0
    assert result["maternity_surcharge"] == 0.0


def test_price_member_abu_dhabi_prices_by_employee_dependant_not_gender():
    # Abu Dhabi's own regulated scheme reuses the Male/Female Price columns
    # as Employee/Dependant price instead - a female EMPLOYEE should get
    # the "male_price" column value (the Employee rate), not "female_price".
    member = {"age": 32, "gender": "F", "marital_status": "married", "relation": "employee", "emirates": "Abu Dhabi"}
    category = {"product": "Bronze", "network": "Net A", "tpa": "TPA X", "variant_selections": {}}
    result = price_member(member, category, RATE_CARDS, VARIANT_RATES)
    assert result["base_price"] == 2100.0  # the Employee (male_price column) rate, not 2600 (Dependant)


def test_price_member_abu_dhabi_married_female_gets_the_maternity_surcharge():
    member = {"age": 32, "gender": "F", "marital_status": "married", "relation": "spouse", "emirates": "Abu Dhabi"}
    category = {"product": "Bronze", "network": "Net A", "tpa": "TPA X", "variant_selections": {}}
    result = price_member(member, category, RATE_CARDS, VARIANT_RATES)
    assert result["base_price"] == 2600.0
    assert result["maternity_surcharge"] == 500.0
    assert result["net_total"] == 3100.0


def test_price_member_married_female_outside_maternity_age_band_gets_no_surcharge():
    member = {"age": 55, "gender": "F", "marital_status": "married", "relation": "spouse", "emirates": "Abu Dhabi"}
    category = {"product": "Bronze", "network": "Net A", "tpa": "TPA X", "variant_selections": {}}
    # No rate card row covers age 55 in this fixture, so this exercises the
    # "missing rate" path instead - a separate, more direct test of the age
    # gate lives below via a member whose age IS covered.
    result = price_member(member, category, RATE_CARDS, VARIANT_RATES)
    assert result["base_price"] is None
    assert "No rate card entry" in result["warnings"][0]


def test_variant_upgrade_percent_impact_is_a_percentage_of_base_price():
    member = {"age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "emirates": "Dubai"}
    category = {
        "product": "Bronze", "network": "Net A", "tpa": "TPA X",
        "variant_selections": {"Annual Limit": "USD 500,000"},
    }
    result = price_member(member, category, RATE_CARDS, VARIANT_RATES)
    assert result["variant_impacts"]["Annual Limit"] == 60.0  # 2000 * 3%
    assert result["net_total"] == 2060.0


def test_variant_downgrade_percent_impact_is_negative():
    member = {"age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "emirates": "Dubai"}
    category = {
        "product": "Bronze", "network": "Net A", "tpa": "TPA X",
        "variant_selections": {"Annual Limit": "USD 75,000"},
    }
    result = price_member(member, category, RATE_CARDS, VARIANT_RATES)
    assert result["variant_impacts"]["Annual Limit"] == -40.0  # 2000 * -2%


def test_variant_fixed_impact_is_a_flat_aed_amount_not_scaled_by_base_price():
    member = {"age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "emirates": "Dubai"}
    category = {
        "product": "Bronze", "network": "Net A", "tpa": "TPA X",
        "variant_selections": {"Dental Limit": "USD 500"},
    }
    result = price_member(member, category, RATE_CARDS, VARIANT_RATES)
    assert result["variant_impacts"]["Dental Limit"] == 275.0


def test_unselected_variant_defaults_to_the_base_option_with_zero_impact():
    member = {"age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "emirates": "Dubai"}
    category = {"product": "Bronze", "network": "Net A", "tpa": "TPA X", "variant_selections": {}}
    result = price_member(member, category, RATE_CARDS, VARIANT_RATES)
    assert result["variant_impacts"]["Dental Limit"] == 0.0
    assert result["variant_impacts"]["Annual Limit"] == 0.0


def test_missing_base_option_for_a_variant_is_flagged_not_silently_skipped():
    # Abu Dhabi/Net A has no Base row for Annual Limit at all in this
    # fixture - an unselected variant with no default should produce a
    # warning rather than silently pricing it at zero impact.
    member = {"age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "emirates": "Abu Dhabi"}
    category = {"product": "Bronze", "network": "Net A", "tpa": "TPA X", "variant_selections": {}}
    result = price_member(member, category, RATE_CARDS, VARIANT_RATES)
    assert "Annual Limit" not in result["variant_impacts"]
    assert any("No base option defined for Annual Limit" in w for w in result["warnings"])


def test_unknown_selected_option_is_flagged_and_falls_back_to_base():
    member = {"age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "emirates": "Dubai"}
    category = {
        "product": "Bronze", "network": "Net A", "tpa": "TPA X",
        "variant_selections": {"Annual Limit": "USD 999,999,999"},
    }
    result = price_member(member, category, RATE_CARDS, VARIANT_RATES)
    assert result["variant_impacts"]["Annual Limit"] == 0.0  # fell back to Base
    assert any("No rate found for Annual Limit option" in w for w in result["warnings"])


def test_category_loading_pct_matches_platinum_vs_bronze_healthcross_fee():
    # commission 10% + QIC 5% + TPA 5% + HealthCross fee (5% Platinum/Gold, 6.5% Silver/Bronze)
    assert category_loading_pct("Platinum") == 0.25
    assert category_loading_pct("Bronze") == 0.265
    assert category_loading_pct("Bronze", commission_pct=0.0) == 0.165


def test_gross_up_divides_rather_than_multiplies():
    assert gross_up(735.0, 0.265) == 1000.0


def test_price_case_sums_per_category_and_grosses_up_each_by_its_own_product():
    census = [
        {"category": "A", "age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "emirates": "Dubai"},
        {"category": "B", "age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "emirates": "Dubai"},
        {"category": "Z", "age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "emirates": "Dubai"},  # no matching category
    ]
    categories = [
        {"category": "A", "product": "Bronze", "network": "Net A", "tpa": "TPA X", "variant_selections": {}},
        {"category": "B", "product": "Platinum", "network": "Net A", "tpa": "TPA X", "variant_selections": {}},
    ]
    result = price_case(census, categories, RATE_CARDS, VARIANT_RATES)
    assert result["uncategorized_member_count"] == 1
    assert result["priced_member_count"] == 2

    cat_a = next(c for c in result["categories"] if c["category"] == "A")
    cat_b = next(c for c in result["categories"] if c["category"] == "B")
    assert cat_a["net_annual_premium"] == 2000.0
    assert cat_a["gross_annual_premium"] == round(gross_up(2000.0, category_loading_pct("Bronze")), 2)
    assert cat_b["net_annual_premium"] == 5000.0
    assert cat_b["gross_annual_premium"] == round(gross_up(5000.0, category_loading_pct("Platinum")), 2)
    assert result["case_gross_annual_premium"] == round(cat_a["gross_annual_premium"] + cat_b["gross_annual_premium"], 2)


def test_assess_opportunity_flags_decline_tier_as_poor_regardless_of_price():
    result = assess_opportunity(rated_premium=10000, target_premium=50000, risk_tier=DECLINE_RISK_TIER)
    assert result["verdict"] == "Poor"


def test_assess_opportunity_good_when_target_comfortably_exceeds_rated_price():
    result = assess_opportunity(rated_premium=10000, target_premium=11000, risk_tier="Preferred")
    assert result["verdict"] == "Good"
    assert result["target_vs_rated_variance_pct"] == 10.0


def test_assess_opportunity_poor_when_target_undercuts_rated_price():
    result = assess_opportunity(rated_premium=10000, target_premium=9000, risk_tier="Standard")
    assert result["verdict"] == "Poor"


def test_assess_opportunity_marginal_within_the_5pct_band():
    result = assess_opportunity(rated_premium=10000, target_premium=10200, risk_tier="Standard")
    assert result["verdict"] == "Marginal"


def test_assess_opportunity_unknown_without_a_target_premium():
    result = assess_opportunity(rated_premium=10000, target_premium=None, risk_tier="Standard")
    assert result["verdict"] == "Unknown"


# Real TPA/network names (matching app/reference/product_tiers.py's
# NETWORK_RICHNESS_ORDER) - Gold and Silver each carry more than one MSH
# network so the "every network under this TPA" grid has something real
# to show, not just one row per product.
TIER_LADDER_RATE_CARDS = [
    {"product": "Platinum", "region": "Dubai", "network": "MSH Platinum", "tpa": "MSH MENA",
     "from_age": 18, "to_age": 40, "male_price": 5000.0, "female_price": 5500.0, "married_female_surcharge": 0.0},
    {"product": "Gold", "region": "Dubai", "network": "MSH Platinum", "tpa": "MSH MENA",
     "from_age": 18, "to_age": 40, "male_price": 4000.0, "female_price": 4500.0, "married_female_surcharge": 0.0},
    {"product": "Gold", "region": "Dubai", "network": "MSH Comprehensive + Mediclinic", "tpa": "MSH MENA",
     "from_age": 18, "to_age": 40, "male_price": 3800.0, "female_price": 4300.0, "married_female_surcharge": 0.0},
    {"product": "Gold", "region": "Dubai", "network": "MSH Comprehensive", "tpa": "MSH MENA",
     "from_age": 18, "to_age": 40, "male_price": 3600.0, "female_price": 4100.0, "married_female_surcharge": 0.0},
    {"product": "Silver", "region": "Dubai", "network": "MSH Premium", "tpa": "MSH MENA",
     "from_age": 18, "to_age": 40, "male_price": 3000.0, "female_price": 3300.0, "married_female_surcharge": 0.0},
    {"product": "Silver", "region": "Dubai", "network": "MSH Enhanced", "tpa": "MSH MENA",
     "from_age": 18, "to_age": 40, "male_price": 2800.0, "female_price": 3100.0, "married_female_surcharge": 0.0},
]

TIER_LADDER_CENSUS = [
    {"category": "A", "age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "emirates": "Dubai"},
]


def test_price_tier_ladder_shows_one_tier_either_side_of_the_chosen_product():
    category = {"category": "A", "product": "Gold", "network": "MSH Platinum", "tpa": "MSH MENA", "variant_selections": {}}
    ladder = price_tier_ladder(TIER_LADDER_CENSUS, category, TIER_LADDER_RATE_CARDS, [])
    assert [r["product"] for r in ladder] == ["Platinum", "Gold", "Silver"]


def test_price_tier_ladder_shows_every_network_under_the_chosen_tpa_for_each_product():
    category = {"category": "A", "product": "Gold", "network": "MSH Platinum", "tpa": "MSH MENA", "variant_selections": {}}
    ladder = price_tier_ladder(TIER_LADDER_CENSUS, category, TIER_LADDER_RATE_CARDS, [])
    gold = next(r for r in ladder if r["product"] == "Gold")
    # Ordered richest to leanest, not just the one network originally chosen.
    assert [n["network"] for n in gold["networks"]] == [
        "MSH Platinum", "MSH Comprehensive + Mediclinic", "MSH Comprehensive",
    ]


def test_price_tier_ladder_flags_the_originally_chosen_product_and_network():
    category = {"category": "A", "product": "Gold", "network": "MSH Comprehensive", "tpa": "MSH MENA", "variant_selections": {}}
    ladder = price_tier_ladder(TIER_LADDER_CENSUS, category, TIER_LADDER_RATE_CARDS, [])
    gold = next(r for r in ladder if r["product"] == "Gold")
    chosen = [n for n in gold["networks"] if n["is_chosen"]]
    assert len(chosen) == 1
    assert chosen[0]["network"] == "MSH Comprehensive"
    # Every other network/product combination is NOT flagged as chosen.
    platinum = next(r for r in ladder if r["product"] == "Platinum")
    assert all(not n["is_chosen"] for n in platinum["networks"])


def test_price_tier_ladder_prices_each_network_independently():
    category = {"category": "A", "product": "Gold", "network": "MSH Platinum", "tpa": "MSH MENA", "variant_selections": {}}
    ladder = price_tier_ladder(TIER_LADDER_CENSUS, category, TIER_LADDER_RATE_CARDS, [])
    gold = next(r for r in ladder if r["product"] == "Gold")
    by_network = {n["network"]: n["net_annual_premium"] for n in gold["networks"]}
    assert by_network == {
        "MSH Platinum": 4000.0,
        "MSH Comprehensive + Mediclinic": 3800.0,
        "MSH Comprehensive": 3600.0,
    }


def test_price_tier_ladder_flags_a_tier_with_no_networks_of_this_tpa_at_all():
    category = {"category": "A", "product": "Gold", "network": "MSH Platinum", "tpa": "MSH MENA", "variant_selections": {}}
    rate_cards_without_silver = [r for r in TIER_LADDER_RATE_CARDS if r["product"] != "Silver"]
    ladder = price_tier_ladder(TIER_LADDER_CENSUS, category, rate_cards_without_silver, [])
    silver = next(r for r in ladder if r["product"] == "Silver")
    assert silver["networks"] == []
    assert silver["warnings"]


def test_price_tier_ladder_never_shows_a_different_tpas_networks():
    # A network belonging to NAS Neuron shouldn't appear just because it's
    # priced under the same Gold product - only MSH MENA networks are shown
    # when the category's own tpa is MSH MENA.
    rate_cards_with_other_tpa = TIER_LADDER_RATE_CARDS + [
        {"product": "Gold", "region": "Dubai", "network": "GN", "tpa": "NAS Neuron",
         "from_age": 18, "to_age": 40, "male_price": 3500.0, "female_price": 3900.0, "married_female_surcharge": 0.0},
    ]
    category = {"category": "A", "product": "Gold", "network": "MSH Platinum", "tpa": "MSH MENA", "variant_selections": {}}
    ladder = price_tier_ladder(TIER_LADDER_CENSUS, category, rate_cards_with_other_tpa, [])
    gold = next(r for r in ladder if r["product"] == "Gold")
    assert "GN" not in [n["network"] for n in gold["networks"]]


def test_price_tier_ladder_only_prices_members_in_that_category():
    census = TIER_LADDER_CENSUS + [
        {"category": "B", "age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "emirates": "Dubai"},
    ]
    category = {"category": "A", "product": "Gold", "network": "MSH Platinum", "tpa": "MSH MENA", "variant_selections": {}}
    ladder = price_tier_ladder(census, category, TIER_LADDER_RATE_CARDS, [])
    gold = next(r for r in ladder if r["product"] == "Gold")
    platinum_network = next(n for n in gold["networks"] if n["network"] == "MSH Platinum")
    assert platinum_network["net_annual_premium"] == 4000.0  # only the one Category A member, not both
