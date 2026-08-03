"""New Business manual/book-rate pricing - the mechanism for a brand-new
group with no (or no credible) claims history, distinct from
app/scoring/rules/renewal_rating.py and claims_projection.py which both
require an actual claims experience to project from. Builds a quoted
annual premium bottom-up from the census and the broker's chosen plan
design instead, using HealthCross's own rate card (app/models/db_models.py's
RateCard and BenefitVariantRate, ingested via app/ingestion/rate_cards.py).

Pricing unit is one broker-defined "category" (CensusRecord.category /
BenefitPlan.category - e.g. "A"/"B") - each category carries its own
Product + Network/TPA choice and its own selected option for each benefit
variant (Annual Limit, Deductible, OP Copay, Pharmacy Copay/Limit, Dental
Copay/Limit, Optical Copay/Limit, Maternity Limit, Alternative Medicine,
Pre-existing & Chronic Conditions); most cases use one category for the
whole group, but a case CAN split different categories onto different
Products.

Per-member formula:
    base = RateCard[category.product, member.region, category.network,
                     member.age_band, member.gender-or-relation]
    maternity = RateCard row's married_female_surcharge, only for a married
                female aged MATERNITY_AGE_MIN-MATERNITY_AGE_MAX in Abu Dhabi
                (the only region this rate card prices it above nil)
    variant_impact = sum over every variant priced for that
                      region/tpa/network of the chosen (or, if none chosen,
                      Base) option's impact on `base`
    net_member = base + maternity + variant_impact

Per-category: gross_total = sum(net_member) / (1 - loading_pct), where
loading_pct = commission (10% default, broker-overridable) + QIC_FEE_PCT
+ TPA_FEE_PCT + HealthCross's own fee (product-tier-dependent) - the same
division-based gross-up used everywhere else in this codebase
(renewal_rating.py, claims_projection.py), not a multiplicative markup.
Case total is the sum of every category's gross_total.
"""
from collections import defaultdict
from typing import Dict, List, Optional

from app.reference.emirate_regions import REGION_ABU_DHABI, region_for_emirate

MATERNITY_AGE_MIN, MATERNITY_AGE_MAX = 18, 50

QIC_FEE_PCT = 0.05
TPA_FEE_PCT = 0.05
DEFAULT_COMMISSION_PCT = 0.10
HEALTHCROSS_FEE_BY_PRODUCT = {
    "platinum": 0.05,
    "gold": 0.05,
    "silver": 0.065,
    "bronze": 0.065,
}
# Used only if a category's product isn't one of the 4 known tiers above -
# defaults to the higher fee so an unrecognized/mistyped product can't
# silently under-price a quote.
_DEFAULT_HEALTHCROSS_FEE_PCT = max(HEALTHCROSS_FEE_BY_PRODUCT.values())


def _find_rate_card(rate_cards: List[dict], product: str, region: str, network: str, age: Optional[int]) -> Optional[dict]:
    if age is None:
        return None
    for row in rate_cards:
        if (
            row["product"] == product
            and row["region"] == region
            and row["network"] == network
            and row["from_age"] <= age <= row["to_age"]
        ):
            return row
    return None


def _find_variant_rate(
    variant_rates: List[dict], region: str, tpa: str, network: str, variant_name: str, option_value: str
) -> Optional[dict]:
    for row in variant_rates:
        if (
            row["region"] == region
            and row["tpa"] == tpa
            and row["network"] == network
            and row["variant_name"] == variant_name
            and row["option_value"] == option_value
        ):
            return row
    return None


def _find_base_variant_rate(variant_rates: List[dict], region: str, tpa: str, network: str, variant_name: str) -> Optional[dict]:
    for row in variant_rates:
        if (
            row["region"] == region
            and row["tpa"] == tpa
            and row["network"] == network
            and row["variant_name"] == variant_name
            and row["direction"] == "Base"
        ):
            return row
    return None


def _variants_for_scope(variant_rates: List[dict], region: str, tpa: str, network: str) -> List[str]:
    return sorted(
        {
            row["variant_name"]
            for row in variant_rates
            if row["region"] == region and row["tpa"] == tpa and row["network"] == network
        }
    )


def _variant_impact(base_price: float, variant_rate: dict) -> float:
    # A live rate sheet has turned up at least one "Base" row with a stray
    # nonzero impact_value left over from a data-entry correction (see
    # app/ingestion/rate_cards.py) - Base is by definition the option
    # already priced into `base_price`, so its impact is always forced to
    # zero here regardless of whatever happens to be in impact_value.
    if variant_rate["direction"] == "Base":
        return 0.0
    sign = 1 if variant_rate["direction"] == "Upgrade" else -1
    impact_type = variant_rate["impact_type"]
    if impact_type == "Percent":
        return sign * base_price * (variant_rate["impact_value"] / 100)
    if impact_type in ("Fixed", "Currency"):
        return sign * variant_rate["impact_value"]
    return 0.0  # "Text" - not expected outside a Base row, no priced impact


def price_member(member: dict, category: dict, rate_cards: List[dict], variant_rates: List[dict]) -> dict:
    """member: {age, gender, marital_status, relation, emirates}
    category: {product, network, tpa, variant_selections: {variant_name: option_value}}
    """
    warnings: List[str] = []
    region = region_for_emirate(member.get("emirates"))
    age = member.get("age")
    gender = (member.get("gender") or "").upper()
    relation = (member.get("relation") or "").lower()
    marital_status = (member.get("marital_status") or "").lower()
    product, network, tpa = category["product"], category["network"], category["tpa"]

    rate_row = _find_rate_card(rate_cards, product, region, network, age)
    if rate_row is None:
        return {
            "base_price": None,
            "maternity_surcharge": 0.0,
            "variant_impacts": {},
            "net_total": None,
            "warnings": [f"No rate card entry for {product}/{region}/{network}, age {age}"],
        }

    if region == REGION_ABU_DHABI:
        # Abu Dhabi's own regulated scheme rates by membership role, not
        # gender - the same two rate-card columns instead carry
        # Employee/Dependant pricing here (see RateCard's docstring).
        base_price = rate_row["male_price"] if relation == "employee" else rate_row["female_price"]
    elif gender == "M":
        base_price = rate_row["male_price"]
    elif gender == "F":
        base_price = rate_row["female_price"]
    else:
        base_price = rate_row["male_price"]
        warnings.append("Missing/unrecognized gender - defaulted to male price")

    maternity_surcharge = 0.0
    if (
        region == REGION_ABU_DHABI
        and gender == "F"
        and marital_status == "married"
        and age is not None
        and MATERNITY_AGE_MIN <= age <= MATERNITY_AGE_MAX
        and rate_row.get("married_female_surcharge") is not None
    ):
        maternity_surcharge = rate_row["married_female_surcharge"]

    variant_impacts: Dict[str, float] = {}
    variant_selections = category.get("variant_selections") or {}
    for variant_name in _variants_for_scope(variant_rates, region, tpa, network):
        chosen_option = variant_selections.get(variant_name)
        variant_row = None
        if chosen_option:
            variant_row = _find_variant_rate(variant_rates, region, tpa, network, variant_name, chosen_option)
            if variant_row is None:
                warnings.append(
                    f"No rate found for {variant_name} option '{chosen_option}' ({region}/{tpa}/{network}) - not applied"
                )
        if variant_row is None:
            variant_row = _find_base_variant_rate(variant_rates, region, tpa, network, variant_name)
            if variant_row is None:
                warnings.append(
                    f"No base option defined for {variant_name} ({region}/{tpa}/{network}) - broker must choose explicitly"
                )
                continue
        variant_impacts[variant_name] = round(_variant_impact(base_price, variant_row), 2)

    net_total = base_price + maternity_surcharge + sum(variant_impacts.values())

    return {
        "base_price": round(base_price, 2),
        "maternity_surcharge": round(maternity_surcharge, 2),
        "variant_impacts": variant_impacts,
        "net_total": round(net_total, 2),
        "warnings": warnings,
    }


def category_loading_pct(product: str, commission_pct: Optional[float] = None) -> float:
    commission = DEFAULT_COMMISSION_PCT if commission_pct is None else commission_pct
    healthcross_fee = HEALTHCROSS_FEE_BY_PRODUCT.get((product or "").strip().lower(), _DEFAULT_HEALTHCROSS_FEE_PCT)
    return commission + QIC_FEE_PCT + TPA_FEE_PCT + healthcross_fee


def gross_up(net_total: float, loading_pct: float) -> float:
    if loading_pct >= 1:
        raise ValueError("loading_pct must be less than 1")
    return net_total / (1 - loading_pct)


def price_case(census: List[dict], categories: List[dict], rate_cards: List[dict], variant_rates: List[dict]) -> dict:
    """categories: [{category, product, network, tpa, commission_pct?, variant_selections}]
    Members are matched to a category by their own `category` field
    (CensusRecord.category) - a member whose category doesn't match any of
    the categories passed in is skipped and counted separately, rather than
    guessed at or silently priced under the wrong plan design.
    """
    categories_by_name = {c["category"]: c for c in categories}

    per_category_net: Dict[str, float] = defaultdict(float)
    per_category_members: Dict[str, List[dict]] = defaultdict(list)
    per_category_warnings: Dict[str, List[str]] = defaultdict(list)
    uncategorized_count = 0

    for member in census:
        category = categories_by_name.get(member.get("category"))
        if category is None:
            uncategorized_count += 1
            continue
        result = price_member(member, category, rate_cards, variant_rates)
        cat_name = category["category"]
        per_category_members[cat_name].append(result)
        if result["net_total"] is not None:
            per_category_net[cat_name] += result["net_total"]
        per_category_warnings[cat_name].extend(result["warnings"])

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
                "tpa": category["tpa"],
                "member_count": len(per_category_members.get(cat_name, [])),
                "net_annual_premium": round(net_total, 2),
                "loading_pct": round(loading_pct, 4),
                "gross_annual_premium": round(gross_total, 2),
                "member_breakdown": per_category_members.get(cat_name, []),
                "warnings": sorted(set(per_category_warnings.get(cat_name, []))),
            }
        )

    return {
        "categories": category_breakdown,
        "case_gross_annual_premium": round(case_gross_total, 2),
        "priced_member_count": len(census) - uncategorized_count,
        "uncategorized_member_count": uncategorized_count,
    }


# Thresholds for how far a broker's target premium can sit above/below the
# actuarially rated price before calling it something other than "Marginal" -
# i.e. within 5% either side is close enough to call a wash.
GOOD_OPPORTUNITY_MARGIN_PCT = 5.0
POOR_OPPORTUNITY_MARGIN_PCT = -5.0

DECLINE_RISK_TIER = "Decline/Refer"


def assess_opportunity(rated_premium: float, target_premium: Optional[float], risk_tier: Optional[str] = None) -> dict:
    """Combines this rate card's actuarially-grounded price with the
    existing risk scorecard (app/scoring/engine.py's risk_tier, if one has
    been computed for the case) and the broker's own target premium into
    one "is this worth writing" verdict - the whole point of building a
    real rate card rather than just a number: knowing whether a target
    premium is generous, thin, or outright underpriced relative to what the
    group actually costs to cover.

    A Decline/Refer risk tier overrides price entirely - a demographically
    bad group isn't a "Good" opportunity just because the broker's target is
    generous relative to its rated cost.
    """
    if risk_tier == DECLINE_RISK_TIER:
        return {
            "verdict": "Poor",
            "reason": f"Risk tier is {DECLINE_RISK_TIER} regardless of price.",
            "rated_premium": round(rated_premium, 2),
            "target_premium": round(target_premium, 2) if target_premium is not None else None,
            "risk_tier": risk_tier,
        }

    if not target_premium or rated_premium <= 0:
        return {
            "verdict": "Unknown",
            "reason": "No target premium to compare the rated price against.",
            "rated_premium": round(rated_premium, 2),
            "target_premium": round(target_premium, 2) if target_premium is not None else None,
            "risk_tier": risk_tier,
        }

    # Positive = broker's target sits above the rated cost (room for
    # margin/commission); negative = the target undercuts what the group
    # actually costs to cover.
    variance_pct = round((target_premium - rated_premium) / rated_premium * 100, 2)
    if variance_pct >= GOOD_OPPORTUNITY_MARGIN_PCT:
        verdict = "Good"
    elif variance_pct >= POOR_OPPORTUNITY_MARGIN_PCT:
        verdict = "Marginal"
    else:
        verdict = "Poor"

    return {
        "verdict": verdict,
        "rated_premium": round(rated_premium, 2),
        "target_premium": round(target_premium, 2),
        "target_vs_rated_variance_pct": variance_pct,
        "risk_tier": risk_tier,
    }
