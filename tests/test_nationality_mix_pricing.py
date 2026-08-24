"""Nationality mix on a new enquiry -
app/scoring/rules/nationality_mix_pricing.py.
"""
import pytest

from app.scoring.rules.nationality_mix_pricing import (
    MIN_MEASURABLE_SHARE,
    apply_mix_to_quote,
    nationality_mix_factor,
)


def _row(nationality, relativity, pricing_ready=True, credibility=0.9, exposure=200.0):
    return {
        "nationality": nationality,
        "nationality_zone": "Zone 1",
        "relativity": relativity,
        "pricing_ready": pricing_ready,
        "credibility": credibility,
        "earned_member_years": exposure,
    }


def _census(**counts):
    out = []
    for nationality, n in counts.items():
        out += [{"nationality": nationality} for _ in range(n)]
    return out


ROWS = [_row("India", 0.80), _row("Egypt", 1.40), _row("France", 1.00)]


def test_a_favourable_mix_prices_below_the_card():
    mix = nationality_mix_factor(_census(India=90, France=10), ROWS)
    assert mix["factor"] == pytest.approx(0.82, abs=0.001)
    assert mix["direction"] == "favourable"
    assert mix["pricing_ready"] is True


def test_an_adverse_mix_prices_above_the_card():
    mix = nationality_mix_factor(_census(Egypt=90, France=10), ROWS)
    assert mix["factor"] > 1.0
    assert mix["direction"] == "adverse"


def test_a_balanced_mix_is_neutral():
    mix = nationality_mix_factor(_census(India=50, Egypt=50), ROWS)
    assert mix["factor"] == pytest.approx(1.10, abs=0.001)
    assert mix["direction"] == "adverse"   # 0.8/1.4 average is above 1


def test_weighting_is_by_headcount_not_by_claims():
    # Each member counts once regardless of what their nationality has
    # historically claimed - weighting by claims would let the expensive
    # nationalities dominate their own factor.
    mix = nationality_mix_factor(_census(India=99, Egypt=1), ROWS)
    expected = (0.80 * 99 + 1.40 * 1) / 100
    assert mix["raw_factor"] == pytest.approx(expected, abs=0.001)


def test_a_thin_nationality_does_not_contribute_its_own_factor():
    rows = ROWS + [_row("Nauru", 3.0, pricing_ready=False, exposure=3.0)]
    mix = nationality_mix_factor(_census(India=50, Nauru=50), rows)
    # Nauru is excluded entirely, so the factor is India's alone.
    assert mix["factor"] == pytest.approx(0.80, abs=0.001)
    assert mix["measured_member_count"] == 50
    assert [u["nationality"] for u in mix["unmeasured"]] == ["nauru"]


def test_unmeasured_members_are_excluded_not_averaged_in_at_neutral():
    # Averaging them in at 1.0 drags every factor toward neutral and makes
    # a real signal look weaker than it is.
    mix = nationality_mix_factor(_census(India=50, Atlantis=50), ROWS)
    assert mix["factor"] == pytest.approx(0.80, abs=0.001)
    assert mix["measurable_share"] == 0.5


def test_a_factor_built_on_too_little_of_the_census_is_not_priced_on():
    mix = nationality_mix_factor(_census(India=20, Atlantis=80), ROWS)
    assert mix["measurable_share"] == pytest.approx(0.2)
    assert mix["measurable_share"] < MIN_MEASURABLE_SHARE
    assert mix["pricing_ready"] is False
    assert mix["factor"] is not None   # still reported, just not trusted


def test_the_factor_is_capped_and_says_when_the_cap_binds():
    mix = nationality_mix_factor(_census(Extreme=100), [_row("Extreme", 2.0)])
    assert mix["raw_factor"] == 2.0
    assert mix["factor"] == 1.35
    assert mix["capped"] is True


def test_an_empty_census_produces_no_factor_rather_than_an_error():
    mix = nationality_mix_factor([], ROWS)
    assert mix["factor"] is None
    assert mix["pricing_ready"] is False
    assert mix["direction"] is None


def test_contributions_are_listed_biggest_first_with_their_share():
    mix = nationality_mix_factor(_census(India=70, Egypt=30), ROWS)
    assert [c["nationality"] for c in mix["contributions"]] == ["India", "Egypt"]
    assert mix["contributions"][0]["share_of_census"] == pytest.approx(0.7)


def test_allowing_thin_nationalities_is_an_explicit_choice():
    rows = [_row("Thin", 1.5, pricing_ready=False, exposure=2.0)]
    strict = nationality_mix_factor(_census(Thin=100), rows, require_pricing_ready=True)
    loose = nationality_mix_factor(_census(Thin=100), rows, require_pricing_ready=False)
    assert strict["factor"] is None
    assert loose["factor"] == 1.35   # 1.5 capped


# --- applying it to a quote ---------------------------------------------

def test_the_quote_is_shown_with_and_without_the_adjustment():
    mix = nationality_mix_factor(_census(India=100), ROWS)
    result = apply_mix_to_quote(100_000.0, mix)
    assert result["card_premium"] == 100_000.0
    assert result["mix_adjusted_premium"] == 80_000.0
    assert result["difference"] == -20_000.0
    assert result["difference_pct"] == -20.0
    assert result["applied"] is True


def test_an_unreliable_mix_is_shown_but_flagged_as_not_applied():
    mix = nationality_mix_factor(_census(India=10, Atlantis=90), ROWS)
    result = apply_mix_to_quote(100_000.0, mix)
    assert result["applied"] is False
    assert "not applied" in result["reason"]
    # The number is still there for the underwriter to weigh.
    assert result["mix_adjusted_premium"] == 80_000.0


def test_no_nationality_experience_leaves_the_quote_untouched():
    mix = nationality_mix_factor(_census(Atlantis=100), ROWS)
    result = apply_mix_to_quote(100_000.0, mix)
    assert result["mix_adjusted_premium"] == 100_000.0
    assert result["factor"] is None
    assert result["applied"] is False


def test_an_adverse_mix_raises_the_quote():
    mix = nationality_mix_factor(_census(Egypt=100), ROWS)
    result = apply_mix_to_quote(100_000.0, mix)
    assert result["mix_adjusted_premium"] > 100_000.0
    assert result["difference"] > 0
