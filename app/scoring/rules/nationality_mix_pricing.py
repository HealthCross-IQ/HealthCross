"""What an enquiry's nationality mix is worth on the price.

The nationality risk engine (see portfolio_analysis's
nationality_risk_table) already knows which nationalities run hot and
which run cold on HealthCross's own book, credibility-blended toward
their zone so a thin one cannot swing on a single claim. Until now that
was a table an underwriter read; it never reached a quote.

This turns a census into one number: the exposure-weighted factor its
particular mix carries relative to a book-average member. A scheme that
is 80% of a nationality running at 0.75x is genuinely cheaper to insure
than the card assumes, and can be priced to win. One that is mostly a
1.4x nationality is not, and quoting it at card rates is how a book ends
up where this one is.

Three deliberate constraints, because a factor like this is easy to abuse:

Only nationalities with enough exposure to price on contribute their own
factor. The rest fall back to 1.0 rather than to their raw ratio - a
nationality with three member-years on the book has a number, but it is
not a fact about that nationality, and pricing off it would be inventing
signal from noise.

The factor is capped in both directions, and the cap is reported when it
binds. Even after credibility blending, a segment carrying one
catastrophic claim can produce arithmetic that is real and indefensible
as a price.

Nothing here decides the price. It returns the factor, what it rests on,
and how much of the census it could actually measure - so an underwriter
can see that a 0.82x is built on 60% of the members and judge whether
that is enough to act on.
"""
from collections import defaultdict
from typing import Dict, List, Optional

#: Below this share of the census being measurable, the mix factor is
#: reported but flagged as not worth pricing on: a factor derived from a
#: third of the members says more about the missing two thirds than about
#: the risk.
MIN_MEASURABLE_SHARE = 0.5

#: Bounds on the blended mix factor itself, on top of the per-nationality
#: caps the risk table already applies. A whole-scheme factor beyond these
#: is almost always a data problem rather than a real risk difference.
MIN_MIX_FACTOR = 0.75
MAX_MIX_FACTOR = 1.35


def _normalize(value: Optional[str]) -> str:
    return (value or "").strip().casefold()


def nationality_mix_factor(
    census: List[dict],
    nationality_rows: List[dict],
    require_pricing_ready: bool = True,
    min_mix_factor: float = MIN_MIX_FACTOR,
    max_mix_factor: float = MAX_MIX_FACTOR,
) -> dict:
    """The factor this census's nationality mix justifies.

    Weighted by headcount rather than by claims: the question is what
    THIS population should cost, so each member counts once regardless of
    what people of their nationality have historically claimed. Weighting
    by claims would let the expensive nationalities dominate their own
    factor and double-count the very thing being measured.

    Members whose nationality has no usable factor are excluded from the
    weighting rather than assigned 1.0 inside it - averaging unmeasured
    members in at neutral drags every factor toward 1.0 and makes a real
    signal look weaker than it is. They are counted separately instead,
    which is what `measurable_share` reports.
    """
    by_nationality: Dict[str, dict] = {}
    for row in nationality_rows:
        if row.get("relativity") is None:
            continue
        if require_pricing_ready and not row.get("pricing_ready"):
            continue
        by_nationality[_normalize(row.get("nationality"))] = row

    counts: Dict[str, int] = defaultdict(int)
    for member in census:
        counts[_normalize(member.get("nationality"))] += 1

    total_members = sum(counts.values())
    weighted = 0.0
    measured = 0
    contributions = []
    unmeasured: Dict[str, int] = {}

    for nationality, count in counts.items():
        row = by_nationality.get(nationality)
        if row is None:
            unmeasured[nationality or "(not recorded)"] = count
            continue
        weighted += row["relativity"] * count
        measured += count
        contributions.append({
            "nationality": row["nationality"],
            "nationality_zone": row.get("nationality_zone"),
            "member_count": count,
            "share_of_census": round(count / total_members, 4) if total_members else None,
            "relativity": row["relativity"],
            "credibility": row.get("credibility"),
            "book_exposure_member_years": row.get("earned_member_years"),
        })

    raw_factor = (weighted / measured) if measured else None
    factor = raw_factor
    capped = False
    if factor is not None:
        capped_factor = max(min_mix_factor, min(max_mix_factor, factor))
        capped = abs(capped_factor - factor) > 1e-9
        factor = round(capped_factor, 4)

    measurable_share = round(measured / total_members, 4) if total_members else 0.0
    contributions.sort(key=lambda c: -c["member_count"])

    return {
        "factor": factor,
        "raw_factor": round(raw_factor, 4) if raw_factor is not None else None,
        "capped": capped,
        "member_count": total_members,
        "measured_member_count": measured,
        "measurable_share": measurable_share,
        # A factor built on a third of the members says more about the
        # missing two thirds than about the risk.
        "pricing_ready": measurable_share >= MIN_MEASURABLE_SHARE and factor is not None,
        "contributions": contributions,
        "unmeasured": [
            {"nationality": k, "member_count": v}
            for k, v in sorted(unmeasured.items(), key=lambda kv: -kv[1])
        ],
        "direction": (
            None if factor is None
            else "favourable" if factor < 0.98
            else "adverse" if factor > 1.02
            else "neutral"
        ),
    }


def within_zone_rows(nationality_rows: List[dict]) -> List[dict]:
    """Each nationality's factor relative to its OWN ZONE rather than to
    the book.

    This exists to avoid double counting. The burning cost cube already
    carries nationality_zone as one of its dimensions, so a price built
    from the cube has the zone effect in it already. Multiplying the
    book-relative nationality factor on top would charge the zone
    component twice - once inside the cube cell, once again as a factor.

    What the cube does NOT know is how nationalities differ WITHIN a
    zone: two nationalities in the same zone sit in the same cube cell
    and are priced identically, even where the book says one costs
    noticeably more than the other. That within-zone difference is the
    only part it is legitimate to apply on top, and it is what this
    returns - `credible_burning_cost / zone_burning_cost` rather than the
    book-relative `relativity`.

    A nationality with no zone rate to compare against is dropped rather
    than falling back to its book-relative figure, which would quietly
    reintroduce the double count for exactly the rows where it is least
    visible.
    """
    out = []
    for row in nationality_rows:
        own = row.get("credible_burning_cost")
        zone = row.get("zone_burning_cost")
        if not own or not zone:
            continue
        out.append({**row, "relativity": round(own / zone, 4)})
    return out


def apply_mix_to_quote(gross_premium: float, mix: dict) -> dict:
    """The quote with the mix factor applied, and the same quote without
    it, side by side.

    Both are returned because this is a competitive judgement rather than
    a correction: the unadjusted price is what the rate card says, and the
    adjusted one is what this particular population justifies. An
    underwriter deciding how hard to chase an enquiry needs to see the gap,
    not just the answer - and on an adverse mix the gap is the discount
    they should NOT be giving.
    """
    factor = mix.get("factor")
    if not gross_premium or factor is None:
        return {
            "card_premium": round(gross_premium, 2) if gross_premium else None,
            "mix_adjusted_premium": round(gross_premium, 2) if gross_premium else None,
            "factor": None,
            "difference": 0.0,
            "applied": False,
            "reason": "No usable nationality experience for this census",
        }

    adjusted = gross_premium * factor
    return {
        "card_premium": round(gross_premium, 2),
        "mix_adjusted_premium": round(adjusted, 2),
        "factor": factor,
        "difference": round(adjusted - gross_premium, 2),
        "difference_pct": round((factor - 1) * 100, 1),
        "applied": bool(mix.get("pricing_ready")),
        "reason": (
            "Applied - the mix is measurable on enough of the census to price on"
            if mix.get("pricing_ready")
            else f"Shown but not applied - only {mix.get('measurable_share', 0) * 100:.0f}% of "
                 f"the census has nationality experience on the book"
        ),
    }
