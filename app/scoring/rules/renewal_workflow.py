"""Where a renewal actually is, as five steps.

The Renewal Bench is thirteen panels in one scroll. Everything needed to
quote is on it and nothing says what is still missing, so the way to
find out an account cannot be priced is to scroll to the pricing panel
and read that it was withheld.

These are the five stages a renewal genuinely passes through, each with
a state derived from real facts rather than from a checkbox someone
ticked:

    done     the step has what it needs
    blocked  something is missing that stops the ones after it
    todo     not reached yet

The distinction between blocked and todo is the point. A renewal whose
fee split was never entered is not "not started" at the pricing step -
it is stopped at the adjustments step, and the difference between those
two readings is whether anyone knows what to go and do.
"""
from typing import List, Optional

STATE_DONE = "done"
STATE_BLOCKED = "blocked"
STATE_TODO = "todo"


def _step(key, label, state, detail=None, blocker=None, anchor=None) -> dict:
    return {
        "key": key,
        "label": label,
        "state": state,
        "detail": detail,
        "blocker": blocker,
        "anchor": anchor,
    }


def renewal_workflow(
    census_member_count: Optional[int] = None,
    incurred_claims: Optional[float] = None,
    claims_present: Optional[bool] = None,
    claims_source: Optional[str] = None,
    loading_problems: Optional[List[dict]] = None,
    loading_pct: Optional[float] = None,
    adjustments_available: int = 0,
    adjustments_applied: int = 0,
    required_premium: Optional[float] = None,
    renewal_increase_pct: Optional[float] = None,
    pricing_problems: Optional[List[dict]] = None,
    increase_source: Optional[str] = None,
    quote_settled: bool = False,
) -> List[dict]:
    """The five steps for one case, in order.

    Nothing here decides anything. Every input is a fact another part of
    the portal already established - the census count, the incurred the
    rating was built from, the loading problems app.api.case_loading
    raised, the required premium Method 1 produced - so a step can never
    report a renewal as ready that the pricing endpoint would refuse.
    """
    steps = []

    has_census = bool(census_member_count)
    steps.append(_step(
        "census", "Census", STATE_DONE if has_census else STATE_TODO,
        detail=f"{census_member_count:,} lives" if has_census else "No census on the case yet",
        blocker=None if has_census else "Upload the renewal census, or open the case from the book.",
        anchor="renewal-bench-population-movement-area",
    ))

    # Whether the account HAS claims, which is not the same question as
    # whether the priced incurred figure is available: a renewal whose
    # loading was never entered has its incurred withheld along with
    # everything else, and reporting that as "no claims" would send
    # someone off to upload a file that is already there.
    has_claims = bool(incurred_claims) if claims_present is None else claims_present
    if not has_claims:
        claims_detail = "No claims experience for this account"
    elif incurred_claims:
        claims_detail = f"{incurred_claims:,.0f} incurred"
        if claims_source:
            claims_detail += f" &middot; {claims_source}"
    else:
        claims_detail = "On file" + (f" &middot; {claims_source}" if claims_source else "")
    steps.append(_step(
        "claims", "Claims", STATE_DONE if has_claims else STATE_TODO,
        detail=claims_detail,
        blocker=None if has_claims else "Upload the book's claims export, or a claims ledger for the case.",
        anchor="renewal-bench-claims-area",
    ))

    # The gate. A renewal is priced on the account's own entered loading
    # and on nothing else, so an unanswered fee split stops everything
    # after this point - and says so here rather than at the bottom of
    # the page where the price should have been.
    if loading_problems:
        adjustments_state = STATE_BLOCKED
        adjustments_detail = "Loading not entered"
        adjustments_blocker = loading_problems[0].get("message")
    elif not has_claims:
        adjustments_state = STATE_TODO
        adjustments_detail = "Waiting on claims"
        adjustments_blocker = None
    else:
        adjustments_state = STATE_DONE
        adjustments_blocker = None
        loading_text = f"loading {loading_pct * 100:.1f}%" if loading_pct is not None else "loading set"
        if adjustments_applied:
            adjustments_detail = f"{adjustments_applied} of {adjustments_available} applied &middot; {loading_text}"
        elif adjustments_available:
            adjustments_detail = f"{adjustments_available} available &middot; {loading_text}"
        else:
            adjustments_detail = f"Nothing to strip &middot; {loading_text}"
    steps.append(_step(
        "adjustments", "Adjustments", adjustments_state,
        detail=adjustments_detail, blocker=adjustments_blocker,
        anchor="renewal-bench-scenarios-area",
    ))

    priced = required_premium is not None and not pricing_problems
    if priced:
        pricing_state, pricing_blocker = STATE_DONE, None
        pricing_detail = f"{required_premium:,.0f}"
        if renewal_increase_pct is not None:
            pricing_detail += f" &middot; {renewal_increase_pct:+.1f}%"
    elif pricing_problems or loading_problems:
        pricing_state = STATE_BLOCKED
        pricing_detail = "Price withheld"
        pricing_blocker = (pricing_problems or loading_problems)[0].get("message")
    else:
        pricing_state, pricing_blocker = STATE_TODO, None
        pricing_detail = "Not priced yet"
    steps.append(_step(
        "pricing", "Pricing", pricing_state, detail=pricing_detail,
        blocker=pricing_blocker, anchor="renewal-premium-buildup-area",
    ))

    # A quote is a decision somebody made, not a number the portal
    # computed. Every priced renewal HAS a computed ask, so treating the
    # existence of one as "quoted" would mark every account on the book
    # finished the moment it was priced - the step would say nothing.
    # It is done when an underwriter has settled the ask: recorded an
    # override, or bound the case.
    quoted = priced and quote_settled
    if not priced:
        quote_detail = "Nothing to quote yet"
    elif quoted:
        quote_detail = f"Quoting the {increase_source}" if increase_source else "Ask settled"
    else:
        quote_detail = (f"{increase_source} ask not confirmed yet" if increase_source
                        else "Ask not confirmed yet")
    steps.append(_step(
        "quote", "Quote", STATE_DONE if quoted else STATE_TODO,
        detail=quote_detail, blocker=None,
        anchor="renewal-bench-premium-area",
    ))

    return steps


def workflow_state(steps: List[dict]) -> dict:
    """The one-line summary: where the renewal is, and what is stopping
    it if anything is."""
    blocked = [s for s in steps if s["state"] == STATE_BLOCKED]
    done = [s for s in steps if s["state"] == STATE_DONE]
    current = next((s for s in steps if s["state"] != STATE_DONE), steps[-1])
    return {
        "steps_done": len(done),
        "steps_total": len(steps),
        "current_step": current["key"],
        "blocked": bool(blocked),
        "blocker": blocked[0]["blocker"] if blocked else None,
        "ready_to_quote": len(done) == len(steps),
    }
