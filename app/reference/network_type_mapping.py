"""Maps the real book's own NETWORKTYPE values (HealthCross_Members export)
onto the canonical MSH network names the New Business rate card actually
prices by (see app/reference/product_tiers.py's NETWORK_RICHNESS_ORDER) -
so Portfolio Analysis (app/scoring/rules/portfolio_analysis.py) can look
each member's real network up against the rate card.

"MSH INTL NETWORK" is a genuinely different product line (international
cover for groups based outside the UAE, confirmed with underwriting) -
there is no UAE rate-card equivalent to substitute, so it's deliberately
left unmapped (returns None) rather than guessed at, and Portfolio
Analysis must treat those members as out of the rate card's scope.
"""
from typing import Optional

_NETWORK_TYPE_MAP = {
    "platinum": "MSH Platinum",
    "comprehensive": "MSH Comprehensive",
    "premium": "MSH Premium",
    "enhanced": "MSH Enhanced",
    "regular": "MSH Regular",
    # Confirmed with underwriting: this book's "Essential" tier is priced
    # the same as Platinum.
    "essential": "MSH Platinum",
}

OUT_OF_SCOPE_NETWORK_TYPES = {"msh intl network"}


def map_network_type(raw_network_type: Optional[str]) -> Optional[str]:
    if not raw_network_type:
        return None
    normalized = raw_network_type.strip().lower()
    return _NETWORK_TYPE_MAP.get(normalized)


def is_out_of_scope_network_type(raw_network_type: Optional[str]) -> bool:
    if not raw_network_type:
        return False
    return raw_network_type.strip().lower() in OUT_OF_SCOPE_NETWORK_TYPES
