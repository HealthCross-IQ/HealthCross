from typing import List, Union

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.ingestion.benefits import parse_table_of_benefits
from app.ingestion.benefits_ocr import is_scanned_pdf, parse_benefits_pdf_ocr
from app.ingestion.benefits_pdf import parse_benefits_pdf, to_benefit_plan_fields
from app.ingestion.census import parse_census
from app.ingestion.claims import parse_claims
from app.ingestion.claims_report import parse_claims_report
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

    # A re-upload replaces the case's census entirely rather than piling on
    # top of a previous one - otherwise re-uploading the same file (e.g.
    # after fixing a typo) silently multiplies the member count.
    db.query(models.CensusRecord).filter_by(case_id=case.id).delete()

    records = [models.CensusRecord(case_id=case.id, **row) for row in parsed]
    db.add_all(records)
    db.commit()
    for record in records:
        db.refresh(record)
    return records


@router.post("/{case_id}/benefits", response_model=List[schemas.BenefitPlanOut])
def upload_benefits(case_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    is_pdf = file.filename.lower().endswith(".pdf")

    try:
        if is_pdf and is_scanned_pdf(file.file):
            ocr_result = parse_benefits_pdf_ocr(file.file, file.filename)
            plans = [
                models.BenefitPlan(
                    case_id=case.id,
                    plan_name="OCR extract (verify against source)",
                    source_format="pdf-ocr",
                    standard_summary=ocr_result["summary"],
                    raw_ocr_text=ocr_result["raw_ocr_text"],
                )
            ]
        elif is_pdf:
            tier_summaries = parse_benefits_pdf(file.file, file.filename)
            plans = [
                models.BenefitPlan(case_id=case.id, **to_benefit_plan_fields(tier, summary))
                for tier, summary in tier_summaries.items()
            ]
        else:
            parsed = parse_table_of_benefits(file.file, file.filename)
            plans = [models.BenefitPlan(case_id=case.id, source_format=file.filename.rsplit(".", 1)[-1].lower(), **row) for row in parsed]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse table of benefits: {exc}")
    if not plans:
        raise HTTPException(status_code=400, detail="No benefit plans found in file")

    # Replace, not accumulate - see the census upload for why.
    db.query(models.BenefitPlan).filter_by(case_id=case.id).delete()

    db.add_all(plans)
    db.commit()
    for plan in plans:
        db.refresh(plan)
    return plans


@router.post("/{case_id}/claims", response_model=Union[List[schemas.ClaimsRecordOut], schemas.ClaimsReportOut])
def upload_claims(case_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    is_pdf = file.filename.lower().endswith(".pdf")

    if is_pdf:
        try:
            parsed = parse_claims_report(file.file, file.filename)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not parse claims report: {exc}")

        # parse_claims_report also returns intermediate fields (e.g. the
        # opening/closing gender split) that ClaimsReport doesn't store as
        # its own columns - opening_members/closing_members already carry
        # the totals that matter downstream.
        report_fields = {k: v for k, v in parsed.items() if k in models.ClaimsReport.__table__.columns.keys()}
        # Replace, not accumulate - see the census upload for why.
        db.query(models.ClaimsReport).filter_by(case_id=case.id).delete()
        report = models.ClaimsReport(case_id=case.id, **report_fields)
        db.add(report)
        db.commit()
        db.refresh(report)
        return report

    try:
        parsed = parse_claims(file.file, file.filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse claims file: {exc}")

    # Replace, not accumulate - see the census upload for why.
    db.query(models.ClaimsRecord).filter_by(case_id=case.id).delete()

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
