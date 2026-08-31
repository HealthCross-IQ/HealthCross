"""Renewal-increase calculation for existing-business cases with a claims
ledger and a known current premium - distinct from
app/scoring/rules/claims_projection.py's burning-cost method, which rescales
a REPORT's population-level experience onto a NEW group's census. A
renewal doesn't need that rescaling: it's the same group's own claims
experience being compared against its own current premium.

Both scorecard methods reach an "Incurred Claims" figure - Paid + Outstanding
+ IBNR, the standard actuarial buildup - but compute IBNR differently, so
each method's incurred base is computed UPSTREAM (see
app.api.routes_analysis._case_renewal_rating) before calculate_renewal_rating
ever sees it; this module's own job is only the trend/credibility/loading
steps from there onward:

  Method A ("Gross Loss Ratio"): IBNR is a DYNAMIC reserve - this case's
  own Paid claims run rate (Paid / elapsed days so far * 30), the exact
  same convention as app/scoring/rules/portfolio_analysis.py's
  ibnr_for_member - projected over a 30-day unreported tail. Trended for
  inflation, then grossed up directly (credibility_pct=1.0, no shading) -
  the full experience-based figure.

  Method B ("Burning Cost"): IBNR is a flat load (DEFAULT_IBNR_PCT, 10%)
  on the same Paid+Outstanding base - the simpler, standard burning-cost
  assumption. The SAME trended incurred claims are then additionally
  weighted by a credibility factor (assumptions.credibility_pct, same
  mechanism as the credibility step in claims_projection.py's own
  burning-cost method) before grossing up.

  DEFAULT_CREDIBILITY_PCT is 1.0 (no shading) here, since a renewal's
  claims ledger is this exact group's own real, known experience - not a
  projection borrowed from someone else's report the way
  claims_projection.py's DEFAULT_CREDIBILITY_PCT=0.90 is. Still
  overridable per-case for a very small or short-history renewal an
  underwriter wants to discount.
"""
from dataclasses import dataclass
from typing import List, Optional

DEFAULT_INFLATION_PCT = 0.075
DEFAULT_LOADING_PCT = 0.33
DEFAULT_IBNR_PCT = 0.10  # Method B's flat IBNR load on Paid+Outstanding - see _case_renewal_rating for Method A's dynamic IBNR instead.
DEFAULT_CREDIBILITY_PCT = 1.0  # Method B (Burning Cost) only - see calculate_renewal_rating_two_methods.
# A renewal's own claims ledger is this exact group's real, known experience,
# not a projection borrowed from someone else's report - unlike New Business's
# burning-cost method (app/scoring/rules/claims_projection.py, its own
# separate DEFAULT_CREDIBILITY_PCT=0.90), where credibility genuinely
# matters since a DIFFERENT report's experience is being rescaled onto a
# new group. Still overridable per-case (e.g. a very small or short-history
# renewal an underwriter wants to discount), just not the default.

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

#: The house floor on a renewal. An account whose own experience asks for
#: less than this still renews at this, because a year of good experience
#: on a small population is mostly luck and the book's costs rise anyway.
#: Applied to the ASK, not to the experience: the figure the account's own
#: claims produce is always reported beside it, so nobody mistakes the
#: floor for a measurement.
MINIMUM_RENEWAL_INCREASE_PCT = 0.09


@dataclass
class RenewalRatingAssumptions:
    inflation_pct: float = DEFAULT_INFLATION_PCT
    loading_pct: float = DEFAULT_LOADING_PCT
    credibility_pct: float = 1.0  # Method B (Burning Cost) only - 1.0 is a no-op, reproducing Method A exactly.


def renewal_from_loss_ratio(
    loss_ratio: float,
    expiring_annual_premium: float,
    inflation_pts: float = DEFAULT_INFLATION_PCT,
    loading_pct: float = DEFAULT_LOADING_PCT,
    minimum_increase_pct: Optional[float] = MINIMUM_RENEWAL_INCREASE_PCT,
) -> dict:
    """The renewal price from the account's own loss ratio.

        trended loss ratio = loss ratio + inflation
        required premium   = expiring premium x trended / (1 - loading)

    NOMADA: 83.6% + 7.5 = 91.1%, over (1 - 23%) = 118.3% of the expiring
    premium, so 103,486 becomes 122,436 and the ask is +18.3%.

    A RATIO carried forward, not an absolute claims figure. That is the
    whole point, and getting it wrong cost a day: pricing off absolute
    claims means annualising them over elapsed days and then dividing by
    the expiring premium - but the claims were earned against the
    PRO-RATA premium, which is smaller. On NOMADA that denominator was
    14.5% larger than the one the claims actually ran against, and the
    account's 83.6% quietly became 73.0%, turning an increase of 18.3%
    into 1.9%. Carrying the ratio keeps both halves on one basis, because
    next year's claims will be measured against next year's premium in
    exactly the same proportion.

    Inflation is added in POINTS, the house convention: 83.6 + 7.5 =
    91.1, not 83.6 x 1.075 = 89.9. The two differ by more the higher the
    ratio, and on a loss-making account the points version is the
    prudent one.

    The loading is a share OF THE PREMIUM, so the part funding claims is
    premium x (1 - loading) - hence dividing rather than marking up.

    Finally the house floor (MINIMUM_RENEWAL_INCREASE_PCT): an account
    asking for less than 9% renews at 9% anyway. It is applied to the ASK
    and never folded back into the experience, because "this account
    needs 9%" and "this account needs 2% and the house floor is 9%" are
    different conversations and one number cannot tell them apart. Both
    come back - experience_increase_pct and renewal_increase_pct - with
    floor_applied saying which is being quoted. Pass None to switch the
    floor off for a what-if; 0.0 is a different setting meaning "never
    quote a decrease".
    """
    if expiring_annual_premium <= 0:
        raise ValueError("expiring_annual_premium must be positive.")
    if loading_pct >= 1:
        raise ValueError("loading_pct must be below 1.")
    trended = loss_ratio + inflation_pts
    experience_share = trended / (1 - loading_pct)

    # The floor is applied to the ask and never folded into the
    # experience. An account renewed at the floor and an account whose
    # own claims happen to need exactly the floor are different
    # conversations, and a single number cannot tell them apart - so both
    # figures come back and the caller can say which one it is quoting.
    # None turns the floor off; 0.0 is a real setting meaning "never
    # quote a decrease". Treating both as zero made a what-if with no
    # floor still refuse to show a reduction.
    if minimum_increase_pct is None:
        floored, required_share = False, experience_share
    else:
        floor_share = 1 + minimum_increase_pct
        floored = experience_share < floor_share
        required_share = max(experience_share, floor_share)
    return {
        "loss_ratio": round(loss_ratio, 4),
        "inflation_pts": inflation_pts,
        "trended_loss_ratio": round(trended, 4),
        "loading_pct": loading_pct,
        "expiring_annual_premium": round(expiring_annual_premium, 2),
        # What the account's own experience asks for, before the floor.
        "experience_share_of_expiring": round(experience_share, 4),
        "experience_required_premium": round(expiring_annual_premium * experience_share, 2),
        "experience_increase_pct": round((experience_share - 1) * 100, 2),
        # The house floor, and whether it is what is being quoted.
        "minimum_increase_pct": minimum_increase_pct,
        "floor_applied": floored,
        "required_share_of_expiring": round(required_share, 4),
        "required_premium": round(expiring_annual_premium * required_share, 2),
        "renewal_increase_pct": round((required_share - 1) * 100, 2),
    }


def calculate_renewal_rating(
    annualized_incurred_claims: float,
    current_annual_premium: float,
    assumptions: Optional[RenewalRatingAssumptions] = None,
    renewal_base: Optional[float] = None,
) -> dict:
    """annualized_incurred_claims is the ALREADY-incurred figure (Paid +
    Outstanding + IBNR) - IBNR is computed upstream by the caller (see
    app.api.routes_analysis._case_renewal_rating), since Method A and
    Method B each use a different IBNR convention (a dynamic Paid-claims
    run rate vs. a flat load - see this module's own docstring) and so
    arrive at this function with different incurred bases already.

    From that incurred base: trended for inflation, optionally weighted
    by a credibility factor (assumptions.credibility_pct, a straight
    multiplier - see app/scoring/rules/claims_projection.py's own
    burning-cost method for the same convention), then grossed up for the
    renewal loading. credibility_pct=1.0 (the default) is a no-op, so a
    caller not passing it gets the full, un-shaded experience-based
    figure.

    Two premiums, because the two questions have different denominators
    and conflating them was wrong in both directions.

    current_annual_premium is what the loss ratio is MEASURED against -
    the account's earned exposure, prorated for members who joined or
    left mid-year.

    renewal_base is what the increase is QUOTED against: the annualised
    expiring premium, a full year at current rates for the headcount
    that is actually renewing. A renewal covers a whole year, so pricing
    it off a prorated part-year figure understates the increase on any
    account that had mid-term joiners. NOMADA earned 90,347 against an
    annualised expiring premium of 103,486, and quoting the increase off
    the first prices a year nobody is buying.

    Defaults to current_annual_premium, so a caller that has only one
    premium is unchanged.
    """
    if annualized_incurred_claims < 0:
        raise ValueError("annualized_incurred_claims must not be negative.")
    if current_annual_premium <= 0:
        raise ValueError("current_annual_premium must be positive.")

    a = assumptions or RenewalRatingAssumptions()

    base = renewal_base if renewal_base and renewal_base > 0 else current_annual_premium

    actual_loss_ratio = annualized_incurred_claims / current_annual_premium
    trended_claims = annualized_incurred_claims * (1 + a.inflation_pct)
    credible_claims = trended_claims * a.credibility_pct
    required_premium = credible_claims / (1 - a.loading_pct)
    renewal_increase_pct = (required_premium / base - 1) * 100

    return {
        "annualized_incurred_claims": round(annualized_incurred_claims, 2),
        "current_annual_premium": round(current_annual_premium, 2),
        "renewal_base_premium": round(base, 2),
        "actual_loss_ratio": round(actual_loss_ratio, 4),
        "trended_claims": round(trended_claims, 2),
        "credible_claims": round(credible_claims, 2),
        "required_premium": round(required_premium, 2),
        "renewal_increase_pct": round(renewal_increase_pct, 2),
        "assumptions_used": {
            "inflation_pct": a.inflation_pct,
            "loading_pct": a.loading_pct,
            "credibility_pct": a.credibility_pct,
        },
    }


def calculate_renewal_rating_two_methods(
    incurred_claims_method_a: float,
    incurred_claims_method_b: float,
    current_annual_premium: float,
    inflation_pct: float = DEFAULT_INFLATION_PCT,
    loading_pct: float = DEFAULT_LOADING_PCT,
    credibility_pct: float = DEFAULT_CREDIBILITY_PCT,
    renewal_base: Optional[float] = None,
) -> dict:
    """Both renewal scorecard methods side by side, each from ITS OWN
    already-incurred claims base (see calculate_renewal_rating's
    docstring for why the two bases differ):

      Method A ("Gross Loss Ratio"): incurred_claims_method_a (dynamic
      IBNR), trended and grossed up directly - no credibility shading.

      Method B ("Burning Cost"): incurred_claims_method_b (flat-IBNR),
      trended, additionally weighted by credibility_pct (default 100% -
      a renewal's own ledger is real known experience, not a borrowed
      projection), then grossed up.
    """
    method_a = calculate_renewal_rating(
        incurred_claims_method_a, current_annual_premium,
        RenewalRatingAssumptions(inflation_pct=inflation_pct, loading_pct=loading_pct, credibility_pct=1.0),
        renewal_base=renewal_base,
    )
    method_b = calculate_renewal_rating(
        incurred_claims_method_b, current_annual_premium,
        RenewalRatingAssumptions(inflation_pct=inflation_pct, loading_pct=loading_pct, credibility_pct=credibility_pct),
        renewal_base=renewal_base,
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


def dynamic_ibnr_incurred_claims(
    total_paid: float,
    total_outstanding: float,
    elapsed_days: int,
    months_count: int,
) -> dict:
    """Method A's own incurred-claims base: Paid + Outstanding (this
    case's own trailing full months from the claims ledger - see
    app.api.routes_analysis._case_renewal_rating) + a DYNAMIC IBNR
    reserve, rather than a flat percentage - this case's own Paid claims
    run rate projected over a 30-day unreported tail (Paid / elapsed days
    so far * 30), the exact same convention as
    app/scoring/rules/portfolio_analysis.py's ibnr_for_member.

    The to-date total (Paid + Outstanding + IBNR) is then annualized the
    same way as the plain Paid+Outstanding figure elsewhere in this
    module - divided by the number of full months actually observed,
    times 12 - so Method A's annualization basis stays consistent with
    Method B's, only the IBNR step itself differs.
    """
    ibnr = (total_paid / elapsed_days * 30) if elapsed_days > 0 else 0.0
    incurred_to_date = total_paid + total_outstanding + ibnr
    scale = (12 / months_count) if months_count else 0.0
    annualized_incurred_claims = incurred_to_date * scale
    return {
        "total_paid": round(total_paid, 2),
        "total_outstanding": round(total_outstanding, 2),
        "elapsed_days": elapsed_days,
        "ibnr": round(ibnr, 2),
        "incurred_to_date": round(incurred_to_date, 2),
        # The IBNR as it lands in the annualised figure. Without this a
        # card printed the to-date IBNR (5,784.65 on NOMADA) beside
        # annualised everything else and its own column stopped adding
        # up: 78,825 + 5,785 = 84,610, under a printed 86,538. The
        # number a reader cannot reconcile is the number they stop
        # trusting.
        "annualized_ibnr": round(ibnr * scale, 2),
        "annualized_paid": round(total_paid * scale, 2),
        "annualized_outstanding": round(total_outstanding * scale, 2),
        "annualization_factor": round(scale, 4),
        "months_count": months_count,
        "annualized_incurred_claims": round(annualized_incurred_claims, 2),
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
