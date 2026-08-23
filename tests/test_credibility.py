"""Tests for app/scoring/rules/credibility.py - partial credibility."""
import pytest

from app.scoring.rules.credibility import (
    FULL_CREDIBILITY_MEMBER_YEARS,
    blend_with_complement,
    credibility_factor,
    relativity,
)


def test_full_exposure_earns_full_credibility():
    assert credibility_factor(FULL_CREDIBILITY_MEMBER_YEARS) == 1.0


def test_credibility_is_capped_at_one_beyond_the_standard():
    assert credibility_factor(FULL_CREDIBILITY_MEMBER_YEARS * 10) == 1.0


def test_no_exposure_earns_no_credibility():
    assert credibility_factor(0) == 0.0
    assert credibility_factor(None) == 0.0
    assert credibility_factor(-5) == 0.0


def test_credibility_follows_the_square_root_rule():
    # A quarter of the full standard earns HALF credibility, not a quarter -
    # reliability grows with sqrt(n), so linear weighting would understate
    # a small segment's real informational value.
    assert credibility_factor(25.0, full_credibility_member_years=100.0) == 0.5
    assert credibility_factor(1.0, full_credibility_member_years=100.0) == 0.1


def test_credibility_rises_with_exposure():
    z = [credibility_factor(e) for e in (1, 10, 40, 80, 100)]
    assert z == sorted(z)
    assert all(0.0 <= v <= 1.0 for v in z)


def test_rejects_a_non_positive_full_credibility_standard():
    with pytest.raises(ValueError):
        credibility_factor(10.0, full_credibility_member_years=0)


def test_blend_sits_between_the_two_rates():
    r = blend_with_complement(own_rate=3000.0, complement_rate=1000.0, exposure_member_years=25.0)

    assert r["credibility"] == 0.5
    assert r["blended_rate"] == 2000.0  # 0.5 * 3000 + 0.5 * 1000
    assert r["own_rate"] == 3000.0
    assert r["complement_rate"] == 1000.0


def test_a_fully_credible_segment_keeps_its_own_rate():
    r = blend_with_complement(3000.0, 1000.0, exposure_member_years=FULL_CREDIBILITY_MEMBER_YEARS)
    assert r["credibility"] == 1.0
    assert r["blended_rate"] == 3000.0


def test_a_segment_with_no_exposure_sits_entirely_on_the_complement():
    r = blend_with_complement(3000.0, 1000.0, exposure_member_years=0)
    assert r["credibility"] == 0.0
    assert r["blended_rate"] == 1000.0


def test_a_thin_segment_barely_moves_off_the_complement():
    # One member-year of experience must not drag a rate 3x - this is the
    # exact failure the blend exists to prevent.
    r = blend_with_complement(9000.0, 1000.0, exposure_member_years=1.0)
    assert r["credibility"] == 0.1
    assert r["blended_rate"] == 1800.0


def test_missing_own_rate_falls_back_to_the_complement():
    r = blend_with_complement(None, 1000.0, exposure_member_years=50.0)
    assert r["blended_rate"] == 1000.0
    assert r["credibility"] == 0.0


def test_missing_complement_leaves_the_own_rate_standing():
    # Nothing to blend toward - returning None would discard real experience.
    r = blend_with_complement(3000.0, None, exposure_member_years=50.0)
    assert r["blended_rate"] == 3000.0
    assert r["credibility"] == 1.0


def test_relativity_expresses_a_rate_against_its_baseline():
    assert relativity(1500.0, 1000.0) == 1.5
    assert relativity(800.0, 1000.0) == 0.8


def test_relativity_is_capped_in_both_directions():
    # Real arithmetic, indefensible as a price - the cap keeps a single
    # catastrophic claim from reaching a quote as a 9x factor.
    assert relativity(9000.0, 1000.0) == 2.0
    assert relativity(10.0, 1000.0) == 0.5


def test_relativity_needs_both_rates():
    assert relativity(None, 1000.0) is None
    assert relativity(1000.0, None) is None
    assert relativity(1000.0, 0) is None
