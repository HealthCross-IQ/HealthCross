"""New Business rate-card pricing - see app/scoring/rules/new_business_rating.py
for the pricing method itself. Two admin upload endpoints refresh
HealthCross's own rate card wholesale from its two source spreadsheets
(app/ingestion/rate_cards.py); the rest let a broker discover what
Product/Network/variant options are actually priced, then compute and
store a quote for a specific case.
"""
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.api.routes_portfolio_analysis import _get_stored_as_of, _run_analysis, analysis_with_cube
from app.database import get_db
from app.ingestion.rate_cards import parse_benefit_variant_option_list, parse_product_pricing_list
from app.models import db_models as models
from app.models import schemas
from app.scoring.rules.new_business_rating import (
    assess_opportunity,
    category_loading_pct,
    price_case,
    price_case_by_tier,
    price_tier_ladder,
)
from app.reference.emirate_regions import region_for_emirate
from app.scoring.rules.portfolio_analysis import _burning_cost_lookup_network, nas_tpa_factor

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


#: Prefixes a table of benefits uses for a category that a census never
#: does. A TOB is written for humans and titles its sections "Category A"
#: or "Cat A"; a census column just says "A". Both mean the same category,
#: and matching them literally means a case where every field is correctly
#: filled in still prices nothing - the plan is complete, the offer is
#: set, and it is invisible because of a word.
_CATEGORY_PREFIXES = ("CATEGORY", "CAT", "CLASS", "PLAN", "TIER")


def _normalize_category(value: Optional[str]) -> Optional[str]:
    """Collapses whitespace/casing differences (e.g. "A" vs "A " vs "a")
    so the same category isn't split into several - fixes both older
    census uploads stored before app/ingestion/census.py started
    normalizing on parse, and any inconsistently-cased category letter
    typed into a Benefits tab category card by hand.

    Also strips the leading noun a table of benefits puts in front of the
    letter ("Category A", "Cat A", "Class A", "Plan A", "Tier A"), which a
    census never carries. Without this the two sources agree on everything
    that matters and still never meet.

    The prefix is only removed when something is left after it, so a
    category genuinely called "Class" or "Plan" survives intact rather
    than normalizing to nothing.
    """
    if not value:
        return None
    text = " ".join(str(value).split()).upper()
    for prefix in _CATEGORY_PREFIXES:
        if text.startswith(prefix):
            remainder = text[len(prefix):].lstrip(" -_:.")
            if remainder:
                text = remainder
                break
    return text or None


def _normalize_quote_categories(categories: List[dict]) -> List[dict]:
    """Normalizes a stored quote's own category letters before matching
    them against a (now-normalized) census - a quote persisted before this
    normalization existed can still carry its categories as "a" or "A ",
    which would otherwise match zero census members when re-priced live
    (see /by-tier and /burning-cost-comparison), silently showing every
    figure as 0 despite the quote's own originally-stored total being real.
    """
    return [{**c, "category": _normalize_category(c.get("category"))} for c in categories]


def _case_census_dicts(case: models.Case) -> List[dict]:
    return [
        {
            "category": _normalize_category(c.category),
            "age": c.age,
            "gender": c.gender,
            "marital_status": c.marital_status,
            "relation": c.relation,
            "emirates": c.emirates,
            # Not used by rate-card pricing (the card has no nationality
            # dimension) - carried for the nationality mix factor, which
            # prices off the book's own experience by nationality rather
            # than off the card. See nationality_mix_pricing.
            "nationality": c.nationality,
            "nationality_zone": c.nationality_zone,
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
        category = _normalize_category(c.category)
        if category:
            counts[category] += 1
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

    categories = _normalize_quote_categories([c.model_dump() for c in payload.categories])
    return _price_and_store_quote(case, categories, census, rate_cards, variant_rates, db)


def _resolve_auto_quote_categories(case: models.Case, db: Session) -> Optional[List[dict]]:
    """Fills in Product/Network/TPA for every census category this case
    has, so a quote can be (re-)computed without the underwriter visiting
    the New Business Quote tab again. Each category's own EXISTING-role
    benefit plan (Benefits tab, see BenefitPlan.nb_product/nb_network/
    nb_tpa) is the source of truth - a case's own categories (e.g. A/B/C/D)
    commonly price against different networks, so there's no single
    case-wide default to fall back to. Falls back to whatever a PRIOR
    quote already had for that category only when the Benefits tab hasn't
    been given a pick yet, so a broker's own manual override on the New
    Business Quote tab still survives a later auto re-quote. Returns None
    if any category still can't be resolved.
    """
    counts: Dict[str, int] = defaultdict(int)
    for c in case.census_records:
        category = _normalize_category(c.category)
        if category:
            counts[category] += 1
    if not counts:
        return None

    benefits_by_category = {
        _normalize_category(p.category): p for p in case.benefit_plans if p.role == "existing" and p.category
    }

    latest_quote = (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case.id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .first()
    )
    prior_by_category = (
        {c["category"]: c for c in _normalize_quote_categories(latest_quote.categories or [])} if latest_quote else {}
    )

    categories = []
    for category_name in sorted(counts):
        plan = benefits_by_category.get(category_name)
        prior = prior_by_category.get(category_name, {})
        product = (plan.nb_product if plan else None) or prior.get("product")
        network = (plan.nb_network if plan else None) or prior.get("network")
        tpa = (plan.nb_tpa if plan else None) or prior.get("tpa")
        if not product or not network or not tpa:
            return None
        categories.append(
            {
                "category": category_name,
                "product": product,
                "network": network,
                "tpa": tpa,
                # Carried from the prior quote so a Plan Details import's
                # own Zone survives an automatic re-quote - dropping it
                # would blank the proposal's area of cover the first time
                # anything else on the case changed.
                "zone": prior.get("zone"),
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


@router.get("/cases/{case_id}/new-business-quote/by-tier")
def get_new_business_quote_by_tier(case_id: int, db: Session = Depends(get_db)):
    """The latest quote's own category picks, re-priced under every full
    product tier (Bronze/Silver/Gold/Platinum) rather than just the one
    already chosen - a quick case-wide "what would this cost on tier X"
    comparison, computed live rather than stored (it's a what-if view of
    the latest quote, not a quote itself). See price_case_by_tier.
    """
    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    quote = (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case_id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .first()
    )
    if not quote:
        raise HTTPException(status_code=404, detail="No new business quote found for this case")

    census = _case_census_dicts(case)
    rate_cards = _rate_card_dicts(db)
    variant_rates = _variant_rate_dicts(db)
    return price_case_by_tier(census, _normalize_quote_categories(quote.categories), rate_cards, variant_rates)


@router.post("/cases/{case_id}/hc-plan-details")
def upload_hc_plan_details(case_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Apply a pricing tool "Plan Details" export to this case, and quote.

    The offer has already been decided in the pricing tool - product,
    network, TPA and every benefit selection, per category. Re-keying that
    into the portal is not data entry, it is an opportunity to disagree
    with the tool that produced it. This reads the export and prices
    directly from it.

    Selections the rate card cannot price are REPORTED rather than
    dropped, because an unrecognised selection does not fail: pricing
    falls back to the variant's base option and the quote comes out
    looking perfectly reasonable while being for a different plan than the
    one exported. That failure is invisible in the number and visible
    only in the warnings.
    """
    from app.ingestion.hc_plan_details import parse_hc_plan_details, unmatched_selections

    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    try:
        parsed = parse_hc_plan_details(file.file, file.filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read the Plan Details export: {exc}")
    if not parsed["categories"]:
        raise HTTPException(status_code=400, detail="No categories found in the Plan Details export")

    census_categories = {
        _normalize_category(r.category) for r in case.census_records if _normalize_category(r.category)
    }

    categories = []
    not_on_census = []
    for entry in parsed["categories"]:
        category = _normalize_category(entry["category"])
        if census_categories and category not in census_categories:
            # Priced in the tool but nobody on the census is in it. Worth
            # saying: it usually means the export and the census are from
            # different points in the negotiation.
            not_on_census.append(entry["category"])
            continue
        if not (entry["product"] and entry["network"] and entry["tpa"]):
            continue
        categories.append({
            "category": category,
            "product": entry["product"],
            "network": entry["network"],
            "tpa": entry["tpa"],
            # The export's Zone is the proposal's area of cover. It is
            # parsed and was then dropped here, which is why the
            # existing-vs-proposed table showed an area of cover on the
            # incumbent's side and nothing on HealthCross's - for a
            # proposal that had in fact stated one.
            "zone": entry.get("zone"),
            "variant_selections": entry["variant_selections"],
        })

    if not categories:
        raise HTTPException(
            status_code=400,
            detail=(
                "None of the export's categories match this case's census. "
                f"Export has {', '.join(e['category'] for e in parsed['categories'])}; "
                f"census has {', '.join(sorted(census_categories)) or 'no categories at all'}."
            ),
        )

    rate_cards = _rate_card_dicts(db)
    if not rate_cards:
        raise HTTPException(status_code=400, detail="No rate card uploaded - nothing can be priced")
    census = _case_census_dicts(case)
    if not census:
        raise HTTPException(status_code=400, detail="Upload a census for this case first")

    variant_rates = _variant_rate_dicts(db)
    # Check the selections against what is actually priced for each
    # category's own region/tpa/network before quoting, so the warnings
    # describe this quote rather than a generic possibility.
    warnings = []
    for entry in categories:
        region = region_for_emirate(next((m.get("emirates") for m in census if m.get("category") == entry["category"]), None))
        options: Dict[str, List[str]] = defaultdict(list)
        for r in variant_rates:
            if r.get("region") == region and r.get("tpa") == entry["tpa"] and r.get("network") == entry["network"]:
                options[r["variant_name"]].append(r["option_value"])
        if options:
            warnings.extend(unmatched_selections([entry], dict(options)))

    quote = _price_and_store_quote(case, categories, census, rate_cards, variant_rates, db)
    return {
        "quote": quote,
        "categories_applied": [c["category"] for c in categories],
        "categories_not_on_census": not_on_census,
        "unmatched_selections": warnings,
        "source_filename": parsed["source_filename"],
    }


@router.get("/cases/{case_id}/existing-vs-proposed")
def get_existing_vs_proposed(case_id: int, db: Session = Depends(get_db)):
    """The client's existing cover beside what HealthCross is proposing,
    field for field, with the direction of each change.

    Both sides are rendered in the same 12-field shape, and the proposal's
    limit and copay variants are recombined into the single line a table
    of benefits actually writes ("USD 300 Co-pay: 20%") - a comparison
    only reads if both sides are phrased alike.
    """
    from app.scoring.rules.proposed_benefits import proposed_benefit_rows

    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    quote = (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case_id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .first()
    )
    quoted_categories = _normalize_quote_categories(quote.categories or []) if quote else []
    selections_by_category = {c["category"]: (c.get("variant_selections") or {}) for c in quoted_categories}
    design_by_category = {c["category"]: c for c in quoted_categories}

    existing_by_category = {
        _normalize_category(p.category): p
        for p in case.benefit_plans
        if p.role == "existing" and p.category
    }
    # The issued quote document, where one has been uploaded. It outranks
    # the rate card's variant selections for the proposed side, because it
    # is what the broker actually received: the card says "Maternity
    # Limit: <the variant that was picked>", the issued quote says
    # "USD 14,000", and only one of those is the offer. It also carries
    # benefit lines the card prices as part of the product rather than as
    # a dropdown - Routine Health Examination among them - which is why
    # they read as "not priced as a variant" when they are in fact
    # quoted, and stated, on page 12.
    quoted_by_category = {
        _normalize_category(p.category): p
        for p in case.benefit_plans
        if p.role == "quoted" and p.category and p.standard_summary
    }
    variant_rates = _variant_rate_dicts(db)

    # The issued quote counts as a category too: a case whose only
    # benefit information is the document the broker received would
    # otherwise produce no rows at all.
    categories = sorted(set(selections_by_category) | set(existing_by_category) | set(quoted_by_category))
    out = []
    for category in categories:
        plan = existing_by_category.get(category)
        design = design_by_category.get(category) or {}
        out.append({
            "category": category,
            "existing_plan_name": plan.plan_name if plan else None,
            "product": design.get("product"),
            "network": design.get("network"),
            "tpa": design.get("tpa"),
            "rows": proposed_benefit_rows(
                (plan.standard_summary if plan else None),
                selections_by_category.get(category),
                variant_rates,
                # Network and Area of Cover are both chosen on the case
                # rather than on a benefit dropdown - Area of Cover is
                # the rate card's own Zone - but both are still part of
                # the proposal. Left blank they read as something
                # HealthCross had not offered, when in fact they are the
                # two lines that frame every limit underneath them.
                proposed_overrides={
                    "network": design.get("network"),
                    "area_of_cover": design.get("zone"),
                    **_issued_quote_values(quoted_by_category.get(category)),
                },
            ),
        })
    return {"categories": out, "has_quote": bool(quote)}


@router.get("/cases/{case_id}/quote-readiness")
def get_quote_readiness(case_id: int, db: Session = Depends(get_db)):
    """Why this case will not price, said out loud.

    Auto-quoting is silent by design - it must never turn a successful
    upload into a failed one - which leaves a user who uploaded a table of
    benefits and saw nothing change with no way to tell whether the portal
    is broken, still working, or waiting on them. This runs the same
    resolution and reports what it found.

    It separates "not done yet" from "wrong", because they look identical
    on screen and need opposite responses: a category with no Product
    chosen is waiting for a decision, while a benefit plan whose category
    letter matches nothing in the census will never resolve however long
    anyone waits.
    """
    from app.scoring.rules.quote_readiness import quote_readiness

    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    counts: Dict[str, int] = defaultdict(int)
    for record in case.census_records:
        category = _normalize_category(record.category)
        if category:
            counts[category] += 1

    plans = [
        {
            "plan_name": p.plan_name,
            "category": p.category,
            "product": p.nb_product,
            "network": p.nb_network,
            "tpa": p.nb_tpa,
        }
        for p in case.benefit_plans
        if p.role == "existing"
    ]

    latest = (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case_id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .first()
    )
    prior = _normalize_quote_categories(latest.categories or []) if latest else []

    readiness = quote_readiness(dict(counts), plans, prior_quote_categories=prior)
    readiness["uncategorised_member_count"] = sum(
        1 for r in case.census_records if not _normalize_category(r.category)
    )
    readiness["census_member_count"] = len(case.census_records)
    readiness["has_rate_card"] = bool(_rate_card_dicts(db))
    if not readiness["has_rate_card"]:
        readiness["blockers"].insert(0, {
            "severity": "blocking",
            "issue": "No rate card uploaded",
            "detail": "Nothing can be priced without one, whatever else is set on the case.",
            "fix_at": "Rate Cards",
        })
        readiness["can_price"] = False
    return readiness


@router.get("/cases/{case_id}/risk-based-price")
def get_risk_based_price(
    case_id: int,
    trend_pct: float = Query(0.10, description="Medical trend between the book's experience period and the policy being priced"),
    apply_nationality: bool = Query(True, description="Apply the within-zone nationality refinement on top of the cube price"),
    as_of: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """A suggested price for a new enquiry, built from risk rather than
    from the rate card.

    Upload a census and a table of benefits and this prices it: each
    member costed against HealthCross's own book for their own
    demographic cell, refined for the scheme's nationality mix, loaded for
    industry and trend, then grossed up for expenses. The rate card price
    sits beside it - the card is what HealthCross charges today, this is
    what the book says this particular population costs, and the gap is
    the underwriting decision.

    Plan design comes from the Benefits tab exactly as auto-quoting takes
    it (see _resolve_auto_quote_categories), so nothing has to be
    re-entered: what makes this a suggestion rather than a form is that
    the census and the benefits are the only inputs.
    """
    from app.api.routes_portfolio_analysis import _get_stored_as_of as _stored_as_of
    from app.scoring.rules.burning_cost_cube import burning_cost_cube
    from app.scoring.rules.expected_cost_pricing import price_by_category
    from app.scoring.rules.nationality_mix_pricing import nationality_mix_factor, within_zone_rows
    from app.scoring.rules.portfolio_analysis import nationality_risk_table

    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    census = _case_census_dicts(case)
    if not census:
        raise HTTPException(status_code=400, detail="Upload a census for this case first")

    categories = _resolve_auto_quote_categories(case, db)
    if not categories:
        # Two routes get a category its plan design, and a case with a
        # census but no benefits document uses the second one - so the
        # message must name it. Saying only "Benefits tab" sends an
        # underwriter who is deliberately keying the benefits in by hand
        # to the one place that cannot help them.
        raise HTTPException(
            status_code=400,
            detail=(
                "Each census category needs a Product, Network and TPA before this can price. "
                "Set them per category on the New Business Quote tab below and press Compute quote, "
                "or upload a table of benefits and set them on the Benefits tab - either works."
            ),
        )

    try:
        results, cube = analysis_with_cube(db, as_of=as_of or _stored_as_of(db), require_rate_card=False)
    except HTTPException:
        raise HTTPException(
            status_code=400,
            detail="Portfolio Analysis has no membership/claims uploaded - there is no experience to price against",
        )

    rate_cards = _rate_card_dicts(db)
    if cube["book"]["burning_cost"] is None:
        raise HTTPException(status_code=400, detail="The book has no claims experience to price against")

    design_by_category = {c["category"]: c for c in categories}
    priced_census = [
        {
            **member,
            "product": (design_by_category.get(member.get("category")) or {}).get("product"),
            "network": _burning_cost_lookup_network(
                (design_by_category.get(member.get("category")) or {}).get("network")
            ),
            # Read off the network the case actually quoted, not the MSH
            # name standing in for it - once substituted it looks like
            # an MSH network and would carry no adjustment.
            "cost_factor": nas_tpa_factor(
                (design_by_category.get(member.get("category")) or {}).get("network")
            ),
        }
        for member in census
    ]

    # The cube already carries nationality_zone as a dimension, so only
    # the WITHIN-ZONE part of a nationality's experience may be applied on
    # top - see within_zone_rows. Applying the book-relative factor here
    # would charge the zone effect twice.
    mix = nationality_mix_factor(census, within_zone_rows(nationality_risk_table(results)))
    nationality_factor = (
        mix["factor"] if (apply_nationality and mix.get("pricing_ready") and mix.get("factor")) else 1.0
    )

    priced = price_by_category(
        priced_census, cube,
        loading_pct_by_category={
            c["category"]: category_loading_pct(c["product"], c.get("commission_pct")) for c in categories
        },
        default_loading_pct=category_loading_pct(""),
        industry=case.industry,
        trend_pct=trend_pct,
    )

    suggested = priced["case_gross_premium"] * nationality_factor
    for cat in priced["categories"]:
        cat["suggested_premium"] = round((cat["gross_premium"] or 0.0) * nationality_factor, 2)
        cat["product"] = (design_by_category.get(cat["category"]) or {}).get("product")
        cat["network"] = (design_by_category.get(cat["category"]) or {}).get("network")

    quote = (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case_id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .first()
    )
    card_premium = quote.result.get("case_gross_annual_premium") if quote and quote.result else None

    return {
        "member_count": len(census),
        "suggested_premium": round(suggested, 2),
        "suggested_per_member": round(suggested / len(census), 2) if census else None,
        "risk_premium": priced["case_risk_premium"],
        "trend_pct": trend_pct,
        "industry": case.industry,
        "nationality_factor": nationality_factor,
        "nationality_applied": nationality_factor != 1.0,
        "nationality_mix": mix,
        "categories": priced["categories"],
        "rate_card_premium": card_premium,
        "gap_vs_rate_card": round(suggested - card_premium, 2) if card_premium else None,
        "gap_pct": round((suggested / card_premium - 1) * 100, 1) if card_premium else None,
        # How much of the price rests on this case's own segments rather
        # than on broader fallbacks - the confidence statement on the total.
        "weighted_credibility": (
            round(
                sum((c["weighted_credibility"] or 0) * (c["expected_claims"] or 0) for c in priced["categories"])
                / sum(c["expected_claims"] or 0 for c in priced["categories"]),
                4,
            )
            if sum(c["expected_claims"] or 0 for c in priced["categories"]) else 0.0
        ),
        "fallback_member_count": sum(c["fallback_member_count"] for c in priced["categories"]),
    }


@router.get("/cases/{case_id}/nationality-mix-pricing")
def get_case_nationality_mix_pricing(
    case_id: int,
    require_pricing_ready: bool = Query(True, description="Only let nationalities with enough book exposure contribute their own factor"),
    as_of: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """What this enquiry's nationality mix is worth on the price.

    A scheme that is mostly a nationality running at 0.75x on the book is
    genuinely cheaper to insure than the rate card assumes and can be
    priced to win; one that is mostly a 1.4x nationality is not, and
    quoting it at card rates is how a book ends up underwater. Returns the
    factor, what it rests on, and the quote with and without it - the
    decision stays with the underwriter.
    """
    from app.api.routes_portfolio_analysis import _get_stored_as_of, _run_analysis, analysis_with_cube
    from app.scoring.rules.nationality_mix_pricing import apply_mix_to_quote, nationality_mix_factor
    from app.scoring.rules.portfolio_analysis import nationality_risk_table

    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    census = _case_census_dicts(case)
    if not census:
        raise HTTPException(status_code=400, detail="Case has no census to measure a nationality mix from")

    try:
        results = _run_analysis(db, as_of=as_of or _get_stored_as_of(db), require_rate_card=False)
    except HTTPException:
        raise HTTPException(
            status_code=400,
            detail="Portfolio Analysis has no membership/claims uploaded - there is no nationality experience to price against",
        )

    mix = nationality_mix_factor(
        census, nationality_risk_table(results), require_pricing_ready=require_pricing_ready
    )

    quote = (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case_id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .first()
    )
    gross = quote.result.get("case_gross_annual_premium") if quote and quote.result else None
    return {**mix, "quote": apply_mix_to_quote(gross, mix) if gross else None}


@router.get("/new-business/rate-card-calibration")
def get_rate_card_calibration(
    target_loss_ratio: float = Query(0.85, description="The loss ratio each cell's suggested price is calibrated to"),
    loading_pct: Optional[float] = Query(None, description="Expense loading as a fraction. Defaults to the standard per-product loading."),
    min_exposure_member_years: float = Query(5.0, description="Below this exposure a cell is reported but not counted as a finding"),
    as_of: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Every rate card cell against what the book says that cell actually
    costs - before anyone quotes, rather than as a footnote after.

    A cell whose implied loss ratio is 140% is not a pricing opinion; it
    is a cell that has lost money on every case it has priced and will
    keep doing so until the number changes. The suggested price sits
    beside the current one rather than replacing it, because a rate card
    is a commercial document too and a cell may be knowingly held below
    cost to win a segment.
    """
    from app.api.routes_portfolio_analysis import _get_stored_as_of, _run_analysis, analysis_with_cube
    from app.scoring.rules.burning_cost_cube import burning_cost_cube
    from app.scoring.rules.rate_card_calibration import (
        calibration_summary_by_product,
        rate_card_calibration,
    )

    rate_cards = _rate_card_dicts(db)
    if not rate_cards:
        raise HTTPException(status_code=400, detail="No rate card uploaded to calibrate")

    try:
        results, cube = analysis_with_cube(db, as_of=as_of or _get_stored_as_of(db), require_rate_card=False)
    except HTTPException:
        raise HTTPException(
            status_code=400,
            detail="Portfolio Analysis has no membership/claims uploaded - there is no experience to calibrate against",
        )

    if cube["book"]["burning_cost"] is None:
        raise HTTPException(status_code=400, detail="The book has no claims experience to calibrate against")

    # Loading varies per product on the card itself, so the default here
    # is the standard product loading rather than one flat figure - using
    # a single average would misstate the implied loss ratio on every
    # product whose real loading differs from it.
    effective_loading = (
        loading_pct if loading_pct is not None
        else category_loading_pct(rate_cards[0].get("product") or "")
    )
    calibration = rate_card_calibration(
        rate_cards, cube,
        loading_pct=effective_loading,
        target_loss_ratio=target_loss_ratio,
        min_exposure_member_years=min_exposure_member_years,
    )
    calibration["by_product"] = calibration_summary_by_product(calibration)
    calibration["book_burning_cost"] = cube["book"]["burning_cost"]
    return calibration


@router.get("/cases/{case_id}/new-business-quote/burning-cost-comparison")
def get_new_business_quote_burning_cost_comparison(
    case_id: int,
    trend_pct: float = Query(0.0, description="Medical trend to apply to the book's historic experience. Defaults to 0 so the figure is the book's own cost, not a forward projection - set it to compare against a card that prices forward."),
    db: Session = Depends(get_db),
):
    """Compares the latest quote's rate-card price against what
    HealthCross's own already-booked book says this same census costs.

    Priced off the burning cost cube (see burning_cost_cube), not the raw
    per-bucket burning cost this used to use. The difference matters: the
    raw version EXCLUDED any member whose exact (Product, Network, age
    band, gender) bucket was missing or too thin, so the comparison
    silently priced fewer members than the quote did and came in low by
    however many it dropped - on a case with an unusual age or an
    uncommon network, most of them. The cube never drops a member; a cell
    with no experience of its own falls back to the nearest broader cell
    that has some, and says how far it had to fall back.

    A reference for whether the rate card is running rich or thin against
    real experience, not something that overrides the quote. Returns null
    (not an error) when Portfolio Analysis hasn't been uploaded, since
    that's optional supporting data; still 404s when there's no quote to
    compare against.
    """
    from app.api.routes_portfolio_analysis import _get_stored_as_of as _stored_as_of
    from app.scoring.rules.burning_cost_cube import burning_cost_cube
    from app.scoring.rules.expected_cost_pricing import price_by_category

    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    quote = (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case_id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .first()
    )
    if not quote:
        raise HTTPException(status_code=404, detail="No new business quote found for this case")

    try:
        portfolio_results, cube = analysis_with_cube(db, as_of=_stored_as_of(db), require_rate_card=False)
    except HTTPException:
        return None

    census = _case_census_dicts(case)
    rate_cards = _rate_card_dicts(db)
    if cube["book"]["burning_cost"] is None:
        return None

    normalized_categories = _normalize_quote_categories(quote.categories)

    # Each category is priced at ITS OWN product's loading, the same way
    # the quote itself is - using one blended rate would misstate every
    # category whose real loading differs from the average.
    loading_by_category = {
        c["category"]: category_loading_pct(c["product"], c.get("commission_pct"))
        for c in normalized_categories
    }
    # The cube prices on (product, network, ...), which the census rows do
    # not carry - each member's product/network comes from their own
    # category's plan design, exactly as rate-card pricing resolves it.
    category_design = {c["category"]: c for c in normalized_categories}
    priced_census = []
    for member in census:
        design = category_design.get(member.get("category"))
        priced_census.append({
            **member,
            "product": design["product"] if design else None,
            "network": _burning_cost_lookup_network(design["network"]) if design else None,
            "cost_factor": nas_tpa_factor(design["network"]) if design else 1.0,
        })

    comparison = price_by_category(
        priced_census, cube,
        loading_pct_by_category=loading_by_category,
        default_loading_pct=category_loading_pct(""),
        trend_pct=trend_pct,
    )
    # Same shape the frontend already reads, plus what the cube adds.
    comparison["case_gross_annual_premium"] = comparison["case_gross_premium"]
    comparison["uncategorized_member_count"] = sum(
        1 for m in census if m.get("category") not in category_design
    )
    for cat in comparison["categories"]:
        design = category_design.get(cat["category"])
        cat["product"] = design["product"] if design else None
        cat["network"] = design["network"] if design else None
        cat["gross_annual_premium"] = cat["gross_premium"]
        cat["net_annual_premium"] = cat["risk_premium"]
        # Kept for callers that read it, but it now always equals
        # member_count: the raw-bucket version dropped members it could
        # not match exactly, and this one never does. A gap between the
        # two was the bug, not a feature.
        cat["priced_member_count"] = cat["member_count"]
        # How much of this category's figure rests on its own segments
        # versus a broader fallback - the honest confidence statement.
        cat["warnings"] = (
            [f"{cat['fallback_member_count']} of {cat['member_count']} members priced from a broader cell than their own"]
            if cat["fallback_member_count"] else []
        )

    # Line up each category against the rate-card quote's own gross premium
    # so the frontend doesn't have to re-match by category name itself.
    quote_gross_by_category = {
        _normalize_category(c["category"]): c["gross_annual_premium"] for c in quote.result["categories"]
    }
    for cat in comparison["categories"]:
        cat["rate_card_gross_annual_premium"] = quote_gross_by_category.get(cat["category"])

    return comparison


@router.get("/cases/{case_id}/annual-limit-exposure")
def get_case_annual_limit_exposure(case_id: int, db: Session = Depends(get_db)):
    """What the annual limit on this quote would have cost against the
    book, per category.

    The limit is picked from a dropdown with nothing beside it, and the
    portfolio already knows the answer - see
    app/scoring/rules/annual_limit_exposure.py. Read against the whole
    book rather than this client's own claims: a single group's members
    are far too few to say anything about a limit that binds on a
    fraction of a percent of them, and the limit is being priced against
    the pool it will actually sit in.
    """
    from app.api.routes_portfolio_analysis import _claim_dicts_for_large_claims
    from app.scoring.rules.annual_limit_exposure import exposure_for_quoted_limits
    from app.scoring.rules.proposed_benefits import proposed_benefit_summary

    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    quote = (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case_id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .first()
    )
    if not quote:
        raise HTTPException(status_code=404, detail="No quote on this case yet")

    variant_rates = _variant_rate_dicts(db)
    quoted_limits = {
        c["category"]: proposed_benefit_summary(c.get("variant_selections") or {}, variant_rates).get("annual_limit")
        for c in _normalize_quote_categories(quote.categories or [])
    }

    claims = _claim_dicts_for_large_claims(db)
    if not claims:
        raise HTTPException(status_code=400, detail="No portfolio claims uploaded yet")
    return exposure_for_quoted_limits(claims, quoted_limits)


def _card_variant_uplift_pct(quote_result: Optional[dict]) -> float:
    """What the rate card has already charged for this quote's benefit
    selections, as a share of the base rates.

    A richer variant is not a free choice on the card - picking a higher
    pre-existing limit moves the quoted price on its own, through
    price_member's variant_impacts. The opportunity assessment has to
    know that figure or it will suggest a loading for a buy-up the quote
    has already paid for.

    Downgrades net off upgrades deliberately: a plan that buys pre-
    existing up and dental down has been charged the difference, not the
    upgrade alone.
    """
    if not quote_result:
        return 0.0
    base_total = 0.0
    impact_total = 0.0
    for category in quote_result.get("categories") or []:
        for member in category.get("member_breakdown") or []:
            base = member.get("base_price")
            if not base:
                continue
            base_total += base
            impact_total += sum((member.get("variant_impacts") or {}).values())
    return (impact_total / base_total) if base_total else 0.0


def _maternity_claims_by_member(db: Session) -> dict:
    """Maternity claims summed per member, straight off the claims book.

    Kept out of the member-result pipeline deliberately: maternity is the
    one benefit whose cost is conditional on the plan design being
    quoted, so it has to be separable from the rest of a member's claims
    rather than blended into one number with them.
    """
    totals: dict = {}
    rows = db.query(
        models.PortfolioClaimEntry.patient_id,
        models.PortfolioClaimEntry.final_amount,
        models.PortfolioClaimEntry.ip_op_maternity,
    ).all()
    for patient_id, amount, ip_op_maternity in rows:
        if not patient_id or "matern" not in str(ip_op_maternity or "").lower():
            continue
        totals[patient_id] = totals.get(patient_id, 0.0) + (amount or 0.0)
    return totals


@router.get("/cases/{case_id}/opportunity-assessment")
def get_opportunity_assessment(
    case_id: int,
    trend_pct: float = Query(0.10),
    as_of: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Is this opportunity worth writing, and at what price.

    Everything the portal already knows about the case, put through one
    conclusion - see app/scoring/rules/opportunity_risk.py. The factors
    the burning-cost cube already prices are shown and explicitly do NOT
    move the number; only the ones it cannot see are allowed to.
    """
    from app.api.routes_portfolio_analysis import _get_stored_as_of as _stored_as_of
    from app.scoring.rules.benefits_comparison import extract_amount_aed
    from app.scoring.rules.burning_cost_cube import burning_cost_cube, expected_cost_for_census
    from app.scoring.rules.opportunity_risk import assess_opportunity, book_benchmarks
    from app.scoring.rules.proposed_benefits import proposed_benefit_rows, proposed_benefit_summary

    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    census = _case_census_dicts(case)
    if not census:
        raise HTTPException(status_code=400, detail="Upload a census for this case first")

    try:
        results, cube = analysis_with_cube(db, as_of=as_of or _stored_as_of(db), require_rate_card=False)
    except HTTPException:
        raise HTTPException(
            status_code=400,
            detail="Portfolio Analysis has no membership/claims uploaded - there is no experience to assess against",
        )

    # A case with a census but no plan design yet still gets an
    # assessment. Product and Network are two of the cube's dimensions,
    # so without them each member prices at a broader cell - which the
    # credibility factor then reports honestly, rather than the whole
    # view refusing to appear until every dropdown is set.
    categories = _resolve_auto_quote_categories(case, db) or []
    design_by_category = {c["category"]: c for c in categories}
    priced_census = [
        {
            **member,
            "product": (design_by_category.get(member.get("category")) or {}).get("product"),
            "network": _burning_cost_lookup_network(
                (design_by_category.get(member.get("category")) or {}).get("network")
            ),
            # Read off the network the case actually quoted, not the MSH
            # name standing in for it - once substituted it looks like
            # an MSH network and would carry no adjustment.
            "cost_factor": nas_tpa_factor(
                (design_by_category.get(member.get("category")) or {}).get("network")
            ),
        }
        for member in census
    ]
    priced = expected_cost_for_census(priced_census, cube)
    priced_members = [m for m in priced["members"] if m.get("expected_cost") is not None]

    # The risk price and the card price, from the same two places the
    # Risk-based price card already reads them, so the two views can
    # never disagree about what is being compared.
    risk_price = None
    try:
        risk_price = get_risk_based_price(
            case_id, trend_pct=trend_pct, apply_nationality=True, as_of=as_of, db=db
        )["suggested_premium"]
    except HTTPException:
        pass

    quote = (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case_id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .first()
    )
    quoted_price = quote.result.get("case_gross_annual_premium") if quote and quote.result else None

    # The plan being proposed, and how it sits against the incumbent's.
    variant_rates = _variant_rate_dicts(db)
    quoted_categories = _normalize_quote_categories(quote.categories or []) if quote else []
    selections = quoted_categories[0].get("variant_selections") if quoted_categories else {}
    proposed_summary = proposed_benefit_summary(selections or {}, variant_rates)
    existing_plan = next((p for p in case.benefit_plans if p.role == "existing"), None)
    comparison_rows = proposed_benefit_rows(
        existing_plan.standard_summary if existing_plan else None, selections or {}, variant_rates
    )

    maternity_value = proposed_summary.get("maternity_limit") or ""
    maternity_covered = bool(maternity_value) and "not covered" not in maternity_value.lower()
    existing_maternity = (existing_plan.standard_summary or {}).get("maternity_limit") if existing_plan else None
    proposed_amount = extract_amount_aed(maternity_value)
    existing_amount = extract_amount_aed(existing_maternity)
    maternity_richer = bool(proposed_amount and existing_amount and proposed_amount > existing_amount)

    return assess_opportunity(
        census_rows=census,
        priced_members=priced_members,
        benchmarks=book_benchmarks(results, _maternity_claims_by_member(db)),
        risk_price_aed=risk_price,
        quoted_price_aed=quoted_price,
        comparison_rows=comparison_rows,
        proposed_summary=proposed_summary,
        maternity_covered=maternity_covered,
        maternity_richer_than_incumbent=maternity_richer,
        card_variant_uplift_pct=_card_variant_uplift_pct(quote.result if quote else None),
    )


def _issued_quote_values(plan) -> dict:
    """The proposed side as the issued quote document states it.

    Placeholders are dropped rather than passed through: a field the
    quote parser could not find comes back as "Not specified in source
    document", and letting that override a value the rate card DID
    resolve would replace a real answer with an apology.
    """
    from app.scoring.rules.benefits_summary import NOT_SPECIFIED

    if plan is None or not plan.standard_summary:
        return {}
    return {
        field: value
        for field, value in plan.standard_summary.items()
        if value and value != NOT_SPECIFIED and not str(value).lower().startswith("not found by ocr")
    }


@router.get("/cases/{case_id}/price-comparison")
def get_price_comparison(
    case_id: int,
    trend_pct: float = Query(0.10),
    as_of: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """What the price should have been, and what actually went out.

    Four numbers - expected claims, the risk-based price, the rate card
    price, and the premium on the quote the broker actually received -
    and the gaps between them. See
    app/scoring/rules/price_comparison.py.

    The last gap is the one nothing else in the portal has ever shown: a
    discount agreed in a meeting leaves the computed quote untouched and
    the issued document different, and the two are never reconciled until
    the account renews badly.
    """
    from app.scoring.rules.price_comparison import compare_prices, issued_price_from_plans

    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    quote = (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case_id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .first()
    )
    card_price = quote.result.get("case_gross_annual_premium") if quote and quote.result else None

    issued = issued_price_from_plans([
        {
            "gross_premium": p.gross_premium,
            "member_count": p.member_count,
            "category": p.category,
            "plan_name": p.plan_name,
        }
        for p in case.benefit_plans
        if p.role == "quoted"
    ])

    risk_price = None
    expected_claims = None
    try:
        risk = get_risk_based_price(
            case_id, trend_pct=trend_pct, apply_nationality=True, as_of=as_of, db=db
        )
        risk_price = risk["suggested_premium"]
        expected_claims = risk["risk_premium"]
    except HTTPException:
        # The risk price needs a census, a plan design and a book to
        # price against. Without it the issued-vs-card comparison still
        # stands on its own, and is the half of this view that does not
        # depend on any model at all.
        pass

    member_count = issued["member_count"] or len(case.census_records) or None
    loading_pct = category_loading_pct("")
    if quote and quote.categories:
        loadings = [
            category_loading_pct(c.get("product"), c.get("commission_pct"))
            for c in _normalize_quote_categories(quote.categories)
            if c.get("product")
        ]
        if loadings:
            loading_pct = sum(loadings) / len(loadings)

    return {
        **compare_prices(
            expected_claims=expected_claims,
            risk_price=risk_price,
            card_price=card_price,
            issued_price=issued["issued_price"],
            loading_pct=loading_pct,
            member_count=member_count,
        ),
        "issued_quote": issued,
        "trend_pct": trend_pct,
    }


def _latest_claims_report_dict(case) -> Optional[dict]:
    """This case's most recent claims report, as the plain dict
    experience_pricing works from.

    Most recent by report period end rather than upload order: a case can
    carry an older report uploaded later, and pricing off the stale one
    because it arrived second would be silent and wrong.
    """
    reports = [r for r in case.claims_reports if r.report_period_end]
    if not reports:
        return None
    report = max(reports, key=lambda r: r.report_period_end)
    return {
        "report_period_start": report.report_period_start,
        "report_period_end": report.report_period_end,
        "total_paid": report.total_paid,
        "reported_not_paid": report.reported_not_paid,
        "incurred_not_reported": report.incurred_not_reported,
        "opening_members": report.opening_members,
        "closing_members": report.closing_members,
    }


@router.get("/cases/{case_id}/experience-price")
def get_experience_price(
    case_id: int,
    trend_pct: float = Query(0.10),
    target_loss_ratio: float = Query(0.85, description="The loss ratio the suggested premium is built to land on"),
    benefit_uplift_pct: float = Query(0.0, description="How much richer the proposal is than the plan these claims were incurred on"),
    as_of: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """What this group's OWN claims say it should be priced at.

    The burning-cost cube says what members like these cost across the
    book. A claims report from the incumbent says what these actual
    people cost, and where the group carries enough exposure the second
    answer is worth more - see app/scoring/rules/experience_pricing.py.

    Returns None-safe: a case with no claims report gets
    has_experience=False and the book's own number, rather than an error.
    That is the common case on a new enquiry and is not a failure.
    """
    from app.scoring.rules.experience_pricing import (
        price_from_experience,
        premium_for_target_loss_ratio,
    )

    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    census = _case_census_dicts(case)
    if not census:
        raise HTTPException(status_code=400, detail="Upload a census for this case first")

    # The book's own estimate for exactly these members - the complement
    # the group's experience is blended against.
    book_expected = None
    try:
        # Every argument passed explicitly: calling an endpoint function
        # in-process leaves anything omitted as the Query() default
        # object rather than its value, which then fails deep inside the
        # arithmetic with a type error that names neither the endpoint
        # nor the argument.
        book_expected = get_risk_based_price(
            case_id, trend_pct=0.0, apply_nationality=True, as_of=as_of, db=db
        )["risk_premium"]
    except HTTPException:
        pass

    report = _latest_claims_report_dict(case)
    priced = (
        price_from_experience(report, book_expected, len(census), trend_pct, benefit_uplift_pct)
        if report else None
    )

    loading_pct = category_loading_pct("")
    quote = (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case_id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .first()
    )
    if quote and quote.categories:
        loadings = [
            category_loading_pct(c.get("product"), c.get("commission_pct"))
            for c in _normalize_quote_categories(quote.categories)
            if c.get("product")
        ]
        if loadings:
            loading_pct = sum(loadings) / len(loadings)

    expected_claims = priced["expected_claims"] if priced else (
        book_expected * (1 + trend_pct) if book_expected else None
    )
    quoted_price = quote.result.get("case_gross_annual_premium") if quote and quote.result else None

    return {
        "has_experience": bool(priced),
        "experience": priced,
        "book_expected_claims": round(book_expected, 2) if book_expected else None,
        "expected_claims": round(expected_claims, 2) if expected_claims else None,
        "loading_pct": loading_pct,
        "quoted_price": quoted_price,
        "break_even_premium": premium_for_target_loss_ratio(expected_claims, loading_pct, 1.0) if expected_claims else None,
        "suggested_premium": premium_for_target_loss_ratio(expected_claims, loading_pct, target_loss_ratio) if expected_claims else None,
        "target_loss_ratio": target_loss_ratio,
        "member_count": len(census),
        "implied_loss_ratio_at_quote": (
            round(expected_claims / (quoted_price * (1 - loading_pct)), 4)
            if expected_claims and quoted_price and quoted_price > 0 else None
        ),
    }


#: Diagnosis keywords that mark a claim as chronic or metabolic. Coarse
#: on purpose: this reads the DHA report's own top-10 diagnosis text, not
#: a coded classification, so it looks for the disease families that
#: actually recur rather than trying to be a clinical taxonomy.
_CHRONIC_KEYWORDS = (
    "diabet", "hypertens", "hyperlipid", "cholesterol", "athscl", "atheroscler",
    "coronary", "cardiac", "ischaem", "ischem", "asthma", "renal failure",
    "thyroid", "obesity",
)


def _chronic_share_of_claims(report: Optional[dict]) -> Optional[float]:
    """How much of the paid claims sit in chronic or metabolic disease.

    Chronic conditions are the ones that transfer with the member and
    claim from month one, so their share is a different statement from
    the total - a group at the same cost made of accidents is a very
    different renewal.
    """
    if not report:
        return None
    breakdown = report.get("diagnosis_breakdown") or []
    total = report.get("total_paid")
    if not breakdown or not total:
        return None
    # The parser's own row shape is {"label", "value", "count", ...} -
    # see app/ingestion/claims_report.py. Reading "diagnosis"/"total"
    # instead silently summed nothing and reported a chronic-heavy book
    # as 0% chronic, which is the most flattering possible answer.
    chronic = sum(
        row.get("value") or 0.0
        for row in breakdown
        if any(word in str(row.get("label") or "").lower() for word in _CHRONIC_KEYWORDS)
    )
    return round(chronic / total, 4) if total else None


@router.get("/cases/{case_id}/underwriting-report")
def get_underwriting_report(
    case_id: int,
    trend_pct: float = Query(0.10),
    target_loss_ratio: float = Query(0.85),
    as_of: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Everything the four-page underwriting document needs, in one call.

    Assembled server-side rather than stitched together in the browser so
    the printed document and the screen can never disagree about what
    this case is - and so the same figures are available to anything else
    that wants them later.
    """
    from app.scoring.rules.risk_scorecard import (
        age_profile_score,
        benefit_design_score,
        build_scorecard,
        chronic_score,
        claims_experience_score,
        gender_maternity_score,
        group_size_score,
        rate_adequacy_score,
        sensitivity,
        stress_absorbed,
    )
    from app.scoring.rules.experience_pricing import premium_for_target_loss_ratio

    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    census = _case_census_dicts(case)
    if not census:
        raise HTTPException(status_code=400, detail="Upload a census for this case first")

    experience = get_experience_price(
        case_id,
        trend_pct=trend_pct,
        target_loss_ratio=target_loss_ratio,
        benefit_uplift_pct=0.0,
        as_of=as_of,
        db=db,
    )
    expected_claims = experience["expected_claims"]
    loading_pct = experience["loading_pct"]
    quoted = experience["quoted_price"]
    break_even = experience["break_even_premium"]
    technical = experience["suggested_premium"]

    # The rate card's own number, for the pricing bridge - what the card
    # would have charged before the group's own claims were read.
    card_price = None
    try:
        card_price = get_risk_based_price(
            case_id, trend_pct=trend_pct, apply_nationality=True, as_of=as_of, db=db
        )["suggested_premium"]
    except HTTPException:
        pass

    report = _latest_claims_report_dict(case)
    full_report = next(
        (r for r in sorted(case.claims_reports, key=lambda r: r.report_period_end or date.min, reverse=True)
         if r.report_period_end), None
    )
    chronic_share = _chronic_share_of_claims(
        {"diagnosis_breakdown": full_report.diagnosis_breakdown, "total_paid": full_report.total_paid}
        if full_report else None
    )

    proposed, comparison_rows = _proposed_and_comparison(case, db)
    # The same call the Benefits tab makes, so the printed comparison and
    # the on-screen one cannot disagree about what was offered.
    comparison_categories = get_existing_vs_proposed(case_id, db=db)["categories"]
    maternity_value = (proposed or {}).get("maternity_limit") or ""
    pharmacy_value = (proposed or {}).get("pharmacy_limit_and_coinsurance") or ""
    deductible_value = (proposed or {}).get("deductible") or ""

    employees = [m for m in census if str(m.get("relation") or "").strip().lower() == "employee"]
    employee_ages = [m["age"] for m in employees if m.get("age") is not None]
    # Maternity exposure is read off the census summary rather than
    # counted again here. Counting it twice gave the report two answers
    # for one question - the dashboard said 24.1% and the census page
    # 18.5%, because one counted every female 20-45 and the other the
    # married 18-40 the rest of the portal prices on.
    census_summary = _census_summary_or_none(case) or {}
    maternity_share = census_summary.get("maternity_risk_pct")

    own_vs_book = None
    if experience.get("experience"):
        blend = experience["experience"]["blend"]
        if blend.get("own_rate") and blend.get("book_rate"):
            own_vs_book = round(blend["own_rate"] / blend["book_rate"], 4)

    scorecard = build_scorecard({
        "claims_experience": claims_experience_score(own_vs_book),
        "group_size": group_size_score(len(census)),
        "age_profile": age_profile_score(
            sum(employee_ages) / len(employee_ages) if employee_ages else None
        ),
        "gender_maternity": gender_maternity_score(
            maternity_share,
            maternity_capped=bool(_amount_in(maternity_value)),
        ),
        "benefit_design": benefit_design_score(
            has_deductible=bool(deductible_value) and "nil" not in deductible_value.lower(),
            pharmacy_capped=bool(_amount_in(pharmacy_value)),
            richer_than_incumbent_count=sum(
                1 for r in (comparison_rows or []) if r.get("direction") == "improved"
            ),
            # An unconfigured case has no plan to score. Without this it
            # reads as a design with every control missing.
            plan_designed=bool(proposed),
        ),
        "chronic_pre_existing": chronic_score(
            chronic_share,
            covered_day_one="not covered" not in str((proposed or {}).get("pre_existing_chronic_limit") or "").lower(),
        ),
        "rate_adequacy": rate_adequacy_score(quoted, break_even),
    })

    return {
        "case": {
            "id": case.id,
            "company_name": case.company_name,
            "broker_name": case.broker_name,
            "industry": case.industry,
            "member_count": len(census),
        },
        "experience": experience,
        "scorecard": scorecard,
        "pricing_bridge": {
            "card_price": card_price,
            "technical_price": technical,
            "commercial_price": quoted,
            "break_even": break_even,
            "card_to_technical_pct": (
                round(technical / card_price - 1, 4) if (card_price and technical) else None
            ),
            "technical_to_commercial_pct": (
                round(quoted / technical - 1, 4) if (technical and quoted) else None
            ),
        },
        "sensitivity": sensitivity(
            expected_claims,
            {"quoted": quoted, "technical": technical, "break_even": break_even},
            loading_pct,
        ),
        "stress_absorbed": {
            "quoted": stress_absorbed(expected_claims, quoted, loading_pct),
            "technical": stress_absorbed(expected_claims, technical, loading_pct),
        },
        "chronic_share_of_claims": chronic_share,
        "claims_report": _claims_report_summary(full_report),
        "target_premiums": {
            f"{int(t * 100)}%": premium_for_target_loss_ratio(expected_claims, loading_pct, t)
            for t in (1.0, 0.95, 0.90, 0.85, 0.80)
        } if expected_claims else {},
        "census": census_summary or None,
        "by_category": _premium_by_category_or_none(case),
        "benefits": {"categories": [
            {"category": c["category"], "existing_plan_name": c["existing_plan_name"],
             "product": c["product"], "network": c["network"], "rows": c["rows"]}
            for c in comparison_categories
        ]},
        "decision": _decision(quoted, technical, break_even, scorecard, expected_claims, loading_pct),
    }


@router.get("/cases/{case_id}/underwriting-report.html", response_class=HTMLResponse,
            include_in_schema=False)
def get_underwriting_report_html(
    case_id: int,
    trend_pct: float = Query(0.10),
    target_loss_ratio: float = Query(0.85),
    as_of: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """The same report as a page the browser can just open.

    This exists because of how the print used to work. The button ran six
    fetches and then called window.open, by which time the browser no
    longer counted the new tab as a response to the click and silently
    blocked it - a button that appeared to do nothing. Pointing it at a
    URL instead makes the open synchronous and the blocker has nothing to
    object to.

    An unexpected failure renders the reason ON THE PAGE rather than the
    browser's bare "Internal Server Error". The report reads a dozen
    parts of a case, any of which a real upload can leave in a shape
    nothing anticipated, and "Internal Server Error" sends whoever hit
    the button hunting through a server console for the one line that
    says which. This is an internal tool - the traceback belongs where
    the person who needs it is already looking.
    """
    from app.reports.underwriting_report import render_underwriting_report

    try:
        payload = get_underwriting_report(
            case_id, trend_pct=trend_pct, target_loss_ratio=target_loss_ratio, as_of=as_of, db=db
        )
        return HTMLResponse(render_underwriting_report(payload))
    except HTTPException as exc:
        # A deliberate refusal - "upload a census first" and the like.
        # It is an answer, not a fault, so it is shown as one.
        return HTMLResponse(_report_problem_page(case_id, str(exc.detail), None),
                            status_code=exc.status_code)
    except Exception as exc:  # noqa: BLE001 - the whole point is to catch it
        import traceback

        return HTMLResponse(
            _report_problem_page(case_id, f"{type(exc).__name__}: {exc}", traceback.format_exc()),
            status_code=500,
        )


def _report_problem_page(case_id: int, message: str, detail: Optional[str]) -> str:
    """Why the document could not be built, on the page where it should
    have been.
    """
    import html as _html

    trace = (f'<pre>{_html.escape(detail)}</pre>' if detail else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Underwriting report - case {case_id}</title>
<style>
 body{{font:14px/1.6 system-ui,-apple-system,sans-serif;background:#f2f8fc;color:#1c2a48;
   margin:0;padding:44px}}
 .box{{max-width:900px;margin:0 auto;background:#fff;border:1px solid #dfe8f0;padding:32px 36px}}
 h1{{font-size:19px;margin:0 0 6px;color:#1c2947}}
 .case{{font:12px ui-monospace,Menlo,monospace;color:#6b7789;letter-spacing:.06em;
   text-transform:uppercase;margin-bottom:18px}}
 .msg{{background:#fbecea;border-left:4px solid #c8443f;padding:13px 16px;margin:0 0 20px;
   font-weight:600;color:#8f2f2b}}
 pre{{background:#f7fafd;border:1px solid #dfe8f0;padding:14px;overflow-x:auto;
   font:11.5px/1.5 ui-monospace,Menlo,monospace;color:#3e4963;white-space:pre-wrap}}
 p{{color:#46586e;max-width:70ch}}
</style></head><body><div class="box">
 <div class="case">Case {case_id} &middot; Underwriting report</div>
 <h1>This document could not be built</h1>
 <p class="msg">{_html.escape(message)}</p>
 <p>Nothing on the case has been changed. Send the block below to whoever
    maintains the portal - it names the exact line that stopped.</p>
 {trace}
</div></body></html>"""


#: Below this share of the technical price, a discount stops being a
#: commercial decision and becomes an underwriting one.
DISCOUNT_AUTHORITY_FLOOR = 0.95


def _decision(
    quoted: Optional[float],
    technical: Optional[float],
    break_even: Optional[float],
    scorecard: dict,
    expected_claims: Optional[float],
    loading_pct: float,
) -> dict:
    """What to do, and who is allowed to decide it.

    A report that stops at "the loss ratio is 173%" leaves the reader to
    work out on their own whether that means decline, re-rate, or sign.
    This says it: the minimum price, how far the quote sits from it, and
    the point past which the answer stops being the account handler's to
    give.
    """
    recommended_minimum = technical
    room = None
    room_aed = None
    if quoted and recommended_minimum:
        room = round(quoted / recommended_minimum - 1, 4)
        room_aed = round(quoted - recommended_minimum, 2)

    band = scorecard.get("overall_band")

    if not quoted or not break_even:
        verdict, headline = "incomplete", "Not enough on file to price this"
    elif quoted < break_even:
        verdict, headline = "decline", "Below break-even - do not issue at this price"
    elif recommended_minimum and quoted < recommended_minimum:
        verdict, headline = "refer", "Priced below the technical minimum - refer before issuing"
    elif band != "high" and room_aed and room_aed > 0:
        # A good account, and worth saying so. "Priced at or above the
        # technical minimum" is true of a marginal account scraping over
        # the line and of one with real headroom, and an account handler
        # reads the same sentence either way. The room is what tells
        # them how hard they can compete for it.
        verdict = "proceed"
        headline = (f"AED {room_aed:,.0f} of room above the technical price - "
                    f"scope to compete hard and still clear it")
    else:
        verdict, headline = "proceed", "Priced at or above the technical minimum"

    floor = round(recommended_minimum * DISCOUNT_AUTHORITY_FLOOR, 2) if recommended_minimum else None
    return {
        "verdict": verdict,
        "headline": headline,
        "recommended_minimum": recommended_minimum,
        "break_even": break_even,
        "quoted": quoted,
        "room_vs_minimum_pct": room,
        # What can be conceded before the quote drops below the price
        # that lands on target - the "how aggressive can we be" figure,
        # in money rather than as a ratio somebody has to convert in a
        # meeting. Negative means the concession has already been made.
        "negotiation_room_aed": room_aed,
        "account_quality": (
            "good" if verdict == "proceed" and band != "high" and (room_aed or 0) > 0
            else "workable" if verdict == "proceed"
            else "poor" if verdict == "decline"
            else "refer" if verdict == "refer" else None
        ),
        # Discount is taken off the claims-funding part of the price, not
        # off the whole of it, so a 5% cut in price is more than a 5%
        # move in loss ratio. Stating the floor in money avoids that
        # arithmetic being done in a meeting.
        "discount_authority_floor": floor,
        "referral_required": verdict in {"decline", "refer"},
        "risk_band": scorecard.get("overall_band"),
        "risk_score": scorecard.get("overall_score"),
        "expected_claims": expected_claims,
        "loading_pct": loading_pct,
    }


def _census_summary_or_none(case) -> Optional[dict]:
    from app.scoring.rules.census_summary import census_demographic_summary

    if not case.census_records:
        return None
    return census_demographic_summary([
        {"age": c.age, "gender": c.gender, "marital_status": c.marital_status,
         "relation": c.relation, "nationality_zone": c.nationality_zone,
         "nationality": c.nationality}
        for c in case.census_records
    ])


def _premium_by_category_or_none(case) -> Optional[dict]:
    quoted_plans = [p for p in case.benefit_plans if p.role == "quoted"]
    if not quoted_plans:
        return None
    categories = [
        {
            "category": p.category,
            "plan_name": p.plan_name,
            "network": p.network_type,
            "member_count": p.member_count,
            "gross_premium": p.gross_premium,
            "premium_per_member": (
                round(p.gross_premium / p.member_count, 2)
                if p.gross_premium is not None and p.member_count else None
            ),
        }
        for p in quoted_plans
    ]
    total_members = sum(c["member_count"] or 0 for c in categories)
    total_premium = sum(c["gross_premium"] or 0 for c in categories)
    return {
        "categories": categories,
        "total_members": total_members,
        "total_gross_premium": total_premium,
        "blended_premium_per_member": round(total_premium / total_members, 2) if total_members else None,
    }


def _amount_in(text: str) -> bool:
    """True when a benefit value states an actual figure rather than
    running to the plan's own limit. "USD 2,500" is a cap; "Annual
    Limit" is the absence of one.
    """
    from app.scoring.rules.benefits_comparison import extract_amount_aed

    return extract_amount_aed(text) is not None


def _proposed_and_comparison(case, db: Session):
    from app.scoring.rules.proposed_benefits import proposed_benefit_rows, proposed_benefit_summary

    quote = (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case.id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .first()
    )
    categories = _normalize_quote_categories(quote.categories or []) if quote else []
    selections = categories[0].get("variant_selections") if categories else {}
    variant_rates = _variant_rate_dicts(db)
    proposed = proposed_benefit_summary(selections or {}, variant_rates)
    existing = next((p for p in case.benefit_plans if p.role == "existing"), None)
    rows = proposed_benefit_rows(
        existing.standard_summary if existing else None, selections or {}, variant_rates
    )
    return proposed, rows


def _claims_report_summary(report) -> Optional[dict]:
    if not report:
        return None
    return {
        "report_period_start": report.report_period_start.isoformat() if report.report_period_start else None,
        "report_period_end": report.report_period_end.isoformat() if report.report_period_end else None,
        "total_paid": report.total_paid,
        "reported_not_paid": report.reported_not_paid,
        "incurred_not_reported": report.incurred_not_reported,
        "opening_members": report.opening_members,
        "closing_members": report.closing_members,
        "monthly_paid": report.monthly_paid or [],
        "claims_by_member_type_value": report.claims_by_member_type_value or [],
        "diagnosis_breakdown": (report.diagnosis_breakdown or [])[:10],
    }
