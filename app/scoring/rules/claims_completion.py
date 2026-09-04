"""How much of a treatment month's cost the book has actually seen yet -
built from the book's own history, not assumed.

The house's standing IBNR rule is a flat 30-day tail on the paid run
rate. That answers "how much of what's already known is still unpaid",
which the book already carries separately as the outstanding figure. The
real IBNR question is different: how much of a recent month's cost has
not even been SUBMITTED yet, and that can only be read off how long
claims actually take to arrive - date_of_treatment against
date_reception, watched across the book's own history.

    completion(lag) = the book's own historical claims, on average, are
                       this fraction of the way to their final total
                       `lag` calendar months after treatment

A SERVICEPLAN account walked through by hand (Sep 2026) found this curve
climbs steeply for two months, then is essentially flat: ~48% at the
treatment month itself, ~94% one month later, ~98% two months later,
~99.9% by month four - so a completion-factor IBNR only really differs
from a flat rule on an account's most recent one or two months. Every
month beyond that, the two methods barely disagree.

Two failure modes the naive version of this hit, both worth stating
because they are not obvious until they bite:

  Averaging cohorts unweighted. A thin origin month (one from the book's
  own start-up period, a few thousand AED against neighbours in the
  hundreds of thousands) gets the same vote as a fully-populated one, and
  drags the whole curve down - on the same data, averaging cohorts
  equally read 96.5% where dollar-weighting read 99.9%. Every average
  here is weighted by the dollars behind it, not the count of cohorts.

  Trusting a cohort's own reception-lag data before it exists. Every
  claim ingested before date_reception was added to this schema has that
  field NULL regardless of when it was actually received - a PAID claim
  with no reception date on file was certainly received, we just do not
  know when. Silently treating that as "still outstanding" would invent
  IBNR that is not there. Those lines are excluded from curve-building
  and from monthly totals alike, not defaulted to anything.
"""
from collections import defaultdict
from datetime import date as date_cls
from typing import Dict, List, Optional, Sequence

#: A claim LINE at or above this is a candidate for exclusion from a
#: "typical month" - the same AED 50,000 line large-loss analysis and the
#: pricing options table already use, so "large claim" cannot come to
#: mean two different amounts depending on which screen is open.
from app.scoring.rules.renewal_scenarios import DEFAULT_LARGE_CLAIM_THRESHOLD

#: How many months a treatment-month cohort must have had to develop
#: before its own current total is trusted as a stand-in for "ultimate".
#: The curve is essentially flat by month 4 in the data this was built
#: from, but a cohort right at that edge is still a thin proxy for its
#: own future - six months gives it real room to have actually finished
#: arriving, not just entered the flat part of its own curve.
DEFAULT_MIN_MATURITY_MONTHS = 6

#: A cohort below this many AED is excluded from curve-building outright,
#: on top of being dollar-weighted. Dollar-weighting alone makes a thin
#: cohort's VOTE negligible; this additionally keeps a thin, possibly
#: anomalous month (a book's own start-up period, a handful of migrated
#: rows) from being read as a genuine data point at all. The book this
#: was built from had a cohort at AED 2,222 sitting flat at 76.8%
#: completion for over 500 days straight - not a real reserving signal,
#: a data artifact.
DEFAULT_MIN_COHORT_AMOUNT = 10_000.0

#: Lag beyond which the curve is not recomputed further - it is already
#: flat well before this, and reading further out only thins the cohort
#: sample available at each additional lag point.
DEFAULT_MAX_LAG_MONTHS = 10


def _month_key(d: date_cls) -> tuple:
    return (d.year, d.month)


def _month_diff(a: tuple, b: tuple) -> int:
    return (b[0] - a[0]) * 12 + (b[1] - a[1])


def completion_curve(
    claims: Sequence[dict],
    as_of: date_cls,
    min_maturity_months: int = DEFAULT_MIN_MATURITY_MONTHS,
    min_cohort_amount: float = DEFAULT_MIN_COHORT_AMOUNT,
    max_lag_months: int = DEFAULT_MAX_LAG_MONTHS,
) -> Dict[str, object]:
    """The book's own completion curve: at each lag, what dollar-weighted
    share of a treatment month's eventual total has typically been
    received by then.

    `claims` is any book's worth of claim dicts carrying date_of_treatment
    and date_reception - the whole portfolio, not one account, because a
    single 100-400 life group does not have enough of its own claims
    history to build a trustworthy curve. This is deliberately the same
    imprecision the house's flat 30-day tail already carries (one rule
    for every account) - the improvement here is in the SHAPE of the
    rule, not in making it account-specific, which would need far more
    history than any one group holds.

    Every mature cohort's OWN current total stands in for its ultimate -
    standard practice once a cohort is old enough that its curve has
    gone flat, but still an approximation: a cohort at exactly
    `min_maturity_months` old has not necessarily finished arriving, only
    entered the flat part of its own development.

    Returns {"points": [{"lag_months", "completion", "dollars_behind",
    "cohort_count"}, ...], "cohorts_used": [...], "insufficient_data":
    bool}. insufficient_data is true when fewer than two mature, credible
    cohorts exist - not enough to average, so nothing is fabricated from
    one data point.
    """
    origin_amount: Dict[tuple, float] = defaultdict(float)
    origin_lag_amount: Dict[tuple, Dict[int, float]] = defaultdict(lambda: defaultdict(float))

    for claim in claims:
        treated = claim.get("date_of_treatment")
        received = claim.get("date_reception")
        # No reception date is not "not yet received" here - it is
        # frequently "received before this field existed to record it".
        # Only claims carrying BOTH dates say anything about how long
        # reception actually takes.
        if not treated or not received:
            continue
        amount = claim.get("final_amount") or 0.0
        origin = _month_key(treated)
        lag = max(0, _month_diff(origin, _month_key(received)))
        origin_amount[origin] += amount
        origin_lag_amount[origin][lag] += amount

    as_of_month = _month_key(as_of)
    mature = sorted(
        origin for origin in origin_amount
        if _month_diff(origin, as_of_month) >= min_maturity_months
        and origin_amount[origin] >= min_cohort_amount
    )

    if len(mature) < 2:
        return {"points": [], "cohorts_used": [], "insufficient_data": True}

    points = []
    for lag in range(0, max_lag_months + 1):
        numerator = 0.0
        denominator = 0.0
        cohort_count = 0
        for origin in mature:
            # A cohort younger than this lag has not reached it yet at
            # the evaluation date - it contributes to earlier lag points
            # only, not to one it has not lived to see.
            if _month_diff(origin, as_of_month) < lag:
                continue
            ultimate = origin_amount[origin]
            received_by_lag = sum(
                origin_lag_amount[origin].get(l, 0.0) for l in range(0, lag + 1)
            )
            numerator += received_by_lag
            denominator += ultimate
            cohort_count += 1
        if denominator <= 0:
            continue
        points.append({
            "lag_months": lag,
            "completion": round(numerator / denominator, 4),
            "dollars_behind": round(denominator, 2),
            "cohort_count": cohort_count,
        })

    return {
        "points": points,
        "cohorts_used": [f"{y}-{m:02d}" for y, m in mature],
        "insufficient_data": False,
    }


def _completion_at(curve: Dict[str, object], lag: int) -> Optional[float]:
    """The curve's own completion for this lag, or its last known point
    for anything beyond what was computed - the curve is flat there in
    every case this was built from, and extrapolating flat is safer than
    extrapolating nothing.
    """
    points = curve.get("points") or []
    if not points:
        return None
    for point in points:
        if point["lag_months"] == lag:
            return point["completion"]
    # Beyond the deepest computed point: hold at the last one rather than
    # refuse an answer for an account whose oldest month happens to run
    # a little past max_lag_months.
    return points[-1]["completion"] if lag > points[-1]["lag_months"] else points[0]["completion"]


def completion_adjusted_monthly(
    claims: Sequence[dict],
    as_of: date_cls,
    curve: Dict[str, object],
    exclude_claim_ids: Sequence[str] = (),
) -> List[dict]:
    """One account's own treatment months, each true-up'd by the book's
    completion curve.

    Only claims carrying a reception date are counted as "received" here,
    for the same reason completion_curve excludes the rest when building
    itself: a paid claim from before date_reception existed has no lag to
    measure, so it cannot be told apart from a genuinely unreceived one
    without the date. Excluding it here (rather than counting it as
    received with an unknown lag) keeps this function and the curve that
    feeds it reading the same population of claims.

    `exclude_claim_ids` pulls named claim LINES out before the month is
    totalled - a large one-off event an underwriter has decided not to
    carry forward (see large_claims_by_month for finding candidates). It
    comes out before completion is applied, not after, so the completion
    factor used still describes what it always describes: how much of
    the claims actually being carried forward has been received.

    Returns one row per treatment month, oldest first: {"month" (YYYY-MM),
    "lag_months", "received", "completion", "completed"}. `completed` is
    `received` where the curve is insufficient to say more.
    """
    if curve.get("insufficient_data"):
        return []

    excluded = set(exclude_claim_ids)
    by_month: Dict[tuple, float] = defaultdict(float)
    for claim in claims:
        treated = claim.get("date_of_treatment")
        received = claim.get("date_reception")
        if not treated or not received:
            continue
        if claim.get("claim_id") in excluded:
            continue
        by_month[_month_key(treated)] += claim.get("final_amount") or 0.0

    as_of_month = _month_key(as_of)
    rows = []
    for om in sorted(by_month):
        lag = _month_diff(om, as_of_month)
        if lag < 0:
            continue
        completion = _completion_at(curve, lag)
        received = round(by_month[om], 2)
        completed = round(received / completion, 2) if completion else received
        rows.append({
            "month": f"{om[0]}-{om[1]:02d}",
            "lag_months": lag,
            "received": received,
            "completion": completion,
            "completed": completed,
        })
    return rows


def large_claims_by_month(
    claims: Sequence[dict],
    threshold: float = DEFAULT_LARGE_CLAIM_THRESHOLD,
) -> Dict[str, List[dict]]:
    """Candidate one-off events, grouped by the month they would otherwise
    inflate.

    Named as candidates, not decisions - the same reasoning
    renewal_scenarios.large_claim_total states: a member who reaches the
    same total through forty ordinary claims has an ordinary year, this
    only catches a single LINE large enough that one event moved a whole
    month. Whether it recurs is a clinical or membership question no
    threshold can answer (see the SERVICEPLAN case this was built from -
    two flagged lines in the same month, one a confirmed leaver, the
    other an open question nothing in the claims file could settle).
    """
    by_month: Dict[str, List[dict]] = defaultdict(list)
    for claim in claims:
        amount = claim.get("final_amount") or 0.0
        treated = claim.get("date_of_treatment")
        if amount < threshold or not treated:
            continue
        by_month[f"{treated.year}-{treated.month:02d}"].append({
            "claim_id": claim.get("claim_id"),
            "patient_id": claim.get("patient_id"),
            "amount": round(amount, 2),
            "diagnosis": claim.get("diagnosis_description"),
        })
    return dict(by_month)


def annual_claims_projection(
    monthly_rows: Sequence[dict],
    included_months: Optional[Sequence[str]] = None,
    total_policy_months: int = 12,
) -> dict:
    """A year's claims, built from however many of an account's own
    months are trusted, not a blanket average x 12.

    Each month named in `included_months` counts at its own
    completion-adjusted figure. Every month of the policy year NOT
    included - because it has not happened yet, or because an
    underwriter has judged it unrepresentative (a large one-off event,
    a month still too immature to trust even after completion) - is
    filled at the average of the months that ARE included, rather than
    being silently absent from a twelve-month total or forcing a choice
    between "use its real number" and "pretend the month does not
    exist".

    `included_months` defaults to every month `monthly_rows` has - the
    ordinary case where nothing needs to be excluded. Passing a shorter
    list is how a specific month (August, at 48% complete; March, with a
    large one-off claim) is left out of both the average and, unless it
    is also fed back in as its own row, the total.
    """
    by_month = {row["month"]: row["completed"] for row in monthly_rows}
    included = [m for m in (included_months if included_months is not None else list(by_month))
                if m in by_month]

    if not included:
        return {
            "included_months": [],
            "excluded_months": sorted(set(by_month) - set(included)),
            "average_included": None,
            "months_filled": total_policy_months,
            "annual_claims": None,
        }

    values = [by_month[m] for m in included]
    average = sum(values) / len(values)
    months_filled = max(0, total_policy_months - len(included))
    annual = sum(values) + average * months_filled

    return {
        "included_months": included,
        "excluded_months": sorted(set(by_month) - set(included)),
        "average_included": round(average, 2),
        "months_filled": months_filled,
        "annual_claims": round(annual, 2),
    }


def price_annual_claims(
    annual_claims: Optional[float],
    expiring_annual_premium: float,
    loading_pct: float,
    inflation_pct: float,
    minimum_increase_pct: Optional[float] = None,
) -> dict:
    """The annual claims figure this method built, carried onto a premium
    the way a burning cost always is.

    Trended as a straight PERCENTAGE of the money, deliberately not the
    house ladder's own convention of adding inflation in points to a loss
    ratio - that rule is defined for a RATIO, and this is a claims total.
    Multiplying it by anything else would be applying Method 1's formula
    to a number Method 1 never produces, which is exactly the mixed-basis
    mistake this whole build exists to avoid making a second way.

    The house floor still applies - a renewal is a renewal regardless of
    which of the house's methods priced it, and two methods that agreed
    on everything except whether the minimum increase binds would be
    worse than either alone.
    """
    if not annual_claims or expiring_annual_premium <= 0 or loading_pct >= 1:
        return {
            "annual_claims": round(annual_claims, 2) if annual_claims else annual_claims,
            "trended_claims": None, "required_premium": None,
            "renewal_increase_pct": None, "projected_loss_ratio": None,
            "floor_applied": False,
        }
    trended = annual_claims * (1 + inflation_pct)
    required = trended / (1 - loading_pct)
    floor_applied = False
    if minimum_increase_pct is not None:
        floor_premium = expiring_annual_premium * (1 + minimum_increase_pct)
        if required < floor_premium:
            required = floor_premium
            floor_applied = True
    return {
        "annual_claims": round(annual_claims, 2),
        "trended_claims": round(trended, 2),
        "required_premium": round(required, 2),
        "renewal_increase_pct": round((required / expiring_annual_premium - 1) * 100, 2),
        "projected_loss_ratio": round(trended / required, 4) if required else None,
        "floor_applied": floor_applied,
    }
