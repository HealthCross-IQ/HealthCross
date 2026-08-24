"""What HealthCross is proposing, written the way a table of benefits is.

The offer already exists as variant selections - "Dental Limit: USD
2,000", "Dental Copay: 20%" - but that is the pricing engine's vocabulary,
not an underwriter's. A table of benefits has one Dental line reading
"USD 2,000 Co-pay: 20%", and the only way to put the proposal next to the
client's existing cover is to say it the same way.

Two things make this more than a rename:

Limit and copay are separate variants and one benefit line. Dental,
Optical and Pharmacy each price as two dropdowns and read as one row, so
they have to be recombined - and in the same phrasing the incumbent's
document uses, or the comparison is between a sentence and a pair of
fields.

"Base (included)" is a value, not a blank. A variant left unselected is
not "no cover" - it is whatever the base option carries, which is often
substantial. Rendering an unselected variant as empty would show the
proposal as offering nothing where it actually offers the base level, and
would read as a reduction against any incumbent who covers it at all.
That is the failure mode this module exists to avoid.

Pure functions over plain dicts - no ORM, no database.
"""
from typing import Dict, List, Optional

#: Which variants make up each standard benefit line, in the order they
#: are written. A field with no variants has no dropdown behind it - the
#: rate card simply does not price it separately - and is left absent
#: rather than rendered as blank, which would read as "not offered".
FIELD_VARIANTS: Dict[str, Dict[str, Optional[str]]] = {
    "annual_limit": {"limit": "Annual Limit", "copay": None},
    "deductible": {"limit": "Deductible", "copay": None},
    "pre_existing_chronic_limit": {"limit": "Pre-existing & Chronic conditions", "copay": None},
    "maternity_limit": {"limit": "Maternity Limit", "copay": None},
    "dental": {"limit": "Dental Limit", "copay": "Dental Copay"},
    "optical": {"limit": "Optical Limit", "copay": "Optical Copay"},
    "pharmacy_limit_and_coinsurance": {"limit": "Pharmacy Limit", "copay": "Pharmacy Copay"},
    "coinsurance": {"limit": None, "copay": "OP Copay"},
    "alternative_or_complementary_treatment": {"limit": "Alternative Medicine", "copay": None},
}

#: Benefit lines the rate card does not price as a variant at all. Listed
#: explicitly so they can be shown as "not priced separately" rather than
#: silently missing - the reader should be able to tell the difference
#: between a benefit with no proposal and one this module simply cannot
#: speak about.
FIELDS_WITHOUT_VARIANTS = (
    # Network is priced, but as a dimension of the rate card rather than
    # as a dropdown on a benefit line - it comes from the case's own
    # Product/Network selection, so there is no variant to read here.
    "network",
    "area_of_cover",
    "maternity_coinsurance",
    "health_screening_wellness",
)


def _normalize(name: Optional[str]) -> str:
    return " ".join(str(name or "").split()).casefold()


def base_option_by_variant(variant_rates: List[dict]) -> Dict[str, str]:
    """Each variant's base option - what a member gets when nobody touches
    the dropdown. Keyed by normalized variant name so a rate file spelling
    a variant slightly differently still resolves.
    """
    return {
        _normalize(r.get("variant_name")): r.get("option_value")
        for r in variant_rates
        if str(r.get("direction") or "").strip().casefold() == "base" and r.get("option_value")
    }


def resolve_variant_value(
    variant_name: Optional[str],
    variant_selections: Dict[str, str],
    base_by_variant: Dict[str, str],
) -> Optional[str]:
    """The value actually being proposed for one variant.

    An unselected variant resolves to its BASE option, not to nothing -
    the member is covered at the base level whether or not anyone opened
    the dropdown. Returning None here would misrepresent the proposal as
    offering no cover at all.
    """
    if not variant_name:
        return None
    chosen = variant_selections.get(variant_name)
    if chosen:
        return chosen
    return base_by_variant.get(_normalize(variant_name))


def proposed_benefit_summary(
    variant_selections: Optional[Dict[str, str]],
    variant_rates: Optional[List[dict]] = None,
) -> Dict[str, str]:
    """The proposal in the same 12-field shape a parsed table of benefits
    uses (see benefits_summary's STANDARD_FIELDS), so the two can be put
    side by side field for field.

    Limit and copay are joined the way the incumbent's own document writes
    them - "USD 2,000 Co-pay: 20%" - rather than as two columns, because a
    comparison only reads if both sides are phrased alike.
    """
    variant_selections = variant_selections or {}
    base_by_variant = base_option_by_variant(variant_rates or [])

    summary: Dict[str, str] = {}
    for field, parts in FIELD_VARIANTS.items():
        limit = resolve_variant_value(parts["limit"], variant_selections, base_by_variant)
        copay = resolve_variant_value(parts["copay"], variant_selections, base_by_variant)

        if limit and copay:
            summary[field] = f"{limit} Co-pay: {copay}"
        elif limit:
            summary[field] = limit
        elif copay:
            # A copay-only line (OP Copay) reads as the copay itself - it
            # is the whole benefit, not a qualifier on a missing limit.
            summary[field] = copay if parts["limit"] is None else f"Co-pay: {copay}"

    return summary


def proposed_benefit_rows(
    existing_summary: Optional[Dict[str, str]],
    variant_selections: Optional[Dict[str, str]],
    variant_rates: Optional[List[dict]] = None,
    field_labels: Optional[Dict[str, str]] = None,
    standard_fields: Optional[List[str]] = None,
    proposed_overrides: Optional[Dict[str, str]] = None,
) -> List[dict]:
    """One row per benefit line: what the client has, what HealthCross is
    proposing, and how they compare.

    Rows are returned for every standard field even where one side is
    missing, so the table is the same shape every time and a benefit the
    incumbent's document did not mention is visibly absent rather than
    quietly dropped - "the TOB says nothing about dental" is itself worth
    seeing, and is the case where a proposal is most likely to be adding
    cover nobody has priced for.

    proposed_overrides fills the fields the proposal knows without a
    dropdown behind them - Network above all, which is a dimension of the
    rate card rather than a benefit variant. It is decided, it is priced,
    and leaving it blank would show the one field that frames every limit
    under it as something HealthCross had not proposed.
    """
    from app.scoring.rules.benefits_comparison import compare_benefit_value
    from app.scoring.rules.benefits_summary import FIELD_LABELS, STANDARD_FIELDS

    labels = field_labels or FIELD_LABELS
    fields = standard_fields or STANDARD_FIELDS
    existing_summary = existing_summary or {}
    proposed = proposed_benefit_summary(variant_selections, variant_rates)
    for field, value in (proposed_overrides or {}).items():
        if value:
            proposed[field] = value

    rows = []
    for field in fields:
        existing_value = existing_summary.get(field)
        proposed_value = proposed.get(field)
        comparison = compare_benefit_value(existing_value, proposed_value)

        rows.append({
            "field": field,
            "label": labels.get(field, field.replace("_", " ").title()),
            "existing": existing_value,
            "proposed": proposed_value,
            "direction": comparison.get("direction"),
            "note": comparison.get("note"),
            "priced_as_variant": field not in FIELDS_WITHOUT_VARIANTS,
        })
    return rows
