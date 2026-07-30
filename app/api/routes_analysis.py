from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import db_models as models
from app.models import schemas
from app.reference.diagnosis_classification import classify_diagnosis_group, flag_diagnosis_group
from app.scoring.rules.benefits_comparison import compare_benefit_summaries, compare_benefit_value
from app.scoring.rules.benefits_summary import build_standard_benefit_summary
from app.scoring.rules.census_summary import census_demographic_summary
from app.scoring.rules.claims_projection import ClaimsProjectionAssumptions, project_annual_claims

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
def get_claims_projection(
    case_id: int,
    db: Session = Depends(get_db),
    credibility_pct: Optional[float] = None,
    inflation_pct: Optional[float] = None,
    ibnr_pct: Optional[float] = None,
    loading_pct: Optional[float] = None,
):
    """Runs the standing burning-cost formula (see
    app/scoring/rules/claims_projection.py) against this case's latest
    claims report and current census member count. Credibility (and the
    other assumptions) can be overridden per call via query params since
    credibility in particular is negotiated per insurer/case rather than
    fixed - the defaults on ClaimsProjectionAssumptions still apply when
    a param is omitted.
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

    defaults = ClaimsProjectionAssumptions()
    assumptions = ClaimsProjectionAssumptions(
        credibility_pct=credibility_pct if credibility_pct is not None else defaults.credibility_pct,
        inflation_pct=inflation_pct if inflation_pct is not None else defaults.inflation_pct,
        ibnr_pct=ibnr_pct if ibnr_pct is not None else defaults.ibnr_pct,
        loading_pct=loading_pct if loading_pct is not None else defaults.loading_pct,
    )

    result = project_annual_claims(
        six_month_paid_claims=six_months,
        opening_members=report.opening_members,
        closing_members=report.closing_members,
        current_census_members=census_count,
        assumptions=assumptions,
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


@router.get("/{case_id}/census-summary")
def get_census_summary(case_id: int, db: Session = Depends(get_db)):
    """Plain demographic breakdown of the uploaded census - age bands,
    gender, marital status, relation, and nationality-zone mix - as counts
    and percentages (see app/scoring/rules/census_summary.py). This is the
    underwriter-facing "what does this group look like" view, distinct from
    the risk-scoring engine's demographic multiplier.
    """
    case = _get_case_or_404(db, case_id)
    if not case.census_records:
        raise HTTPException(status_code=404, detail="No census uploaded for this case")

    census = [
        {
            "age": c.age,
            "gender": c.gender,
            "marital_status": c.marital_status,
            "relation": c.relation,
            "nationality_zone": c.nationality_zone,
            "nationality": c.nationality,
        }
        for c in case.census_records
    ]
    return census_demographic_summary(census)


def _benefit_summary(plan: models.BenefitPlan) -> dict:
    source = plan.standard_summary or {
        "annual_limit": f"USD {plan.annual_limit:,.0f}" if plan.annual_limit else None,
        "maternity_limit": f"USD {plan.maternity_limit:,.0f}" if plan.maternity_limit else None,
        "dental": "Covered" if plan.dental_covered else "Not covered",
        "optical": "Covered" if plan.optical_covered else "Not covered",
        "pre_existing_chronic_limit": "Covered" if plan.pre_existing_covered else "Not covered",
    }
    return build_standard_benefit_summary(source)


@router.get("/{case_id}/benefits-summary")
def get_benefits_summary(case_id: int, db: Session = Depends(get_db)):
    """Renders every uploaded EXISTING/incumbent benefit plan/tier for this
    case in the fixed 10-field standard format (see
    app/scoring/rules/benefits_summary.py). A quoted plan uploaded via
    /quote is not included here - see /benefits-comparison.
    """
    case = _get_case_or_404(db, case_id)
    existing_plans = [p for p in case.benefit_plans if p.role == "existing"]
    if not existing_plans:
        raise HTTPException(status_code=404, detail="No table of benefits uploaded for this case")

    return [{"plan_name": plan.plan_name, "summary": _benefit_summary(plan)} for plan in existing_plans]


@router.get("/{case_id}/premium-by-category")
def get_premium_by_category(case_id: int, db: Session = Depends(get_db)):
    """Per-category premium breakdown for the latest uploaded quote (see
    POST /cases/{id}/quote) - members, network, gross premium, and premium
    per member for each category, plus a blended total across categories.
    """
    case = _get_case_or_404(db, case_id)
    quoted_plans = [p for p in case.benefit_plans if p.role == "quoted"]
    if not quoted_plans:
        raise HTTPException(status_code=404, detail="No quote uploaded for this case")

    categories = []
    for plan in quoted_plans:
        premium_per_member = (
            round(plan.gross_premium / plan.member_count, 2) if plan.gross_premium and plan.member_count else None
        )
        categories.append(
            {
                "category": plan.category,
                "plan_name": plan.plan_name,
                "network": plan.network_type,
                "member_count": plan.member_count,
                "gross_premium": plan.gross_premium,
                "premium_per_member": premium_per_member,
            }
        )

    total_members = sum(c["member_count"] or 0 for c in categories)
    total_premium = sum(c["gross_premium"] or 0 for c in categories)
    blended_premium_per_member = round(total_premium / total_members, 2) if total_members else None

    return {
        "categories": categories,
        "total_members": total_members,
        "total_gross_premium": total_premium,
        "blended_premium_per_member": blended_premium_per_member,
    }


@router.get("/{case_id}/benefits-comparison")
def get_benefits_comparison(case_id: int, db: Session = Depends(get_db)):
    """Field-by-field comparison of the existing/incumbent plan(s) against
    the quoted plan(s) for this case (see
    app/scoring/rules/benefits_comparison.py).

    Pairing: existing and quoted plans line up by position (1st existing
    vs 1st quoted category, 2nd vs 2nd, etc.), since neither side's
    category naming is guaranteed to match (e.g. the existing plan's
    "Category 1"/"Category 2" vs a quote's "CAT A"/"CAT B"). If one side
    has fewer plans than the other - most commonly a scanned/OCR'd
    existing plan, which only ever produces ONE combined entry regardless
    of how many categories the source document actually has (see
    app/ingestion/benefits_ocr.py) - the shorter side's LAST plan is
    reused for the extra categories rather than comparing against nothing.
    `existing_plan_reused` flags this so the UI can make clear the same
    existing figures are being shown against more than one quoted category.
    """
    case = _get_case_or_404(db, case_id)
    existing_plans = [p for p in case.benefit_plans if p.role == "existing"]
    quoted_plans = [p for p in case.benefit_plans if p.role == "quoted"]
    if not existing_plans:
        raise HTTPException(status_code=404, detail="No existing table of benefits uploaded for this case")
    if not quoted_plans:
        raise HTTPException(status_code=404, detail="No quote uploaded for this case")

    comparisons = []
    for i in range(max(len(existing_plans), len(quoted_plans))):
        existing_plan = existing_plans[i] if i < len(existing_plans) else existing_plans[-1]
        quoted_plan = quoted_plans[i] if i < len(quoted_plans) else quoted_plans[-1]
        existing_summary = _benefit_summary(existing_plan)
        quoted_summary = _benefit_summary(quoted_plan)

        fields = compare_benefit_summaries(existing_summary, quoted_summary)
        fields["network"] = compare_benefit_value(existing_plan.network_type, quoted_plan.network_type)

        comparisons.append(
            {
                "existing_plan_name": existing_plan.plan_name,
                "existing_category": existing_plan.category,
                "quoted_plan_name": quoted_plan.plan_name,
                "quoted_category": quoted_plan.category,
                "quoted_gross_premium": quoted_plan.gross_premium,
                "quoted_member_count": quoted_plan.member_count,
                "existing_plan_reused": len(existing_plans) < len(quoted_plans) and i >= len(existing_plans),
                "fields": fields,
            }
        )
    return comparisons
