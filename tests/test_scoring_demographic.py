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


def test_nationality_zone_multiplier_is_applied():
    baseline = [{"age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "nationality_zone": "zone_1_asia"}]
    loaded = demographic_risk(baseline, zone_multipliers={"zone_1_asia": 1.5})
    neutral = demographic_risk(baseline, zone_multipliers={"zone_1_asia": 1.0})

    assert loaded["score"] > neutral["score"]
