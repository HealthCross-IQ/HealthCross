from app.ingestion.claims_report import first_full_months, parse_claims_report_text

SAMPLE_REPORT_TEXT = """
Health Insurance Claims Record-DHA Mandated Format
PART I Health insurance claims record summary
1Name of scheme / employer LEGRAND SNC FZE Group ID Claims Paid in AED
2Policy number 54773 54775 1,772,027.07
3Policy period 0 -
3a Policy effective date 27 Sep 2025 0 -
3b Policy expiry date 26 Sep 2026 0 -
4a Report period start date 27 Sep 2025 0 -
4b Report period end date 30 Jun 2026 0 -
4c Report production date 13 Jul 2026 0 -
5a Value of claims paid during report period only 1,772,027 0 -
5c Value of claims incurred but not reported up to end of reporting period 421,387
6Population census (at beginning of reporting period) 0-15 16-25 26-35 36-50 51-65 Over 65 Totals
6a Female 23 10 22 30 5 0 90
6b Male 19 5 11 32 4 0 71
7Population census (at end of reporting period) 0-15 16-25 26-35 36-50 51-65 Over 65 Totals
7a Female 34 14 27 40 7 1 123
7b Male 30 7 17 43 6 1 104
PART II Claims data
8 Claims data by member type (value AED) IP OP Pharmacy Dental Optical Not yet classified Totals
8a Employee 99,786 331,844 122,566 56,008 39,920 3,741 653,865
8b Spouse 382,229 289,781 121,788 25,231 16,364 907 836,299
8c Dependents 44,695 118,480 58,711 32,162 23,989 3,826 281,863
8d Totals 526,709 740,105 303,065 113,401 80,272 8,475 1,772,027
9 Claims data by member type (number) IP OP Pharmacy Dental Optical Not yet classified Totals
9a Employee 49 553 310 40 30 2 984
9b Spouse 126 435 280 19 13 3 876
9c Dependents 24 311 252 38 19 6 650
9d Totals 199 1,299 842 97 62 11 2,510
10a NEOPLASMS 346,113 32,224 2,789 0 0 0 381,126
10bENDOCRINE, NUTRITIONAL, METABOLIC, IMMUNITY 5,484 64,483 118,828 0 0 0 188,795
11a 6 31 15 0 0 0 52
11b 24 124 99 0 0 0 247
12aBURJEEL HOSPITAL (VPS) 325,338 12,799 0 0 0 0 338,138
14aDirect Billing 503,772 589,041 286,748 14,784 683 144 1,395,172
14bReimbursement 22,937 151,064 16,317 98,617 79,589 8,331 376,855
"""

MONTHLY_TEXT = """
17Total claims paid per service month (by AED value) Month Year Value
2025 Sep 8,870
2025 Oct 2 03,861
2025 Nov 216,391
2025 Dec 175,170
2026 Jan 5 02,079
2026 Feb 157,146
2026 Mar 155,289
2026 Apr 191,921
2026 May 141,087
2026 Jun 2 0,214
Total 1,772,027
"""


def test_parses_policy_and_dates():
    result = parse_claims_report_text(SAMPLE_REPORT_TEXT)
    assert result["policy_number"] == "54773"
    assert str(result["policy_effective_date"]) == "2025-09-27"
    assert str(result["policy_expiry_date"]) == "2026-09-26"
    assert str(result["report_period_start"]) == "2025-09-27"
    assert str(result["report_period_end"]) == "2026-06-30"
    assert str(result["report_production_date"]) == "2026-07-13"


def test_parses_totals_without_being_corrupted_by_adjacent_placeholder_columns():
    result = parse_claims_report_text(SAMPLE_REPORT_TEXT)
    assert result["total_paid"] == 1772027.0
    assert result["incurred_not_reported"] == 421387.0


def test_parses_population_census():
    result = parse_claims_report_text(SAMPLE_REPORT_TEXT)
    assert result["opening_female"] == 90
    assert result["opening_male"] == 71
    assert result["opening_members"] == 161
    assert result["closing_female"] == 123
    assert result["closing_male"] == 104
    assert result["closing_members"] == 227


def test_parses_diagnosis_and_provider_breakdown():
    result = parse_claims_report_text(SAMPLE_REPORT_TEXT)
    labels = {d["label"] for d in result["diagnosis_breakdown"]}
    assert "NEOPLASMS" in labels
    neoplasms = next(d for d in result["diagnosis_breakdown"] if d["label"] == "NEOPLASMS")
    assert neoplasms["value"] == 381126.0
    assert neoplasms["count"] == 52
    assert neoplasms["ip_value"] == 346113.0
    assert neoplasms["ip_count"] == 6

    assert result["provider_breakdown"][0]["provider"] == "BURJEEL HOSPITAL (VPS)"
    assert result["provider_breakdown"][0]["value"] == 338138.0

    assert result["claims_by_type"][0]["type"] == "Direct Billing"
    assert result["claims_by_type"][0]["value"] == 1395172.0


def test_parses_claims_by_member_type_value_and_count_excluding_the_totals_row():
    result = parse_claims_report_text(SAMPLE_REPORT_TEXT)
    value_rows = {r["relation"]: r for r in result["claims_by_member_type_value"]}
    assert set(value_rows) == {"Employee", "Spouse", "Dependents"}  # "Totals" row dropped
    assert value_rows["Employee"]["in_patient"] == 99786.0
    assert value_rows["Employee"]["total"] == 653865.0
    assert value_rows["Spouse"]["dental"] == 25231.0

    count_rows = {r["relation"]: r for r in result["claims_by_member_type_count"]}
    assert count_rows["Dependents"]["optical"] == 19
    assert count_rows["Dependents"]["total"] == 650


def test_glues_pdf_rendering_whitespace_within_monthly_figures():
    result = parse_claims_report_text(MONTHLY_TEXT)
    monthly = {m["month"]: m["paid"] for m in result["monthly_paid"]}
    assert monthly["Oct"] == 203861.0
    assert monthly["Jan"] == 502079.0
    assert monthly["Jun"] == 20214.0


def test_flags_the_partial_inception_month():
    combined_text = SAMPLE_REPORT_TEXT + MONTHLY_TEXT
    result = parse_claims_report_text(combined_text)
    monthly = result["monthly_paid"]
    assert monthly[0]["month"] == "Sep"
    assert monthly[0]["partial"] is True
    assert all(not m["partial"] for m in monthly[1:])


def test_first_full_months_skips_the_partial_stub():
    combined_text = SAMPLE_REPORT_TEXT + MONTHLY_TEXT
    result = parse_claims_report_text(combined_text)
    months = first_full_months(result["monthly_paid"], 6)
    assert months == [203861.0, 216391.0, 175170.0, 502079.0, 157146.0, 155289.0]


def test_first_full_months_raises_when_not_enough_full_months():
    result = parse_claims_report_text(SAMPLE_REPORT_TEXT)  # no monthly data at all here
    try:
        first_full_months(result["monthly_paid"], 6)
        assert False, "expected ValueError"
    except ValueError:
        pass
