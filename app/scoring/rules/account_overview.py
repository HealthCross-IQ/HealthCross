"""The two readings the account dashboard needs that nothing else
already computes: the shape of the year, and where the account sits
against the rest of the book.

Everything else on that screen is assembled from functions that already
exist - the loss ratio row from account_loss_ratio_rows, the encounter
split from utilization_by_encounter_type, the claimant ranking from
member_claim_ranking, the readings from underwriting_alerts. That is
deliberate: a dashboard is the single most tempting place to recompute a
figure "just for the summary", and a summary that disagrees with the
detail underneath it is worse than no summary.

So only what is genuinely missing lives here.
"""
from collections import defaultdict
from datetime import date as date_cls
from typing import Dict, List, Optional, Sequence

from app.scoring.rules.portfolio_analysis import _is_paid_claim_status


def claims_by_month(
    claims: Sequence[dict],
    months: Optional[int] = None,
) -> List[dict]:
    """Each month's claims, split paid vs outstanding.

    The split is the point, not the total. An account whose recent months
    are almost entirely outstanding has not necessarily got worse - it
    has claims the TPA has not settled yet, and those months will keep
    moving after the quote goes out. A single-colour monthly total hides
    exactly that, and it is the difference between "deteriorating" and
    "not yet known".

    Months with no claims inside the range are returned as zero rows
    rather than skipped, so the gap between two active months reads as a
    quiet month instead of closing up.
    """
    buckets: Dict[tuple, dict] = defaultdict(
        lambda: {"paid": 0.0, "outstanding": 0.0, "claim_count": 0}
    )
    for claim in claims:
        treated = claim.get("date_of_treatment")
        if not treated:
            continue
        bucket = buckets[(treated.year, treated.month)]
        amount = claim.get("final_amount") or 0.0
        if _is_paid_claim_status(claim.get("claim_status")):
            bucket["paid"] += amount
        else:
            bucket["outstanding"] += amount
        bucket["claim_count"] += 1

    if not buckets:
        return []

    ordered = sorted(buckets)
    first, last = ordered[0], ordered[-1]
    rows = []
    year, month = first
    while (year, month) <= last:
        bucket = buckets.get((year, month), {"paid": 0.0, "outstanding": 0.0, "claim_count": 0})
        rows.append(
            {
                "month": f"{year}-{month:02d}",
                "paid": round(bucket["paid"], 2),
                "outstanding": round(bucket["outstanding"], 2),
                "total": round(bucket["paid"] + bucket["outstanding"], 2),
                "claim_count": bucket["claim_count"],
            }
        )
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    return rows[-months:] if months else rows


def median(values: Sequence[float]) -> Optional[float]:
    """The middle value, or the mean of the middle two. Public because the
    renewal due list needs the book's median outstanding share too, and a
    second copy of four lines is still a second copy."""
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _percentile_rank(value: float, population: Sequence[float]) -> Optional[int]:
    """Where `value` sits in `population`, 1-100, worst-is-highest.

    Reported as a rank rather than a raw comparison because "71.4% vs
    248.6%" invites the reader to do the division and stop; "99th
    percentile" says the thing the division is for.
    """
    if not population:
        return None
    at_or_below = sum(1 for other in population if other <= value)
    return max(1, min(100, round(at_or_below / len(population) * 100)))


def book_position(row: Optional[dict], book_rows: Sequence[dict]) -> Optional[dict]:
    """This account against the book it belongs to.

    A loss ratio on its own is only readable by someone who already knows
    the book. 51.4% of incurred still outstanding is alarming and 18% is
    ordinary, and the screen cannot say which without the comparison, so
    the comparison is computed rather than left in the reader's head.

    The account's own row is left IN the book population. Taking it out
    would make each account's percentile depend on which account is being
    looked at, and on a 129-account book the difference is noise anyway.
    """
    if not row or not book_rows:
        return None

    loss_ratios = [r["gross_loss_ratio"] for r in book_rows if r.get("gross_loss_ratio") is not None]
    outstanding_shares = [
        r["outstanding"] / r["incurred_claims"]
        for r in book_rows
        if r.get("incurred_claims") and r.get("outstanding") is not None
    ]
    premium_per_life = [
        r["gross_premium"] / r["member_count"]
        for r in book_rows
        if r.get("member_count") and r.get("gross_premium") is not None
    ]

    members = row.get("member_count") or 0
    incurred = row.get("incurred_claims") or 0.0
    outstanding = row.get("outstanding")

    return {
        "accounts": len(book_rows),
        "loss_ratio": row.get("gross_loss_ratio"),
        "loss_ratio_percentile": (
            _percentile_rank(row["gross_loss_ratio"], loss_ratios)
            if row.get("gross_loss_ratio") is not None
            else None
        ),
        "book_median_loss_ratio": median(loss_ratios),
        "outstanding_share": (outstanding / incurred) if incurred and outstanding is not None else None,
        "book_median_outstanding_share": median(outstanding_shares),
        "premium_per_life": (row["gross_premium"] / members) if members and row.get("gross_premium") else None,
        "book_median_premium_per_life": median(premium_per_life),
        "claims_per_life": (incurred / members) if members else None,
    }


def data_window(claims: Sequence[dict]) -> Dict[str, Optional[date_cls]]:
    """First and last treatment date actually present, so a screen can say
    what the figures cover rather than implying they cover the term."""
    treated = [c["date_of_treatment"] for c in claims if c.get("date_of_treatment")]
    return {"from": min(treated) if treated else None, "to": max(treated) if treated else None}
