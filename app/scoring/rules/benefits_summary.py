"""Standard table-of-benefits summary format.

Fixed layout requested for every plan review, regardless of insurer or
plan tier: Network, Area of Cover, Annual Limit, Deductible, Pre-existing
& Chronic Limit, Maternity Limit, Maternity Co-insurance, Dental, Optical,
Coinsurance, Alternative/Complementary Treatment, Pharmacy Limit &
Coinsurance, Health Screening/Wellness Package.

Network leads the list because it frames everything under it: the same
annual limit on a restricted network and on a comprehensive one are two
different offers, and comparing an incumbent's benefits against a
proposal without it compares limits while ignoring where they can be
spent.
"""
from typing import Any, Dict

STANDARD_FIELDS = [
    "network",
    "area_of_cover",
    "annual_limit",
    "deductible",
    "pre_existing_chronic_limit",
    "maternity_limit",
    "maternity_coinsurance",
    "dental",
    "optical",
    "coinsurance",
    "alternative_or_complementary_treatment",
    "pharmacy_limit_and_coinsurance",
    "health_screening_wellness",
]

FIELD_LABELS = {
    "network": "Network",
    "area_of_cover": "Area of Cover",
    "annual_limit": "Annual Limit",
    "deductible": "Deductible",
    "pre_existing_chronic_limit": "Pre-existing & Chronic Limit",
    "maternity_limit": "Maternity Limit",
    "maternity_coinsurance": "Maternity Co-insurance",
    "dental": "Dental",
    "optical": "Optical",
    "coinsurance": "Coinsurance",
    "alternative_or_complementary_treatment": "Alternative/Complementary Treatment",
    "pharmacy_limit_and_coinsurance": "Pharmacy Limit & Coinsurance",
    "health_screening_wellness": "Health Screening/Wellness Package",
}

NOT_SPECIFIED = "Not specified in source document"


def build_standard_benefit_summary(plan_details: Dict[str, Any], not_specified_text: str = NOT_SPECIFIED) -> Dict[str, str]:
    """Return the fixed standard summary, defaulting any missing field.

    plan_details may come from any insurer's table of benefits, in whatever
    shape that insurer's parser produces - callers pass in whatever they've
    extracted under these exact keys. Anything absent is marked explicitly
    rather than silently omitted, so a gap in the source document stays
    visible instead of just disappearing from the summary. not_specified_text
    lets a caller use a different default for a field OCR (a lower-confidence
    extraction than real document text) simply never found a value for - see
    app/api/routes_analysis.py's _benefit_summary.
    """
    return {field: plan_details.get(field) or not_specified_text for field in STANDARD_FIELDS}


def format_benefit_summary_markdown(plan_name: str, plan_details: Dict[str, Any]) -> str:
    summary = build_standard_benefit_summary(plan_details)
    lines = [f"### {plan_name}", "", "| Field | Value |", "|---|---|"]
    for field in STANDARD_FIELDS:
        lines.append(f"| {FIELD_LABELS[field]} | {summary[field]} |")
    return "\n".join(lines)
