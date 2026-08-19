"""Tests for app/ingestion/claims_report.py's _parse_ppr_text - an
insurer-issued "Policy Performance Review" dashboard (a Power BI export),
not a DHA-mandated claims record. Deliberately scoped to only what its
text layer extracts cleanly (review period, average lives, total paid,
monthly paid-claims figures) - its detailed benefit/diagnosis/age
breakdown tables extract too unreliably to trust (a "Power BI Desktop"
watermark is interleaved character-by-character into nearby headings,
and find_tables() returns badly malformed rows for the denser tables).
"""
from app.ingestion.claims_report import _parse_ppr_text

# Synthetic but structurally faithful to the real document's extracted
# text (a two-column "Paid Claims by Month" / "Paid Claims by Subgroup"
# layout reads back interleaved, same as the real export).
SAMPLE_TEXT = """
Power BI Desktop
62968 SAMPLE GROUP LLC - DUBAI BRANCH
Review Period: October 22 2025 to July 21 2026
Policy Performance Review

OvPoewerr BaI Dleslk toSp napshot
Paid Claims Average Lives No. of Claimants
543,155 172 150
Outstanding Claims Members Dependents No. of Claims Claims Per Member Cost Per Claim
10,420 43.4% 56.6% 2,025 11.8 268.22
Paid Claims by Month Paid Claims by Subgroup
Policy Name Paid Amount Number of Number of
Claims Claimants
2025 Oct 10,772
SAMPLE GROUP LLC - DUBAI BRANCH 470,191 1,609 95
2025 Nov 43,220 SAMPLE GROUP LLC - ABU DHABI 72,965 416 55
Total 543,155 2,025 150
2025 Dec 32,708
2026 Jan 118,408
2026 Feb 70,957
2026 Mar 61,507
2026 Apr 48,779
2026 May 53,649
2026 Jun 89,215
2026 Jul 13,941
0K 20K 40K 60K 80K 100K 120K
* Paid Claims do not include Outstanding and IBNR.
"""


def test_parses_policy_number_and_review_period():
    result = _parse_ppr_text(SAMPLE_TEXT)
    assert result["policy_number"] == "62968"
    assert str(result["report_period_start"]) == "2025-10-22"
    assert str(result["report_period_end"]) == "2026-07-21"


def test_parses_total_paid_and_average_lives():
    result = _parse_ppr_text(SAMPLE_TEXT)
    assert result["total_paid"] == 543_155.0
    # No separate opening/closing figure in this document - the single
    # average-lives KPI stands in for both.
    assert result["opening_members"] == 172
    assert result["closing_members"] == 172


def test_parses_monthly_paid_and_flags_the_partial_inception_month():
    result = _parse_ppr_text(SAMPLE_TEXT)
    assert len(result["monthly_paid"]) == 10
    assert result["monthly_paid"][0] == {"year": 2025, "month": "Oct", "paid": 10772.0, "partial": True}
    assert result["monthly_paid"][1] == {"year": 2025, "month": "Nov", "paid": 43220.0, "partial": False}
    assert result["monthly_paid"][-1] == {"year": 2026, "month": "Jul", "paid": 13941.0, "partial": False}


def test_does_not_attempt_the_unreliable_detail_breakdowns():
    # Deliberately empty - not silently wrong data from the malformed
    # benefit/diagnosis/provider tables this document's later pages have.
    result = _parse_ppr_text(SAMPLE_TEXT)
    assert result["diagnosis_breakdown"] == []
    assert result["provider_breakdown"] == []
    assert result["treatment_type_breakdown"] == []
    assert result["claims_by_member_type_value"] == []


def test_missing_review_period_leaves_partial_flag_off():
    text_without_period = SAMPLE_TEXT.replace(
        "Review Period: October 22 2025 to July 21 2026", "Review Period: unavailable"
    )
    result = _parse_ppr_text(text_without_period)
    assert result["report_period_start"] is None
    assert result["monthly_paid"][0]["partial"] is False
