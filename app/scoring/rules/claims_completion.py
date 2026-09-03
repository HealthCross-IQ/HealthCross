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

    Returns one row per treatment month, oldest first: {"month" (YYYY-MM),
    "lag_months", "received", "completion", "completed"}. `completed` is
    `received` where the curve is insufficient to say more.
    """
    if curve.get("insufficient_data"):
        return []

    by_month: Dict[tuple, float] = defaultdict(float)
    for claim in claims:
        treated = claim.get("date_of_treatment")
        received = claim.get("date_reception")
        if not treated or not received:
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
