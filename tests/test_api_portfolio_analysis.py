"""API tests for Portfolio Analysis (app/api/routes_portfolio_analysis.py) -
uploading the book's own membership/claims/group-product-mapping data and
running the analysis against a rate card. Uses small synthetic
spreadsheets rather than HealthCross's real (client PII) book export.
"""
import openpyxl
import pytest

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
        tmp_path,
        "members.xlsx",
        MEMBERS_HEADER,
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
        tmp_path,
        "claims.xlsx",
        CLAIMS_HEADER,
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
def mapping_xlsx(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Subgroup", "Master Group", "Product"])
    ws.append(["Acme Sub LLC", "Acme Holdings", "Gold"])
    path = tmp_path / "mapping.xlsx"
    wb.save(path)
    return path


@pytest.fixture()
def rate_card_xlsx(tmp_path):
    return _write_xlsx(
        tmp_path,
        "pricing.xlsx",
        PRODUCT_PRICING_HEADER,
        [
            ["Gold", 18, 40, 4000, 4400, "0 (Applicable only for age band 18-50)", "Dubai", "MSH Platinum", "MSH MENA", "Worldwide", "2025-01-01", ""],
        ],
    )


def test_upload_members_ingests_rows(client, members_xlsx):
    with open(members_xlsx, "rb") as f:
        resp = client.post("/portfolio-analysis/members/upload", files={"file": ("members.xlsx", f, "application/octet-stream")})
    assert resp.status_code == 200
    assert resp.json()["rows_ingested"] == 1


def test_upload_members_replaces_existing_rows(client, members_xlsx):
    for _ in range(2):
        with open(members_xlsx, "rb") as f:
            resp = client.post("/portfolio-analysis/members/upload", files={"file": ("members.xlsx", f, "application/octet-stream")})
    assert resp.json()["rows_ingested"] == 1


def test_upload_claims_ingests_rows(client, claims_xlsx):
    with open(claims_xlsx, "rb") as f:
        resp = client.post("/portfolio-analysis/claims/upload", files={"file": ("claims.xlsx", f, "application/octet-stream")})
    assert resp.status_code == 200
    assert resp.json()["rows_ingested"] == 1


def test_upload_group_product_mapping_ingests_rows(client, mapping_xlsx):
    with open(mapping_xlsx, "rb") as f:
        resp = client.post("/portfolio-analysis/group-product-mapping/upload", files={"file": ("mapping.xlsx", f, "application/octet-stream")})
    assert resp.status_code == 200
    assert resp.json()["rows_ingested"] == 2  # subgroup + master group both get a row


def test_summary_requires_members_uploaded_first(client):
    resp = client.get("/portfolio-analysis/summary")
    assert resp.status_code == 400


def test_summary_requires_rate_card_uploaded(client, members_xlsx):
    with open(members_xlsx, "rb") as f:
        client.post("/portfolio-analysis/members/upload", files={"file": ("members.xlsx", f, "application/octet-stream")})
    resp = client.get("/portfolio-analysis/summary")
    assert resp.status_code == 400


def test_full_pipeline_summary_by_product(client, members_xlsx, claims_xlsx, mapping_xlsx, rate_card_xlsx):
    with open(members_xlsx, "rb") as f:
        client.post("/portfolio-analysis/members/upload", files={"file": ("members.xlsx", f, "application/octet-stream")})
    with open(claims_xlsx, "rb") as f:
        client.post("/portfolio-analysis/claims/upload", files={"file": ("claims.xlsx", f, "application/octet-stream")})
    with open(mapping_xlsx, "rb") as f:
        client.post("/portfolio-analysis/group-product-mapping/upload", files={"file": ("mapping.xlsx", f, "application/octet-stream")})
    with open(rate_card_xlsx, "rb") as f:
        client.post("/admin/rate-cards/upload", files={"file": ("pricing.xlsx", f, "application/octet-stream")})

    resp = client.get("/portfolio-analysis/summary", params={"group_by": "product"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_members"] == 1
    assert body["out_of_scope_member_count"] == 0
    assert body["unmapped_product_member_count"] == 0

    gold = next(r for r in body["rows"] if r["product"] == "Gold")
    assert gold["member_count"] == 1
    assert gold["standard_premium"] == 4000.0  # male, 18-40, Gold/MSH Platinum
    assert gold["actual_premium"] == 12000.0
    assert gold["actual_claims"] == 90.0


def test_summary_rejects_an_invalid_group_by(client, members_xlsx, rate_card_xlsx):
    with open(members_xlsx, "rb") as f:
        client.post("/portfolio-analysis/members/upload", files={"file": ("members.xlsx", f, "application/octet-stream")})
    with open(rate_card_xlsx, "rb") as f:
        client.post("/admin/rate-cards/upload", files={"file": ("pricing.xlsx", f, "application/octet-stream")})

    resp = client.get("/portfolio-analysis/summary", params={"group_by": "not_a_field"})
    assert resp.status_code == 400


def test_member_detail_endpoint_returns_per_member_rows(client, members_xlsx, rate_card_xlsx):
    with open(members_xlsx, "rb") as f:
        client.post("/portfolio-analysis/members/upload", files={"file": ("members.xlsx", f, "application/octet-stream")})
    with open(rate_card_xlsx, "rb") as f:
        client.post("/admin/rate-cards/upload", files={"file": ("pricing.xlsx", f, "application/octet-stream")})

    resp = client.get("/portfolio-analysis/members")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["beneficiary_id"] == "ACM0001"
