"""Portfolio Analysis - checks HealthCross's own already-booked book (see
app/ingestion/portfolio_members.py and portfolio_claims.py) against the New
Business rate card, rather than a single case's own census. For each real
member, what would the CURRENT rate card charge (standard_premium, reusing
price_member exactly as New Business quoting does) vs what was actually
charged (actual_premium) and what was actually claimed (actual_claims)?
Segmented by any dimension (Product, Network, region, ...), this tells you
directly where the rate card is priced right, rich, or thin - not just
whether a case as a whole was profitable, the way the existing
Outcome/recalibration loop does.

Every real network name seen in this book so far (Platinum, Comprehensive,
Premium, Enhanced, Regular, Essential) is one of MSH MENA's own - none of
NAS Neuron's distinctly-named tiers (GN, Restricted, Super Restricted) - so
this book is assumed to be entirely on the MSH MENA TPA. If a future export
turns up a genuine NAS Neuron group, this assumption needs revisiting
rather than silently mis-pricing it.
"""
from collections import defaultdict
from datetime import date as date_cls
from datetime import timedelta
from typing import Dict, List, Optional

from app.reference.network_type_mapping import is_out_of_scope_network_type, map_network_type
from app.reference.treatment_classification import classify_paramedical
from app.scoring.rules.credibility import (
    FULL_CREDIBILITY_MEMBER_YEARS,
    blend_with_complement,
    relativity,
)
from app.scoring.rules.census_summary import census_demographic_summary
from app.scoring.rules.new_business_rating import category_loading_pct, gross_up, price_member

BOOK_TPA = "MSH MENA"

# Temporary stand-in until HealthCross has enough of its own booked NAS
# business to compute real NAS-specific burning cost: the booked book is
# entirely MSH MENA today (see BOOK_TPA above), so a New Business quote
# category priced on a NAS Neuron network is compared against its MSH
# equivalent's real burning cost instead of having no reference data for
# NAS at all. Provided by underwriting (MSH network = NAS network); revisit
# once real NAS book data exists. A couple of known alternate phrasings for
# the same NAS network are included since real spreadsheets aren't always
# worded identically from one upload to the next.
NAS_TO_MSH_NETWORK = {
    "comprehensive": "MSH Platinum",
    # Comprehensive Plus Mediclinic and plain Comprehensive run the same
    # burning cost in practice (confirmed with underwriting), and the
    # booked book has no real members on "Comprehensive Plus Mediclinic"
    # specifically - so GN maps to the network that actually has data.
    "gn": "MSH Comprehensive",
    "gn excluding mediclinic and american": "MSH Comprehensive",
    "gn excluding american mediclinic group": "MSH Comprehensive",
    # The trailing pluses are a richness tier, not punctuation:
    # "Restricted +++" sits ABOVE plain "Restricted", so they must not
    # normalise to the same key.
    "restricted+++": "MSH Premium",
    "restricted": "MSH Enhanced",
    "super restricted zulekha": "MSH Regular",
    "super restricted zulekha group": "MSH Regular",
    "super restricted": "MSH Regular",
}

#: What a NAS network costs relative to the MSH network standing in for
#: it. The book has no NAS experience of its own, so a NAS category is
#: priced off its MSH equivalent - but the two do not cost the same at
#: the same network richness. NAS is one of the largest TPAs in the UAE
#: by volume, and underwriting puts its burning cost 10-15% below MSH's
#: on that buying power. Without this, every NAS category is quoted at
#: an MSH price.
#:
#: Set at 10%, the CAUTIOUS end of the 10-15% range rather than its
#: midpoint - a house decision. The adjustment is a discount applied to
#: the only experience we have, so claiming the larger one buys a lower
#: price on a belief the book cannot yet evidence. Assuming the smaller
#: discount is wrong in the direction that costs nothing.
#:
#: Not a measured figure. It is a single named constant precisely so it
#: can be replaced by one once there is real NAS book data - the same
#: caveat NAS_TO_MSH_NETWORK carries.
NAS_VS_MSH_BURNING_COST_RANGE = (0.85, 0.90)
NAS_VS_MSH_BURNING_COST = 0.90

#: Spellings of the same network that arrive from different uploads.
#: Applied before the lookup, so one canonical key covers all of them.
_NETWORK_SPELLINGS = {
    "zulaikha": "zulekha",
    "zulaykha": "zulekha",
    "excl": "excluding",
    "and": "",
    "&": "",
    "plus": "",
}


def _normalize_network_key(network: str) -> str:
    """One key per network, whatever the upload called it.

    The map used to be keyed on literal lowercased strings, which meant
    "Super Restricted + Zulekha Group" missed "super restricted+
    zulaikha" over a vowel and a suffix - and a miss here is silent and
    expensive. The network then matches nothing in the book, every
    member falls back past the network dimension, and a restricted
    network gets priced off the whole product across every network the
    book carries, rich ones included. That is how a Super Restricted
    category came to be quoted at over AED 8,700 a head.

    So punctuation and spacing are collapsed rather than enumerated, and
    the handful of genuine spelling variants are folded to one form.

    With one exception: a run of pluses at the END of the name is a
    richness tier, not punctuation. "Restricted +++" sits above plain
    "Restricted" and maps to a different MSH network, so stripping the
    pluses would quietly price the richer tier off the cheaper one - the
    same class of silent substitution this function exists to prevent. A
    plus BETWEEN words ("Super Restricted + Zulekha") is just a joiner
    and is dropped as before.
    """
    import re

    raw = (network or "").lower().strip()
    tier = re.search(r"\++$", raw)
    suffix = tier.group(0) if tier else ""
    body = raw[: tier.start()] if tier else raw
    key = re.sub(r"[^a-z0-9]+", " ", body).strip()
    words = [_NETWORK_SPELLINGS.get(w, w) for w in key.split()]
    return " ".join(w for w in words if w) + suffix


def _burning_cost_lookup_network(network: Optional[str]) -> Optional[str]:
    """Resolves a New Business quote category's own network to whatever
    name the booked book's burning cost is actually keyed under - an
    already-MSH network passes through unchanged, while a recognized NAS
    Neuron network is substituted for its MSH equivalent (see
    NAS_TO_MSH_NETWORK above) so it still has real burning cost to compare
    against instead of no match at all.
    """
    if not network:
        return network
    key = _normalize_network_key(network)
    if key in NAS_TO_MSH_NETWORK:
        return NAS_TO_MSH_NETWORK[key]
    # Longest matching prefix, so an upload that appends a qualifier we
    # have not seen before ("... Group", "... excluding X") still lands
    # on the right network instead of silently missing.
    for candidate in sorted(NAS_TO_MSH_NETWORK, key=len, reverse=True):
        if key.startswith(candidate + " "):
            return NAS_TO_MSH_NETWORK[candidate]
    return network


def is_nas_stand_in(network: Optional[str]) -> bool:
    """True when this network has no book experience of its own and is
    being priced off an MSH equivalent.
    """
    if not network:
        return False
    return _burning_cost_lookup_network(network) != network


def nas_tpa_factor(network: Optional[str]) -> float:
    """What to multiply an MSH stand-in's burning cost by to price this
    network.

    1.0 for an MSH network priced off its own experience. Below 1.0 for
    a NAS network, which is being priced off MSH's book rather than its
    own and does not cost the same - see NAS_VS_MSH_BURNING_COST.
    """
    return NAS_VS_MSH_BURNING_COST if is_nas_stand_in(network) else 1.0


def resolve_group_product(member: dict, group_product_by_name: Dict[str, str]) -> Optional[str]:
    """A member's own contract (sub-group) takes priority over its
    master-contract - a master group split across multiple Products (one
    sub-group upgraded, say) would otherwise all resolve to whichever
    Product the master-level mapping happened to carry.

    A member's own `product_name` (populated directly from the export's
    own PRODUCTNAME column starting Aug 2026 - see app/ingestion/
    portfolio_members.py) is authoritative when present, since it's this
    member's own row rather than a separately-uploaded, contract-keyed
    mapping - only falls back to the uploaded GroupProductMapping lookup
    on an older-format export that doesn't carry it.
    """
    if member.get("product_name"):
        return member["product_name"]
    return group_product_by_name.get(member.get("contract")) or group_product_by_name.get(member.get("master_contract"))


def normalize_subgroup_key(name: Optional[str]) -> str:
    """Collapses whitespace and case so a subgroup name typed slightly
    differently between the membership export's own CONTRACT column and
    the manually-maintained Subgroup->Master mapping sheet (a stray
    trailing space, extra internal spacing, different capitalization)
    still matches - real spreadsheets prepared by hand aren't perfectly
    consistent about this, and a silent non-match would leave that
    subgroup showing as its own separate master rather than rolling up
    correctly. Callers building a subgroup_master_by_name dict for
    resolve_master_client must key it with this same normalization.
    """
    return " ".join((name or "").split()).casefold()


def resolve_master_client(member: dict, subgroup_master_by_name: Optional[Dict[str, str]]) -> Optional[str]:
    """Which master policy a member's own subgroup belongs to.

    A member's own `master_client_name` (populated directly from the
    export's own "Master Client Name" column starting Aug 2026 - see
    app/ingestion/portfolio_members.py) is authoritative when present,
    since it's this member's own row rather than a separately-uploaded,
    contract-keyed mapping. On an older-format export that doesn't carry
    it, falls back to the uploaded Subgroup->Master mapping (see
    app/ingestion/subgroup_mapping.py, a dedicated Subgroup/Group Name
    sheet), since PortfolioMember's own MASTERCONTRACT column on the
    system export isn't reliable for this. Falls back further to the raw
    master_contract/contract fields only when neither is available, so a
    member is never silently dropped from every master-level view just
    because no mapping has been uploaded yet. `subgroup_master_by_name`
    must be keyed by normalize_subgroup_key(subgroup_name), not the raw
    name.
    """
    if member.get("master_client_name"):
        return member["master_client_name"]
    contract = member.get("contract")
    if subgroup_master_by_name and contract:
        key = normalize_subgroup_key(contract)
        if key in subgroup_master_by_name:
            return subgroup_master_by_name[key]
    return member.get("master_contract") or contract


def renewal_due_accounts(
    members: List[dict],
    subgroup_master_by_name: Optional[Dict[str, str]] = None,
    within_days: int = 60,
    as_of: Optional[date_cls] = None,
) -> List[dict]:
    """Every distinct master client (see resolve_master_client) whose own
    policy_end_date falls within the next `within_days` days of `as_of` -
    a real "coming renewals" list driven directly by the Membership
    export's own policy dates, not a manually-maintained renewal_date on
    a separate case record (see app/api/routes_cases.py's Case model for
    that other, case-workflow-scoped notion of a renewal date).

    A master client's own policy_end_date is read from whichever of its
    members carries the latest one - most master clients share one term
    across every member, but taking the max keeps this correct even if a
    stray row has a missing or mismatched date. Cases already past their
    own end date (in real arrears) or further out than the window are
    both excluded - this is a due-soon list, not a full renewal calendar.
    """
    as_of = as_of or date_cls.today()
    horizon = as_of + timedelta(days=within_days)

    by_client: Dict[str, dict] = {}
    for m in members:
        master_client = resolve_master_client(m, subgroup_master_by_name)
        if not master_client:
            continue
        entry = by_client.setdefault(master_client, {"master_client": master_client, "policy_end_date": None, "member_count": 0})
        entry["member_count"] += 1
        end_date = m.get("policy_end_date")
        if end_date and (entry["policy_end_date"] is None or end_date > entry["policy_end_date"]):
            entry["policy_end_date"] = end_date

    due = [
        {**v, "days_until_renewal": (v["policy_end_date"] - as_of).days}
        for v in by_client.values()
        if v["policy_end_date"] is not None and as_of <= v["policy_end_date"] <= horizon
    ]
    due.sort(key=lambda d: d["policy_end_date"])
    return due


def earned_premium_fraction(policy_start, policy_end, as_of: date_cls) -> float:
    """How much of a member's policy period has actually elapsed as of
    `as_of` - claims incurred so far only reflect this much exposure, so
    comparing them against the FULL annual premium understates the true
    loss ratio for any policy that hasn't run its whole term yet (e.g. a
    group 3 months into a 12-month policy should only count 3/12 of its
    annual premium as "earned"). Missing/invalid dates fall back to fully
    earned (1.0) rather than dropping the member's premium entirely.
    """
    if not policy_start or not policy_end or policy_end <= policy_start:
        return 1.0
    total_days = (policy_end - policy_start).days
    elapsed_days = (min(as_of, policy_end) - policy_start).days
    if elapsed_days <= 0:
        return 0.0
    return min(1.0, elapsed_days / total_days)


def _claim_matches_period(date_of_treatment, policy_start, policy_end) -> bool:
    """A claim only counts against a member's OWN policy period, not just
    their beneficiary ID - a renewed member appears as a SEPARATE row per
    policy year (e.g. 2025 and 2026) sharing the SAME beneficiary ID, so
    matching by ID alone would double count that person's claims into
    every one of their policy years. Missing dates (on either side) fall
    back to matching rather than silently dropping a real claim.

    The period is HALF-OPEN: [start, end). The export's own end date is
    the renewal date - the day the NEXT policy begins - so a claim treated
    that day belongs to the incoming policy, not the expiring one. Two
    things follow from that. An annual policy measures exactly 365 days
    (1 May 2025 to 1 May 2026), where counting the end date too would make
    it 366. And consecutive policies stop overlapping, which is what used
    to let a claim treated on a renewal date match both periods and be
    counted twice.
    """
    if not policy_start or not policy_end or not date_of_treatment:
        return True
    if policy_end <= policy_start:
        # A same-day or inverted period would match nothing at all under a
        # half-open rule - fall back to inclusive rather than silently
        # dropping every claim for a member whose dates are malformed.
        return date_of_treatment == policy_start
    return policy_start <= date_of_treatment < policy_end


_PAID_CLAIM_STATUS_KEYWORDS = ("paid", "validated")


def _is_paid_claim_status(claim_status: Optional[str]) -> bool:
    """The real claims export carries a Claim Status of "Paid Claims" (or,
    on a newer export template, "Validated Claims" - same meaning, just a
    different word for a settled claim) vs "Outstanding Claims" (reserved/
    reported but not yet settled). Anything not explicitly one of these is
    treated as outstanding, so a future status value doesn't silently get
    miscounted as paid.
    """
    if not claim_status:
        return False
    lowered = claim_status.lower()
    return any(keyword in lowered for keyword in _PAID_CLAIM_STATUS_KEYWORDS)


def actual_claims_for_member(member: dict, claims_by_beneficiary: Dict[str, List[dict]]) -> Dict[str, float]:
    """Sums only the claim lines whose date_of_treatment falls within this
    member's own enrollment window (see _claim_matches_period), split into
    paid vs outstanding - "total" always equals paid + outstanding, so the
    two segments reconcile back to the same grand total callers already
    relied on.

    Matches against member_start_date/member_end_date rather than the
    broader policy_start_date/policy_end_date whenever the former are set.
    The two aren't the same thing: a member who transfers between subgroups
    mid-term appears as TWO rows sharing the SAME policy_start/policy_end
    (the one overall MSH policy term) but with different, non-overlapping
    member_start/member_end windows (their own actual enrollment sub-period
    under each subgroup). Matching against the full policy term in that
    case would double count every claim in the year against BOTH rows,
    since both share an identical policy period even though the person was
    only actually enrolled under each subgroup for part of it.
    """
    beneficiary_id = member.get("beneficiary_id")
    period_start = member.get("member_start_date") or member.get("policy_start_date")
    period_end = member.get("member_end_date") or member.get("policy_end_date")
    paid = 0.0
    outstanding = 0.0
    count = 0
    for c in claims_by_beneficiary.get(beneficiary_id, []):
        if not _claim_matches_period(c.get("date_of_treatment"), period_start, period_end):
            continue
        amount = c.get("final_amount") or 0.0
        if _is_paid_claim_status(c.get("claim_status")):
            paid += amount
        else:
            outstanding += amount
        count += 1
    return {"total": paid + outstanding, "paid": paid, "outstanding": outstanding, "count": count}


IBNR_LOOKBACK_DAYS = 30
IBNR_POLICY_EXPIRY_DAYS = 365


def ibnr_for_member(member: dict, claims_by_beneficiary: Dict[str, List[dict]], as_of: date_cls) -> float:
    """Incurred-but-not-reported reserve estimate for one member, per
    underwriting's own two rules:

    1. IBNR = (this member's own total Paid claims so far, from the start
       of their period through `as_of`) / (elapsed days so far) * 30 - a
       dynamic daily paid-claims run rate projected over a 30-day
       unreported tail, rather than a flat sum of whatever happened to
       land in the last 30 calendar days (which can swing wildly on a
       single large or small claim landing just inside/outside that
       window). Early in a policy period this run rate is naturally
       noisier (few days of data) and it smooths out as more of the
       period elapses.
    2. Zero once the member's own policy has run past a full year
       (`as_of` more than 365 days after policy_start_date) - by then the
       policy period is already closed out and has had a full year for
       claims to filter through, so there's no meaningful unreported tail
       left to estimate for it, regardless of that member's own recent
       paid activity.
    """
    policy_start = member.get("policy_start_date")
    if not policy_start or (as_of - policy_start).days > IBNR_POLICY_EXPIRY_DAYS:
        return 0.0

    beneficiary_id = member.get("beneficiary_id")
    period_start = member.get("member_start_date") or member.get("policy_start_date")
    period_end = member.get("member_end_date") or member.get("policy_end_date")
    effective_as_of = min(as_of, period_end) if period_end else as_of

    elapsed_days = (effective_as_of - period_start).days
    if elapsed_days <= 0:
        return 0.0

    total_paid = 0.0
    for c in claims_by_beneficiary.get(beneficiary_id, []):
        date_of_treatment = c.get("date_of_treatment")
        if not date_of_treatment or date_of_treatment > effective_as_of:
            continue
        if not _claim_matches_period(date_of_treatment, period_start, period_end):
            continue
        if _is_paid_claim_status(c.get("claim_status")):
            total_paid += c.get("final_amount") or 0.0
    return total_paid / elapsed_days * IBNR_LOOKBACK_DAYS


def analyze_portfolio_member(
    member: dict,
    group_product_by_name: Dict[str, str],
    rate_cards: List[dict],
    variant_rates: List[dict],
    claims_by_beneficiary: Dict[str, List[dict]],
    as_of: Optional[date_cls] = None,
    subgroup_master_by_name: Optional[Dict[str, str]] = None,
) -> dict:
    """member: one row from app/ingestion/portfolio_members.py."""
    beneficiary_id = member["beneficiary_id"]
    as_of = as_of or date_cls.today()

    # Computed up front, before the in-scope check below, so an
    # out-of-scope member's own premium/claims/IBNR still feed Level 1's
    # whole-book KPIs (executive_portfolio_summary counts every member "in
    # AND out of scope" by design) without also leaking into any
    # Product/Network-keyed rollup - summarize_portfolio and every other
    # per-dimension view explicitly skip `in_scope: False` rows, so this
    # is safe to compute unconditionally.
    earned_fraction = earned_premium_fraction(member.get("policy_start_date"), member.get("policy_end_date"), as_of)
    actual_gross_premium = member.get("actual_gross_premium")
    actual_premium = actual_gross_premium * earned_fraction if actual_gross_premium is not None else None
    claims_breakdown = actual_claims_for_member(member, claims_by_beneficiary)
    ibnr = ibnr_for_member(member, claims_by_beneficiary, as_of)

    if is_out_of_scope_network_type(member.get("network_type_raw")):
        # Still a real person in the population even though no rate-card
        # price applies to them - their age/gender/relation/nationality
        # are real facts, not something scope exclusion should also erase.
        # See demographic_summary, which counts every member (in and out
        # of scope) toward the same headline total every other Portfolio
        # Analysis view reports.
        #
        # Product and network are just as real: these members ARE sold a
        # product, and their network is a genuine one (it simply is not a
        # UAE rate-card network). Carrying both means they roll up under
        # the product they actually hold instead of "Unmapped" - only
        # standard_premium stays absent, since that is the one figure
        # that truly requires a rate card.
        return {
            "beneficiary_id": beneficiary_id,
            "in_scope": False,
            "reason": f"'{member.get('network_type_raw')}' is outside the UAE rate card's scope",
            "product": resolve_group_product(member, group_product_by_name),
            "network": (member.get("network_type_raw") or "").strip() or None,
            "region": member.get("region"),
            "nationality": member.get("nationality"),
            "nationality_zone": member.get("nationality_zone"),
            "client": member.get("contract") or member.get("master_contract"),
            "master_client": resolve_master_client(member, subgroup_master_by_name),
            "gender": member.get("gender"),
            "relation": member.get("relation"),
            "marital_status": member.get("marital_status"),
            "age": member.get("age"),
            "category": member.get("category"),
            "policy_year": str(member["policy_start_date"].year) if member.get("policy_start_date") else None,
            "policy_start_date": member.get("policy_start_date"),
            "policy_end_date": member.get("policy_end_date"),
            "member_start_date": member.get("member_start_date"),
            "member_end_date": member.get("member_end_date"),
            "written_premium": round(actual_gross_premium, 2) if actual_gross_premium is not None else None,
            "booked_gross_premium": round(member["gross_premium"], 2) if member.get("gross_premium") is not None else None,
            "actual_premium": round(actual_premium, 2) if actual_premium is not None else None,
            "actual_claims": round(claims_breakdown["total"], 2),
            "actual_claims_paid": round(claims_breakdown["paid"], 2),
            "actual_claims_outstanding": round(claims_breakdown["outstanding"], 2),
            "claim_count": claims_breakdown["count"],
            "ibnr": round(ibnr, 2),
            "earned_premium_fraction": round(earned_fraction, 4),
        }

    warnings: List[str] = []
    network = map_network_type(member.get("network_type_raw"))
    if network is None:
        warnings.append(f"Unrecognized network type '{member.get('network_type_raw')}'")

    product = resolve_group_product(member, group_product_by_name)
    if not product:
        warnings.append("No Product mapping found for this member's group")

    standard_premium = None
    if network and product:
        price_result = price_member(
            {
                "age": member.get("age"),
                "gender": member.get("gender"),
                "marital_status": member.get("marital_status"),
                "relation": member.get("relation"),
                "emirates": member.get("residence_emirate"),
            },
            {"product": product, "network": network, "tpa": BOOK_TPA, "variant_selections": {}},
            rate_cards,
            variant_rates,
        )
        if price_result["net_total"] is not None:
            standard_premium = price_result["net_total"] * earned_fraction
        warnings.extend(price_result["warnings"])

    return {
        "beneficiary_id": beneficiary_id,
        "in_scope": True,
        "product": product,
        "network": network,
        "region": member.get("region"),
        "nationality": member.get("nationality"),
        "nationality_zone": member.get("nationality_zone"),
        "client": member.get("contract") or member.get("master_contract"),
        # Always the MASTER policy, regardless of whether this member also
        # has its own subgroup (contract) - a master with 3 subgroups rolls
        # all of them up into one row here, unlike "client" above which
        # shows each subgroup separately. Lets loss ratio/burning cost be
        # seen at the master level first, then drilled into subgroups via
        # the master_client filter + group_by=client.
        "master_client": resolve_master_client(member, subgroup_master_by_name),
        "gender": member.get("gender"),
        "relation": member.get("relation"),
        "marital_status": member.get("marital_status"),
        "age": member.get("age"),
        # The member's own benefit category (e.g. Category A/B/C) - a
        # separate dimension from Product/Network, letting loss ratio be
        # seen by benefit tier within a scheme.
        "category": member.get("category"),
        # The calendar year a member's own policy period started in - a
        # client that's already renewed will have some members on last
        # year's policy and some on this year's within the same upload;
        # this is what lets those cohorts be told apart (group_by or the
        # policy_year filter in _run_analysis).
        "policy_year": str(member["policy_start_date"].year) if member.get("policy_start_date") else None,
        "policy_start_date": member.get("policy_start_date"),
        "policy_end_date": member.get("policy_end_date"),
        "member_start_date": member.get("member_start_date"),
        "member_end_date": member.get("member_end_date"),
        "standard_premium": round(standard_premium, 2) if standard_premium is not None else None,
        # "Written" (the full annual amount, regardless of how much of the
        # term has elapsed) vs. "actual"/earned (prorated by earned_fraction
        # above) - Level 1's own "Average Premium per Member" KPI uses the
        # written figure, since that's the plain per-member price point,
        # not the year-to-date earned amount.
        "written_premium": round(actual_gross_premium, 2) if actual_gross_premium is not None else None,
        "booked_gross_premium": round(member["gross_premium"], 2) if member.get("gross_premium") is not None else None,
        "actual_premium": round(actual_premium, 2) if actual_premium is not None else None,
        "actual_claims": round(claims_breakdown["total"], 2),
        "actual_claims_paid": round(claims_breakdown["paid"], 2),
        "actual_claims_outstanding": round(claims_breakdown["outstanding"], 2),
        "claim_count": claims_breakdown["count"],
        "ibnr": round(ibnr, 2),
        "earned_premium_fraction": round(earned_fraction, 4),
        "warnings": warnings,
    }


def group_claims_by_beneficiary(claims: List[dict]) -> Dict[str, List[dict]]:
    """Groups raw claim lines by patient_id, keeping each line's own
    date_of_treatment + final_amount rather than pre-summing them - a
    renewed member appears as a SEPARATE row per policy year sharing the
    SAME beneficiary ID, so a flat per-ID total (with no date match) would
    double count that person's claims into every one of their policy
    years. See actual_claims_for_member for the date-matched total.
    """
    grouped: Dict[str, List[dict]] = defaultdict(list)
    for c in claims:
        patient_id = c.get("patient_id")
        if patient_id:
            grouped[patient_id].append(c)
    return dict(grouped)


DEFAULT_LARGE_CLAIM_THRESHOLDS = (50_000.0, 100_000.0, 250_000.0)
RECURRING_HIGH_COST_MIN_CLAIM_COUNT = 3


def top_claims_by_value(claims: List[dict], top_n: int = 10) -> List[dict]:
    """The single largest individual claim LINES by their own final_amount -
    distinct from top_members_by_total_claims below, which ranks by a
    member's cumulative total across every claim line. One catastrophic
    claim line can rank here without that member necessarily also ranking
    among the highest-total members (a single large claim vs. many
    moderate ones summing high are different risk stories).
    """
    dated = [c for c in claims if c.get("final_amount") is not None]
    ranked = sorted(dated, key=lambda c: c["final_amount"], reverse=True)[:top_n]
    return [
        {
            "patient_id": c.get("patient_id"),
            "group_name": c.get("group_name"),
            "client_name": c.get("client_name"),
            "provider_name": c.get("provider_name"),
            "diagnosis_description": c.get("diagnosis_description"),
            "date_of_treatment": c.get("date_of_treatment"),
            "final_amount": round(c["final_amount"], 2),
        }
        for c in ranked
    ]


def top_members_by_total_claims(claims: List[dict], top_n: int = 20) -> List[dict]:
    """Every claim line's final_amount summed per patient_id, ranked
    highest total first - a member driving high cost through many
    moderate claims shows up here even if no single line of theirs would
    make top_claims_by_value on its own.
    """
    by_patient: Dict[str, dict] = defaultdict(lambda: {"total": 0.0, "claim_count": 0, "group_name": None, "client_name": None})
    for c in claims:
        patient_id = c.get("patient_id")
        if not patient_id:
            continue
        bucket = by_patient[patient_id]
        bucket["total"] += c.get("final_amount") or 0.0
        bucket["claim_count"] += 1
        if bucket["group_name"] is None and c.get("group_name"):
            bucket["group_name"] = c["group_name"]
        if bucket["client_name"] is None and c.get("client_name"):
            bucket["client_name"] = c["client_name"]

    ranked = sorted(by_patient.items(), key=lambda item: item[1]["total"], reverse=True)[:top_n]
    return [
        {
            "patient_id": patient_id,
            "group_name": bucket["group_name"],
            "client_name": bucket["client_name"],
            "total_claims": round(bucket["total"], 2),
            "claim_count": bucket["claim_count"],
        }
        for patient_id, bucket in ranked
    ]


def claims_above_thresholds(claims: List[dict], thresholds: tuple = DEFAULT_LARGE_CLAIM_THRESHOLDS) -> List[dict]:
    """Count and total value of individual claim LINES at or above each of
    a handful of round-number thresholds (e.g. AED 50K/100K/250K) -
    underwriting's usual first cut at "how many genuinely large claims
    does this book have", before drilling into who they belong to.
    """
    rows = []
    for threshold in thresholds:
        matching = [c for c in claims if (c.get("final_amount") or 0.0) >= threshold]
        rows.append({
            "threshold": threshold,
            "claim_count": len(matching),
            "total_value": round(sum(c["final_amount"] for c in matching), 2),
        })
    return rows


def recurring_high_cost_members(
    claims: List[dict], claim_threshold: float = DEFAULT_LARGE_CLAIM_THRESHOLDS[0],
    min_claim_count: int = RECURRING_HIGH_COST_MIN_CLAIM_COUNT,
) -> List[dict]:
    """Members with several separate claim lines each at or above
    `claim_threshold` - distinct from a single catastrophic claim (see
    top_claims_by_value): a member who keeps generating repeated
    large-but-not-shock claims is a different underwriting concern (an
    ongoing chronic condition, say) than one large one-off event, even
    when their totals end up similar.
    """
    by_patient: Dict[str, dict] = defaultdict(lambda: {"total": 0.0, "large_claim_count": 0, "group_name": None, "client_name": None})
    for c in claims:
        if (c.get("final_amount") or 0.0) < claim_threshold:
            continue
        patient_id = c.get("patient_id")
        if not patient_id:
            continue
        bucket = by_patient[patient_id]
        bucket["total"] += c["final_amount"]
        bucket["large_claim_count"] += 1
        if bucket["group_name"] is None and c.get("group_name"):
            bucket["group_name"] = c["group_name"]
        if bucket["client_name"] is None and c.get("client_name"):
            bucket["client_name"] = c["client_name"]

    recurring = [
        {
            "patient_id": patient_id,
            "group_name": bucket["group_name"],
            "client_name": bucket["client_name"],
            "large_claim_count": bucket["large_claim_count"],
            "total_claims": round(bucket["total"], 2),
        }
        for patient_id, bucket in by_patient.items()
        if bucket["large_claim_count"] >= min_claim_count
    ]
    recurring.sort(key=lambda r: r["total_claims"], reverse=True)
    return recurring


#: MEDICAL_CATEGORY -> friendlier display label, for the categories that
#: map cleanly onto a named benefit. PARAMEDICAL reads as "Physiotherapy"
#: because in HealthCross's own claims book it's overwhelmingly
#: musculoskeletal diagnoses (back pain, joint/disc/muscle disorders) -
#: an empirical read of the real data, not a guess. Everything else keeps
#: its own real category name (see _utilization_category_label) rather
#: than being forced into an artificial "Other" bucket.
UTILIZATION_CATEGORY_LABELS = {
    "PHARMACY": "Pharmacy",
    "VISION CARE": "Optical",
    "PSYCHIATRY": "Mental Health",
}
#: Shown as one combined "Dental" row rather than three separate ones.
UTILIZATION_DENTAL_CATEGORIES = {"GENERAL DENTAL", "ORTHODONTIA", "DENTAL PROSTHESIS"}


def _utilization_category_label(raw_category: Optional[str], medical_act: Optional[str] = None) -> str:
    """PARAMEDICAL is split by the treatment actually performed rather than
    shown as one row: the category holds true physiotherapy, every
    alternative therapy on the book, and nursing, and labelling the whole
    thing "Physiotherapy" (as this did) both overstated physiotherapy and
    hid alternative treatment entirely. See
    app/reference/treatment_classification.py."""
    if not raw_category:
        return "Unclassified"
    if raw_category in UTILIZATION_DENTAL_CATEGORIES:
        return "Dental"
    if raw_category.strip().upper() == "PARAMEDICAL":
        return classify_paramedical(medical_act)
    if raw_category in UTILIZATION_CATEGORY_LABELS:
        return UTILIZATION_CATEGORY_LABELS[raw_category]
    return raw_category.title()


def utilization_by_encounter_type(claims: List[dict]) -> List[dict]:
    """Outpatient/Inpatient/Maternity split, straight from each claim
    line's own IP_OP_MATERNITY field - HealthCross's export populates
    this on every row, so this is a complete cut (unlike benefit category
    below, nothing falls through to "Unclassified" here in practice).
    """
    buckets: Dict[str, dict] = defaultdict(lambda: {"claim_count": 0, "total_value": 0.0})
    total_value_all = 0.0
    for c in claims:
        key = (c.get("ip_op_maternity") or "Unclassified").title()
        amount = c.get("final_amount") or 0.0
        buckets[key]["claim_count"] += 1
        buckets[key]["total_value"] += amount
        total_value_all += amount

    rows = [
        {
            "encounter_type": key,
            "claim_count": bucket["claim_count"],
            "total_value": round(bucket["total_value"], 2),
            "pct_of_total": round(bucket["total_value"] / total_value_all * 100, 1) if total_value_all else None,
        }
        for key, bucket in buckets.items()
    ]
    rows.sort(key=lambda r: r["total_value"], reverse=True)
    return rows


def utilization_by_benefit_category(claims: List[dict]) -> List[dict]:
    """Which benefits are actually driving cost, to spot benefit leakage
    and major cost drivers. PHARMACY/VISION CARE/PSYCHIATRY/PARAMEDICAL
    show under the friendlier Pharmacy/Optical/Mental Health/
    Physiotherapy labels, the three dental categories (GENERAL DENTAL/
    ORTHODONTIA/DENTAL PROSTHESIS) combine into one "Dental" row, and
    everything else (Laboratory/Consultation/Diagnostic Procedures/
    Hospitalisation/Day Case/Maternity/Prevention/Miscellaneous) is shown
    under its own real category name - several of these are large shares
    of total spend on their own, so folding them into a vague "Other"
    would hide rather than reveal a cost driver.

    PARAMEDICAL is split into "Physiotherapy", "Alternative Treatment"
    (ayurvedic/homeopathy/acupuncture/osteopathy/chiropractic) and "Other
    Paramedical" by each line's own treatment, since the category holds
    all three and reporting it as one row overstated physiotherapy while
    hiding alternative treatment completely.

    Chronic conditions and high-cost specialty treatments still aren't
    represented - the export has no field tagging a claim as either, so
    building them would mean guessing rather than reading real data.
    """
    buckets: Dict[str, dict] = defaultdict(lambda: {"claim_count": 0, "total_value": 0.0})
    total_value_all = 0.0
    for c in claims:
        key = _utilization_category_label(c.get("medical_category"), c.get("medical_act"))
        amount = c.get("final_amount") or 0.0
        buckets[key]["claim_count"] += 1
        buckets[key]["total_value"] += amount
        total_value_all += amount

    rows = [
        {
            "category": key,
            "claim_count": bucket["claim_count"],
            "total_value": round(bucket["total_value"], 2),
            "pct_of_total": round(bucket["total_value"] / total_value_all * 100, 1) if total_value_all else None,
        }
        for key, bucket in buckets.items()
    ]
    rows.sort(key=lambda r: r["total_value"], reverse=True)
    return rows


_GROUP_BY_FIELDS = {
    "product", "network", "region", "nationality_zone", "client", "master_client",
    "gender", "relation", "policy_year", "category",
}


def summarize_portfolio(member_results: List[dict], group_by: str) -> List[dict]:
    """Rolls up analyze_portfolio_member's per-member results by one
    dimension (product/network/region/nationality_zone/client/gender/
    relation/category). A member missing that dimension's own value (e.g.
    no Product mapping yet) rolls up under "Unmapped" rather than being
    dropped silently.

    Members the rate card cannot price (see is_out_of_scope_network_type)
    are INCLUDED - they hold a real product and their premium and claims
    are real money, so leaving them out understated every account they
    belong to. They are counted in out_of_scope_member_count, and the two
    rate-card comparisons (loss_ratio_vs_standard, actual_vs_standard_pct)
    are measured against the priced members' own premium and claims so
    they stay apples-to-apples; every other figure covers the whole bucket. "client" groups by the
    member's own contract (sub-group), falling back to its master
    contract. "gender"/"relation" (employee/spouse/child) let pricing see
    burning cost by demographic segment, not just Product/Network-wide -
    e.g. spouse burning cost typically running well above employee's.
    "category" is the member's own benefit category (e.g. Category A/B/C),
    a separate dimension from Product/Network.

    Each row also carries the group's own Product, Network, and policy
    start date (the first non-null value seen in that group) - a real
    subgroup or category is normally homogeneous on these, so this lets a
    "by subgroup" or "by category" table show what it's actually priced
    on without a separate lookup, even when grouping by something else.
    """
    if group_by not in _GROUP_BY_FIELDS:
        raise ValueError(f"group_by must be one of {sorted(_GROUP_BY_FIELDS)}")

    buckets: Dict[str, dict] = defaultdict(
        lambda: {
            "member_count": 0,
            "priced_member_count": 0,
            "standard_premium": 0.0,
            # The priced subset's OWN actual figures, so a vs-rate-card
            # comparison stays apples-to-apples even in a bucket that also
            # holds members the card cannot price (see the loop below).
            "priced_actual_premium": 0.0,
            "priced_actual_claims": 0.0,
            "out_of_scope_member_count": 0,
            "actual_premium": 0.0,
            "actual_claims": 0.0,
            "actual_claims_paid": 0.0,
            "actual_claims_outstanding": 0.0,
            "ibnr": 0.0,
            "claim_count": 0,
            "earned_member_years": 0.0,
            "product": None,
            "network": None,
            "policy_start_date": None,
        }
    )
    for r in member_results:
        in_scope = r.get("in_scope", True)
        key = r.get(group_by) or "Unmapped"
        bucket = buckets[key]
        bucket["member_count"] += 1
        if not in_scope:
            bucket["out_of_scope_member_count"] += 1
        if r.get("actual_premium") is not None:
            bucket["actual_premium"] += r["actual_premium"]
            if in_scope:
                bucket["priced_actual_premium"] += r["actual_premium"]
        if in_scope:
            bucket["priced_actual_claims"] += r.get("actual_claims") or 0.0
        bucket["actual_claims"] += r.get("actual_claims") or 0.0
        bucket["actual_claims_paid"] += r.get("actual_claims_paid") or 0.0
        bucket["actual_claims_outstanding"] += r.get("actual_claims_outstanding") or 0.0
        bucket["ibnr"] += r.get("ibnr") or 0.0
        bucket["claim_count"] += r.get("claim_count") or 0
        bucket["earned_member_years"] += r.get("earned_premium_fraction") or 0.0
        if r.get("standard_premium") is not None:
            bucket["standard_premium"] += r["standard_premium"]
            bucket["priced_member_count"] += 1
        if bucket["product"] is None and r.get("product"):
            bucket["product"] = r["product"]
        if bucket["network"] is None and r.get("network"):
            bucket["network"] = r["network"]
        if bucket["policy_start_date"] is None and r.get("policy_start_date"):
            bucket["policy_start_date"] = r["policy_start_date"]

    rows = []
    for key, bucket in buckets.items():
        standard_premium = bucket["standard_premium"]
        actual_premium = bucket["actual_premium"]
        actual_claims = bucket["actual_claims"]
        earned_member_years = bucket["earned_member_years"]
        # Compared against the priced members' OWN premium/claims, never
        # the bucket totals: a bucket can also hold members the rate card
        # cannot price (e.g. MSH INTL NETWORK), and measuring everyone's
        # claims against only the priced members' standard premium would
        # overstate the ratio purely because of who the card covers.
        priced_actual_premium = bucket["priced_actual_premium"]
        priced_actual_claims = bucket["priced_actual_claims"]
        rows.append(
            {
                group_by: key,
                "member_count": bucket["member_count"],
                "priced_member_count": bucket["priced_member_count"],
                "standard_premium": round(standard_premium, 2),
                "actual_premium": round(actual_premium, 2),
                "actual_claims": round(actual_claims, 2),
                # These two always sum back to "actual_claims" above -
                # segregated so paid (settled) claims can be told apart
                # from outstanding (reserved/reported but not yet paid).
                "actual_claims_paid": round(bucket["actual_claims_paid"], 2),
                "actual_claims_outstanding": round(bucket["actual_claims_outstanding"], 2),
                # Incurred-but-not-reported reserve estimate (see
                # ibnr_for_member) - zero for a bucket made up entirely of
                # already-expired policies, not necessarily zero overall.
                "ibnr": round(bucket["ibnr"], 2),
                "out_of_scope_member_count": bucket["out_of_scope_member_count"],
                "loss_ratio_vs_standard": round(priced_actual_claims / standard_premium, 4) if standard_premium else None,
                "loss_ratio_vs_actual": round(actual_claims / actual_premium, 4) if actual_premium else None,
                # Underwriting's own fuller loss ratio: Paid + Outstanding +
                # IBNR (actual_claims already sums the first two), over
                # Earned Premium - distinct from loss_ratio_vs_actual above,
                # which omits IBNR entirely.
                "loss_ratio_incl_ibnr": round((actual_claims + bucket["ibnr"]) / actual_premium, 4)
                if actual_premium
                else None,
                # Positive = actual premium sits above standard (charging
                # more than the rate card); negative = discounted below it.
                "actual_vs_standard_pct": round((priced_actual_premium - standard_premium) / standard_premium * 100, 2)
                if standard_premium
                else None,
                "earned_member_years": round(earned_member_years, 4),
                # Claims cost per earned member-year (AED per member per
                # annum) - the technical "burning cost" rate underwriters
                # use to set a required premium independent of what was
                # actually charged, unlike the premium-relative loss ratios above.
                "burning_cost": round(actual_claims / earned_member_years, 2) if earned_member_years else None,
                "claim_count": bucket["claim_count"],
                # Frequency: claims per earned member-year (exposure-adjusted,
                # not flat per-head, so a bucket with partial-year members
                # isn't penalized for looking like it claims less often than
                # it actually does). Severity: average cost per claim - high
                # frequency + low severity vs. low frequency + high severity
                # can produce the same loss ratio for very different reasons.
                "claim_frequency": round(bucket["claim_count"] / earned_member_years, 3) if earned_member_years else None,
                "claim_severity": round(actual_claims / bucket["claim_count"], 2) if bucket["claim_count"] else None,
                "policy_start_date": bucket["policy_start_date"].isoformat() if bucket["policy_start_date"] else None,
            }
        )
        # Only added when they're not the group-by dimension itself, since
        # otherwise this representative value would silently overwrite the
        # actual group key above (e.g. group_by="product" would have its
        # real "Bronze"/"Gold" key clobbered by this representative field).
        if group_by != "product":
            rows[-1]["product"] = bucket["product"]
        if group_by != "network":
            rows[-1]["network"] = bucket["network"]
    rows.sort(key=lambda r: r[group_by])
    return rows


def age_bands_from_rate_cards(rate_cards: List[dict]) -> List[tuple]:
    """The distinct (from_age, to_age) bands the CURRENTLY loaded rate card
    actually prices by - not a hardcoded assumption, since the real UAE
    rate card's bands could change over time. Burning cost is bucketed
    into these same bands so it lines up directly, row-for-row, against
    the Male/Female price columns in the standard pricing tool.
    """
    return sorted({(r["from_age"], r["to_age"]) for r in rate_cards if r.get("from_age") is not None and r.get("to_age") is not None})


def _matching_age_band(age: Optional[int], bands: List[tuple]) -> Optional[tuple]:
    if age is None:
        return None
    for from_age, to_age in bands:
        if from_age <= age <= to_age:
            return (from_age, to_age)
    return None


# A bucket's burning cost is one claims-total divided by one member-years
# total - with only a couple of member-years behind it, a single large
# claim can swing the per-member-year rate by 100x, which reads as a wild
# outlier even though it's really just a tiny, unlucky (or lucky) sample.
# Below this many earned member-years, a bucket is flagged low_credibility
# rather than silently trusted - callers decide what to do with that
# (price_case_against_burning_cost excludes it entirely; the Portfolio
# Analysis UI just badges it so the raw number stays visible for
# diagnosis). Not a formal actuarial credibility standard, just a
# pragmatic floor for "don't treat this as a reliable rate yet".
MIN_CREDIBLE_MEMBER_YEARS = 5.0


def summarize_burning_cost_by_age_gender(member_results: List[dict], rate_cards: List[dict]) -> List[dict]:
    """Burning cost bucketed by the SAME (age-band x gender) structure the
    standard pricing rate card itself uses - each row here lines up with
    one Male-Price/Female-Price row in the rate card, so underwriting can
    directly compare "the card charges X for this band" against "actual
    burning cost for this band is Y" and recalibrate from there.
    """
    bands = age_bands_from_rate_cards(rate_cards)
    buckets: Dict[tuple, dict] = defaultdict(lambda: {"member_count": 0, "actual_claims": 0.0, "earned_member_years": 0.0})

    for r in member_results:
        if not r.get("in_scope", True):
            continue
        band = _matching_age_band(r.get("age"), bands)
        band_label = f"{band[0]}-{band[1]}" if band else "Unmapped age"
        gender = r.get("gender") or "Unmapped"
        key = (band_label, gender)
        bucket = buckets[key]
        bucket["member_count"] += 1
        bucket["actual_claims"] += r.get("actual_claims") or 0.0
        bucket["earned_member_years"] += r.get("earned_premium_fraction") or 0.0

    rows = []
    for (band_label, gender), bucket in buckets.items():
        earned_member_years = bucket["earned_member_years"]
        rows.append(
            {
                "age_band": band_label,
                "gender": gender,
                "member_count": bucket["member_count"],
                "actual_claims": round(bucket["actual_claims"], 2),
                "earned_member_years": round(earned_member_years, 4),
                "burning_cost": round(bucket["actual_claims"] / earned_member_years, 2) if earned_member_years else None,
                "low_credibility": 0 < earned_member_years < MIN_CREDIBLE_MEMBER_YEARS,
            }
        )
    rows.sort(key=lambda r: (r["age_band"], r["gender"]))
    return rows


def summarize_burning_cost_by_product_network(member_results: List[dict]) -> List[dict]:
    """Actual burning cost from the already-booked book, broken out by the
    same (Product, Network) pairing a New Business rate card row prices -
    lets New Business Rating show "the card charges X for this Product/
    Network, the book's own real claims experience runs Y" side by side,
    a reference for the underwriter to weigh rather than something that
    silently overrides the rate card itself.
    """
    buckets: Dict[tuple, dict] = defaultdict(lambda: {"member_count": 0, "actual_claims": 0.0, "earned_member_years": 0.0})

    for r in member_results:
        if not r.get("in_scope", True):
            continue
        product = r.get("product")
        network = r.get("network")
        if not product or not network:
            continue
        key = (product, network)
        bucket = buckets[key]
        bucket["member_count"] += 1
        bucket["actual_claims"] += r.get("actual_claims") or 0.0
        bucket["earned_member_years"] += r.get("earned_premium_fraction") or 0.0

    rows = []
    for (product, network), bucket in buckets.items():
        earned_member_years = bucket["earned_member_years"]
        rows.append(
            {
                "product": product,
                "network": network,
                "member_count": bucket["member_count"],
                "actual_claims": round(bucket["actual_claims"], 2),
                "earned_member_years": round(earned_member_years, 4),
                "burning_cost": round(bucket["actual_claims"] / earned_member_years, 2) if earned_member_years else None,
            }
        )
    rows.sort(key=lambda r: (r["product"], r["network"]))
    return rows


def summarize_burning_cost_by_product_network_age_gender(member_results: List[dict], rate_cards: List[dict]) -> List[dict]:
    """Burning cost bucketed by (Product, Network, age band, gender)
    together - the same four dimensions price_member itself resolves a
    rate-card row by - rather than just (Product, Network) alone. Finer
    than summarize_burning_cost_by_product_network, so a New Business
    case's own age/gender mix can be re-priced against the booked book's
    own real experience for that exact slice (see
    price_case_against_burning_cost) instead of one flat average across
    every age and gender on that Product/Network.
    """
    bands = age_bands_from_rate_cards(rate_cards)
    buckets: Dict[tuple, dict] = defaultdict(lambda: {"member_count": 0, "actual_claims": 0.0, "earned_member_years": 0.0})

    for r in member_results:
        if not r.get("in_scope", True):
            continue
        product = r.get("product")
        network = r.get("network")
        if not product or not network:
            continue
        band = _matching_age_band(r.get("age"), bands)
        band_label = f"{band[0]}-{band[1]}" if band else "Unmapped age"
        gender = r.get("gender") or "Unmapped"
        key = (product, network, band_label, gender)
        bucket = buckets[key]
        bucket["member_count"] += 1
        bucket["actual_claims"] += r.get("actual_claims") or 0.0
        bucket["earned_member_years"] += r.get("earned_premium_fraction") or 0.0

    rows = []
    for (product, network, band_label, gender), bucket in buckets.items():
        earned_member_years = bucket["earned_member_years"]
        rows.append(
            {
                "product": product,
                "network": network,
                "age_band": band_label,
                "gender": gender,
                "member_count": bucket["member_count"],
                "actual_claims": round(bucket["actual_claims"], 2),
                "earned_member_years": round(earned_member_years, 4),
                "burning_cost": round(bucket["actual_claims"] / earned_member_years, 2) if earned_member_years else None,
                "low_credibility": 0 < earned_member_years < MIN_CREDIBLE_MEMBER_YEARS,
            }
        )
    rows.sort(key=lambda r: (r["product"], r["network"], r["age_band"], r["gender"]))
    return rows


def price_case_against_burning_cost(
    census: List[dict],
    categories: List[dict],
    rate_cards: List[dict],
    burning_cost_rows: List[dict],
) -> dict:
    """SUPERSEDED - do not use for new work. See
    app/scoring/rules/expected_cost_pricing.py's price_by_category, which
    the burning-cost comparison endpoint now uses.

    The reason is the behaviour described below as a safeguard: excluding
    a member whose exact bucket is missing or thin does not produce a
    cautious figure, it produces a LOW one. The excluded members do not
    stop existing - they are simply priced at nothing, so the total comes
    in short by however many were dropped, and the sparser the book the
    worse the understatement. That is exactly the situation where an
    underwriter is most likely to lean on this number. The cube's
    hierarchical fallback prices every member from the nearest cell that
    does have exposure and reports how far it had to fall back, which is
    the honest version of the same caution.

    Kept only because it still has tests describing the old behaviour;
    nothing in the application calls it.

    Re-prices a New Business case's own census against the booked book's
    own real burning cost (see
    summarize_burning_cost_by_product_network_age_gender) instead of the
    rate card, applying the SAME category_loading_pct/gross_up loading New
    Business quoting itself uses on top - so the result lands in the same
    "gross annual premium" units as price_case's own output and the two
    can be compared category by category, not just eyeballed as
    differently-scaled numbers.

    A member whose own (Product, Network, age band, gender) combination has
    no matching burning-cost bucket in the booked book (sparse or no
    portfolio data for that exact slice) is excluded from that category's
    net total and flagged via a warning, rather than silently priced at
    zero - this is a reference figure to weigh against the rate card, not
    something that should look artificially cheap just because the book
    has a data gap. A bucket with a burning cost but too few earned
    member-years to be credible (see MIN_CREDIBLE_MEMBER_YEARS) is treated
    the same way - a single large claim on a handful of member-years can
    swing that bucket's rate by 100x, which would otherwise show up as a
    dramatic-looking variance that's really just sampling noise, not a
    genuine signal the rate card is mispriced.
    """
    bands = age_bands_from_rate_cards(rate_cards)
    burning_cost_by_key = {
        (r["product"], r["network"], r["age_band"], r["gender"]): r["burning_cost"]
        for r in burning_cost_rows
        if r.get("burning_cost") is not None and not r.get("low_credibility")
    }

    categories_by_name = {c["category"]: c for c in categories}
    per_category_net: Dict[str, float] = defaultdict(float)
    per_category_priced_count: Dict[str, int] = defaultdict(int)
    per_category_total_count: Dict[str, int] = defaultdict(int)
    per_category_warnings: Dict[str, List[str]] = defaultdict(list)
    uncategorized_count = 0

    for member in census:
        category = categories_by_name.get(member.get("category"))
        if category is None:
            uncategorized_count += 1
            continue
        cat_name = category["category"]
        per_category_total_count[cat_name] += 1

        band = _matching_age_band(member.get("age"), bands)
        band_label = f"{band[0]}-{band[1]}" if band else "Unmapped age"
        gender = member.get("gender") or "Unmapped"
        lookup_network = _burning_cost_lookup_network(category["network"])
        key = (category["product"], lookup_network, band_label, gender)
        burning_cost = burning_cost_by_key.get(key)
        if burning_cost is None:
            per_category_warnings[cat_name].append(
                f"No booked-book burning cost for {category['product']}/{lookup_network}, "
                f"age band {band_label}, {gender}"
            )
            continue
        per_category_net[cat_name] += burning_cost
        per_category_priced_count[cat_name] += 1

    category_breakdown = []
    case_gross_total = 0.0
    for cat_name, category in categories_by_name.items():
        loading_pct = category_loading_pct(category["product"], category.get("commission_pct"))
        net_total = per_category_net.get(cat_name, 0.0)
        gross_total = gross_up(net_total, loading_pct)
        case_gross_total += gross_total
        category_breakdown.append(
            {
                "category": cat_name,
                "product": category["product"],
                "network": category["network"],
                "member_count": per_category_total_count.get(cat_name, 0),
                "priced_member_count": per_category_priced_count.get(cat_name, 0),
                "net_annual_premium": round(net_total, 2),
                "loading_pct": round(loading_pct, 4),
                "gross_annual_premium": round(gross_total, 2),
                "warnings": sorted(set(per_category_warnings.get(cat_name, []))),
            }
        )

    return {
        "categories": category_breakdown,
        "case_gross_annual_premium": round(case_gross_total, 2),
        "uncategorized_member_count": uncategorized_count,
    }


def summarize_burning_cost_overall(member_results: List[dict]) -> Optional[dict]:
    """The whole book's own burning cost, with no grouping at all - a
    fallback reference for a case whose proposed network isn't known yet
    (no HealthCross quote uploaded), so there's still SOME real-book figure
    to show rather than nothing. Returns None only when there's no earned
    exposure to divide by (e.g. an empty book).
    """
    member_count = 0
    actual_claims = 0.0
    earned_member_years = 0.0
    for r in member_results:
        if not r.get("in_scope", True):
            continue
        member_count += 1
        actual_claims += r.get("actual_claims") or 0.0
        earned_member_years += r.get("earned_premium_fraction") or 0.0

    if not earned_member_years:
        return None
    return {
        "member_count": member_count,
        "actual_claims": round(actual_claims, 2),
        "earned_member_years": round(earned_member_years, 4),
        "burning_cost": round(actual_claims / earned_member_years, 2),
    }


DEFAULT_EXPENSE_RATIO_PCT = 0.33  # matches the case-level renewal loading default (see renewal_rating.py)


def resolve_client_opex_pct(
    master_client: Optional[str],
    policy_start_date: Optional[date_cls],
    opex_records_by_client: Optional[Dict[str, List[dict]]],
    default_opex_pct: float,
) -> float:
    """Which OPEX/Loading % applies to one member, given their own master
    client and policy_start_date.

    A client's real loading sometimes changes from one renewal to the
    next, so the uploaded Client Master sheet (see
    app/ingestion/client_master.py) can carry several dated records for
    the SAME client - each its own {start_date, end_date, opex_pct} -
    rather than one flat figure. This picks whichever record's own
    [start_date, end_date] window actually covers this member's policy
    period, so an earlier and later renewal's loading are never blended
    into one figure for that member.

    Falls back to default_opex_pct whenever no record applies: the
    client has no records at all, none of its records' date windows
    cover this member's own policy_start_date, or the member has no
    policy_start_date to match against in the first place - EXCEPT when
    the client has exactly one record with no dates on it at all, which
    is read as "this client's one flat figure, no date-based logic
    needed" (the common case - most clients won't have renewal-by-
    renewal loading changes on file).
    """
    on_file = client_opex_pct_on_file(master_client, policy_start_date, opex_records_by_client)
    return default_opex_pct if on_file is None else on_file


def client_opex_pct_on_file(
    master_client: Optional[str],
    policy_start_date: Optional[date_cls],
    opex_records_by_client: Optional[Dict[str, List[dict]]],
) -> Optional[float]:
    """This client's own loading from the uploaded Client Master sheet, or
    None where the sheet has nothing that applies.

    Split out from resolve_client_opex_pct so a caller can tell a real
    loading from the house default WITHOUT re-deriving the lookup - the
    two answers now come from one walk of the records rather than from
    two functions that could drift apart on which record wins.

    The distinction matters on screen: 33% shown flat reads as this
    account's loading, and a reader has no way to see that nobody
    supplied it. Every figure resting on it is then partly assumed, and
    nothing says which part.
    """
    if not master_client or not opex_records_by_client:
        return None
    records = opex_records_by_client.get(master_client)
    if not records:
        return None
    if len(records) == 1 and records[0].get("start_date") is None and records[0].get("end_date") is None:
        return records[0]["opex_pct"]
    if not policy_start_date:
        return None
    for record in records:
        start = record.get("start_date")
        end = record.get("end_date")
        if start is not None and policy_start_date < start:
            continue
        if end is not None and policy_start_date > end:
            continue
        return record["opex_pct"]
    return None


def executive_portfolio_summary(
    member_results: List[dict],
    expense_ratio_pct: float = DEFAULT_EXPENSE_RATIO_PCT,
    opex_records_by_client: Optional[Dict[str, List[dict]]] = None,
) -> dict:
    """The top-of-page "Level 1 - Executive Portfolio" KPI set: Total
    Groups, Total Members, Written Premium, Earned Premium, Incurred
    Claims, Loss Ratio, Combined Ratio, Average Premium per Member, Claim
    Frequency, Claim Severity.

    Total Groups/Members count every member on the book (in AND out of
    the rate card's scope - group/member counts aren't a pricing
    question), keyed by master_client (see resolve_master_client) so a
    3-subgroup master counts as one group, not three. Written Premium is
    each member's own full annual premium; Earned Premium prorates it by
    elapsed policy term (same earned_premium_fraction every other metric
    here uses) - Incurred Claims is Paid + Outstanding + IBNR, i.e. the
    same fuller figure loss_ratio_incl_ibnr is built from.

    Combined Ratio = Loss Ratio + an expense ratio (commission + TPA +
    admin + HC/management fees, as a fraction of premium) -
    underwriting's own reminder that a healthy-looking loss ratio can
    still mean an unprofitable book once acquisition/administration cost
    is added on top. `opex_records_by_client` (master_client -> list of
    {start_date, end_date, opex_pct}, from the uploaded Client Master
    sheet - see app/ingestion/client_master.py) gives each client's own
    REAL expense ratio where it's on file, resolved per member by
    resolve_client_opex_pct (so a client whose loading changed between
    renewals uses the right one for each member's own policy period);
    `expense_ratio_pct` (defaults to 33%, the same default loading used
    for a single case's own renewal rating - see renewal_rating.py's
    DEFAULT_LOADING_PCT) is only the FALLBACK for a member with no real
    figure resolved. The reported expense_ratio_pct is the resulting
    premium-weighted BLEND across every member, not a flat assumption,
    whenever any real OPEX is on file at all.

    Claim Frequency (claims per earned member-year) and Claim Severity
    (average AED cost per claim) are the SAME whole-book totals
    summarize_portfolio computes per Product/Network/client row - this is
    just the one number for the entire book, so a poor loss ratio can be
    read as "too many claims" vs. "a few expensive ones" before drilling
    into any one segment.
    """
    groups = set()
    total_members = 0
    written_premium = 0.0
    earned_premium = 0.0
    actual_claims_total = 0.0
    ibnr_total = 0.0
    claim_count = 0
    earned_member_years = 0.0
    weighted_expense = 0.0
    for r in member_results:
        total_members += 1
        master_client = r.get("master_client")
        if master_client:
            groups.add(master_client)
        if r.get("written_premium") is not None:
            written_premium += r["written_premium"]
        premium = r.get("actual_premium")
        if premium is not None:
            earned_premium += premium
            member_opex = resolve_client_opex_pct(
                master_client, r.get("policy_start_date"), opex_records_by_client, expense_ratio_pct
            )
            weighted_expense += premium * member_opex
        actual_claims_total += r.get("actual_claims") or 0.0
        ibnr_total += r.get("ibnr") or 0.0
        claim_count += r.get("claim_count") or 0
        earned_member_years += r.get("earned_premium_fraction") or 0.0

    incurred_claims = actual_claims_total + ibnr_total
    loss_ratio = incurred_claims / earned_premium if earned_premium else None
    blended_expense_ratio_pct = weighted_expense / earned_premium if earned_premium else expense_ratio_pct
    claim_frequency = claim_count / earned_member_years if earned_member_years else None
    claim_severity = actual_claims_total / claim_count if claim_count else None
    return {
        "total_groups": len(groups),
        "total_members": total_members,
        "written_premium": round(written_premium, 2),
        "earned_premium": round(earned_premium, 2),
        "incurred_claims": round(incurred_claims, 2),
        "loss_ratio": round(loss_ratio, 4) if loss_ratio is not None else None,
        "expense_ratio_pct": round(blended_expense_ratio_pct, 4),
        "combined_ratio": round(loss_ratio + blended_expense_ratio_pct, 4) if loss_ratio is not None else None,
        "average_premium_per_member": round(written_premium / total_members, 2) if total_members else None,
        "claim_count": claim_count,
        "claim_frequency": round(claim_frequency, 4) if claim_frequency is not None else None,
        "claim_severity": round(claim_severity, 2) if claim_severity is not None else None,
    }


#: (min_size, max_size or None for open-ended, label) - the same rough
#: credibility bands underwriting uses to decide how much weight a small
#: group's OWN claims experience gets versus the wider portfolio's pooled
#: experience: a 1-10-life group is priced almost entirely off the
#: portfolio/manual rate, while a 100+-life group's own claims carry real
#: credibility on their own.
GROUP_SIZE_BANDS = (
    (1, 10, "1-10"),
    (11, 50, "11-50"),
    (51, 100, "51-100"),
    (101, None, "100+"),
)


def _group_size_band(member_count: int) -> str:
    for lo, hi, label in GROUP_SIZE_BANDS:
        if member_count >= lo and (hi is None or member_count <= hi):
            return label
    return "Unknown"


def summarize_by_group_size_band(member_results: List[dict]) -> List[dict]:
    """Pools every master client's own loss ratio into the credibility
    bands underwriting actually uses (see GROUP_SIZE_BANDS) - a small
    group's own claims experience is too thin to be statistically
    meaningful on its own, so actuaries lean on the POOLED experience of
    every other similarly-sized group instead. Also doubles as "Group
    Size Distribution": each row's group_count/member_count/
    average_group_size describe the book's own shape (how many small vs.
    large groups it actually has), not just their loss ratio.

    Group size (member count) and every rolled-up figure here count
    EVERY member on the book, in and out of the rate card's scope - same
    as executive_portfolio_summary - since a group's headcount and its
    own premium/claims are real regardless of whether its network type
    happens to be priced by this rate card.
    """
    group_member_counts: Dict[str, int] = defaultdict(int)
    for r in member_results:
        master_client = r.get("master_client")
        if master_client:
            group_member_counts[master_client] += 1

    band_by_group: Dict[str, str] = {mc: _group_size_band(n) for mc, n in group_member_counts.items()}

    buckets: Dict[str, dict] = defaultdict(
        lambda: {"groups": set(), "member_count": 0, "actual_premium": 0.0, "actual_claims": 0.0, "ibnr": 0.0}
    )
    for r in member_results:
        master_client = r.get("master_client")
        band = band_by_group.get(master_client, "Unknown") if master_client else "Unknown"
        bucket = buckets[band]
        if master_client:
            bucket["groups"].add(master_client)
        bucket["member_count"] += 1
        if r.get("actual_premium") is not None:
            bucket["actual_premium"] += r["actual_premium"]
        bucket["actual_claims"] += r.get("actual_claims") or 0.0
        bucket["ibnr"] += r.get("ibnr") or 0.0

    band_order = [label for _, _, label in GROUP_SIZE_BANDS] + ["Unknown"]
    rows = []
    for band in band_order:
        if band not in buckets:
            continue
        bucket = buckets[band]
        group_count = len(bucket["groups"])
        incurred_claims = bucket["actual_claims"] + bucket["ibnr"]
        loss_ratio = incurred_claims / bucket["actual_premium"] if bucket["actual_premium"] else None
        rows.append({
            "band": band,
            "group_count": group_count,
            "member_count": bucket["member_count"],
            "average_group_size": round(bucket["member_count"] / group_count, 1) if group_count else None,
            "actual_premium": round(bucket["actual_premium"], 2),
            "actual_claims": round(bucket["actual_claims"], 2),
            "ibnr": round(bucket["ibnr"], 2),
            "incurred_claims": round(incurred_claims, 2),
            "loss_ratio": round(loss_ratio, 4) if loss_ratio is not None else None,
        })
    return rows


def summarize_new_vs_renewal(member_results: List[dict]) -> List[dict]:
    """Splits the book into New Business vs. Renewal by how many distinct
    policy years appear for each master client in this book-wide extract:
    a client with only ONE policy year on file is New Business (this is
    its first year on book); a client with TWO OR MORE is Renewal (it's
    already renewed onto at least one later policy year, evidenced by
    both cohorts' members appearing in the same current extract).

    This is a heuristic, not a true "first ever policy" flag - a
    long-standing renewal client whose prior-year members have since
    fully rolled off the current extract (e.g. a stale/inactive cohort
    purged from the export) would misclassify as New Business under this
    rule, since only what's actually present in the uploaded book can be
    counted. There is no other new-vs-renewal indicator in HealthCross's
    own export to fall back on (POLICYSEQUENCE is blank in practice).
    """
    years_by_group: Dict[str, set] = defaultdict(set)
    for r in member_results:
        master_client = r.get("master_client")
        policy_year = r.get("policy_year")
        if master_client and policy_year:
            years_by_group[master_client].add(policy_year)

    classification_by_group: Dict[str, str] = {
        mc: ("Renewal" if len(years) >= 2 else "New Business") for mc, years in years_by_group.items()
    }

    buckets: Dict[str, dict] = defaultdict(
        lambda: {"groups": set(), "member_count": 0, "actual_premium": 0.0, "actual_claims": 0.0, "ibnr": 0.0}
    )
    for r in member_results:
        master_client = r.get("master_client")
        classification = classification_by_group.get(master_client, "Unknown") if master_client else "Unknown"
        bucket = buckets[classification]
        if master_client:
            bucket["groups"].add(master_client)
        bucket["member_count"] += 1
        if r.get("actual_premium") is not None:
            bucket["actual_premium"] += r["actual_premium"]
        bucket["actual_claims"] += r.get("actual_claims") or 0.0
        bucket["ibnr"] += r.get("ibnr") or 0.0

    rows = []
    for classification in ("New Business", "Renewal", "Unknown"):
        if classification not in buckets:
            continue
        bucket = buckets[classification]
        group_count = len(bucket["groups"])
        incurred_claims = bucket["actual_claims"] + bucket["ibnr"]
        loss_ratio = incurred_claims / bucket["actual_premium"] if bucket["actual_premium"] else None
        rows.append({
            "classification": classification,
            "group_count": group_count,
            "member_count": bucket["member_count"],
            "actual_premium": round(bucket["actual_premium"], 2),
            "actual_claims": round(bucket["actual_claims"], 2),
            "ibnr": round(bucket["ibnr"], 2),
            "incurred_claims": round(incurred_claims, 2),
            "loss_ratio": round(loss_ratio, 4) if loss_ratio is not None else None,
        })
    return rows


def summarize_population_mix(member_results: List[dict]) -> Optional[dict]:
    """The whole book's own population composition (nationality zone mix,
    gender mix, average age) - a reference for comparing a case's own
    census against real HealthCross book norms, the same way
    summarize_burning_cost_overall gives a claims-cost reference. Purely
    informational context, not a scoring input. Returns None for an empty
    book.
    """
    in_scope = [r for r in member_results if r.get("in_scope", True)]
    if not in_scope:
        return None

    total = len(in_scope)
    zone_counts: Dict[str, int] = defaultdict(int)
    gender_counts: Dict[str, int] = defaultdict(int)
    ages = []
    for r in in_scope:
        zone_counts[r.get("nationality_zone") or "Unmapped"] += 1
        gender_counts[r.get("gender") or "Unmapped"] += 1
        if r.get("age") is not None:
            ages.append(r["age"])

    return {
        "member_count": total,
        "avg_age": round(sum(ages) / len(ages), 1) if ages else None,
        "nationality_zone_mix": {zone: round(count / total, 4) for zone, count in zone_counts.items()},
        "gender_mix": {gender: round(count / total, 4) for gender, count in gender_counts.items()},
    }


def demographic_summary(member_results: List[dict]) -> dict:
    """Full demographic profile of the booked book - the same "what does
    this group look like" view a single case's own Census tab shows (age
    bands, gender, marital status, relation, nationality zone mix with top
    nationalities per zone - see census_demographic_summary), reused
    directly here since analyze_portfolio_member's own per-member output
    already carries the same age/gender/marital_status/relation/
    nationality/nationality_zone fields a CensusRecord does. Adds Product
    and Network member counts on top - dimensions a single case's census
    never has (it's already scoped to one scheme), but the whole booked
    book spans many of.

    Deliberately run on EVERY member, not just in-scope ones, so
    total_members matches the same headline count every other Portfolio
    Analysis view reports (see /insights' own `total_members=len(results)`)
    rather than a smaller, inconsistent-looking number - an out-of-scope
    member's age/gender/nationality are still real facts about the
    population even though no rate-card price applies to them. An
    out-of-scope member's result dict carries no product/network at all,
    so they're naturally excluded from the Product/Network counts below
    without needing a separate filter.
    """
    summary = census_demographic_summary(member_results)

    product_counts: Dict[str, int] = defaultdict(int)
    network_counts: Dict[str, int] = defaultdict(int)
    for r in member_results:
        if r.get("product"):
            product_counts[r["product"]] += 1
        if r.get("network"):
            network_counts[r["network"]] += 1
    summary["product_counts"] = dict(sorted(product_counts.items()))
    summary["network_counts"] = dict(sorted(network_counts.items()))
    summary["out_of_scope_member_count"] = sum(1 for r in member_results if not r.get("in_scope", True))
    return summary


#: A policy whose term has fully run has no unreported tail left to
#: reserve for - every claim that will ever be reported against it has
#: been. Above this many elapsed days the account is treated as expired:
#: IBNR drops to zero and the FULL annual premium is earned, rather than
#: prorating past 100%.
FULL_POLICY_TERM_DAYS = 365

#: The unreported tail an open policy reserves for, expressed as a number
#: of days of its own paid-claims run rate (Paid / elapsed days * 30) -
#: the same 30-day convention ibnr_for_member uses per member, applied
#: here at whole-account level instead.
ACCOUNT_IBNR_TAIL_DAYS = 30


#: Which of the Membership export's own premium columns to build the
#: account's Gross Premium from:
#:
#:   "actual" - ActualGrossPremium, each member's premium already prorated
#:              for their own joining/leaving dates. This is the basis
#:              HealthCross underwrites on, and the default here: summed
#:              across members it is the account's real WRITTEN premium,
#:              reflecting who was actually on cover rather than a
#:              full-year price for everyone.
#:   "booked" - GrossPremium, each member's full ANNUAL premium regardless
#:              of enrollment dates. Offered for comparison, and useful
#:              for spotting how much of an account's price never gets
#:              written because of mid-term joiners and leavers.
#:
#: Note that Earned Premium then applies Days / 365 on top of whichever
#: basis is chosen, so an "actual" figure carries member-level proration
#: and account-level earning together.
PREMIUM_BASES = ("actual", "booked")



#: How a loss ratio attributes premium and claims to a period.
#:
#:   "underwriting" - one row per POLICY period. Each policy year is its
#:                    own cohort: premium earned from inception, claims
#:                    matched to the policy that covered them. This is the
#:                    basis for pricing, because it compares claims against
#:                    the premium actually charged to cover them.
#:   "calendar"     - one row per CALENDAR year. A policy spanning a year
#:                    end is SPLIT across both years by the days falling in
#:                    each, and a renewed client's calendar year therefore
#:                    aggregates the tail of the expiring policy with the
#:                    start of the new one. This is the basis for financial
#:                    reporting, because it lines up with the accounting
#:                    period rather than the contract.
YEAR_BASES = ("underwriting", "calendar")


def period_overlap_days(a_start, a_end, b_start, b_end) -> int:
    """Days two date ranges share, counting both endpoints - the same
    inclusive convention as the rest of this module (a policy incepting
    today has one day of exposure, not zero). Zero when they do not
    overlap, or when either range is unusable."""
    if not a_start or not a_end or not b_start or not b_end:
        return 0
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    if end < start:
        return 0
    return (end - start).days + 1


def _calendar_windows(policy_start, policy_end, as_of):
    """Splits a policy period into one window per calendar year it touches,
    stopping at as_of. Yields (year, window_start, window_end) - the pieces
    a calendar-year loss ratio is built from."""
    if not policy_start or not policy_end:
        return
    last = min(policy_end, as_of)
    if last < policy_start:
        return
    for year in range(policy_start.year, last.year + 1):
        window_start = max(policy_start, date_cls(year, 1, 1))
        window_end = min(last, date_cls(year, 12, 31))
        if window_end >= window_start:
            yield year, window_start, window_end

def account_loss_ratio_rows(
    member_results: List[dict],
    as_of: date_cls,
    opex_records_by_client: Optional[Dict[str, List[dict]]] = None,
    default_loading_pct: float = DEFAULT_EXPENSE_RATIO_PCT,
    premium_basis: str = "actual",
) -> List[dict]:
    """Per-account loss ratio, one row per account POLICY PERIOD - the
    underwriting "Loss Ratio" view HealthCross tracks its own book on:

        Days             = as_of - policy effective date
        IBNR             = Paid / Days * 30, or 0 once the policy expired
        Incurred Claims  = Paid + Outstanding + IBNR
        Earned Premium   = Gross * Days / 365, or the FULL Gross once expired
        Net Premium      = Earned * (1 - Loading)
        Gross LR         = Incurred / Earned
        Net LR           = Incurred / Net

    Rows are keyed by (master client, policy start date), NOT by client
    alone: a client that has already renewed has members on two different
    policy periods in the same upload, and each period earns, reserves,
    and runs its own loss ratio separately - collapsing them would blend
    an expired year's settled claims into the current year's open one.

    IBNR here is deliberately an ACCOUNT-level figure (the account's own
    combined paid run rate over its own elapsed days), not the sum of each
    member's own ibnr_for_member - one member's part-year enrollment
    shouldn't project its own 30-day tail independently of the policy the
    account actually runs on.

    Loading is each client's own real OPEX % from the uploaded Client
    Master sheet where it's on file (resolved per policy period via
    resolve_client_opex_pct, so a client whose loading changed between
    renewals uses the right one for each), falling back to
    default_loading_pct otherwise.

    Gross Premium comes from the Membership export's ActualGrossPremium
    column by default - each member's premium already prorated for their
    own joining/leaving dates, which is the basis HealthCross underwrites
    on. Pass premium_basis="booked" to build it from the full annual
    GrossPremium instead; see PREMIUM_BASES.
    """
    if premium_basis not in PREMIUM_BASES:
        raise ValueError(f"premium_basis must be one of {PREMIUM_BASES}")
    premium_field = "booked_gross_premium" if premium_basis == "booked" else "written_premium"

    buckets: Dict[tuple, dict] = {}
    for r in member_results:
        master_client = r.get("master_client")
        policy_start = r.get("policy_start_date")
        if not master_client or not policy_start:
            continue
        key = (master_client, policy_start)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = buckets[key] = {
                "master_client": master_client,
                "policy_start_date": policy_start,
                "member_count": 0,
                "paid": 0.0,
                "outstanding": 0.0,
                "gross_premium": 0.0,
                "claim_count": 0,
            }
        bucket["member_count"] += 1
        bucket["paid"] += r.get("actual_claims_paid") or 0.0
        bucket["outstanding"] += r.get("actual_claims_outstanding") or 0.0
        bucket["gross_premium"] += r.get(premium_field) or 0.0
        bucket["claim_count"] += r.get("claim_count") or 0

    rows = []
    for (master_client, policy_start), bucket in buckets.items():
        # Inclusive of the effective date itself - a policy incepting today
        # has one day of exposure, not zero. Matches the book's own Loss
        # Ratio sheet exactly; an exclusive count understates elapsed days
        # by one, which then flows through IBNR, Earned Premium and both
        # loss ratios.
        days = (as_of - policy_start).days + 1
        expired = days > FULL_POLICY_TERM_DAYS
        paid = bucket["paid"]
        outstanding = bucket["outstanding"]
        gross_premium = bucket["gross_premium"]

        ibnr = 0.0 if (expired or days <= 0 or not paid) else paid / days * ACCOUNT_IBNR_TAIL_DAYS
        incurred_claims = paid + outstanding + ibnr

        if expired:
            earned_premium = gross_premium
        elif days > 0:
            earned_premium = gross_premium * days / FULL_POLICY_TERM_DAYS
        else:
            earned_premium = 0.0

        on_file = client_opex_pct_on_file(master_client, policy_start, opex_records_by_client)
        loading_pct = default_loading_pct if on_file is None else on_file
        net_premium = earned_premium * (1 - loading_pct)

        rows.append(
            {
                "master_client": master_client,
                "policy_start_date": policy_start.isoformat(),
                "premium_basis": premium_basis,
                "member_count": bucket["member_count"],
                "days": days,
                "expired": expired,
                "paid": round(paid, 2),
                "outstanding": round(outstanding, 2),
                "ibnr": round(ibnr, 2),
                "incurred_claims": round(incurred_claims, 2),
                "loading_pct": round(loading_pct, 4),
                # Whether anyone actually supplied that loading, or the
                # house average is standing in for one. The net loss ratio
                # built on it is only as real as this flag says, and a
                # screen showing 33% flat gives the reader no way to tell.
                "loading_is_default": on_file is None,
                "gross_premium": round(gross_premium, 2),
                "earned_premium": round(earned_premium, 2),
                "net_premium": round(net_premium, 2),
                "gross_loss_ratio": round(incurred_claims / earned_premium, 4) if earned_premium else None,
                "net_loss_ratio": round(incurred_claims / net_premium, 4) if net_premium else None,
                "claim_count": bucket["claim_count"],
            }
        )

    rows.sort(key=lambda r: (r["master_client"], r["policy_start_date"]))
    return rows


def account_loss_ratio_totals(rows: List[dict]) -> dict:
    """Book-wide totals across account_loss_ratio_rows - the sheet's own
    bottom line. Loss ratios are recomputed from the summed amounts, not
    averaged across rows: a 12-life account's own ratio must not carry the
    same weight as a 900-life one in a book-wide figure."""
    paid = sum(r["paid"] for r in rows)
    outstanding = sum(r["outstanding"] for r in rows)
    ibnr = sum(r["ibnr"] for r in rows)
    incurred_claims = sum(r["incurred_claims"] for r in rows)
    gross_premium = sum(r["gross_premium"] for r in rows)
    earned_premium = sum(r["earned_premium"] for r in rows)
    net_premium = sum(r["net_premium"] for r in rows)
    return {
        "account_count": len(rows),
        "member_count": sum(r["member_count"] for r in rows),
        "claim_count": sum(r["claim_count"] for r in rows),
        "paid": round(paid, 2),
        "outstanding": round(outstanding, 2),
        "ibnr": round(ibnr, 2),
        "incurred_claims": round(incurred_claims, 2),
        "gross_premium": round(gross_premium, 2),
        "earned_premium": round(earned_premium, 2),
        "net_premium": round(net_premium, 2),
        "gross_loss_ratio": round(incurred_claims / earned_premium, 4) if earned_premium else None,
        "net_loss_ratio": round(incurred_claims / net_premium, 4) if net_premium else None,
    }


def _reprice_scenario(
    row: dict,
    book_incurred: float,
    book_net: float,
    book_net_lr: Optional[float],
    target_net_loss_ratio: float,
) -> dict:
    """The other half of the decision: what if this account is RENEWED, at
    the price its own experience actually justifies?

    Shedding is the option of last resort. Re-pricing keeps the premium,
    keeps the relationship, and fixes the loss ratio at source - and
    because the account stays on the book, its premium keeps absorbing
    expense that would otherwise be spread over a smaller book. The
    comparison an underwriter needs is not "how bad is this account" but
    "shed or re-price, and what does each do to the book".

    The required increase is computed on the account's own claims at its
    own loading, so it is the increase that makes THIS account stand on
    its own feet - not a blanket uplift. An increase above roughly 30-40%
    is where clients start shopping the market, which is exactly when
    shedding becomes the realistic alternative rather than a threat.
    """
    incurred = row["incurred_claims"]
    current_net = row["net_premium"]
    current_earned = row["earned_premium"]
    if not current_earned or not incurred or target_net_loss_ratio <= 0:
        return {
            "required_net_premium": None,
            "required_earned_premium": None,
            "required_increase_pct": None,
            "book_net_loss_ratio_repriced": None,
            "repriced_lr_change": None,
        }

    # The account's own expense loading, implied by the gap between what
    # it earns and what is left to fund claims after expenses.
    retention = (current_net / current_earned) if current_earned else 1.0

    required_net = incurred / target_net_loss_ratio
    required_earned = required_net / retention if retention else None
    book_net_repriced = book_net - current_net + required_net
    lr_repriced = round(book_incurred / book_net_repriced, 4) if book_net_repriced > 0 else None

    return {
        "required_net_premium": round(required_net, 2),
        "required_earned_premium": round(required_earned, 2) if required_earned else None,
        "required_increase_pct": (
            round((required_earned / current_earned - 1) * 100, 1) if required_earned else None
        ),
        "book_net_loss_ratio_repriced": lr_repriced,
        "repriced_lr_change": (
            round(lr_repriced - book_net_lr, 4)
            if lr_repriced is not None and book_net_lr is not None else None
        ),
    }


#: The net loss ratio an account is re-priced TO. 1.0 means the premium
#: exactly funds expected claims after expenses - break-even, no margin.
#: Deliberately a parameter: pricing every remediation to break-even
#: leaves nothing for adverse development, while pricing well under the
#: house maximum asks for an increase some clients will simply refuse.
DEFAULT_TARGET_NET_LOSS_RATIO = 1.0


def loss_ratio_shed_impact(
    rows: List[dict],
    top_n: Optional[int] = None,
    target_net_loss_ratio: float = DEFAULT_TARGET_NET_LOSS_RATIO,
) -> dict:
    """What the book's loss ratio becomes if a given account is not
    renewed - the "should we walk away from this one?" calculation.

    An account only improves the book by leaving if its OWN loss ratio is
    worse than the book's, and by how much depends on its size as well as
    its ratio: a 400% account with two lives on it moves nothing, while a
    115% account carrying a tenth of the premium moves the book
    materially. Ranking accounts by their own loss ratio answers the
    wrong question; this ranks them by the improvement actually on offer.

    Both loss ratios are recomputed from the remaining amounts rather
    than averaged - removing an account takes its premium out along with
    its claims, and a book LR is a ratio of sums, never a mean of ratios.

    `premium_at_risk` is the other half of the decision and is returned
    alongside: shedding an account gives up its premium permanently, and
    expense loadings are spread over a book that is now smaller. This
    function deliberately does not net the two into a recommendation -
    whether losing AED 2m of premium to gain 3 points of loss ratio is
    worth it is a portfolio strategy question, not an arithmetic one.
    """
    totals = account_loss_ratio_totals(rows)
    book_incurred = totals["incurred_claims"]
    book_earned = totals["earned_premium"]
    book_net = totals["net_premium"]
    book_gross_lr = totals["gross_loss_ratio"]
    book_net_lr = totals["net_loss_ratio"]

    # Roll each account's policy periods together before measuring the
    # impact. On the underwriting basis an account renewed once appears as
    # TWO rows (2025 and 2026), and you do not lose one policy year of a
    # client - you lose the client. Measuring a single row's removal while
    # the decision removes every row for that client understates the
    # impact on any multi-period account, which is most of the book.
    by_client: Dict[str, dict] = {}
    for row in rows:
        name = row["master_client"]
        acc = by_client.setdefault(name, {
            "master_client": name, "member_count": 0, "incurred_claims": 0.0,
            "earned_premium": 0.0, "net_premium": 0.0, "period_count": 0,
        })
        acc["member_count"] += row["member_count"]
        acc["incurred_claims"] += row["incurred_claims"]
        acc["earned_premium"] += row["earned_premium"]
        acc["net_premium"] += row["net_premium"]
        acc["period_count"] += 1

    impacts = []
    for row in by_client.values():
        row = {
            **row,
            "gross_loss_ratio": (
                round(row["incurred_claims"] / row["earned_premium"], 4) if row["earned_premium"] else None
            ),
            "net_loss_ratio": (
                round(row["incurred_claims"] / row["net_premium"], 4) if row["net_premium"] else None
            ),
        }
        remaining_earned = book_earned - row["earned_premium"]
        remaining_net = book_net - row["net_premium"]
        remaining_incurred = book_incurred - row["incurred_claims"]

        gross_lr_without = round(remaining_incurred / remaining_earned, 4) if remaining_earned > 0 else None
        net_lr_without = round(remaining_incurred / remaining_net, 4) if remaining_net > 0 else None

        impacts.append({
            "master_client": row["master_client"],
            "period_count": row["period_count"],
            "member_count": row["member_count"],
            "earned_premium": row["earned_premium"],
            "incurred_claims": row["incurred_claims"],
            "gross_loss_ratio": row["gross_loss_ratio"],
            "net_loss_ratio": row["net_loss_ratio"],
            "book_gross_loss_ratio_without": gross_lr_without,
            "book_net_loss_ratio_without": net_lr_without,
            # Negative = the book improves by shedding this account.
            "gross_lr_change": (
                round(gross_lr_without - book_gross_lr, 4)
                if gross_lr_without is not None and book_gross_lr is not None else None
            ),
            "net_lr_change": (
                round(net_lr_without - book_net_lr, 4)
                if net_lr_without is not None and book_net_lr is not None else None
            ),
            "premium_at_risk": row["earned_premium"],
            "share_of_book_premium": (
                round(row["earned_premium"] / book_earned, 4) if book_earned else None
            ),
            **_reprice_scenario(row, book_incurred, book_net, book_net_lr, target_net_loss_ratio),
        })

    # An account carrying claims but NO premium always tops a ranking by
    # improvement - removing it costs nothing and takes claims out, so the
    # arithmetic makes it look like the best decision on the book. It is
    # almost never a shedding opportunity; it is a data gap (a missing
    # premium column, an unmapped subgroup, a client on the claims file
    # that never made it onto the membership export). Ranking those
    # alongside real candidates would put the underwriter's attention on
    # exactly the wrong accounts, so they come back separately, labelled
    # for what they are.
    unpriced = [i for i in impacts if not i["earned_premium"] and i["incurred_claims"]]
    candidates = [i for i in impacts if i["earned_premium"]]

    # Biggest improvement first - most negative change at the top.
    candidates.sort(key=lambda i: (i["net_lr_change"] if i["net_lr_change"] is not None else 0.0))
    unpriced.sort(key=lambda i: -i["incurred_claims"])
    if top_n:
        candidates = candidates[:top_n]

    return {
        "book_gross_loss_ratio": book_gross_lr,
        "book_net_loss_ratio": book_net_lr,
        "book_earned_premium": book_earned,
        "book_incurred_claims": book_incurred,
        "account_count": len(by_client),
        "accounts": candidates,
        "unpriced_accounts": unpriced,
        "unpriced_incurred": round(sum(i["incurred_claims"] for i in unpriced), 2),
    }


def loss_ratio_shed_cumulative(rows: List[dict], max_accounts: int = 10) -> List[dict]:
    """Shedding accounts one after another, worst first - because the
    single-account impacts above do NOT add up.

    Each standalone impact is measured against the ORIGINAL book. Once
    the worst account is gone both the numerator and the denominator have
    moved, so the next account's improvement is measured from a different
    baseline. The combined effect is not the sum of the parts, and it is
    not reliably smaller OR larger than that sum either - which way it
    lands depends on the relative size and ratio of the accounts
    involved, so there is no shortcut correction to apply. The only way
    to know what shedding several accounts achieves is to walk it:
    remove, recompute, remove again.
    """
    remaining = list(rows)
    shed: List[dict] = []
    out = []

    for _ in range(min(max_accounts, len(rows))):
        totals = account_loss_ratio_totals(remaining)
        current_lr = totals["net_loss_ratio"]
        if current_lr is None:
            break
        # Whichever single account left to drop improves the book most
        # from where it now stands, not from where it originally stood.
        impact = loss_ratio_shed_impact(remaining)
        best = impact["accounts"][0] if impact["accounts"] else None
        if best is None or best["net_lr_change"] is None or best["net_lr_change"] >= 0:
            break  # nothing left whose removal helps

        remaining = [r for r in remaining if r["master_client"] != best["master_client"]]
        shed.append(best)
        new_totals = account_loss_ratio_totals(remaining)
        out.append({
            "step": len(shed),
            "master_client": best["master_client"],
            "own_net_loss_ratio": best["net_loss_ratio"],
            "premium_given_up": best["earned_premium"],
            "book_net_loss_ratio": new_totals["net_loss_ratio"],
            "book_earned_premium": new_totals["earned_premium"],
            "cumulative_premium_given_up": round(sum(s["earned_premium"] for s in shed), 2),
            "cumulative_lr_change": (
                round(new_totals["net_loss_ratio"] - current_lr, 4)
                if new_totals["net_loss_ratio"] is not None else None
            ),
        })

    return out


#: Credibility at which a nationality's own factor is considered solid
#: enough to actually price on. Below it the factor is still computed and
#: shown - it is real information, and it is what will cross the line as
#: the book grows - but it is marked not-yet-pricing-ready so a thin
#: segment is never mistaken for an established one. Deliberately a
#: parameter rather than a constant: the right threshold depends on how
#: much of the book an underwriter is willing to price off partial data.
DEFAULT_PRICING_CREDIBILITY = 0.5


def nationality_risk_table(
    member_results: List[dict],
    full_credibility_member_years: float = FULL_CREDIBILITY_MEMBER_YEARS,
    min_relativity: float = 0.5,
    max_relativity: float = 2.0,
    pricing_credibility: float = DEFAULT_PRICING_CREDIBILITY,
) -> List[dict]:
    """Per-nationality burning cost, credibility-weighted toward the
    nationality's own zone - the evidence behind a nationality rating
    factor, rather than a raw rate that a handful of claims could swing.

    Zone is the complement rather than the whole book deliberately: a
    nationality's nearest comparable population is the other nationalities
    in its own zone, so a thin Egyptian cell falls back to Middle East
    experience rather than to a book average dominated by a different
    zone entirely.

    Every row carries the exposure standing behind it and the credibility
    that exposure earned, so a number can never be read without seeing how
    much data it rests on. age/gender mix is carried for the same reason:
    nationality correlates with age, role and gender mix, so a nationality
    that looks expensive may really be an older or more female population,
    and the mix is what lets an underwriter tell those apart instead of
    baking a confounded signal into a rate.

    relativity is the figure a quote would actually use - the blended rate
    over the whole book's rate, capped (see credibility.relativity).
    """
    by_nationality: Dict[str, dict] = defaultdict(
        lambda: {"claims": 0.0, "exposure": 0.0, "members": 0, "zone": None,
                 "ages": [], "female": 0, "gendered": 0}
    )
    by_zone: Dict[str, dict] = defaultdict(lambda: {"claims": 0.0, "exposure": 0.0})
    book_claims = 0.0
    book_exposure = 0.0

    for r in member_results:
        nationality = (r.get("nationality") or "").strip() or None
        if not nationality:
            continue
        zone = r.get("nationality_zone")
        claims = (r.get("actual_claims") or 0.0) + (r.get("ibnr") or 0.0)
        exposure = r.get("earned_premium_fraction") or 0.0

        bucket = by_nationality[nationality]
        bucket["claims"] += claims
        bucket["exposure"] += exposure
        bucket["members"] += 1
        if bucket["zone"] is None and zone:
            bucket["zone"] = zone
        if r.get("age") is not None:
            bucket["ages"].append(r["age"])
        gender = (r.get("gender") or "").strip().upper()
        if gender in ("M", "F"):
            bucket["gendered"] += 1
            if gender == "F":
                bucket["female"] += 1

        if zone:
            by_zone[zone]["claims"] += claims
            by_zone[zone]["exposure"] += exposure
        book_claims += claims
        book_exposure += exposure

    book_rate = book_claims / book_exposure if book_exposure else None
    zone_rate = {
        z: (b["claims"] / b["exposure"] if b["exposure"] else None) for z, b in by_zone.items()
    }

    rows = []
    for nationality, b in by_nationality.items():
        own_rate = b["claims"] / b["exposure"] if b["exposure"] else None
        # Falls back to the book rate for a nationality whose zone could
        # not be resolved - still a complement, just a broader one.
        complement = zone_rate.get(b["zone"]) if b["zone"] else None
        if complement is None:
            complement = book_rate

        blend = blend_with_complement(
            own_rate, complement, b["exposure"],
            full_credibility_member_years=full_credibility_member_years,
        )
        rows.append(
            {
                "nationality": nationality,
                "nationality_zone": b["zone"],
                "member_count": b["members"],
                "earned_member_years": round(b["exposure"], 4),
                "incurred_claims": round(b["claims"], 2),
                "burning_cost": round(own_rate, 2) if own_rate is not None else None,
                "zone_burning_cost": round(complement, 2) if complement is not None else None,
                "credibility": blend["credibility"],
                "credible_burning_cost": blend["blended_rate"],
                "relativity": relativity(
                    blend["blended_rate"], book_rate,
                    min_relativity=min_relativity, max_relativity=max_relativity,
                ),
                # Carried so a nationality's apparent cost can be checked
                # against its population rather than taken at face value.
                "avg_age": round(sum(b["ages"]) / len(b["ages"]), 1) if b["ages"] else None,
                "female_pct": round(b["female"] / b["gendered"] * 100, 1) if b["gendered"] else None,
                # Enough exposure behind it to price on today. A nationality
                # below this still gets a factor - it is real information,
                # and it is what crosses the line as the book grows - but
                # it should not be mistaken for an established rate.
                "pricing_ready": blend["credibility"] >= pricing_credibility,
                # How much more exposure would take this nationality to
                # full credibility - i.e. what the book has to grow by
                # before its own experience is trusted outright.
                "member_years_to_full_credibility": round(
                    max(0.0, full_credibility_member_years - b["exposure"]), 1
                ),
            }
        )

    rows.sort(key=lambda r: (r["relativity"] is None, -(r["relativity"] or 0)))
    return rows


def account_calendar_loss_ratio_rows(
    member_results: List[dict],
    claims_by_beneficiary: Dict[str, List[dict]],
    as_of: date_cls,
    opex_records_by_client: Optional[Dict[str, List[dict]]] = None,
    default_loading_pct: float = DEFAULT_EXPENSE_RATIO_PCT,
    premium_basis: str = "actual",
) -> List[dict]:
    """Loss ratio on a CALENDAR year basis - one row per account per
    calendar year, rather than per policy period (see YEAR_BASES).

    A policy spanning a year end contributes to BOTH years, split by the
    days falling in each: a 1 May 2025 - 1 May 2026 policy earns 245/365
    of its premium into 2025 and 121/365 into 2026, with each year's
    claims taken from treatments dated inside that year's own window. A
    renewed client's calendar year therefore aggregates the tail of the
    expiring policy with the start of the new one - which is exactly the
    difference from underwriting-year basis, where each policy stays its
    own cohort.

    IBNR is reserved only for a window still open at the report date. A
    calendar year that closed before then has had time to develop, so
    reserving an unreported tail against it would overstate a period whose
    claims are already in.

    Gross premium here is WRITTEN premium, attributed wholly to the year
    the policy incepts - the standard accounting split, and what keeps a
    book-wide total from counting a year-spanning policy twice. Earned
    premium is the time-apportioned figure and is what both loss ratios
    are measured against.
    """
    if premium_basis not in PREMIUM_BASES:
        raise ValueError(f"premium_basis must be one of {PREMIUM_BASES}")
    premium_field = "booked_gross_premium" if premium_basis == "booked" else "written_premium"

    buckets: Dict[tuple, dict] = {}
    for r in member_results:
        master_client = r.get("master_client")
        policy_start = r.get("policy_start_date")
        policy_end = r.get("policy_end_date") or r.get("policy_start_date")
        if not master_client or not policy_start:
            continue

        annual_premium = r.get(premium_field) or 0.0
        # Claims are re-matched by treatment date per window, so the
        # member's own enrollment period still bounds them - a member who
        # left mid-year must not pick up claims from after they left.
        enroll_start = r.get("member_start_date") or policy_start
        enroll_end = r.get("member_end_date") or policy_end

        for year, window_start, window_end in _calendar_windows(policy_start, policy_end, as_of):
            key = (master_client, year)
            bucket = buckets.get(key)
            if bucket is None:
                bucket = buckets[key] = {
                    "master_client": master_client, "year": year,
                    "member_count": 0, "paid": 0.0, "outstanding": 0.0,
                    "gross_premium": 0.0, "earned_premium": 0.0,
                    "claim_count": 0, "days": 0, "open": False,
                    "policy_start_dates": set(),
                    # A renewing member appears as two rows whose periods
                    # MEET on the renewal date (old policy ends and new
                    # one starts the same day). Both windows contain that
                    # day and both land in this same calendar bucket, so a
                    # claim treated on the boundary would otherwise be
                    # counted twice. One claim is one payment - identity
                    # here keeps it counted once, whichever policy row
                    # reaches it first.
                    "seen_claims": set(),
                }
            days = (window_end - window_start).days + 1
            bucket["member_count"] += 1
            bucket["days"] = max(bucket["days"], days)
            # WRITTEN premium belongs wholly to the year the policy
            # INCEPTS, the standard accounting treatment - only EARNED
            # premium spreads across the years the policy runs through.
            # Adding the annual figure to every window a policy touches
            # would count a year-spanning policy's premium twice in any
            # book-wide total.
            if year == policy_start.year:
                bucket["gross_premium"] += annual_premium
            bucket["earned_premium"] += annual_premium * days / FULL_POLICY_TERM_DAYS
            bucket["policy_start_dates"].add(policy_start)
            if window_end >= as_of:
                bucket["open"] = True

            for c in claims_by_beneficiary.get(r.get("beneficiary_id"), []):
                treated = c.get("date_of_treatment")
                if not treated or not (window_start <= treated <= window_end):
                    continue
                if not _claim_matches_period(treated, enroll_start, enroll_end):
                    continue
                if id(c) in bucket["seen_claims"]:
                    continue
                bucket["seen_claims"].add(id(c))
                amount = c.get("final_amount") or 0.0
                if _is_paid_claim_status(c.get("claim_status")):
                    bucket["paid"] += amount
                else:
                    bucket["outstanding"] += amount
                bucket["claim_count"] += 1

    rows = []
    for (master_client, year), b in buckets.items():
        paid, outstanding = b["paid"], b["outstanding"]
        days = b["days"]
        # Only a window still running at the report date has an unreported
        # tail worth reserving for - see the docstring.
        ibnr = (paid / days * ACCOUNT_IBNR_TAIL_DAYS) if (b["open"] and days > 0 and paid) else 0.0
        incurred_claims = paid + outstanding + ibnr
        earned_premium = b["earned_premium"]

        loading_pct = resolve_client_opex_pct(
            master_client, min(b["policy_start_dates"]) if b["policy_start_dates"] else None,
            opex_records_by_client, default_loading_pct,
        )
        net_premium = earned_premium * (1 - loading_pct)

        rows.append(
            {
                "master_client": master_client,
                "calendar_year": year,
                "policy_start_date": str(year),
                "premium_basis": premium_basis,
                "year_basis": "calendar",
                "member_count": b["member_count"],
                "days": days,
                "expired": not b["open"],
                "paid": round(paid, 2),
                "outstanding": round(outstanding, 2),
                "ibnr": round(ibnr, 2),
                "incurred_claims": round(incurred_claims, 2),
                "loading_pct": round(loading_pct, 4),
                "gross_premium": round(b["gross_premium"], 2),
                "earned_premium": round(earned_premium, 2),
                "net_premium": round(net_premium, 2),
                "gross_loss_ratio": round(incurred_claims / earned_premium, 4) if earned_premium else None,
                "net_loss_ratio": round(incurred_claims / net_premium, 4) if net_premium else None,
                "claim_count": b["claim_count"],
            }
        )

    rows.sort(key=lambda r: (r["master_client"], r["calendar_year"]))
    return rows
