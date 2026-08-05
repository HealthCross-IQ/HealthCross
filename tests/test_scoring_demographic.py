from app.scoring.rules.demographic import demographic_risk


def _employee(age, gender, marital_status="single"):
    return {"age": age, "gender": gender, "marital_status": marital_status, "relation": "employee"}


def test_married_female_18_to_40_is_higher_risk_than_single_female_same_age():
    married = [_employee(30, "F", "married")]
    single = [_employee(30, "F", "single")]

    assert demographic_risk(married)["score"] > demographic_risk(single)["score"]


def test_married_female_outside_18_to_40_does_not_get_maternity_loading():
    married_in_band = [_employee(30, "F", "married")]
    married_out_of_band = [_employee(55, "F", "married")]

    # out-of-band case should be lower purely from age banding, but verify the
    # maternity flag itself only applies inside 18-40 by comparing against a
    # same-age unmarried case at 55.
    single_out_of_band = [_employee(55, "F", "single")]
    assert demographic_risk(married_out_of_band)["score"] == demographic_risk(single_out_of_band)["score"]


def test_female_spouse_is_higher_risk_than_male_spouse():
    female_spouse = [{"age": 32, "gender": "F", "marital_status": "married", "relation": "spouse"}]
    male_spouse = [{"age": 32, "gender": "M", "marital_status": "married", "relation": "spouse"}]

    assert demographic_risk(female_spouse)["score"] > demographic_risk(male_spouse)["score"]


def test_male_employees_are_individually_favorable_vs_female_employees():
    male = [_employee(35, "M")]
    female = [_employee(35, "F")]

    assert demographic_risk(male)["score"] < demographic_risk(female)["score"]


def test_infant_child_is_higher_risk_than_toddler_child():
    infant = [{"age": 0, "gender": "F", "marital_status": "single", "relation": "child"}]
    toddler = [{"age": 5, "gender": "F", "marital_status": "single", "relation": "child"}]

    assert demographic_risk(infant)["score"] > demographic_risk(toddler)["score"]
    assert demographic_risk(infant)["infant_count"] == 1
    assert demographic_risk(toddler)["favorable_children_count"] == 1


def test_larger_male_heavy_employee_group_is_more_favorable():
    small_group = [_employee(35, "F"), _employee(35, "F")]
    large_male_group = [_employee(35, "M") for _ in range(200)]

    small_result = demographic_risk(small_group)
    large_result = demographic_risk(large_male_group)

    assert large_result["group_favorability_discount"] > small_result["group_favorability_discount"]
    assert large_result["score"] < small_result["score"]


def test_group_below_50_gets_small_group_loading_group_of_50_plus_does_not():
    small_group = [_employee(35, "M") for _ in range(10)]
    threshold_group = [_employee(35, "M") for _ in range(50)]

    small_result = demographic_risk(small_group)
    threshold_result = demographic_risk(threshold_group)

    assert small_result["small_group_loading"] > 0
    assert threshold_result["small_group_loading"] == 0


def test_smaller_group_has_higher_small_group_loading_than_a_bigger_small_group():
    tiny_group = [_employee(35, "M") for _ in range(5)]
    mid_small_group = [_employee(35, "M") for _ in range(30)]

    tiny_result = demographic_risk(tiny_group)
    mid_result = demographic_risk(mid_small_group)

    assert tiny_result["small_group_loading"] > mid_result["small_group_loading"]
    assert tiny_result["score"] > mid_result["score"]


def test_nationality_zone_multiplier_is_applied():
    baseline = [{"age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "nationality_zone": "zone_1_asia"}]
    loaded = demographic_risk(baseline, zone_multipliers={"zone_1_asia": 1.5})
    neutral = demographic_risk(baseline, zone_multipliers={"zone_1_asia": 1.0})

    assert loaded["score"] > neutral["score"]


def test_zone_maternity_multiplier_only_applies_to_maternity_risk_members():
    maternity_member = [
        {"age": 30, "gender": "F", "marital_status": "married", "relation": "employee", "nationality_zone": "zone_2_middle_east"}
    ]
    non_maternity_member = [
        {"age": 30, "gender": "M", "marital_status": "married", "relation": "employee", "nationality_zone": "zone_2_middle_east"}
    ]

    loaded_maternity = demographic_risk(maternity_member, zone_maternity_multipliers={"zone_2_middle_east": 1.5})
    neutral_maternity = demographic_risk(maternity_member, zone_maternity_multipliers={"zone_2_middle_east": 1.0})
    assert loaded_maternity["score"] > neutral_maternity["score"]

    loaded_non_maternity = demographic_risk(non_maternity_member, zone_maternity_multipliers={"zone_2_middle_east": 1.5})
    neutral_non_maternity = demographic_risk(non_maternity_member, zone_maternity_multipliers={"zone_2_middle_east": 1.0})
    assert loaded_non_maternity["score"] == neutral_non_maternity["score"]


def test_zone_network_multiplier_is_scaled_by_network_tier_score():
    member = [{"age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "nationality_zone": "zone_3_europe_americas"}]

    neutral = demographic_risk(member, zone_network_multipliers={"zone_3_europe_americas": 1.0}, network_tier_score=1.0)
    loaded_cheap_network = demographic_risk(
        member, zone_network_multipliers={"zone_3_europe_americas": 1.5}, network_tier_score=0.0
    )
    loaded_rich_network = demographic_risk(
        member, zone_network_multipliers={"zone_3_europe_americas": 1.5}, network_tier_score=1.0
    )

    # A network_tier_score of 0 (cheapest possible network) should fully mute
    # the zone-network interaction, leaving the score unchanged from neutral.
    assert loaded_cheap_network["score"] == neutral["score"]
    # A rich (score=1.0) network should fully apply the zone-network multiplier.
    assert loaded_rich_network["score"] > loaded_cheap_network["score"]


def test_a_none_multiplier_value_falls_back_to_neutral_instead_of_crashing():
    # Regression test: a real ScoringWeightSet row can have one of these
    # zone multiplier columns present but NULL - e.g. a column added after
    # that row already existed in a persistent SQLite DB (see
    # app/db_migrate.py) - so the caller-built dict can carry a real key
    # with a None value, not just a missing key. `.get(zone, 1.0)` alone
    # only covers a MISSING key; a present-but-None value used to reach
    # `network_zone_multiplier - 1` directly and crash with a TypeError.
    member = [
        {"age": 30, "gender": "F", "marital_status": "married", "relation": "employee", "nationality_zone": "zone_2_middle_east"}
    ]
    result = demographic_risk(
        member,
        zone_multipliers={"zone_2_middle_east": None},
        zone_maternity_multipliers={"zone_2_middle_east": None},
        zone_network_multipliers={"zone_2_middle_east": None},
    )
    neutral = demographic_risk(
        member,
        zone_multipliers={"zone_2_middle_east": 1.0},
        zone_maternity_multipliers={"zone_2_middle_east": 1.0},
        zone_network_multipliers={"zone_2_middle_east": 1.0},
    )
    assert result["score"] == neutral["score"]


def test_overage_loading_is_distinct_from_the_age_band_multiplier():
    # Two members with the same age-band multiplier (both fall in 41-59),
    # but only one is actually over the overage threshold (50) - the older
    # one should score higher despite sharing an age band, because the
    # overage loading is a separate signal layered on top.
    just_over_threshold = [_employee(51, "M")]
    under_threshold_same_band = [_employee(45, "M")]

    assert demographic_risk(just_over_threshold)["score"] > demographic_risk(under_threshold_same_band)["score"]


def test_overage_loading_scales_with_the_fraction_of_the_census_over_the_threshold():
    all_over = [_employee(55, "M"), _employee(60, "M")]
    half_over = [_employee(55, "M"), _employee(30, "M")]
    none_over = [_employee(30, "M"), _employee(35, "M")]

    all_over_result = demographic_risk(all_over)
    half_over_result = demographic_risk(half_over)
    none_over_result = demographic_risk(none_over)

    assert all_over_result["overage_fraction"] == 1.0
    assert half_over_result["overage_fraction"] == 0.5
    assert none_over_result["overage_fraction"] == 0.0
    assert all_over_result["overage_loading"] > half_over_result["overage_loading"] > none_over_result["overage_loading"] == 0.0


def test_overage_threshold_and_cap_are_caller_adjustable():
    member = [_employee(45, "M")]

    default_result = demographic_risk(member)
    assert default_result["overage_count"] == 0  # 45 is not over the default threshold of 50

    lowered_threshold_result = demographic_risk(member, overage_age_threshold=40)
    assert lowered_threshold_result["overage_count"] == 1
    assert lowered_threshold_result["score"] > default_result["score"]

    higher_cap_result = demographic_risk(member, overage_age_threshold=40, overage_loading_cap=0.5)
    assert higher_cap_result["score"] > lowered_threshold_result["score"]
