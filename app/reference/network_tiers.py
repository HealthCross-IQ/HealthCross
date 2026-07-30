"""Maps a free-text insurer network name (e.g. "MSH Platinum", "Premium",
"Comprehensive+") to a 0-1 "richness" score - how expensive/broad the
network is, on a normalized scale - for use as the network side of the
nationality-zone x network-tier interaction learned in
app/feedback/recalibration.py (see zone_network_multipliers).

This is deliberately separate from benefit_richness.py's own
NETWORK_MULTIPLIER, which expects one of exactly 3 canonical values
("in_country"/"regional"/"worldwide") set by the generic spreadsheet
parser - real insurer PDF documents use their own marketing tier names
instead, which need keyword matching, not an exact-value lookup.
"""
from typing import List, Optional, Tuple

DEFAULT_NETWORK_TIER_SCORE = 0.5

# Checked in order; the first matching keyword wins. Ordered richest-first
# so a name mentioning multiple tiers (unlikely in practice) resolves to
# the richer one.
_NETWORK_TIER_KEYWORDS: List[Tuple[float, List[str]]] = [
    (1.0, ["platinum"]),
    (0.9, ["comprehensive+", "elite", "ultimate", "diamond"]),
    (0.75, ["comprehensive", "premier", "gold"]),
    (0.6, ["premium", "signature"]),
    (0.4, ["standard", "select", "silver", "regional"]),
    (0.15, ["essential", "basic", "bronze", "in_country", "in-country"]),
]


def network_tier_score(network_type: Optional[str]) -> float:
    """Returns 0-1, defaulting to the neutral midpoint (0.5) for an
    unrecognized or missing network name rather than guessing high or low.
    """
    if not network_type:
        return DEFAULT_NETWORK_TIER_SCORE
    normalized = network_type.lower()
    for score, keywords in _NETWORK_TIER_KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            return score
    return DEFAULT_NETWORK_TIER_SCORE
