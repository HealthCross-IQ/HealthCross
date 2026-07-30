"""Renewal-increase calculation for existing-business cases with a claims
ledger and a known current premium - distinct from
app/scoring/rules/claims_projection.py's burning-cost method, which rescales
a REPORT's population-level experience onto a NEW group's census. A
renewal doesn't need that rescaling: it's the same group's own claims
experience being compared against its own current premium.

Method (as specified): actual loss ratio (annualized incurred claims over
current premium), trended for inflation, then grossed up for the
commission/OPEX loading the same way as the burning-cost method - by
division, not a multiplicative add-on - to get the required premium and
the renewal increase.
"""
from dataclasses import dataclass
from typing import Optional

DEFAULT_INFLATION_PCT = 0.075
DEFAULT_LOADING_PCT = 0.28


@dataclass
class RenewalRatingAssumptions:
    inflation_pct: float = DEFAULT_INFLATION_PCT
    loading_pct: float = DEFAULT_LOADING_PCT


def calculate_renewal_rating(
    annualized_incurred_claims: float,
    current_annual_premium: float,
    assumptions: Optional[RenewalRatingAssumptions] = None,
) -> dict:
    if annualized_incurred_claims < 0:
        raise ValueError("annualized_incurred_claims must not be negative.")
    if current_annual_premium <= 0:
        raise ValueError("current_annual_premium must be positive.")

    a = assumptions or RenewalRatingAssumptions()

    actual_loss_ratio = annualized_incurred_claims / current_annual_premium
    trended_claims = annualized_incurred_claims * (1 + a.inflation_pct)
    required_premium = trended_claims / (1 - a.loading_pct)
    renewal_increase_pct = (required_premium / current_annual_premium - 1) * 100

    return {
        "annualized_incurred_claims": round(annualized_incurred_claims, 2),
        "current_annual_premium": round(current_annual_premium, 2),
        "actual_loss_ratio": round(actual_loss_ratio, 4),
        "trended_claims": round(trended_claims, 2),
        "required_premium": round(required_premium, 2),
        "renewal_increase_pct": round(renewal_increase_pct, 2),
        "assumptions_used": {
            "inflation_pct": a.inflation_pct,
            "loading_pct": a.loading_pct,
        },
    }
