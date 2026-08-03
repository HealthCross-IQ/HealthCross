"""Tests for the manual weight-adjustment and insurer-tier-preference admin
endpoints (app/api/routes_admin.py) - the "dynamic, adjustable from
portfolio analysis" mechanism, distinct from the automatic /recalibrate
loop tested in test_api_admin_recalibrate.py.
"""
from app.models import db_models as models


def test_patch_active_weights_creates_a_new_version_and_deactivates_the_old_one(client):
    db = client.db_session_local()
    original = db.query(models.ScoringWeightSet).filter_by(is_active=True).first()
    original_id = original.id
    db.close()

    resp = client.patch("/admin/weights/active", json={"zone_1_asia_multiplier": 0.8, "notes": "Portfolio review Q3"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["zone_1_asia_multiplier"] == 0.8
    assert body["version"] == original.version + 1
    assert body["is_active"] is True
    assert body["notes"] == "Portfolio review Q3"

    db = client.db_session_local()
    stale = db.get(models.ScoringWeightSet, original_id)
    assert stale.is_active is False
    db.close()


def test_patch_active_weights_carries_forward_unset_fields_unchanged(client):
    db = client.db_session_local()
    active = db.query(models.ScoringWeightSet).filter_by(is_active=True).first()
    original_zone_1_asia = active.zone_1_asia_multiplier
    db.close()

    resp = client.patch("/admin/weights/active", json={"overage_loading_cap": 0.25})
    assert resp.status_code == 200
    body = resp.json()
    assert body["overage_loading_cap"] == 0.25
    # Untouched fields keep whatever value the active weight set already had.
    assert body["zone_1_asia_multiplier"] == original_zone_1_asia
    assert body["overage_age_threshold"] == 50


def test_patch_active_weights_defaults_notes_when_not_provided(client):
    resp = client.patch("/admin/weights/active", json={"overage_age_threshold": 55})
    assert resp.status_code == 200
    assert resp.json()["notes"] == "Manual adjustment"


def test_list_insurer_tier_preferences_returns_the_seeded_defaults(client):
    db = client.db_session_local()
    db.add(models.InsurerTierPreference(insurer_name="Allianz", suggested_product="Platinum"))
    db.add(models.InsurerTierPreference(insurer_name="Daman", suggested_product="Bronze"))
    db.commit()
    db.close()

    resp = client.get("/admin/insurer-tier-preferences")
    assert resp.status_code == 200
    body = {row["insurer_name"]: row["suggested_product"] for row in resp.json()}
    assert body == {"Allianz": "Platinum", "Daman": "Bronze"}


def test_upsert_insurer_tier_preference_creates_then_updates_in_place(client):
    resp = client.post("/admin/insurer-tier-preferences", json={"insurer_name": "Orient", "suggested_product": "Bronze"})
    assert resp.status_code == 200
    assert resp.json()["suggested_product"] == "Bronze"

    resp = client.post("/admin/insurer-tier-preferences", json={"insurer_name": "Orient", "suggested_product": "Silver"})
    assert resp.status_code == 200
    assert resp.json()["suggested_product"] == "Silver"

    resp = client.get("/admin/insurer-tier-preferences")
    assert len(resp.json()) == 1  # updated in place, not duplicated


def test_upsert_insurer_tier_preference_rejects_an_unknown_product(client):
    resp = client.post("/admin/insurer-tier-preferences", json={"insurer_name": "Orient", "suggested_product": "Diamond"})
    assert resp.status_code == 400


def test_delete_insurer_tier_preference(client):
    client.post("/admin/insurer-tier-preferences", json={"insurer_name": "Orient", "suggested_product": "Bronze"})
    resp = client.delete("/admin/insurer-tier-preferences/Orient")
    assert resp.status_code == 200
    assert client.get("/admin/insurer-tier-preferences").json() == []


def test_delete_missing_insurer_tier_preference_404s(client):
    resp = client.delete("/admin/insurer-tier-preferences/Not A Real Insurer")
    assert resp.status_code == 404
