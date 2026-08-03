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
    parsed = pd.to_datetime(value, errors="coerce")
    return parsed.date() if pd.notna(parsed) else None


def _str_or_none(value) -> Optional[str]:
    return str(value).strip() if pd.notna(value) else None


def _float_or_none(value) -> Optional[float]:
    return float(value) if pd.notna(value) else None


def parse_portfolio_members(file: BinaryIO, filename: str) -> List[Dict]:
    df = pd.read_excel(file)

    rows = []
    for _, row in df.iterrows():
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
