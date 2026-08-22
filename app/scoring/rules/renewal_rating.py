"""Renewal-increase calculation for existing-business cases with a claims
ledger and a known current premium - distinct from
app/scoring/rules/claims_projection.py's burning-cost method, which rescales
a REPORT's population-level experience onto a NEW group's census. A
renewal doesn't need that rescaling: it's the same group's own claims
experience being compared against its own current premium.

Both scorecard methods start from the SAME "Incurred Claims" base -
Paid + Outstanding (this group's own trailing full months from the claims
ledger, averaged and annualized - see
app.api.routes_analysis._case_renewal_rating) + IBNR (a flat load,
DEFAULT_IBNR_PCT, folded in BEFORE either method sees the figure, so
"annualized_incurred_claims" passed into calculate_renewal_rating is
already the true incurred figure, not just paid/outstanding). From there:

  Method A ("Standard"/Gross Loss Ratio): incurred claims, trended for
  inflation, then grossed up for the commission/OPEX loading by division
  - the full experience-based figure, un-shaded.

  Method B ("Burning Cost"): the SAME trended incurred claims, but
  additionally weighted by a credibility factor (DEFAULT_CREDIBILITY_PCT,
  matching the credibility step in app/scoring/rules/claims_projection.py's
  own burning-cost method) before grossing up - a partial-credibility
  view of the same experience, rather than a second IBNR guess bolted onto
  Method A.
"""
from dataclasses import dataclass
from typing import List, Optional

DEFAULT_INFLATION_PCT = 0.075
DEFAULT_LOADING_PCT = 0.33
DEFAULT_IBNR_PCT = 0.10  # Folded into the shared incurred-claims base BEFORE either method - see _case_renewal_rating.
DEFAULT_CREDIBILITY_PCT = 0.90  # Method B (Burning Cost) only - see calculate_renewal_rating_two_methods.

# The renewal loading (33%) isn't one fee - it's broker commission, TPA
# administration, HealthCross's own margin, and QIC's (the carrier's) own
# margin bundled together - matching a real acquisition-cost breakdown
# (Brokerage 15% + TPA 6.5% + HC 6.5% + QIC Margin 5%). These four sum to
# exactly DEFAULT_LOADING_PCT, so a case that hasn't set its own
# tpa_fee_pct/commission_pct/hc_fee_pct/qic_fee_pct still reproduces the
# same required_premium calculate_renewal_rating always has, just broken
# into its real named pieces for display - see premium_component_breakdown.
DEFAULT_TPA_FEE_PCT = 0.065
DEFAULT_COMMISSION_PCT = 0.15
DEFAULT_HC_FEE_PCT = 0.065
DEFAULT_QIC_FEE_PCT = 0.05

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
    ibnr_pct: float = DEFAULT_IBNR_PCT  # Paid+Outstanding -> Incurred - applies equally to both methods.
    credibility_pct: float = 1.0  # Method B (Burning Cost) only - 1.0 is a no-op, reproducing Method A exactly.


def calculate_renewal_rating(
    annualized_incurred_claims: float,
    current_annual_premium: float,
    assumptions: Optional[RenewalRatingAssumptions] = None,
) -> dict:
    """annualized_incurred_claims is Paid + Outstanding (this group's own
    trailing full months from the claims ledger, averaged and annualized -
    see app.api.routes_analysis._case_renewal_rating for where that base
    figure comes from) - an IBNR load (assumptions.ibnr_pct, default 10%)
    is applied here to turn that into a true INCURRED figure
    (Paid + Outstanding + IBNR) before anything else happens to it, per
    the standard actuarial definition.

    From that incurred base: trended for inflation, optionally weighted
    by a credibility factor (assumptions.credibility_pct, a straight
    multiplier - see app/scoring/rules/claims_projection.py's own
    burning-cost method for the same convention), then grossed up for the
    renewal loading. credibility_pct=1.0 (the default) is a no-op, so a
    caller not passing it gets the full, un-shaded experience-based
    figure - see calculate_renewal_rating_two_methods for Method A
    (credibility_pct=1.0) and Method B/"Burning Cost"
    (credibility_pct=DEFAULT_CREDIBILITY_PCT) side by side.
    """
    if annualized_incurred_claims < 0:
        raise ValueError("annualized_incurred_claims must not be negative.")
    if current_annual_premium <= 0:
        raise ValueError("current_annual_premium must be positive.")

    a = assumptions or RenewalRatingAssumptions()

    actual_loss_ratio = annualized_incurred_claims / current_annual_premium
    claims_with_ibnr = annualized_incurred_claims * (1 + a.ibnr_pct)
    trended_claims = claims_with_ibnr * (1 + a.inflation_pct)
    credible_claims = trended_claims * a.credibility_pct
    required_premium = credible_claims / (1 - a.loading_pct)
    renewal_increase_pct = (required_premium / current_annual_premium - 1) * 100

    return {
        "annualized_incurred_claims": round(annualized_incurred_claims, 2),
        "current_annual_premium": round(current_annual_premium, 2),
        "actual_loss_ratio": round(actual_loss_ratio, 4),
        "claims_with_ibnr": round(claims_with_ibnr, 2),
        "trended_claims": round(trended_claims, 2),
        "credible_claims": round(credible_claims, 2),
        "required_premium": round(required_premium, 2),
        "renewal_increase_pct": round(renewal_increase_pct, 2),
        "assumptions_used": {
            "inflation_pct": a.inflation_pct,
            "loading_pct": a.loading_pct,
            "ibnr_pct": a.ibnr_pct,
            "credibility_pct": a.credibility_pct,
        },
    }


def calculate_renewal_rating_two_methods(
    annualized_incurred_claims: float,
    current_annual_premium: float,
    inflation_pct: float = DEFAULT_INFLATION_PCT,
    loading_pct: float = DEFAULT_LOADING_PCT,
    ibnr_pct: float = DEFAULT_IBNR_PCT,
    credibility_pct: float = DEFAULT_CREDIBILITY_PCT,
) -> dict:
    """Both renewal scorecard methods from the SAME incurred-claims base
    (Paid + Outstanding + the SAME ibnr_pct load, applied equally to
    both), side by side:

      Method A ("Standard"/Gross Loss Ratio): the full incurred claims
      figure, trended and grossed up - no credibility shading.

      Method B ("Burning Cost"): the SAME trended incurred claims,
      additionally weighted by credibility_pct (default 90%) before
      grossing up - a partial-credibility view of the same experience,
      matching claims_projection.py's own burning-cost convention.

    The two are directly comparable since only the credibility step
    differs; passing credibility_pct=1.0 reproduces Method A exactly.
    """
    method_a = calculate_renewal_rating(
        annualized_incurred_claims, current_annual_premium,
        RenewalRatingAssumptions(inflation_pct=inflation_pct, loading_pct=loading_pct, ibnr_pct=ibnr_pct, credibility_pct=1.0),
    )
    method_b = calculate_renewal_rating(
        annualized_incurred_claims, current_annual_premium,
        RenewalRatingAssumptions(inflation_pct=inflation_pct, loading_pct=loading_pct, ibnr_pct=ibnr_pct, credibility_pct=credibility_pct),
    )
    return {
        "method_a": method_a,
        "method_b": method_b,
        "gap": round(method_b["required_premium"] - method_a["required_premium"], 2),
        "gap_pct": (
            round((method_b["required_premium"] / method_a["required_premium"] - 1) * 100, 2)
            if method_a["required_premium"] else None
        ),
    }


def resolve_fee_pcts(
    tpa_fee_pct: Optional[float] = None,
    commission_pct: Optional[float] = None,
    hc_fee_pct: Optional[float] = None,
    qic_fee_pct: Optional[float] = None,
) -> tuple:
    """Fills in any unset fee % with its DEFAULT_*_PCT counterpart, so a
    case that hasn't set its own fee split still reproduces
    DEFAULT_LOADING_PCT overall - shared by case_loading_pct (what feeds
    calculate_renewal_rating) and premium_component_breakdown (how the
    result is split for display), so the two stay consistent."""
    return (
        DEFAULT_TPA_FEE_PCT if tpa_fee_pct is None else tpa_fee_pct,
        DEFAULT_COMMISSION_PCT if commission_pct is None else commission_pct,
        DEFAULT_HC_FEE_PCT if hc_fee_pct is None else hc_fee_pct,
        DEFAULT_QIC_FEE_PCT if qic_fee_pct is None else qic_fee_pct,
    )


def case_loading_pct(
    tpa_fee_pct: Optional[float] = None,
    commission_pct: Optional[float] = None,
    hc_fee_pct: Optional[float] = None,
    qic_fee_pct: Optional[float] = None,
) -> float:
    """The actual total renewal loading implied by a case's own TPA
    Fee/Commission/HC Fee/QIC Fee split. Pass this as
    calculate_renewal_rating's loading_pct so required_premium/
    renewal_increase_pct reflect the case's own fee structure instead of
    always the 33% default - otherwise the fee fields only relabel an
    already-fixed number (see premium_component_breakdown) rather than
    actually changing it."""
    tpa, commission, hc, qic = resolve_fee_pcts(tpa_fee_pct, commission_pct, hc_fee_pct, qic_fee_pct)
    return tpa + commission + hc + qic


def premium_component_breakdown(
    renewal_result: dict,
    tpa_fee_pct: Optional[float] = None,
    commission_pct: Optional[float] = None,
    hc_fee_pct: Optional[float] = None,
    qic_fee_pct: Optional[float] = None,
) -> dict:
    """Splits a renewal_result (from calculate_renewal_rating) into its
    real named pieces - Risk Premium (the pure claims-funding cost), TPA
    Fee, Commission, HC Fee, and QIC Fee (the carrier's own margin on top
    of funding claims) - instead of one blended loading %, applied to
    both the existing and the proposed premium so they're directly
    comparable side by side.

    Risk Premium is always exactly trended_claims, whatever loading_pct
    the renewal_result was actually computed with - not a fixed %, so
    this stays correct even if a case overrides the default loading. The
    remaining loading amount (required_premium - trended_claims) is then
    split across TPA Fee/Commission/HC Fee/QIC Fee by their RELATIVE
    weights, not their absolute values - they don't need to sum to the
    loading_pct actually used, only to each other, so the four
    components always reconstruct the exact required_premium regardless
    of what's set.
    """
    tpa, commission, hc, qic = resolve_fee_pcts(tpa_fee_pct, commission_pct, hc_fee_pct, qic_fee_pct)
    total_weight = tpa + commission + hc + qic
    if total_weight <= 0:
        raise ValueError("tpa_fee_pct + commission_pct + hc_fee_pct + qic_fee_pct must sum to a positive value.")

    current_premium = renewal_result["current_annual_premium"]
    required_premium = renewal_result["required_premium"]
    trended_claims = renewal_result["trended_claims"]

    risk_premium_share = trended_claims / required_premium if required_premium else 0.0
    loading_share = 1 - risk_premium_share

    def _split(total: float) -> dict:
        loading_amount = total * loading_share
        return {
            "total": round(total, 2),
            "risk_premium": round(total * risk_premium_share, 2),
            "tpa_fee": round(loading_amount * tpa / total_weight, 2),
            "commission": round(loading_amount * commission / total_weight, 2),
            "hc_fee": round(loading_amount * hc / total_weight, 2),
            "qic_fee": round(loading_amount * qic / total_weight, 2),
        }

    return {
        "risk_premium_pct": round(risk_premium_share, 4),
        "tpa_fee_pct": round(tpa / total_weight * loading_share, 4),
        "commission_pct": round(commission / total_weight * loading_share, 4),
        "hc_fee_pct": round(hc / total_weight * loading_share, 4),
        "qic_fee_pct": round(qic / total_weight * loading_share, 4),
        "existing": _split(current_premium),
        "proposed": _split(required_premium),
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
