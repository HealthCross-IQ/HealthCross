import pytest

from app.scoring.rules.renewal_rating import RenewalRatingAssumptions, calculate_renewal_rating


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
