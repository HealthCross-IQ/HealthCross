"""Standalone reference library of insurer table-of-benefits plans, used to
build the detailed international (and later local) insurer comparison - a
broker uploads each insurer's TOB once, tagged with an insurer name and
plan/tier label, then picks any combination of previously-uploaded plans
to compare side by side. Distinct from a case's own BenefitPlan rows
(existing vs quoted for one specific submission): this is a general
reference matrix, not tied to any case.
"""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.ingestion.benefits_pdf import extract_all_rows_by_tier
from app.ingestion.international_tob import extract_benefit_rows as extract_generic_rows
from app.ingestion.labeled_row_benefits_pdf import extract_all_rows as extract_labeled_row_rows
from app.models import db_models as models
from app.models import schemas

router = APIRouter(prefix="/reference-plans", tags=["reference-plans"])


def _get_plan_or_404(db: Session, plan_id: int) -> models.ReferenceBenefitPlan:
    plan = db.get(models.ReferenceBenefitPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Reference plan not found")
    return plan


@router.post("", response_model=List[schemas.ReferenceBenefitPlanOut])
def upload_reference_plan(
    insurer_name: str = Query(..., description="e.g. 'Bupa', 'Cigna Global Care', 'Allianz', 'MSH', 'Max Health'"),
    plan_label: Optional[str] = Query(
        None,
        description="Plan/tier name, e.g. 'Elite' or 'Global Care Flexible - Dubai'. "
        "Not required for a Bupa-style multi-tier file (each tier is named automatically) "
        "or a Maxmed-style labeled-row file (falls back to the document's own plan name).",
    ),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    filename = file.filename

    # Try each known document family in turn, falling through to the next
    # on a no-match (None, or no rows found) rather than guessing - same
    # pattern as the per-case /benefits upload's fallback chain.
    try:
        labeled = extract_labeled_row_rows(file.file, filename)
    except Exception:
        labeled = None
    if labeled and labeled["rows"]:
        plan = models.ReferenceBenefitPlan(
            insurer_name=insurer_name,
            plan_label=plan_label or labeled.get("plan_name") or filename,
            source_filename=filename,
            benefit_rows=[{"section": "", "label": r["label"], "value": r["value"], "note": ""} for r in labeled["rows"]],
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
        return [plan]

    file.file.seek(0)
    try:
        by_tier: Dict[str, List[Dict[str, str]]] = extract_all_rows_by_tier(file.file, filename)
    except Exception:
        by_tier = {}
    by_tier = {tier: rows for tier, rows in by_tier.items() if rows}
    if by_tier:
        plans = [
            models.ReferenceBenefitPlan(
                insurer_name=insurer_name,
                plan_label=tier.title(),
                source_filename=filename,
                benefit_rows=[{"section": "", "label": r["label"], "value": r["value"], "note": ""} for r in rows],
            )
            for tier, rows in by_tier.items()
        ]
        db.add_all(plans)
        db.commit()
        for plan in plans:
            db.refresh(plan)
        return plans

    file.file.seek(0)
    rows = extract_generic_rows(file.file, filename)
    if not rows:
        raise HTTPException(status_code=400, detail="Could not find any benefit rows in this file")
    if not plan_label:
        raise HTTPException(status_code=400, detail="plan_label is required for this document format")

    plan = models.ReferenceBenefitPlan(
        insurer_name=insurer_name,
        plan_label=plan_label,
        source_filename=filename,
        benefit_rows=rows,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return [plan]


@router.get("", response_model=List[schemas.ReferenceBenefitPlanSummary])
def list_reference_plans(db: Session = Depends(get_db)):
    plans = db.query(models.ReferenceBenefitPlan).order_by(models.ReferenceBenefitPlan.created_at.desc()).all()
    return [
        schemas.ReferenceBenefitPlanSummary(
            id=p.id,
            insurer_name=p.insurer_name,
            plan_label=p.plan_label,
            source_filename=p.source_filename,
            row_count=len(p.benefit_rows or []),
            created_at=p.created_at,
        )
        for p in plans
    ]


@router.delete("/{plan_id}")
def delete_reference_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = _get_plan_or_404(db, plan_id)
    db.delete(plan)
    db.commit()
    return {"deleted": True}


@router.get("/compare")
def compare_reference_plans(ids: str = Query(..., description="Comma-separated reference plan IDs to compare"), db: Session = Depends(get_db)):
    """Detailed side-by-side comparison: every distinct benefit label seen
    across the selected plans becomes a row (grouped under whichever
    section it appeared under), each selected plan becomes a column, and a
    plan that has no row for a given label shows null rather than a forced
    guess - insurers genuinely offer different benefits, and papering over
    that with "Not covered" would misrepresent documents that simply never
    mention it.
    """
    try:
        plan_ids = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="ids must be a comma-separated list of integers")
    if not plan_ids:
        raise HTTPException(status_code=400, detail="At least one plan id is required")

    plans = db.query(models.ReferenceBenefitPlan).filter(models.ReferenceBenefitPlan.id.in_(plan_ids)).all()
    found_ids = {p.id for p in plans}
    missing = [pid for pid in plan_ids if pid not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"Reference plan(s) not found: {missing}")
    # Preserve the caller's requested column order rather than the DB's.
    plans_by_id = {p.id: p for p in plans}
    ordered_plans = [plans_by_id[pid] for pid in plan_ids]

    # section -> label -> {plan_id: value}
    sections: Dict[str, Dict[str, Dict[int, str]]] = {}
    section_order: List[str] = []
    label_order: Dict[str, List[str]] = {}

    for plan in ordered_plans:
        for row in plan.benefit_rows or []:
            section = row.get("section") or "General"
            label = row["label"]
            if section not in sections:
                sections[section] = {}
                section_order.append(section)
                label_order[section] = []
            if label not in sections[section]:
                sections[section][label] = {}
                label_order[section].append(label)
            sections[section][label][plan.id] = row.get("value")

    result_sections = [
        {
            "section": section,
            "rows": [
                {
                    "label": label,
                    "values": {plan.id: sections[section][label].get(plan.id) for plan in ordered_plans},
                }
                for label in label_order[section]
            ],
        }
        for section in section_order
    ]

    return {
        "plans": [
            {"id": p.id, "insurer_name": p.insurer_name, "plan_label": p.plan_label}
            for p in ordered_plans
        ],
        "sections": result_sections,
    }
