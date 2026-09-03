"""What each price on the table would actually do.

An underwriter does not choose between percentages, they choose between
premiums - the technical one, the one the broker will carry, the one
that keeps the account. The question that separates them is the same for
all of them and is almost never on the page: at this premium, where does
the loss ratio land?

    projected loss ratio = trended claims / the premium being quoted

Trended claims, not last year's: the premium covers next year, so the
comparison has to be against what next year is expected to cost. Using
the raw incurred figure flatters every option by the whole of inflation.

Two things follow from that one line and are worth stating because they
are what make the table decidable rather than decorative.

The TECHNICAL premium always projects to exactly (1 - loading). That is
what the ladder means: it is the premium at which trended claims consume
precisely the part of the premium not spoken for by expenses. Any option
priced below it eats the expense allowance, and the projected ratio says
how much of it.

The MINIMUM ACCEPTABLE premium is derived, not typed. It is the lowest
premium at which the account still lands inside the house's own maximum
loss ratio - so it moves with the account's claims rather than being a
number somebody remembers.
"""
from typing import List, Optional, Sequence

from app.scoring.rules.experience_pricing import HOUSE_TARGET_LOSS_RATIO
from app.scoring.rules.renewal_scenarios import DEFAULT_LARGE_CLAIM_THRESHOLD

DECISION_ACCEPT = "accept"
DECISION_REVIEW = "review"
DECISION_REJECT = "reject"

#: How far below the minimum acceptable premium is still a conversation
#: rather than a refusal. Inside this band the account is close enough
#: that an underwriter can weigh the relationship against the shortfall;
#: below it, the price does not fund the risk and saying so is the whole
#: point of having a floor.
REVIEW_BAND_PCT = 0.10


def minimum_acceptable_premium(
    trended_claims: Optional[float],
    target_loss_ratio: float = HOUSE_TARGET_LOSS_RATIO,
) -> Optional[float]:
    """The lowest premium that still lands inside the house maximum.

    Derived from this account's own trended claims rather than typed, so
    it moves when the experience does. A remembered figure goes stale the
    moment the claims file is refreshed and nobody notices.
    """
    if not trended_claims or target_loss_ratio <= 0:
        return None
    return round(trended_claims / target_loss_ratio, 2)


def _decide(premium: Optional[float], minimum: Optional[float]) -> Optional[str]:
    if premium is None or minimum is None:
        return None
    if premium >= minimum:
        return DECISION_ACCEPT
    if premium >= minimum * (1 - REVIEW_BAND_PCT):
        return DECISION_REVIEW
    return DECISION_REJECT


def price_option(
    label: str,
    premium: Optional[float],
    expiring_annual_premium: float,
    trended_claims: Optional[float],
    minimum: Optional[float],
    note: Optional[str] = None,
    key: Optional[str] = None,
) -> dict:
    """One row: the premium, what it is against expiring, where the loss
    ratio lands at it, and whether that is writable."""
    projected = (
        round(trended_claims / premium, 4)
        if (trended_claims and premium) else None
    )
    return {
        "key": key or label.lower().replace(" ", "_"),
        "label": label,
        "note": note,
        "premium": round(premium, 2) if premium is not None else None,
        "change_pct": (
            round((premium / expiring_annual_premium - 1) * 100, 2)
            if (premium is not None and expiring_annual_premium) else None
        ),
        "projected_loss_ratio": projected,
        "decision": _decide(premium, minimum),
    }


def renewal_options(
    expiring_annual_premium: float,
    technical_premium: Optional[float],
    trended_claims: Optional[float],
    loading_pct: Optional[float] = None,
    target_loss_ratio: float = HOUSE_TARGET_LOSS_RATIO,
    quoted: Optional[Sequence[dict]] = None,
) -> dict:
    """The price points on the table, each with the loss ratio it lands on.

    `quoted` are the underwriter's own price points - {label, premium,
    note} - and they are priced by exactly the same projection as the
    technical one. A commercial number that was compared against a
    different denominator to the technical number would make the table
    unreadable in the only way that matters: you could not tell which
    option was better.
    """
    if expiring_annual_premium <= 0:
        raise ValueError("expiring_annual_premium must be positive.")

    minimum = minimum_acceptable_premium(trended_claims, target_loss_ratio)

    rows: List[dict] = [
        price_option(
            "Current premium", expiring_annual_premium, expiring_annual_premium,
            trended_claims, minimum, key="expiring",
            note="what the account pays today"),
        price_option(
            "Technical premium", technical_premium, expiring_annual_premium,
            trended_claims, minimum, key="technical",
            note="Method 1's own ask - the ladder, unadjusted"),
        price_option(
            "Minimum acceptable", minimum, expiring_annual_premium,
            trended_claims, minimum, key="minimum_acceptable",
            note=f"lands exactly on the {target_loss_ratio:.0%} house maximum"),
    ]
    for option in (quoted or []):
        rows.append(price_option(
            option["label"], option.get("premium"), expiring_annual_premium,
            trended_claims, minimum, note=option.get("note"),
            key=option.get("key")))

    return {
        "expiring_annual_premium": round(expiring_annual_premium, 2),
        "trended_claims": round(trended_claims, 2) if trended_claims else None,
        "target_loss_ratio": target_loss_ratio,
        "minimum_acceptable_premium": minimum,
        "loading_pct": loading_pct,
        # The technical premium's own projection, stated because it is not
        # a coincidence: it is (1 - loading) by construction, and it is
        # the line every other option is really being measured against.
        "technical_projected_loss_ratio": (
            round(1 - loading_pct, 4) if loading_pct is not None else None),
        "review_band_pct": REVIEW_BAND_PCT,
        "options": rows,
    }


def premium_build_up(
    expiring_annual_premium: float,
    incurred_claims: Optional[float],
    inflation_pts: float,
    loading_pct: float,
    required_premium: Optional[float] = None,
) -> List[dict]:
    """The ladder as a waterfall: expiring, then the three things that
    move it, ending on the required premium.

    Each step is the ACTUAL amount that step adds, so the bars sum to the
    total. A waterfall whose steps do not reach its own final bar is
    worse than no waterfall - it invites the reader to check, and the
    check fails.

    Inflation is expiring x points, because the ladder adds inflation to
    the LOSS RATIO in points; taking a percentage of the claims instead
    is the other formula, and on a loss-making account it is much larger.
    """
    if expiring_annual_premium <= 0 or incurred_claims is None:
        return []
    experience = incurred_claims - expiring_annual_premium
    inflation = expiring_annual_premium * inflation_pts
    before_loading = incurred_claims + inflation
    loading = before_loading / (1 - loading_pct) - before_loading
    total = before_loading + loading

    steps = [
        ("Expiring premium", expiring_annual_premium, "what the account pays today"),
        ("Claims experience", experience,
         "the account's own incurred claims against what it paid"),
        (f"Claim inflation ({inflation_pts:.1%})", inflation,
         "points of the expiring premium, the house convention"),
        (f"Expenses & risk loading ({loading_pct:.1%})", loading,
         "grossed up, not marked up - the loading is a share of the premium"),
    ]
    # The running total accumulates the ROUNDED steps, not the exact
    # ones. A reader checks the waterfall by adding up the numbers in
    # front of them, and those are the rounded ones - a total that is a
    # cent away from its own visible parts fails exactly that check.
    rows, running = [], 0.0
    for label, amount, note in steps:
        shown = round(amount, 2)
        running = shown if label == "Expiring premium" else round(running + shown, 2)
        rows.append({"label": label, "amount": shown, "running": running, "note": note})
    # Where the ask that gets quoted is not the one the experience built -
    # the house floor lifted it, or an underwriter overrode it - that
    # difference is a STEP, not a discrepancy between the bars and their
    # own total. Leaving it out is precisely the failure this waterfall
    # exists to avoid: the reader adds up what is in front of them and
    # lands somewhere other than the final bar.
    quoted = round(required_premium if required_premium is not None else total, 2)
    gap = round(quoted - running, 2)
    if abs(gap) >= 0.01:
        rows.append({
            "label": "House minimum" if gap > 0 else "Underwriter adjustment",
            "amount": gap,
            "running": quoted,
            "note": ("the account's own experience asks for less than the house floor"
                     if gap > 0 else "the ask being quoted, not the one the experience built"),
        })
    rows.append({"label": "Required premium", "amount": None,
                 "running": quoted, "note": "the ask being quoted"})
    return rows


#: A member whose own claims reach this in a term is a high-cost member -
#: the same line Portfolio Analysis's large-loss analysis leads with, so
#: "large claim" and "high-cost member" cannot come to mean two amounts.
HIGH_COST_MEMBER_THRESHOLD = DEFAULT_LARGE_CLAIM_THRESHOLD


def claims_performance(claims: Sequence[dict], member_count: Optional[int] = None) -> dict:
    """The claims table: what the year cost, and the shape of it.

    Frequency is claim LINES per member, and the claimant ratio is the
    share of members with any claim at all - a member with five claims
    counts five times in one and once in the other, which is the whole
    reason both are on the page. An account can be expensive because a
    few people are very ill or because everybody goes to the doctor, and
    those are different renewals.

    Chronic is read off each claim's own diagnosis chapter (see
    reference/diagnosis_classification), and is an ESTIMATE: a chapter
    blends ongoing conditions with one-off events, and no claims file
    marks a claim as chronic. It is labelled as such wherever it is
    shown.
    """
    from app.reference.diagnosis_classification import CHRONIC, classify_diagnosis_group
    from app.reference.icd10_chapters import icd10_chapter

    amounts = [c.get("final_amount") or 0.0 for c in claims]
    total = sum(amounts)
    by_member: dict = {}
    chronic = 0.0
    for claim in claims:
        amount = claim.get("final_amount") or 0.0
        member = claim.get("patient_id")
        if member:
            by_member[member] = by_member.get(member, 0.0) + amount
        chapter = (icd10_chapter(claim.get("diagnosis_code"))
                   or claim.get("medical_category") or "")
        if chapter and classify_diagnosis_group(chapter).get("classification") == CHRONIC:
            chronic += amount

    top_ten = sorted(amounts, reverse=True)[:10]
    high_cost = [m for m, v in by_member.items() if v >= HIGH_COST_MEMBER_THRESHOLD]

    return {
        "total_incurred": round(total, 2),
        "claim_count": len(claims),
        "distinct_claimants": len(by_member),
        "claim_frequency": round(len(claims) / member_count, 2) if member_count else None,
        "claimant_ratio": round(len(by_member) / member_count, 4) if member_count else None,
        "average_claim_cost": round(total / len(claims), 2) if claims else None,
        "largest_claim": round(max(amounts), 2) if amounts else None,
        "top_ten_claims": round(sum(top_ten), 2),
        "top_ten_share": round(sum(top_ten) / total, 4) if total else None,
        "chronic_claims": round(chronic, 2),
        "chronic_share": round(chronic / total, 4) if total else None,
        "high_cost_members": len(high_cost),
        "high_cost_threshold": HIGH_COST_MEMBER_THRESHOLD,
        "high_cost_incurred": round(sum(by_member[m] for m in high_cost), 2),
    }
