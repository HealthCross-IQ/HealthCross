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
from typing import Dict, List, Optional, Sequence, Tuple

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
        # The network this account is actually on, off its own membership
        # rows. The rate card needs Product, Network and TPA to price a
        # census; two of the three are already on the book, and asking an
        # underwriter to re-pick those on the Benefits tab is asking them
        # to retype what the portal holds. TPA is not on the membership
        # export at all, so it stays a manual pick rather than a guess.
        "network": _mode([m.get("network_type_raw") for m in term_members]),
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


#: Below this share of the roster holding one end date, that date is not
#: the term end - it is a coincidence. Refusing to guess is the point.
ROSTER_TERM_END_MAJORITY = 0.5


def roster_term_end(members: List[dict]) -> Tuple[Optional[date_cls], Optional[str]]:
    """The term's own end date, read off the roster rather than the
    policy_end_date field, plus a warning where the two disagree.

    The exports do not agree with each other about the same day: the
    claims export puts this scheme's policy end at 2026-10-01 and the
    membership export at 2026-09-30. A rule written against either field
    returns a confident, silent zero on the other - every member reads as
    deleted, and nothing says so.

    The roster cannot drift like that. Whatever convention an export
    uses, the members who run the full term all share one end date, and
    that date IS the term end. Where no date is held by a clear majority
    the account is not a normal renewal and this refuses to guess.
    """
    ends = [m.get("member_end_date") for m in current_term_members(members) if m.get("member_end_date")]
    if not ends:
        _, policy_end = current_term(members)
        return policy_end, None

    counts: Dict[date_cls, int] = {}
    for end in ends:
        counts[end] = counts.get(end, 0) + 1
    term_end, held_by = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    if held_by / len(ends) < ROSTER_TERM_END_MAJORITY:
        return None, (
            f"No single cover end date is shared by most of the roster "
            f"(the commonest, {term_end}, covers {held_by} of {len(ends)}). "
            f"Set the cut date explicitly."
        )

    warning = None
    policy_ends = {m.get("policy_end_date") for m in current_term_members(members)
                   if m.get("policy_end_date")}
    if policy_ends and term_end not in policy_ends:
        warning = (
            f"The roster's cover ends {term_end} but policy_end_date says "
            f"{sorted(policy_ends)[-1]}. The exports disagree about the same day - "
            f"the roster was used."
        )
    return term_end, warning


def continuing_and_leaving(
    members: List[dict],
    as_at: Optional[date_cls] = None,
) -> Tuple[List[dict], List[dict]]:
    """The renewing term's members split by whether they are still on
    risk at the cut date.

    `as_at` is the cut date, and is meant to be set per renewal - the
    date an underwriter is actually pricing to. Left unset it is read
    off the roster (see roster_term_end) rather than from a policy field,
    because the exports disagree about that field by a day.

    A member whose cover ends ON the cut date is continuing - the term
    runs to the end of that day. A member whose cover ended in April is
    part of the expiring year's cost and none of the incoming year's
    exposure.
    """
    term_members = current_term_members(members)
    if as_at is None:
        as_at, _ = roster_term_end(members)
    if as_at is None:
        return term_members, []

    continuing, leaving = [], []
    for m in term_members:
        end = m.get("member_end_date")
        start = m.get("member_start_date")
        gone = end is not None and end < as_at
        unstarted = start is not None and start > as_at
        (leaving if (gone or unstarted) else continuing).append(m)
    return continuing, leaving


def claims_by_member_status(
    members: List[dict],
    claims_by_beneficiary: Dict[str, List[dict]],
    as_at: Optional[date_cls] = None,
) -> dict:
    """What the expiring year cost, split between the members who are
    renewing and the members who are not.

    A renewal quoted on the account's headline loss ratio prices the
    incoming year off a population that includes people who will not be
    in it. That is only harmless when leavers cost the same per head as
    stayers, and they routinely do not - a leaver's premium is earned for
    part of a year while their claims are not, so their own loss ratio
    runs high even when their claims are ordinary.

    Splitting the two says which it is. If stripping the leavers moves
    the loss ratio a long way, last year's headline overstates what the
    renewing population actually costs. If it barely moves, the base rate
    is the problem and renewing on headcount carries it straight into the
    new year.
    """
    # Resolved here rather than left to continuing_and_leaving, which
    # works it out internally and keeps it. The date decides the whole
    # split, so a caller must be able to see which one was used.
    supplied = as_at is not None
    warning = None
    if not supplied:
        as_at, warning = roster_term_end(members)
    continuing, leaving = continuing_and_leaving(members, as_at)
    windows = term_member_windows(current_term_members(members))

    def totals(group: List[dict]) -> dict:
        paid = outstanding = premium = 0.0
        lines = 0
        claimants = set()
        for m in group:
            beneficiary_id = m.get("beneficiary_id")
            premium += member_annual_rate(m) or 0.0
            for claim in claims_by_beneficiary.get(beneficiary_id, ()):
                if not claim_belongs_to_term(beneficiary_id, claim.get("date_of_treatment"), windows):
                    continue
                amount = claim.get("final_amount") or 0.0
                status = str(claim.get("claim_status") or "")
                if "outstanding" in status.lower():
                    outstanding += amount
                else:
                    paid += amount
                lines += 1
                claimants.add(beneficiary_id)
        incurred = paid + outstanding
        count = len(group)
        return {
            "member_count": count,
            "paid": round(paid, 2),
            "outstanding": round(outstanding, 2),
            "incurred": round(incurred, 2),
            "premium": round(premium, 2),
            "claim_lines": lines,
            "members_who_claimed": len(claimants),
            "claims_per_member": round(incurred / count, 2) if count else None,
            # Against premium as booked. A leaver's is already prorated in
            # the export, which is exactly why their ratio runs high.
            "loss_ratio": round(incurred / premium, 4) if premium else None,
        }

    continuing_totals = totals(continuing)
    leaving_totals = totals(leaving)
    combined_incurred = continuing_totals["incurred"] + leaving_totals["incurred"]
    combined_premium = continuing_totals["premium"] + leaving_totals["premium"]

    return {
        "as_at": as_at,
        # Set explicitly by the underwriter, or read off the roster. They
        # need to know which, because only one of them is their decision.
        "cut_date_source": "supplied" if supplied else "roster",
        "warning": warning,
        "continuing": continuing_totals,
        "leaving": leaving_totals,
        "total": {
            "member_count": continuing_totals["member_count"] + leaving_totals["member_count"],
            "incurred": round(combined_incurred, 2),
            "premium": round(combined_premium, 2),
            "loss_ratio": round(combined_incurred / combined_premium, 4) if combined_premium else None,
        },
        # The number the renewal actually turns on: what the loss ratio
        # becomes once the people who are not renewing are taken out.
        "loss_ratio_excluding_leavers": continuing_totals["loss_ratio"],
        "leaver_share_of_claims": (
            round(leaving_totals["incurred"] / combined_incurred, 4) if combined_incurred else None
        ),
        "leaver_share_of_members": (
            round(leaving_totals["member_count"] /
                  (continuing_totals["member_count"] + leaving_totals["member_count"]), 4)
            if (continuing_totals["member_count"] + leaving_totals["member_count"]) else None
        ),
    }


def population_movement(
    members: List[dict],
    term_start: Optional[date_cls] = None,
    term_end: Optional[date_cls] = None,
) -> dict:
    """The term's population as it actually moved: opening, joiners,
    leavers, closing - read off each member's own dates.

    The Renewal Bench used to derive this by comparing two census
    snapshots, which answers a different question. A snapshot comparison
    shows what changed between two UPLOADS; on an account whose census
    has not been re-uploaded it shows nothing at all, and reports zero
    movement on a year in which people plainly joined and left. Serviceplan
    lost thirteen members over its term and read 178 -> 178, change 0.

    The roster cannot hide that, because every joiner and leaver carries
    their own dates. Opening is who was on risk on day one; closing is
    who is on risk at the end; the difference is accounted for member by
    member rather than inferred from two totals.

    Exposure is reported alongside the headcounts, because they answer
    different questions. Twelve members who each stayed one month are
    twelve leavers and one member-year, and a renewal priced on headcount
    alone treats them as twelve.
    """
    term_members = current_term_members(members)
    if term_start is None or term_end is None:
        start, end = current_term(term_members)
        term_start = term_start or start
        term_end = term_end or end
    if term_start is None or term_end is None:
        return {"term_start": term_start, "term_end": term_end, "rows": [], "totals": None}

    term_days = (term_end - term_start).days or 1

    def joined_late(m):
        start = m.get("member_start_date")
        return bool(start and start > term_start)

    def left_early(m):
        end = m.get("member_end_date")
        return bool(end and end < term_end)

    buckets: Dict[str, dict] = {}
    for m in term_members:
        relation = (m.get("relation") or "Unspecified").title()
        row = buckets.setdefault(relation, {
            "relation": relation, "opening": 0, "joiners": 0, "leavers": 0,
            "closing": 0, "member_years": 0.0,
            "joiner_premium": 0.0, "leaver_premium": 0.0,
        })
        rate = member_annual_rate(m) or 0.0
        joined, left = joined_late(m), left_early(m)
        if not joined:
            row["opening"] += 1
        else:
            row["joiners"] += 1
            row["joiner_premium"] += rate
        if left:
            row["leavers"] += 1
            row["leaver_premium"] += rate
        else:
            row["closing"] += 1
        # What the member was actually on risk for, not what they were
        # counted as.
        on = max(m.get("member_start_date") or term_start, term_start)
        off = min(m.get("member_end_date") or term_end, term_end)
        row["member_years"] += max(0, (off - on).days) / term_days

    rows = sorted(buckets.values(), key=lambda r: -r["closing"])
    for row in rows:
        row["net_change"] = row["closing"] - row["opening"]
        row["member_years"] = round(row["member_years"], 2)
        row["joiner_premium"] = round(row["joiner_premium"], 2)
        row["leaver_premium"] = round(row["leaver_premium"], 2)

    totals = {
        key: sum(r[key] for r in rows)
        for key in ("opening", "joiners", "leavers", "closing")
    }
    totals["net_change"] = totals["closing"] - totals["opening"]
    totals["member_years"] = round(sum(r["member_years"] for r in rows), 2)
    totals["joiner_premium"] = round(sum(r["joiner_premium"] for r in rows), 2)
    totals["leaver_premium"] = round(sum(r["leaver_premium"] for r in rows), 2)
    totals["net_premium_impact"] = round(totals["joiner_premium"] - totals["leaver_premium"], 2)
    # Headcount at the close against exposure actually carried. They
    # diverge exactly where the churn was, which is the point.
    totals["average_lives"] = round(totals["member_years"], 2)
    totals["turnover_pct"] = (
        round((totals["joiners"] + totals["leavers"]) / totals["opening"], 4)
        if totals["opening"] else None
    )

    return {"term_start": term_start, "term_end": term_end, "rows": rows, "totals": totals}


#: The unreported tail the house prices on - the same 30 days the
#: portfolio Loss Ratio board uses, so the two cannot disagree.
RENEWAL_IBNR_TAIL_DAYS = 30


def renewal_loss_ratio(
    members: List[dict],
    claims_by_beneficiary: Dict[str, List[dict]],
    as_of: Optional[date_cls] = None,
    cut_date: Optional[date_cls] = None,
    ibnr_tail_days: int = RENEWAL_IBNR_TAIL_DAYS,
) -> dict:
    """The house renewal loss ratio, in the order underwriting states it:

      1. Drop the claims of members who are not renewing.
      2. Add IBNR - the active members' own paid run-rate over a 30-day
         unreported tail.
      3. Divide by premium EARNED by those same active members.

    Every step is about keeping the two halves of the ratio describing
    the same thing. Leavers' claims do not carry forward, so they are
    not part of what the renewing population costs. And claims measured
    over part of a year belong against premium earned over that same
    part - earning the premium down is a measurement, where scaling the
    claims up asserts the rest of the year looks like the part observed.
    On KIKO, where one family carried 9% of the claims, that assumption
    is exactly the one that fails.

    Ordering matters and is not cosmetic: IBNR is computed on the ACTIVE
    members' paid claims, after the leavers have gone. Computing it on
    everybody's would reserve a tail for people who are no longer on
    risk.
    """
    as_of = as_of or date_cls.today()
    term_members = current_term_members(members)
    continuing, leaving = continuing_and_leaving(members, cut_date)
    windows = term_member_windows(term_members)
    term_start, term_end = current_term(term_members)

    def split(group):
        paid = outstanding = 0.0
        for m in group:
            bid = m.get("beneficiary_id")
            for claim in claims_by_beneficiary.get(bid, ()):
                if not claim_belongs_to_term(bid, claim.get("date_of_treatment"), windows):
                    continue
                amount = claim.get("final_amount") or 0.0
                if "outstanding" in str(claim.get("claim_status") or "").lower():
                    outstanding += amount
                else:
                    paid += amount
        return paid, outstanding

    active_paid, active_outstanding = split(continuing)
    leaver_paid, leaver_outstanding = split(leaving)

    # Elapsed exposure, per member, on their own cover - not on the
    # scheme term. A member endorsed on in month ten has earned two
    # months of premium, not ten.
    earned = 0.0
    elapsed_days = 0
    for m in continuing:
        rate = member_annual_rate(m) or 0.0
        start = m.get("member_start_date") or term_start
        end = m.get("member_end_date") or term_end
        if not (rate and start and end):
            continue
        on = max(start, term_start) if term_start else start
        off = min(end, as_of, term_end) if term_end else min(end, as_of)
        days = max(0, (off - on).days + 1)
        term_days = ((term_end - term_start).days + 1) if (term_start and term_end) else 365
        earned += rate * min(1.0, days / term_days)
        elapsed_days = max(elapsed_days, days)

    ibnr = round(active_paid / elapsed_days * ibnr_tail_days, 2) if (active_paid and elapsed_days) else 0.0
    incurred = active_paid + active_outstanding + ibnr

    return {
        "as_of": as_of,
        "cut_date": cut_date,
        "active_member_count": len(continuing),
        "deleted_member_count": len(leaving),
        "paid": round(active_paid, 2),
        "outstanding": round(active_outstanding, 2),
        "ibnr": ibnr,
        "ibnr_tail_days": ibnr_tail_days,
        "incurred": round(incurred, 2),
        "earned_premium": round(earned, 2),
        "elapsed_days": elapsed_days,
        "loss_ratio": round(incurred / earned, 4) if earned else None,
        # Reported so the exclusion can be seen rather than taken on
        # trust - a leaver group carrying a large share of the year is
        # the finding, not a footnote.
        "excluded": {
            "paid": round(leaver_paid, 2),
            "outstanding": round(leaver_outstanding, 2),
            "incurred": round(leaver_paid + leaver_outstanding, 2),
            "share_of_all_claims": (
                round((leaver_paid + leaver_outstanding) /
                      (active_paid + active_outstanding + leaver_paid + leaver_outstanding), 4)
                if (active_paid + active_outstanding + leaver_paid + leaver_outstanding) else None
            ),
        },
    }


def compare_against_supplied_census(
    members: List[dict],
    supplied_refs: Sequence[str],
    claims_by_beneficiary: Dict[str, List[dict]],
    as_of: Optional[date_cls] = None,
    cut_date: Optional[date_cls] = None,
    elsewhere_in_book: Optional[Dict[str, str]] = None,
) -> dict:
    """The book's active roster against a census the broker has sent.

    A renewal census usually differs from the book, and the difference
    is the account's shape at renewal rather than a reconciliation
    chore. KIKO arrived with 69 names against 71 on the book: five
    leaving - four of them one household carrying 9% of the year's
    claims - and three joining with no history at all.

    Both scenarios are returned, never one. Priced on the book's own
    roster the account is what it has been; priced on the broker's list
    it is what it will be, and an underwriter needs to see the two
    beside each other to know whether the difference is worth anything.
    Showing only the second invites a better ratio to be quoted without
    anyone seeing what produced it.

    Matching is on beneficiary reference alone. Where the two sides do
    not share identifiers this reports that rather than falling back to
    pairing on demographics - a guessed pairing produced 93 phantom
    leavers on another account and halved its experience.

    A name on the census that is not on this account's roster is not
    automatically a joiner. Groups are booked as several contracts far
    more often than they are quoted as several: KIKO's census covers
    seven entities and comparing it against the one master client the
    renewal is filed under reported 26 joiners, of which 23 were
    already on the book under a sibling contract. Pass
    ``elsewhere_in_book`` - every reference in the portfolio mapped to
    the account it sits on - and those are named as what they are
    instead of being counted as new lives.
    """
    active, _ = continuing_and_leaving(members, cut_date)
    supplied = {str(r).strip() for r in supplied_refs if r is not None and str(r).strip()}
    by_ref = {str(m.get("beneficiary_id") or "").strip(): m for m in active}

    staying = [m for ref, m in by_ref.items() if ref in supplied]
    leaving = [m for ref, m in by_ref.items() if ref not in supplied]
    unmatched = sorted(supplied - set(by_ref))

    # Split the unmatched names: on another contract in the book, or
    # genuinely new. Only the second group has no experience behind it.
    on_other_contracts: Dict[str, List[str]] = {}
    if elsewhere_in_book:
        for ref in unmatched:
            account = elsewhere_in_book.get(ref)
            if account:
                on_other_contracts.setdefault(account, []).append(ref)
    seen_elsewhere = {r for refs in on_other_contracts.values() for r in refs}
    joining = [r for r in unmatched if r not in seen_elsewhere]

    windows = term_member_windows(current_term_members(members))

    def claims_for(group) -> float:
        total = 0.0
        for m in group:
            bid = m.get("beneficiary_id")
            total += sum(
                c.get("final_amount") or 0.0
                for c in claims_by_beneficiary.get(bid, ())
                if claim_belongs_to_term(bid, c.get("date_of_treatment"), windows)
            )
        return round(total, 2)

    def premium_for(group) -> float:
        return round(sum(member_annual_rate(m) or 0.0 for m in group), 2)

    matched = len(staying)
    return {
        "as_at": cut_date,
        "book_active_count": len(active),
        "supplied_count": len(supplied),
        "staying": {"member_count": matched, "claims": claims_for(staying),
                    "premium": premium_for(staying)},
        "leaving": {"member_count": len(leaving), "claims": claims_for(leaving),
                    "premium": premium_for(leaving),
                    "members": [
                        {"beneficiary_id": m.get("beneficiary_id"),
                         "relation": m.get("relation"), "age": m.get("age"),
                         "gender": m.get("gender"),
                         "annual_rate": member_annual_rate(m),
                         "claims": claims_for([m])}
                        for m in sorted(leaving, key=lambda x: -claims_for([x]))
                    ]},
        "joining": {"member_count": len(joining), "references": joining},
        # Named, not counted as joiners: these are on the book already,
        # under another contract. Whether they belong in this renewal is
        # an underwriting question, and it needs the account names to be
        # answerable.
        "on_other_contracts": {
            "member_count": len(seen_elsewhere),
            "accounts": [
                {"master_client": account, "member_count": len(refs), "references": sorted(refs)}
                for account, refs in sorted(on_other_contracts.items(), key=lambda kv: -len(kv[1]))
            ],
            "note": (
                f"{len(seen_elsewhere)} of the {len(unmatched)} names on the census that are not on "
                f"this account's roster are already in the portfolio under "
                f"{len(on_other_contracts)} other contract(s). They are only joiners to this "
                f"renewal if the account is being written as one contract - re-run the comparison "
                f"including those contracts to price it that way."
            ) if seen_elsewhere else None,
        },
        # Both prices. On the book's roster, and on the roster the
        # broker actually sent.
        "on_book_roster": renewal_loss_ratio(members, claims_by_beneficiary,
                                             as_of=as_of, cut_date=cut_date),
        # The same measurement with only the active members the census
        # left out removed. Rows that are not on the active roster at
        # all - prior policy years, members already off risk - stay in,
        # because the loss ratio needs them to build its term windows.
        "on_supplied_census": renewal_loss_ratio(
            [m for m in members
             if str(m.get("beneficiary_id") or "").strip() in supplied
             or str(m.get("beneficiary_id") or "").strip() not in by_ref],
            claims_by_beneficiary, as_of=as_of, cut_date=cut_date),
        # A census that shares no references with the book has not been
        # compared, it has been guessed at, and saying so is the only
        # honest output.
        "reliable": bool(supplied) and matched > 0,
        "warning": (
            f"None of the {len(supplied)} references on the supplied census matches the book. "
            f"The two files do not share member identifiers, so this is not a comparison."
            if supplied and not matched else None
        ),
    }
