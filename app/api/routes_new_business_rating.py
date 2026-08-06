"""New Business rate-card pricing - see app/scoring/rules/new_business_rating.py
for the pricing method itself. Two admin upload endpoints refresh
HealthCross's own rate card wholesale from its two source spreadsheets
(app/ingestion/rate_cards.py); the rest let a broker discover what
Product/Network/variant options are actually priced, then compute and
store a quote for a specific case.
"""
from collections import defaultdict
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session

from app.database import get_db
from app.ingestion.rate_cards import parse_benefit_variant_option_list, parse_product_pricing_list
from app.models import db_models as models
from app.models import schemas
from app.scoring.rules.new_business_rating import assess_opportunity, price_case, price_tier_ladder

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

    for case_id in [c.id for c in db.query(models.Case.id).all()]:
        maybe_auto_requote(case_id, db)

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

    for case_id in [c.id for c in db.query(models.Case.id).all()]:
        maybe_auto_requote(case_id, db)

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

    suggested_product = None
    if case.existing_insurer:
        pref = db.query(models.InsurerTierPreference).filter_by(insurer_name=case.existing_insurer).first()
        suggested_product = pref.suggested_product if pref else None

    return {
        "categories": [{"category": k, "member_count": v} for k, v in sorted(counts.items())],
        "uncategorized_member_count": uncategorized,
        "suggested_product": suggested_product,
    }


def _price_and_store_quote(
    case: models.Case, categories: List[dict], census: List[dict], rate_cards: List[dict],
    variant_rates: List[dict], db: Session,
) -> models.NewBusinessQuote:
    result = price_case(census, categories, rate_cards, variant_rates)
    for cat_result, cat_input in zip(result["categories"], categories):
        cat_result["tier_ladder"] = price_tier_ladder(census, cat_input, rate_cards, variant_rates)

    latest_scorecard = (
        db.query(models.Scorecard)
        .filter_by(case_id=case.id)
        .order_by(models.Scorecard.created_at.desc())
        .first()
    )
    opportunity = assess_opportunity(
        rated_premium=result["case_gross_annual_premium"],
        target_premium=case.target_premium,
        risk_tier=latest_scorecard.risk_tier if latest_scorecard else None,
    )

    quote = models.NewBusinessQuote(
        case_id=case.id,
        categories=categories,
        case_gross_annual_premium=result["case_gross_annual_premium"],
        result=result,
        opportunity_assessment=opportunity,
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)
    return quote


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
    return _price_and_store_quote(case, categories, census, rate_cards, variant_rates, db)


def _resolve_auto_quote_categories(case: models.Case, db: Session) -> Optional[List[dict]]:
    """Fills in Product/Network/TPA for every census category this case
    has, so a quote can be (re-)computed without the underwriter visiting
    the New Business Quote tab again - preferring whatever a PRIOR quote
    already had for that category (so a broker's own override survives a
    later auto re-quote), then falling back to the case's own
    default_product/default_network/default_tpa (see Case model), and, for
    product only, the insurer's suggested tier. Returns None if any
    category still can't be resolved - there just isn't enough
    information yet for even the case-level defaults to cover.
    """
    counts: Dict[str, int] = defaultdict(int)
    for c in case.census_records:
        if c.category:
            counts[c.category] += 1
    if not counts:
        return None

    suggested_product = None
    if case.existing_insurer:
        pref = db.query(models.InsurerTierPreference).filter_by(insurer_name=case.existing_insurer).first()
        suggested_product = pref.suggested_product if pref else None

    latest_quote = (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case.id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .first()
    )
    prior_by_category = {c["category"]: c for c in (latest_quote.categories or [])} if latest_quote else {}

    categories = []
    for category_name in sorted(counts):
        prior = prior_by_category.get(category_name, {})
        product = prior.get("product") or case.default_product or suggested_product
        network = prior.get("network") or case.default_network
        tpa = prior.get("tpa") or case.default_tpa
        if not product or not network or not tpa:
            return None
        categories.append(
            {
                "category": category_name,
                "product": product,
                "network": network,
                "tpa": tpa,
                "commission_pct": prior.get("commission_pct"),
                "variant_selections": prior.get("variant_selections") or {},
            }
        )
    return categories


def maybe_auto_requote(case_id: int, db: Session) -> None:
    """Best-effort automatic re-pricing whenever an input the New Business
    Quote depends on changes (census, table of benefits, rate card) -
    reuses whatever Product/Network/TPA is already resolvable (see
    _resolve_auto_quote_categories) rather than requiring a fresh manual
    "Compute quote" click every time. Silently does nothing if there isn't
    yet enough information to price every category, or if pricing itself
    fails for any reason - this is an opportunistic side effect of the
    caller's own request (a census/benefits/rate-card upload), and must
    never turn a successful upload into a failed one.
    """
    case = db.get(models.Case, case_id)
    if not case:
        return
    categories = _resolve_auto_quote_categories(case, db)
    if not categories:
        return
    census = _case_census_dicts(case)
    if not census:
        return
    rate_cards = _rate_card_dicts(db)
    if not rate_cards:
        return
    variant_rates = _variant_rate_dicts(db)
    try:
        _price_and_store_quote(case, categories, census, rate_cards, variant_rates, db)
    except Exception:
        db.rollback()


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
