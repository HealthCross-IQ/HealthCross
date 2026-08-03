import random

from app.models import db_models as models


def test_recalibrate_endpoint_succeeds_with_legacy_zone_4_column(client):
    """Regression test: recalibrate_zone_multipliers() only returns the
    current 3 zones (see app/reference/nationality_zones.py), but the DB
    still carries a legacy zone_4_other_multiplier column on
    ScoringWeightSet. The /admin/recalibrate route must carry that column
    forward from the previous active weight set rather than indexing into
    the (now 3-key) recalibration result, or it 500s with a KeyError.
    """
    db = client.db_session_local()
    active = db.query(models.ScoringWeightSet).filter_by(is_active=True).first()
    active_zone_4_other_multiplier = active.zone_4_other_multiplier

    random.seed(3)
    for _ in range(25):
        case = models.Case(broker_name="Broker", company_name="Co", industry="trading")
        db.add(case)
        db.flush()

        zone_2_fraction = random.uniform(0.0, 1.0)
        zone_mix = {
            "zone_1_asia": (1 - zone_2_fraction) * 0.6,
            "zone_2_middle_east": zone_2_fraction,
            "zone_3_europe_americas": (1 - zone_2_fraction) * 0.4,
        }
        profitable = zone_2_fraction < 0.5

        scorecard = models.Scorecard(
            case_id=case.id,
            weight_set_id=active.id,
            demographic_risk=random.uniform(0.8, 1.5),
            claims_experience_risk=random.uniform(0.8, 2.0),
            benefit_richness_risk=random.uniform(0.8, 1.3),
            industry_risk=random.uniform(0.85, 1.3),
            composite_score=50.0,
            risk_tier="Standard",
            suggested_loading_pct=10.0,
            details={"demographic": {"nationality_zone_mix": zone_mix}},
        )
        db.add(scorecard)
        db.flush()

        db.add(models.Outcome(case_id=case.id, scorecard_id=scorecard.id, bound=True, profitable=profitable))

    db.commit()
    db.close()

    resp = client.post("/admin/recalibrate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["recalibrated"] is True
    assert body["new_weight_set"]["zone_4_other_multiplier"] == active_zone_4_other_multiplier


def test_recalibrate_endpoint_carries_forward_overage_settings_unchanged(client):
    """Regression test: overage_age_threshold/overage_loading_cap aren't
    part of what /admin/recalibrate learns (see
    app/scoring/rules/demographic.py's overage loading), so a recalibration
    run must carry them forward from the previous active weight set rather
    than silently resetting them to the ScoringWeightSet column defaults.
    """
    db = client.db_session_local()
    active = db.query(models.ScoringWeightSet).filter_by(is_active=True).first()
    active.overage_age_threshold = 55
    active.overage_loading_cap = 0.33
    db.commit()

    random.seed(7)
    for _ in range(25):
        case = models.Case(broker_name="Broker", company_name="Co", industry="trading")
        db.add(case)
        db.flush()
        scorecard = models.Scorecard(
            case_id=case.id,
            weight_set_id=active.id,
            demographic_risk=random.uniform(0.8, 1.5),
            claims_experience_risk=random.uniform(0.8, 2.0),
            benefit_richness_risk=random.uniform(0.8, 1.3),
            industry_risk=random.uniform(0.85, 1.3),
            composite_score=50.0,
            risk_tier="Standard",
            suggested_loading_pct=10.0,
            details={},
        )
        db.add(scorecard)
        db.flush()
        db.add(models.Outcome(case_id=case.id, scorecard_id=scorecard.id, bound=True, profitable=random.random() < 0.5))
    db.commit()
    db.close()

    resp = client.post("/admin/recalibrate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["recalibrated"] is True
    assert body["new_weight_set"]["overage_age_threshold"] == 55
    assert body["new_weight_set"]["overage_loading_cap"] == 0.33


def test_recalibrate_endpoint_also_learns_zone_maternity_and_network_interactions(client):
    """End-to-end regression test for the zone x maternity and zone x
    network learned interaction effects: drives outcomes carrying
    zone_maternity_mix and network_tier_score through the real
    /admin/recalibrate route and confirms the new weight set comes back with
    all 6 new multiplier columns populated (no KeyError, no silent 500).
    """
    db = client.db_session_local()
    active = db.query(models.ScoringWeightSet).filter_by(is_active=True).first()

    random.seed(5)
    for _ in range(25):
        case = models.Case(broker_name="Broker", company_name="Co", industry="trading")
        db.add(case)
        db.flush()

        zone_2_maternity_fraction = random.uniform(0.0, 0.6)
        zone_mix = {"zone_1_asia": 0.3, "zone_2_middle_east": 0.4, "zone_3_europe_americas": 0.3}
        zone_maternity_mix = {
            "zone_1_asia": 0.05,
            "zone_2_middle_east": zone_2_maternity_fraction,
            "zone_3_europe_americas": 0.05,
        }
        network_tier_score = random.uniform(0.0, 1.0)
        profitable = zone_2_maternity_fraction < 0.3

        scorecard = models.Scorecard(
            case_id=case.id,
            weight_set_id=active.id,
            demographic_risk=random.uniform(0.8, 1.5),
            claims_experience_risk=random.uniform(0.8, 2.0),
            benefit_richness_risk=random.uniform(0.8, 1.3),
            industry_risk=random.uniform(0.85, 1.3),
            composite_score=50.0,
            risk_tier="Standard",
            suggested_loading_pct=10.0,
            details={
                "demographic": {"nationality_zone_mix": zone_mix, "zone_maternity_mix": zone_maternity_mix},
                "network_tier_score": network_tier_score,
            },
        )
        db.add(scorecard)
        db.flush()

        db.add(models.Outcome(case_id=case.id, scorecard_id=scorecard.id, bound=True, profitable=profitable))

    db.commit()
    db.close()

    resp = client.post("/admin/recalibrate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["recalibrated"] is True
    new_weight_set = body["new_weight_set"]
    for field in (
        "zone_1_asia_maternity_multiplier",
        "zone_2_middle_east_maternity_multiplier",
        "zone_3_europe_americas_maternity_multiplier",
        "zone_1_asia_network_multiplier",
        "zone_2_middle_east_network_multiplier",
        "zone_3_europe_americas_network_multiplier",
    ):
        assert field in new_weight_set
