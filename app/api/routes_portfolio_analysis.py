"""Portfolio Analysis - checks HealthCross's own already-booked book
against the New Business rate card (see
app/scoring/rules/portfolio_analysis.py). Three admin upload endpoints
refresh the book's own membership/claims/group-product-mapping data
wholesale (same pattern as the rate card itself); the analysis endpoint
joins all of it against whatever rate card is currently active.
"""
from collections import defaultdict
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session

from app.database import get_db
from app.ingestion.group_product_mapping import parse_group_product_mapping
from app.ingestion.portfolio_claims import parse_portfolio_claims
from app.ingestion.portfolio_members import parse_portfolio_members
from app.models import db_models as models
from app.models import schemas
from app.scoring.rules.portfolio_analysis import analyze_portfolio_member, claims_total_by_beneficiary, summarize_portfolio

router = APIRouter(prefix="/portfolio-analysis", tags=["portfolio-analysis"])


@router.post("/members/upload", response_model=schemas.PortfolioUploadOut)
def upload_portfolio_members(file: UploadFile = File(...), db: Session = Depends(get_db)):
    rows = parse_portfolio_members(file.file, file.filename)
    if not rows:
        raise HTTPException(status_code=400, detail="No member rows found in this file")

    db.query(models.PortfolioMember).delete()
    db.add_all([models.PortfolioMember(**row) for row in rows])
    db.commit()
    return schemas.PortfolioUploadOut(rows_ingested=len(rows))


@router.post("/claims/upload", response_model=schemas.PortfolioUploadOut)
def upload_portfolio_claims(file: UploadFile = File(...), db: Session = Depends(get_db)):
    rows = parse_portfolio_claims(file.file, file.filename)
    if not rows:
        raise HTTPException(status_code=400, detail="No claim rows found in this file")

    db.query(models.PortfolioClaimEntry).delete()
    db.add_all([models.PortfolioClaimEntry(**row) for row in rows])
    db.commit()
    return schemas.PortfolioUploadOut(rows_ingested=len(rows))


@router.post("/group-product-mapping/upload", response_model=schemas.PortfolioUploadOut)
def upload_group_product_mapping(file: UploadFile = File(...), db: Session = Depends(get_db)):
    rows = parse_group_product_mapping(file.file, file.filename)
    if not rows:
        raise HTTPException(status_code=400, detail="No group/product rows found in this file")

    db.query(models.GroupProductMapping).delete()
    db.add_all([models.GroupProductMapping(**row) for row in rows])
    db.commit()
    return schemas.PortfolioUploadOut(rows_ingested=len(rows))


def _rate_card_dicts(db: Session) -> List[dict]:
    return [
        {
            "product": r.product,
            "region": r.region,
            "network": r.network,
            "tpa": r.tpa,
            "from_age": r.from_age,
            "to_age": r.to_age,
            "male_price": r.male_price,
            "female_price": r.female_price,
            "married_female_surcharge": r.married_female_surcharge,
        }
        for r in db.query(models.RateCard).all()
    ]


def _variant_rate_dicts(db: Session) -> List[dict]:
    return [
        {
            "variant_name": r.variant_name,
            "option_value": r.option_value,
            "direction": r.direction,
            "impact_type": r.impact_type,
            "impact_value": r.impact_value,
            "region": r.region,
            "tpa": r.tpa,
            "network": r.network,
        }
        for r in db.query(models.BenefitVariantRate).all()
    ]


def _member_dicts(db: Session) -> List[dict]:
    return [
        {
            "beneficiary_id": m.beneficiary_id,
            "contract": m.contract,
            "master_contract": m.master_contract,
            "network_type_raw": m.network_type_raw,
            "age": m.age,
            "gender": m.gender,
            "marital_status": m.marital_status,
            "relation": m.relation,
            "nationality_zone": m.nationality_zone,
            "residence_emirate": m.residence_emirate,
            "region": m.region,
            "actual_gross_premium": m.actual_gross_premium,
        }
        for m in db.query(models.PortfolioMember).all()
    ]


def _run_analysis(db: Session) -> List[dict]:
    members = _member_dicts(db)
    if not members:
        raise HTTPException(status_code=400, detail="No portfolio members uploaded yet")
    rate_cards = _rate_card_dicts(db)
    if not rate_cards:
        raise HTTPException(status_code=400, detail="No rate card uploaded yet")
    variant_rates = _variant_rate_dicts(db)

    claims = [{"patient_id": c.patient_id, "final_amount": c.final_amount} for c in db.query(models.PortfolioClaimEntry).all()]
    claims_by_beneficiary = claims_total_by_beneficiary(claims)

    group_product_by_name: Dict[str, str] = {
        gp.group_name: gp.product for gp in db.query(models.GroupProductMapping).all()
    }

    return [
        analyze_portfolio_member(m, group_product_by_name, rate_cards, variant_rates, claims_by_beneficiary)
        for m in members
    ]


@router.get("/summary", response_model=schemas.PortfolioSummaryOut)
def portfolio_summary(
    group_by: str = Query("product", description="One of: product, network, region, nationality_zone"),
    db: Session = Depends(get_db),
):
    results = _run_analysis(db)
    in_scope = [r for r in results if r.get("in_scope", True)]
    out_of_scope_count = len(results) - len(in_scope)
    unmapped_product_count = sum(1 for r in in_scope if not r.get("product"))
    unmapped_network_count = sum(1 for r in in_scope if not r.get("network"))

    try:
        rows = summarize_portfolio(results, group_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return schemas.PortfolioSummaryOut(
        group_by=group_by,
        rows=rows,
        total_members=len(results),
        out_of_scope_member_count=out_of_scope_count,
        unmapped_product_member_count=unmapped_product_count,
        unmapped_network_member_count=unmapped_network_count,
    )


@router.get("/members", response_model=List[dict])
def portfolio_member_detail(db: Session = Depends(get_db)):
    """Every member's own analysis row, unaggregated - for spot-checking a
    specific group/member rather than only seeing the rolled-up summary.
    """
    return _run_analysis(db)
