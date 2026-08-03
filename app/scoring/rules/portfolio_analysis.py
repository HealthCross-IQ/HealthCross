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
from typing import Dict, List, Optional

from app.reference.network_type_mapping import is_out_of_scope_network_type, map_network_type
from app.scoring.rules.new_business_rating import price_member

BOOK_TPA = "MSH MENA"


def resolve_group_product(member: dict, group_product_by_name: Dict[str, str]) -> Optional[str]:
    """A member's own contract (sub-group) takes priority over its
    master-contract - a master group split across multiple Products (one
    sub-group upgraded, say) would otherwise all resolve to whichever
    Product the master-level mapping happened to carry.
    """
    return group_product_by_name.get(member.get("contract")) or group_product_by_name.get(member.get("master_contract"))


def analyze_portfolio_member(
    member: dict,
    group_product_by_name: Dict[str, str],
    rate_cards: List[dict],
    variant_rates: List[dict],
    actual_claims_by_beneficiary: Dict[str, float],
) -> dict:
    """member: one row from app/ingestion/portfolio_members.py."""
    beneficiary_id = member["beneficiary_id"]

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
        standard_premium = price_result["net_total"]
        warnings.extend(price_result["warnings"])

    return {
        "beneficiary_id": beneficiary_id,
        "in_scope": True,
        "product": product,
        "network": network,
        "region": member.get("region"),
        "nationality_zone": member.get("nationality_zone"),
        "standard_premium": round(standard_premium, 2) if standard_premium is not None else None,
        "actual_premium": member.get("actual_gross_premium"),
        "actual_claims": round(actual_claims_by_beneficiary.get(beneficiary_id, 0.0), 2),
        "warnings": warnings,
    }


def claims_total_by_beneficiary(claims: List[dict]) -> Dict[str, float]:
    totals: Dict[str, float] = defaultdict(float)
    for c in claims:
        patient_id = c.get("patient_id")
        if patient_id:
            totals[patient_id] += c.get("final_amount") or 0.0
    return dict(totals)


_GROUP_BY_FIELDS = {"product", "network", "region", "nationality_zone"}


def summarize_portfolio(member_results: List[dict], group_by: str) -> List[dict]:
    """Rolls up analyze_portfolio_member's per-member results by one
    dimension (product/network/region/nationality_zone) - members outside
    the rate card's scope are excluded entirely (see analyze_portfolio_member),
    and a member missing that dimension's own value (e.g. no Product
    mapping yet) rolls up under "Unmapped" rather than being dropped
    silently.
    """
    if group_by not in _GROUP_BY_FIELDS:
        raise ValueError(f"group_by must be one of {sorted(_GROUP_BY_FIELDS)}")

    buckets: Dict[str, dict] = defaultdict(
        lambda: {"member_count": 0, "priced_member_count": 0, "standard_premium": 0.0, "actual_premium": 0.0, "actual_claims": 0.0}
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
        if r.get("standard_premium") is not None:
            bucket["standard_premium"] += r["standard_premium"]
            bucket["priced_member_count"] += 1

    rows = []
    for key, bucket in buckets.items():
        standard_premium = bucket["standard_premium"]
        actual_premium = bucket["actual_premium"]
        actual_claims = bucket["actual_claims"]
        rows.append(
            {
                group_by: key,
                "member_count": bucket["member_count"],
                "priced_member_count": bucket["priced_member_count"],
                "standard_premium": round(standard_premium, 2),
                "actual_premium": round(actual_premium, 2),
                "actual_claims": round(actual_claims, 2),
                "loss_ratio_vs_standard": round(actual_claims / standard_premium, 4) if standard_premium else None,
                "loss_ratio_vs_actual": round(actual_claims / actual_premium, 4) if actual_premium else None,
                # Positive = actual premium sits above standard (charging
                # more than the rate card); negative = discounted below it.
                "actual_vs_standard_pct": round((actual_premium - standard_premium) / standard_premium * 100, 2)
                if standard_premium
                else None,
            }
        )
    rows.sort(key=lambda r: r[group_by])
    return rows
