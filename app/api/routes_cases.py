from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.ingestion.benefits import parse_table_of_benefits
from app.ingestion.census import parse_census
from app.ingestion.claims import parse_claims
from app.ingestion.plan_details import parse_plan_details
from app.models import db_models as models
from app.models import schemas

router = APIRouter(prefix="/cases", tags=["cases"])


def _get_case_or_404(db: Session, case_id: int) -> models.Case:
    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.post("", response_model=schemas.CaseOut)
def create_case(payload: schemas.CaseCreate, db: Session = Depends(get_db)):
    case = models.Case(**payload.model_dump())
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


@router.get("", response_model=List[schemas.CaseOut])
def list_cases(db: Session = Depends(get_db)):
    return db.query(models.Case).order_by(models.Case.submitted_at.desc()).all()


@router.get("/{case_id}", response_model=schemas.CaseOut)
def get_case(case_id: int, db: Session = Depends(get_db)):
    return _get_case_or_404(db, case_id)


@router.post("/{case_id}/census", response_model=List[schemas.CensusRecordOut])
def upload_census(case_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    try:
        parsed = parse_census(file.file, file.filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse census file: {exc}")
    if not parsed:
        raise HTTPException(status_code=400, detail="No census rows found in file")

    records = [models.CensusRecord(case_id=case.id, **row) for row in parsed]
    db.add_all(records)
    db.commit()
    for record in records:
        db.refresh(record)
    return records


@router.post("/{case_id}/benefits", response_model=List[schemas.BenefitPlanOut])
def upload_benefits(case_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    try:
        parsed = parse_table_of_benefits(file.file, file.filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse table of benefits: {exc}")
    if not parsed:
        raise HTTPException(status_code=400, detail="No benefit plans found in file")

    plans = [models.BenefitPlan(case_id=case.id, **row) for row in parsed]
    db.add_all(plans)
    db.commit()
    for plan in plans:
        db.refresh(plan)
    return plans


@router.post("/{case_id}/claims", response_model=List[schemas.ClaimsRecordOut])
def upload_claims(case_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    try:
        parsed = parse_claims(file.file, file.filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse claims file: {exc}")

    records = [models.ClaimsRecord(case_id=case.id, **row) for row in parsed]
    db.add_all(records)
    db.commit()
    for record in records:
        db.refresh(record)
    return records


@router.post("/{case_id}/plan-details", response_model=schemas.PlanDetailsOut)
def upload_plan_details(case_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    try:
        extracted = parse_plan_details(file.file, file.filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse plan details: {exc}")

    for field, value in extracted.items():
        setattr(case, field, value)
    db.commit()
    db.refresh(case)

    return schemas.PlanDetailsOut(
        broker_name=case.broker_name,
        industry=case.industry,
        existing_insurer=case.existing_insurer,
        years_with_existing_insurer=case.years_with_existing_insurer,
        target_premium=case.target_premium,
        claims_available=case.claims_available,
        renewal_date=case.renewal_date,
        region=case.region,
        updated_fields=sorted(extracted.keys()),
    )
