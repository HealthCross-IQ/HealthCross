"""Tests for the standalone international/local insurer reference library
(app/api/routes_reference_plans.py) - upload once, compare any combination
dynamically. Uses the repo's existing real Maxmed fixture PDFs (already
used by test_api_benefits_labeled_row.py) to exercise the labeled-row
branch of the upload fallback chain end-to-end; the Bupa/Cigna/Allianz/MSH/
Sukoon/multi-tier (HealthCROSS Global's side-by-side "Gold - CAT A" /
"Gold - CAT B" layout, see extract_multi_tier_rows) branches were verified
manually against real client documents that aren't committed here (real
employee/client PII, and Cigna's needs a slow OCR fallback - see
app/ingestion/international_tob.py's module docstring).
"""
from pathlib import Path

from app.ingestion.international_tob import (
    _contained_in,
    _is_non_benefit_section,
    _looks_garbled,
    _looks_like_tier_name,
)
from app.reference.benefit_category_mapping import unify_currency_to_aed

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


def test_looks_like_tier_name_flags_category_and_plan_values():
    # Some insurers (e.g. Sukoon's local-market TOB) restate the plan/tier
    # name as a mini-header at the top of every section within one big
    # table, not just once in a document-wide header row - these need to
    # be recognized as banners rather than real benefit values.
    assert _looks_like_tier_name("Category 1") is True
    assert _looks_like_tier_name("Plan 2") is True
    assert _looks_like_tier_name("Tier A") is True
    assert _looks_like_tier_name("Covered") is False
    assert _looks_like_tier_name("1,000,000/-") is False


def test_contained_in_detects_nested_table_fragments():
    # A document whose automatic table detection finds a real table plus
    # redundant smaller sub-regions nested inside it (the same wrapped
    # cell text re-detected as its own tiny table).
    outer = (50, 100, 500, 700)
    inner = (60, 150, 300, 200)
    assert _contained_in(inner, outer) is True
    assert _contained_in(outer, inner) is False

    overlapping_not_contained = (400, 650, 600, 750)
    assert _contained_in(overlapping_not_contained, outer) is False


def test_unify_currency_to_aed_converts_usd_amounts():
    assert unify_currency_to_aed("US$ 7,500,000 per year of insurance") == (
        "US$ 7,500,000 (AED 27,543,750) per year of insurance"
    )
    assert unify_currency_to_aed("$ 1,000,000 per year of insurance") == (
        "$ 1,000,000 (AED 3,672,500) per year of insurance"
    )


def test_unify_currency_to_aed_leaves_aed_values_and_non_currency_text_alone():
    # Bupa's documents already state their own AED equivalent - adding a
    # second, redundant one would be confusing, not helpful.
    already_aed = "USD 4,700,000 (AED 17,260,750), GBP 3,500,000, EUR 4,200,000 each membership year"
    assert unify_currency_to_aed(already_aed) == already_aed
    assert unify_currency_to_aed("AED 1,000,000/-") == "AED 1,000,000/-"
    assert unify_currency_to_aed("Covered") == "Covered"
    assert unify_currency_to_aed(None) is None


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


def test_compare_maps_both_plans_onto_the_fixed_category_list(client):
    with open(GOLD_CATEGORY_A, "rb") as f:
        gold = client.post("/reference-plans?insurer_name=Max Health&plan_label=Gold", files={"file": (GOLD_CATEGORY_A.name, f, "application/pdf")}).json()[0]
    with open(BRONZE_CATEGORY_B, "rb") as f:
        bronze = client.post("/reference-plans?insurer_name=Max Health&plan_label=Bronze", files={"file": (BRONZE_CATEGORY_B.name, f, "application/pdf")}).json()[0]

    resp = client.get(f"/reference-plans/compare?ids={gold['id']},{bronze['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert [p["plan_label"] for p in body["plans"]] == ["Gold", "Bronze"]

    all_rows = [row for section in body["sections"] for row in section["rows"]]
    # Every one of the fixed 37 categories appears as its own row, in the
    # agreed order, regardless of whether either plan actually has a match.
    assert len(all_rows) == 37
    assert [row["label"] for row in all_rows][:2] == ["Annual/Indemnity Maximum", "Area of Cover"]

    dental_row = next(row for row in all_rows if row["label"] == "Dental Annual Limit")
    # Both plans word their dental limit differently ("DENTAL Dental Limit"
    # vs whatever Bronze's own wording is) but both map onto this one row.
    assert all(v is not None for v in dental_row["values"].values())

    # Real rows that don't match any of the 37 categories aren't dropped -
    # they're kept per plan, verbatim, in other_benefits.
    assert len(body["other_benefits"][str(gold["id"])]) > 0
    assert len(body["other_benefits"][str(bronze["id"])]) > 0


def test_compare_missing_plan_id_404s(client):
    resp = client.get("/reference-plans/compare?ids=999999")
    assert resp.status_code == 404


def test_compare_requires_at_least_one_id(client):
    resp = client.get("/reference-plans/compare?ids=")
    assert resp.status_code == 400
