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
    "gn excluding american & mediclinic group": "MSH Comprehensive",
    "restricted +++": "MSH Premium",
    "restricted+++": "MSH Premium",
    "restricted": "MSH Enhanced",
    "super restricted+ zulaikha": "MSH Regular",
}


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
    return NAS_TO_MSH_NETWORK.get(network.strip().lower(), network)


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
        # Still a real person in the population even though no rate-card
        # price applies to them - their age/gender/relation/nationality
        # are real facts, not something scope exclusion should also erase.
        # See demographic_summary, which counts every member (in and out
        # of scope) toward the same headline total every other Portfolio
        # Analysis view reports.
        return {
            "beneficiary_id": beneficiary_id,
            "in_scope": False,
            "reason": f"'{member.get('network_type_raw')}' is outside the UAE rate card's scope",
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


_GROUP_BY_FIELDS = {
    "product", "network", "region", "nationality_zone", "client", "master_client",
    "gender", "relation", "policy_year", "category",
}


def summarize_portfolio(member_results: List[dict], group_by: str) -> List[dict]:
    """Rolls up analyze_portfolio_member's per-member results by one
    dimension (product/network/region/nationality_zone/client/gender/
    relation/category) - members outside the rate card's scope are excluded
    entirely (see analyze_portfolio_member), and a member missing that
    dimension's own value (e.g. no Product mapping yet) rolls up under
    "Unmapped" rather than being dropped silently. "client" groups by the
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
            "actual_premium": 0.0,
            "actual_claims": 0.0,
            "actual_claims_paid": 0.0,
            "actual_claims_outstanding": 0.0,
            "earned_member_years": 0.0,
            "product": None,
            "network": None,
            "policy_start_date": None,
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
    """Re-prices a New Business case's own census against the booked book's
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
