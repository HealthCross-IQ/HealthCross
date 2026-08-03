"""API tests for the New Business rate-card pricing endpoints
(app/api/routes_new_business_rating.py) - uploading the two rate-card
spreadsheets, discovering priced options, and computing/storing a quote
for a case. Uses small synthetic spreadsheets built in-test rather than
HealthCross's real (commercially sensitive) rate card.
"""
import openpyxl
import pytest

PRODUCT_PRICING_HEADER = [
    "Product Name", "From Age", "To Age", "Male Price", "Female Price",
    "Married Female Price", "Region", "Network", "TPA", "Zone", "Created Date", "Updated Date",
]

VARIANT_HEADER = [
    "Benefit Name", "Variant Name", "Option Value", "Direction", "Impact Type",
    "Impact Value", "Is Default", "Zone", "Region", "TPA", "Network", "Created Date", "Updated Date",
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
def rate_card_files(tmp_path):
    pricing_path = _write_xlsx(
        tmp_path,
        "pricing.xlsx",
        PRODUCT_PRICING_HEADER,
        [
            ["Bronze", 0, 17, 1000, 1000, "Not Applicable", "Dubai", "Net A", "TPA X", "Worldwide", "2025-01-01", ""],
            ["Bronze", 18, 40, 2000, 2200, "0 (Applicable only for age band 18-50)", "Dubai", "Net A", "TPA X", "Worldwide", "2025-01-01", ""],
            ["Platinum", 18, 40, 5000, 5500, "0 (Applicable only for age band 18-50)", "Dubai", "Net A", "TPA X", "Worldwide", "2025-01-01", ""],
        ],
    )
    variants_path = _write_xlsx(
        tmp_path,
        "variants.xlsx",
        VARIANT_HEADER,
        [
            ["UAE Benefit", "Annual Limit", "USD 150,000", "Base", "Text", 0, "No", "Worldwide", "Dubai", "TPA X", "Net A", "2025-01-01", ""],
            ["UAE Benefit", "Annual Limit", "USD 500,000", "Upgrade", "Percent", 3, "No", "Worldwide", "Dubai", "TPA X", "Net A", "2025-01-01", ""],
        ],
    )
    return pricing_path, variants_path


def _upload_rate_cards(client, rate_card_files):
    pricing_path, variants_path = rate_card_files
    with open(pricing_path, "rb") as f:
        resp = client.post("/admin/rate-cards/upload", files={"file": ("pricing.xlsx", f, "application/octet-stream")})
    assert resp.status_code == 200
    with open(variants_path, "rb") as f:
        resp = client.post("/admin/benefit-variant-rates/upload", files={"file": ("variants.xlsx", f, "application/octet-stream")})
    assert resp.status_code == 200


def _make_case(client, target_premium=None):
    db = client.db_session_local()
    from app.models import db_models as models

    case = models.Case(broker_name="Broker", company_name="Acme", industry="trading", target_premium=target_premium)
    db.add(case)
    db.commit()
    db.refresh(case)
    db.add_all(
        [
            models.CensusRecord(case_id=case.id, category="A", age=30, gender="M", marital_status="single", relation="employee", emirates="Dubai"),
            models.CensusRecord(case_id=case.id, category="A", age=28, gender="F", marital_status="married", relation="spouse", emirates="Dubai"),
        ]
    )
    db.commit()
    case_id = case.id
    db.close()
    return case_id


def test_upload_rate_card_replaces_existing_rows(client, rate_card_files):
    pricing_path, _ = rate_card_files
    with open(pricing_path, "rb") as f:
        resp = client.post("/admin/rate-cards/upload", files={"file": ("pricing.xlsx", f, "application/octet-stream")})
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows_ingested"] == 3
    assert set(body["products"]) == {"Bronze", "Platinum"}

    # A second upload should wholesale-replace, not append.
    with open(pricing_path, "rb") as f:
        resp = client.post("/admin/rate-cards/upload", files={"file": ("pricing.xlsx", f, "application/octet-stream")})
    assert resp.json()["rows_ingested"] == 3


def test_rate_card_options_lists_products_and_their_networks(client, rate_card_files):
    _upload_rate_cards(client, rate_card_files)
    resp = client.get("/new-business/rate-card-options")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["products"]) == {"Bronze", "Platinum"}
    assert body["product_networks"]["Bronze"] == [{"network": "Net A", "tpa": "TPA X"}]


def test_variant_options_returns_options_grouped_by_variant(client, rate_card_files):
    _upload_rate_cards(client, rate_card_files)
    resp = client.get("/new-business/variant-options", params={"region": "Dubai", "tpa": "TPA X", "network": "Net A"})
    assert resp.status_code == 200
    body = resp.json()
    values = {opt["option_value"] for opt in body["Annual Limit"]}
    assert values == {"USD 150,000", "USD 500,000"}


def test_compute_new_business_quote_for_a_case(client, rate_card_files):
    _upload_rate_cards(client, rate_card_files)
    case_id = _make_case(client, target_premium=5000)

    resp = client.post(
        f"/cases/{case_id}/new-business-quote",
        json={"categories": [{"category": "A", "product": "Bronze", "network": "Net A", "tpa": "TPA X", "variant_selections": {}}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    # net = 2000 (male) + 2200 (married female, Dubai has no surcharge) = 4200
    # loading = 10% + 5% + 5% + 6.5% (Bronze) = 26.5%
    assert body["result"]["categories"][0]["net_annual_premium"] == 4200.0
    assert body["case_gross_annual_premium"] == round(4200.0 / (1 - 0.265), 2)
    assert body["opportunity_assessment"]["verdict"] in {"Good", "Marginal", "Poor"}


def test_quote_persists_and_is_retrievable_as_latest(client, rate_card_files):
    _upload_rate_cards(client, rate_card_files)
    case_id = _make_case(client)

    client.post(
        f"/cases/{case_id}/new-business-quote",
        json={"categories": [{"category": "A", "product": "Bronze", "network": "Net A", "tpa": "TPA X", "variant_selections": {}}]},
    )
    resp = client.get(f"/cases/{case_id}/new-business-quote")
    assert resp.status_code == 200
    assert resp.json()["case_gross_annual_premium"] > 0

    resp = client.get(f"/cases/{case_id}/new-business-quotes")
    assert len(resp.json()) == 1


def test_quote_without_target_premium_reports_unknown_opportunity(client, rate_card_files):
    _upload_rate_cards(client, rate_card_files)
    case_id = _make_case(client, target_premium=None)

    resp = client.post(
        f"/cases/{case_id}/new-business-quote",
        json={"categories": [{"category": "A", "product": "Bronze", "network": "Net A", "tpa": "TPA X", "variant_selections": {}}]},
    )
    assert resp.json()["opportunity_assessment"]["verdict"] == "Unknown"


def test_quote_missing_case_404s(client, rate_card_files):
    _upload_rate_cards(client, rate_card_files)
    resp = client.post(
        "/cases/999999/new-business-quote",
        json={"categories": [{"category": "A", "product": "Bronze", "network": "Net A", "tpa": "TPA X", "variant_selections": {}}]},
    )
    assert resp.status_code == 404


def test_quote_without_uploaded_rate_card_400s(client):
    case_id = _make_case(client)
    resp = client.post(
        f"/cases/{case_id}/new-business-quote",
        json={"categories": [{"category": "A", "product": "Bronze", "network": "Net A", "tpa": "TPA X", "variant_selections": {}}]},
    )
    assert resp.status_code == 400


def test_census_categories_reports_member_counts_and_uncategorized(client):
    case_id = _make_case(client)
    db = client.db_session_local()
    from app.models import db_models as models

    db.add(models.CensusRecord(case_id=case_id, category=None, age=10, gender="M", marital_status="single", relation="child", emirates="Dubai"))
    db.commit()
    db.close()

    resp = client.get(f"/cases/{case_id}/census-categories")
    assert resp.status_code == 200
    body = resp.json()
    assert body["categories"] == [{"category": "A", "member_count": 2}]
    assert body["uncategorized_member_count"] == 1
