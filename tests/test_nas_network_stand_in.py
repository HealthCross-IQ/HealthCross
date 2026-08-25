"""A network that misses its mapping is priced off the wrong book -
NAS_TO_MSH_NETWORK and nas_tpa_factor in portfolio_analysis.py.
"""
import pytest

from app.scoring.rules.burning_cost_cube import expected_cost_for_member
from app.scoring.rules.portfolio_analysis import (
    NAS_VS_MSH_BURNING_COST,
    NAS_VS_MSH_BURNING_COST_RANGE,
    _burning_cost_lookup_network,
    is_nas_stand_in,
    nas_tpa_factor,
)


# --- the mapping has to survive how people actually type ----------------

@pytest.mark.parametrize("written", [
    "Super Restricted + Zulekha Group",   # what the portal actually shows
    "super restricted+ zulaikha",         # the only spelling that used to work
    "Super Restricted + Zulaikha",
    "SUPER RESTRICTED +  ZULEKHA  GROUP",
    "Super Restricted",
])
def test_every_way_this_network_is_written_finds_the_same_book(written):
    # A miss here is silent and expensive: the network matches nothing,
    # every member falls back past the network dimension, and a
    # restricted network gets priced off the whole product - rich
    # networks included. That is how 36 members came to be quoted at
    # over AED 8,700 a head.
    assert _burning_cost_lookup_network(written) == "MSH Regular"


@pytest.mark.parametrize("written,expected", [
    ("GN", "MSH Comprehensive"),
    ("GN excluding Mediclinic and American", "MSH Comprehensive"),
    ("GN Excluding American & Mediclinic Group", "MSH Comprehensive"),
    ("Comprehensive", "MSH Platinum"),
    ("Restricted", "MSH Enhanced"),
])
def test_the_other_nas_networks_still_map(written, expected):
    assert _burning_cost_lookup_network(written) == expected


@pytest.mark.parametrize("written", ["Restricted +++", "restricted+++", "Restricted+++"])
def test_trailing_pluses_are_a_tier_not_punctuation(written):
    # "Restricted +++" sits ABOVE plain "Restricted". Normalising the
    # pluses away priced the richer tier off the cheaper network.
    assert _burning_cost_lookup_network(written) == "MSH Premium"
    assert _burning_cost_lookup_network("Restricted") == "MSH Enhanced"


def test_an_msh_network_passes_through_untouched():
    assert _burning_cost_lookup_network("MSH Platinum") == "MSH Platinum"
    assert _burning_cost_lookup_network("MSH Regular") == "MSH Regular"


def test_an_unknown_network_is_left_alone_rather_than_guessed_at():
    assert _burning_cost_lookup_network("Some New Network") == "Some New Network"
    assert _burning_cost_lookup_network(None) is None


# --- the TPA differential ------------------------------------------------

def test_a_nas_network_is_not_priced_at_an_msh_price():
    # The book has no NAS experience, so a NAS category reads MSH's -
    # but NAS is one of the largest TPAs in the UAE by volume and does
    # not cost the same at the same network richness.
    assert nas_tpa_factor("Super Restricted + Zulekha Group") == NAS_VS_MSH_BURNING_COST
    assert nas_tpa_factor("Comprehensive") == NAS_VS_MSH_BURNING_COST
    assert is_nas_stand_in("GN") is True


def test_an_msh_network_priced_off_its_own_book_is_not_adjusted():
    assert nas_tpa_factor("MSH Platinum") == 1.0
    assert nas_tpa_factor(None) == 1.0
    assert is_nas_stand_in("MSH Regular") is False


def test_the_adjustment_sits_inside_the_range_underwriting_gave():
    low, high = NAS_VS_MSH_BURNING_COST_RANGE
    assert low <= NAS_VS_MSH_BURNING_COST <= high
    assert NAS_VS_MSH_BURNING_COST < 1.0, "NAS is cheaper than MSH, not dearer"


# --- the factor reaches the price ---------------------------------------

def _cube():
    return {
        "dimensions": ["product", "network"],
        "age_bands": [],
        "book": {"burning_cost": 5000.0, "earned_member_years": 100.0},
        "cells": [
            {"level": 2, "key_path": ["Bronze", "MSH Regular"], "key": {}, "expected_cost": 4000.0,
             "credibility": 1.0, "own_rate": 4000.0, "earned_member_years": 50.0},
        ],
    }


def test_a_member_carrying_a_cost_factor_is_priced_below_the_cell_they_read():
    plain = expected_cost_for_member({"product": "Bronze", "network": "MSH Regular"}, _cube())
    adjusted = expected_cost_for_member(
        {"product": "Bronze", "network": "MSH Regular", "cost_factor": 0.875}, _cube()
    )
    assert plain["expected_cost"] == 4000.0
    assert adjusted["expected_cost"] == 3500.0
    assert adjusted["cost_factor"] == 0.875


def test_no_cost_factor_means_no_adjustment():
    priced = expected_cost_for_member({"product": "Bronze", "network": "MSH Regular"}, _cube())
    assert priced["cost_factor"] == 1.0


def test_the_factor_applies_to_the_book_fallback_too():
    # A member in an entirely unpopulated corner falls back to the book
    # rate. That is still an MSH figure standing in for a NAS network.
    priced = expected_cost_for_member(
        {"product": "Nothing", "network": "Nowhere", "cost_factor": 0.875}, _cube()
    )
    assert priced["matched_level"] == 0
    assert priced["expected_cost"] == pytest.approx(4375.0)
