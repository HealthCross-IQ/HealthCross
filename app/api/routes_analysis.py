from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import db_models as models
from app.models import schemas
from app.reference.diagnosis_classification import classify_diagnosis_group, flag_diagnosis_group
from app.scoring.rules.benefits_summary import build_standard_benefit_summary
from app.scoring.rules.claims_projection import project_annual_claims

router = APIRouter(prefix="/cases", tags=["analysis"])


def _get_case_or_404(db: Session, case_id: int) -> models.Case:
    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _latest_claims_report(db: Session, case_id: int) -> models.ClaimsReport:
    report = (
        db.query(models.ClaimsReport)
        .filter_by(case_id=case_id)
        .order_by(models.ClaimsReport.created_at.desc())
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="No claims report uploaded for this case")
    return report


@router.get("/{case_id}/claims-report", response_model=schemas.ClaimsReportOut)
def get_claims_report(case_id: int, db: Session = Depends(get_db)):
    _get_case_or_404(db, case_id)
    return _latest_claims_report(db, case_id)


@router.get("/{case_id}/claims-projection", response_model=schemas.ClaimsProjectionOut)
def get_claims_projection(case_id: int, db: Session = Depends(get_db)):
    """Runs the standing burning-cost formula (see
    app/scoring/rules/claims_projection.py) against this case's latest
    claims report and current census member count.
    """
    case = _get_case_or_404(db, case_id)
    report = _latest_claims_report(db, case_id)

    census_count = len(case.census_records)
    if not census_count:
        raise HTTPException(status_code=400, detail="Case has no census data to size the projection against")

    monthly = report.monthly_paid or []
    full_months = [m["paid"] for m in monthly if not m.get("partial")]
    if len(full_months) < 6:
        raise HTTPException(
            status_code=400,
            detail=f"Need at least 6 full months of claims data, found {len(full_months)}",
        )
    six_months = full_months[:6]
    month_labels = [f"{m['month']} {m['year']}" for m in monthly if not m.get("partial")][:6]

    if not report.opening_members or not report.closing_members:
        raise HTTPException(status_code=400, detail="Claims report is missing opening/closing member counts")

    result = project_annual_claims(
        six_month_paid_claims=six_months,
        opening_members=report.opening_members,
        closing_members=report.closing_members,
        current_census_members=census_count,
    )
    result["months_used"] = month_labels
    return result


@router.get("/{case_id}/diagnosis-exposure", response_model=List[schemas.DiagnosisExposureRow])
def get_diagnosis_exposure(case_id: int, db: Session = Depends(get_db)):
    """Applies the standing chronic/high-exposure classification (see
    app/reference/diagnosis_classification.py) to this case's latest
    claims report's diagnosis breakdown.
    """
    _get_case_or_404(db, case_id)
    report = _latest_claims_report(db, case_id)

    rows = []
    for entry in report.diagnosis_breakdown or []:
        classification = classify_diagnosis_group(entry["label"])
        flags = flag_diagnosis_group(entry["value"], entry["count"], entry["ip_value"], entry["ip_count"])
        rows.append(
            {
                "label": entry["label"],
                "value": entry["value"],
                "count": entry["count"],
                "ip_value": entry["ip_value"],
                "ip_count": entry["ip_count"],
                "avg_per_claim": flags["avg_per_claim"],
                "ip_avg_per_claim": flags["ip_avg_per_claim"],
                "classification": classification["classification"],
                "high_exposure": classification["high_exposure"],
                "note": classification["note"],
                "flags": flags["flags"],
            }
        )
    rows.sort(key=lambda r: r["value"], reverse=True)
    return rows


@router.get("/{case_id}/benefits-summary")
def get_benefits_summary(case_id: int, db: Session = Depends(get_db)):
    """Renders every uploaded benefit plan/tier for this case in the fixed
    10-field standard format (see app/scoring/rules/benefits_summary.py).
    """
    case = _get_case_or_404(db, case_id)
    if not case.benefit_plans:
        raise HTTPException(status_code=404, detail="No table of benefits uploaded for this case")

    summaries = []
    for plan in case.benefit_plans:
        source = plan.standard_summary or {
            "annual_limit": f"USD {plan.annual_limit:,.0f}" if plan.annual_limit else None,
            "maternity_limit": f"USD {plan.maternity_limit:,.0f}" if plan.maternity_limit else None,
            "dental": "Covered" if plan.dental_covered else "Not covered",
            "optical": "Covered" if plan.optical_covered else "Not covered",
            "pre_existing_chronic_limit": "Covered" if plan.pre_existing_covered else "Not covered",
        }
        summaries.append({"plan_name": plan.plan_name, "summary": build_standard_benefit_summary(source)})
    return summaries
