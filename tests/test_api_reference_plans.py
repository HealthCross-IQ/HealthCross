"""Tests for the standalone international/local insurer reference library
(app/api/routes_reference_plans.py) - upload once, compare any combination
dynamically. Uses the repo's existing real Maxmed fixture PDFs (already
used by test_api_benefits_labeled_row.py) to exercise the labeled-row
branch of the upload fallback chain end-to-end; the Bupa/Cigna/Allianz/MSH
branches were verified manually against real client documents that aren't
committed here (real employee PII, and Cigna's needs a slow OCR fallback -
see app/ingestion/international_tob.py's module docstring).
"""
from pathlib import Path

from app.ingestion.international_tob import _is_non_benefit_section, _looks_garbled

FIXTURES = Path(__file__).parent / "fixtures"
GOLD_CATEGORY_A = FIXTURES / "Table_of_Benefits_Maxmed_Neuron_Gold_Group_Category_A.pdf"
BRONZE_CATEGORY_B = FIXTURES / "Table_of_Benefits_Maxmed_Neuron_Bronze_Group_Category_B.pdf"


def test_looks_garbled_flags_text_with_no_common_english_words():
    garbled = "GHM>7%M[\\f%Ubb^_Xg%\\f%\\agXaWXW%Tf%T%Zh\\WX " * 10
    assert _looks_garbled(garbled) is True


def test_looks_garbled_false_for_real_english_and_short_text():
    real_text = "We pay for the following Treatment and it is covered per the plan for the whole family. " * 5
    assert _looks_garbled(real_text) is False
    assert _looks_garbled("short") is False  # below the length threshold - not enough signal either way


def test_is_non_benefit_section_flags_premium_and_quotation_sections():
    assert _is_non_benefit_section("PREMIUM CALCULATION") is True
    assert _is_non_benefit_section("MEDICAL INSURANCE - QUOTATION SUMMARY AND TERMS") is True
    assert _is_non_benefit_section("HEALTHCARE BENEFITS") is False


def test_upload_labeled_row_pdf_creates_one_reference_plan(client):
    with open(GOLD_CATEGORY_A, "rb") as f:
        resp = client.post(
            "/reference-plans?insurer_name=Max Health",
            files={"file": (GOLD_CATEGORY_A.name, f, "application/pdf")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["insurer_name"] == "Max Health"
    assert body[0]["plan_label"] == "MAXMED Neuron GOLD GROUP"
    assert len(body[0]["benefit_rows"]) > 10
    assert any("dental limit" in r["label"].lower() for r in body[0]["benefit_rows"])


def test_upload_labeled_row_pdf_honors_explicit_plan_label(client):
    with open(GOLD_CATEGORY_A, "rb") as f:
        resp = client.post(
            "/reference-plans?insurer_name=Max Health&plan_label=Gold",
            files={"file": (GOLD_CATEGORY_A.name, f, "application/pdf")},
        )
    assert resp.json()[0]["plan_label"] == "Gold"


def test_list_reference_plans_omits_benefit_rows_but_reports_row_count(client):
    with open(GOLD_CATEGORY_A, "rb") as f:
        client.post("/reference-plans?insurer_name=Max Health", files={"file": (GOLD_CATEGORY_A.name, f, "application/pdf")})

    resp = client.get("/reference-plans")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert "benefit_rows" not in body[0]
    assert body[0]["row_count"] > 10


def test_delete_reference_plan(client):
    with open(GOLD_CATEGORY_A, "rb") as f:
        created = client.post("/reference-plans?insurer_name=Max Health", files={"file": (GOLD_CATEGORY_A.name, f, "application/pdf")}).json()
    plan_id = created[0]["id"]

    resp = client.delete(f"/reference-plans/{plan_id}")
    assert resp.status_code == 200
    assert client.get("/reference-plans").json() == []


def test_compare_two_plans_returns_union_of_labels_by_section(client):
    with open(GOLD_CATEGORY_A, "rb") as f:
        gold = client.post("/reference-plans?insurer_name=Max Health&plan_label=Gold", files={"file": (GOLD_CATEGORY_A.name, f, "application/pdf")}).json()[0]
    with open(BRONZE_CATEGORY_B, "rb") as f:
        bronze = client.post("/reference-plans?insurer_name=Max Health&plan_label=Bronze", files={"file": (BRONZE_CATEGORY_B.name, f, "application/pdf")}).json()[0]

    resp = client.get(f"/reference-plans/compare?ids={gold['id']},{bronze['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert [p["plan_label"] for p in body["plans"]] == ["Gold", "Bronze"]

    all_rows = [row for section in body["sections"] for row in section["rows"]]
    dental_row = next(row for row in all_rows if "dental limit" in row["label"].lower())
    # Both plans have a real value against the same (matched) label.
    assert all(v is not None for v in dental_row["values"].values())

    # The union of labels across both plans must be at least as large as
    # either plan's own row count - a label unique to one plan still gets
    # its own row (with a null on the other plan's side) rather than being
    # dropped or forced to match something it isn't.
    assert len(all_rows) >= max(len(gold["benefit_rows"]), len(bronze["benefit_rows"]))


def test_compare_missing_plan_id_404s(client):
    resp = client.get("/reference-plans/compare?ids=999999")
    assert resp.status_code == 404


def test_compare_requires_at_least_one_id(client):
    resp = client.get("/reference-plans/compare?ids=")
    assert resp.status_code == 400
