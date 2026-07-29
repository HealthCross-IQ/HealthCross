from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import db_models as models
from app.models import schemas
from app.scoring.engine import ScoringWeights, compute_scorecard

router = APIRouter(prefix="/cases", tags=["scoring"])


def _active_weight_set(db: Session) -> models.ScoringWeightSet:
    weight_set = db.query(models.ScoringWeightSet).filter_by(is_active=True).first()
    if not weight_set:
        raise HTTPException(status_code=500, detail="No active scoring weight set configured")
    return weight_set


def _census_dicts(case: models.Case) -> List[dict]:
    return [
        {
            "age": c.age,
            "gender": c.gender,
            "marital_status": c.marital_status,
            "relation": c.relation,
            "nationality_zone": c.nationality_zone,
        }
        for c in case.census_records
    ]


def _benefit_dicts(case: models.Case) -> List[dict]:
    return [
        {
            "annual_limit": b.annual_limit,
            "deductible": b.deductible,
            "co_insurance_pct": b.co_insurance_pct,
            "room_type": b.room_type,
            "network_type": b.network_type,
            "maternity_covered": b.maternity_covered,
            "dental_covered": b.dental_covered,
            "optical_covered": b.optical_covered,
            "pre_existing_covered": b.pre_existing_covered,
            "chronic_covered": b.chronic_covered,
            "member_count": b.member_count,
        }
        for b in case.benefit_plans
    ]


def _claims_dicts(case: models.Case) -> List[dict]:
    return [{"amount_paid": c.amount_paid, "policy_year": c.policy_year} for c in case.claims_records]


@router.post("/{case_id}/score", response_model=schemas.ScorecardOut)
def score_case(case_id: int, payload: schemas.ScoreRequest, db: Session = Depends(get_db)):
    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    census = _census_dicts(case)
    if not census:
        raise HTTPException(status_code=400, detail="Case has no census data to score")

    benefit_plans = _benefit_dicts(case)
    if not benefit_plans:
        raise HTTPException(status_code=400, detail="Case has no table of benefits to score")

    claims = _claims_dicts(case)

    weight_set = _active_weight_set(db)
    weights = ScoringWeights(
        w_demographic=weight_set.w_demographic,
        w_claims_experience=weight_set.w_claims_experience,
        w_benefit_richness=weight_set.w_benefit_richness,
        w_industry=weight_set.w_industry,
        zone_multipliers={
            "zone_1_asia": weight_set.zone_1_asia_multiplier,
            "zone_2_middle_east": weight_set.zone_2_middle_east_multiplier,
            "zone_3_europe_americas": weight_set.zone_3_europe_americas_multiplier,
            "zone_4_other": weight_set.zone_4_other_multiplier,
        },
    )

    result = compute_scorecard(
        census=census,
        benefit_plans=benefit_plans,
        claims=claims,
        industry=case.industry,
        weights=weights,
        estimated_annual_premium=payload.estimated_annual_premium,
    )

    scorecard = models.Scorecard(
        case_id=case.id,
        weight_set_id=weight_set.id,
        demographic_risk=result["demographic_risk"],
        claims_experience_risk=result["claims_experience_risk"],
        benefit_richness_risk=result["benefit_richness_risk"],
        industry_risk=result["industry_risk"],
        credibility_factor=result["credibility_factor"],
        composite_score=result["composite_score"],
        risk_tier=result["risk_tier"],
        suggested_loading_pct=result["suggested_loading_pct"],
        details=result["details"],
    )
    db.add(scorecard)
    case.status = models.CaseStatus.SCORED
    db.commit()
    db.refresh(scorecard)
    return scorecard


@router.get("/{case_id}/scorecards", response_model=List[schemas.ScorecardOut])
def list_scorecards(case_id: int, db: Session = Depends(get_db)):
    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return (
        db.query(models.Scorecard)
        .filter_by(case_id=case_id)
        .order_by(models.Scorecard.created_at.desc())
        .all()
    )


@router.get("/{case_id}/scorecard", response_model=schemas.ScorecardOut)
def get_latest_scorecard(case_id: int, db: Session = Depends(get_db)):
    scorecard = (
        db.query(models.Scorecard)
        .filter_by(case_id=case_id)
        .order_by(models.Scorecard.created_at.desc())
        .first()
    )
    if not scorecard:
        raise HTTPException(status_code=404, detail="No scorecard found for this case")
    return scorecard
