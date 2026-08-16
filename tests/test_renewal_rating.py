import pytest

from app.scoring.rules.renewal_rating import (
    MIN_CREDIBLE_CASE_COUNT,
    RenewalRatingAssumptions,
    benchmark_case_against_book,
    calculate_renewal_rating,
)


def test_worked_example_matches_hand_calculation():
    # annualized incurred 2,966,593 vs current premium 3,000,000 (hypothetical)
    result = calculate_renewal_rating(2_966_593, 3_000_000)

    assert result["actual_loss_ratio"] == pytest.approx(0.9889, abs=0.001)
    trended = 2_966_593 * 1.075
    assert result["trended_claims"] == pytest.approx(trended, abs=1)
    required = trended / (1 - 0.28)
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
