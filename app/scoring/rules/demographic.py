"""Census-driven demographic & group-composition risk.

Encodes the underwriting rules as specified:
  - Age bands: 0-17, 18-40, 41-59, 60-69, 70-99.
  - Married female aged 18-40 -> high risk (maternity exposure).
  - Female spouse -> higher risk than a male spouse.
  - A group with more employees and a higher male ratio -> favorable
    ("good account") -- lower risk.
  - Children are a favorable signal, EXCEPT infants aged <= 1, which carry a
    loading (newborn/delivery-related utilization).
  - Male employees individually skew favorable, reinforcing the group-level
    male-ratio discount.

Nationality-zone multipliers are passed in rather than hardcoded here: their
direction/magnitude isn't asserted by policy, so they live on the active
ScoringWeightSet and are tuned by the feedback/recalibration loop as real
case outcomes accumulate (see app/feedback/recalibration.py).

Two further interaction effects follow the exact same learned pattern rather
than asserting a fixed judgment call:
  - zone_maternity_multipliers: how much more (or less) a zone's maternity
    exposure should count, on top of the flat MATERNITY_LOADING.
  - zone_network_multipliers: how much more (or less) a zone matters when the
    case's benefit plan sits on an expensive/broad network tier. Scaled by
    network_tier_score (0-1, see app/reference/network_tiers.py) so a cheap
    in-country network mutes the effect and a Platinum network amplifies it.
Both start neutral (1.0) and are recalibrated from real outcomes.
"""
from statistics import mean
from typing import Dict, List, Optional

from app.reference.nationality_zones import ALL_ZONES, ZONE_MIDDLE_EAST

AGE_BANDS = [
    (0, 17, 0.70),
    (18, 40, 1.00),
    (41, 59, 1.30),
    (60, 69, 1.80),
    (70, 99, 2.50),
]

MATERNITY_AGE_MIN, MATERNITY_AGE_MAX = 18, 40
MATERNITY_LOADING = 1.25  # married female, age 18-40

SPOUSE_FEMALE_LOADING = 1.15  # female spouse vs. neutral male spouse

INFANT_AGE_MAX = 1
INFANT_LOADING = 1.30  # child aged <= 1 (newborn exposure)
CHILD_FAVORABLE_DISCOUNT = 0.85  # child aged > 1

MALE_EMPLOYEE_DISCOUNT = 0.95  # individual favorable nudge for male employees

GROUP_SIZE_DISCOUNT_CAP = 0.10
GROUP_SIZE_SCALE = 500  # employees needed to reach the full size discount
MALE_RATIO_DISCOUNT_CAP = 0.10
MALE_RATIO_BASELINE = 0.5
MAX_GROUP_FAVORABILITY_DISCOUNT = 0.20

# A small group's claims pool is far less predictable than a large one - a
# single bad claim swings the whole book. Below this headcount, a distinct
# loading is layered on top of (not instead of) the size-discount above, so a
# 5-employee group is priced up rather than merely missing out on the
# large-group discount that a 500-employee group gets.
SMALL_GROUP_THRESHOLD = 50
SMALL_GROUP_LOADING_CAP = 0.15  # loading at the smallest group sizes, phasing out to 0 at the threshold

# Distinct from the age bands above: an additional loading scaled by what
# fraction of the census sits above this age, stacked on top of each
# member's own age-band multiplier rather than replacing it. Defaults here
# only apply when the caller doesn't pass its own (see ScoringWeightSet's
# overage_age_threshold/overage_loading_cap, which is the normal path via
# app/scoring/engine.py) - kept adjustable rather than asserted, per the
# same underwriting-judgment-as-a-starting-point pattern as the zone
# multipliers below.
DEFAULT_OVERAGE_AGE_THRESHOLD = 50
DEFAULT_OVERAGE_LOADING_CAP = 0.15


def _age_band_multiplier(age: int) -> float:
    for low, high, multiplier in AGE_BANDS:
        if low <= age <= high:
            return multiplier
    return AGE_BANDS[-1][2]


def _member_risk_multiplier(
    member: dict,
    zone_multipliers: Dict[str, float],
    zone_maternity_multipliers: Optional[Dict[str, float]] = None,
    zone_network_multipliers: Optional[Dict[str, float]] = None,
    network_tier_score: float = 0.5,
) -> float:
    age = member.get("age")
    gender = member.get("gender")
    marital_status = (member.get("marital_status") or "").lower()
    relation = (member.get("relation") or "other").lower()

    multiplier = _age_band_multiplier(age) if age is not None else 1.0

    if relation == "child":
        if age is not None and age <= INFANT_AGE_MAX:
            multiplier *= INFANT_LOADING
        else:
            multiplier *= CHILD_FAVORABLE_DISCOUNT
    elif relation == "employee" and gender == "M":
        multiplier *= MALE_EMPLOYEE_DISCOUNT

    zone = member.get("nationality_zone") or ZONE_MIDDLE_EAST

    if gender == "F":
        if relation == "spouse":
            multiplier *= SPOUSE_FEMALE_LOADING
        if marital_status == "married" and age is not None and MATERNITY_AGE_MIN <= age <= MATERNITY_AGE_MAX:
            multiplier *= MATERNITY_LOADING
            if zone_maternity_multipliers:
                # `or 1.0`, not just a .get() default - a stored weight-set
                # row can have this zone's own key present but NULL (e.g. a
                # column added after that row already existed; see
                # app/db_migrate.py), which .get(zone, 1.0) would pass
                # straight through as None instead of falling back.
                multiplier *= zone_maternity_multipliers.get(zone) or 1.0

    multiplier *= zone_multipliers.get(zone) or 1.0

    if zone_network_multipliers:
        network_zone_multiplier = zone_network_multipliers.get(zone) or 1.0
        multiplier *= 1 + (network_zone_multiplier - 1) * network_tier_score

    return multiplier


def demographic_risk(
    census: List[dict],
    zone_multipliers: Optional[Dict[str, float]] = None,
    zone_maternity_multipliers: Optional[Dict[str, float]] = None,
    zone_network_multipliers: Optional[Dict[str, float]] = None,
    network_tier_score: float = 0.5,
    overage_age_threshold: int = DEFAULT_OVERAGE_AGE_THRESHOLD,
    overage_loading_cap: float = DEFAULT_OVERAGE_LOADING_CAP,
) -> dict:
    if not census:
        return {"score": 1.0, "group_size": 0}

    zone_multipliers = zone_multipliers or {zone: 1.0 for zone in ALL_ZONES}

    per_member_multipliers = [
        _member_risk_multiplier(
            m,
            zone_multipliers,
            zone_maternity_multipliers=zone_maternity_multipliers,
            zone_network_multipliers=zone_network_multipliers,
            network_tier_score=network_tier_score,
        )
        for m in census
    ]
    avg_member_risk = mean(per_member_multipliers)

    employees = [m for m in census if (m.get("relation") or "").lower() == "employee"]
    group_size = len(employees) or len(census)
    male_employees = sum(1 for m in employees if m.get("gender") == "M")
    male_ratio = (male_employees / len(employees)) if employees else 0.5

    size_discount = min(GROUP_SIZE_DISCOUNT_CAP, (group_size / GROUP_SIZE_SCALE) * GROUP_SIZE_DISCOUNT_CAP)
    male_ratio_discount = min(
        MALE_RATIO_DISCOUNT_CAP,
        max(0.0, male_ratio - MALE_RATIO_BASELINE) * 2 * MALE_RATIO_DISCOUNT_CAP,
    )
    group_favorability_discount = min(MAX_GROUP_FAVORABILITY_DISCOUNT, size_discount + male_ratio_discount)

    small_group_loading = SMALL_GROUP_LOADING_CAP * max(0.0, (SMALL_GROUP_THRESHOLD - group_size) / SMALL_GROUP_THRESHOLD)

    overage_count = sum(1 for m in census if m.get("age") is not None and m["age"] > overage_age_threshold)
    overage_fraction = overage_count / len(census)
    overage_loading = overage_loading_cap * overage_fraction

    score = avg_member_risk * (1 - group_favorability_discount) * (1 + small_group_loading) * (1 + overage_loading)

    ages = [m["age"] for m in census if m.get("age") is not None]
    infants = sum(1 for m in census if (m.get("relation") or "").lower() == "child" and (m.get("age") or 0) <= INFANT_AGE_MAX)
    favorable_children = sum(
        1 for m in census if (m.get("relation") or "").lower() == "child" and (m.get("age") or 0) > INFANT_AGE_MAX
    )
    maternity_risk_count = sum(
        1
        for m in census
        if m.get("gender") == "F"
        and (m.get("marital_status") or "").lower() == "married"
        and m.get("age") is not None
        and MATERNITY_AGE_MIN <= m["age"] <= MATERNITY_AGE_MAX
    )
    female_spouse_count = sum(1 for m in census if (m.get("relation") or "").lower() == "spouse" and m.get("gender") == "F")

    zone_counts = {zone: 0 for zone in ALL_ZONES}
    zone_maternity_counts = {zone: 0 for zone in ALL_ZONES}
    for m in census:
        # Anything not one of the current zones (missing, or a zone from
        # before the 4th zone was folded away) counts toward Middle East
        # rather than raising - see nationality_zones.py.
        zone = m.get("nationality_zone")
        resolved_zone = zone if zone in zone_counts else ZONE_MIDDLE_EAST
        zone_counts[resolved_zone] += 1
        if (
            m.get("gender") == "F"
            and (m.get("marital_status") or "").lower() == "married"
            and m.get("age") is not None
            and MATERNITY_AGE_MIN <= m["age"] <= MATERNITY_AGE_MAX
        ):
            zone_maternity_counts[resolved_zone] += 1
    zone_mix = {zone: round(count / len(census), 4) for zone, count in zone_counts.items()}
    # Fraction of the WHOLE census (not just maternity-risk members) that is
    # both maternity-risk and in each zone - lets recalibration learn whether
    # a zone's maternity exposure specifically predicts profitability,
    # distinct from that zone's plain headcount (zone_mix above).
    zone_maternity_mix = {zone: round(count / len(census), 4) for zone, count in zone_maternity_counts.items()}

    return {
        "score": round(score, 4),
        "group_size": group_size,
        "member_count": len(census),
        "avg_age": round(mean(ages), 1) if ages else None,
        "male_ratio_employees": round(male_ratio, 3),
        "group_favorability_discount": round(group_favorability_discount, 4),
        "small_group_loading": round(small_group_loading, 4),
        "overage_count": overage_count,
        "overage_fraction": round(overage_fraction, 4),
        "overage_loading": round(overage_loading, 4),
        "infant_count": infants,
        "favorable_children_count": favorable_children,
        "maternity_risk_count": maternity_risk_count,
        "female_spouse_count": female_spouse_count,
        "nationality_zone_mix": zone_mix,
        "zone_maternity_mix": zone_maternity_mix,
    }
