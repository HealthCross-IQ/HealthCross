"""Structural facts about HealthCross's own 4-product New Business ladder
and each TPA's own network richness ordering - used by the tier-ladder
comparison (app/scoring/rules/new_business_rating.py) to show a chosen
plan design priced under the tier above and below the one selected, across
every network belonging to the same TPA (never mixing in another TPA's
networks), ordered richest to leanest.

Unlike the insurer->tier suggestion (app/models/db_models.py's
InsurerTierPreference, admin-editable since underwriting judgment there
can shift) or the risk-weighting parameters (ScoringWeightSet), these
orderings are simple facts about which network/product is richer than
which, not risk judgments - so they stay as code constants here, the same
way the fixed 36-category DISPLAY_ORDER does in benefit_category_mapping.py.
"""
from typing import Dict, List

PRODUCT_TIER_ORDER: List[str] = ["Platinum", "Gold", "Silver", "Bronze"]

# Richest to leanest, per TPA - confirmed against HealthCross's own network
# naming.
NETWORK_RICHNESS_ORDER: Dict[str, List[str]] = {
    "MSH MENA": [
        "MSH Platinum",
        "MSH Comprehensive + Mediclinic",
        "MSH Comprehensive",
        "MSH Premium",
        "MSH Enhanced",
        "MSH Regular",
    ],
    "NAS Neuron": [
        "Comprehensive",
        "GN",
        "GN Excluding American & Mediclinic Group",
        "Restricted+++",
        "Restricted",
        "Super Restricted + Zulekha Group",
    ],
}


def tier_ladder(base_product: str) -> List[str]:
    """base_product plus one tier above and one tier below, bounded at the
    ends of PRODUCT_TIER_ORDER - e.g. Platinum (the top) returns
    [Platinum, Gold] since there's nothing richer to show above it.
    """
    if base_product not in PRODUCT_TIER_ORDER:
        return [base_product]
    idx = PRODUCT_TIER_ORDER.index(base_product)
    lo = max(0, idx - 1)
    hi = min(len(PRODUCT_TIER_ORDER), idx + 2)
    return PRODUCT_TIER_ORDER[lo:hi]
