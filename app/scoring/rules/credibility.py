"""Partial credibility - how much of a rate should come from a segment's own
experience, and how much from a broader one it belongs to.

Portfolio Analysis already flags a thin bucket via MIN_CREDIBLE_MEMBER_YEARS,
but that flag is binary: a bucket is either trusted whole or thrown away. For
pricing that is too blunt in both directions. A nationality with 40 member-
years is not worthless (it is real experience), and it is certainly not
reliable enough to price on alone - the honest answer is a blend, weighted by
how much exposure actually stands behind it.

This module implements limited-fluctuation ("square root") credibility, the
standard approach:

    Z = min(1, sqrt(exposure / full_credibility_exposure))
    blended = Z * own_rate + (1 - Z) * complement_rate

The complement is whatever broader rate the segment belongs to - a
nationality's own zone, a zone's own book. So a segment with almost no data
sits at the broader rate and moves toward its own experience as exposure
accumulates, rather than flipping between the two at an arbitrary threshold.

Why square root rather than the linear form used in
app/scoring/rules/claims_experience.py: linear credibility gives a small
segment far less weight than its statistical reliability warrants (at a
quarter of the full standard, linear assigns 25% weight where the square
root rule assigns 50%). The square root rule comes from the standard error
of the estimate scaling with 1/sqrt(n), so it reflects how reliability
actually grows with exposure. claims_experience.py's own linear factor is
left alone deliberately - it feeds the legacy composite scorecard, and
changing it would silently move every existing case's score.
"""
import math
from typing import Optional

#: Exposure at which a segment's own experience is trusted completely.
#: 100 member-years - i.e. roughly 100 lives observed for a full year -
#: matching the 1200 member-months already used as the full-credibility
#: standard in app/scoring/rules/claims_experience.py, so the two parts of
#: the system mean the same thing by "fully credible" even though they
#: reach it on different curves.
FULL_CREDIBILITY_MEMBER_YEARS = 100.0


def credibility_factor(
    exposure_member_years: Optional[float],
    full_credibility_member_years: float = FULL_CREDIBILITY_MEMBER_YEARS,
) -> float:
    """How much weight a segment's own experience earns, from 0 (none) to
    1 (full). Zero or missing exposure earns nothing rather than raising -
    a segment with no members is a normal state in a demographic cube, not
    an error."""
    if full_credibility_member_years <= 0:
        raise ValueError("full_credibility_member_years must be positive.")
    if not exposure_member_years or exposure_member_years <= 0:
        return 0.0
    return min(1.0, math.sqrt(exposure_member_years / full_credibility_member_years))


def blend_with_complement(
    own_rate: Optional[float],
    complement_rate: Optional[float],
    exposure_member_years: Optional[float],
    full_credibility_member_years: float = FULL_CREDIBILITY_MEMBER_YEARS,
) -> dict:
    """Credibility-weighted blend of a segment's own rate with the broader
    rate it belongs to.

    Returns the blended rate alongside the credibility actually applied and
    both inputs, so a quote can always show WHY a number is what it is -
    "40 member-years earns 63% credibility, so this is 63% your own
    experience and 37% the zone rate" is defensible to a broker in a way
    that a bare figure is not.

    A segment with no own rate at all (no claims data for that cell) falls
    back to the complement entirely. If there is no complement either,
    the own rate stands alone - there is nothing to blend toward, and
    returning None would lose real experience.
    """
    if own_rate is None:
        return {
            "blended_rate": complement_rate,
            "credibility": 0.0,
            "own_rate": None,
            "complement_rate": complement_rate,
            "exposure_member_years": round(exposure_member_years or 0.0, 4),
        }
    if complement_rate is None:
        return {
            "blended_rate": own_rate,
            "credibility": 1.0,
            "own_rate": own_rate,
            "complement_rate": None,
            "exposure_member_years": round(exposure_member_years or 0.0, 4),
        }

    z = credibility_factor(exposure_member_years, full_credibility_member_years)
    return {
        "blended_rate": round(z * own_rate + (1 - z) * complement_rate, 2),
        "credibility": round(z, 4),
        "own_rate": round(own_rate, 2),
        "complement_rate": round(complement_rate, 2),
        "exposure_member_years": round(exposure_member_years or 0.0, 4),
    }


def relativity(
    blended_rate: Optional[float],
    baseline_rate: Optional[float],
    min_relativity: float = 0.5,
    max_relativity: float = 2.0,
) -> Optional[float]:
    """A segment's blended rate expressed as a multiplier of the baseline
    it should be priced against - the form a rating factor actually takes.

    Capped in both directions. Even after credibility weighting, a segment
    that happens to contain one catastrophic claim can produce a
    multiplier that is real arithmetic but indefensible as a price, and an
    uncapped factor would carry that straight into a quote. The caps are
    stated rather than hidden so an underwriter can see when one binds.
    """
    if not blended_rate or not baseline_rate:
        return None
    return round(max(min_relativity, min(max_relativity, blended_rate / baseline_rate)), 4)
