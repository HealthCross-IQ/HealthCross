"""What the price should have been, and what actually went out.

The portal computes a price. Nothing has ever checked whether the price
the broker actually received matched it - and the gap between the two is
where a book's loss ratio is really made. A discount agreed in a meeting
never appears on any screen: the quote in the system still shows the
computed number, the issued document shows a different one, and nobody
reconciles them until the account renews badly a year later.

Four numbers, in the order they are built up:

  expected_claims  what these members should cost, from HealthCross's own
                   book. Pure risk, no loading, no margin.
  risk_price       expected claims carried forward for trend, industry
                   and nationality mix, then grossed up for expenses.
                   What the book says to charge.
  card_price       what the rate card charges for this plan design. What
                   HealthCross charges today.
  issued_price     what the broker actually received.

And the three gaps between them, which answer different questions:

  card vs risk     is the rate card adequate for THIS population?
  issued vs card   what was given away in the room.
  issued vs claims the implied loss ratio of the deal actually done -
                   the number that predicts next year's problem.

On discounts, because the arithmetic is not what it looks like. A
discount does not come off margin, it comes off the money that funds
claims. At a 26.5% loading, premium x 0.735 pays claims; cut the price
5% and expected claims do not move, so the implied loss ratio rises by
1/0.95 - 5.3% relative, not 5% absolute. An account at 95% goes to 100%.
That is worth seeing at the moment the discount is typed, not at renewal.

Pure functions over plain numbers - no ORM, no database.
"""
from typing import List, Optional

#: Below this the two prices are the same number written twice - a
#: rounding difference between the portal's own arithmetic and whatever
#: the quote document rounded to, not a decision anybody made.
MATERIAL_GAP_PCT = 0.005

DISCOUNT_GIVEN = "discount"
PREMIUM_ADDED = "loaded"
PRICE_HELD = "held"


def _pct_gap(actual: Optional[float], reference: Optional[float]) -> Optional[float]:
    if actual is None or not reference:
        return None
    return (actual - reference) / reference


def implied_loss_ratio(
    premium: Optional[float],
    expected_claims: Optional[float],
    loading_pct: float,
) -> Optional[float]:
    """Expected claims against the part of the premium that funds them.

    Measured against premium x (1 - loading), never against gross
    premium: the loading is already committed to commission, TPA, admin
    and fees, and cannot pay a claim. Comparing claims to gross premium
    flatters every account by exactly the loading.
    """
    if premium is None or expected_claims is None or premium <= 0:
        return None
    funding = premium * (1 - loading_pct)
    if funding <= 0:
        return None
    return expected_claims / funding


def discount_effect(
    card_price: Optional[float],
    issued_price: Optional[float],
    expected_claims: Optional[float],
    loading_pct: float,
) -> Optional[dict]:
    """What the difference between the computed price and the issued one
    did to the account's own loss ratio.

    Reported even when the movement was upward: a quote issued ABOVE the
    card is just as much a decision somebody made, and just as invisible
    on every other screen.
    """
    gap = _pct_gap(issued_price, card_price)
    if gap is None:
        return None

    before = implied_loss_ratio(card_price, expected_claims, loading_pct)
    after = implied_loss_ratio(issued_price, expected_claims, loading_pct)
    if abs(gap) < MATERIAL_GAP_PCT:
        direction = PRICE_HELD
    else:
        direction = DISCOUNT_GIVEN if gap < 0 else PREMIUM_ADDED

    return {
        "direction": direction,
        "pct": round(gap, 4),
        "amount_aed": round(issued_price - card_price, 2) if (issued_price is not None and card_price is not None) else None,
        "implied_loss_ratio_before": round(before, 4) if before is not None else None,
        "implied_loss_ratio_after": round(after, 4) if after is not None else None,
        "loss_ratio_movement": (
            round(after - before, 4) if (before is not None and after is not None) else None
        ),
    }


def compare_prices(
    expected_claims: Optional[float],
    risk_price: Optional[float],
    card_price: Optional[float],
    issued_price: Optional[float],
    loading_pct: float,
    member_count: Optional[int] = None,
) -> dict:
    """The four prices, the gaps between them, and what the issued one
    actually implies.

    Every figure is returned as None rather than zero when its input is
    missing. A case with no issued quote has not been discounted by 100%,
    and a zero here would say exactly that on screen.
    """
    per_member = lambda value: (round(value / member_count, 2) if (value and member_count) else None)

    return {
        "loading_pct": loading_pct,
        "member_count": member_count,
        "prices": {
            "expected_claims": round(expected_claims, 2) if expected_claims is not None else None,
            "risk_price": round(risk_price, 2) if risk_price is not None else None,
            "card_price": round(card_price, 2) if card_price is not None else None,
            "issued_price": round(issued_price, 2) if issued_price is not None else None,
        },
        "per_member": {
            "expected_claims": per_member(expected_claims),
            "risk_price": per_member(risk_price),
            "card_price": per_member(card_price),
            "issued_price": per_member(issued_price),
        },
        "gaps": {
            # Is the card adequate for this population? Positive means the
            # card charges more than the book says this group costs.
            "card_vs_risk_pct": _round(_pct_gap(card_price, risk_price)),
            # What was given away in the room.
            "issued_vs_card_pct": _round(_pct_gap(issued_price, card_price)),
            "issued_vs_risk_pct": _round(_pct_gap(issued_price, risk_price)),
        },
        "implied_loss_ratio": {
            "at_risk_price": _round(implied_loss_ratio(risk_price, expected_claims, loading_pct)),
            "at_card_price": _round(implied_loss_ratio(card_price, expected_claims, loading_pct)),
            "at_issued_price": _round(implied_loss_ratio(issued_price, expected_claims, loading_pct)),
        },
        "discount": discount_effect(card_price, issued_price, expected_claims, loading_pct),
    }


def _round(value: Optional[float]) -> Optional[float]:
    return round(value, 4) if value is not None else None


def issued_price_from_plans(plans: List[dict]) -> dict:
    """The issued quote's own premium, read off the document that was
    sent rather than recomputed.

    Summed across the document's categories, because a quote is issued as
    one number to the broker however many category tables sit behind it.
    Categories carrying no premium are counted separately rather than as
    zero - a table the parser could not read is not a category that was
    quoted free.
    """
    total = 0.0
    members = 0
    priced = 0
    unpriced: List[str] = []
    for plan in plans:
        premium = plan.get("gross_premium")
        if premium is None:
            unpriced.append(str(plan.get("category") or plan.get("plan_name") or "?"))
            continue
        total += premium
        members += plan.get("member_count") or 0
        priced += 1
    return {
        "issued_price": round(total, 2) if priced else None,
        "member_count": members or None,
        "categories_priced": priced,
        "categories_without_a_premium": unpriced,
    }
