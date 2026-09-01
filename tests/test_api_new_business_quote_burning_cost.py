"""API tests for GET /cases/{case_id}/new-business-quote/burning-cost-comparison
(app/api/routes_new_business_rating.py) - comparing a New Business quote's
rate-card price against what HealthCross's own already-booked Portfolio
Analysis book would charge for the same census, re-priced at real burning
cost (see app/scoring/rules/portfolio_analysis.py's
price_case_against_burning_cost). Uses small synthetic spreadsheets rather
than HealthCross's real (client PII) book export.
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
def rate_card_xlsx(tmp_path):
    return _write_xlsx(
        tmp_path, "pricing.xlsx", PRODUCT_PRICING_HEADER,
        [["Gold", 18, 40, 4000, 4400, "0 (Applicable only for age band 18-50)", "Dubai", "MSH Platinum", "MSH MENA", "Worldwide", "2025-01-01", ""]],
    )


@pytest.fixture()
def members_xlsx(tmp_path):
    # Five 35-year-old males, fully within the 18-40 band the rate card
    # itself prices, on a real recognized network raw label ("PLATINUM" ->
    # MSH Platinum via app/reference/network_type_mapping.py). Five members
    # (5.0 earned member-years once fully elapsed) meets
    # MIN_CREDIBLE_MEMBER_YEARS so their bucket isn't excluded from the
    # burning-cost comparison as low-credibility - a single member would be.
    return _write_xlsx(
        tmp_path, "members.xlsx", MEMBERS_HEADER,
        [
            [
                "Acme Sub LLC", "Acme Holdings", "P100", "QC1-ACM-A", f"ACM000{i}",
                "1990-06-01", "M", "Single", "India", "Principal",
                "Dubai", "QIC/HC/BR/ACM/DXB/A", "PLATINUM",
                "2025-01-01", "2026-01-01", "2025-01-01", "2026-01-01",
                12000, 12000, None, None, 500,
            ]
            for i in range(1, 6)
        ],
    )


@pytest.fixture()
def claims_xlsx(tmp_path):
    return _write_xlsx(
        tmp_path, "claims.xlsx", CLAIMS_HEADER,
        [
            [
                f"ACM000{i}", f"CLM{i}", "Paid Claims", "Acme Sub LLC", "Acme Holdings", "QC1-ACM-A",
                "2025-01-01", "2026-01-01", "2025-01-01", "2026-01-01",
                "2025-06-01", "Main Insured", "OP", "PHARMACY", "Some Pharmacy",
                "J309", "Allergic rhinitis", 100.0, 90.0,
            ]
            for i in range(1, 6)
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


def _upload_portfolio_book(client, members_xlsx, claims_xlsx, mapping_xlsx, rate_card_xlsx):
    with open(rate_card_xlsx, "rb") as f:
        client.post("/admin/rate-cards/upload", files={"file": ("pricing.xlsx", f, "application/octet-stream")})
    with open(members_xlsx, "rb") as f:
        client.post("/portfolio-analysis/members/upload", files={"file": ("members.xlsx", f, "application/octet-stream")})
    with open(claims_xlsx, "rb") as f:
        client.post("/portfolio-analysis/claims/upload", files={"file": ("claims.xlsx", f, "application/octet-stream")})
    with open(mapping_xlsx, "rb") as f:
        client.post("/portfolio-analysis/group-product-mapping/upload", files={"file": ("mapping.xlsx", f, "application/octet-stream")})
    # Fully past the member's own policy period (2025-01-01 to 2026-01-01)
    # so earned_premium_fraction is exactly 1.0 - a deterministic burning
    # cost (actual_claims / 1.0) rather than one that depends on today's date.
    client.post("/portfolio-analysis/data-as-of", json={"data_as_of_date": "2026-06-01"})


def _make_case_with_census(client):
    resp = client.post("/cases", json={"broker_name": "Broker", "company_name": "Acme", "industry": "trading"})
    case_id = resp.json()["id"]
    db = client.db_session_local()
    from app.models import db_models as models

    db.add(models.CensusRecord(case_id=case_id, category="A", age=35, gender="M", marital_status="single", relation="employee", emirates="Dubai"))
    db.commit()
    db.close()
    return case_id


def test_burning_cost_comparison_matches_rate_card_quote_by_category(client, members_xlsx, claims_xlsx, mapping_xlsx, rate_card_xlsx):
    _upload_portfolio_book(client, members_xlsx, claims_xlsx, mapping_xlsx, rate_card_xlsx)
    case_id = _make_case_with_census(client)

    resp = client.post(
        f"/cases/{case_id}/new-business-quote",
        json={"categories": [{"category": "A", "product": "Gold", "network": "MSH Platinum", "tpa": "MSH MENA", "variant_selections": {}}]},
    )
    assert resp.status_code == 200
    rate_card_quote = resp.json()

    resp = client.get(f"/cases/{case_id}/new-business-quote/burning-cost-comparison")
    assert resp.status_code == 200
    body = resp.json()
    cat = body["categories"][0]
    assert cat["category"] == "A"
    # actual_claims (450.0 across 5 members) / earned_member_years (5.0,
    # fully elapsed) = 90.0 net, then the SAME Gold loading the rate-card
    # quote itself used.
    assert cat["net_annual_premium"] == 90.0
    assert cat["priced_member_count"] == 1
    assert cat["loading_pct"] == rate_card_quote["result"]["categories"][0]["loading_pct"]
    assert cat["gross_annual_premium"] == pytest.approx(round(90.0 / (1 - cat["loading_pct"]), 2), rel=1e-6)
    assert cat["rate_card_gross_annual_premium"] == rate_card_quote["result"]["categories"][0]["gross_annual_premium"]


def test_burning_cost_comparison_matches_a_stale_un_normalized_category_from_before_the_fix(client, members_xlsx, claims_xlsx, mapping_xlsx, rate_card_xlsx):
    # Regression test: a quote persisted before category normalization
    # existed can still carry its own category as typed at the time (e.g.
    # lowercase "a"). Re-pricing it live against the current (normalized)
    # census must still match, not silently show 0 for every category.
    _upload_portfolio_book(client, members_xlsx, claims_xlsx, mapping_xlsx, rate_card_xlsx)
    case_id = _make_case_with_census(client)

    db = client.db_session_local()
    from app.models import db_models as models

    quote = models.NewBusinessQuote(
        case_id=case_id,
        categories=[{"category": "a", "product": "Gold", "network": "MSH Platinum", "tpa": "MSH MENA", "commission_pct": None, "variant_selections": {}}],
        case_gross_annual_premium=5333.33,
        result={
            "categories": [{
                "category": "a", "product": "Gold", "network": "MSH Platinum", "tpa": "MSH MENA",
                "member_count": 1, "net_annual_premium": 4000.0, "loading_pct": 0.30,
                "gross_annual_premium": 5333.33, "member_breakdown": [], "warnings": [],
            }],
            "case_gross_annual_premium": 5333.33, "priced_member_count": 1, "uncategorized_member_count": 0,
        },
    )
    db.add(quote)
    db.commit()
    db.close()

    resp = client.get(f"/cases/{case_id}/new-business-quote/burning-cost-comparison")
    assert resp.status_code == 200
    cat = resp.json()["categories"][0]
    assert cat["category"] == "A"  # normalized on the way out
    assert cat["priced_member_count"] == 1
    assert cat["net_annual_premium"] == 90.0
    assert cat["rate_card_gross_annual_premium"] == 5333.33


def test_burning_cost_comparison_returns_null_without_a_portfolio_book(client, rate_card_xlsx):
    with open(rate_card_xlsx, "rb") as f:
        client.post("/admin/rate-cards/upload", files={"file": ("pricing.xlsx", f, "application/octet-stream")})
    case_id = _make_case_with_census(client)
    client.post(
        f"/cases/{case_id}/new-business-quote",
        json={"categories": [{"category": "A", "product": "Gold", "network": "MSH Platinum", "tpa": "MSH MENA", "variant_selections": {}}]},
    )

    resp = client.get(f"/cases/{case_id}/new-business-quote/burning-cost-comparison")
    assert resp.status_code == 200
    assert resp.json() is None


def test_burning_cost_comparison_404s_without_a_prior_quote(client, members_xlsx, claims_xlsx, mapping_xlsx, rate_card_xlsx):
    _upload_portfolio_book(client, members_xlsx, claims_xlsx, mapping_xlsx, rate_card_xlsx)
    case_id = _make_case_with_census(client)

    resp = client.get(f"/cases/{case_id}/new-business-quote/burning-cost-comparison")
    assert resp.status_code == 404


def test_burning_cost_comparison_404s_for_missing_case(client):
    resp = client.get("/cases/999999/new-business-quote/burning-cost-comparison")
    assert resp.status_code == 404


def test_a_member_the_book_has_no_exact_cell_for_is_still_priced(
    client, members_xlsx, claims_xlsx, mapping_xlsx, rate_card_xlsx
):
    """The bug this endpoint used to have.

    It priced off raw per-bucket burning cost, which EXCLUDED any member
    whose exact (Product, Network, age band, gender) bucket was missing
    or too thin. The comparison then silently priced fewer members than
    the quote did and came in low by however many it dropped - and the
    thinner the book, the worse the understatement, which is exactly when
    an underwriter is most likely to lean on it.

    The book here has only 35-year-old males. A 60-year-old female has no
    cell of her own, so under the old behaviour she vanished from the
    comparison entirely.
    """
    _upload_portfolio_book(client, members_xlsx, claims_xlsx, mapping_xlsx, rate_card_xlsx)

    resp = client.post("/cases", json={"broker_name": "B", "company_name": "Edge", "industry": "trading"})
    case_id = resp.json()["id"]
    db = client.db_session_local()
    from app.models import db_models as models
    db.add(models.CensusRecord(case_id=case_id, category="A", age=35, gender="M",
                               marital_status="single", relation="employee", emirates="Dubai"))
    db.add(models.CensusRecord(case_id=case_id, category="A", age=60, gender="F",
                               marital_status="married", relation="employee", emirates="Dubai"))
    db.commit()
    db.close()

    client.post(
        f"/cases/{case_id}/new-business-quote",
        json={"categories": [{"category": "A", "product": "Gold", "network": "MSH Platinum",
                              "tpa": "MSH MENA", "variant_selections": {}}]},
    )

    body = client.get(f"/cases/{case_id}/new-business-quote/burning-cost-comparison").json()
    cat = body["categories"][0]

    # Both members priced, not just the one with an exact cell.
    assert cat["member_count"] == 2
    assert cat["priced_member_count"] == 2
    assert cat["gross_annual_premium"] > 0
    # And the fallback is declared rather than hidden.
    assert cat["fallback_member_count"] >= 1
    assert cat["warnings"], "a member priced from a broader cell should be reported"


def test_the_comparison_reports_the_books_own_cost_not_a_forward_projection(
    client, members_xlsx, claims_xlsx, mapping_xlsx, rate_card_xlsx
):
    # Trend defaults to zero so the figure means "what the book cost",
    # which is what it has always meant here. Applying it is an explicit
    # choice, for comparing against a card that prices forward.
    _upload_portfolio_book(client, members_xlsx, claims_xlsx, mapping_xlsx, rate_card_xlsx)
    case_id = _make_case_with_census(client)
    client.post(
        f"/cases/{case_id}/new-business-quote",
        json={"categories": [{"category": "A", "product": "Gold", "network": "MSH Platinum",
                              "tpa": "MSH MENA", "variant_selections": {}}]},
    )
    flat = client.get(f"/cases/{case_id}/new-business-quote/burning-cost-comparison").json()
    trended = client.get(
        f"/cases/{case_id}/new-business-quote/burning-cost-comparison?trend_pct=0.10"
    ).json()
    assert flat["categories"][0]["net_annual_premium"] == 90.0
    assert trended["categories"][0]["net_annual_premium"] == pytest.approx(99.0)


def test_a_census_with_no_benefits_document_can_still_be_risk_priced(
    client, members_xlsx, claims_xlsx, mapping_xlsx, rate_card_xlsx
):
    """The common new-business shape: a census arrives, no table of
    benefits does, and the underwriter keys the plan design in by hand on
    the New Business Quote tab. That route has to reach a risk-based
    price - routing them to the Benefits tab, which they deliberately are
    not using, is a dead end.
    """
    _upload_portfolio_book(client, members_xlsx, claims_xlsx, mapping_xlsx, rate_card_xlsx)
    case_id = client.post(
        "/cases", json={"broker_name": "B", "company_name": "Census Only", "industry": "trading"}
    ).json()["id"]

    db = client.db_session_local()
    from app.models import db_models as models
    db.add(models.CensusRecord(case_id=case_id, category="A", age=35, gender="M",
                               marital_status="single", relation="employee",
                               emirates="Dubai", nationality="India"))
    db.commit()
    db.close()

    # Nothing configured yet: refused, but the message names BOTH routes.
    resp = client.get(f"/cases/{case_id}/risk-based-price")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "New Business Quote tab" in detail
    assert "Benefits tab" in detail

    # Plan design set on the quote tab - no benefits document anywhere.
    assert not client.get(f"/cases/{case_id}/benefit-plans").json()
    client.post(f"/cases/{case_id}/new-business-quote", json={"categories": [
        {"category": "A", "product": "Gold", "network": "MSH Platinum",
         "tpa": "MSH MENA", "variant_selections": {}}]})

    resp = client.get(f"/cases/{case_id}/risk-based-price")
    assert resp.status_code == 200
    body = resp.json()
    assert body["suggested_premium"] > 0
    assert body["rate_card_premium"] > 0
    assert body["gap_pct"] is not None
    assert body["categories"][0]["product"] == "Gold"
