"""Basic case create/update round-trip - existing_insurer specifically,
since it's a real Case column (used for the New Business tier-preference
suggestion, see census-categories) that wasn't actually settable through
either POST /cases or PATCH /cases/{id} until now.
"""


def test_create_case_accepts_existing_insurer(client):
    resp = client.post(
        "/cases",
        json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading", "existing_insurer": "Allianz"},
    )
    assert resp.status_code == 200
    assert resp.json()["existing_insurer"] == "Allianz"


def test_update_case_sets_existing_insurer(client):
    resp = client.post("/cases", json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"})
    case_id = resp.json()["id"]
    assert resp.json()["existing_insurer"] is None

    resp = client.patch(f"/cases/{case_id}", json={"existing_insurer": "Cigna Global Care"})
    assert resp.status_code == 200
    assert resp.json()["existing_insurer"] == "Cigna Global Care"
