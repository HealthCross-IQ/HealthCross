"""Is this opportunity worth writing, and at what price -
app/scoring/rules/opportunity_risk.py.

The rates in these fixtures are the shape of the real book (measured Aug
2026 over 3,762 lives and 73,885 claims): a newborn at roughly 2.6x any
later childhood year, a female spouse of maternity age delivering at
roughly 4x the rate of a female employee of the same age.
"""
from app.scoring.rules.opportunity_risk import (
    ASSUMPTIONS,
    TREATMENT_ALREADY_PRICED,
    TREATMENT_LOAD,
    TREATMENT_WIDEN_MARGIN,
    VERDICT_AGGRESSIVE,
    VERDICT_DECLINE,
    VERDICT_NEEDS_LOADING,
    VERDICT_PRICED_RIGHT,
    VERDICT_UNKNOWN,
    assess_opportunity,
    benefit_buy_up_finding,
    book_benchmarks,
    child_age_finding,
    child_cost_curve,
    credibility_finding,
    data_quality_finding,
    group_size_finding,
    maternity_finding,
    maternity_rates,
    newborn_pipeline_finding,
    pre_existing_finding,
    verdict,
)


def _book_member(beneficiary_id, relation, age, gender, claims, years=1.0):
    return {
        "beneficiary_id": beneficiary_id,
        "relation": relation,
        "age": age,
        "gender": gender,
        "actual_claims": claims,
        "earned_premium_fraction": years,
    }


def _book(children_per_band=30, newborn_rate=7278, other_child_rate=2750):
    """A book shaped like the real one: newborns expensive, every later
    childhood age flat.
    """
    members = []
    n = 0
    for age, rate in ((0, newborn_rate), (2, other_child_rate), (7, other_child_rate), (14, other_child_rate)):
        for _ in range(children_per_band):
            n += 1
            members.append(_book_member(f"C{n}", "Child", age, "M", rate))
    for i in range(60):
        members.append(_book_member(f"E{i}", "Employee", 35, "M", 6459))
    return members


# --- the child cost curve -----------------------------------------------

def test_the_curve_splits_the_band_the_rate_card_cannot():
    curve = {r["band"]: r for r in child_cost_curve(_book())}
    assert curve["0 (newborn)"]["claims_per_member_year"] == 7278
    assert curve["1-4"]["claims_per_member_year"] == 2750
    assert curve["10-17"]["claims_per_member_year"] == 2750


def test_a_child_age_band_with_almost_no_exposure_is_not_credible():
    curve = {r["band"]: r for r in child_cost_curve(_book(children_per_band=2))}
    assert curve["0 (newborn)"]["credible"] is False


def test_only_children_are_counted_in_the_child_curve():
    members = _book() + [_book_member("S1", "Spouse", 30, "F", 99_999)]
    total = sum(r["member_years"] for r in child_cost_curve(members))
    assert total == 120  # the four child bands, 30 each - no spouse


# --- maternity, frequency times severity --------------------------------

def _maternity_book():
    members = []
    maternity = {}
    # 40 female spouses of maternity age, 8 of whom delivered (20%)
    for i in range(40):
        members.append(_book_member(f"S{i}", "Spouse", 30, "F", 8302))
        maternity[f"S{i}"] = 13_382 if i < 8 else 0.0
    # 40 female employees of the same age, 2 of whom delivered (5%)
    for i in range(40):
        members.append(_book_member(f"E{i}", "Employee", 30, "F", 5119))
        maternity[f"E{i}"] = 22_108 if i < 2 else 0.0
    return members, maternity


def test_maternity_is_split_by_relation_because_the_book_says_they_differ():
    # A female spouse is enrolled at a very different point in her life
    # from a female employee - blending them hides that entirely.
    members, maternity = _maternity_book()
    rates = maternity_rates(members, maternity)
    assert rates["spouse"]["frequency"] == 0.2
    assert rates["employee"]["frequency"] == 0.05
    assert rates["spouse"]["frequency"] > rates["employee"]["frequency"] * 3


def test_severity_and_frequency_are_reported_separately_not_as_one_rate():
    # They move independently: a richer maternity limit lifts severity
    # and leaves frequency alone.
    members, maternity = _maternity_book()
    spouse = maternity_rates(members, maternity)["spouse"]
    assert spouse["severity"] == 13_382
    assert round(spouse["cost_per_member_year"]) == round(0.2 * 13_382)


def test_men_and_out_of_age_women_are_not_maternity_exposure():
    members = [
        _book_member("M1", "Employee", 30, "M", 100),
        _book_member("F1", "Employee", 60, "F", 100),
    ]
    assert maternity_rates(members, {}) == {}


# --- the census-side findings -------------------------------------------

def _census(children_ages=(), employees=10, female_spouses_30=0):
    rows = [{"relation": "Employee", "age": 35, "gender": "M"} for _ in range(employees)]
    rows += [{"relation": "Child", "age": age, "gender": "M"} for age in children_ages]
    rows += [{"relation": "Spouse", "age": 30, "gender": "F"} for _ in range(female_spouses_30)]
    return rows


def test_a_group_of_babies_costs_more_than_the_flat_band_says():
    benchmarks = book_benchmarks(_book(), {})
    finding = child_age_finding(_census(children_ages=(0, 0, 0)), benchmarks)
    assert finding["difference_aed"] > 0
    assert finding["newborn_count"] == 3
    assert finding["treatment"] == TREATMENT_LOAD


def test_a_group_of_teenagers_is_not_loaded_for_its_children():
    # The same 0-17 band, the other way round - this must not become a
    # one-directional ratchet.
    benchmarks = book_benchmarks(_book(), {})
    finding = child_age_finding(_census(children_ages=(14, 14, 14)), benchmarks)
    assert finding["difference_aed"] < 0
    assert finding["treatment"] == TREATMENT_ALREADY_PRICED


def test_a_census_with_no_children_has_no_child_finding():
    assert child_age_finding(_census(), book_benchmarks(_book(), {})) is None


def test_a_child_with_no_age_is_counted_and_named_not_dropped():
    benchmarks = book_benchmarks(_book(), {})
    rows = _census() + [{"relation": "Child", "age": None, "gender": "M"}]
    finding = child_age_finding(rows, benchmarks)
    assert finding["children_without_an_age"] == 1
    assert finding["child_count"] == 1


# --- maternity on a census ----------------------------------------------

def test_maternity_age_females_are_costed_at_the_books_own_rates():
    members, maternity = _maternity_book()
    benchmarks = book_benchmarks(members, maternity)
    finding = maternity_finding(_census(female_spouses_30=10), benchmarks, maternity_covered=True)
    assert finding["maternity_age_females"] == {"spouse": 10}
    assert round(finding["expected_cost_aed"]) == round(10 * 0.2 * 13_382)
    assert round(finding["expected_births"], 1) == 2.0


def test_maternity_not_covered_is_reported_as_room_to_go_in_harder():
    # The cube's spouse rates are full of maternity. A group that does
    # not buy it is being overcharged by the risk price, and saying so is
    # what stops this being a one-way ratchet.
    members, maternity = _maternity_book()
    benchmarks = book_benchmarks(members, maternity)
    finding = maternity_finding(_census(female_spouses_30=10), benchmarks, maternity_covered=False)
    assert finding["direction"] == "reduces_risk"
    assert finding["expected_cost_aed"] == 0.0
    assert finding["cost_in_the_risk_price_aed"] > 0


def test_a_richer_maternity_limit_turns_maternity_into_a_loading():
    members, maternity = _maternity_book()
    benchmarks = book_benchmarks(members, maternity)
    plain = maternity_finding(_census(female_spouses_30=10), benchmarks, True, maternity_richer_than_incumbent=False)
    richer = maternity_finding(_census(female_spouses_30=10), benchmarks, True, maternity_richer_than_incumbent=True)
    assert plain["treatment"] == TREATMENT_ALREADY_PRICED
    assert richer["treatment"] == TREATMENT_LOAD


def test_the_newborn_pipeline_prices_members_who_are_not_on_the_census():
    members, maternity = _maternity_book()
    benchmarks = book_benchmarks(members + _book(), maternity)
    finding = maternity_finding(_census(female_spouses_30=10), benchmarks, maternity_covered=True)
    pipeline = newborn_pipeline_finding(finding, benchmarks)
    assert pipeline["expected_births"] == 2.0
    assert pipeline["newborn_multiple"] > 1.5
    assert pipeline["expected_cost_aed"] > 0
    assert pipeline["treatment"] == TREATMENT_LOAD


def test_no_maternity_exposure_means_no_newborn_pipeline():
    benchmarks = book_benchmarks(_book(), {})
    assert newborn_pipeline_finding(None, benchmarks) is None


# --- the factors that are about the offer, not the people ---------------

def test_a_thin_priced_population_widens_the_margin_rather_than_loading():
    thin = [{"expected_cost": 100.0, "credibility": 0.1} for _ in range(10)]
    finding = credibility_finding(thin)
    assert finding["treatment"] == TREATMENT_WIDEN_MARGIN
    assert finding["share_of_cost_from_thin_cells"] == 1.0


def test_a_well_observed_population_is_not_flagged_at_all():
    solid = [{"expected_cost": 100.0, "credibility": 0.95} for _ in range(10)]
    assert credibility_finding(solid)["treatment"] == TREATMENT_ALREADY_PRICED


def test_a_small_group_is_a_wider_spread_not_a_higher_mean():
    assert group_size_finding(_census(employees=20))["treatment"] == TREATMENT_WIDEN_MARGIN
    assert group_size_finding(_census(employees=200))["treatment"] == TREATMENT_ALREADY_PRICED


def test_lives_that_did_not_price_are_a_gap_not_a_zero():
    finding = data_quality_finding(_census(employees=100), priced_member_count=90)
    assert finding["unpriced_lives"] == 10
    assert finding["treatment"] == TREATMENT_LOAD


def test_richer_benefits_load_and_leaner_ones_do_not():
    richer = benefit_buy_up_finding([
        {"field": "annual_limit", "label": "Annual Limit", "direction": "improved"},
        {"field": "dental", "label": "Dental", "direction": "improved"},
    ])
    assert richer["treatment"] == TREATMENT_LOAD
    assert len(richer["richer_fields"]) == 2

    leaner = benefit_buy_up_finding([{"field": "dental", "label": "Dental", "direction": "reduced"}])
    assert leaner["treatment"] == TREATMENT_ALREADY_PRICED


def test_a_field_outside_the_buy_up_list_does_not_load():
    # Area of cover differing is not a buy-up in the utilisation sense.
    assert benefit_buy_up_finding([{"field": "area_of_cover", "label": "Area", "direction": "improved"}]) is None


def test_pre_existing_from_day_one_loads_and_excluded_does_not():
    assert pre_existing_finding({"pre_existing_chronic_limit": "Covered"})["treatment"] == TREATMENT_LOAD
    assert pre_existing_finding({"pre_existing_chronic_limit": "Not Covered"})["treatment"] == TREATMENT_ALREADY_PRICED
    assert pre_existing_finding({}) is None


# --- the conclusion -----------------------------------------------------

def test_only_factors_the_cube_cannot_see_move_the_required_margin():
    # The whole discipline of this module in one assertion: an old,
    # spouse-heavy, dependant-heavy group is already priced for all of
    # that, so a clean offer to one clears at the base margin alone.
    result = assess_opportunity(
        census_rows=_census(employees=200),
        priced_members=[{"expected_cost": 100.0, "credibility": 0.95} for _ in range(200)],
        benchmarks=book_benchmarks(_book(), {}),
        risk_price_aed=1_000_000,
        quoted_price_aed=1_060_000,
        comparison_rows=[],
        proposed_summary={"pre_existing_chronic_limit": "Not Covered"},
    )
    assert result["required_margin_pct"] == ASSUMPTIONS["base_required_margin_pct"]
    assert result["required_margin_contributions"] == []


def test_each_point_of_required_margin_names_the_factor_that_put_it_there():
    result = assess_opportunity(
        census_rows=_census(employees=20),
        priced_members=[{"expected_cost": 100.0, "credibility": 0.1} for _ in range(20)],
        benchmarks=book_benchmarks(_book(), {}),
        risk_price_aed=1_000_000,
        quoted_price_aed=1_050_000,
        comparison_rows=[{"field": "dental", "label": "Dental", "direction": "improved"}],
        proposed_summary={"pre_existing_chronic_limit": "Covered"},
    )
    keys = {c["key"] for c in result["required_margin_contributions"]}
    assert keys == {"credibility", "group_size", "pre_existing", "benefit_buy_up"}
    assert all(c["why"] for c in result["required_margin_contributions"])
    expected = ASSUMPTIONS["base_required_margin_pct"] + sum(c["pct"] for c in result["required_margin_contributions"])
    assert result["required_margin_pct"] == round(expected, 4)


def test_the_buy_up_loading_is_capped():
    rows = [{"field": f, "label": f, "direction": "improved"} for f in
            ("annual_limit", "maternity_limit", "dental", "optical", "coinsurance", "deductible")]
    result = assess_opportunity(
        census_rows=_census(employees=200),
        priced_members=[{"expected_cost": 1.0, "credibility": 0.95} for _ in range(200)],
        benchmarks=book_benchmarks(_book(), {}),
        risk_price_aed=1_000, quoted_price_aed=1_000,
        comparison_rows=rows,
        proposed_summary={},
    )
    buy_up = next(c for c in result["required_margin_contributions"] if c["key"] == "benefit_buy_up")
    assert buy_up["pct"] == ASSUMPTIONS["benefit_buy_up_max_load_pct"]


def test_uncovered_maternity_lowers_the_risk_price_it_is_compared_against():
    members, maternity = _maternity_book()
    benchmarks = book_benchmarks(members + _book(), maternity)
    result = assess_opportunity(
        census_rows=_census(employees=200, female_spouses_30=50),
        priced_members=[{"expected_cost": 1.0, "credibility": 0.95} for _ in range(250)],
        benchmarks=benchmarks,
        risk_price_aed=1_000_000,
        quoted_price_aed=1_000_000,
        maternity_covered=False,
    )
    assert result["adjusted_risk_price_aed"] < result["risk_price_aed"]
    assert result["actual_margin_pct"] > 0


def test_a_price_below_its_own_risk_cost_is_not_a_margin_conversation():
    assert verdict(-0.05, 0.10)["verdict"] == VERDICT_DECLINE


def test_short_of_the_required_margin_says_by_how_much():
    result = verdict(0.04, 0.15)
    assert result["verdict"] == VERDICT_NEEDS_LOADING
    assert "11" in result["headline"]


def test_clearly_over_the_required_margin_is_room_to_move():
    assert verdict(0.30, 0.10)["verdict"] == VERDICT_AGGRESSIVE


def test_just_over_the_required_margin_is_not_room_to_move():
    # A point or two of headroom is noise, and calling it "go aggressive"
    # is how a portal talks an underwriter into giving away real money.
    assert verdict(0.11, 0.10)["verdict"] == VERDICT_PRICED_RIGHT


def test_no_quote_yet_says_so_rather_than_guessing():
    assert verdict(None, 0.10)["verdict"] == VERDICT_UNKNOWN


def test_the_open_questions_are_returned_and_never_assumed_benign():
    result = assess_opportunity(
        census_rows=_census(employees=100),
        priced_members=[{"expected_cost": 1.0, "credibility": 0.95} for _ in range(100)],
        benchmarks=book_benchmarks(_book(), {}),
        risk_price_aed=None, quoted_price_aed=None,
    )
    assert {q["key"] for q in result["open_questions"]} == {
        "participation", "incumbent_loss_ratio", "reason_for_moving"
    }
    assert result["verdict"]["verdict"] == VERDICT_UNKNOWN
