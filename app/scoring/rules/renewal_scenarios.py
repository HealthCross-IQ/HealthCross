"""What the renewal costs if some of the experience is taken out.

An account's headline loss ratio is often one event. Deciding whether
that event renews with the account is an underwriting judgement, and the
number that judgement produces has to be comparable with the one it
replaces - so every scenario here is priced by the SAME ladder as the
headline (renewal_rating.renewal_from_loss_ratio), with a different
incurred figure fed into it. Nothing in this module divides, grosses up,
or trends anything itself.

That is the whole design. A "what if we strip the big claim" panel that
builds its own price is a second renewal premium on the same screen, and
the reader has no way to know which one the house would quote.

How a removal moves the numbers:

    adjusted incurred = incurred - removed
    adjusted ratio    = adjusted incurred / expiring premium
    required premium  = the ladder, on that ratio

IBNR is deliberately NOT reduced with the claim. Incurred is paid +
outstanding + IBNR, and the IBNR tail is a projection of the account's
ongoing run rate, not of the one-off being stripped - shrinking it
alongside the claim would take the same event out twice. Leaving it
prices the scenario slightly higher, which is the right direction to be
wrong in.
"""
from typing import List, Optional, Sequence

from app.scoring.rules.renewal_rating import (
    DEFAULT_INFLATION_PCT,
    MINIMUM_RENEWAL_INCREASE_PCT,
    renewal_from_loss_ratio,
)

#: A claim line at or above this is a "large claim" for the purpose of
#: the adjustments panel - the same threshold Portfolio Analysis's own
#: large-loss analysis leads with (DEFAULT_LARGE_CLAIM_THRESHOLDS[0]).
DEFAULT_LARGE_CLAIM_THRESHOLD = 50_000.0


def price_scenario(
    label: str,
    expiring_annual_premium: float,
    incurred: float,
    removed: float = 0.0,
    loading_pct: float = 0.33,
    inflation_pts: float = DEFAULT_INFLATION_PCT,
    minimum_increase_pct: Optional[float] = MINIMUM_RENEWAL_INCREASE_PCT,
    note: Optional[str] = None,
    key: Optional[str] = None,
    loss_ratio: Optional[float] = None,
    loss_ratio_premium_basis: Optional[float] = None,
) -> dict:
    """One row of the comparison, priced by the house ladder.

    `loss_ratio` is the account's OWN reported ratio where the caller has
    it, and a removal reduces it by removed / loss_ratio_premium_basis -
    the premium that ratio was divided by. Recomputing the ratio as
    incurred / expiring instead would put this row a few hundred dirhams
    away from the rating card on the same screen, because the book
    measures its ratio against premium EARNED to date while it quotes
    against a full annualised year, and because the published ratio is
    rounded to four places. Neither gap is large; both are the kind that
    make a reader stop trusting the page.

    A removal larger than the experience is clamped at zero rather than
    producing a negative loss ratio: two adjustments can genuinely
    overlap (a large claim belonging to a member who is also leaving),
    and a negative ratio would price a renewal on a refund.
    """
    if expiring_annual_premium <= 0:
        raise ValueError("expiring_annual_premium must be positive.")
    basis = loss_ratio_premium_basis or expiring_annual_premium
    reported_ratio = loss_ratio if loss_ratio is not None else incurred / expiring_annual_premium
    removed = max(0.0, min(removed, reported_ratio * basis))
    adjusted_ratio = max(0.0, reported_ratio - removed / basis)
    adjusted_incurred = incurred - removed
    priced = renewal_from_loss_ratio(
        adjusted_ratio, expiring_annual_premium, inflation_pts, loading_pct,
        minimum_increase_pct=minimum_increase_pct,
    )
    return {
        "key": key or label.lower().replace(" ", "_"),
        "label": label,
        "note": note,
        "removed": round(removed, 2),
        "adjusted_incurred": round(max(0.0, adjusted_incurred), 2),
        "loss_ratio": priced["loss_ratio"],
        "trended_loss_ratio": priced["trended_loss_ratio"],
        "required_premium": priced["required_premium"],
        "renewal_increase_pct": priced["renewal_increase_pct"],
        "experience_increase_pct": priced["experience_increase_pct"],
        "floor_applied": priced["floor_applied"],
    }


def scenario_rows(
    expiring_annual_premium: float,
    incurred: float,
    adjustments: Sequence[dict],
    loading_pct: float,
    inflation_pts: float = DEFAULT_INFLATION_PCT,
    minimum_increase_pct: Optional[float] = MINIMUM_RENEWAL_INCREASE_PCT,
    override_premium: Optional[float] = None,
    override_reason: Optional[str] = None,
    loss_ratio: Optional[float] = None,
    loss_ratio_premium_basis: Optional[float] = None,
) -> List[dict]:
    """The comparison table: as reported, each adjustment on its own, all
    of them together, and any manual override.

    "As reported" is always the first row and is never removed. A price
    produced by taking something out is only a price if that something
    genuinely is not renewing, and showing it alone invites it to be
    quoted as though it were - the same reason renewal_repricing always
    returns the price with everybody in.

    Each `adjustment` is {key, label, amount, available, note}. An
    adjustment that is not available (nothing to strip, no revised
    benefit table on file) is priced as a zero removal and carries its
    own note, so the reader sees the lever and why it is doing nothing
    rather than not seeing it at all.
    """
    def price(label, removed, note=None, key=None):
        return price_scenario(
            label, expiring_annual_premium, incurred, removed,
            loading_pct=loading_pct, inflation_pts=inflation_pts,
            minimum_increase_pct=minimum_increase_pct, note=note, key=key,
            loss_ratio=loss_ratio, loss_ratio_premium_basis=loss_ratio_premium_basis,
        )

    rows = [price("As reported", 0.0, key="as_reported",
                  note="Every claim on the account's own experience.")]

    applied = [a for a in adjustments if a.get("available") and (a.get("amount") or 0) > 0]
    for adjustment in adjustments:
        amount = adjustment.get("amount") or 0.0
        rows.append(price(
            adjustment["label"],
            amount if adjustment.get("available") else 0.0,
            note=adjustment.get("note"),
            key=adjustment.get("key"),
        ))

    # Only worth a row when more than one lever actually moves something -
    # otherwise it repeats the single adjustment above it verbatim.
    if len(applied) > 1:
        rows.append(price(
            "All adjustments",
            sum(a["amount"] for a in applied),
            key="combined",
            note=f"{len(applied)} adjustments together.",
        ))

    if override_premium is not None:
        rows.append({
            "key": "override",
            "label": "Override",
            "note": override_reason,
            "removed": None,
            "adjusted_incurred": None,
            "loss_ratio": None,
            "trended_loss_ratio": None,
            "required_premium": round(override_premium, 2),
            "renewal_increase_pct": round(
                (override_premium / expiring_annual_premium - 1) * 100, 2),
            "experience_increase_pct": None,
            "floor_applied": False,
        })

    return rows


def large_claim_total(
    claims: Sequence[dict],
    threshold: float = DEFAULT_LARGE_CLAIM_THRESHOLD,
) -> dict:
    """Claim LINES at or above the threshold, and whose they are.

    Ranked by line rather than by member total on purpose: this lever
    strips catastrophic events, and a member who reaches the same total
    through forty ordinary claims has an ordinary year, not a one-off.
    Stripping them would be pricing away the account's real experience.
    """
    large = [c for c in claims if (c.get("final_amount") or 0.0) >= threshold]
    members = {c.get("patient_id") for c in large if c.get("patient_id")}
    return {
        "amount": round(sum(c.get("final_amount") or 0.0 for c in large), 2),
        "claim_count": len(large),
        "member_count": len(members),
        "threshold": threshold,
        "beneficiary_ids": sorted(m for m in members if m),
    }


def claims_for_members(claims: Sequence[dict], beneficiary_ids: Sequence[str]) -> float:
    """Everything a named set of members claimed - what leaves the
    experience when they leave the account."""
    wanted = {b for b in beneficiary_ids if b}
    if not wanted:
        return 0.0
    return round(sum(
        c.get("final_amount") or 0.0
        for c in claims
        if c.get("patient_id") in wanted
    ), 2)
