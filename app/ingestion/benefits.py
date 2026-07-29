from typing import Any, BinaryIO, Dict, List

import pandas as pd

from app.ingestion.column_mapping import map_columns

BENEFIT_ALIASES: Dict[str, List[str]] = {
    "plan_name": ["plan", "plan name", "tier", "category"],
    "annual_limit": ["annual limit", "aal", "annual maximum", "overall limit"],
    "room_type": ["room type", "room board", "room and board", "accommodation"],
    "deductible": ["deductible", "excess"],
    "co_insurance_pct": ["co insurance", "coinsurance", "co insurance pct", "coinsurance pct"],
    "network_type": ["network", "geographic scope", "area of cover"],
    "maternity_covered": ["maternity", "maternity cover"],
    "maternity_limit": ["maternity limit"],
    "dental_covered": ["dental", "dental cover"],
    "optical_covered": ["optical", "vision"],
    "chronic_covered": ["chronic", "chronic conditions"],
    "pre_existing_covered": ["pre existing", "pre existing conditions", "ped", "pre existing illness"],
    "member_count": ["members", "no of members", "lives", "eligible employees"],
}

_TRUTHY = {"y", "yes", "covered", "included", "true", "1", "fully covered"}


def _to_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    return str(val).strip().lower() in _TRUTHY


def _safe_float(val: Any):
    try:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        return float(str(val).replace(",", "").replace("$", "").replace("USD", "").replace("AED", "").strip())
    except (ValueError, TypeError):
        return None


def _normalize_room(val: Any) -> str:
    if not val or (isinstance(val, float) and pd.isna(val)):
        return "ward"
    value = str(val).strip().lower()
    if "private" in value:
        return "private"
    if "semi" in value:
        return "semi_private"
    return "ward"


def _normalize_network(val: Any) -> str:
    if not val or (isinstance(val, float) and pd.isna(val)):
        return "in_country"
    value = str(val).strip().lower()
    if "world" in value or "global" in value:
        return "worldwide"
    if "region" in value:
        return "regional"
    return "in_country"


def parse_table_of_benefits(file: BinaryIO, filename: str) -> List[dict]:
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file)

    df = map_columns(df, BENEFIT_ALIASES)

    plans = []
    for _, row in df.iterrows():
        plans.append(
            {
                "plan_name": row.get("plan_name") if pd.notna(row.get("plan_name")) else "Base Plan",
                "annual_limit": _safe_float(row.get("annual_limit")),
                "room_type": _normalize_room(row.get("room_type")),
                "deductible": _safe_float(row.get("deductible")) or 0.0,
                "co_insurance_pct": _safe_float(row.get("co_insurance_pct")) or 0.0,
                "network_type": _normalize_network(row.get("network_type")),
                "maternity_covered": _to_bool(row.get("maternity_covered")),
                "maternity_limit": _safe_float(row.get("maternity_limit")),
                "dental_covered": _to_bool(row.get("dental_covered")),
                "optical_covered": _to_bool(row.get("optical_covered")),
                "chronic_covered": _to_bool(row.get("chronic_covered")) if pd.notna(row.get("chronic_covered")) else True,
                "pre_existing_covered": _to_bool(row.get("pre_existing_covered")),
                "member_count": int(row.get("member_count")) if pd.notna(row.get("member_count")) else None,
            }
        )
    return plans
