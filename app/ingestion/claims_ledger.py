"""Parser for a raw per-claim-line claims ledger export (e.g. the
"ServicePlan" format seen from QIC/HealthCross's TPA: PATIENT_ID, CLAIM_ID,
DATE_OF_TREATMENT, DIAGNOSIS_CODE, "Final Amount in AED", etc.).

Only relevant for existing-business renewals - a fresh quote has no claims
ledger of its own to upload. Distinct from both the generic
app/ingestion/claims.py spreadsheet parser and the pre-aggregated DHA-style
app/ingestion/claims_report.py: this is raw, line-level detail, letting
top-patient/top-diagnosis breakdowns and the renewal-increase calculation
(app/scoring/rules/renewal_rating.py) be computed directly from the
group's own real experience rather than a third party's report.
"""
from typing import Any, BinaryIO, Dict, List

import pandas as pd

from app.ingestion.column_mapping import map_columns

CLAIMS_LEDGER_ALIASES: Dict[str, List[str]] = {
    "patient_id": ["patient_id", "patient id"],
    "claim_id": ["claim_id", "claim id"],
    "claim_status": ["claim status", "claim_status", "status"],
    "policy_start_date": ["policy_start_date", "policy start date"],
    "policy_end_date": ["policy_end_date", "policy end date"],
    # The scheme's own policy_start/end_date above is fixed for every row -
    # these are the individual MEMBER's own enrollment dates, which can
    # fall short of the scheme's if they joined late or left early.
    "member_start_date": ["member_start_date", "member start date"],
    "member_end_date": ["member_end_date", "member end date"],
    "date_of_treatment": ["date_of_treatment", "date of treatment", "treatment date"],
    "relation": ["relation", "relationship"],
    "ip_op_maternity": ["ip_op_maternity", "ip/op/maternity", "claim type"],
    "medical_category": ["medical_category", "medical category"],
    # The specific treatment performed (e.g. "Physical Therapist",
    # "Osteopath", "Ayuverdic"). Finer than medical_category, and the ONLY
    # field that distinguishes true physiotherapy from alternative therapy
    # - both of which land in the same "PARAMEDICAL" category.
    "medical_act": ["medical_act", "medical act"],
    "provider_name": ["provider_name", "provider name", "provider", "hospital/clinic", "hospital name", "facility name"],
    "diagnosis_code": ["diagnosis_code", "diagnosis code", "icd code", "icd10"],
    "diagnosis_description": ["diagnosis_description", "diagnosis description", "diagnosis_short_description", "diagnosis short description"],
    # "... Contract" is a newer HealthCross book-wide export's own wording
    # for the same AED figure (its own CONTRACT_CURRENCY column confirms
    # AED throughout - this isn't a different currency, just a renamed
    # header) - without this alias, claimed_amount/final_amount silently
    # came back None for every row instead of erroring.
    "claimed_amount": ["claimed amount aed", "claimed_amount_aed", "claimed amount", "claimed amount contract"],
    "final_amount": ["final amount in aed", "final_amount_in_aed", "final amount", "amount paid", "final amount contract"],
}


def parse_claims_ledger(file: BinaryIO, filename: str) -> List[dict]:
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file)

    df = map_columns(df, CLAIMS_LEDGER_ALIASES)

    def _date_or_none(value: Any):
        parsed_date = pd.to_datetime(value, errors="coerce")
        return parsed_date.date() if pd.notna(parsed_date) else None

    def _str_or_none(value: Any) -> Any:
        return str(value).strip() if pd.notna(value) else None

    def _float_or_none(value: Any) -> Any:
        return float(value) if pd.notna(value) else None

    records = []
    for _, row in df.iterrows():
        records.append(
            {
                "patient_id": _str_or_none(row.get("patient_id")),
                "claim_id": _str_or_none(row.get("claim_id")),
                "claim_status": _str_or_none(row.get("claim_status")),
                "policy_start_date": _date_or_none(row.get("policy_start_date")),
                "policy_end_date": _date_or_none(row.get("policy_end_date")),
                "member_start_date": _date_or_none(row.get("member_start_date")),
                "member_end_date": _date_or_none(row.get("member_end_date")),
                "date_of_treatment": _date_or_none(row.get("date_of_treatment")),
                "relation": _str_or_none(row.get("relation")),
                "ip_op_maternity": _str_or_none(row.get("ip_op_maternity")),
                "medical_category": _str_or_none(row.get("medical_category")),
                "medical_act": _str_or_none(row.get("medical_act")),
                "provider_name": _str_or_none(row.get("provider_name")),
                "diagnosis_code": _str_or_none(row.get("diagnosis_code")),
                "diagnosis_description": _str_or_none(row.get("diagnosis_description")),
                "claimed_amount": _float_or_none(row.get("claimed_amount")),
                "final_amount": _float_or_none(row.get("final_amount")),
            }
        )
    return records
