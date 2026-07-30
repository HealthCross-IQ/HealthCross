from datetime import date

from app.scoring.rules.claims_ledger_analysis import (
    full_months_only,
    monthly_final_amount,
    top_diagnoses_by_final_amount,
    top_patients_by_final_amount,
)


def _entry(patient_id, final_amount, claim_id=None, diagnosis_code=None, diagnosis_description=None,
           ip_op_maternity="OP", date_of_treatment=None):
    return {
        "patient_id": patient_id,
        "claim_id": claim_id or f"{patient_id}-{final_amount}",
        "final_amount": final_amount,
        "diagnosis_code": diagnosis_code,
        "diagnosis_description": diagnosis_description,
        "ip_op_maternity": ip_op_maternity,
        "date_of_treatment": date_of_treatment,
    }


def test_top_patients_ranked_by_total_final_amount():
    entries = [
        _entry("P1", 1000, claim_id="C1"),
        _entry("P1", 500, claim_id="C2"),
        _entry("P2", 2000, claim_id="C3"),
        _entry("P3", 100, claim_id="C4"),
    ]
    top = top_patients_by_final_amount(entries, top_n=2)
    assert top[0] == {"patient_id": "P2", "final_amount": 2000.0, "claim_count": 1}
    assert top[1] == {"patient_id": "P1", "final_amount": 1500.0, "claim_count": 2}


def test_top_diagnoses_classified_chronic_vs_non_chronic():
    entries = [
        _entry("P1", 50000, diagnosis_code="C50", diagnosis_description="Malignant neoplasm of breast", ip_op_maternity="IP"),
        _entry("P2", 200, diagnosis_code="Z511", diagnosis_description="Other medical care"),
    ]
    top = top_diagnoses_by_final_amount(entries)
    cancer_row = next(r for r in top if r["diagnosis_code"] == "C50")
    assert cancer_row["classification"] == "chronic"
    assert cancer_row["high_exposure"] is True

    admin_row = next(r for r in top if r["diagnosis_code"] == "Z511")
    assert admin_row["classification"] == "non_chronic"
    assert admin_row["high_exposure"] is False


def test_monthly_final_amount_sums_by_treatment_month():
    entries = [
        _entry("P1", 100, date_of_treatment=date(2025, 10, 5)),
        _entry("P1", 200, date_of_treatment=date(2025, 10, 20)),
        _entry("P2", 300, date_of_treatment=date(2025, 11, 1)),
    ]
    monthly = monthly_final_amount(entries)
    assert monthly == [
        {"year": 2025, "month": 10, "final_amount": 300.0},
        {"year": 2025, "month": 11, "final_amount": 300.0},
    ]


def test_full_months_only_drops_partial_start_and_trailing_month():
    monthly = [
        {"year": 2025, "month": 10, "final_amount": 100.0},
        {"year": 2025, "month": 11, "final_amount": 200.0},
        {"year": 2025, "month": 12, "final_amount": 300.0},
    ]
    # policy started on the 15th - October is a partial stub, and December
    # (the last month present) is always dropped as the trailing partial.
    full = full_months_only(monthly, policy_start_year=2025, policy_start_month=10, policy_start_day=15)
    assert full == [{"year": 2025, "month": 11, "final_amount": 200.0}]


def test_full_months_only_keeps_first_month_when_policy_started_on_the_1st():
    monthly = [
        {"year": 2025, "month": 10, "final_amount": 100.0},
        {"year": 2025, "month": 11, "final_amount": 200.0},
        {"year": 2025, "month": 12, "final_amount": 300.0},
    ]
    full = full_months_only(monthly, policy_start_year=2025, policy_start_month=10, policy_start_day=1)
    assert full == [
        {"year": 2025, "month": 10, "final_amount": 100.0},
        {"year": 2025, "month": 11, "final_amount": 200.0},
    ]
