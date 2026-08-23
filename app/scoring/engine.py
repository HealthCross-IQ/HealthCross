from dataclasses import dataclass
from typing import List, Optional

from app.reference.network_tiers import DEFAULT_NETWORK_TIER_SCORE, network_tier_score as _network_tier_score
from app.scoring.rules.benefit_richness import benefit_richness_risk
from app.scoring.rules.claims_experience import claims_experience_risk
from app.scoring.rules.demographic import DEFAULT_OVERAGE_AGE_THRESHOLD, DEFAULT_OVERAGE_LOADING_CAP, demographic_risk
from app.scoring.rules.expected_cost_pricing import DEFAULT_TREND_PCT, price_census_at_expected_cost
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
    overage_age_threshold: int = DEFAULT_OVERAGE_AGE_THRESHOLD
    overage_loading_cap: float = DEFAULT_OVERAGE_LOADING_CAP


def _effective_weights(has_claims: bool, weights: ScoringWeights) -> dict:
    """The weights actually used for one scorecard run. Most New Business
    cases have no claims history at all to draw on (an incumbent insurer's
    claims either don't exist yet or aren't shared) - scoring them with a
    fixed claims-experience weight still in the blend means that slot's
    forced-neutral (1.0) score dilutes the real signal from demographic/
    benefits/industry down to whatever fraction of the total weight remains,
    even though there's nothing genuinely uninformative being contributed.
    When there are no claims at all, w_claims_experience is dropped
    entirely and redistributed proportionally across the other three, so
    they still carry their full combined weight rather than being diluted
    by a placeholder. Any real claims history (even a short, low-
    credibility one) keeps the configured weight as-is - claims_experience_
    risk's own credibility blending already fades a thin claims sample
    toward neutral without needing to touch the weights themselves.
    """
    if has_claims:
        return {
            "demographic": weights.w_demographic,
            "claims_experience": weights.w_claims_experience,
            "benefit_richness": weights.w_benefit_richness,
            "industry": weights.w_industry,
        }

    remaining = weights.w_demographic + weights.w_benefit_richness + weights.w_industry
    if remaining <= 0:
        # Degenerate configuration (all weight on claims alone) - nothing
        # sensible to redistribute to, so fall back to using it as-is.
        return {
            "demographic": weights.w_demographic,
            "claims_experience": weights.w_claims_experience,
            "benefit_richness": weights.w_benefit_richness,
            "industry": weights.w_industry,
        }

    total = weights.w_demographic + weights.w_claims_experience + weights.w_benefit_richness + weights.w_industry
    scale = total / remaining
    return {
        "demographic": weights.w_demographic * scale,
        "claims_experience": 0.0,
        "benefit_richness": weights.w_benefit_richness * scale,
        "industry": weights.w_industry * scale,
    }


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
    cube: Optional[dict] = None,
    trend_pct: float = DEFAULT_TREND_PCT,
    loading_pct: Optional[float] = None,
) -> dict:
    case_network_tier_score = _case_network_tier_score(benefit_plans)
    demo = demographic_risk(
        census,
        zone_multipliers=weights.zone_multipliers,
        zone_maternity_multipliers=weights.zone_maternity_multipliers,
        zone_network_multipliers=weights.zone_network_multipliers,
        network_tier_score=case_network_tier_score,
        overage_age_threshold=weights.overage_age_threshold,
        overage_loading_cap=weights.overage_loading_cap,
    )
    benefits = benefit_richness_risk(benefit_plans)
    claims_exp = claims_experience_risk(claims, member_count=len(census), estimated_annual_premium=estimated_annual_premium)
    industry_score = industry_risk(industry)
    effective_weights = _effective_weights(bool(claims), weights)

    raw_composite = (
        effective_weights["demographic"] * demo["score"]
        + effective_weights["claims_experience"] * claims_exp["score"]
        + effective_weights["benefit_richness"] * benefits["score"]
        + effective_weights["industry"] * industry_score
    )

    composite_score = max(
        0.0,
        min(100.0, (raw_composite - COMPOSITE_FLOOR) / (COMPOSITE_CEILING - COMPOSITE_FLOOR) * 100),
    )

    tier = _tier_for_score(composite_score)

    # The price, where the book has enough experience to produce one.
    #
    # `suggested_loading_pct` used to be (composite_score - 50) * 1.0 - a
    # unitless score read as a percentage, so a case scoring 70 was
    # "loaded 20%" only because 70 minus 50 is 20. Where a burning cost
    # cube is available the loading is now derived from what this census
    # is actually expected to cost relative to a book-average member (see
    # expected_cost_pricing), which is a statement about money rather
    # than about a scoring scale. The old formula remains as the fallback
    # for a case with no cube - a brand-new deployment with no book
    # uploaded yet - so scoring never simply stops working, but the
    # scorecard says which of the two produced the number.
    expected_cost = None
    loading_basis = "composite_score"
    if cube:
        expected_cost = price_census_at_expected_cost(
            census,
            cube,
            industry=industry,
            trend_pct=trend_pct,
            loading_pct=loading_pct or 0.0,
        )
        relativity = expected_cost.get("book_relativity")
        if relativity is not None:
            suggested_loading_pct = round(max(0.0, (relativity - 1.0) * 100), 1)
            loading_basis = "expected_cost"
        else:
            suggested_loading_pct = round(max(0.0, (composite_score - 50) * 1.0), 1)
    else:
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
            # Carried inside details (a JSON column) rather than as its own
            # top-level field so a saved Scorecard keeps the whole price
            # build-up without a schema migration - the build-up is what
            # makes the number defensible later, and a stored scorecard
            # that kept only the final figure would lose exactly that.
            "expected_cost": expected_cost,
            "loading_basis": loading_basis,
            "demographic": demo,
            "benefit_richness": benefits,
            "claims_experience": claims_exp,
            "industry_multiplier": industry_score,
            "network_tier_score": round(case_network_tier_score, 4),
            # The weights ACTUALLY applied to this scorecard (see
            # _effective_weights) - not necessarily the same as the
            # ScoringWeightSet's configured values, when claims were
            # unavailable and claims_experience's weight was redistributed.
            "weights_used": {
                "demographic": round(effective_weights["demographic"], 4),
                "claims_experience": round(effective_weights["claims_experience"], 4),
                "benefit_richness": round(effective_weights["benefit_richness"], 4),
                "industry": round(effective_weights["industry"], 4),
                "zone_multipliers": weights.zone_multipliers,
                "zone_maternity_multipliers": weights.zone_maternity_multipliers,
                "zone_network_multipliers": weights.zone_network_multipliers,
            },
        },
    }
