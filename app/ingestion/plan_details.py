import re
from typing import Any, BinaryIO, Dict

import pandas as pd

# Labels as they appear in the broker's "CLIENT & PLAN details" sheet, column
# A, with the value in column B. Sheet is read positionally (label/value
# pairs), not via header matching, since it's a key-value block rather than a
# table.
_LABEL_FIELDS = {
    "broker": "broker_name",
    "existing insurer": "existing_insurer",
    "no of years with existing insurer": "years_with_existing_insurer",
    "years with existing insurer": "years_with_existing_insurer",
    "target premium": "target_premium",
    "claims available": "claims_available",
    "location": "region",
    "industry": "industry",
    "renewal date": "renewal_date",
}


def _normalize_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", str(value).strip().lower()).strip()


def _to_bool_or_none(value: Any):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().lower()
    if text in {"y", "yes", "true", "available"}:
        return True
    if text in {"n", "no", "false", "not available"}:
        return False
    return None


def parse_plan_details(file: BinaryIO, filename: str, sheet_name: str = "CLIENT & PLAN details") -> Dict[str, Any]:
    """Extract the mandatory-details key/value block from the plan sheet.

    Returns only the fields it could confidently identify; callers should
    merge non-None values onto the existing Case rather than overwrite blindly.
    """
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(file, header=None)
    else:
        df = pd.read_excel(file, sheet_name=sheet_name, header=None)

    extracted: Dict[str, Any] = {}
    for _, row in df.iterrows():
        label = _normalize_label(row.get(0))
        if label not in _LABEL_FIELDS:
            continue
        field = _LABEL_FIELDS[label]
        raw_value = row.get(1)
        if raw_value is None or (isinstance(raw_value, float) and pd.isna(raw_value)):
            continue

        if field == "years_with_existing_insurer":
            try:
                extracted[field] = int(float(raw_value))
            except (ValueError, TypeError):
                continue
        elif field == "target_premium":
            try:
                extracted[field] = float(str(raw_value).replace(",", "").replace("USD", "").replace("AED", "").strip())
            except (ValueError, TypeError):
                continue
        elif field == "claims_available":
            parsed_bool = _to_bool_or_none(raw_value)
            if parsed_bool is not None:
                extracted[field] = parsed_bool
        elif field == "renewal_date":
            parsed_date = pd.to_datetime(raw_value, errors="coerce")
            if pd.notna(parsed_date):
                extracted[field] = parsed_date.date()
        else:
            extracted[field] = str(raw_value).strip()

    return extracted
