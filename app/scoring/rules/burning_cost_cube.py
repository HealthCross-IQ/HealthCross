"""What a member actually costs, cell by cell, from HealthCross's own book.

The existing burning-cost summaries (see portfolio_analysis's
summarize_burning_cost_by_product_network_age_gender) each answer one
fixed question at one fixed grain, and a cell with three lives in it
reports its own raw claims rate as though it were a price. That is fine
for a reference table an underwriter reads and weighs, and unusable as
the basis for actually quoting: the thin cells are exactly the ones a new
enquiry lands in, and their raw rates swing by an order of magnitude on a
single claim.

This module builds the same experience as a *hierarchy* instead. Level 0
is the whole book. Each level below adds one more dimension - product,
then network, then age band, gender, relation, and finally nationality
zone - and every cell is credibility-blended toward its own parent rather
than toward a flat book average. So a cell with real exposure prices on
its own experience; a thin cell falls back to the nearest broader cell
that does have exposure, not to an unrelated book-wide mean. That is what
makes an individual cell safe to price from, and it is the shared
dependency for expected-cost pricing (which replaces the scorecard's
arbitrary loading) and for the nationality factors feeding New Business.

Everything here is a pure function over the member-result dicts
analyze_portfolio_member already produces; nothing touches the database.

Terminology, since two different words get used loosely elsewhere in the
codebase: `own_rate` is a cell's raw claims per member-year (what it
literally cost), and `expected_cost` is that rate after credibility
blending (what it should be priced at). They differ precisely where the
data is thin, which is the whole point.
"""
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from app.scoring.rules.credibility import (
    FULL_CREDIBILITY_MEMBER_YEARS,
    blend_with_complement,
    relativity,
)

#: Cube dimensions, ordered broadest-first - the order IS the hierarchy,
#: so it is a modelling decision rather than a cosmetic one. Product and
#: network come first because plan design sets the cost level before any
#: demographic does; age band next because it is the strongest demographic
#: driver; gender, relation and nationality zone refine within that. A
#: member always falls back along this same path, so the fallback is
#: always to the most specific cell that still has exposure.
DEFAULT_CUBE_DIMENSIONS: Tuple[str, ...] = (
    "product",
    "network",
    "age_band",
    "gender",
    "relation",
    "nationality_zone",
)

#: Value used when a member carries no value for a dimension at all.
#: Kept as a real key rather than dropping the member, so book totals
#: still reconcile against the executive summary - a member with no
#: recorded nationality is unmapped, not absent.
UNMAPPED = "Unmapped"


def age_band_label(age: Optional[int], bands: List[tuple]) -> str:
    """Which rate-card age band a member falls in, as a label. Bands come
    from the rate card itself rather than being invented here, so a cube
    cell lines up exactly with the card row that prices it.
    """
    if age is None:
        return UNMAPPED
    for low, high in bands:
        if low <= age <= high:
            return f"{low}-{high}"
    return UNMAPPED


def member_cube_key(
    result: dict,
    dimensions: Tuple[str, ...],
    bands: List[tuple],
) -> Tuple[str, ...]:
    """The full-depth cube coordinates for one member result."""
    key = []
    for dim in dimensions:
        if dim == "age_band":
            key.append(age_band_label(result.get("age"), bands))
        else:
            value = result.get(dim)
            key.append(str(value).strip() if value not in (None, "") else UNMAPPED)
    return tuple(key)


def burning_cost_cube(
    member_results: List[dict],
    rate_cards: List[dict],
    dimensions: Tuple[str, ...] = DEFAULT_CUBE_DIMENSIONS,
    full_credibility_member_years: float = FULL_CREDIBILITY_MEMBER_YEARS,
    min_relativity: float = 0.5,
    max_relativity: float = 2.0,
) -> dict:
    """The book's own experience as a credibility-blended hierarchy.

    Exposure is measured in member-years (`earned_premium_fraction`, the
    share of the policy term a member was actually on risk for) rather
    than headcount, so a member who joined in month 10 contributes the
    quarter-year of exposure they actually represent instead of counting
    as a whole life. Pricing off headcount would understate cost on any
    account with mid-term joiners.

    Each level's cells blend toward their PARENT'S already-blended rate,
    not the parent's raw rate. That is what makes the fallback graceful:
    a thin (product, network, age, gender, relation, nationality) cell
    leans on a (product, network, age, gender, relation) cell that has
    itself already been stabilised, rather than inheriting whatever noise
    sits one level up.
    """
    from app.scoring.rules.portfolio_analysis import age_bands_from_rate_cards

    bands = age_bands_from_rate_cards(rate_cards)

    # Accumulate exposure and claims at every level at once. Level 0 is
    # the empty key - the whole book - and level k is the first k
    # dimensions, so a member contributes to exactly one cell per level.
    totals: Dict[Tuple[int, Tuple[str, ...]], dict] = defaultdict(
        lambda: {"member_count": 0, "earned_member_years": 0.0, "actual_claims": 0.0}
    )

    for result in member_results:
        if not result.get("in_scope", True):
            continue
        exposure = result.get("earned_premium_fraction") or 0.0
        claims = result.get("actual_claims") or 0.0
        full_key = member_cube_key(result, dimensions, bands)
        for level in range(len(dimensions) + 1):
            bucket = totals[(level, full_key[:level])]
            bucket["member_count"] += 1
            bucket["earned_member_years"] += exposure
            bucket["actual_claims"] += claims

    def own_rate_of(bucket: dict) -> Optional[float]:
        years = bucket["earned_member_years"]
        return (bucket["actual_claims"] / years) if years else None

    book = totals.get((0, ()))
    book_rate = own_rate_of(book) if book else None

    # Blend level by level, so a level's parents are always already
    # blended by the time their children need them as a complement.
    blended_by_key: Dict[Tuple[int, Tuple[str, ...]], Optional[float]] = {(0, ()): book_rate}
    cells: List[dict] = []

    for level in range(1, len(dimensions) + 1):
        for (lvl, key), bucket in totals.items():
            if lvl != level:
                continue
            own = own_rate_of(bucket)
            complement = blended_by_key.get((level - 1, key[:-1]))
            blend = blend_with_complement(
                own, complement, bucket["earned_member_years"], full_credibility_member_years
            )
            expected = blend["blended_rate"]
            blended_by_key[(level, key)] = expected
            cells.append(
                {
                    "level": level,
                    "key": dict(zip(dimensions[:level], key)),
                    "key_path": list(key),
                    "member_count": bucket["member_count"],
                    "earned_member_years": round(bucket["earned_member_years"], 4),
                    "actual_claims": round(bucket["actual_claims"], 2),
                    "own_rate": round(own, 2) if own is not None else None,
                    "complement_rate": round(complement, 2) if complement is not None else None,
                    "credibility": blend["credibility"],
                    "expected_cost": round(expected, 2) if expected is not None else None,
                    "relativity": relativity(expected, book_rate, min_relativity, max_relativity),
                }
            )

    cells.sort(key=lambda c: (c["level"], c["key_path"]))
    return {
        "dimensions": list(dimensions),
        "age_bands": [[low, high] for low, high in bands],
        "full_credibility_member_years": full_credibility_member_years,
        "book": {
            "member_count": book["member_count"] if book else 0,
            "earned_member_years": round(book["earned_member_years"], 4) if book else 0.0,
            "actual_claims": round(book["actual_claims"], 2) if book else 0.0,
            "burning_cost": round(book_rate, 2) if book_rate is not None else None,
        },
        "levels": [
            {
                "level": level,
                "dimensions": list(dimensions[:level]),
                "cell_count": sum(1 for c in cells if c["level"] == level),
            }
            for level in range(1, len(dimensions) + 1)
        ],
        "cells": cells,
    }


def build_cube_index(cube: dict) -> Dict[Tuple[int, Tuple[str, ...]], dict]:
    """Cells keyed for lookup. Built separately from the cube itself so
    the cube stays a plain JSON-serializable structure the API can return
    unchanged, while repeated per-member pricing doesn't rescan the list.
    """
    return {(c["level"], tuple(c["key_path"])): c for c in cube["cells"]}


def expected_cost_for_member(
    member: dict,
    cube: dict,
    index: Optional[Dict[Tuple[int, Tuple[str, ...]], dict]] = None,
) -> dict:
    """What one member is expected to cost in claims over a full year.

    Walks from the most specific cell the member maps to back toward the
    book, taking the first cell that has a blended rate at all. Because
    every cell is already blended toward its parent, the first hit is
    also the most specific defensible answer - there is no separate
    "is this cell credible enough?" threshold to tune, which is
    deliberate: a hard cutoff would make two nearly-identical members
    price very differently either side of it.

    `matched_level` and `credibility` come back with the figure so a
    quote can always show how much of the price is this member's own
    segment versus a broader fallback.
    """
    index = index if index is not None else build_cube_index(cube)
    dimensions = tuple(cube["dimensions"])
    bands = [tuple(b) for b in cube["age_bands"]]
    full_key = member_cube_key(member, dimensions, bands)

    for level in range(len(dimensions), 0, -1):
        cell = index.get((level, full_key[:level]))
        if cell and cell["expected_cost"] is not None:
            return {
                "expected_cost": cell["expected_cost"],
                "matched_level": level,
                "matched_key": cell["key"],
                "credibility": cell["credibility"],
                "own_rate": cell["own_rate"],
                "exposure_member_years": cell["earned_member_years"],
                "fell_back": level < len(dimensions),
            }

    # Nothing anywhere in the member's own path - fall back to the book
    # itself rather than returning nothing, so a census never prices at
    # zero just because a member sits in an entirely unpopulated corner.
    book_rate = cube["book"]["burning_cost"]
    return {
        "expected_cost": book_rate,
        "matched_level": 0,
        "matched_key": {},
        "credibility": 0.0,
        "own_rate": None,
        "exposure_member_years": cube["book"]["earned_member_years"],
        "fell_back": True,
    }


def expected_cost_for_census(
    census: List[dict],
    cube: dict,
) -> dict:
    """Expected annual claims cost for a whole census - the risk premium
    a case should be priced off, before any expense loading.

    Returns the per-member detail alongside the total so an underwriter
    can see which members drive the number and how much of it rests on
    fallbacks rather than the book's own experience for that exact
    segment. `fallback_member_count` is the honest health warning on the
    total: a case where most members fell back several levels is being
    priced off broad averages, and should be treated with more caution
    than the single total figure alone would suggest.
    """
    index = build_cube_index(cube)
    members: List[dict] = []
    total = 0.0
    rated = 0
    fallbacks = 0
    credibility_weighted = 0.0

    for member in census:
        priced = expected_cost_for_member(member, cube, index)
        members.append({**priced, "member": member})
        if priced["expected_cost"] is not None:
            total += priced["expected_cost"]
            rated += 1
            credibility_weighted += priced["credibility"] * priced["expected_cost"]
        if priced["fell_back"]:
            fallbacks += 1

    return {
        "expected_annual_claims": round(total, 2),
        "member_count": len(census),
        "rated_member_count": rated,
        "fallback_member_count": fallbacks,
        "average_expected_cost": round(total / rated, 2) if rated else None,
        # Exposure-weighted rather than a plain mean, so one thin member
        # can't drag the reported confidence of a large case around.
        "weighted_credibility": round(credibility_weighted / total, 4) if total else 0.0,
        "members": members,
    }
