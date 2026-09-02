"""What the renewal costs, and what one member is worth to it.

An account's renewal price is usually decided by a handful of people.
Serviceplan's expiring year cost AED 2,127,850 across 114 active
members, and one of them was 522,042 of it - a quarter of the account in
a single life. The renewal is a different conversation depending on
whether that member is renewing, is leaving, or can be written with a
specific-condition exclusion, and none of those are visible in a total.

So this prices the account with any set of members held out, and reports
what holding them out was worth. Two rules keep it honest.

Excluding a member is a QUESTION, not an answer. The figure that comes
back is what the account would have cost without them - it is not a
price unless that member is genuinely not renewing, or their condition
is genuinely excluded. A high claimant who is renewing on the same terms
costs what they cost, and taking them off the page does not take them
off the risk. Every result says which members were held out so nobody
inherits a number without its condition.

Part months are not annualised as though they were whole. A claims
export cut mid-month ends on a month that is only partly there, and
averaging it in with full ones drags the run-rate down by however much
of the month is missing - which is exactly the direction that makes an
account look cheaper than it is.
"""
import calendar
from datetime import date as date_cls
from typing import Dict, List, Optional, Sequence, Tuple

from app.scoring.rules.renewal_rating import (
    DEFAULT_INFLATION_PCT,
    MINIMUM_RENEWAL_INCREASE_PCT,
    renewal_from_loss_ratio,
)

#: The claims inflation carried onto the expiring year's experience, in
#: POINTS added to the loss ratio - taken from the rating rather than
#: written again, because this panel and the renewal card sit on the same
#: screen and a second 7.5 that could drift from the first is exactly how
#: two premiums for one account get onto one page.
DEFAULT_TREND_PCT = DEFAULT_INFLATION_PCT


def monthly_totals(
    members: Sequence[dict],
    claims_by_beneficiary: Dict[str, List[dict]],
    windows: Dict[str, List[Tuple]],
    exclude: Sequence[str] = (),
) -> Dict[Tuple[int, int], float]:
    """Claims by calendar month, for the given members, inside their own
    exposure windows.
    """
    from app.scoring.rules.renewal_intake import claim_belongs_to_term

    held_out = set(exclude or ())
    totals: Dict[Tuple[int, int], float] = {}
    for member in members:
        beneficiary_id = member.get("beneficiary_id")
        if beneficiary_id in held_out:
            continue
        for claim in claims_by_beneficiary.get(beneficiary_id, ()):
            treated = claim.get("date_of_treatment")
            if not treated or not claim_belongs_to_term(beneficiary_id, treated, windows):
                continue
            key = (treated.year, treated.month)
            totals[key] = totals.get(key, 0.0) + (claim.get("final_amount") or 0.0)
    return dict(sorted(totals.items()))


def _month_end(year: int, month: int) -> date_cls:
    return date_cls(year, month, calendar.monthrange(year, month)[1])


def annualise(
    monthly: Dict[Tuple[int, int], float],
    data_to: Optional[date_cls] = None,
) -> dict:
    """A full year's claims from a part year's experience.

    Only whole months feed the average. The month the export was cut in
    is partly there and averaging it in understates the run-rate by
    whatever share of the month is missing - always downward, which is
    the direction that flatters an account.

    Reported alongside the figure: how many months it rests on, and which
    were dropped. A run-rate built on three months is not the same claim
    as one built on eleven, and the reader should not have to ask.
    """
    if not monthly:
        return {"annualised": None, "full_months": 0, "average_full_month": None,
                "months_used": [], "months_dropped": []}

    last = data_to or _month_end(*max(monthly))
    used, dropped = {}, []
    for (year, month), amount in monthly.items():
        if _month_end(year, month) <= last:
            used[(year, month)] = amount
        else:
            dropped.append(f"{year}-{month:02d}")

    if not used:
        # Everything is a part month - one month of data, cut mid-way.
        # Averaging that is not a run-rate, it is a guess.
        return {"annualised": None, "full_months": 0, "average_full_month": None,
                "months_used": [], "months_dropped": dropped,
                "note": "no complete month of experience to annualise from"}

    average = sum(used.values()) / len(used)
    return {
        "annualised": round(average * 12, 2),
        "average_full_month": round(average, 2),
        "full_months": len(used),
        "months_used": [f"{y}-{m:02d}" for y, m in sorted(used)],
        "months_dropped": dropped,
        "incurred_to_date": round(sum(monthly.values()), 2),
    }


def premium_for(expected_claims: Optional[float], loading_pct: float,
                target_loss_ratio: float) -> Optional[float]:
    """Grossed up, not marked up - the loading is a share of the premium,
    so the part that funds claims is premium x (1 - loading).
    """
    if not expected_claims or target_loss_ratio <= 0 or loading_pct >= 1:
        return None
    return round(expected_claims / target_loss_ratio / (1 - loading_pct), 2)


def member_claim_ranking(
    members: Sequence[dict],
    claims_by_beneficiary: Dict[str, List[dict]],
    windows: Dict[str, List[Tuple]],
    top: int = 15,
) -> List[dict]:
    """Who the account's cost actually is, worst first.

    Carries each member's share of the total and their monthly run, so a
    one-off event and a condition still being treated can be told apart -
    which is the whole of whether holding them out is defensible.
    """
    from app.scoring.rules.renewal_intake import claim_belongs_to_term

    rows = []
    grand_total = 0.0
    for member in members:
        beneficiary_id = member.get("beneficiary_id")
        total = 0.0
        months: Dict[Tuple[int, int], float] = {}
        diagnoses: Dict[str, float] = {}
        for claim in claims_by_beneficiary.get(beneficiary_id, ()):
            treated = claim.get("date_of_treatment")
            if not treated or not claim_belongs_to_term(beneficiary_id, treated, windows):
                continue
            amount = claim.get("final_amount") or 0.0
            total += amount
            months[(treated.year, treated.month)] = months.get((treated.year, treated.month), 0.0) + amount
            label = claim.get("diagnosis_description") or claim.get("diagnosis") or "Not stated"
            diagnoses[label] = diagnoses.get(label, 0.0) + amount
        if total <= 0:
            continue
        grand_total += total
        ordered = sorted(months.items())
        rows.append({
            "beneficiary_id": beneficiary_id,
            "relation": member.get("relation"),
            "age": member.get("age"),
            "gender": member.get("gender"),
            "incurred": round(total, 2),
            "monthly": [{"month": f"{y}-{m:02d}", "amount": round(v, 2)} for (y, m), v in ordered],
            "months_with_claims": len(ordered),
            # Still running, or finished? A member claiming in most of the
            # months they were exposed for is being treated, not unlucky,
            # and treatment renews with them.
            "top_diagnosis": max(diagnoses.items(), key=lambda kv: kv[1])[0] if diagnoses else None,
        })

    rows.sort(key=lambda r: -r["incurred"])
    for row in rows:
        row["share_of_claims"] = round(row["incurred"] / grand_total, 4) if grand_total else None
    return rows[:top]


def reprice(
    members: Sequence[dict],
    claims_by_beneficiary: Dict[str, List[dict]],
    windows: Dict[str, List[Tuple]],
    current_premium: Optional[float],
    loading_pct: Optional[float],
    loss_ratio: Optional[float] = None,
    incurred_to_date: Optional[float] = None,
    exclude: Sequence[str] = (),
    trend_pct: float = DEFAULT_TREND_PCT,
    data_to: Optional[date_cls] = None,
    minimum_increase_pct: Optional[float] = MINIMUM_RENEWAL_INCREASE_PCT,
) -> dict:
    """The renewal price with a set of members held out, beside the price
    with everybody in.

    Both are always returned. A figure produced by holding someone out is
    only a price if that member is genuinely not renewing or their
    condition is genuinely excluded, and showing it alone invites it to
    be quoted as though it were.

    Priced by the HOUSE LADDER, the same one the renewal card and the
    scenarios table use. It used to run its own formula - claims x (1 +
    trend), over a target loss ratio, over (1 - loading) - which is a
    third route to a renewal premium, and on Nomada it put 1,769,992 on
    the screen directly beside Method 1's 1,456,375. The panel's real
    subject is the DIFFERENCE between two prices, and a difference is
    only readable if both sides were arrived at the same way.

    It also takes the account's OWN loss ratio rather than deriving one.
    It used to annualise the claims itself, off complete months x 12,
    while the rating annualises by the exposure actually run - and on
    Nomada the same book claims came out at 1,048,000 one way and 905,373
    the other, putting 1,456,375 beside the rating's 1,384,300. Same
    account, same claims, same loading, two annualisations.

    So the ratio comes in and holding a member out reduces it by that
    member's SHARE of the account's claims. Share rather than amount
    because the question here is different from the scenarios panel's: a
    member who is not renewing takes their whole ongoing cost with them,
    where a catastrophic admission that is stripped happened once and is
    not annualised at all.

    A loading or a loss ratio of None withholds the premium rather than
    assuming one. The claimant ranking and the run-rate still come back,
    because what is missing is the fee split, not the account.
    """
    held_out = [m for m in members if m.get("beneficiary_id") in set(exclude or ())]
    kept = [m for m in members if m.get("beneficiary_id") not in set(exclude or ())]

    # The share of the account's own claims each subset carries. The
    # denominator is every member's in-term claims, so holding nobody out
    # is a share of exactly 1 and prices the account as the rating does.
    all_claims = (annualise(monthly_totals(members, claims_by_beneficiary, windows),
                            data_to).get("incurred_to_date") or 0.0)

    def price(group: Sequence[dict], excluded: Sequence[str] = ()) -> dict:
        monthly = monthly_totals(members, claims_by_beneficiary, windows, exclude=excluded)
        run_rate = annualise(monthly, data_to)
        subset_claims = run_rate.get("incurred_to_date") or 0.0
        kept_share = (subset_claims / all_claims) if all_claims else 1.0
        count = len(group)
        priced = (
            renewal_from_loss_ratio(
                loss_ratio * kept_share, current_premium, trend_pct, loading_pct,
                minimum_increase_pct=minimum_increase_pct,
            )
            if (loss_ratio is not None and current_premium and loading_pct is not None)
            else None
        )
        required = priced["required_premium"] if priced else None
        # The claims the ask is built to fund, in money: the trended loss
        # ratio against the expiring premium. The panel used to show its
        # OWN annualised claims here, which is the figure that disagreed
        # with the rating - it is not reported at all now, because a
        # number on screen that nothing is priced from is worse than a
        # missing column.
        trended = round(priced["trended_loss_ratio"] * current_premium, 2) if priced else None
        return {
            "member_count": count,
            "incurred_to_date": run_rate.get("incurred_to_date"),
            "full_months": run_rate.get("full_months"),
            "months_used": run_rate.get("months_used"),
            "months_dropped": run_rate.get("months_dropped"),
            "share_of_claims": round(kept_share, 4),
            "loss_ratio": priced["loss_ratio"] if priced else None,
            "trended_loss_ratio": priced["trended_loss_ratio"] if priced else None,
            "floor_applied": priced["floor_applied"] if priced else None,
            "trended_claims": trended,
            "claims_per_member": round(trended / count, 2) if (trended and count) else None,
            "required_premium": required,
            "required_per_member": round(required / count, 2) if (required and count) else None,
            "increase_vs_current_pct": (
                round(required / current_premium - 1, 4) if (required and current_premium) else None
            ),
        }

    everybody = price(members)
    without = price(kept, exclude) if exclude else None

    return {
        "as_priced": everybody,
        "excluding": without,
        "excluded_members": [
            {"beneficiary_id": m.get("beneficiary_id"), "relation": m.get("relation")}
            for m in held_out
        ],
        "worth_of_exclusion": (
            round(everybody["required_premium"] - without["required_premium"], 2)
            if (without and everybody["required_premium"] and without["required_premium"]) else None
        ),
        "current_premium": current_premium,
        "trend_pct": trend_pct,
        "loading_pct": loading_pct,
        "minimum_increase_pct": minimum_increase_pct,
        "account_loss_ratio": loss_ratio,
        # No fee split - or no experience on the book - no price. The
        # account is still worth reading either way.
        "pricing_blocked": loading_pct is None or loss_ratio is None,
        # Said on every result, because the number invites being quoted.
        "caveat": (
            "Excluding a member shows what the account would have cost without them. It is only "
            "a price if that member is not renewing, or their condition is excluded on the "
            "renewal terms - a high claimant renewing unchanged costs what they cost."
        ) if exclude else None,
    }
