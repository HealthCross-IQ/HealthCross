"""HealthCross's own pricing tool "Plan Details" export.

This is the offer, already decided, in a file. Someone has sat in the
pricing tool and chosen the plan for each category - Gold on MSH
Platinum through MSH MENA, annual limit USD 1,000,000, dental USD 2,000
at 20% copay - and exported it. Re-keying all of that into the portal by
hand is not data entry, it is an opportunity to disagree with the tool
that produced it.

The layout is a stacked block per category:

    Category Emirates TPA      Network       Zone       Product  Benefit Name   Benefit Value
    A        DXB      MSH MENA MSH Platinum  Worldwide  Gold     Annual Limit   USD 1,000,000
                                                                 Deductible     NIL
                                                                 Dental Limit   USD 2,000
    (blank row)
    B        DXB      MSH MENA MSH Platinum  Worldwide  Gold     Annual Limit   USD 1,000,000
    ...

Only the first row of each block carries the category header; the rest
inherit it. That is a human convention - it reads well and would be
tedious to repeat - and it means the header has to be carried forward
rather than read per row, or every benefit after the first would be
attributed to no category at all.

The Benefit Name column uses the same vocabulary as the rate card's own
benefit variants ("Dental Limit", "OP Copay", "Pre-existing & Chronic
conditions"), so the parsed values drop straight into a quote's variant
selections with no translation layer in between. That is not a
coincidence to rely on silently, though: a value naming an option the
rate card does not price is reported rather than dropped, because a
silently ignored selection prices the base option instead and the quote
comes out looking fine.
"""
import re
from typing import Any, BinaryIO, Dict, List

import pandas as pd

#: Columns carrying the block header - present on a block's first row and
#: blank on the rest.
_HEADER_COLUMNS = ("Category", "Emirates", "TPA", "Network", "Zone", "Product")

_BENEFIT_NAME_COLUMN = "Benefit Name"
_BENEFIT_VALUE_COLUMN = "Benefit Value"


def _clean(value: Any) -> str:
    """Collapses whitespace and strips the stray tabs and non-breaking
    spaces a spreadsheet export picks up - the Network column arrives as
    "\\tMSH Platinum" often enough that matching it raw would fail against
    every rate card row.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def parse_hc_plan_details(file: BinaryIO, filename: str) -> Dict[str, Any]:
    """Every category's plan design and benefit selections from a pricing
    tool export.

    Returns one entry per category, each carrying the fields a quote needs
    (product/network/tpa) plus its benefit selections keyed by benefit
    name - the shape a New Business quote's `variant_selections` already
    takes.
    """
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file, sheet_name=0)

    missing = [c for c in (_BENEFIT_NAME_COLUMN, _BENEFIT_VALUE_COLUMN) if c not in df.columns]
    if missing:
        raise ValueError(
            f"Not a Plan Details export - missing column(s): {', '.join(missing)}. "
            f"Found: {', '.join(str(c) for c in df.columns)}"
        )

    header_columns = [c for c in _HEADER_COLUMNS if c in df.columns]
    # Carry the block header down its own rows. Forward-fill is confined
    # to the header columns: doing it across the whole frame would also
    # repeat the last benefit name into the blank separator rows and
    # invent selections nobody made.
    filled = df.copy()
    for column in header_columns:
        filled[column] = filled[column].ffill()

    categories: Dict[str, dict] = {}
    order: List[str] = []
    for _, row in filled.iterrows():
        benefit_name = _clean(row.get(_BENEFIT_NAME_COLUMN))
        benefit_value = _clean(row.get(_BENEFIT_VALUE_COLUMN))
        category = _clean(row.get("Category")) if "Category" in filled.columns else ""
        if not category or not benefit_name:
            # Separator rows, and any trailing notes below the grid.
            continue

        entry = categories.get(category)
        if entry is None:
            entry = {
                "category": category,
                "emirates": _clean(row.get("Emirates")) or None,
                "tpa": _clean(row.get("TPA")) or None,
                "network": _clean(row.get("Network")) or None,
                "zone": _clean(row.get("Zone")) or None,
                "product": _clean(row.get("Product")) or None,
                "variant_selections": {},
            }
            categories[category] = entry
            order.append(category)

        # A benefit named twice in one block is a mistake in the export,
        # not two selections - the last one wins, matching how a person
        # would read the file top to bottom.
        entry["variant_selections"][benefit_name] = benefit_value

    return {
        "source_filename": filename,
        "categories": [categories[c] for c in order],
        "category_count": len(order),
    }


def unmatched_selections(
    parsed_categories: List[dict],
    known_variant_options: Dict[str, List[str]],
) -> List[dict]:
    """Selections the rate card cannot price, so they can be reported
    rather than silently ignored.

    This matters more than it looks. An unrecognised selection does not
    fail - pricing falls back to the variant's base option - so the quote
    comes out looking perfectly reasonable while being for a different
    plan than the one exported. The failure is invisible in the number and
    only visible here.

    `known_variant_options` is {variant_name: [option_value, ...]} for the
    region/tpa/network being priced.
    """
    known = {
        _clean(name).casefold(): {_clean(o).casefold() for o in options}
        for name, options in known_variant_options.items()
    }

    issues = []
    for entry in parsed_categories:
        for name, value in (entry.get("variant_selections") or {}).items():
            key = _clean(name).casefold()
            if key not in known:
                issues.append({
                    "category": entry.get("category"),
                    "variant_name": name,
                    "option_value": value,
                    "reason": "no such benefit variant is priced for this network",
                })
            elif _clean(value).casefold() not in known[key]:
                issues.append({
                    "category": entry.get("category"),
                    "variant_name": name,
                    "option_value": value,
                    "reason": "this option is not priced for this network - the base option would be used instead",
                })
    return issues
