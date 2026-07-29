from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import db_models as models
from app.models import schemas

router = APIRouter(prefix="/cases", tags=["feedback"])

# Actual loss ratio at/below this threshold is considered profitable for the book.
PROFITABLE_LOSS_RATIO_THRESHOLD = 0.85


@router.post("/{case_id}/outcome", response_model=schemas.OutcomeOut)
def record_outcome(case_id: int, payload: schemas.OutcomeCreate, db: Session = Depends(get_db)):
    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    scorecard = (
        db.query(models.Scorecard)
        .filter_by(case_id=case_id)
        .order_by(models.Scorecard.created_at.desc())
        .first()
    )
    if not scorecard:
        raise HTTPException(status_code=400, detail="Case must be scored before recording an outcome")

    profitable = None
    if payload.actual_loss_ratio is not None:
        profitable = payload.actual_loss_ratio <= PROFITABLE_LOSS_RATIO_THRESHOLD

    outcome = db.query(models.Outcome).filter_by(case_id=case_id).first()
    if outcome:
        outcome.bound = payload.bound
        outcome.final_premium = payload.final_premium
        outcome.actual_loss_ratio = payload.actual_loss_ratio
        outcome.profitable = profitable
        outcome.scorecard_id = scorecard.id
    else:
        outcome = models.Outcome(
            case_id=case_id,
            scorecard_id=scorecard.id,
            bound=payload.bound,
            final_premium=payload.final_premium,
            actual_loss_ratio=payload.actual_loss_ratio,
            profitable=profitable,
        )
        db.add(outcome)

    case.status = models.CaseStatus.BOUND if payload.bound else models.CaseStatus.DECLINED
    db.commit()
    db.refresh(outcome)
    return outcome
