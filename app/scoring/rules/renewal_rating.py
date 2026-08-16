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
from typing import List, Optional

DEFAULT_INFLATION_PCT = 0.075
DEFAULT_LOADING_PCT = 0.28

# Below this many other cases with their own computed renewal rating, a
# percentile/median isn't credible - same "don't trust a rate built on
# too few data points" principle as
# app/scoring/rules/portfolio_analysis.py's MIN_CREDIBLE_MEMBER_YEARS,
# just case-count instead of member-years.
MIN_CREDIBLE_CASE_COUNT = 5


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


def _median(values: List[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def benchmark_case_against_book(this_result: dict, other_results: List[dict]) -> dict:
    """Where this case's own actual loss ratio sits among every OTHER
    case's own renewal rating (each computed the same way, via
    calculate_renewal_rating) - a case-to-case benchmark rather than an
    external market rate, since HealthCross has no such data source.
    Deliberately a snapshot, not a trend: most cases only have one year
    of claims history so far, so there's nothing to trend against yet -
    see app/api/routes_analysis.py's get_renewal_benchmark.

    Percentile counts a tie as half a rank rather than fully above or
    below (the standard convention for a value's percentile within a
    set). Flagged low_credibility below MIN_CREDIBLE_CASE_COUNT
    comparable cases - the benchmark still computes, it's just not
    trustworthy yet with a handful of cases behind it.
    """
    count = len(other_results)
    if count == 0:
        return {
            "comparable_case_count": 0,
            "percentile": None,
            "median_loss_ratio": None,
            "min_loss_ratio": None,
            "max_loss_ratio": None,
            "median_renewal_increase_pct": None,
            "other_loss_ratios": [],
            "low_credibility": True,
        }

    this_ratio = this_result["actual_loss_ratio"]
    other_ratios = [r["actual_loss_ratio"] for r in other_results]
    below = sum(1 for r in other_ratios if r < this_ratio)
    equal = sum(1 for r in other_ratios if r == this_ratio)
    percentile = round(100 * (below + 0.5 * equal) / count, 1)

    return {
        "comparable_case_count": count,
        "percentile": percentile,
        "median_loss_ratio": round(_median(other_ratios), 4),
        "min_loss_ratio": round(min(other_ratios), 4),
        "max_loss_ratio": round(max(other_ratios), 4),
        "median_renewal_increase_pct": round(_median([r["renewal_increase_pct"] for r in other_results]), 2),
        "other_loss_ratios": sorted(round(r, 4) for r in other_ratios),
        "low_credibility": count < MIN_CREDIBLE_CASE_COUNT,
    }
