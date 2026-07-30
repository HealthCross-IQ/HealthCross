from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.feedback.recalibration import (
    recalibrate_weights,
    recalibrate_zone_maternity_multipliers,
    recalibrate_zone_multipliers,
    recalibrate_zone_network_multipliers,
)
from app.models import db_models as models
from app.models import schemas

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/weights", response_model=List[schemas.WeightSetOut])
def list_weights(db: Session = Depends(get_db)):
    return db.query(models.ScoringWeightSet).order_by(models.ScoringWeightSet.version.desc()).all()


@router.post("/recalibrate", response_model=schemas.RecalibrationResult)
def recalibrate(db: Session = Depends(get_db)):
    active = db.query(models.ScoringWeightSet).filter_by(is_active=True).first()
    if not active:
        raise HTTPException(status_code=500, detail="No active weight set configured")

    outcomes = db.query(models.Outcome).filter(models.Outcome.profitable.isnot(None)).all()

    weight_samples = []
    zone_samples = []
    zone_maternity_samples = []
    zone_network_samples = []
    for outcome in outcomes:
        scorecard = outcome.scorecard
        weight_samples.append(
            {
                "demographic_risk": scorecard.demographic_risk,
                "claims_experience_risk": scorecard.claims_experience_risk,
                "benefit_richness_risk": scorecard.benefit_richness_risk,
                "industry_risk": scorecard.industry_risk,
                "profitable": outcome.profitable,
            }
        )
        details = scorecard.details or {}
        demographic_details = details.get("demographic", {})
        zone_mix = demographic_details.get("nationality_zone_mix")
        if zone_mix:
            zone_samples.append({"zone_mix": zone_mix, "profitable": outcome.profitable})

        zone_maternity_mix = demographic_details.get("zone_maternity_mix")
        if zone_maternity_mix:
            zone_maternity_samples.append({"zone_maternity_mix": zone_maternity_mix, "profitable": outcome.profitable})

        network_tier_score = details.get("network_tier_score")
        if zone_mix and network_tier_score is not None:
            zone_network_mix = {zone: fraction * network_tier_score for zone, fraction in zone_mix.items()}
            zone_network_samples.append({"zone_network_mix": zone_network_mix, "profitable": outcome.profitable})

    current_weights = {
        "w_demographic": active.w_demographic,
        "w_claims_experience": active.w_claims_experience,
        "w_benefit_richness": active.w_benefit_richness,
        "w_industry": active.w_industry,
    }
    # zone_4_other is a legacy column (see app/reference/nationality_zones.py -
    # there are only 3 zones now) - it isn't part of ALL_ZONES, so it's
    # deliberately excluded from recalibration and carried forward unchanged
    # below rather than passed through recalibrate_zone_multipliers.
    current_zone_multipliers = {
        "zone_1_asia": active.zone_1_asia_multiplier,
        "zone_2_middle_east": active.zone_2_middle_east_multiplier,
        "zone_3_europe_americas": active.zone_3_europe_americas_multiplier,
    }
    current_zone_maternity_multipliers = {
        "zone_1_asia": active.zone_1_asia_maternity_multiplier,
        "zone_2_middle_east": active.zone_2_middle_east_maternity_multiplier,
        "zone_3_europe_americas": active.zone_3_europe_americas_maternity_multiplier,
    }
    current_zone_network_multipliers = {
        "zone_1_asia": active.zone_1_asia_network_multiplier,
        "zone_2_middle_east": active.zone_2_middle_east_network_multiplier,
        "zone_3_europe_americas": active.zone_3_europe_americas_network_multiplier,
    }

    weight_result = recalibrate_weights(weight_samples, current_weights)
    zone_result = recalibrate_zone_multipliers(zone_samples, current_zone_multipliers)
    zone_maternity_result = recalibrate_zone_maternity_multipliers(zone_maternity_samples, current_zone_maternity_multipliers)
    zone_network_result = recalibrate_zone_network_multipliers(zone_network_samples, current_zone_network_multipliers)

    any_recalibrated = any(
        r["recalibrated"] for r in (weight_result, zone_result, zone_maternity_result, zone_network_result)
    )
    if not any_recalibrated:
        reason = (
            weight_result.get("reason")
            or zone_result.get("reason")
            or zone_maternity_result.get("reason")
            or zone_network_result.get("reason")
        )
        return schemas.RecalibrationResult(recalibrated=False, reason=reason)

    new_weights = weight_result["weights"] if weight_result["recalibrated"] else current_weights
    new_zone_multipliers = zone_result["multipliers"] if zone_result["recalibrated"] else current_zone_multipliers
    new_zone_maternity_multipliers = (
        zone_maternity_result["multipliers"] if zone_maternity_result["recalibrated"] else current_zone_maternity_multipliers
    )
    new_zone_network_multipliers = (
        zone_network_result["multipliers"] if zone_network_result["recalibrated"] else current_zone_network_multipliers
    )

    active.is_active = False
    new_version = models.ScoringWeightSet(
        version=active.version + 1,
        is_active=True,
        trained_sample_size=len(weight_samples),
        training_metrics={
            "weights": weight_result.get("metrics"),
            "zone_multipliers": zone_result.get("metrics"),
            "zone_maternity_multipliers": zone_maternity_result.get("metrics"),
            "zone_network_multipliers": zone_network_result.get("metrics"),
        },
        notes="Auto-recalibrated from case outcomes",
        w_demographic=new_weights["w_demographic"],
        w_claims_experience=new_weights["w_claims_experience"],
        w_benefit_richness=new_weights["w_benefit_richness"],
        w_industry=new_weights["w_industry"],
        zone_1_asia_multiplier=new_zone_multipliers["zone_1_asia"],
        zone_2_middle_east_multiplier=new_zone_multipliers["zone_2_middle_east"],
        zone_3_europe_americas_multiplier=new_zone_multipliers["zone_3_europe_americas"],
        zone_4_other_multiplier=active.zone_4_other_multiplier,
        zone_1_asia_maternity_multiplier=new_zone_maternity_multipliers["zone_1_asia"],
        zone_2_middle_east_maternity_multiplier=new_zone_maternity_multipliers["zone_2_middle_east"],
        zone_3_europe_americas_maternity_multiplier=new_zone_maternity_multipliers["zone_3_europe_americas"],
        zone_1_asia_network_multiplier=new_zone_network_multipliers["zone_1_asia"],
        zone_2_middle_east_network_multiplier=new_zone_network_multipliers["zone_2_middle_east"],
        zone_3_europe_americas_network_multiplier=new_zone_network_multipliers["zone_3_europe_americas"],
    )
    db.add(new_version)
    db.commit()
    db.refresh(new_version)

    return schemas.RecalibrationResult(
        recalibrated=True,
        new_weight_set=new_version,
        metrics={
            "weights": weight_result.get("metrics"),
            "zone_multipliers": zone_result.get("metrics"),
            "zone_maternity_multipliers": zone_maternity_result.get("metrics"),
            "zone_network_multipliers": zone_network_result.get("metrics"),
        },
    )
