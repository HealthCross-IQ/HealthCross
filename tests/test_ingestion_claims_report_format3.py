"""Tests for the third real-world "Health insurance claims record" layout
(app/ingestion/claims_report.py's _parse_format3_text) - seen on a real
DAL Group DHA Claims Utilization Report. Distinct from both existing
formats: it lacks the literal "DHA Mandated Format" marker format 1 keys
off of, but (unlike format 2) has no bordered table structure at all and
uses different column counts (3 for diagnosis/provider, 6 - no "Not Yet
Classified" - for member-type/network) than format 1's fixed 7 columns.
"""
from app.ingestion.claims_report import _parse_format3_text as parse_claims_report_text

SAMPLE_REPORT_TEXT = """
Health insurance claims record
PART I Health insurance claims record summary
1 Name of scheme/employer DAL GROUP CO. LTD
2 Policy number
607836-001 / 607836-004 / 607836-005 / 607836-007 / 607836-008 / 607836-009 /
607836-010 / 607836-011 / 607836-012 / 607836-013
3 Policy period
3a Policy effective date 29/08/2025
3b Policy expiry date 28/08/2026
3c Initial policy effective date (date from which you have provided 29/08/2021
continuous cover for this client)
4 Report period (must be a minimum 9 months, less at discretion of insurer)
4a Report period start date 29/08/2025
4b Report period end date 31/05/2026
4c Report production date 07/07/2026
5 Total values (AED)
5a Value of claims paid during report period only 1,315,830
5b Value of claims incurred , reported but not paid up to end of reporting 56,581
period
5c Value of claims incurred but not reported up to end of reporting period 314,461
6 Population census (at beginning of reporting period) 0 - 15 16 - 25 26 - 35 36 - 50 51 - 65 Over 65
6a Male 16 6 17 25 12 0
6b Single Female 17 6 6 5 1 0
6c Married Female 0 0 11 19 4 0
7 Population census (at end of reporting period) 0 - 15 16 - 25 26 - 35 36 - 50 51 - 65 Over 65
7a Male 19 6 17 32 16 0
7b Single Female 21 7 8 4 2 0
7c Married Female 0 0 11 23 5 0
PART II Claims data
8 Claims data by member type (value AED) IP OP Pharmacy Dental Optical Total
8a Employee 447,817 423,879 95,144 1,105 349 968,294
8b Spouse 98,319 90,848 21,392 1,540 255 212,355
8c Dependents 25,992 39,244 66,135 3,809 0 135,180
8d Total 572,129 553,971 182,671 6,454 604 1,315,830
9 Claims data by member type (number) IP OP Pharmacy Dental Optical Total
9a Employee 31 535 244 5 2 817
9b Spouse 4 126 61 6 1 198
9c Dependents 9 106 122 11 0 248
9d Total 44 767 427 22 3 1,263
10 Claims data by diagnosis grouping (top 10 by value) IP OP Total
10a Factors Influencing Health Status 231,752 133,761 365,513
10b Diseases of the Nervous system 74,542 64,703 139,245
11 Number of claims by diagnosis grouping (corresponds to list in 10 by value) IP OP Total
11a Factors Influencing Health Status 11 62 73
11b Diseases of the Nervous system 2 75 77
12 Claims data by provider (top 10 by AED value) IP OP Total
12a American Hospital Group 226,035 198,300 424,335
12b Mediclinic Group - Dubai 78,498 107,164 185,662
13 Number of Claims by provider (corresponding to top 10 by AED value) IP OP Total
13a American Hospital Group 9 119 128
13b Mediclinic Group - Dubai 3 147 150
14 Claims data by network (UAE only by AED value) IP OP Pharmacy Dental Optical Total
14a In network 572,129 538,211 182,414 5,838 364 1,298,956
14b Out of network 0 15,761 257 616 240 16,874
15 Claims data by network (UAE only by number) IP OP Pharmacy Dental Optical Total
15a In network 44 751 426 20 2 1,243
15b Out of network 0 16 1 2 1 20
16 Non-UAE claims data IP OP Total
16a By value (AED) 0 0 0
16b By number 0 0 0
17 Total claims paid per service month (by AED value) Month ending date Year Value
17a August 31 2025 313
17b September 30 2025 127,378
17k
17l
17m
18 Patient Support Programs
Number of Members Enrolled in Basmah Initative -
2 ongoing cancer cases with expected utilization of USD 325,909 for the upcoming policy period.
Authentication statement
"""


def test_recognizes_policy_number_and_dates():
    result = parse_claims_report_text(SAMPLE_REPORT_TEXT)
    assert result["policy_number"] == (
        "607836-001 / 607836-004 / 607836-005 / 607836-007 / 607836-008 / 607836-009 / "
        "607836-010 / 607836-011 / 607836-012 / 607836-013"
    )
    assert str(result["policy_effective_date"]) == "2025-08-29"
    assert str(result["policy_expiry_date"]) == "2026-08-28"
    assert str(result["report_period_start"]) == "2025-08-29"
    assert str(result["report_period_end"]) == "2026-05-31"
    assert str(result["report_production_date"]) == "2026-07-07"


def test_a_later_bare_digit_line_never_reopens_policy_number_collection():
    # "2 ongoing cancer cases..." under section 18 starts with a bare "2"
    # too - must not be mistaken for a second occurrence of row 2 and
    # appended onto the real policy number.
    result = parse_claims_report_text(SAMPLE_REPORT_TEXT)
    assert "ongoing cancer cases" not in result["policy_number"]


def test_total_values():
    result = parse_claims_report_text(SAMPLE_REPORT_TEXT)
    assert result["total_paid"] == 1315830.0
    assert result["incurred_not_reported"] == 314461.0


def test_population_totals_summed_across_the_three_gender_marital_rows():
    result = parse_claims_report_text(SAMPLE_REPORT_TEXT)
    # 6a: 16+6+17+25+12+0=76, 6b: 17+6+6+5+1+0=35, 6c: 0+0+11+19+4+0=34 -> 145
    assert result["opening_members"] == 145
    # 7a: 19+6+17+32+16+0=90, 7b: 21+7+8+4+2+0=42, 7c: 0+0+11+23+5+0=39 -> 171
    assert result["closing_members"] == 171


def test_diagnosis_and_provider_breakdowns_use_the_three_column_shape():
    result = parse_claims_report_text(SAMPLE_REPORT_TEXT)
    by_label = {r["label"]: r for r in result["diagnosis_breakdown"]}
    assert by_label["Factors Influencing Health Status"]["value"] == 365513.0
    assert by_label["Factors Influencing Health Status"]["ip_value"] == 231752.0
    assert by_label["Factors Influencing Health Status"]["count"] == 73
    assert by_label["Factors Influencing Health Status"]["ip_count"] == 11

    by_provider = {r["provider"]: r["value"] for r in result["provider_breakdown"]}
    assert by_provider["American Hospital Group"] == 424335.0


def test_member_type_rows_use_the_six_column_shape_with_no_not_yet_classified_data():
    result = parse_claims_report_text(SAMPLE_REPORT_TEXT)
    by_relation = {r["relation"]: r for r in result["claims_by_member_type_value"]}
    assert "Total" not in by_relation  # the cross-check row is dropped, not treated as a category
    employee = by_relation["Employee"]
    assert employee["in_patient"] == 447817.0
    assert employee["out_patient"] == 423879.0
    assert employee["pharmacy"] == 95144.0
    assert employee["dental"] == 1105.0
    assert employee["optical"] == 349.0
    assert employee["not_yet_classified"] == 0.0
    assert employee["total"] == 968294.0


def test_claims_by_type_and_treatment_type_breakdown_from_network_rows():
    result = parse_claims_report_text(SAMPLE_REPORT_TEXT)
    by_type = {r["type"]: r["value"] for r in result["claims_by_type"]}
    assert by_type["In network"] == 1298956.0
    assert by_type["Out of network"] == 16874.0

    treatment = {r["type"]: r["value"] for r in result["treatment_type_breakdown"]}
    # 14a + 14b summed per column
    assert treatment["In-Patient"] == 572129.0 + 0.0
    assert treatment["Pharmacy"] == 182414.0 + 257.0
    assert treatment["Not Yet Classified"] == 0.0  # this variant has no such column at all


def test_monthly_paid_parses_full_month_name_rows_and_skips_blank_ones():
    result = parse_claims_report_text(SAMPLE_REPORT_TEXT)
    assert len(result["monthly_paid"]) == 2
    assert result["monthly_paid"][0] == {"year": 2025, "month": "Aug", "paid": 313.0, "partial": True}
    assert result["monthly_paid"][1]["month"] == "Sep"
    assert result["monthly_paid"][1]["paid"] == 127378.0


# A second real export shape for item 17 (seen on a real Arabia Insurance/
# Maxtube report): each row spells out its own month-ending date in full
# ("31/10/2025") ahead of the year, and its value carries decimals -
# distinct from SAMPLE_REPORT_TEXT's bare "August 31 2025 313" shape.
ALT_MONTHLY_REPORT_TEXT = """
Health insurance claims record
2 Policy number
6252
3a Policy effective date 05/10/2025
3b Policy expiry date 30/09/2026
17 Total Claim paid per Month ending date Year Value
17a October 31/10/2025 2025 4,219.80
17b November 30/11/2025 2025 16,405.40
17c December 31/12/2025 2025 36,150.21
17k
17l
"""


def test_monthly_paid_parses_the_full_ending_date_row_shape():
    result = parse_claims_report_text(ALT_MONTHLY_REPORT_TEXT)
    assert result["monthly_paid"] == [
        {"year": 2025, "month": "Oct", "paid": 4219.80, "partial": True},
        {"year": 2025, "month": "Nov", "paid": 16405.40, "partial": False},
        {"year": 2025, "month": "Dec", "paid": 36150.21, "partial": False},
    ]


# A third real export shape for item 17 (seen on a real DHA-labeled
# "Health insurance claims record" that lacks the literal "DHA Mandated
# Format" marker format 1 keys off of): no day-of-month at all, just
# "Aug 2025 54,977" - the row-number prefix stays attached to the same
# line rather than the month/year/value shape ALT_MONTHLY_REPORT_TEXT
# and SAMPLE_REPORT_TEXT both use.
NO_DAY_MONTHLY_REPORT_TEXT = """
Health insurance claims record
2 Policy number
627803, 627804
3a Policy effective date 15-Aug-2025
3b Policy expiry date 14-Aug-2026
17 Total claims Processed per service month (by AED value) Month ending date Year Value
17a Aug 2025 54,977
17b Sep 2025 135,530
17c Oct 2025 92,917
17k Jun 2026
17l Jul 2026
17m Aug 2026
"""


def test_monthly_paid_parses_the_no_day_of_month_row_shape():
    result = parse_claims_report_text(NO_DAY_MONTHLY_REPORT_TEXT)
    assert result["monthly_paid"] == [
        {"year": 2025, "month": "Aug", "paid": 54977.0, "partial": True},
        {"year": 2025, "month": "Sep", "paid": 135530.0, "partial": False},
        {"year": 2025, "month": "Oct", "paid": 92917.0, "partial": False},
    ]
