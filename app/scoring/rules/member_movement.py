"""Who actually left, who joined, and what each of them claimed.

Census Movement (see routes_cases' get_census_movement) compares the
expiring and renewal censuses as COUNTS per relation: "Employees 8 -> 7,
Children 9 -> 8". That answers how many, never which - and at renewal
"which" is the question that changes the price. Two lives coming off a
20-life scheme is a routine -10% headcount adjustment if they were
healthy, and the single most important fact about the renewal if they
were the two members who drove the year's claims.

This module matches the renewal census back to the expiring population
member by member, then splits the expiring year's claims three ways:
what the continuing members cost, what the leavers cost, and therefore
what experience actually carries forward into the renewal. An underwriter
pricing the continuing population off total incurred claims - leavers
included - is charging the remaining members for claims that walked out
the door.

Matching is on date of birth plus the demographic identity (relation,
gender, nationality), never on age alone: the same person is 42 on the
expiring census and 43 on the renewal one, so age matches are guaranteed
to be off by one and would report the entire population as leavers plus
an equal number of joiners. Where a census carries no DOB at all (older
uploads predating CensusRecord.date_of_birth) the match degrades to
demographics with an age window that allows for exactly that one-year
shift, which is weaker and is reported as such rather than presented
with false confidence.

Pure functions over plain dicts - no ORM, no database.
"""
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

#: How a matched pair was established, strongest first. Surfaced on every
#: continuing member so an underwriter can see which conclusions rest on
#: an exact identity and which on a demographic inference.
MATCH_EXACT_DOB = "dob"
MATCH_DEMOGRAPHIC = "demographic"


def _norm(value: Optional[str]) -> str:
    return str(value).strip().casefold() if value not in (None, "") else ""


def _dob_key(record: dict) -> Optional[tuple]:
    dob = record.get("date_of_birth")
    if not dob:
        return None
    return (dob, _norm(record.get("relation")), _norm(record.get("gender")))


def _demographic_key(record: dict) -> tuple:
    return (
        _norm(record.get("relation")),
        _norm(record.get("gender")),
        _norm(record.get("nationality")),
    )


def match_members(
    expiring: List[dict],
    renewal: List[dict],
    age_shift: int = 1,
    age_tolerance: int = 1,
) -> dict:
    """Pair the renewal census against the expiring one.

    Two passes, strongest evidence first. Every member matched on DOB is
    taken out of the pool before demographic matching runs, so a weaker
    rule can never steal a member away from an exact match.

    `age_shift` is the year that passes between the two censuses; a
    member aged N on the expiring census is expected at N + age_shift on
    the renewal one. `age_tolerance` allows for the ragged edge around
    birthdays, since each census fixes age at its own policy start.

    Returns continuing/leavers/joiners, where a leaver is an expiring
    member with no counterpart in the renewal census and a joiner is the
    reverse. Newborns added mid-term show up as joiners, which is why a
    scheme can lose four members and gain two while the headcount only
    reports "-2".
    """
    unmatched_expiring = list(expiring)
    unmatched_renewal = list(renewal)
    continuing: List[dict] = []

    # --- pass 1: exact date of birth ---------------------------------
    expiring_by_dob: Dict[tuple, List[dict]] = defaultdict(list)
    for record in unmatched_expiring:
        key = _dob_key(record)
        if key:
            expiring_by_dob[key].append(record)

    still_unmatched_renewal = []
    matched_expiring_ids = set()
    for candidate in unmatched_renewal:
        key = _dob_key(candidate)
        pool = expiring_by_dob.get(key) if key else None
        if pool:
            partner = pool.pop(0)
            matched_expiring_ids.add(id(partner))
            continuing.append({"expiring": partner, "renewal": candidate, "match": MATCH_EXACT_DOB})
        else:
            still_unmatched_renewal.append(candidate)

    unmatched_expiring = [r for r in unmatched_expiring if id(r) not in matched_expiring_ids]
    unmatched_renewal = still_unmatched_renewal

    # --- pass 2: demographic identity, with the one-year age shift ----
    expiring_by_demo: Dict[tuple, List[dict]] = defaultdict(list)
    for record in unmatched_expiring:
        expiring_by_demo[_demographic_key(record)].append(record)

    still_unmatched_renewal = []
    matched_expiring_ids = set()
    for candidate in unmatched_renewal:
        pool = expiring_by_demo.get(_demographic_key(candidate), [])
        renewal_age = candidate.get("age")
        partner = None
        for record in pool:
            expiring_age = record.get("age")
            if renewal_age is None or expiring_age is None:
                # No age on either side leaves demographics as the only
                # evidence; accept it rather than forcing a false leaver,
                # but it is still reported as a demographic match.
                partner = record
                break
            if abs(renewal_age - (expiring_age + age_shift)) <= age_tolerance:
                partner = record
                break
        if partner is not None:
            pool.remove(partner)
            matched_expiring_ids.add(id(partner))
            continuing.append({"expiring": partner, "renewal": candidate, "match": MATCH_DEMOGRAPHIC})
        else:
            still_unmatched_renewal.append(candidate)

    leavers = [r for r in unmatched_expiring if id(r) not in matched_expiring_ids]
    joiners = still_unmatched_renewal

    exact = sum(1 for c in continuing if c["match"] == MATCH_EXACT_DOB)
    return {
        "continuing": continuing,
        "leavers": leavers,
        "joiners": joiners,
        "expiring_count": len(expiring),
        "renewal_count": len(renewal),
        "continuing_count": len(continuing),
        "leaver_count": len(leavers),
        "joiner_count": len(joiners),
        "exact_match_count": exact,
        "demographic_match_count": len(continuing) - exact,
        **_reliability(expiring, renewal, continuing, leavers, joiners, exact),
    }


#: Below this share of continuing members matched on date of birth, the
#: pairing is guesswork and the split it produces is not evidence.
RELIABLE_EXACT_MATCH_SHARE = 0.5


def _reliability(expiring, renewal, continuing, leavers, joiners, exact) -> dict:
    """Whether this comparison is worth acting on.

    The matching pairs on date of birth first and falls back to
    (relation, gender, age). The fallback is a guess, and when it is
    doing ALL the work the result stops being a movement report and
    becomes an artefact of two rosters that do not share identifiers.

    Safran showed it: 182 expiring against a 178-member renewal census,
    89 continuing - every one of them matched on demographics, none on
    date of birth - and therefore 93 leavers and 89 joiners. Almost
    certainly the same people twice, with the census carrying no dates of
    birth to match on. The panel then reported that leavers took half the
    year's claims with them and that the renewing members bring only
    1,027,160 of experience rather than 2,043,338 - halving the account's
    own history on a matching failure.

    So the report says when it cannot be trusted, and says why.
    """
    reasons = []
    share = (exact / len(continuing)) if continuing else 0.0
    churn = ((len(leavers) + len(joiners)) / len(expiring)) if expiring else 0.0

    if continuing and share < RELIABLE_EXACT_MATCH_SHARE:
        reasons.append(
            f"{len(continuing) - exact} of {len(continuing)} continuing members were paired on "
            f"demographics rather than date of birth"
        )
    if churn > 1.0:
        reasons.append(
            f"{len(leavers)} left and {len(joiners)} joined against {len(expiring)} expiring - "
            f"more movement than the account has members"
        )
    if not exact and (leavers or joiners):
        reasons.append(
            "no member matched on date of birth, so the two rosters may not share identifiers at all"
        )

    return {
        "exact_match_share": round(share, 4),
        "reliable": not reasons,
        "unreliable_because": reasons,
        "caveat": (
            "This comparison is not reliable enough to price from: " + "; ".join(reasons) +
            ". The likeliest explanation is that the renewal census carries no dates of birth, "
            "so continuing members could not be recognised and appear as leavers and joiners at "
            "once. Population movement, read off the book's own cover dates, does not depend on "
            "matching two rosters."
        ) if reasons else None,
    }


def claims_by_member_ref(claims: List[dict]) -> Dict[str, dict]:
    """Roll a claims ledger up per member, split paid vs outstanding.

    Keyed by the ledger's own patient_id, which is the same identifier a
    census seeded from the book carries as employee_ref (see
    renewal_intake's census_rows_from_members) - so a case opened from
    the book joins claims to members with no manual reconciliation. A
    manually-uploaded census whose refs don't match the ledger simply
    produces no per-member claims, which the caller reports rather than
    silently showing everyone as claim-free.
    """
    by_ref: Dict[str, dict] = {}
    for claim in claims:
        ref = claim.get("patient_id")
        if not ref:
            continue
        bucket = by_ref.setdefault(str(ref), {"paid": 0.0, "outstanding": 0.0, "claim_count": 0})
        amount = claim.get("final_amount") or 0.0
        status = str(claim.get("claim_status") or "").lower()
        if "paid" in status or "validated" in status:
            bucket["paid"] += amount
        else:
            bucket["outstanding"] += amount
        bucket["claim_count"] += 1
    for bucket in by_ref.values():
        bucket["incurred"] = round(bucket["paid"] + bucket["outstanding"], 2)
        bucket["paid"] = round(bucket["paid"], 2)
        bucket["outstanding"] = round(bucket["outstanding"], 2)
    return by_ref


def _member_label(record: dict) -> dict:
    return {
        "employee_ref": record.get("employee_ref"),
        "relation": record.get("relation"),
        "gender": record.get("gender"),
        "age": record.get("age"),
        "date_of_birth": record.get("date_of_birth"),
        "nationality": record.get("nationality"),
        "category": record.get("category"),
        "existing_annual_rate": record.get("existing_annual_rate"),
    }


def _empty_claims() -> dict:
    return {"paid": 0.0, "outstanding": 0.0, "incurred": 0.0, "claim_count": 0}


def movement_with_claims(
    expiring: List[dict],
    renewal: List[dict],
    claims: List[dict],
    age_shift: int = 1,
    age_tolerance: int = 1,
) -> dict:
    """Member movement with the expiring year's claims attached to it.

    The headline output is `continuing_incurred` - the claims belonging
    to members who are actually still on the scheme. That, not total
    incurred, is the experience the renewal population carries forward.
    `leaver_incurred` is reported beside it rather than merely subtracted,
    because a large leaver share is itself the finding: it says the
    expiring loss ratio overstates the risk being renewed.

    Joiners carry no claims by construction - they were not on the
    expiring policy - so they add exposure to the renewal without adding
    history, which is why they are counted separately rather than folded
    into the continuing population.
    """
    matched = match_members(expiring, renewal, age_shift=age_shift, age_tolerance=age_tolerance)
    by_ref = claims_by_member_ref(claims)

    def claims_for(record: dict) -> dict:
        ref = record.get("employee_ref")
        return by_ref.get(str(ref), _empty_claims()) if ref else _empty_claims()

    continuing_rows = []
    continuing_totals = _empty_claims()
    for pair in matched["continuing"]:
        member_claims = claims_for(pair["expiring"])
        continuing_rows.append(
            {
                **_member_label(pair["expiring"]),
                "renewal_age": pair["renewal"].get("age"),
                "match": pair["match"],
                "claims": member_claims,
            }
        )
        for field in ("paid", "outstanding", "incurred", "claim_count"):
            continuing_totals[field] += member_claims[field]

    leaver_rows = []
    leaver_totals = _empty_claims()
    for record in matched["leavers"]:
        member_claims = claims_for(record)
        leaver_rows.append({**_member_label(record), "claims": member_claims})
        for field in ("paid", "outstanding", "incurred", "claim_count"):
            leaver_totals[field] += member_claims[field]

    joiner_rows = [_member_label(record) for record in matched["joiners"]]

    total_incurred = round(continuing_totals["incurred"] + leaver_totals["incurred"], 2)
    for totals in (continuing_totals, leaver_totals):
        for field in ("paid", "outstanding", "incurred"):
            totals[field] = round(totals[field], 2)

    # Claims whose patient_id matches nobody on the expiring census -
    # reported rather than dropped, since a large unattributed figure
    # means the census and the ledger aren't the same population and the
    # whole split below should be treated with suspicion.
    known_refs = {str(r.get("employee_ref")) for r in expiring if r.get("employee_ref")}
    unattributed = round(
        sum(v["incurred"] for ref, v in by_ref.items() if ref not in known_refs), 2
    )

    leaver_share = (
        round(leaver_totals["incurred"] / total_incurred, 4) if total_incurred else 0.0
    )

    return {
        **{k: v for k, v in matched.items() if k not in ("continuing", "leavers", "joiners")},
        "continuing": continuing_rows,
        "leavers": leaver_rows,
        "joiners": joiner_rows,
        "continuing_claims": continuing_totals,
        "leaver_claims": leaver_totals,
        "total_incurred": total_incurred,
        "leaver_claims_share": leaver_share,
        "unattributed_incurred": unattributed,
        "claims_matched": bool(by_ref) and unattributed < total_incurred,
    }
