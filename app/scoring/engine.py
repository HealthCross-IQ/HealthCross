from dataclasses import dataclass
from typing import List, Optional

from app.reference.network_tiers import DEFAULT_NETWORK_TIER_SCORE, network_tier_score as _network_tier_score
from app.scoring.rules.benefit_richness import benefit_richness_risk
from app.scoring.rules.claims_experience import claims_experience_risk
from app.scoring.rules.demographic import demographic_risk
from app.scoring.rules.industry import industry_risk


@dataclass
class ScoringWeights:
    w_demographic: float = 0.30
    w_claims_experience: float = 0.35
    w_benefit_richness: float = 0.20
    w_industry: float = 0.15
    zone_multipliers: Optional[dict] = None
    zone_maternity_multipliers: Optional[dict] = None
    zone_network_multipliers: Optional[dict] = None


def _case_network_tier_score(benefit_plans: List[dict]) -> float:
    """Member-count-weighted blend of each existing-role plan's network tier
    richness (see app/reference/network_tiers.py), so a case split across a
    Platinum plan for most staff and a Standard plan for a few isn't scored
    as if everyone were on one or the other.
    """
    weighted_total = 0.0
    total_members = 0
    for plan in benefit_plans:
        weight = plan.get("member_count") or 1
        weighted_total += _network_tier_score(plan.get("network_type")) * weight
        total_members += weight
    if total_members == 0:
        return DEFAULT_NETWORK_TIER_SCORE
    return weighted_total / total_members


RISK_TIERS = [
    (40, "Preferred"),
    (60, "Standard"),
    (80, "Substandard"),
    (float("inf"), "Decline/Refer"),
]

# Raw composite is a weighted blend of ~0.5x-2.0x multipliers; map that onto
# a 0-100 scorecard where 1.0x (neutral risk) lands at 50.
COMPOSITE_FLOOR = 0.5
COMPOSITE_CEILING = 2.0


def _tier_for_score(score: float) -> str:
    for threshold, label in RISK_TIERS:
        if score < threshold:
            return label
    return "Decline/Refer"


def compute_scorecard(
    census: List[dict],
    benefit_plans: List[dict],
    claims: List[dict],
    industry: str,
    weights: ScoringWeights,
    estimated_annual_premium: Optional[float] = None,
) -> dict:
    case_network_tier_score = _case_network_tier_score(benefit_plans)
    demo = demographic_risk(
        census,
        zone_multipliers=weights.zone_multipliers,
        zone_maternity_multipliers=weights.zone_maternity_multipliers,
        zone_network_multipliers=weights.zone_network_multipliers,
        network_tier_score=case_network_tier_score,
    )
    benefits = benefit_richness_risk(benefit_plans)
    claims_exp = claims_experience_risk(claims, member_count=len(census), estimated_annual_premium=estimated_annual_premium)
    industry_score = industry_risk(industry)

    raw_composite = (
        weights.w_demographic * demo["score"]
        + weights.w_claims_experience * claims_exp["score"]
        + weights.w_benefit_richness * benefits["score"]
        + weights.w_industry * industry_score
    )

    composite_score = max(
        0.0,
        min(100.0, (raw_composite - COMPOSITE_FLOOR) / (COMPOSITE_CEILING - COMPOSITE_FLOOR) * 100),
    )

    tier = _tier_for_score(composite_score)
    suggested_loading_pct = round(max(0.0, (composite_score - 50) * 1.0), 1)

    return {
        "demographic_risk": demo["score"],
        "claims_experience_risk": claims_exp["score"],
        "benefit_richness_risk": benefits["score"],
        "industry_risk": industry_score,
        "credibility_factor": claims_exp["credibility"],
        "composite_score": round(composite_score, 2),
        "risk_tier": tier,
        "suggested_loading_pct": suggested_loading_pct,
        "details": {
            "demographic": demo,
            "benefit_richness": benefits,
            "claims_experience": claims_exp,
            "industry_multiplier": industry_score,
            "network_tier_score": round(case_network_tier_score, 4),
            "weights_used": {
                "demographic": weights.w_demographic,
                "claims_experience": weights.w_claims_experience,
                "benefit_richness": weights.w_benefit_richness,
                "industry": weights.w_industry,
                "zone_multipliers": weights.zone_multipliers,
                "zone_maternity_multipliers": weights.zone_maternity_multipliers,
                "zone_network_multipliers": weights.zone_network_multipliers,
            },
        },
    }
