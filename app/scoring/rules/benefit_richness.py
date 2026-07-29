from typing import List

ROOM_TYPE_MULTIPLIER = {"ward": 0.9, "semi_private": 1.0, "private": 1.25}
NETWORK_MULTIPLIER = {"in_country": 0.9, "regional": 1.05, "worldwide": 1.25}


def _plan_richness(plan: dict) -> float:
    score = 1.0

    limit = plan.get("annual_limit") or 0
    if limit >= 1_000_000:
        score *= 1.25
    elif limit >= 500_000:
        score *= 1.10
    elif limit >= 100_000:
        score *= 1.0
    else:
        score *= 0.85

    deductible = plan.get("deductible") or 0
    if deductible == 0:
        score *= 1.10
    elif deductible < 500:
        score *= 1.0
    else:
        score *= 0.90

    # co_insurance_pct is the insurer's paid share; a higher share means more
    # of the cost risk sits with the insurer.
    insurer_share = plan.get("co_insurance_pct") or 100
    score *= 0.85 + (insurer_share / 100) * 0.30

    score *= ROOM_TYPE_MULTIPLIER.get(plan.get("room_type") or "ward", 1.0)
    score *= NETWORK_MULTIPLIER.get(plan.get("network_type") or "in_country", 1.0)

    if plan.get("maternity_covered"):
        score *= 1.10
    if plan.get("dental_covered"):
        score *= 1.03
    if plan.get("optical_covered"):
        score *= 1.02
    if plan.get("pre_existing_covered"):
        score *= 1.20
    if plan.get("chronic_covered", True):
        score *= 1.05

    return score


def benefit_richness_risk(plans: List[dict]) -> dict:
    if not plans:
        return {"score": 1.0, "plan_count": 0}

    weights = [(p.get("member_count") or 1) for p in plans]
    total_weight = sum(weights)
    weighted_score = sum(_plan_richness(p) * w for p, w in zip(plans, weights)) / total_weight

    return {"score": round(weighted_score, 4), "plan_count": len(plans)}
