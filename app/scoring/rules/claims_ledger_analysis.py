"""Analysis over a raw per-claim-line claims ledger (see
app/ingestion/claims_ledger.py) - top patients and diagnoses by final
claims amount, and a month-wise claims trend feeding the burning-cost-style
"expected annual claims" figure used by app/scoring/rules/renewal_rating.py.

All entries (both "Paid Claims" and "Outstanding Claims" status) count
toward these totals unless the caller filters otherwise - "Outstanding"
represents claims already incurred but not yet finalized, not a
speculative estimate, so excluding it would understate true incurred cost.
"""
from collections import defaultdict
from typing import List, Optional

from app.reference.diagnosis_classification import classify_diagnosis_group, flag_diagnosis_group
from app.reference.icd10_chapters import icd10_chapter

TOP_N_DEFAULT = 10


def top_patients_by_final_amount(entries: List[dict], top_n: int = TOP_N_DEFAULT) -> List[dict]:
    totals: dict = defaultdict(lambda: {"final_amount": 0.0, "claim_count": 0, "claim_ids": set()})
    for e in entries:
        patient_id = e.get("patient_id")
        if not patient_id:
            continue
        bucket = totals[patient_id]
        bucket["final_amount"] += e.get("final_amount") or 0.0
        if e.get("claim_id"):
            bucket["claim_ids"].add(e["claim_id"])

    rows = [
        {"patient_id": patient_id, "final_amount": round(v["final_amount"], 2), "claim_count": len(v["claim_ids"])}
        for patient_id, v in totals.items()
    ]
    rows.sort(key=lambda r: r["final_amount"], reverse=True)
    return rows[:top_n]


def top_providers_by_final_amount(entries: List[dict], top_n: int = TOP_N_DEFAULT) -> List[dict]:
    totals: dict = defaultdict(lambda: {"final_amount": 0.0, "claim_count": 0, "claim_ids": set(), "patient_ids": set()})
    for e in entries:
        provider_name = e.get("provider_name")
        if not provider_name:
            continue
        bucket = totals[provider_name]
        bucket["final_amount"] += e.get("final_amount") or 0.0
        if e.get("claim_id"):
            bucket["claim_ids"].add(e["claim_id"])
        if e.get("patient_id"):
            bucket["patient_ids"].add(e["patient_id"])

    rows = [
        {
            "provider_name": provider_name,
            "final_amount": round(v["final_amount"], 2),
            "claim_count": len(v["claim_ids"]),
            "patient_count": len(v["patient_ids"]),
        }
        for provider_name, v in totals.items()
    ]
    rows.sort(key=lambda r: r["final_amount"], reverse=True)
    return rows[:top_n]


def top_diagnoses_by_final_amount(entries: List[dict], top_n: int = TOP_N_DEFAULT) -> List[dict]:
    totals: dict = defaultdict(lambda: {
        "description": None, "value": 0.0, "count": 0, "ip_value": 0.0, "ip_count": 0,
    })
    for e in entries:
        code = e.get("diagnosis_code")
        description = e.get("diagnosis_description")
        key = code or description
        if not key:
            continue
        bucket = totals[key]
        bucket["description"] = bucket["description"] or description or code
        amount = e.get("final_amount") or 0.0
        bucket["value"] += amount
        bucket["count"] += 1
        if (e.get("ip_op_maternity") or "").upper() == "IP":
            bucket["ip_value"] += amount
            bucket["ip_count"] += 1

    rows = []
    for code, v in totals.items():
        chapter = icd10_chapter(code)
        classification = classify_diagnosis_group(chapter) if chapter else {
            "classification": "mixed", "high_exposure": False, "note": "No ICD-10 chapter could be determined for this code.",
        }
        flags = flag_diagnosis_group(v["value"], v["count"], v["ip_value"], v["ip_count"])
        rows.append(
            {
                "diagnosis_code": code,
                "diagnosis_description": v["description"],
                "value": round(v["value"], 2),
                "count": v["count"],
                "chapter": chapter,
                "classification": classification["classification"],
                "high_exposure": classification["high_exposure"],
                "note": classification["note"],
                "avg_per_claim": flags["avg_per_claim"],
                "flags": flags["flags"],
            }
        )
    rows.sort(key=lambda r: r["value"], reverse=True)
    return rows[:top_n]


def monthly_final_amount(entries: List[dict]) -> List[dict]:
    """Sums final_amount by (year, month) of date_of_treatment, sorted
    chronologically. Does not itself decide which months are "full" - see
    full_months_only() below, which needs the policy start date to know
    whether the first month is a partial stub.
    """
    totals: dict = defaultdict(float)
    for e in entries:
        d = e.get("date_of_treatment")
        if not d:
            continue
        totals[(d.year, d.month)] += e.get("final_amount") or 0.0

    return [
        {"year": year, "month": month, "final_amount": round(amount, 2)}
        for (year, month), amount in sorted(totals.items())
    ]


def category_burning_cost(
    entries: List[dict],
    full_month_keys: List[tuple],
    inflation_pct: float,
    loading_pct: float,
) -> List[dict]:
    """Same burning-cost formula as the overall renewal rating (average of
    the full months, annualize x12, trend for inflation, gross up for the
    commission/OPEX loading), computed separately per `medical_category` -
    e.g. Inpatient/Outpatient/Dental/Optical, or whatever categorization the
    source ledger uses.

    `full_month_keys` is the (year, month) set already established as
    "full" (see full_months_only()) from the WHOLE ledger, not recomputed
    per category - so every category is measured over the identical time
    window rather than each independently deciding its own edge exclusions.
    """
    full_month_set = set(full_month_keys)
    by_category: dict = defaultdict(lambda: defaultdict(float))
    patients_by_category: dict = defaultdict(set)

    for e in entries:
        category = e.get("medical_category") or "Uncategorized"
        d = e.get("date_of_treatment")
        if not d or (d.year, d.month) not in full_month_set:
            continue
        by_category[category][(d.year, d.month)] += e.get("final_amount") or 0.0
        if e.get("patient_id"):
            patients_by_category[category].add(e["patient_id"])

    rows = []
    for category, monthly_totals in by_category.items():
        avg_month = sum(monthly_totals.values()) / len(full_month_set)
        annualized = avg_month * 12
        trended = annualized * (1 + inflation_pct)
        projected_annual_claims = trended / (1 - loading_pct)
        rows.append(
            {
                "category": category,
                "member_count": len(patients_by_category[category]),
                "avg_month": round(avg_month, 2),
                "annualized_incurred_claims": round(annualized, 2),
                "trended_claims": round(trended, 2),
                "projected_annual_claims": round(projected_annual_claims, 2),
            }
        )
    rows.sort(key=lambda r: r["trended_claims"], reverse=True)
    return rows


def full_months_only(monthly: List[dict], policy_start_year: Optional[int] = None, policy_start_month: Optional[int] = None, policy_start_day: Optional[int] = None) -> List[dict]:
    """Drops the first month if the policy didn't start on the 1st (a
    partial stub), and always drops the LAST month present, since a claims
    ledger is exported "as of" some date mid-month rather than reporting a
    guaranteed-complete final month.
    """
    if not monthly:
        return []

    result = list(monthly)
    if (
        policy_start_year is not None
        and policy_start_month is not None
        and policy_start_day is not None
        and policy_start_day != 1
        and result
        and (result[0]["year"], result[0]["month"]) == (policy_start_year, policy_start_month)
    ):
        result = result[1:]

    if result:
        result = result[:-1]

    return result
