"""Credibility-blended burning cost cube -
app/scoring/rules/burning_cost_cube.py.
"""
import pytest

from app.scoring.rules.burning_cost_cube import (
    DEFAULT_CUBE_DIMENSIONS,
    UNMAPPED,
    age_band_label,
    build_cube_index,
    burning_cost_cube,
    expected_cost_for_census,
    expected_cost_for_member,
    member_cube_key,
)

RATE_CARDS = [
    {"product_name": "Gold", "from_age": 0, "to_age": 17, "male_price": 1000, "female_price": 1000},
    {"product_name": "Gold", "from_age": 18, "to_age": 45, "male_price": 2000, "female_price": 2500},
    {"product_name": "Gold", "from_age": 46, "to_age": 65, "male_price": 4000, "female_price": 4200},
]


def _r(claims, exposure=1.0, product="Gold", network="GN", age=30, gender="M",
       relation="employee", zone="Zone 3"):
    return {
        "in_scope": True,
        "product": product,
        "network": network,
        "age": age,
        "gender": gender,
        "relation": relation,
        "nationality_zone": zone,
        "actual_claims": claims,
        "earned_premium_fraction": exposure,
    }


def test_age_band_labels_come_from_the_rate_card_not_invented_bands():
    bands = [(0, 17), (18, 45), (46, 65)]
    assert age_band_label(30, bands) == "18-45"
    assert age_band_label(0, bands) == "0-17"
    assert age_band_label(99, bands) == UNMAPPED
    assert age_band_label(None, bands) == UNMAPPED


def test_a_member_with_no_value_for_a_dimension_is_unmapped_not_dropped():
    key = member_cube_key(_r(100, zone=None), DEFAULT_CUBE_DIMENSIONS, [(18, 45)])
    assert key[-1] == UNMAPPED


def test_book_level_burning_cost_is_claims_over_member_years():
    # Exposure, not headcount: a member on risk for a quarter of the year
    # contributes a quarter of a member-year.
    cube = burning_cost_cube([_r(1000, exposure=1.0), _r(0, exposure=0.25)], RATE_CARDS)
    assert cube["book"]["earned_member_years"] == 1.25
    assert cube["book"]["burning_cost"] == pytest.approx(800.0)


def test_out_of_scope_members_are_excluded_entirely():
    out = _r(99999.0)
    out["in_scope"] = False
    cube = burning_cost_cube([_r(1000), out], RATE_CARDS)
    assert cube["book"]["member_count"] == 1
    assert cube["book"]["actual_claims"] == 1000.0


def test_a_thin_cell_is_pulled_toward_its_parent_not_left_at_its_raw_rate():
    # One member with a freak claim must not price at that claim. With
    # 1 member-year against a 100 member-year standard, credibility is
    # sqrt(1/100) = 0.1, so the cell keeps a tenth of its own experience.
    members = [_r(1000.0, zone="Zone 3") for _ in range(60)]
    members.append(_r(100_000.0, zone="Zone 1"))
    cube = burning_cost_cube(members, RATE_CARDS)

    leaf = [c for c in cube["cells"]
            if c["level"] == len(DEFAULT_CUBE_DIMENSIONS) and c["key"]["nationality_zone"] == "Zone 1"][0]
    assert leaf["own_rate"] == 100_000.0
    assert leaf["credibility"] == pytest.approx(0.1, abs=1e-3)
    # Blended well below its own raw rate, and above its parent's.
    assert leaf["expected_cost"] < 20_000.0
    assert leaf["expected_cost"] > leaf["complement_rate"]


def test_a_cell_with_full_exposure_prices_on_its_own_experience():
    members = [_r(3000.0, zone="Zone 3") for _ in range(150)]
    cube = burning_cost_cube(members, RATE_CARDS)
    leaf = [c for c in cube["cells"] if c["level"] == len(DEFAULT_CUBE_DIMENSIONS)][0]
    assert leaf["credibility"] == 1.0
    assert leaf["expected_cost"] == pytest.approx(3000.0)


def test_every_level_of_the_hierarchy_is_present():
    cube = burning_cost_cube([_r(1000)], RATE_CARDS)
    assert [lvl["level"] for lvl in cube["levels"]] == list(range(1, len(DEFAULT_CUBE_DIMENSIONS) + 1))
    assert cube["levels"][0]["dimensions"] == ["product"]
    assert cube["dimensions"] == list(DEFAULT_CUBE_DIMENSIONS)


def test_a_member_matching_a_populated_cell_prices_off_it_without_falling_back():
    members = [_r(2500.0) for _ in range(120)]
    cube = burning_cost_cube(members, RATE_CARDS)
    priced = expected_cost_for_member(_r(0), cube)
    assert priced["matched_level"] == len(DEFAULT_CUBE_DIMENSIONS)
    assert priced["fell_back"] is False
    assert priced["expected_cost"] == pytest.approx(2500.0)


def test_a_member_in_an_unseen_corner_falls_back_up_the_hierarchy():
    # Nobody in the book shares this member's nationality zone, so the
    # price comes from the next level up rather than from nothing.
    members = [_r(2500.0, zone="Zone 3") for _ in range(120)]
    cube = burning_cost_cube(members, RATE_CARDS)
    stranger = _r(0, zone="Zone 9")
    priced = expected_cost_for_member(stranger, cube)
    assert priced["fell_back"] is True
    assert priced["matched_level"] == len(DEFAULT_CUBE_DIMENSIONS) - 1
    assert priced["expected_cost"] is not None


def test_a_member_matching_nothing_at_all_falls_back_to_the_book():
    cube = burning_cost_cube([_r(2500.0) for _ in range(10)], RATE_CARDS)
    alien = _r(0, product="Platinum", network="Other")
    priced = expected_cost_for_member(alien, cube)
    assert priced["matched_level"] == 0
    assert priced["expected_cost"] == cube["book"]["burning_cost"]


def test_census_pricing_totals_the_members_and_reports_its_own_fallbacks():
    members = [_r(2000.0) for _ in range(120)]
    cube = burning_cost_cube(members, RATE_CARDS)
    census = [_r(0), _r(0), _r(0, zone="Zone 9")]
    result = expected_cost_for_census(census, cube)

    assert result["member_count"] == 3
    assert result["rated_member_count"] == 3
    assert result["fallback_member_count"] == 1
    assert result["expected_annual_claims"] == pytest.approx(6000.0, rel=1e-3)
    assert result["average_expected_cost"] == pytest.approx(2000.0, rel=1e-3)


def test_an_empty_book_prices_nothing_rather_than_raising():
    cube = burning_cost_cube([], RATE_CARDS)
    assert cube["book"]["burning_cost"] is None
    assert cube["cells"] == []
    priced = expected_cost_for_member(_r(0), cube)
    assert priced["expected_cost"] is None


def test_the_index_finds_the_same_cells_the_lookup_walks():
    cube = burning_cost_cube([_r(2000.0) for _ in range(20)], RATE_CARDS)
    index = build_cube_index(cube)
    assert len(index) == len(cube["cells"])
    with_index = expected_cost_for_member(_r(0), cube, index)
    without = expected_cost_for_member(_r(0), cube)
    assert with_index == without


def test_ibnr_is_part_of_the_cost_a_cell_is_priced_at():
    # Paid 1000 + IBNR 200 per member: the cell costs 1200, not 1000. A
    # price built from paid claims alone is short by the whole IBNR.
    members = [dict(_r(1000.0), ibnr=200.0) for _ in range(150)]
    cube = burning_cost_cube(members, RATE_CARDS)
    assert cube["book"]["burning_cost"] == pytest.approx(1200.0)


def test_a_blended_cell_is_held_within_the_relativity_band_of_its_parent():
    # 20 member-years at 50,000 against a 2,000 parent: credibility
    # sqrt(20/100)=0.45 alone would still price the cell near 23,000 -
    # more than 11x its parent. The cap holds it at 2x and says so.
    members = [_r(2000.0, zone="Zone 3") for _ in range(200)]
    members += [_r(50_000.0, zone="Zone 1") for _ in range(20)]
    cube = burning_cost_cube(members, RATE_CARDS)
    leaf = [c for c in cube["cells"]
            if c["level"] == len(DEFAULT_CUBE_DIMENSIONS) and c["key"]["nationality_zone"] == "Zone 1"][0]
    assert leaf["capped"] is True
    assert leaf["expected_cost"] == pytest.approx(2 * leaf["complement_rate"], rel=1e-3)


def test_a_large_claim_cap_pools_the_excess_across_the_whole_book():
    # One 210,000 claim in a 100-member book, capped at 100,000: the cell
    # it landed in sees 100,000; the other 110,000 is spread over every
    # member-year as a flat load, so every cell pays 1,100 for it.
    members = [_r(1000.0, zone="Zone 3") for _ in range(99)]
    members.append(_r(210_000.0, zone="Zone 1"))
    cube = burning_cost_cube(members, RATE_CARDS, large_claim_cap=100_000.0)
    assert cube["pooled_excess"] == pytest.approx(110_000.0)
    assert cube["capped_member_count"] == 1
    assert cube["pooled_load_per_member_year"] == pytest.approx(1100.0)
    zone3 = [c for c in cube["cells"]
             if c["level"] == len(DEFAULT_CUBE_DIMENSIONS) and c["key"]["nationality_zone"] == "Zone 3"][0]
    assert zone3["own_rate"] == pytest.approx(1000.0 + 1100.0)
    # Without the cap the book cost is identical - the cap moves cost
    # between cells, it never removes any.
    uncapped = burning_cost_cube(members, RATE_CARDS)
    assert cube["book"]["burning_cost"] == pytest.approx(uncapped["book"]["burning_cost"])


def test_review_age_bands_override_the_rate_card_bands():
    cube = burning_cost_cube([_r(1000.0, age=1), _r(1000.0, age=10)], RATE_CARDS, age_bands=[(0, 1), (2, 17)])
    assert cube["age_bands"] == [[0, 1], [2, 17]]
    bands = {c["key"]["age_band"] for c in cube["cells"] if c["level"] == 3}
    assert bands == {"0-1", "2-17"}
