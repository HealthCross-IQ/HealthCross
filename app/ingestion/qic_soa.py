"""Parser for a QIC Statement of Account (SOA) export - the ground truth
HealthCross reconciles its own Payment Tracker against (see
app.finance.reconciliation).

QIC's own export shape has varied across two real samples seen so far:
one carries separate "Debit LC"/"Credit LC" amount columns, the other a
single signed "AMOUNT" column plus a "Dr/Cr" flag. Both are normalized here
onto the same debit_amount/credit_amount pair so every downstream query
only deals with one shape, regardless of which export produced a given
QicSoaLine row.
"""
from typing import Any, BinaryIO, Dict, List, Optional

import pandas as pd

from app.finance.common import normalize_doc_no
from app.ingestion.column_mapping import map_columns

QIC_SOA_ALIASES: Dict[str, List[str]] = {
    "doc_no": ["doc no"],
    "tran_code": ["tran code"],
    "doc_date": ["doc dt", "doc date"],
    "tran_type": ["tran type"],
    "doc_due_date": ["doc due date"],
    "lob_code": ["lob code"],
    "policy_no": ["policy no"],
    "insured_name": ["insured name"],
    "insured_code": ["insured code"],
    "currency": ["currency"],
    "doc_desc": ["doc desc"],
    "debit_lc": ["debit lc"],
    "credit_lc": ["credit lc"],
    "amount": ["amount"],
    "sequence_no": ["sequence no"],
    "dr_cr": ["dr/cr", "dr cr"],
    "policy_from_date": ["policy from date"],
    "policy_to_date": ["policy to date"],
    "cust_code": ["cust code"],
    "cust_name": ["cust name"],
    "endorsement_no": ["endorsement no"],
    "pol_comms_doc": ["pol/comms doc", "pol comms doc"],
    "branch": ["branch"],
    "gross_amount": ["gross amount"],
    "age_band": ["age band"],
    "prod_code": ["prod code"],
    "cust_group_code": ["cust group code"],
    "cust_group_name": ["cust group name"],
    "broker_name": ["broker name"],
    "control_account": ["control account"],
    "cal_year": ["cal year"],
    "doc_created_by": ["doc created by"],
    "installment_number": ["installment number"],
    "endorsement_type": ["endorsement type"],
}


def _str_or_none(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip() or None


def _float_or_none(value: Any) -> Any:
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_or_none(value: Any):
    if pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    return parsed.date() if pd.notna(parsed) else None


def _amounts(row: pd.Series) -> Optional[Dict[str, float]]:
    """Normalizes whichever amount shape this export uses onto
    (debit_amount, credit_amount), both >= 0.
    """
    has_split_columns = "debit_lc" in row.index or "credit_lc" in row.index
    if has_split_columns:
        debit = _float_or_none(row.get("debit_lc")) or 0.0
        credit = _float_or_none(row.get("credit_lc")) or 0.0
        return {"debit_amount": debit, "credit_amount": credit}

    amount = _float_or_none(row.get("amount"))
    if amount is None:
        return {"debit_amount": 0.0, "credit_amount": 0.0}
    dr_cr = _str_or_none(row.get("dr_cr"))
    magnitude = abs(amount)
    is_credit = (dr_cr and dr_cr.upper().startswith("C")) or (not dr_cr and amount < 0)
    if is_credit:
        return {"debit_amount": 0.0, "credit_amount": magnitude}
    return {"debit_amount": magnitude, "credit_amount": 0.0}


def parse_qic_soa(file: BinaryIO, filename: str, sheet_name: Any = 0) -> List[dict]:
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file, sheet_name=sheet_name)

    df = map_columns(df, QIC_SOA_ALIASES)

    records = []
    for _, row in df.iterrows():
        doc_no_raw = row.get("doc_no")
        if pd.isna(doc_no_raw):
            continue

        dr_cr = _str_or_none(row.get("dr_cr"))
        records.append(
            {
                "doc_no": normalize_doc_no(doc_no_raw),
                "doc_no_raw": _str_or_none(doc_no_raw),
                "tran_code": _str_or_none(row.get("tran_code")),
                "doc_date": _date_or_none(row.get("doc_date")),
                "tran_type": _str_or_none(row.get("tran_type")),
                "doc_due_date": _date_or_none(row.get("doc_due_date")),
                "lob_code": _str_or_none(row.get("lob_code")),
                "policy_no": _str_or_none(row.get("policy_no")),
                "insured_name": _str_or_none(row.get("insured_name")),
                "insured_code": _str_or_none(row.get("insured_code")),
                "currency": _str_or_none(row.get("currency")),
                "doc_desc": _str_or_none(row.get("doc_desc")),
                **_amounts(row),
                "dr_cr": dr_cr,
                "sequence_no": int(row["sequence_no"]) if pd.notna(row.get("sequence_no")) else None,
                "policy_from_date": _date_or_none(row.get("policy_from_date")),
                "policy_to_date": _date_or_none(row.get("policy_to_date")),
                "cust_code": _str_or_none(row.get("cust_code")),
                "cust_name": _str_or_none(row.get("cust_name")),
                "endorsement_no": _str_or_none(row.get("endorsement_no")),
                "pol_comms_doc": _str_or_none(row.get("pol_comms_doc")),
                "branch": _str_or_none(row.get("branch")),
                "gross_amount": _float_or_none(row.get("gross_amount")),
                "age_band": _str_or_none(row.get("age_band")),
                "prod_code": _str_or_none(row.get("prod_code")),
                "cust_group_code": _str_or_none(row.get("cust_group_code")),
                "cust_group_name": _str_or_none(row.get("cust_group_name")),
                "broker_name": _str_or_none(row.get("broker_name")),
                "control_account": _str_or_none(row.get("control_account")),
                "cal_year": _str_or_none(row.get("cal_year")),
                "doc_created_by": _str_or_none(row.get("doc_created_by")),
                "installment_number": _str_or_none(row.get("installment_number")),
                "endorsement_type": _str_or_none(row.get("endorsement_type")),
            }
        )
    return records
