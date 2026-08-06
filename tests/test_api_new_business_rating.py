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


def test_census_categories_suggests_a_product_tier_from_the_existing_insurer(client):
    from app.models import db_models as models

    db = client.db_session_local()
    db.add(models.InsurerTierPreference(insurer_name="Allianz", suggested_product="Platinum"))
    case = models.Case(broker_name="Broker", company_name="Acme", industry="trading", existing_insurer="Allianz")
    db.add(case)
    db.commit()
    db.refresh(case)
    db.add(models.CensusRecord(case_id=case.id, category="A", age=30, gender="M", marital_status="single", relation="employee", emirates="Dubai"))
    db.commit()
    case_id = case.id
    db.close()

    resp = client.get(f"/cases/{case_id}/census-categories")
    assert resp.status_code == 200
    assert resp.json()["suggested_product"] == "Platinum"


def test_census_categories_suggested_product_is_none_for_an_unmapped_insurer(client):
    case_id = _make_case(client)
    db = client.db_session_local()
    from app.models import db_models as models

    case = db.get(models.Case, case_id)
    case.existing_insurer = "Some Insurer Not In The Table"
    db.commit()
    db.close()

    resp = client.get(f"/cases/{case_id}/census-categories")
    assert resp.status_code == 200
    assert resp.json()["suggested_product"] is None


def test_quote_includes_a_tier_ladder_per_category(client, tmp_path):
    pricing_path = _write_xlsx(
        tmp_path,
        "pricing_ladder.xlsx",
        PRODUCT_PRICING_HEADER,
        [
            ["Bronze", 18, 40, 2000, 2200, "0 (Applicable only for age band 18-50)", "Dubai", "MSH Regular", "MSH MENA", "Worldwide", "2025-01-01", ""],
            ["Silver", 18, 40, 3000, 3300, "0 (Applicable only for age band 18-50)", "Dubai", "MSH Premium", "MSH MENA", "Worldwide", "2025-01-01", ""],
            ["Silver", 18, 40, 3200, 3500, "0 (Applicable only for age band 18-50)", "Dubai", "MSH Enhanced", "MSH MENA", "Worldwide", "2025-01-01", ""],
        ],
    )
    with open(pricing_path, "rb") as f:
        resp = client.post("/admin/rate-cards/upload", files={"file": ("pricing_ladder.xlsx", f, "application/octet-stream")})
    assert resp.status_code == 200

    case_id = _make_case(client)
    resp = client.post(
        f"/cases/{case_id}/new-business-quote",
        json={"categories": [{"category": "A", "product": "Bronze", "network": "MSH Regular", "tpa": "MSH MENA", "variant_selections": {}}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    ladder = body["result"]["categories"][0]["tier_ladder"]
    products_in_ladder = [row["product"] for row in ladder]
    assert products_in_ladder == ["Silver", "Bronze"]  # Bronze is the floor - nothing below it

    silver = next(r for r in ladder if r["product"] == "Silver")
    # Every MSH network offered under Silver shows up, not just one.
    assert {n["network"] for n in silver["networks"]} == {"MSH Premium", "MSH Enhanced"}
    premium_row = next(n for n in silver["networks"] if n["network"] == "MSH Premium")
    assert premium_row["net_annual_premium"] == 3000.0 + 3300.0  # both census members priced under Silver too

    bronze = next(r for r in ladder if r["product"] == "Bronze")
    chosen_row = next(n for n in bronze["networks"] if n["is_chosen"])
    assert chosen_row["network"] == "MSH Regular"


def _insert_existing_plan_with_nb_pick(client, case_id, category, product=None, network=None, tpa=None):
    db = client.db_session_local()
    from app.models import db_models as models

    plan = models.BenefitPlan(
        case_id=case_id, role="existing", plan_name=f"Category {category}",
        category=category, nb_product=product, nb_network=network, nb_tpa=tpa,
        standard_summary={},
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    plan_id = plan.id
    db.close()
    return plan_id


def test_benefits_category_pick_lets_the_first_quote_auto_compute_on_census_upload(client, rate_card_files, tmp_path):
    # Before any manual "Compute quote" ever happens, a case whose Benefits
    # tab category already has its own Product/Network/TPA set (see
    # BenefitPlan.nb_product/nb_network/nb_tpa) should get its first quote
    # automatically the moment a matching census is uploaded.
    _upload_rate_cards(client, rate_card_files)
    case_id = _make_case(client)
    # _make_case's own census is category A - remove it so this test
    # controls exactly when the census (the auto-quote trigger) arrives.
    db = client.db_session_local()
    from app.models import db_models as models

    db.query(models.CensusRecord).filter_by(case_id=case_id).delete()
    db.commit()
    db.close()

    _insert_existing_plan_with_nb_pick(client, case_id, "A", product="Bronze", network="Net A", tpa="TPA X")

    resp = client.get(f"/cases/{case_id}/new-business-quote")
    assert resp.status_code == 404  # nothing yet - no census

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Employee Ref", "Category", "Age", "Gender", "Marital Status", "Relation", "Emirate"])
    ws.append(["E1", "A", 30, "M", "single", "employee", "Dubai"])
    ws.append(["E2", "A", 28, "F", "married", "spouse", "Dubai"])
    path = tmp_path / "census.xlsx"
    wb.save(path)

    with open(path, "rb") as f:
        resp = client.post(f"/cases/{case_id}/census", files={"file": ("census.xlsx", f, "application/octet-stream")})
    assert resp.status_code == 200

    resp = client.get(f"/cases/{case_id}/new-business-quote")
    assert resp.status_code == 200
    assert resp.json()["case_gross_annual_premium"] > 0
    quoted_category = resp.json()["categories"][0]
    assert quoted_category["product"] == "Bronze"
    assert quoted_category["network"] == "Net A"
    assert quoted_category["tpa"] == "TPA X"


def test_without_a_benefits_category_pick_or_a_prior_quote_nothing_auto_computes(client, rate_card_files):
    _upload_rate_cards(client, rate_card_files)
    case_id = _make_case(client)  # census category A has no matching benefit plan/pick

    resp = client.get(f"/cases/{case_id}/new-business-quote")
    assert resp.status_code == 404


def test_auto_requote_prefers_the_benefits_tab_pick_over_a_stale_prior_quote(client, rate_card_files):
    # The Benefits tab is the source of truth going forward - if it's
    # updated after an earlier manual quote, a later auto re-quote should
    # pick up the new value rather than keep reusing the old one.
    _upload_rate_cards(client, rate_card_files)
    case_id = _make_case(client)

    resp = client.post(
        f"/cases/{case_id}/new-business-quote",
        json={"categories": [{"category": "A", "product": "Platinum", "network": "Net A", "tpa": "TPA X", "variant_selections": {}}]},
    )
    assert resp.status_code == 200

    _insert_existing_plan_with_nb_pick(client, case_id, "A", product="Bronze", network="Net A", tpa="TPA X")

    # Trigger via a real re-upload of the census.
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Employee Ref", "Category", "Age", "Gender", "Marital Status", "Relation", "Emirate"])
    ws.append(["E1", "A", 30, "M", "single", "employee", "Dubai"])
    ws.append(["E2", "A", 28, "F", "married", "spouse", "Dubai"])

    import io

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    resp = client.post(f"/cases/{case_id}/census", files={"file": ("census.xlsx", buf, "application/octet-stream")})
    assert resp.status_code == 200

    resp = client.get(f"/cases/{case_id}/new-business-quotes")
    quotes = resp.json()
    assert len(quotes) == 2  # the manual quote, plus one auto-triggered by the census re-upload
    latest = quotes[0]  # ordered newest first
    assert latest["categories"][0]["product"] == "Bronze"  # the Benefits tab's own pick won, not the stale prior quote


def test_saving_a_categorys_nb_pick_on_the_benefits_tab_auto_computes_the_quote_immediately(client, rate_card_files):
    # Saving Product/Network/TPA on a Benefits tab category card is itself
    # the input that unlocks auto-quoting - the underwriter shouldn't have
    # to also re-upload the census just to see the quote appear.
    _upload_rate_cards(client, rate_card_files)
    case_id = _make_case(client)  # already has a census, per _make_case
    plan_id = _insert_existing_plan_with_nb_pick(client, case_id, "A")  # no product/network/tpa yet

    resp = client.get(f"/cases/{case_id}/new-business-quote")
    assert resp.status_code == 404

    resp = client.put(
        f"/cases/{case_id}/benefits/{plan_id}/summary",
        json={"fields": {}, "nb_product": "Bronze", "nb_network": "Net A", "nb_tpa": "TPA X"},
    )
    assert resp.status_code == 200

    resp = client.get(f"/cases/{case_id}/new-business-quote")
    assert resp.status_code == 200
    assert resp.json()["categories"][0]["product"] == "Bronze"


def test_new_business_quote_by_tier_covers_every_product_and_case_totals(client, rate_card_files):
    _upload_rate_cards(client, rate_card_files)
    case_id = _make_case(client)

    resp = client.post(
        f"/cases/{case_id}/new-business-quote",
        json={"categories": [{"category": "A", "product": "Bronze", "network": "Net A", "tpa": "TPA X", "variant_selections": {}}]},
    )
    assert resp.status_code == 200

    resp = client.get(f"/cases/{case_id}/new-business-quote/by-tier")
    assert resp.status_code == 200
    by_tier = resp.json()
    assert [r["product"] for r in by_tier] == ["Platinum", "Gold", "Silver", "Bronze"]
    bronze = next(r for r in by_tier if r["product"] == "Bronze")
    platinum = next(r for r in by_tier if r["product"] == "Platinum")
    assert bronze["case_gross_annual_premium"] == pytest.approx(4200.0 / (1 - 0.265), rel=1e-6)
    assert platinum["case_gross_annual_premium"] > bronze["case_gross_annual_premium"]


def test_new_business_quote_by_tier_404s_without_a_prior_quote(client, rate_card_files):
    _upload_rate_cards(client, rate_card_files)
    case_id = _make_case(client)

    resp = client.get(f"/cases/{case_id}/new-business-quote/by-tier")
    assert resp.status_code == 404
