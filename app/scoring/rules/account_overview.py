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


def loss_ratio_by_period(rows: Sequence[dict]) -> List[dict]:
    """One row per policy year, oldest first, with the move against the
    year before it.

    An account that has renewed has a row per period on the book, and the
    dashboard showed only the latest - so a group that went from 68% to
    241% looked exactly like one that has always run at 241%. Those are
    completely different conversations: the first is a year that went
    wrong and may be one event, the second is an account that is
    structurally underpriced.

    The move is in POINTS, not as a percentage of a percentage. "Up 173
    points" is a fact about the account; "up 254%" is a fact about the
    arithmetic and invites being read as the premium change.
    """
    ordered = sorted(rows, key=lambda r: r.get("policy_start_date") or "")
    out: List[dict] = []
    previous: Optional[float] = None
    for row in ordered:
        loss_ratio = row.get("gross_loss_ratio")
        change = (
            round((loss_ratio - previous) * 100, 1)
            if (loss_ratio is not None and previous is not None) else None
        )
        out.append({
            "policy_start_date": row.get("policy_start_date"),
            "days": row.get("days"),
            "expired": row.get("expired"),
            "member_count": row.get("member_count"),
            "paid": row.get("paid"),
            "outstanding": row.get("outstanding"),
            "ibnr": row.get("ibnr"),
            "incurred_claims": row.get("incurred_claims"),
            "gross_premium": row.get("gross_premium"),
            "earned_premium": row.get("earned_premium"),
            "loss_ratio": loss_ratio,
            "net_loss_ratio": row.get("net_loss_ratio"),
            "loading_pct": row.get("loading_pct"),
            "loading_is_default": row.get("loading_is_default"),
            "change_pts": change,
            # A part year is not comparable with a full one on its face -
            # a term four months in has four months of claims against
            # four months of premium, which is a real ratio, but one
            # admission moves it in a way it cannot move a closed year.
            "part_year": bool(row.get("days")) and not row.get("expired"),
        })
        if loss_ratio is not None:
            previous = loss_ratio
    return out


def monthly_burning_cost(
    claims: Sequence[dict],
    members: Sequence[dict],
    policy_start: date_cls,
    policy_end: date_cls,
    up_to: Optional[date_cls] = None,
) -> List[dict]:
    """Claims per member per month, on the exposure actually carried.

    The denominator is the month's Exposed Risk Population, not a flat
    headcount: a member who joined on the 20th contributes a third of a
    life to that month, and a group that grew from 96 to 140 over its
    term did not carry 140 lives in month one. Dividing by the closing
    headcount flatters the early months and understates the later ones,
    which is the shape that makes a deteriorating account look steady.

    Months after `up_to` are dropped rather than shown at zero. A month
    the data does not reach has no claims in it, and a burning cost of
    nil is a statement about the account rather than about the export.
    """
    from app.scoring.rules.exposed_risk_population import monthly_exposed_risk_population

    by_month = {row["month"]: row for row in claims_by_month(claims)}
    limit = up_to or policy_end
    rows = []
    for erp_row in monthly_exposed_risk_population(members, policy_start, policy_end):
        year, month = erp_row["year"], erp_row["month"]
        if (year, month) > (limit.year, limit.month):
            continue
        key = f"{year}-{month:02d}"
        claimed = by_month.get(key, {})
        incurred = claimed.get("total", 0.0) or 0.0
        erp = erp_row["erp"]
        rows.append({
            "month": key,
            "erp": erp,
            "paid": claimed.get("paid", 0.0) or 0.0,
            "outstanding": claimed.get("outstanding", 0.0) or 0.0,
            "incurred": round(incurred, 2),
            "claim_count": claimed.get("claim_count", 0),
            "burning_cost": round(incurred / erp, 2) if erp else None,
        })
    return rows


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
