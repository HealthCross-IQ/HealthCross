"""Parses HealthCross's own two internal New Business rate-card
spreadsheets (fixed, known column layouts - not a broker template with
varying wording, so no column-alias guessing is needed here the way
app/ingestion/census.py's column_mapping is):

- ProductPricingList: the base Product x Region x Network x age-band rate
  (app/models/db_models.py's RateCard).
- BenefitVariantOptionList: the priced Upgrade/Downgrade options for each
  benefit variant, scoped by Region x TPA x Network
  (app/models/db_models.py's BenefitVariantRate).

See app/scoring/rules/new_business_rating.py for how these two tables
combine into an actual quote.
"""
import re
from collections import defaultdict
from typing import BinaryIO, Dict, List, Optional, Tuple

import pandas as pd

_MARRIED_FEMALE_AMOUNT_RE = re.compile(r"^\s*([\d,]+(?:\.\d+)?)")


def _parse_married_female_surcharge(raw: object) -> Optional[float]:
    """"Not Applicable" (outside the maternity age band) -> None. A leading
    number, e.g. "950 (Applicable only for age band 18-50)" or a bare
    "0" -> that number, still a real (if nil) surcharge within the band.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower().startswith("not applicable"):
        return None
    match = _MARRIED_FEMALE_AMOUNT_RE.match(text)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _most_recent_timestamp(row) -> pd.Timestamp:
    updated = pd.to_datetime(row.get("Updated Date"), errors="coerce")
    if pd.notna(updated):
        return updated
    return pd.to_datetime(row.get("Created Date"), errors="coerce")


def _drop_stale_duplicates(parsed: List[Tuple[Dict, pd.Timestamp]], key_fields: List[str]) -> List[Dict]:
    """A live admin-managed rate sheet occasionally ends up with more than
    one row for what should be the same unique combination - e.g. a
    correction that added a new row instead of editing the old one in
    place. Keeps only the most recently created/updated row per
    `key_fields` combination, so downstream consumers (the rating engine,
    the broker's option dropdowns) never see two conflicting answers for
    the same lookup.
    """
    groups = defaultdict(list)
    for data, timestamp in parsed:
        key = tuple(data[field] for field in key_fields)
        groups[key].append((data, timestamp))

    stale_ids = set()
    for group in groups.values():
        if len(group) > 1:
            group.sort(key=lambda item: item[1] if pd.notna(item[1]) else pd.Timestamp.min)
            stale_ids.update(id(data) for data, _ in group[:-1])

    return [data for data, _ in parsed if id(data) not in stale_ids]


def parse_product_pricing_list(file: BinaryIO, filename: str) -> List[Dict]:
    df = pd.read_excel(file, sheet_name=0)
    parsed = []
    for _, row in df.iterrows():
        if pd.isna(row.get("Product Name")):
            continue
        parsed.append(
            (
                {
                    "product": str(row["Product Name"]).strip(),
                    "region": str(row["Region"]).strip(),
                    "network": str(row["Network"]).strip(),
                    "tpa": str(row["TPA"]).strip(),
                    "from_age": int(row["From Age"]),
                    "to_age": int(row["To Age"]),
                    "male_price": float(row["Male Price"]),
                    "female_price": float(row["Female Price"]),
                    "married_female_surcharge": _parse_married_female_surcharge(row.get("Married Female Price")),
                    "zone": str(row["Zone"]).strip() if pd.notna(row.get("Zone")) else None,
                    "source_filename": filename,
                },
                _most_recent_timestamp(row),
            )
        )
    return _drop_stale_duplicates(parsed, ["product", "region", "network", "tpa", "from_age", "to_age"])


def parse_benefit_variant_option_list(file: BinaryIO, filename: str) -> List[Dict]:
    df = pd.read_excel(file, sheet_name=0)
    parsed = []
    for _, row in df.iterrows():
        if pd.isna(row.get("Variant Name")):
            continue
        parsed.append(
            (
                {
                    "variant_name": str(row["Variant Name"]).strip(),
                    "option_value": str(row["Option Value"]).strip(),
                    "direction": str(row["Direction"]).strip(),
                    "impact_type": str(row["Impact Type"]).strip(),
                    "impact_value": float(row["Impact Value"]),
                    "is_default": str(row.get("Is Default")).strip().lower() == "yes",
                    "region": str(row["Region"]).strip(),
                    "tpa": str(row["TPA"]).strip(),
                    "network": str(row["Network"]).strip(),
                    "zone": str(row["Zone"]).strip() if pd.notna(row.get("Zone")) else None,
                    "source_filename": filename,
                },
                _most_recent_timestamp(row),
            )
        )
    # Only "Base" rows can conflict on region/tpa/network/variant alone (an
    # Upgrade/Downgrade row is also keyed by its own distinct option_value,
    # and no two rows in practice share all of region/tpa/network/variant/
    # option_value) - so de-duplicate Base rows on the group key, and
    # everything else on the full option-value key.
    base_rows = [(d, t) for d, t in parsed if d["direction"] == "Base"]
    other_rows = [(d, t) for d, t in parsed if d["direction"] != "Base"]
    deduped_base = _drop_stale_duplicates(base_rows, ["region", "tpa", "network", "variant_name"])
    deduped_other = _drop_stale_duplicates(other_rows, ["region", "tpa", "network", "variant_name", "option_value"])
    return deduped_base + deduped_other
