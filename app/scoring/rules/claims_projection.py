"""Standardized annual claims projection ("burning cost" method).

Fixed methodology agreed for how claims experience gets projected:
  1. Average the first 6 FULL months of paid claims (the caller is
     responsible for excluding any partial/stub first month - a policy
     that started mid-month should not have that stub month counted as a
     complete one, since it understates the true monthly run-rate).
  2. Annualize (x12).
  3. Add a flat IBNR loading (default 10%) rather than relying on an
     insurer-supplied IBNR estimate, which is inconsistent across reports
     and not always broken out the same way.
  4. Divide by the average of the opening and closing member counts (taken
     from the claims report's own population census) to get an annual
     burning cost per member.
  5. Multiply by the CURRENT census member count (the actual submission
     under review right now) to get a projected claims cost sized to this
     specific group.
  6. Apply trend/inflation.
  7. Apply credibility as a straight multiplier on the experience-based
     figure (see note below on the manual-rate complement).
  8. Gross up for commission/OPEX loading via division by (1 - loading),
     not a multiplicative add-on.

Note on credibility: full actuarial credibility rating blends
Z * Experience + (1-Z) * Manual/Book Rate. This applies Z as a straight
multiplier on the experience-based projection because no manual/book rate
is currently available for this segment. If one becomes available, blend
it in rather than using this simplified form.
"""
from dataclasses import dataclass
from typing import List, Optional

DEFAULT_INFLATION_PCT = 0.075
DEFAULT_CREDIBILITY_PCT = 0.90
DEFAULT_IBNR_PCT = 0.10
DEFAULT_LOADING_PCT = 0.28


@dataclass
class ClaimsProjectionAssumptions:
    inflation_pct: float = DEFAULT_INFLATION_PCT
    credibility_pct: float = DEFAULT_CREDIBILITY_PCT
    ibnr_pct: float = DEFAULT_IBNR_PCT
    loading_pct: float = DEFAULT_LOADING_PCT


def project_annual_claims(
    six_month_paid_claims: List[float],
    opening_members: int,
    closing_members: int,
    current_census_members: int,
    assumptions: Optional[ClaimsProjectionAssumptions] = None,
) -> dict:
    if len(six_month_paid_claims) != 6:
        raise ValueError("Expected exactly 6 monthly paid-claims values (first full 6 months).")
    if opening_members <= 0 or closing_members <= 0 or current_census_members <= 0:
        raise ValueError("Member counts must be positive.")

    a = assumptions or ClaimsProjectionAssumptions()

    avg_month = sum(six_month_paid_claims) / 6
    annualized = avg_month * 12
    with_ibnr = annualized * (1 + a.ibnr_pct)

    avg_report_members = (opening_members + closing_members) / 2
    burning_cost_per_member = with_ibnr / avg_report_members

    projected_current_group = burning_cost_per_member * current_census_members
    trended = projected_current_group * (1 + a.inflation_pct)
    credible = trended * a.credibility_pct
    final_projected_claims = credible / (1 - a.loading_pct)

    return {
        "avg_month": round(avg_month, 2),
        "annualized": round(annualized, 2),
        "with_ibnr": round(with_ibnr, 2),
        "avg_report_members": avg_report_members,
        "burning_cost_per_member": round(burning_cost_per_member, 2),
        "projected_current_group": round(projected_current_group, 2),
        "trended": round(trended, 2),
        "credible": round(credible, 2),
        "final_projected_claims": round(final_projected_claims, 2),
        "assumptions_used": {
            "inflation_pct": a.inflation_pct,
            "credibility_pct": a.credibility_pct,
            "ibnr_pct": a.ibnr_pct,
            "loading_pct": a.loading_pct,
        },
    }
