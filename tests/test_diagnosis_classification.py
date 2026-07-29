from app.reference.diagnosis_classification import (
    CHRONIC,
    MIXED,
    NON_CHRONIC,
    classify_diagnosis_group,
    flag_diagnosis_group,
)


def test_cancer_is_chronic_and_high_exposure():
    result = classify_diagnosis_group("Neoplasms")
    assert result["classification"] == CHRONIC
    assert result["high_exposure"] is True


def test_heart_disease_is_chronic_and_high_exposure():
    result = classify_diagnosis_group("Diseases of circulatory system")
    assert result["classification"] == CHRONIC
    assert result["high_exposure"] is True


def test_kidney_genitourinary_is_flagged_high_exposure_even_when_mixed():
    result = classify_diagnosis_group("Diseases of genitourinary system")
    assert result["classification"] == MIXED
    assert result["high_exposure"] is True


def test_dental_is_non_chronic():
    result = classify_diagnosis_group("Dental/Oral diseases")
    assert result["classification"] == NON_CHRONIC
    assert result["high_exposure"] is False


def test_unknown_grouping_falls_back_gracefully():
    result = classify_diagnosis_group("Some New Diagnosis Chapter")
    assert result["classification"] == MIXED
    assert result["high_exposure"] is False


def test_flag_large_ip_claim_average():
    # 6 IP claims averaging AED 57,686 - well above the large-claim threshold
    flags = flag_diagnosis_group(value=381126, count=52, ip_value=346113, ip_count=6)
    assert "possible_large_or_shock_claim" in flags["flags"]


def test_flag_low_ip_claim_average_as_daycase_artifact():
    # 89 "in-patient" claims averaging AED 231 - implausibly low for real admissions
    flags = flag_diagnosis_group(value=20578, count=89, ip_value=20578, ip_count=89)
    assert "possible_daycase_coding_artifact" in flags["flags"]


def test_no_flags_for_ordinary_claim_averages():
    flags = flag_diagnosis_group(value=100000, count=200, ip_value=10000, ip_count=10)
    assert flags["flags"] == []
