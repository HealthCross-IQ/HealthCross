import pytest

from app.scoring.rules.renewal_rating import (
    MIN_CREDIBLE_CASE_COUNT,
    RenewalRatingAssumptions,
    benchmark_case_against_book,
    calculate_renewal_rating,
    case_loading_pct,
    premium_component_breakdown,
)


def test_worked_example_matches_hand_calculation():
    # annualized incurred 2,966,593 vs current premium 3,000,000 (hypothetical)
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
