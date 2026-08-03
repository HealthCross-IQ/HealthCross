"""New Business rate-card pricing - see app/scoring/rules/new_business_rating.py
for the pricing method itself. Two admin upload endpoints refresh
HealthCross's own rate card wholesale from its two source spreadsheets
(app/ingestion/rate_cards.py); the rest let a broker discover what
Product/Network/variant options are actually priced, then compute and
store a quote for a specific case.
"""
from collections import defaultdict
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session

from app.database import get_db
from app.ingestion.rate_cards import parse_benefit_variant_option_list, parse_product_pricing_list
from app.models import db_models as models
from app.models import schemas
from app.scoring.rules.new_business_rating import assess_opportunity, price_case

router = APIRouter(tags=["new-business-rating"])


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


@router.post("/admin/rate-cards/upload", response_model=schemas.RateCardUploadOut)
def upload_rate_card(file: UploadFile = File(...), db: Session = Depends(get_db)):
    rows = parse_product_pricing_list(file.file, file.filename)
    if not rows:
        raise HTTPException(status_code=400, detail="No rate card rows found in this file")

    db.query(models.RateCard).delete()
    db.add_all([models.RateCard(**row) for row in rows])
    db.commit()

    return schemas.RateCardUploadOut(
        rows_ingested=len(rows),
        products=sorted({r["product"] for r in rows}),
        regions=sorted({r["region"] for r in rows}),
        networks=sorted({r["network"] for r in rows}),
    )


@router.post("/admin/benefit-variant-rates/upload", response_model=schemas.BenefitVariantRateUploadOut)
def upload_benefit_variant_rates(file: UploadFile = File(...), db: Session = Depends(get_db)):
    rows = parse_benefit_variant_option_list(file.file, file.filename)
    if not rows:
        raise HTTPException(status_code=400, detail="No benefit variant rate rows found in this file")

    db.query(models.BenefitVariantRate).delete()
    db.add_all([models.BenefitVariantRate(**row) for row in rows])
    db.commit()

    return schemas.BenefitVariantRateUploadOut(
        rows_ingested=len(rows),
        variant_names=sorted({r["variant_name"] for r in rows}),
    )


@router.get("/new-business/rate-card-options", response_model=schemas.RateCardOptionsOut)
def rate_card_options(db: Session = Depends(get_db)):
    """Everything a broker UI needs to build cascading Product -> Network
    dropdowns, without exposing the raw per-age-band prices themselves.
    """
    rows = db.query(models.RateCard).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No rate card uploaded yet")

    product_networks: Dict[str, set] = defaultdict(set)
    regions = set()
    for r in rows:
        product_networks[r.product].add((r.network, r.tpa))
        regions.add(r.region)

    return schemas.RateCardOptionsOut(
        products=sorted(product_networks.keys()),
        regions=sorted(regions),
        product_networks={
            product: [
                schemas.NetworkOptionOut(network=network, tpa=tpa)
                for network, tpa in sorted(networks)
            ]
            for product, networks in product_networks.items()
        },
    )


@router.get("/new-business/variant-options", response_model=Dict[str, List[schemas.VariantOptionOut]])
def variant_options(
    region: str = Query(...),
    tpa: str = Query(...),
    network: str = Query(...),
    db: Session = Depends(get_db),
):
    """Every priced option for every benefit variant available on this
    Region x TPA x Network, grouped by variant name - what a broker picks
    from once they've chosen a category's Product/Network.
    """
    rows = (
        db.query(models.BenefitVariantRate)
        .filter_by(region=region, tpa=tpa, network=network)
        .all()
    )
    by_variant: Dict[str, List[schemas.VariantOptionOut]] = defaultdict(list)
    for r in rows:
        by_variant[r.variant_name].append(
            schemas.VariantOptionOut(
                option_value=r.option_value,
                direction=r.direction,
                impact_type=r.impact_type,
                impact_value=r.impact_value,
            )
        )
    return by_variant


def _case_census_dicts(case: models.Case) -> List[dict]:
    return [
        {
            "category": c.category,
            "age": c.age,
            "gender": c.gender,
            "marital_status": c.marital_status,
            "relation": c.relation,
            "emirates": c.emirates,
        }
        for c in case.census_records
    ]


@router.get("/cases/{case_id}/census-categories")
def census_categories(case_id: int, db: Session = Depends(get_db)):
    """Distinct broker plan-tier categories present in this case's census
    (CensusRecord.category, e.g. "A"/"B") with member counts - lets the
    New Business quoting screen offer exactly the categories this case
    actually has, rather than a free-text field prone to typos that would
    silently leave members unpriced (see price_case's uncategorized_member_count).
    """
    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    counts: Dict[str, int] = defaultdict(int)
    uncategorized = 0
    for c in case.census_records:
        if c.category:
            counts[c.category] += 1
        else:
            uncategorized += 1
    return {
        "categories": [{"category": k, "member_count": v} for k, v in sorted(counts.items())],
        "uncategorized_member_count": uncategorized,
    }


@router.post("/cases/{case_id}/new-business-quote", response_model=schemas.NewBusinessQuoteOut)
def compute_new_business_quote(case_id: int, payload: schemas.NewBusinessQuoteRequest, db: Session = Depends(get_db)):
    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    census = _case_census_dicts(case)
    if not census:
        raise HTTPException(status_code=400, detail="Case has no census data to rate")

    rate_cards = _rate_card_dicts(db)
    if not rate_cards:
        raise HTTPException(status_code=400, detail="No rate card uploaded yet")
    variant_rates = _variant_rate_dicts(db)

    categories = [c.model_dump() for c in payload.categories]
    result = price_case(census, categories, rate_cards, variant_rates)

    latest_scorecard = (
        db.query(models.Scorecard)
        .filter_by(case_id=case_id)
        .order_by(models.Scorecard.created_at.desc())
        .first()
    )
    opportunity = assess_opportunity(
        rated_premium=result["case_gross_annual_premium"],
        target_premium=case.target_premium,
        risk_tier=latest_scorecard.risk_tier if latest_scorecard else None,
    )

    quote = models.NewBusinessQuote(
        case_id=case_id,
        categories=categories,
        case_gross_annual_premium=result["case_gross_annual_premium"],
        result=result,
        opportunity_assessment=opportunity,
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)
    return quote


@router.get("/cases/{case_id}/new-business-quotes", response_model=List[schemas.NewBusinessQuoteOut])
def list_new_business_quotes(case_id: int, db: Session = Depends(get_db)):
    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case_id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .all()
    )


@router.get("/cases/{case_id}/new-business-quote", response_model=schemas.NewBusinessQuoteOut)
def get_latest_new_business_quote(case_id: int, db: Session = Depends(get_db)):
    quote = (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case_id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .first()
    )
    if not quote:
        raise HTTPException(status_code=404, detail="No new business quote found for this case")
    return quote
