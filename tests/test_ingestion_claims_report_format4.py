"""Tests for the fourth real-world "Health insurance claims record" layout
(app/ingestion/claims_report.py's _parse_format4_from_rows) - seen on a
real MetLife-issued DHA Claims Report. Row numbering matches format 1's
(dates at 3a/3b/4a/4b/4c, totals at 5a/5c) rather than format 2's, but
like format 2 it renders with real, reliably-detected bordered tables.
Distinct from every other format in carrying a 6th "Others" claims
category alongside IP/OP/Pharmacy/Dental/Optical throughout, and in row
17's month being a bare "01".."12" number with recycled row-letters
(17a-17g) once the report spans a second calendar year.
"""
from app.ingestion.claims_report import _parse_format4_from_rows, first_full_months

# Synthetic but structurally faithful to the real report's extracted
# table rows: each row is [row_key, label, IP, OP, Pharmacy, Dental,
# Optical, Others, Totals] (9 columns).
SAMPLE_ROWS = [
    ["DHA CLAIMS REPORT", None, None, None, None, None, None, None, None],
    ["Part I", "Health Insurance Claims Record Summary", None, None, None, None, None, None, None],
    ["1", "Name of scheme/ employer:", "SAMPLE GROUP LLC", None, None, None, None, None, None],
    ["2", "Policy Number:", "6296800000", None, None, None, None, None, None],
    ["2a", "Policy Group Code:", "39499", None, None, None, None, None, None],
    ["3a", "Policy Group Effective Date:", "22/10/2025", None, None, None, None, None, None],
    ["3b", "Policy Group Expiry Date:", "22/10/2026", None, None, None, None, None, None],
    ["4a", "Report period start date", "22/10/2025", None, None, None, None, None, None],
    ["4b", "Report period end date", "21/07/2026", None, None, None, None, None, None],
    ["4c", "Report production date", "13/08/2026", None, None, None, None, None, None],
    ["5a", "Value of claims paid during report period only", "1,761,829", None, None, None, None, None, None],
    ["5b", "Value of claims incurred, reported but not paid", "19,698", None, None, None, None, None, None],
    ["5c", "Value of claims incurred but not reported", "271,730", None, None, None, None, None, None],
    ["6", "Population census (at beginning of reporting period)", "0-15", "16-25", "26-35", "36-50", "51-65", "Over 65", "Totals"],
    ["6a", "Male", "20", "5", "5", "15", "13", "", "58"],
    ["6b", "Single Females", "15", "1", "1", "1", "", "", "18"],
    ["6c", "Married Females", "", "1", "5", "15", "5", "", "26"],
    ["7", "Population census (at end of reporting period)", "0-15", "16-25", "26-35", "36-50", "51-65", "Over 65", "Totals"],
    ["7a", "Male", "23", "5", "6", "19", "10", "", "63"],
    ["7b", "Single Females", "15", "1", "1", "1", "", "", "18"],
    ["7c", "Married Females", "", "1", "6", "17", "5", "", "29"],
    ["8", "Claims data by member type (value AED)", "IP", "OP", "Pharmacy", "Dental", "Optical", "Others", "Totals"],
    ["8a", "Employee", "74,837", "312,134", "85,643", "23,251", "11,064", "23,953", "530,883"],
    ["8b", "Spouse", "58,216", "260,126", "33,311", "8,955", "5,680", "11,727", "378,014"],
    ["8c", "Dependents", "58,377", "624,708", "107,232", "18,200", "2,606", "41,809", "852,932"],
    ["8d", "Totals", "191,430", "1,196,968", "226,186", "50,406", "19,351", "77,488", "1,761,829"],
    ["9", "Claims data by member type (number)", "IP", "OP", "Pharmacy", "Dental", "Optical", "Others", "Totals"],
    ["9a", "Employee", "3", "336", "193", "28", "14", "20", "594"],
    ["9b", "Spouse", "7", "334", "119", "11", "7", "13", "491"],
    ["9c", "Dependents", "3", "333", "211", "25", "4", "60", "636"],
    ["10", "Claims data by diagnosis grouping (top 10 by value)", "IP", "OP", "Pharmacy", "Dental", "Optical", "Others", "Totals"],
    ["10a", "Encounter for antineoplastic chemotherapy Z51.11", "49,112", "309,610", "", "", "", "", "358,722"],
    ["10b", "Other atopic dermatitis L20.89", "", "940", "51,437", "", "", "", "52,377"],
    ["11", "Number of claims by diagnosis grouping", "IP", "OP", "Pharmacy", "Dental", "Optical", "Others", "Totals"],
    ["11a", "Encounter for antineoplastic chemotherapy Z51.11", "1", "16", "", "", "", "", "17"],
    ["11b", "Other atopic dermatitis L20.89", "", "4", "8", "", "", "", "12"],
    ["12", "Claims data by provider (top 10 by AED value)", "IP", "OP", "Pharmacy", "Dental", "Optical", "Others", "Totals"],
    ["12a", "MEDICLINIC CITY HOSPITAL FZ LLC - H193", "49,112", "570,353", "", "", "", "", "619,465"],
    ["12b", "DR.SULAIMAN AL HABIB HOSPITAL - H677", "65,008", "91,160", "715", "525", "", "", "157,408"],
    ["13", "Number of Claims by provider", "IP", "OP", "Pharmacy", "Dental", "Optical", "Others", "Totals"],
    ["13a", "MEDICLINIC CITY HOSPITAL FZ LLC - H193", "1", "56", "", "", "", "", "57"],
    ["14", "Claims data by network (UAE only by AED value)", "IP", "OP", "Pharmacy", "Dental", "Optical", "Others", "Totals"],
    ["14a", "In Network", "179,356", "1,159,055", "224,869", "15,772", "2,304", "37,259", "1,618,615"],
    ["14b", "Out of Network", "", "27,045", "1,003", "27,156", "12,048", "38,818", "106,070"],
    ["17", "Total claims paid per service month (by AED value)", "Month Ending Date", None, "Year", "Value", None, None, None],
    ["17a", "", "10", None, "2025", "37,906", None, None, None],
    ["17b", "", "11", None, "2025", "141,164", None, None, None],
    ["17c", "", "12", None, "2025", "97,547", None, None, None],
    ["17a", "", "01", None, "2026", "394,240", None, None, None],
    ["17b", "", "02", None, "2026", "231,325", None, None, None],
    ["17c", "", "03", None, "2026", "210,661", None, None, None],
    ["17d", "", "04", None, "2026", "168,910", None, None, None],
    ["17e", "", "05", None, "2026", "138,697", None, None, None],
    ["17f", "", "06", None, "2026", "266,045", None, None, None],
    ["17g", "", "07", None, "2026", "75,333", None, None, None],
    ["18", "Patient Support Programs", None, None, None, None, None, None, None],
    ["18a", "Number of Members enrolled in BASMAH initiative", "0", None, None, None, None, None, None],
]


def test_parses_policy_number_and_dates():
    result = _parse_format4_from_rows(SAMPLE_ROWS)
    assert result["policy_number"] == "6296800000"
    assert str(result["policy_effective_date"]) == "2025-10-22"
    assert str(result["policy_expiry_date"]) == "2026-10-22"
    assert str(result["report_period_start"]) == "2025-10-22"
    assert str(result["report_period_end"]) == "2026-07-21"
    assert str(result["report_production_date"]) == "2026-08-13"


def test_parses_totals():
    result = _parse_format4_from_rows(SAMPLE_ROWS)
    assert result["total_paid"] == 1_761_829.0
    assert result["incurred_not_reported"] == 271_730.0


def test_sums_the_three_population_categories_from_the_totals_column():
    result = _parse_format4_from_rows(SAMPLE_ROWS)
    assert result["opening_members"] == 58 + 18 + 26
    assert result["closing_members"] == 63 + 18 + 29


def test_member_type_breakdown_maps_others_into_not_yet_classified():
    result = _parse_format4_from_rows(SAMPLE_ROWS)
    employee = next(r for r in result["claims_by_member_type_value"] if r["relation"] == "Employee")
    assert employee == {
        "relation": "Employee", "in_patient": 74837.0, "out_patient": 312134.0, "pharmacy": 85643.0,
        "dental": 23251.0, "optical": 11064.0, "not_yet_classified": 23953.0, "total": 530883.0,
    }
    assert len(result["claims_by_member_type_value"]) == 3  # the "8d" Totals row isn't itself a member row

    employee_count = next(r for r in result["claims_by_member_type_count"] if r["relation"] == "Employee")
    assert employee_count["in_patient"] == 3
    assert employee_count["not_yet_classified"] == 20


def test_treatment_type_breakdown_from_the_totals_row():
    result = _parse_format4_from_rows(SAMPLE_ROWS)
    breakdown = {t["type"]: t["value"] for t in result["treatment_type_breakdown"]}
    assert breakdown == {
        "In-Patient": 191430.0, "Out-Patient": 1196968.0, "Pharmacy": 226186.0,
        "Dental": 50406.0, "Optical": 19351.0, "Not Yet Classified": 77488.0,
    }


def test_diagnosis_and_provider_breakdowns():
    result = _parse_format4_from_rows(SAMPLE_ROWS)
    assert result["diagnosis_breakdown"] == [
        {
            "label": "Encounter for antineoplastic chemotherapy Z51.11", "value": 358722.0,
            "count": 17, "ip_value": 49112.0, "ip_count": 1,
        },
        {
            "label": "Other atopic dermatitis L20.89", "value": 52377.0,
            "count": 12, "ip_value": 0.0, "ip_count": 0,
        },
    ]
    assert result["provider_breakdown"] == [
        {"provider": "MEDICLINIC CITY HOSPITAL FZ LLC - H193", "value": 619465.0},
        {"provider": "DR.SULAIMAN AL HABIB HOSPITAL - H677", "value": 157408.0},
    ]


def test_network_breakdown():
    result = _parse_format4_from_rows(SAMPLE_ROWS)
    assert result["claims_by_type"] == [
        {"type": "In Network", "value": 1618615.0},
        {"type": "Out of Network", "value": 106070.0},
    ]


def test_monthly_paid_handles_recycled_row_letters_across_calendar_years():
    # Row 17's own labels only go 17a-17g, so a 10-month report reuses
    # 17a/17b/17c for the second year's Jan/Feb/Mar - can't be looked up
    # by unique key, must be read positionally between the 17/18 headers.
    result = _parse_format4_from_rows(SAMPLE_ROWS)
    assert len(result["monthly_paid"]) == 10
    assert result["monthly_paid"][0] == {"year": 2025, "month": "Oct", "paid": 37906.0, "partial": True}
    assert result["monthly_paid"][3] == {"year": 2026, "month": "Jan", "paid": 394240.0, "partial": False}
    assert result["monthly_paid"][-1] == {"year": 2026, "month": "Jul", "paid": 75333.0, "partial": False}


def test_monthly_paid_stops_at_the_18_section_header():
    result = _parse_format4_from_rows(SAMPLE_ROWS)
    # "18a" ("0" for BASMAH enrollment) must never be mistaken for a month.
    assert all(m["year"] in (2025, 2026) for m in result["monthly_paid"])
    assert len(result["monthly_paid"]) == 10


def test_first_full_months_skips_the_partial_inception_month():
    result = _parse_format4_from_rows(SAMPLE_ROWS)
    full = first_full_months(result["monthly_paid"], count=6)
    assert full == [141164.0, 97547.0, 394240.0, 231325.0, 210661.0, 168910.0]
