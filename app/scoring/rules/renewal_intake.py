"""Opening a renewal straight from the book, rather than re-keying it.

Every account due for renewal is already fully described inside the
portal: the Membership export (app/ingestion/portfolio_members.py) knows
its lives, categories, policy term and per-member premium, and the Claims
export knows its experience. Making an underwriter open "New Case" and
re-type the company name, renewal date, headcount and premium - for an
account HealthCross already has on its own book - is pure duplicate data
entry, and every re-keyed field is a chance for the case to disagree with
the book it was copied from.

So this module derives a renewal case's own opening state directly from
the membership rows: which policy term is the one actually renewing, who
is on it, what each member's existing annual rate is, and what the
account's expiring premium therefore comes to. The API layer
(app/api/routes_portfolio_analysis.py's open_renewal_intake) turns that
into a real Case plus its seeded census, so the only thing left for the
underwriter to actually upload is what the book genuinely does NOT hold -
the expiring table of benefits, the renewal quote, and any broker
documents.

Nothing here writes to the database or imports the ORM; it works on the
same plain member dicts as portfolio_analysis, so it stays unit-testable
against hand-built rows.
"""
from collections import Counter
from datetime import date as date_cls
from typing import Dict, List, Optional, Tuple

from app.scoring.rules.portfolio_analysis import _claim_matches_period, resolve_master_client


def account_members(
    members: List[dict],
    master_client: str,
    subgroup_master_by_name: Optional[Dict[str, str]] = None,
) -> List[dict]:
    """Every membership row belonging to one master client, across all of
    its policy years - subgroups roll up into their master exactly the
    way they do everywhere else in Portfolio Analysis (see
    resolve_master_client), so an account booked as five subgroups opens
    as one renewal, not five.
    """
    target = (master_client or "").strip().casefold()
    if not target:
        return []
    return [
        m for m in members
        if (resolve_master_client(m, subgroup_master_by_name) or "").strip().casefold() == target
    ]


def current_term(members: List[dict]) -> Tuple[Optional[date_cls], Optional[date_cls]]:
    """The policy term that is actually renewing: the latest one on the
    account. An account that has already renewed once inside the export
    carries a row per member per policy year sharing one beneficiary ID
    (see _claim_matches_period's note), so "the current term" is the term
    with the latest end date - not simply every row for the account.

    The term's start is the earliest start among the rows sharing that
    end date rather than the latest, so a member endorsed on mid-term
    (whose own row still carries the scheme's term dates) can't pull the
    term start forward.
    """
    ends = [m.get("policy_end_date") for m in members if m.get("policy_end_date")]
    if not ends:
        return None, None
    term_end = max(ends)
    starts = [
        m.get("policy_start_date") for m in members
        if m.get("policy_end_date") == term_end and m.get("policy_start_date")
    ]
    return (min(starts) if starts else None), term_end


def current_term_members(members: List[dict]) -> List[dict]:
    """Just the rows on the renewing term (see current_term). Rows with no
    policy_end_date at all are kept only when the account has no dated
    rows whatsoever, so an undated export still opens a case rather than
    seeding an empty census.
    """
    _, term_end = current_term(members)
    if term_end is None:
        return list(members)
    return [m for m in members if m.get("policy_end_date") == term_end]


def term_member_windows(members: List[dict]) -> Dict[str, List[Tuple]]:
    """Each member's own exposure window(s) on the renewing term, keyed by
    beneficiary ID - what a claim line has to fall inside to belong to
    this renewal rather than to the account's previous policy year.

    A member's own endorsement dates take precedence over the scheme term
    where present, since a member who joined late or left early was only
    exposed for their own part of it. One ID can map to more than one
    window (a member endorsed off and back on again), hence a list.
    """
    windows: Dict[str, List[Tuple]] = {}
    for m in members:
        beneficiary_id = m.get("beneficiary_id")
        if not beneficiary_id:
            continue
        start = m.get("member_start_date") or m.get("policy_start_date")
        end = m.get("member_end_date") or m.get("policy_end_date")
        windows.setdefault(beneficiary_id, []).append((start, end))
    return windows


def claim_belongs_to_term(patient_id, date_of_treatment, windows: Dict[str, List[Tuple]]) -> bool:
    """Whether one claim line belongs to the renewing term, using exactly
    the same period rule the rest of Portfolio Analysis uses (see
    _claim_matches_period) - half-open, so a claim treated on the renewal
    date itself belongs to the incoming policy year, not the expiring one.
    Sharing that one implementation is deliberate: a renewal case seeded
    with claims under a slightly different rule would disagree with the
    Loss Ratio board for the very same account.
    """
    if not patient_id:
        return False
    return any(
        _claim_matches_period(date_of_treatment, start, end)
        for start, end in windows.get(patient_id, ())
    )


def member_annual_rate(member: dict) -> Optional[float]:
    """A member's own EXISTING annual rate - what the renewal is priced
    off, per member.

    The export carries both a full-year GrossPremium and an
    ActualGrossPremium pro-rated by the member's own join/leave dates. A
    rate is an annual figure by definition, so the full-year GrossPremium
    is the right basis here: a member who joined in month 10 is still on
    the same annual rate as everyone else in their category, they've just
    been charged three months of it. Falls back to the pro-rated figure
    only when the export carries no full-year premium at all, so a member
    still contributes something rather than showing as unrated.
    """
    gross = member.get("gross_premium")
    if gross:
        return float(gross)
    actual = member.get("actual_gross_premium")
    return float(actual) if actual else None


def _mode(values: List[Optional[str]]) -> Optional[str]:
    present = [v for v in values if v]
    if not present:
        return None
    return Counter(present).most_common(1)[0][0]


def renewal_intake_profile(
    members: List[dict],
    master_client: str,
    subgroup_master_by_name: Optional[Dict[str, str]] = None,
) -> dict:
    """Everything needed to open this account's renewal case, derived from
    the book alone.

    `annualised_premium` (headcount x each member's own annual rate) is
    what the case's own current_annual_premium is opened at, because a
    renewal is quoted against a full year of the expiring rates - not
    against `booked_premium`, the pro-rated amount actually charged over
    the expiring term, which is lower on any account that had joiners
    part-way through and would understate the expiring price it is being
    renewed against. Both are returned so the difference stays visible
    rather than being an invisible modelling choice.
    """
    account = account_members(members, master_client, subgroup_master_by_name)
    term_start, term_end = current_term(account)
    term_members = current_term_members(account)

    annualised = 0.0
    booked = 0.0
    rated = 0
    by_category: Dict[str, dict] = {}
    for m in term_members:
        category = (m.get("category") or "Unspecified").strip() or "Unspecified"
        bucket = by_category.setdefault(
            category, {"category": category, "member_count": 0, "rated_member_count": 0, "annual_premium": 0.0}
        )
        bucket["member_count"] += 1
        booked += float(m.get("actual_gross_premium") or 0.0)
        rate = member_annual_rate(m)
        if rate is not None:
            rated += 1
            annualised += rate
            bucket["rated_member_count"] += 1
            bucket["annual_premium"] += rate

    for bucket in by_category.values():
        bucket["annual_premium"] = round(bucket["annual_premium"], 2)
        bucket["average_rate"] = (
            round(bucket["annual_premium"] / bucket["rated_member_count"], 2)
            if bucket["rated_member_count"] else None
        )

    relations = Counter((m.get("relation") or "Unspecified").strip().title() for m in term_members)

    return {
        "master_client": master_client,
        "member_count": len(term_members),
        "rated_member_count": rated,
        "policy_start_date": term_start,
        "policy_end_date": term_end,
        "annualised_premium": round(annualised, 2),
        "booked_premium": round(booked, 2),
        "average_annual_rate": round(annualised / rated, 2) if rated else None,
        "region": _mode([m.get("region") for m in term_members]),
        "product": _mode([m.get("product_name") for m in term_members]),
        "prior_term_member_count": len(account) - len(term_members),
        "by_category": sorted(by_category.values(), key=lambda b: b["category"]),
        "by_relation": [{"relation": k, "member_count": v} for k, v in sorted(relations.items())],
    }


def census_rows_from_members(members: List[dict]) -> List[dict]:
    """Membership rows mapped onto the census shape a case works in (see
    CensusRecord), so the Renewal Bench's census-driven views - the
    demographic/exposed-risk analysis, the category-level Existing
    Premium build-up, census movement at the next renewal - all work off
    the book's own data without the underwriter re-uploading a member
    list they already gave the portal.

    `existing_annual_rate` is carried across so Existing Premium adds up
    per member per category on its own (see renewal_bench_metrics's
    existing_premium_breakdown) rather than depending on a separately-
    imported rate card.
    """
    rows: List[dict] = []
    for m in members:
        rows.append({
            "employee_ref": m.get("beneficiary_id"),
            "category": (m.get("category") or None),
            # Carried so the NEXT renewal can match this population member
            # for member rather than only by headcount - see
            # app/scoring/rules/member_movement.py.
            "date_of_birth": m.get("date_of_birth"),
            "age": m.get("age"),
            "gender": m.get("gender"),
            "marital_status": m.get("marital_status"),
            "relation": m.get("relation"),
            "emirates": m.get("residence_emirate"),
            "nationality": m.get("nationality"),
            "nationality_zone": m.get("nationality_zone"),
            "policy_start_date": m.get("policy_start_date"),
            "policy_end_date": m.get("policy_end_date"),
            "member_start_date": m.get("member_start_date"),
            "member_end_date": m.get("member_end_date"),
            "existing_annual_rate": member_annual_rate(m),
        })
    return rows
