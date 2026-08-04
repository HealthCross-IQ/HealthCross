"""Parses HealthCross's own book-wide membership export ("HealthCross
Members" - one row per member across every group/policy currently on
book, not a single case's census) for Portfolio Analysis
(app/scoring/rules/portfolio_analysis.py).

Fixed, known column layout from HealthCross's own system export - not a
broker's varying template, so (like app/ingestion/rate_cards.py) this
reads exact column names directly rather than alias-guessing.
"""
from datetime import date
from typing import BinaryIO, Dict, List, Optional

import pandas as pd

from app.ingestion.census import _calc_age, _classify_relation
from app.reference.emirate_regions import region_for_emirate
from app.reference.nationality_zones import classify_zone


def _date_or_none(value) -> Optional[date]:
    return value.date() if pd.notna(value) else None


def _str_or_none(value) -> Optional[str]:
    return str(value).strip() if pd.notna(value) else None


def _float_or_none(value) -> Optional[float]:
    return float(value) if pd.notna(value) else None


def parse_portfolio_members(file: BinaryIO, filename: str) -> List[Dict]:
    # calamine (a Rust reader) is roughly twice as fast as the default
    # openpyxl engine on this export's real size (thousands of rows across
    # 80 columns) - matters here since the whole upload request blocks
    # until parsing finishes.
    df = pd.read_excel(file, engine="calamine")

    # Parsed one column at a time (vectorized) rather than value-by-value -
    # much faster than calling pd.to_datetime() per cell in the row loop below.
    for date_col in ("DOB", "Eff Date", "Exp Date", "EndoDate (Member Start Date)", "EndoDate (Member End Date)"):
        if date_col in df.columns:
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

    rows = []
    for row in df.to_dict("records"):
        beneficiary_id = _str_or_none(row.get("BENEFICIARYID"))
        if not beneficiary_id:
            continue

        dob = _date_or_none(row.get("DOB"))
        nationality = _str_or_none(row.get("NATIONALITY"))
        rows.append(
            {
                "beneficiary_id": beneficiary_id,
                "contract": _str_or_none(row.get("CONTRACT")),
                "master_contract": _str_or_none(row.get("MASTERCONTRACT")),
                # Underwriting now adds these two directly into the export
                # itself (starting Aug 2026) instead of maintaining them as
                # separate Group->Product/Subgroup->Master mapping uploads -
                # "Master Client Name" and "PRODUCTNAME" respectively. Both
                # are still None on an older-format export with no such
                # column, in which case resolve_group_product/
                # resolve_master_client (app/scoring/rules/
                # portfolio_analysis.py) fall back to the separate mapping
                # uploads exactly as before.
                "master_client_name": _str_or_none(row.get("Master Client Name")),
                "product_name": _str_or_none(row.get("PRODUCTNAME")),
                "policy_number": _str_or_none(row.get("POLICYNUMBER")),
                "msh_policy_number": _str_or_none(row.get("MSH_POLICYNUMBER")),
                "category": _str_or_none(row.get("CATEGORY")),
                "network_type_raw": _str_or_none(row.get("NETWORKTYPE")),
                "age": _calc_age(dob) if dob else None,
                "gender": _str_or_none(row.get("GENDER")),
                "marital_status": _str_or_none(row.get("MARITALSTATUS")),
                "relation": _classify_relation(row.get("DEPENDENCY")),
                "nationality": nationality,
                "nationality_zone": classify_zone(nationality) if nationality else None,
                "residence_emirate": _str_or_none(row.get("PERSONRESIDENCEEMIRATE")),
                "region": region_for_emirate(row.get("PERSONRESIDENCEEMIRATE")),
                "policy_start_date": _date_or_none(row.get("Eff Date")),
                "policy_end_date": _date_or_none(row.get("Exp Date")),
                "member_start_date": _date_or_none(row.get("EndoDate (Member Start Date)")),
                "member_end_date": _date_or_none(row.get("EndoDate (Member End Date)")),
                "gross_premium": _float_or_none(row.get("GrossPremium")),
                "actual_gross_premium": _float_or_none(row.get("ActualGrossPremium")),
                "net_premium": _float_or_none(row.get("NETPREMIUM")),
                "actual_net_premium": _float_or_none(row.get("ACTUALNETPREMIUM")),
                "tpa_fee": _float_or_none(row.get("TPA FEE")),
                "source_filename": filename,
            }
        )
    return rows
