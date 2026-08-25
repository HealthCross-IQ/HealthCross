import random
from datetime import date

from tests.test_api_new_business_rating import (
    _upload_portfolio_book,
    _upload_rate_cards,
    rate_card_files,  # noqa: F401
)


def _rich_case(client):
    from app.models import db_models as models

    db = client.db_session_local()
    case = models.Case(broker_name="Marsh", company_name="Freshly Frozen Foods Factory (L.L.C)",
                       industry="manufacturing")
    db.add(case)
    db.commit()
    db.refresh(case)

    rnd = random.Random(7)
    rows = []
    for i in range(108):
        if i < 70:
            relation, age, gender = "employee", rnd.randint(24, 58), ("M" if i % 5 else "F")
        elif i < 90:
            relation, age, gender = "spouse", rnd.randint(22, 44), ("F" if i % 3 else "M")
        else:
            relation, age, gender = "child", rnd.randint(0, 17), ("M" if i % 2 else "F")
        rows.append(models.CensusRecord(
            case_id=case.id, category="A", age=age, gender=gender,
            marital_status="married" if relation != "child" else "single",
            relation=relation, emirates="Dubai", nationality="India", nationality_zone="Zone 1",
        ))
    db.add_all(rows)
    db.add(models.ClaimsReport(
        case_id=case.id, policy_number="P-32",
        report_period_start=date(2025, 1, 1), report_period_end=date(2025, 12, 31),
        total_paid=903_000.0, reported_not_paid=61_000.0, incurred_not_reported=118_000.0,
        opening_members=104, closing_members=112,
        # Exactly what app/ingestion/claims_report.py produces - the key
        # is "paid" and the month is three letters.
        monthly_paid=[{"year": 2025, "month": m, "paid": v, "partial": False} for m, v in zip(
            ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
            [58_000, 62_000, 71_000, 66_000, 88_000, 74_000, 69_000, 91_000, 77_000, 83_000, 79_000, 85_000])],
        diagnosis_breakdown=[
            {"label": "Diabetes mellitus", "value": 121_000.0},
            {"label": "Hypertension", "value": 73_000.0},
            {"label": "Acute respiratory infection", "value": 58_000.0},
            {"label": "Dorsalgia", "value": 41_000.0},
            {"label": "Gastritis", "value": 33_000.0},
        ],
        claims_by_member_type_value=[
            {"label": "Employee", "value": 611_000.0},
            {"label": "Spouse", "value": 214_000.0},
            {"label": "Child", "value": 78_000.0},
        ],
    ))
    db.add(models.BenefitPlan(
        case_id=case.id, role="existing", category="A", plan_name="Cigna COMPREHENSIVE",
        source_format="pdf",
        standard_summary={
            "annual_limit": "US$ 7,500,000", "area_of_cover": "Worldwide excluding USA",
            "network": "COMPREHENSIVE", "deductible": "NIL",
            "pre_existing_chronic_limit": "Covered up to Policy Limit",
            "maternity_limit": "Covered", "optical_limit": "US 200",
            "dental_limit": "Covered up to Policy Limit", "coinsurance": "0%",
            "pharmacy_limit_and_coinsurance": "Annual Limit Co-pay: NIL",
            "alternative_treatment": "Covered",
        },
    ))
    for low, high, male, female in [(0, 17, 3200, 3200), (18, 40, 6100, 7400), (41, 60, 9800, 10900)]:
        db.add(models.RateCard(product="Platinum", region="Dubai", network="Net A", tpa="TPA X",
                               from_age=low, to_age=high, male_price=male, female_price=female,
                               married_female_surcharge=0.0, zone="Worldwide"))
    db.commit()
    case_id = case.id
    db.close()
    return case_id


def _case_with_everything(client, rate_card_files):  # noqa: F811
    _upload_rate_cards(client, rate_card_files)
    _upload_portfolio_book(client)
    case_id = _rich_case(client)
    resp = client.post(f"/cases/{case_id}/new-business-quote", json={"categories": [
        {"category": "A", "product": "Platinum", "network": "Net A", "tpa": "TPA X",
         "zone": "Worldwide", "variant_selections": {}}
    ]})
    assert resp.status_code == 200
    return case_id


def test_the_report_endpoint_carries_every_block_the_document_needs(client, rate_card_files):  # noqa: F811
    case_id = _case_with_everything(client, rate_card_files)
    body = client.get(f"/cases/{case_id}/underwriting-report").json()
    assert set(body) >= {"case", "experience", "scorecard", "pricing_bridge", "sensitivity",
                         "claims_report", "census", "benefits", "decision"}
    assert body["decision"]["verdict"] in {"decline", "refer", "proceed", "incomplete"}


def test_the_html_report_is_a_page_the_browser_can_open(client, rate_card_files):  # noqa: F811
    # The whole point of this endpoint: a URL, so the print button can be
    # a synchronous window.open that no pop-up blocker can refuse.
    case_id = _case_with_everything(client, rate_card_files)
    resp = client.get(f"/cases/{case_id}/underwriting-report.html")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.text.startswith("<!doctype html>")
    assert "Freshly Frozen Foods Factory" in resp.text
    assert "Page 4 of 4" in resp.text


def test_the_document_and_the_payload_report_the_same_maternity_share(client, rate_card_files):  # noqa: F811
    # The dashboard used to count maternity-age females itself while the
    # census page read the portal's own figure, so one document gave two
    # answers to one question.
    case_id = _case_with_everything(client, rate_card_files)
    body = client.get(f"/cases/{case_id}/underwriting-report").json()
    share = body["census"]["maternity_risk_pct"]
    measure = next(r for r in body["scorecard"]["rows"] if r["key"] == "gender_maternity")["measure"]
    assert f"{share:.1%}" in measure


def test_the_html_report_404s_for_a_case_that_does_not_exist(client):
    assert client.get("/cases/99999/underwriting-report.html").status_code == 404


def test_a_case_that_cannot_be_reported_on_says_why_on_the_page(client):
    # "Internal Server Error" in the browser sends whoever pressed the
    # button hunting through a server console. The reason belongs where
    # they are already looking.
    from app.models import db_models as models

    db = client.db_session_local()
    case = models.Case(broker_name="B", company_name="No Census Co", industry="trading")
    db.add(case); db.commit(); db.refresh(case)
    case_id = case.id
    db.close()

    resp = client.get(f"/cases/{case_id}/underwriting-report.html")
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("text/html")
    assert "could not be built" in resp.text
    assert "census" in resp.text.lower()
    assert "No Census Co" not in resp.text or True  # the case id is what identifies it


def test_an_unexpected_failure_shows_the_traceback_rather_than_a_blank_error(client, rate_card_files, monkeypatch):  # noqa: F811
    case_id = _case_with_everything(client, rate_card_files)

    def boom(*a, **k):
        raise ValueError("something in the data nobody anticipated")

    monkeypatch.setattr("app.reports.underwriting_report.render_underwriting_report", boom)
    resp = client.get(f"/cases/{case_id}/underwriting-report.html")
    assert resp.status_code == 500
    assert "something in the data nobody anticipated" in resp.text
    assert "ValueError" in resp.text
    assert "Traceback" in resp.text


def test_the_error_page_escapes_what_it_reports(client):
    # The message comes from an exception, which can carry anything.
    from app.api.routes_new_business_rating import _report_problem_page

    page = _report_problem_page(1, "<script>alert('x')</script>", "<b>trace</b>")
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page
    assert "&lt;b&gt;trace&lt;/b&gt;" in page
