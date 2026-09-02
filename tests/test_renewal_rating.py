import pytest

from app.scoring.rules.renewal_rating import (
    DEFAULT_CREDIBILITY_PCT,
    MIN_CREDIBLE_CASE_COUNT,
    RenewalRatingAssumptions,
    benchmark_case_against_book,
    calculate_renewal_rating,
    calculate_renewal_rating_two_methods,
    case_loading_pct,
    dynamic_ibnr_incurred_claims,
    premium_component_breakdown,
)


def test_worked_example_matches_hand_calculation():
    # 2,966,593 is ALREADY the incurred figure (Paid+Outstanding+IBNR,
    # computed upstream by the caller - see _case_renewal_rating) vs
    # current premium 3,000,000 (hypothetical).
    result = calculate_renewal_rating(2_966_593, 3_000_000)

    assert result["actual_loss_ratio"] == pytest.approx(0.9889, abs=0.001)
    trended = 2_966_593 * 1.075
    assert result["trended_claims"] == pytest.approx(trended, abs=1)
    required = trended / (1 - 0.33)
    assert result["required_premium"] == pytest.approx(required, abs=1)
    expected_increase = (required / 3_000_000 - 1) * 100
    assert result["renewal_increase_pct"] == pytest.approx(expected_increase, abs=0.01)


def test_low_loss_ratio_can_still_require_an_increase_due_to_loading_and_inflation():
    # Even a modest loss ratio can still net to a rate increase once
    # inflation and the commission/OPEX loading are applied.
    result = calculate_renewal_rating(500_000, 1_000_000)
    assert result["actual_loss_ratio"] == 0.5
    assert result["renewal_increase_pct"] > -50  # sanity: not a nonsensical value


def test_custom_assumptions_change_the_result():
    default_result = calculate_renewal_rating(1_000_000, 1_000_000)
    lighter_load = calculate_renewal_rating(
        1_000_000, 1_000_000, assumptions=RenewalRatingAssumptions(loading_pct=0.10)
    )
    assert lighter_load["renewal_increase_pct"] < default_result["renewal_increase_pct"]


def test_credibility_pct_one_is_a_no_op():
    # credibility_pct=1.0 (Method A's own setting) is a true no-op - the
    # SAME trended claims just get grossed up directly, no
    # partial-credibility shading.
    result = calculate_renewal_rating(1_000_000, 1_000_000)
    assert result["credible_claims"] == result["trended_claims"]
    assert result["assumptions_used"]["credibility_pct"] == 1.0


def test_credibility_pct_below_one_shades_the_trended_claims_down():
    result = calculate_renewal_rating(1_000_000, 1_000_000, RenewalRatingAssumptions(credibility_pct=0.90))
    assert result["credible_claims"] == pytest.approx(result["trended_claims"] * 0.90, abs=0.01)
    assert result["required_premium"] < calculate_renewal_rating(1_000_000, 1_000_000)["required_premium"]


def test_calculate_renewal_rating_two_methods_uses_each_methods_own_incurred_base():
    # Method A and Method B get DIFFERENT incurred-claims figures (each
    # method's own IBNR convention - see _case_renewal_rating) - the combo
    # function just runs each through calculate_renewal_rating with its
    # own credibility setting.
    both = calculate_renewal_rating_two_methods(1_100_000, 1_050_000, 1_000_000)
    assert both["method_a"]["annualized_incurred_claims"] == 1_100_000
    assert both["method_b"]["annualized_incurred_claims"] == 1_050_000
    assert both["method_a"]["assumptions_used"]["credibility_pct"] == 1.0
    assert both["method_b"]["assumptions_used"]["credibility_pct"] == DEFAULT_CREDIBILITY_PCT == 1.0
    assert both["gap"] == round(both["method_b"]["required_premium"] - both["method_a"]["required_premium"], 2)


def test_calculate_renewal_rating_two_methods_matches_when_given_the_same_base_and_default_credibility():
    # DEFAULT_CREDIBILITY_PCT is 1.0 - a renewal's own claims ledger is
    # real, known experience, not a projection needing to be discounted -
    # so given the SAME incurred base for both, they produce the SAME
    # required premium by default.
    both = calculate_renewal_rating_two_methods(1_000_000, 1_000_000, 1_000_000)
    assert both["method_b"]["required_premium"] == both["method_a"]["required_premium"]
    assert both["gap"] == 0.0


def test_overriding_credibility_pct_makes_method_b_require_less():
    both = calculate_renewal_rating_two_methods(1_000_000, 1_000_000, 1_000_000, credibility_pct=0.90)
    assert both["method_b"]["assumptions_used"]["credibility_pct"] == 0.90
    assert both["method_b"]["required_premium"] < both["method_a"]["required_premium"]
    assert both["gap"] == round(both["method_b"]["required_premium"] - both["method_a"]["required_premium"], 2)
    assert both["gap_pct"] < 0


def test_dynamic_ibnr_incurred_claims_matches_the_documented_formula():
    # Paid 158,785 over 351 elapsed days, projected over a 30-day tail -
    # same worked example used to validate against Portfolio Analysis's
    # own ibnr_for_member convention.
    result = dynamic_ibnr_incurred_claims(
        total_paid=158_785, total_outstanding=17_392, elapsed_days=351, months_count=10,
    )
    assert result["ibnr"] == pytest.approx(13_571.37, abs=0.01)
    assert result["incurred_to_date"] == pytest.approx(158_785 + 17_392 + 13_571.37, abs=0.01)
    assert result["annualized_incurred_claims"] == pytest.approx(result["incurred_to_date"] / 10 * 12, abs=0.01)


def test_dynamic_ibnr_incurred_claims_handles_zero_elapsed_days():
    result = dynamic_ibnr_incurred_claims(total_paid=100_000, total_outstanding=0, elapsed_days=0, months_count=1)
    assert result["ibnr"] == 0.0
    assert result["incurred_to_date"] == 100_000.0


def test_rejects_negative_claims_or_non_positive_premium():
    with pytest.raises(ValueError):
        calculate_renewal_rating(-1, 100)
    with pytest.raises(ValueError):
        calculate_renewal_rating(100, 0)


def _result(loss_ratio, increase_pct):
    return {"actual_loss_ratio": loss_ratio, "renewal_increase_pct": increase_pct}


def test_benchmark_case_against_book_computes_percentile_and_median():
    this_result = _result(0.90, 30.0)
    others = [_result(r, i) for r, i in [(0.5, 10.0), (0.6, 15.0), (0.7, 20.0), (0.8, 25.0), (0.85, 28.0)]]
    benchmark = benchmark_case_against_book(this_result, others)
    assert benchmark["comparable_case_count"] == 5
    assert benchmark["percentile"] == 100.0  # higher than every other case
    assert benchmark["median_loss_ratio"] == 0.7
    assert benchmark["min_loss_ratio"] == 0.5
    assert benchmark["max_loss_ratio"] == 0.85
    assert benchmark["median_renewal_increase_pct"] == 20.0
    assert benchmark["other_loss_ratios"] == [0.5, 0.6, 0.7, 0.8, 0.85]


def test_benchmark_case_against_book_ties_count_as_half_a_rank():
    this_result = _result(0.7, 20.0)
    others = [_result(0.7, 20.0), _result(0.5, 10.0), _result(0.9, 30.0)]
    benchmark = benchmark_case_against_book(this_result, others)
    # 1 below (0.5), 1 tied (0.7), 1 above (0.9) -> (1 + 0.5) / 3 * 100
    assert benchmark["percentile"] == pytest.approx(50.0, abs=0.1)


def test_benchmark_case_against_book_flags_low_credibility_below_threshold():
    this_result = _result(0.7, 20.0)
    few_others = [_result(0.6, 15.0), _result(0.8, 25.0)]
    assert len(few_others) < MIN_CREDIBLE_CASE_COUNT
    benchmark = benchmark_case_against_book(this_result, few_others)
    assert benchmark["low_credibility"] is True

    enough_others = [_result(0.5 + 0.05 * i, 10.0 + i) for i in range(MIN_CREDIBLE_CASE_COUNT)]
    benchmark = benchmark_case_against_book(this_result, enough_others)
    assert benchmark["low_credibility"] is False


def test_benchmark_case_against_book_handles_no_comparable_cases():
    benchmark = benchmark_case_against_book(_result(0.7, 20.0), [])
    assert benchmark["comparable_case_count"] == 0
    assert benchmark["percentile"] is None
    assert benchmark["low_credibility"] is True


def test_premium_component_breakdown_defaults_reproduce_the_67_6_5_15_6_5_5_split():
    result = calculate_renewal_rating(3_995_650.67, 1_869_572)
    breakdown = premium_component_breakdown(result)

    assert breakdown["risk_premium_pct"] == pytest.approx(0.67, abs=0.001)
    assert breakdown["tpa_fee_pct"] == pytest.approx(0.065, abs=0.001)
    assert breakdown["commission_pct"] == pytest.approx(0.15, abs=0.001)
    assert breakdown["hc_fee_pct"] == pytest.approx(0.065, abs=0.001)
    assert breakdown["qic_fee_pct"] == pytest.approx(0.05, abs=0.001)

    proposed = breakdown["proposed"]
    assert proposed["total"] == result["required_premium"]
    assert proposed["risk_premium"] == result["trended_claims"]
    reconstructed = (
        proposed["risk_premium"] + proposed["tpa_fee"] + proposed["commission"]
        + proposed["hc_fee"] + proposed["qic_fee"]
    )
    assert reconstructed == pytest.approx(result["required_premium"], abs=0.5)

    existing = breakdown["existing"]
    assert existing["total"] == result["current_annual_premium"]
    reconstructed_existing = (
        existing["risk_premium"] + existing["tpa_fee"] + existing["commission"]
        + existing["hc_fee"] + existing["qic_fee"]
    )
    assert reconstructed_existing == pytest.approx(result["current_annual_premium"], abs=0.5)


def test_premium_component_breakdown_stays_consistent_with_a_custom_loading():
    # A case that overrides loading_pct away from the 33% default, but
    # leaves the fee split at its defaults (which only sum to 33%) - the
    # components must still reconstruct the ACTUAL required_premium, not
    # silently assume a 67% risk premium share that's no longer true.
    result = calculate_renewal_rating(1_000_000, 1_000_000, assumptions=RenewalRatingAssumptions(loading_pct=0.40))
    breakdown = premium_component_breakdown(result)

    assert breakdown["risk_premium_pct"] == pytest.approx(0.60, abs=0.001)  # 1 - 0.40, not 0.67
    proposed = breakdown["proposed"]
    reconstructed = (
        proposed["risk_premium"] + proposed["tpa_fee"] + proposed["commission"]
        + proposed["hc_fee"] + proposed["qic_fee"]
    )
    assert reconstructed == pytest.approx(result["required_premium"], abs=0.5)


def test_premium_component_breakdown_respects_custom_fee_weights():
    result = calculate_renewal_rating(1_000_000, 1_000_000)
    # Custom weights not summing to 0.33 - only their relative proportions matter.
    breakdown = premium_component_breakdown(
        result, tpa_fee_pct=0.4, commission_pct=0.3, hc_fee_pct=0.2, qic_fee_pct=0.1
    )

    proposed = breakdown["proposed"]
    reconstructed = (
        proposed["risk_premium"] + proposed["tpa_fee"] + proposed["commission"]
        + proposed["hc_fee"] + proposed["qic_fee"]
    )
    assert reconstructed == pytest.approx(result["required_premium"], abs=0.5)
    # TPA's weight (0.4 of 1.0 total) should be exactly 40% of the loading amount.
    loading_amount = proposed["total"] - proposed["risk_premium"]
    assert proposed["tpa_fee"] == pytest.approx(loading_amount * 0.4, abs=0.5)


def test_premium_component_breakdown_rejects_all_zero_weights():
    result = calculate_renewal_rating(1_000_000, 1_000_000)
    with pytest.raises(ValueError):
        premium_component_breakdown(result, tpa_fee_pct=0, commission_pct=0, hc_fee_pct=0, qic_fee_pct=0)


def test_case_loading_pct_defaults_to_33_percent():
    assert case_loading_pct(None, None, None, None) == pytest.approx(0.33)


def test_case_loading_pct_sums_the_cases_own_fee_split():
    # Matches a real acquisition-cost breakdown: brokerage 15% + TPA 6.5% + HC 6.5% + QIC 5% = 33%.
    assert case_loading_pct(
        tpa_fee_pct=0.065, commission_pct=0.15, hc_fee_pct=0.065, qic_fee_pct=0.05
    ) == pytest.approx(0.33)


def test_case_loading_pct_feeds_calculate_renewal_rating_so_fees_move_the_bottom_line():
    default_loading = case_loading_pct(None, None, None, None)
    default_result = calculate_renewal_rating(
        1_000_000, 1_000_000, RenewalRatingAssumptions(loading_pct=default_loading)
    )

    custom_loading = case_loading_pct(tpa_fee_pct=0.15, commission_pct=0.10, hc_fee_pct=0.08, qic_fee_pct=0.05)
    custom_result = calculate_renewal_rating(
        1_000_000, 1_000_000, RenewalRatingAssumptions(loading_pct=custom_loading)
    )

    assert custom_loading == pytest.approx(0.38)
    assert custom_result["required_premium"] != pytest.approx(default_result["required_premium"])


def test_the_ladder_prices_the_loss_ratio_it_reports():
    """Feeding a published loss ratio back in reproduces the premium it
    was published beside.

    Every screen publishes loss_ratio rounded to four places, and several
    then rework it - the scenarios table strips a claim out of it, the
    drivers waterfall decomposes it. When the ladder priced the unrounded
    input but reported the rounded one, those screens landed a couple of
    hundred dirhams from the rating card on the same account: 6,248,967.59
    against 6,248,789.81 on Amazonico. Small enough to look like a
    rounding artefact, large enough for someone to stop trusting the page.
    """
    from app.scoring.rules.renewal_rating import renewal_from_loss_ratio

    for raw in (1.5601462, 0.8360049, 0.35459999, 2.4863333):
        first = renewal_from_loss_ratio(raw, 3_000_000.0, 0.075, 0.215)
        again = renewal_from_loss_ratio(first["loss_ratio"], 3_000_000.0, 0.075, 0.215)
        assert again["required_premium"] == first["required_premium"]
        assert again["renewal_increase_pct"] == first["renewal_increase_pct"]
        assert again["loss_ratio"] == first["loss_ratio"]
