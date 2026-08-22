"""Extra Renewal Bench metrics beyond the Method A/B scorecard itself (see
renewal_rating.py): per-case claim frequency/severity/claimant ratio, and
the Renewal Drivers waterfall that decomposes the total renewal increase
into named contributing factors (claims experience, medical trend, census
change, underwriter adjustment) so the increase is traceable rather than
one opaque percentage - matching the approved Renewal Bench mockup.
"""
from typing import List, Optional


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
    annualized_incurred_claims: float,
    trended_claims: float,
    current_annual_premium: float,
    loading_pct: float,
    census_change_pct: Optional[float] = None,
    underwriter_adjustment_pct: float = 0.0,
    authority_threshold_pct: float = 15.0,
) -> dict:
    """Decomposes the total renewal movement into named contributing
    factors, each an additive percentage-point contribution against
    current_annual_premium (so they sum exactly to total_pct, unlike a
    multiplicative build-up):

      claims_experience_pct: what the increase would be from this case's
      own incurred claims LEVEL alone (Method A's annualized_incurred_claims,
      grossed up by the same loading), before any inflation trend.

      medical_trend_pct: the INCREMENTAL effect of applying inflation on
      top of that same claims level - isolates trend from experience.

      census_change_pct: the census-movement premium impact (see
      census_change_pct_from_snapshots) as a % of current premium - None
      (shown as "not available") when there's no prior census snapshot.

      underwriter_adjustment_pct: a manual override, 0 by default - the
      Recommended Renewal Premium hero card's own editable field.

    recommended_premium is current_annual_premium grossed up by exactly
    total_pct, so the hero card's own number always reconciles with the
    waterfall shown alongside it. within_authority flags whether
    |underwriter_adjustment_pct| stays inside authority_threshold_pct - a
    visible, overridable assumption (like inflation_pct/loading_pct
    elsewhere in this module), not a hidden business rule.
    """
    if current_annual_premium <= 0:
        raise ValueError("current_annual_premium must be positive.")
    if not (0 <= loading_pct < 1):
        raise ValueError("loading_pct must be between 0 and 1 (exclusive of 1).")

    required_premium_no_trend = annualized_incurred_claims / (1 - loading_pct)
    required_premium_with_trend = trended_claims / (1 - loading_pct)

    claims_experience_pct = round((required_premium_no_trend / current_annual_premium - 1) * 100, 2)
    medical_trend_pct = round(
        (required_premium_with_trend / current_annual_premium - 1) * 100 - claims_experience_pct, 2
    )
    effective_census_change_pct = census_change_pct if census_change_pct is not None else 0.0
    underwriter_adjustment_pct = round(underwriter_adjustment_pct, 2)

    total_pct = round(
        claims_experience_pct + medical_trend_pct + effective_census_change_pct + underwriter_adjustment_pct, 2
    )
    recommended_premium = round(current_annual_premium * (1 + total_pct / 100), 2)

    return {
        "claims_experience_pct": claims_experience_pct,
        "medical_trend_pct": medical_trend_pct,
        "census_change_pct": census_change_pct,
        "underwriter_adjustment_pct": underwriter_adjustment_pct,
        "total_pct": total_pct,
        "recommended_premium": recommended_premium,
        "authority_threshold_pct": authority_threshold_pct,
        "within_authority": abs(underwriter_adjustment_pct) <= authority_threshold_pct,
    }
