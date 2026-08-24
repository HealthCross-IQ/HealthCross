"""Why a case will not price, said out loud.

Auto-quoting is deliberately silent: it is an opportunistic side effect of
someone's census or benefits upload, and must never turn a successful
upload into a failed one. That is right, and it has a cost - when it gives
up, the user sees nothing at all. They uploaded a table of benefits, the
screen did not change, and there is no way to tell whether the portal is
broken, still thinking, or waiting on something they have not done.

Silence is fine as a failure mode for a background job. It is not fine as
the only thing a user ever learns. This computes the same resolution the
auto-quote does, and reports what it found instead of discarding it: which
categories are ready, which are missing which field, and where that field
is set.

The distinction that matters most here is between "you have not done this
yet" and "this is wrong". A category with no plan design chosen is waiting
for a decision; a benefit plan whose category letter matches nothing in
the census is a mistake, and one that will never resolve itself no matter
how long the user waits. Both currently look identical - nothing happens -
and they need opposite responses.

Pure functions over plain dicts - no ORM, no database.
"""
from collections import defaultdict
from typing import Dict, List, Optional

#: Every field a category needs before it can be priced. All three, or the
#: category cannot resolve at all - two out of three prices nothing.
REQUIRED_FIELDS = ("product", "network", "tpa")


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def quote_readiness(
    census_categories: Dict[str, int],
    benefit_plans: List[dict],
    prior_quote_categories: Optional[List[dict]] = None,
    offers: Optional[List[dict]] = None,
) -> dict:
    """What each census category still needs before the case can price.

    Resolution order mirrors the real one: an explicit offer on the case
    wins, then the category's own benefit plan, then whatever a prior
    quote used. Each category reports which source answered it, so a
    figure that came from a stale prior quote is distinguishable from one
    the user set deliberately - they look the same on screen and mean very
    different things.
    """
    offers_by_category = {_norm(o.get("category")): o for o in (offers or [])}
    plans_by_category = {
        _norm(p.get("category")): p for p in benefit_plans if p.get("category")
    }
    prior_by_category = {
        _norm(c.get("category")): c for c in (prior_quote_categories or [])
    }

    rows = []
    for category, member_count in sorted(census_categories.items()):
        key = _norm(category)
        resolved: Dict[str, Optional[str]] = {}
        sources: Dict[str, str] = {}

        for field in REQUIRED_FIELDS:
            for source_name, source in (
                ("offer", offers_by_category.get(key)),
                ("benefits", plans_by_category.get(key)),
                ("prior quote", prior_by_category.get(key)),
            ):
                if source and source.get(field):
                    resolved[field] = source[field]
                    sources[field] = source_name
                    break
            else:
                resolved[field] = None

        missing = [f for f in REQUIRED_FIELDS if not resolved[f]]
        rows.append({
            "category": category,
            "member_count": member_count,
            "product": resolved["product"],
            "network": resolved["network"],
            "tpa": resolved["tpa"],
            "sources": sources,
            "missing": missing,
            "ready": not missing,
            "has_benefit_plan": key in plans_by_category,
        })

    # Benefit plans whose category matches nothing in the census. This is
    # the failure that never resolves itself: the user has done the work,
    # the plan is sitting there with its Product and Network set, and it
    # is invisible to pricing because the letter does not match. Waiting
    # longer will not fix it, so it cannot be reported as "not ready yet".
    census_keys = {_norm(c) for c in census_categories}
    orphan_plans = [
        {
            "plan_name": p.get("plan_name"),
            "category": p.get("category"),
            "product": p.get("product"),
            "network": p.get("network"),
        }
        for p in benefit_plans
        if p.get("category") and _norm(p.get("category")) not in census_keys
    ]

    # Plans with no category at all - they cannot be matched to anything,
    # however complete they otherwise are.
    uncategorised_plans = [
        {"plan_name": p.get("plan_name"), "product": p.get("product"), "network": p.get("network")}
        for p in benefit_plans
        if not p.get("category")
    ]

    ready_rows = [r for r in rows if r["ready"]]
    blockers = _blockers(rows, orphan_plans, uncategorised_plans, census_categories)

    return {
        "categories": rows,
        "category_count": len(rows),
        "ready_category_count": len(ready_rows),
        # Auto-quoting is all-or-nothing: one unresolved category and the
        # whole case stays unpriced, so a partial count is not progress.
        "can_price": bool(rows) and len(ready_rows) == len(rows),
        "orphan_benefit_plans": orphan_plans,
        "uncategorised_benefit_plans": uncategorised_plans,
        "blockers": blockers,
    }


def _blockers(
    rows: List[dict],
    orphan_plans: List[dict],
    uncategorised_plans: List[dict],
    census_categories: Dict[str, int],
) -> List[dict]:
    """The specific things standing in the way, each with where to fix it.

    Ordered by what to do first, and phrased as instructions rather than
    diagnoses - "set a Product for category B" is actionable in a way that
    "category B is not ready" is not.
    """
    blockers: List[dict] = []

    if not census_categories:
        blockers.append({
            "severity": "blocking",
            "issue": "No categories on the census",
            "detail": (
                "Every member needs a Category value (A, B, C...) for the case to price - "
                "pricing is per category, so a census with none has nothing to price."
            ),
            "fix_at": "Census upload",
        })
        return blockers

    for row in rows:
        if row["missing"]:
            blockers.append({
                "severity": "blocking",
                "issue": f"Category {row['category']} has no {', '.join(row['missing'])}",
                "detail": (
                    f"{row['member_count']} member(s) in this category. All of "
                    f"{', '.join(REQUIRED_FIELDS)} must be set before ANY category prices - "
                    f"auto-quoting is all-or-nothing."
                ),
                "fix_at": "New Business Quote tab, or the Benefits tab if a table of benefits is uploaded",
            })

    for plan in orphan_plans:
        blockers.append({
            "severity": "mistake",
            "issue": f"Benefit plan '{plan['plan_name']}' is set to category {plan['category']}, which no census member is in",
            "detail": (
                "This plan can never be matched to anyone. Waiting will not fix it - either the "
                "plan's category letter is wrong, or the census uses different letters."
            ),
            "fix_at": "Benefits tab - correct the plan's category",
        })

    for plan in uncategorised_plans:
        blockers.append({
            "severity": "mistake",
            "issue": f"Benefit plan '{plan['plan_name']}' has no category set",
            "detail": (
                "Plans are matched to census categories by their category letter, not by their "
                "name - a plan called 'Category A' with an empty category field matches nothing."
            ),
            "fix_at": "Benefits tab - set the plan's category",
        })

    return blockers
