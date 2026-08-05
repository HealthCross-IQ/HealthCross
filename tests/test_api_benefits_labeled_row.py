"""End-to-end test uploading the real "MAXMED Neuron" fixture PDFs through
the actual /cases/{id}/benefits endpoint (no mocking) - confirms the
labeled-row parser is reached and wired up correctly, and that the two
categories can be uploaded as separate files via mode=append (see
app/ingestion/labeled_row_benefits_pdf.py).
"""
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
GOLD_CATEGORY_A = FIXTURES / "Table_of_Benefits_Maxmed_Neuron_Gold_Group_Category_A.pdf"
BRONZE_CATEGORY_B = FIXTURES / "Table_of_Benefits_Maxmed_Neuron_Bronze_Group_Category_B.pdf"
CIGNA_SMARTCARE = FIXTURES / "Table_of_Benefits_Cigna_SmartCare_Annexure1.pdf"


def test_uploads_real_labeled_row_pdf_end_to_end(client):
    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    with open(GOLD_CATEGORY_A, "rb") as f:
        resp = client.post(
            f"/cases/{case_id}/benefits",
            files={"file": (GOLD_CATEGORY_A.name, f, "application/pdf")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["plan_name"] == "MAXMED Neuron GOLD GROUP"
    assert body[0]["category"] == "A"
    assert body[0]["network_type"] == "Neuron General Plus"
    assert body[0]["source_format"] == "pdf-labeled-row"
    assert body[0]["standard_summary"]["dental"] == "AED 3,500"


def test_uploads_both_categories_as_separate_files_with_append_mode(client):
    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    with open(GOLD_CATEGORY_A, "rb") as f:
        client.post(
            f"/cases/{case_id}/benefits?mode=append",
            files={"file": (GOLD_CATEGORY_A.name, f, "application/pdf")},
        )
    with open(BRONZE_CATEGORY_B, "rb") as f:
        resp = client.post(
            f"/cases/{case_id}/benefits?mode=append",
            files={"file": (BRONZE_CATEGORY_B.name, f, "application/pdf")},
        )
    assert resp.status_code == 200

    from app.models import db_models as models

    db = client.db_session_local()
    existing_plans = db.query(models.BenefitPlan).filter_by(case_id=case_id, role="existing").all()
    db.close()
    plans_by_category = {p.category: p for p in existing_plans}
    assert set(plans_by_category.keys()) == {"A", "B"}
    assert plans_by_category["A"].network_type == "Neuron General Plus"
    assert plans_by_category["B"].network_type == "Neuron General"


def test_a_cigna_smartcare_document_falls_through_to_the_generic_table_parser(client):
    # Regression test: this real file's title line and one of its 85+ real
    # benefit rows used to coincidentally match this labeled-row parser's
    # own detection (see test_ingestion_labeled_row_benefits_pdf.py), giving
    # a near-empty summary instead of reaching the generic label/value/
    # description table parser that actually understands this document's
    # narrative/clarifications layout.
    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]

    with open(CIGNA_SMARTCARE, "rb") as f:
        resp = client.post(
            f"/cases/{case_id}/benefits",
            files={"file": (CIGNA_SMARTCARE.name, f, "application/pdf")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["source_format"] == "pdf-generic-table"
    assert body[0]["annual_limit"] == 3_673_000.0
    assert body[0]["network_type"] == "Cigna"
    summary = body[0]["standard_summary"]
    assert summary["area_of_cover"] == "Worldwide excluding USA"
    assert summary["pre_existing_chronic_limit"] == "Covered"
