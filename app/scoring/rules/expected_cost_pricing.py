"""Pricing a case from what its members are actually expected to cost.

The scorecard's suggested loading was `(composite_score - 50) * 1.0` - a
0-100 risk score, minus the midpoint, read as a percentage. It has no
units. A case scoring 70 got "20% loading" not because 20% funds anything
in particular, but because 70 minus 50 is 20. Two cases with the same
score and completely different demographics got the same answer, and no
part of the number could be traced back to a dirham of claims.

This prices from the other end: what does this specific census cost,
member by member, according to HealthCross's own book (see
burning_cost_cube), and what premium funds that cost after industry,
trend and expenses. Every step is named and carries its own factor, so
the price can be shown as a build-up rather than asserted - which is both
what an underwriter needs to defend it to a broker, and what makes it
possible to argue with one step without discarding the whole number.

The relationship to the old score is deliberately inverted: the score no
longer produces the price, the price produces a relativity. "This case
prices 34% above the book average" is a statement about money that
happens to also rank the case; "this case scores 67" ranks it and says
nothing about money.

Pure functions over plain dicts - the caller resolves each member's
product/network (as price_case_against_burning_cost does) before pricing.
"""
from collections import defaultdict
from typing import Dict, List, Optional

from app.scoring.rules.burning_cost_cube import build_cube_index, expected_cost_for_member
from app.scoring.rules.industry import industry_risk

#: Annual medical inflation applied between the experience period and the
#: policy period being priced. A book's own claims are historic by
#: construction - pricing next year at last year's cost prices in a loss.
#: UAE medical trend has run at high single to low double digits; 10% is
#: the working assumption and is exposed on every quote so it can be
#: overridden per case rather than being an invisible constant.
DEFAULT_TREND_PCT = 0.10


def _step(label: str, factor: Optional[float], amount: float, note: str) -> dict:
    return {
        "label": label,
        "factor": round(factor, 4) if factor is not None else None,
        "amount": round(amount, 2),
        "note": note,
    }


def price_census_at_expected_cost(
    census: List[dict],
    cube: dict,
    industry: Optional[str] = None,
    trend_pct: float = DEFAULT_TREND_PCT,
    loading_pct: float = 0.0,
    non_recurring_claims: float = 0.0,
) -> dict:
    """Expected-cost price for a census, as a step-by-step build-up.

    `non_recurring_claims` removes cost that genuinely will not repeat -
    a completed maternity, a one-off surgery on a member who has since
    left. It is a caller-supplied judgement rather than something
    inferred here, because "will this recur?" is a clinical and
    underwriting question, not an arithmetic one: a delivery is complete,
    but endometriosis that required surgery this year may well require it
    again. Passing it as an explicit adjustment keeps that judgement
    visible on the quote instead of buried in a base figure.

    Loading is applied as a gross-up, not a markup: premium x (1 -
    loading) is what remains to fund claims, so the premium that funds
    `risk_premium` is `risk_premium / (1 - loading)`. Marking up instead
    (risk x 1.33) under-collects, and by more the larger the loading.
    """
    if loading_pct >= 1:
        raise ValueError("loading_pct must be less than 1 - premium cannot be entirely expenses.")

    index = build_cube_index(cube)
    priced_members = []
    expected_claims = 0.0
    fallbacks = 0
    credibility_weighted = 0.0

    for member in census:
        priced = expected_cost_for_member(member, cube, index)
        cost = priced["expected_cost"] or 0.0
        expected_claims += cost
        credibility_weighted += priced["credibility"] * cost
        if priced["fell_back"]:
            fallbacks += 1
        priced_members.append(
            {
                "category": member.get("category"),
                "relation": member.get("relation"),
                "age": member.get("age"),
                "gender": member.get("gender"),
                "nationality_zone": member.get("nationality_zone"),
                "expected_cost": priced["expected_cost"],
                "matched_level": priced["matched_level"],
                "credibility": priced["credibility"],
                "fell_back": priced["fell_back"],
            }
        )

    build_up = [_step("Expected claims from the book", None, expected_claims,
                      f"{len(census)} members priced against their own cells in the burning cost cube")]

    running = expected_claims
    if non_recurring_claims:
        running = max(0.0, running - non_recurring_claims)
        build_up.append(_step("Less non-recurring claims", None, running,
                              f"AED {non_recurring_claims:,.0f} of cost judged not to repeat"))

    industry_factor = industry_risk(industry) if industry else 1.0
    if industry_factor != 1.0:
        running *= industry_factor
        build_up.append(_step(f"Industry ({industry})", industry_factor, running,
                              "occupational risk - not present in the membership data, so applied here"))

    running *= (1 + trend_pct)
    build_up.append(_step("Medical trend", 1 + trend_pct, running,
                          f"{trend_pct * 100:.1f}% between the experience period and the policy period"))

    risk_premium = running
    gross_premium = risk_premium / (1 - loading_pct) if loading_pct < 1 else None
    build_up.append(_step("Gross up for loading", 1 / (1 - loading_pct), gross_premium or 0.0,
                          f"{loading_pct * 100:.1f}% commission, TPA, HealthCross and QIC fees"))

    member_count = len(census)
    book_rate = cube["book"]["burning_cost"]
    book_expected = (book_rate or 0.0) * member_count
    relativity = round(expected_claims / book_expected, 4) if book_expected else None

    return {
        "member_count": member_count,
        "expected_claims": round(expected_claims, 2),
        "non_recurring_claims": round(non_recurring_claims, 2),
        "industry": industry,
        "industry_factor": round(industry_factor, 4),
        "trend_pct": trend_pct,
        "loading_pct": loading_pct,
        "risk_premium": round(risk_premium, 2),
        "gross_premium": round(gross_premium, 2) if gross_premium is not None else None,
        "premium_per_member": round(gross_premium / member_count, 2) if gross_premium and member_count else None,
        "build_up": build_up,
        # How much of this price rests on the case's own segments versus
        # broader fallbacks - the honest confidence statement on the total.
        "weighted_credibility": round(credibility_weighted / expected_claims, 4) if expected_claims else 0.0,
        "fallback_member_count": fallbacks,
        # Against a book-average member of no particular demographic - the
        # replacement for the old 0-100 score, in units that mean something.
        "book_relativity": relativity,
        "members": priced_members,
    }


def price_by_category(
    census: List[dict],
    cube: dict,
    loading_pct_by_category: Optional[Dict[str, float]] = None,
    default_loading_pct: float = 0.0,
    industry: Optional[str] = None,
    trend_pct: float = DEFAULT_TREND_PCT,
) -> dict:
    """The same build-up, per plan category, since a scheme's categories
    are priced and sold separately - a quote lists a rate per category,
    not one blended number. Members with no category are priced into an
    "Unspecified" bucket rather than dropped, so the category totals
    always add back to the case total.
    """
    loading_pct_by_category = loading_pct_by_category or {}
    by_category: Dict[str, List[dict]] = defaultdict(list)
    for member in census:
        by_category[(member.get("category") or "Unspecified")].append(member)

    categories = []
    case_gross = 0.0
    case_risk = 0.0
    for category, members in sorted(by_category.items()):
        loading = loading_pct_by_category.get(category, default_loading_pct)
        priced = price_census_at_expected_cost(
            members, cube, industry=industry, trend_pct=trend_pct, loading_pct=loading
        )
        categories.append({"category": category, **priced})
        case_gross += priced["gross_premium"] or 0.0
        case_risk += priced["risk_premium"]

    return {
        "categories": categories,
        "case_risk_premium": round(case_risk, 2),
        "case_gross_premium": round(case_gross, 2),
        "member_count": len(census),
        "premium_per_member": round(case_gross / len(census), 2) if census else None,
    }


def renewal_premium_from_experience(
    continuing_incurred: float,
    loading_pct: float,
    elapsed_days: Optional[int] = None,
    ibnr_tail_days: int = 30,
    trend_pct: float = DEFAULT_TREND_PCT,
    non_recurring_claims: float = 0.0,
    forward_provision: float = 0.0,
    member_count: Optional[int] = None,
) -> dict:
    """The renewal price built from an account's OWN experience rather
    than the book's - the calculation an underwriter does by hand at
    every renewal, made explicit.

    `continuing_incurred` must already exclude leavers' claims (see
    member_movement), because claims belonging to members who have come
    off the scheme do not carry forward and charging the remaining
    members for them prices the account on risk it no longer holds.

    `non_recurring_claims` and `forward_provision` are the two halves of
    the same judgement and are deliberately separate: a completed
    maternity comes OUT of the base because that pregnancy is over, and a
    maternity provision goes BACK IN because the group still contains
    women who may deliver next year. Netting them into one number hides
    the reasoning and makes the quote impossible to argue with line by
    line.

    IBNR is applied as a rate (`ibnr_tail_days / elapsed_days`) on
    whatever base survives the non-recurring adjustment, rather than as a
    pre-computed amount on the full one. The order matters and is a
    deliberate choice: this is a PRICE, not a reserve. Cost that has been
    judged not to carry forward should not generate a reserve that does -
    reserving IBNR on a completed pregnancy and then removing the
    pregnancy leaves its tail behind in the price. (The Loss Ratio
    board's IBNR is the reserving view of the same rule and correctly
    runs on the full paid amount; the two answer different questions.)
    """
    if loading_pct >= 1:
        raise ValueError("loading_pct must be less than 1 - premium cannot be entirely expenses.")

    build_up = [_step("Continuing members' incurred claims", None, continuing_incurred,
                      "leavers' claims excluded - they do not carry forward")]
    running = continuing_incurred

    if non_recurring_claims:
        running = max(0.0, running - non_recurring_claims)
        build_up.append(_step("Less non-recurring", None, running,
                              f"AED {non_recurring_claims:,.0f} judged not to repeat"))

    ibnr = 0.0
    if elapsed_days and elapsed_days > 0 and running:
        ibnr = running * (ibnr_tail_days / elapsed_days)
        running += ibnr
        build_up.append(_step("IBNR", 1 + ibnr_tail_days / elapsed_days, running,
                              f"{ibnr_tail_days} days of the go-forward run rate over {elapsed_days} elapsed days"))

    if forward_provision:
        running += forward_provision
        build_up.append(_step("Forward provision", None, running,
                              f"AED {forward_provision:,.0f} for expected but not-yet-incurred exposure"))

    running *= (1 + trend_pct)
    build_up.append(_step("Medical trend", 1 + trend_pct, running, f"{trend_pct * 100:.1f}%"))

    risk_premium = running
    gross_premium = risk_premium / (1 - loading_pct)
    build_up.append(_step("Gross up for loading", 1 / (1 - loading_pct), gross_premium,
                          f"{loading_pct * 100:.1f}%"))

    return {
        "continuing_incurred": round(continuing_incurred, 2),
        "ibnr": round(ibnr, 2),
        "elapsed_days": elapsed_days,
        "ibnr_tail_days": ibnr_tail_days,
        "non_recurring_claims": round(non_recurring_claims, 2),
        "forward_provision": round(forward_provision, 2),
        "trend_pct": trend_pct,
        "loading_pct": loading_pct,
        "risk_premium": round(risk_premium, 2),
        "gross_premium": round(gross_premium, 2),
        "member_count": member_count,
        "premium_per_member": round(gross_premium / member_count, 2) if member_count else None,
        "build_up": build_up,
    }
