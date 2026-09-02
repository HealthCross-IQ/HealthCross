"""A census writes the emirate as a three-letter code as often as in full.

Only the full names were mapped, and the fallback is Dubai, so every code
except NE resolved to Dubai: AUH priced an Abu Dhabi member on the DUBAI
card, and SHJ and AJM priced Northern Emirates members on it too. DXB
looked correct and was not - it was the fallback, not a match, so it
would have kept looking correct however the mapping drifted.

Abu Dhabi is the expensive one to get wrong: it has its own regulated
rate card, and it is the only region where the married-female maternity
surcharge is priced above nil.
"""
import pytest

from app.reference.emirate_regions import (
    REGION_ABU_DHABI,
    REGION_DUBAI,
    REGION_NORTHERN_EMIRATES,
    region_for_emirate,
)


@pytest.mark.parametrize("value", ["AUH", "auh", " AUH ", "AD", "Abu Dhabi", "abu dhabi"])
def test_abu_dhabi_is_read_as_abu_dhabi(value):
    assert region_for_emirate(value) == REGION_ABU_DHABI


@pytest.mark.parametrize("value", ["DXB", "dxb", "Dubai", "dubai"])
def test_dubai_is_read_as_dubai(value):
    assert region_for_emirate(value) == REGION_DUBAI


@pytest.mark.parametrize("value", [
    "NE", "ne", "Northern Emirates",
    "SHJ", "Sharjah", "AJM", "Ajman",
    "RAK", "Ras Al Khaimah", "FUJ", "Fujairah", "UAQ", "Umm Al Quwain",
    "Al Ain", "AAN",
])
def test_the_northern_emirates_are_read_as_one_tier(value):
    assert region_for_emirate(value) == REGION_NORTHERN_EMIRATES


@pytest.mark.parametrize("value", ["", None, "   "])
def test_a_missing_emirate_still_prices(value):
    # A single blank must never block pricing a whole census.
    assert region_for_emirate(value) == REGION_DUBAI


def test_an_abu_dhabi_member_is_priced_on_the_abu_dhabi_card():
    # The consequence, not just the mapping: Abu Dhabi rates by
    # membership role rather than gender, and carries the maternity
    # surcharge. Reading AUH as Dubai gets both wrong.
    from app.scoring.rules.new_business_rating import price_member

    card = [{"product": "Gold", "region": REGION_ABU_DHABI, "network": "N",
             "from_age": 18, "to_age": 40, "male_price": 5000.0,
             "female_price": 6000.0, "married_female_surcharge": 2000.0}]
    category = {"product": "Gold", "network": "N", "tpa": "T", "variant_selections": {}}
    member = {"age": 30, "gender": "F", "marital_status": "married",
              "relation": "spouse", "emirates": "AUH"}

    priced = price_member(member, category, card, [])
    assert priced["net_total"] is not None, priced["warnings"]
    assert priced["maternity_surcharge"] == 2000.0
