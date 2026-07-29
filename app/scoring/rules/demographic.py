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
"""
from statistics import mean
from typing import Dict, List, Optional

from app.reference.nationality_zones import ALL_ZONES, ZONE_OTHER

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


def _age_band_multiplier(age: int) -> float:
    for low, high, multiplier in AGE_BANDS:
        if low <= age <= high:
            return multiplier
    return AGE_BANDS[-1][2]


def _member_risk_multiplier(member: dict, zone_multipliers: Dict[str, float]) -> float:
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

    if gender == "F":
        if relation == "spouse":
            multiplier *= SPOUSE_FEMALE_LOADING
        if marital_status == "married" and age is not None and MATERNITY_AGE_MIN <= age <= MATERNITY_AGE_MAX:
            multiplier *= MATERNITY_LOADING

    zone = member.get("nationality_zone") or ZONE_OTHER
    multiplier *= zone_multipliers.get(zone, 1.0)

    return multiplier


def demographic_risk(
    census: List[dict],
    zone_multipliers: Optional[Dict[str, float]] = None,
) -> dict:
    if not census:
        return {"score": 1.0, "group_size": 0}

    zone_multipliers = zone_multipliers or {zone: 1.0 for zone in ALL_ZONES}

    per_member_multipliers = [_member_risk_multiplier(m, zone_multipliers) for m in census]
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

    score = avg_member_risk * (1 - group_favorability_discount)

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
    for m in census:
        zone_counts[m.get("nationality_zone") or ZONE_OTHER] += 1
    zone_mix = {zone: round(count / len(census), 4) for zone, count in zone_counts.items()}

    return {
        "score": round(score, 4),
        "group_size": group_size,
        "member_count": len(census),
        "avg_age": round(mean(ages), 1) if ages else None,
        "male_ratio_employees": round(male_ratio, 3),
        "group_favorability_discount": round(group_favorability_discount, 4),
        "infant_count": infants,
        "favorable_children_count": favorable_children,
        "maternity_risk_count": maternity_risk_count,
        "female_spouse_count": female_spouse_count,
        "nationality_zone_mix": zone_mix,
    }
