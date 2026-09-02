"""Extra Renewal Bench metrics beyond the Method A/B scorecard itself (see
renewal_rating.py): per-case claim frequency/severity/claimant ratio, and
the Renewal Drivers waterfall that decomposes the total renewal increase
into named contributing factors (claims experience, medical trend, census
change, underwriter adjustment) so the increase is traceable rather than
one opaque percentage - matching the approved Renewal Bench mockup.
"""
from typing import List, Optional

#: Distinguishes "caller said None" from "caller said nothing" for the
#: house floor, which None deliberately switches OFF for a what-if.
_UNSET = object()


def existing_premium_breakdown(members: List[dict]) -> dict:
    """Total existing premium built bottom-up from the CURRENT active/
    membership list's own per-member existing_annual_rate - headcount x
    rate, by category - rather than relying on a single manually-typed
    current_annual_premium figure. This is what the Renewal Bench's
    "Existing" leg (alongside Renewal-required and the Portal-generated
    quote) is built from, and what auto-populates a case's own
    current_annual_premium the first time member rates are saved (see
    app.api.routes_analysis's update_member_rates/import_member_rate_card)
    - a case can still override it manually afterward.

    Each member dict needs "category" (nullable) and
    "existing_annual_rate" (nullable - a member without a rate set yet
    simply doesn't contribute to total_existing_premium, but still counts
    toward total_members so coverage_pct reflects how much of the census
    actually has a rate on file).
    """
    by_category: dict = {}
    total_members = len(members)
    rated_members = 0
    total_existing_premium = 0.0

    for m in members:
        category = m.get("category") or "Unspecified"
        bucket = by_category.setdefault(category, {"member_count": 0, "rated_member_count": 0, "total_premium": 0.0})
        bucket["member_count"] += 1
        rate = m.get("existing_annual_rate")
        if rate is not None:
            bucket["rated_member_count"] += 1
            bucket["total_premium"] += rate
            rated_members += 1
            total_existing_premium += rate

    categories = [
        {
            "category": category,
            "member_count": bucket["member_count"],
            "rated_member_count": bucket["rated_member_count"],
            "total_premium": round(bucket["total_premium"], 2),
            "avg_rate": round(bucket["total_premium"] / bucket["rated_member_count"], 2) if bucket["rated_member_count"] else None,
        }
        for category, bucket in sorted(by_category.items())
    ]

    return {
        "categories": categories,
        "total_members": total_members,
        "rated_members": rated_members,
        "coverage_pct": round(rated_members / total_members * 100, 1) if total_members else None,
        "total_existing_premium": round(total_existing_premium, 2),
    }


def case_claim_kpis(
    claims: List[dict],
    census_member_count: int,
    census_ages: List[int],
    months_count: int,
) -> dict:
    """claim_frequency: claim LINE count, annualized from the trailing
    months actually observed, per current census member - this case's own
    run rate, not a book-wide earned-member-years figure (see
    portfolio_analysis.py's claim_frequency for that book-wide version).
    avg_claim_severity: average $ per claim line. claimant_ratio: share of
    the CURRENT census with at least one claim on file (by patient_id) -
    a member with 5 claims still counts once, so this reads differently
    from claim_frequency (which is line-count based). avg_member_age:
    plain mean of the census's own ages.
    """
    claim_count = len(claims)
    total_incurred = sum(c.get("final_amount") or 0.0 for c in claims)
    distinct_claimants = len({c.get("patient_id") for c in claims if c.get("patient_id")})

    annualized_claim_count = (claim_count / months_count * 12) if months_count else claim_count
    claim_frequency = annualized_claim_count / census_member_count if census_member_count else None
    avg_claim_severity = total_incurred / claim_count if claim_count else None
    claimant_ratio = distinct_claimants / census_member_count if census_member_count else None
    avg_member_age = sum(census_ages) / len(census_ages) if census_ages else None

    return {
        "claim_count": claim_count,
        "distinct_claimants": distinct_claimants,
        "claim_frequency": round(claim_frequency, 2) if claim_frequency is not None else None,
        "avg_claim_severity": round(avg_claim_severity, 2) if avg_claim_severity is not None else None,
        "claimant_ratio": round(claimant_ratio, 4) if claimant_ratio is not None else None,
        "avg_member_age": round(avg_member_age, 1) if avg_member_age is not None else None,
    }


def census_change_pct_from_snapshots(
    snapshots: List[dict],
    current_relation_counts: dict,
    current_annual_premium: Optional[float],
) -> Optional[float]:
    """The census-movement premium impact (see GET /cases/{id}/census-movement
    - headcount change per relation x the case's own average premium per
    member), expressed as a % of current_annual_premium so it can sit
    alongside the other Renewal Drivers as a percentage-point contribution.
    None when there's no prior census snapshot to compare against, or no
    current_annual_premium to express the impact as a % of.
    """
    if not snapshots or not current_annual_premium:
        return None
    expiring_by_relation = {s["relation"]: s["member_count"] for s in snapshots}
    total_expiring = sum(expiring_by_relation.values())
    if not total_expiring:
        return None
    avg_premium_per_member = current_annual_premium / total_expiring

    relations = set(expiring_by_relation) | set(current_relation_counts)
    total_impact = 0.0
    for rel in relations:
        expiring = expiring_by_relation.get(rel, 0)
        renewal = current_relation_counts.get(rel, 0)
        total_impact += (renewal - expiring) * avg_premium_per_member

    return round(total_impact / current_annual_premium * 100, 2)


def renewal_drivers(
    loss_ratio: float,
    expiring_annual_premium: float,
    loading_pct: float,
    inflation_pts: Optional[float] = None,
    census_change_pct: Optional[float] = None,
    underwriter_adjustment_pct: float = 0.0,
    authority_threshold_pct: float = 15.0,
    minimum_increase_pct: Optional[float] = _UNSET,
    required_premium: Optional[float] = None,
) -> dict:
    """The Renewal Bench waterfall: Method 1's price, broken into the
    named factors that produced it.

    It used to build its own price, and the two disagreed. The ladder
    (renewal_from_loss_ratio) adds inflation to the LOSS RATIO in points
    and this decomposition multiplied the CLAIMS by it, so the same
    account had two renewal premiums on the same screen - K A F at a
    248.6% loss ratio came out +226.2% under Method 1 and +240.4% here,
    a gap of AED 140,739. The gap widens with the loss ratio, which is
    exactly backwards: the worse the account, the further the headline
    drifted from the number the house actually quotes.

    So this no longer computes a price. It calls the same ladder twice -
    once with the trend switched off, once with it on - and reports the
    difference. Every component is therefore a real slice of Method 1's
    own ask, and their sum IS Method 1's ask:

      claims_experience_pct: the increase this account's own loss ratio
      asks for with no inflation at all.

      medical_trend_pct: what adding the inflation points on top costs,
      isolated from the experience underneath it.

      floor_pct: the extra points the house minimum adds when the
      account's own experience asks for less than it. Reported as its
      own bar rather than folded into experience, because "this account
      needs 9%" and "this account needs 2% and the floor is 9%" are
      different conversations.

      census_change_pct: the census-movement premium impact (see
      census_change_pct_from_snapshots) as a % of expiring premium -
      None (shown as "not available") with no prior census snapshot.

      underwriter_adjustment_pct: the hero card's own editable field.

    recommended_premium is Method 1's required_premium plus the census
    and underwriter adjustments in premium terms, so with both at zero
    it is Method 1's number to the cent - not a rounding of it.
    """
    from app.scoring.rules.renewal_rating import (
        DEFAULT_INFLATION_PCT,
        MINIMUM_RENEWAL_INCREASE_PCT,
        renewal_from_loss_ratio,
    )

    if expiring_annual_premium <= 0:
        raise ValueError("expiring_annual_premium must be positive.")
    if not (0 <= loading_pct < 1):
        raise ValueError("loading_pct must be between 0 and 1 (exclusive of 1).")

    if inflation_pts is None:
        inflation_pts = DEFAULT_INFLATION_PCT
    if minimum_increase_pct is _UNSET:
        minimum_increase_pct = MINIMUM_RENEWAL_INCREASE_PCT

    # The ladder itself, three times over: no trend, trend, and the ask
    # actually quoted. Nothing here re-derives what any of them mean.
    no_trend = renewal_from_loss_ratio(
        loss_ratio, expiring_annual_premium, 0.0, loading_pct, minimum_increase_pct=None)
    with_trend = renewal_from_loss_ratio(
        loss_ratio, expiring_annual_premium, inflation_pts, loading_pct, minimum_increase_pct=None)
    quoted = renewal_from_loss_ratio(
        loss_ratio, expiring_annual_premium, inflation_pts, loading_pct,
        minimum_increase_pct=minimum_increase_pct)

    claims_experience_pct = no_trend["renewal_increase_pct"]
    medical_trend_pct = round(with_trend["renewal_increase_pct"] - claims_experience_pct, 2)
    floor_pct = round(quoted["renewal_increase_pct"] - with_trend["renewal_increase_pct"], 2)

    effective_census_change_pct = census_change_pct if census_change_pct is not None else 0.0
    underwriter_adjustment_pct = round(underwriter_adjustment_pct, 2)
    adjustment_pct = effective_census_change_pct + underwriter_adjustment_pct

    # Method 1's own premium, then the named adjustments on top of it in
    # premium terms. Adding percentage points to a premium the ladder
    # already rounded would leave the hero a few dirhams off the figure
    # the rest of the portal quotes.
    #
    # `required_premium` is the ask as the rating card PUBLISHED it. The
    # published loss ratio is rounded to four places, so re-running the
    # ladder on it lands a couple of hundred dirhams from the premium
    # that ratio was originally computed from - close enough to look
    # like a bug and far enough to be one.
    method_1_premium = (required_premium if required_premium is not None
                        else quoted["required_premium"])
    recommended_premium = round(
        method_1_premium + expiring_annual_premium * adjustment_pct / 100, 2)
    total_pct = round((recommended_premium / expiring_annual_premium - 1) * 100, 2)

    return {
        "claims_experience_pct": claims_experience_pct,
        "medical_trend_pct": medical_trend_pct,
        "floor_pct": floor_pct,
        "floor_applied": quoted["floor_applied"],
        "minimum_increase_pct": minimum_increase_pct,
        "census_change_pct": census_change_pct,
        "underwriter_adjustment_pct": underwriter_adjustment_pct,
        "total_pct": total_pct,
        "recommended_premium": recommended_premium,
        # Method 1's own ask, carried through untouched so a screen can
        # show what the adjustments were applied TO.
        "method_1_required_premium": round(method_1_premium, 2),
        "method_1_increase_pct": round(
            (method_1_premium / expiring_annual_premium - 1) * 100, 2),
        "loss_ratio": quoted["loss_ratio"],
        "trended_loss_ratio": quoted["trended_loss_ratio"],
        "inflation_pts": inflation_pts,
        "loading_pct": loading_pct,
        "expiring_annual_premium": round(expiring_annual_premium, 2),
        "authority_threshold_pct": authority_threshold_pct,
        "within_authority": abs(underwriter_adjustment_pct) <= authority_threshold_pct,
    }
