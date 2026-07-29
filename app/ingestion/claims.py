from typing import Any, BinaryIO, Dict, List

import pandas as pd

from app.ingestion.column_mapping import map_columns

CLAIMS_ALIASES: Dict[str, List[str]] = {
    "member_ref": ["member id", "employee id", "staff no"],
    "claim_date": ["claim date", "date of service", "incurred date"],
    "service_type": ["service type", "claim type", "category"],
    "diagnosis_category": ["diagnosis", "diagnosis category", "condition"],
    "amount_billed": ["billed amount", "amount billed", "gross amount"],
    "amount_paid": ["paid amount", "amount paid", "net amount", "approved amount"],
    "policy_year": ["policy year", "year"],
}


def _safe_float(val: Any):
    try:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        return float(str(val).replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return None


def parse_claims(file: BinaryIO, filename: str) -> List[dict]:
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file)

    df = map_columns(df, CLAIMS_ALIASES)

    records = []
    for _, row in df.iterrows():
        claim_date = None
        if "claim_date" in df.columns:
            cd = pd.to_datetime(row.get("claim_date"), errors="coerce")
            if pd.notna(cd):
                claim_date = cd.date()

        paid = _safe_float(row.get("amount_paid"))
        billed = _safe_float(row.get("amount_billed"))
        if paid is None:
            paid = billed

        policy_year = row.get("policy_year")
        if pd.notna(policy_year):
            policy_year = int(policy_year)
        else:
            policy_year = claim_date.year if claim_date else None

        records.append(
            {
                "member_ref": str(row.get("member_ref")) if pd.notna(row.get("member_ref")) else None,
                "claim_date": claim_date,
                "service_type": row.get("service_type") if pd.notna(row.get("service_type")) else "outpatient",
                "diagnosis_category": row.get("diagnosis_category") if pd.notna(row.get("diagnosis_category")) else None,
                "amount_billed": billed,
                "amount_paid": paid,
                "policy_year": policy_year,
            }
        )
    return records
