from datetime import date

from app.scoring.rules.claims_ledger_analysis import (
    category_burning_cost,
    full_months_only,
    monthly_final_amount,
    top_diagnoses_by_final_amount,
    top_patients_by_final_amount,
    top_providers_by_final_amount,
)


def _entry(patient_id, final_amount, claim_id=None, diagnosis_code=None, diagnosis_description=None,
           ip_op_maternity="OP", date_of_treatment=None, provider_name=None, medical_category=None):
    return {
        "patient_id": patient_id,
        "claim_id": claim_id or f"{patient_id}-{final_amount}",
        "final_amount": final_amount,
        "diagnosis_code": diagnosis_code,
        "diagnosis_description": diagnosis_description,
        "ip_op_maternity": ip_op_maternity,
        "date_of_treatment": date_of_treatment,
        "provider_name": provider_name,
        "medical_category": medical_category,
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


def test_top_providers_ranked_by_total_final_amount():
    entries = [
        _entry("P1", 1000, claim_id="C1", provider_name="Mediclinic City"),
        _entry("P2", 500, claim_id="C2", provider_name="Mediclinic City"),
        _entry("P3", 2000, claim_id="C3", provider_name="American Hospital"),
        _entry("P4", 100, claim_id="C4", provider_name=None),
    ]
    top = top_providers_by_final_amount(entries, top_n=2)
    assert top[0] == {"provider_name": "American Hospital", "final_amount": 2000.0, "claim_count": 1, "patient_count": 1}
    assert top[1] == {"provider_name": "Mediclinic City", "final_amount": 1500.0, "claim_count": 2, "patient_count": 2}


def test_category_burning_cost_only_uses_full_months_and_matches_overall_formula():
    entries = [
        _entry("P1", 100, medical_category="Inpatient", date_of_treatment=date(2025, 10, 5)),
        _entry("P2", 200, medical_category="Outpatient", date_of_treatment=date(2025, 10, 20)),
        _entry("P1", 300, medical_category="Inpatient", date_of_treatment=date(2025, 11, 1)),
        # December is outside the full-months window (excluded as a trailing
        # partial by the caller) and must not affect the average at all.
        _entry("P3", 99999, medical_category="Inpatient", date_of_treatment=date(2025, 12, 1)),
    ]
    full_month_keys = [(2025, 10), (2025, 11)]

    rows = category_burning_cost(entries, full_month_keys, inflation_pct=0.075, loading_pct=0.28)
    by_category = {r["category"]: r for r in rows}

    assert set(by_category.keys()) == {"Inpatient", "Outpatient"}
    # Inpatient: (100 + 300) / 2 months = 200 avg -> matches the same
    # average/annualize/trend/load formula used for the overall figure.
    inpatient = by_category["Inpatient"]
    assert inpatient["avg_month"] == 200.0
    assert inpatient["annualized_incurred_claims"] == 2400.0
    assert inpatient["trended_claims"] == round(2400.0 * 1.075, 2)
    assert inpatient["projected_annual_claims"] == round(2400.0 * 1.075 / (1 - 0.28), 2)
    assert inpatient["member_count"] == 1

    outpatient = by_category["Outpatient"]
    assert outpatient["avg_month"] == 100.0
    assert outpatient["member_count"] == 1


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
