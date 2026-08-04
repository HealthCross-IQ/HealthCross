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
from typing import Dict, List, Optional

from app.reference.network_type_mapping import is_out_of_scope_network_type, map_network_type
from app.scoring.rules.new_business_rating import price_member

BOOK_TPA = "MSH MENA"


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
    """
    if not policy_start or not policy_end or not date_of_treatment:
        return True
    return policy_start <= date_of_treatment <= policy_end


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
    for c in claims_by_beneficiary.get(beneficiary_id, []):
        if not _claim_matches_period(c.get("date_of_treatment"), period_start, period_end):
            continue
        amount = c.get("final_amount") or 0.0
        if _is_paid_claim_status(c.get("claim_status")):
            paid += amount
        else:
            outstanding += amount
    return {"total": paid + outstanding, "paid": paid, "outstanding": outstanding}


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

    if is_out_of_scope_network_type(member.get("network_type_raw")):
        return {
            "beneficiary_id": beneficiary_id,
            "in_scope": False,
            "reason": f"'{member.get('network_type_raw')}' is outside the UAE rate card's scope",
        }

    warnings: List[str] = []
    network = map_network_type(member.get("network_type_raw"))
    if network is None:
        warnings.append(f"Unrecognized network type '{member.get('network_type_raw')}'")

    product = resolve_group_product(member, group_product_by_name)
    if not product:
        warnings.append("No Product mapping found for this member's group")

    earned_fraction = earned_premium_fraction(member.get("policy_start_date"), member.get("policy_end_date"), as_of)

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

    actual_gross_premium = member.get("actual_gross_premium")
    actual_premium = actual_gross_premium * earned_fraction if actual_gross_premium is not None else None
    claims_breakdown = actual_claims_for_member(member, claims_by_beneficiary)

    return {
        "beneficiary_id": beneficiary_id,
        "in_scope": True,
        "product": product,
        "network": network,
        "region": member.get("region"),
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
        "age": member.get("age"),
        # The calendar year a member's own policy period started in - a
        # client that's already renewed will have some members on last
        # year's policy and some on this year's within the same upload;
        # this is what lets those cohorts be told apart (group_by or the
        # policy_year filter in _run_analysis).
        "policy_year": str(member["policy_start_date"].year) if member.get("policy_start_date") else None,
        "standard_premium": round(standard_premium, 2) if standard_premium is not None else None,
        "actual_premium": round(actual_premium, 2) if actual_premium is not None else None,
        "actual_claims": round(claims_breakdown["total"], 2),
        "actual_claims_paid": round(claims_breakdown["paid"], 2),
        "actual_claims_outstanding": round(claims_breakdown["outstanding"], 2),
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


_GROUP_BY_FIELDS = {"product", "network", "region", "nationality_zone", "client", "master_client", "gender", "relation", "policy_year"}


def summarize_portfolio(member_results: List[dict], group_by: str) -> List[dict]:
    """Rolls up analyze_portfolio_member's per-member results by one
    dimension (product/network/region/nationality_zone/client/gender/
    relation) - members outside the rate card's scope are excluded
    entirely (see analyze_portfolio_member), and a member missing that
    dimension's own value (e.g. no Product mapping yet) rolls up under
    "Unmapped" rather than being dropped silently. "client" groups by the
    member's own contract (sub-group), falling back to its master
    contract. "gender"/"relation" (employee/spouse/child) let pricing see
    burning cost by demographic segment, not just Product/Network-wide -
    e.g. spouse burning cost typically running well above employee's.
    """
    if group_by not in _GROUP_BY_FIELDS:
        raise ValueError(f"group_by must be one of {sorted(_GROUP_BY_FIELDS)}")

    buckets: Dict[str, dict] = defaultdict(
        lambda: {
            "member_count": 0,
            "priced_member_count": 0,
            "standard_premium": 0.0,
            "actual_premium": 0.0,
            "actual_claims": 0.0,
            "actual_claims_paid": 0.0,
            "actual_claims_outstanding": 0.0,
            "earned_member_years": 0.0,
        }
    )
    for r in member_results:
        if not r.get("in_scope", True):
            continue
        key = r.get(group_by) or "Unmapped"
        bucket = buckets[key]
        bucket["member_count"] += 1
        if r.get("actual_premium") is not None:
            bucket["actual_premium"] += r["actual_premium"]
        bucket["actual_claims"] += r.get("actual_claims") or 0.0
        bucket["actual_claims_paid"] += r.get("actual_claims_paid") or 0.0
        bucket["actual_claims_outstanding"] += r.get("actual_claims_outstanding") or 0.0
        bucket["earned_member_years"] += r.get("earned_premium_fraction") or 0.0
        if r.get("standard_premium") is not None:
            bucket["standard_premium"] += r["standard_premium"]
            bucket["priced_member_count"] += 1

    rows = []
    for key, bucket in buckets.items():
        standard_premium = bucket["standard_premium"]
        actual_premium = bucket["actual_premium"]
        actual_claims = bucket["actual_claims"]
        earned_member_years = bucket["earned_member_years"]
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
                "loss_ratio_vs_standard": round(actual_claims / standard_premium, 4) if standard_premium else None,
                "loss_ratio_vs_actual": round(actual_claims / actual_premium, 4) if actual_premium else None,
                # Positive = actual premium sits above standard (charging
                # more than the rate card); negative = discounted below it.
                "actual_vs_standard_pct": round((actual_premium - standard_premium) / standard_premium * 100, 2)
                if standard_premium
                else None,
                "earned_member_years": round(earned_member_years, 4),
                # Claims cost per earned member-year (AED per member per
                # annum) - the technical "burning cost" rate underwriters
                # use to set a required premium independent of what was
                # actually charged, unlike the premium-relative loss ratios above.
                "burning_cost": round(actual_claims / earned_member_years, 2) if earned_member_years else None,
            }
        )
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
            }
        )
    rows.sort(key=lambda r: (r["age_band"], r["gender"]))
    return rows
