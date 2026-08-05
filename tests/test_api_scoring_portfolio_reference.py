"""End-to-end tests for the Portfolio Analysis 'book reference' context
that POST /cases/{id}/score attaches to a scorecard's claims-experience
detail - HealthCross's own already-booked burning cost (matched to the
case's own HealthCross quote network when one's been uploaded, else the
whole book's overall figure) and population mix, purely informational
context that never feeds into the composite score itself.
"""
import openpyxl
import pytest

from app.models import db_models as models

MEMBERS_HEADER = [
    "CONTRACT", "MASTERCONTRACT", "POLICYNUMBER", "MSH_POLICYNUMBER", "BENEFICIARYID",
    "DOB", "GENDER", "MARITALSTATUS", "NATIONALITY", "DEPENDENCY",
    "PERSONRESIDENCEEMIRATE", "CATEGORY", "NETWORKTYPE",
    "Eff Date", "Exp Date", "EndoDate (Member Start Date)", "EndoDate (Member End Date)",
    "GrossPremium", "ActualGrossPremium", "NETPREMIUM", "ACTUALNETPREMIUM", "TPA FEE",
]

CLAIMS_HEADER = [
    "PATIENT_ID", "CLAIM_ID", "Claim Status", "GROUP_NAME", "CLIENT_NAME", "MSH_POLICY_NUMBER",
    "POLICY_START_DATE", "POLICY_END_DATE", "Member Start Date", "Member End Date",
    "DATE_OF_TREATMENT", "RELATION", "IP_OP_MATERNITY", "MEDICAL_CATEGORY", "PROVIDER_NAME",
    "DIAGNOSIS_CODE", "DIAGNOSIS_DESCRIPTION", "Claimed Amount AED", "Final Amount in AED",
]

PRODUCT_PRICING_HEADER = [
    "Product Name", "From Age", "To Age", "Male Price", "Female Price",
    "Married Female Price", "Region", "Network", "TPA", "Zone", "Created Date", "Updated Date",
]


def _write_xlsx(tmp_path, name, header, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    path = tmp_path / name
    wb.save(path)
    return path


@pytest.fixture()
def members_xlsx(tmp_path):
    return _write_xlsx(
        tmp_path, "members.xlsx", MEMBERS_HEADER,
        [
            [
                "Acme Sub LLC", "Acme Holdings", "P100", "QC1-ACM-A", "ACM0001",
                "1990-01-01", "M", "Single", "India", "Principal",
                "Dubai", "QIC/HC/BR/ACM/DXB/A", "PLATINUM",
                "2025-01-01", "2026-01-01", "2025-01-01", "2026-01-01",
                12000, 12000, None, None, 500,
            ],
        ],
    )


@pytest.fixture()
def claims_xlsx(tmp_path):
    return _write_xlsx(
        tmp_path, "claims.xlsx", CLAIMS_HEADER,
        [
            [
                "ACM0001", "CLM1", "Paid Claims", "Acme Sub LLC", "Acme Holdings", "QC1-ACM-A",
                "2025-01-01", "2026-01-01", "2025-01-01", "2026-01-01",
                "2025-06-01", "Main Insured", "OP", "PHARMACY", "Some Pharmacy",
                "J309", "Allergic rhinitis", 100.0, 90.0,
            ],
        ],
    )


@pytest.fixture()
def rate_card_xlsx(tmp_path):
    return _write_xlsx(
        tmp_path, "pricing.xlsx", PRODUCT_PRICING_HEADER,
        [["Gold", 18, 40, 4000, 4400, "0", "Dubai", "MSH Platinum", "MSH MENA", "Worldwide", "2025-01-01", ""]],
    )


def _create_case_with_census_and_benefits(client):
    resp = client.post("/cases", json={"broker_name": "B", "company_name": "New Biz LLC", "industry": "trading", "business_type": "new"})
    case_id = resp.json()["id"]
    db = client.db_session_local()
    db.add(models.CensusRecord(case_id=case_id, age=30, gender="M", relation="employee", nationality_zone="zone_1_asia"))
    db.add(models.BenefitPlan(case_id=case_id, role="existing", plan_name="Base Plan", network_type="Standard", member_count=1))
    db.commit()
    db.close()
    return case_id


def test_score_attaches_a_network_matched_burning_cost_reference_when_a_quote_is_uploaded(
    client, members_xlsx, claims_xlsx, rate_card_xlsx
):
    with open(members_xlsx, "rb") as f:
        client.post("/portfolio-analysis/members/upload", files={"file": ("members.xlsx", f, "application/octet-stream")})
    with open(claims_xlsx, "rb") as f:
        client.post("/portfolio-analysis/claims/upload", files={"file": ("claims.xlsx", f, "application/octet-stream")})
    with open(rate_card_xlsx, "rb") as f:
        client.post("/admin/rate-cards/upload", files={"file": ("pricing.xlsx", f, "application/octet-stream")})

    case_id = _create_case_with_census_and_benefits(client)
    db = client.db_session_local()
    db.add(models.BenefitPlan(case_id=case_id, role="quoted", plan_name="Quoted - CAT A", category="A", network_type="MSH Platinum"))
    db.commit()
    db.close()

    resp = client.post(f"/cases/{case_id}/score", json={})
    assert resp.status_code == 200
    ref = resp.json()["details"]["portfolio_reference"]
    assert ref["burning_cost"]["basis"] == "network"
    assert ref["burning_cost"]["rows"][0]["network"] == "MSH Platinum"
    assert ref["burning_cost"]["rows"][0]["burning_cost"] is not None
    assert ref["population_mix"]["member_count"] == 1


def test_score_falls_back_to_whole_book_burning_cost_when_no_quote_network_matches(
    client, members_xlsx, claims_xlsx, rate_card_xlsx
):
    with open(members_xlsx, "rb") as f:
        client.post("/portfolio-analysis/members/upload", files={"file": ("members.xlsx", f, "application/octet-stream")})
    with open(claims_xlsx, "rb") as f:
        client.post("/portfolio-analysis/claims/upload", files={"file": ("claims.xlsx", f, "application/octet-stream")})
    with open(rate_card_xlsx, "rb") as f:
        client.post("/admin/rate-cards/upload", files={"file": ("pricing.xlsx", f, "application/octet-stream")})

    # No quote uploaded at all - only case-level census/benefits.
    case_id = _create_case_with_census_and_benefits(client)

    resp = client.post(f"/cases/{case_id}/score", json={})
    assert resp.status_code == 200
    ref = resp.json()["details"]["portfolio_reference"]
    assert ref["burning_cost"]["basis"] == "whole_book"
    assert ref["population_mix"]["member_count"] == 1


def test_score_omits_the_reference_entirely_when_portfolio_analysis_isnt_set_up(client):
    # No members/claims/rate-card uploaded anywhere - the reference should
    # be silently absent, not an error, since it's optional context.
    case_id = _create_case_with_census_and_benefits(client)

    resp = client.post(f"/cases/{case_id}/score", json={})
    assert resp.status_code == 200
    assert "portfolio_reference" not in resp.json()["details"]


def test_score_still_never_uses_portfolio_burning_cost_in_the_composite_math(
    client, members_xlsx, claims_xlsx, rate_card_xlsx
):
    # Regression guard for the "reference only" decision - the presence of
    # a portfolio reference must not change claims_experience_risk or the
    # composite score versus the same case scored with no Portfolio
    # Analysis data uploaded at all.
    case_id_without_portfolio = _create_case_with_census_and_benefits(client)
    resp_without = client.post(f"/cases/{case_id_without_portfolio}/score", json={})

    with open(members_xlsx, "rb") as f:
        client.post("/portfolio-analysis/members/upload", files={"file": ("members.xlsx", f, "application/octet-stream")})
    with open(claims_xlsx, "rb") as f:
        client.post("/portfolio-analysis/claims/upload", files={"file": ("claims.xlsx", f, "application/octet-stream")})
    with open(rate_card_xlsx, "rb") as f:
        client.post("/admin/rate-cards/upload", files={"file": ("pricing.xlsx", f, "application/octet-stream")})

    case_id_with_portfolio = _create_case_with_census_and_benefits(client)
    resp_with = client.post(f"/cases/{case_id_with_portfolio}/score", json={})

    assert resp_with.json()["claims_experience_risk"] == resp_without.json()["claims_experience_risk"]
    assert resp_with.json()["composite_score"] == resp_without.json()["composite_score"]
