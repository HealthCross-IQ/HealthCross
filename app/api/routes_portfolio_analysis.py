"""Portfolio Analysis - checks HealthCross's own already-booked book
against the New Business rate card (see
app/scoring/rules/portfolio_analysis.py). Three admin upload endpoints
refresh the book's own membership/claims/group-product-mapping data
wholesale (same pattern as the rate card itself); the analysis endpoint
joins all of it against whatever rate card is currently active.
"""
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session

from app.database import get_db
from app.ingestion.group_product_mapping import parse_group_product_mapping
from app.ingestion.portfolio_claims import parse_portfolio_claims
from app.ingestion.portfolio_members import parse_portfolio_members
from app.ingestion.subgroup_mapping import parse_subgroup_mapping
from app.models import db_models as models
from app.models import schemas
from app.reference.network_type_mapping import is_out_of_scope_network_type, map_network_type
from app.scoring.rules.portfolio_analysis import (
    analyze_portfolio_member,
    group_claims_by_beneficiary,
    normalize_subgroup_key,
    resolve_group_product,
    resolve_master_client,
    summarize_burning_cost_by_age_gender,
    summarize_burning_cost_by_product_network,
    summarize_burning_cost_by_product_network_age_gender,
    summarize_portfolio,
)

router = APIRouter(prefix="/portfolio-analysis", tags=["portfolio-analysis"])


def _get_stored_as_of(db: Session) -> Optional[date]:
    snapshot = db.query(models.PortfolioDataSnapshot).first()
    return snapshot.data_as_of_date if snapshot else None


def _set_stored_as_of(db: Session, as_of_date: date) -> None:
    snapshot = db.query(models.PortfolioDataSnapshot).first()
    if snapshot:
        snapshot.data_as_of_date = as_of_date
    else:
        db.add(models.PortfolioDataSnapshot(data_as_of_date=as_of_date))
    db.commit()


@router.get("/data-as-of", response_model=schemas.PortfolioDataAsOfOut)
def get_data_as_of(db: Session = Depends(get_db)):
    return schemas.PortfolioDataAsOfOut(data_as_of_date=_get_stored_as_of(db))


@router.post("/data-as-of", response_model=schemas.PortfolioDataAsOfOut)
def set_data_as_of(payload: schemas.PortfolioDataAsOfIn, db: Session = Depends(get_db)):
    """Sets the book's own data-as-of (production/extract) date - earned
    premium proration measures elapsed policy time against this date by
    default (see app/scoring/rules/portfolio_analysis.py), rather than
    whatever calendar day the analysis happens to be run on, which can be
    weeks after the data was actually pulled.
    """
    _set_stored_as_of(db, payload.data_as_of_date)
    return schemas.PortfolioDataAsOfOut(data_as_of_date=payload.data_as_of_date)


@router.post("/members/upload", response_model=schemas.PortfolioUploadOut)
def upload_portfolio_members(
    file: UploadFile = File(...),
    data_as_of: Optional[date] = Form(None, description="This export's own production/extract date, if known"),
    db: Session = Depends(get_db),
):
    rows = parse_portfolio_members(file.file, file.filename)
    if not rows:
        raise HTTPException(status_code=400, detail="No member rows found in this file")

    db.query(models.PortfolioMember).delete()
    db.bulk_insert_mappings(models.PortfolioMember, rows)
    db.commit()
    if data_as_of is not None:
        _set_stored_as_of(db, data_as_of)
    return schemas.PortfolioUploadOut(rows_ingested=len(rows))


@router.post("/claims/upload", response_model=schemas.PortfolioUploadOut)
def upload_portfolio_claims(
    file: UploadFile = File(...),
    data_as_of: Optional[date] = Form(None, description="This export's own production/extract date, if known"),
    db: Session = Depends(get_db),
):
    rows = parse_portfolio_claims(file.file, file.filename)
    if not rows:
        raise HTTPException(status_code=400, detail="No claim rows found in this file")

    db.query(models.PortfolioClaimEntry).delete()
    db.bulk_insert_mappings(models.PortfolioClaimEntry, rows)
    db.commit()
    if data_as_of is not None:
        _set_stored_as_of(db, data_as_of)
    return schemas.PortfolioUploadOut(rows_ingested=len(rows))


@router.post("/group-product-mapping/upload", response_model=schemas.PortfolioUploadOut)
def upload_group_product_mapping(file: UploadFile = File(...), db: Session = Depends(get_db)):
    rows = parse_group_product_mapping(file.file, file.filename)
    if not rows:
        raise HTTPException(status_code=400, detail="No group/product rows found in this file")

    db.query(models.GroupProductMapping).delete()
    db.bulk_insert_mappings(models.GroupProductMapping, rows)
    db.commit()
    return schemas.PortfolioUploadOut(rows_ingested=len(rows))


@router.post("/subgroup-mapping/upload", response_model=schemas.PortfolioUploadOut)
def upload_subgroup_mapping(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """The dedicated Subgroup -> Master Group mapping sheet (two columns:
    Subgroup, Group Name) - the authoritative source for master_client
    resolution, since PortfolioMember's own MASTERCONTRACT field on the
    real system export isn't reliable for this.
    """
    rows = parse_subgroup_mapping(file.file, file.filename)
    if not rows:
        raise HTTPException(status_code=400, detail="No subgroup/master rows found in this file")

    db.query(models.SubgroupMasterMapping).delete()
    db.bulk_insert_mappings(models.SubgroupMasterMapping, rows)
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
            "master_client_name": m.master_client_name,
            "product_name": m.product_name,
            "category": m.category,
            "network_type_raw": m.network_type_raw,
            "age": m.age,
            "gender": m.gender,
            "marital_status": m.marital_status,
            "relation": m.relation,
            "nationality_zone": m.nationality_zone,
            "residence_emirate": m.residence_emirate,
            "region": m.region,
            "actual_gross_premium": m.actual_gross_premium,
            "policy_start_date": m.policy_start_date,
            "policy_end_date": m.policy_end_date,
            "member_start_date": m.member_start_date,
            "member_end_date": m.member_end_date,
        }
        for m in db.query(models.PortfolioMember).all()
    ]


#: Result-level fields (present on analyze_portfolio_member's output, only
#: known after pricing/network resolution) that can be filtered on directly
#: - e.g. product=Gold AND network=... to stack more than one filter at
#: once, unlike group_by which only picks what's shown in rows.
_FILTERABLE_RESULT_FIELDS = ("product", "network", "region", "nationality_zone", "gender", "relation", "category", "master_client")


def _run_analysis(
    db: Session,
    as_of: Optional[date] = None,
    policy_year: Optional[str] = None,
    client: Optional[str] = None,
    filters: Optional[Dict[str, str]] = None,
) -> List[dict]:
    members = _member_dicts(db)
    if not members:
        raise HTTPException(status_code=400, detail="No portfolio members uploaded yet")
    if policy_year:
        members = [m for m in members if m.get("policy_start_date") and str(m["policy_start_date"].year) == policy_year]
        if not members:
            raise HTTPException(status_code=400, detail=f"No members found whose policy started in {policy_year}")
    if client:
        members = [m for m in members if (m.get("contract") or m.get("master_contract")) == client]
        if not members:
            raise HTTPException(status_code=400, detail=f"No members found for client '{client}'")
    rate_cards = _rate_card_dicts(db)
    if not rate_cards:
        raise HTTPException(status_code=400, detail="No rate card uploaded yet")
    variant_rates = _variant_rate_dicts(db)

    claims = [
        {
            "patient_id": patient_id,
            "date_of_treatment": date_of_treatment,
            "final_amount": final_amount,
            "claim_status": claim_status,
        }
        for patient_id, date_of_treatment, final_amount, claim_status in db.query(
            models.PortfolioClaimEntry.patient_id,
            models.PortfolioClaimEntry.date_of_treatment,
            models.PortfolioClaimEntry.final_amount,
            models.PortfolioClaimEntry.claim_status,
        ).all()
    ]
    claims_by_beneficiary = group_claims_by_beneficiary(claims)

    group_product_by_name: Dict[str, str] = {
        gp.group_name: gp.product for gp in db.query(models.GroupProductMapping).all()
    }
    subgroup_master_by_name: Dict[str, str] = {
        normalize_subgroup_key(sm.subgroup_name): sm.master_name for sm in db.query(models.SubgroupMasterMapping).all()
    }

    results = [
        analyze_portfolio_member(
            m, group_product_by_name, rate_cards, variant_rates, claims_by_beneficiary,
            as_of=as_of, subgroup_master_by_name=subgroup_master_by_name,
        )
        for m in members
    ]

    for field, value in (filters or {}).items():
        if value:
            results = [r for r in results if str(r.get(field)) == value]
    return results


def _result_filters(
    product: Optional[str] = Query(None, description="Restrict to one Product - stack with other filters/group_by to drill down, e.g. product=Gold + group_by=network"),
    network: Optional[str] = Query(None, description="Restrict to one Network (e.g. 'MSH Platinum')"),
    region: Optional[str] = Query(None, description="Restrict to one region (Dubai/Abu Dhabi/Northern Emirates)"),
    nationality_zone: Optional[str] = Query(None, description="Restrict to one nationality zone"),
    gender: Optional[str] = Query(None, description="Restrict to one gender"),
    relation: Optional[str] = Query(None, description="Restrict to one relation (employee/spouse/child)"),
    category: Optional[str] = Query(None, description="Restrict to one benefit category (e.g. 'Category A')"),
    master_client: Optional[str] = Query(
        None,
        description=(
            "Restrict to one master policy/group - e.g. narrow to one master before switching "
            "group_by=client to see that master's own subgroups broken out"
        ),
    ),
) -> Dict[str, str]:
    return {
        "product": product,
        "network": network,
        "region": region,
        "nationality_zone": nationality_zone,
        "gender": gender,
        "relation": relation,
        "category": category,
        "master_client": master_client,
    }


@router.get("/summary", response_model=schemas.PortfolioSummaryOut)
def portfolio_summary(
    group_by: str = Query(
        "product",
        description=(
            "One of: product, network, region, nationality_zone, client (subgroup), master_client "
            "(master policy - combines all its subgroups into one row), gender, relation, policy_year"
        ),
    ),
    as_of: Optional[date] = Query(
        None,
        description=(
            "Date to compute earned premium as of (each member's premium is prorated by elapsed policy term) - "
            "defaults to the stored data-as-of date (see /portfolio-analysis/data-as-of), or today if none is set"
        ),
    ),
    policy_year: Optional[str] = Query(
        None,
        description=(
            "Restrict to members whose own policy started in this year (e.g. '2026') - a client that's already "
            "renewed can have some members on last year's policy and some on this year's within the same upload; "
            "this lets you compare the two cohorts separately (e.g. group_by=client, policy_year=2026 vs 2025)"
        ),
    ),
    client: Optional[str] = Query(
        None, description="Restrict to one client (matches PortfolioMember.contract, falling back to master_contract) for a client-level drill-down"
    ),
    filters: Dict[str, str] = Depends(_result_filters),
    db: Session = Depends(get_db),
):
    results = _run_analysis(db, as_of=as_of or _get_stored_as_of(db), policy_year=policy_year, client=client, filters=filters)
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


@router.get("/insights")
def portfolio_insights(
    as_of: Optional[date] = Query(None, description="Date to compute earned premium as of - defaults to the stored data-as-of date, or today if none is set"),
    policy_year: Optional[str] = Query(None, description="Restrict to members whose own policy started in this year"),
    client: Optional[str] = Query(None, description="Restrict to one client for a client-level drill-down"),
    filters: Dict[str, str] = Depends(_result_filters),
    db: Session = Depends(get_db),
):
    """Every summary view the Portfolio Insights dashboard needs, computed
    from a SINGLE run of the underlying analysis rather than the 7 separate
    round trips the dashboard used to make (one per group_by dimension,
    plus age/gender) - each of those independently re-fetched every member
    and every claim and re-ran analyze_portfolio_member over the whole
    book from scratch, so the dashboard was doing 7x the necessary work on
    a real ~3,700-member/~80,000-claim-line book. summarize_portfolio/
    summarize_burning_cost_by_age_gender are cheap in-memory regroupings
    of the same already-computed per-member results, so there's no
    correctness difference - only fetches the expensive shared inputs once.
    """
    results = _run_analysis(db, as_of=as_of or _get_stored_as_of(db), policy_year=policy_year, client=client, filters=filters)
    in_scope = [r for r in results if r.get("in_scope", True)]
    out_of_scope_count = len(results) - len(in_scope)
    unmapped_product_count = sum(1 for r in in_scope if not r.get("product"))
    unmapped_network_count = sum(1 for r in in_scope if not r.get("network"))
    rate_cards = _rate_card_dicts(db)

    def _summary(group_by: str) -> schemas.PortfolioSummaryOut:
        return schemas.PortfolioSummaryOut(
            group_by=group_by,
            rows=summarize_portfolio(results, group_by),
            total_members=len(results),
            out_of_scope_member_count=out_of_scope_count,
            unmapped_product_member_count=unmapped_product_count,
            unmapped_network_member_count=unmapped_network_count,
        )

    return {
        "by_product": _summary("product"),
        "by_network": _summary("network"),
        "by_nationality_zone": _summary("nationality_zone"),
        "by_relation": _summary("relation"),
        "by_gender": _summary("gender"),
        "by_policy_year": _summary("policy_year"),
        "by_category": _summary("category"),
        # "client" groups by the member's own subgroup/contract - shown as
        # "by subgroup" here since that's what it means once a single
        # master_client has been picked (its own subgroups broken out).
        "by_subgroup": _summary("client"),
        "by_age_gender": summarize_burning_cost_by_age_gender(results, rate_cards),
    }


@router.get("/members", response_model=List[dict])
def portfolio_member_detail(
    as_of: Optional[date] = Query(
        None, description="Date to compute earned premium as of - defaults to the stored data-as-of date, or today if none is set"
    ),
    policy_year: Optional[str] = Query(None, description="Restrict to members whose own policy started in this year"),
    client: Optional[str] = Query(None, description="Restrict to one client for a client-level drill-down"),
    filters: Dict[str, str] = Depends(_result_filters),
    db: Session = Depends(get_db),
):
    """Every member's own analysis row, unaggregated - for spot-checking a
    specific group/member rather than only seeing the rolled-up summary.
    """
    return _run_analysis(db, as_of=as_of or _get_stored_as_of(db), policy_year=policy_year, client=client, filters=filters)


@router.get("/burning-cost-by-age-gender", response_model=List[dict])
def burning_cost_by_age_gender(
    as_of: Optional[date] = Query(None, description="Date to compute earned premium as of"),
    policy_year: Optional[str] = Query(None, description="Restrict to members whose own policy started in this year"),
    client: Optional[str] = Query(None, description="Restrict to one client for a client-level drill-down"),
    filters: Dict[str, str] = Depends(_result_filters),
    db: Session = Depends(get_db),
):
    """Burning cost bucketed into the SAME (age-band x gender) structure
    the standard pricing rate card itself uses, so each row lines up
    directly against one Male-Price/Female-Price row in the rate card for
    calibration.
    """
    results = _run_analysis(db, as_of=as_of or _get_stored_as_of(db), policy_year=policy_year, client=client, filters=filters)
    rate_cards = _rate_card_dicts(db)
    return summarize_burning_cost_by_age_gender(results, rate_cards)


@router.get("/burning-cost-by-product-network", response_model=List[dict])
def burning_cost_by_product_network(
    as_of: Optional[date] = Query(None, description="Date to compute earned premium as of"),
    db: Session = Depends(get_db),
):
    """Actual burning cost from the already-booked book for every (Product,
    Network) pairing actually present - lets New Business Rating show the
    book's own real claims experience for the same Product/Network a rate
    card row prices, as a reference alongside the card's own price rather
    than something that feeds into or overrides it. Returns an empty list
    (not an error) when Members/Claims/a rate card haven't been uploaded
    yet, since this is optional supporting context for New Business Rating,
    not something that page requires to function.
    """
    try:
        results = _run_analysis(db, as_of=as_of or _get_stored_as_of(db))
    except HTTPException:
        return []
    return summarize_burning_cost_by_product_network(results)


@router.get("/burning-cost-by-product-network-age-gender", response_model=List[dict])
def burning_cost_by_product_network_age_gender(
    as_of: Optional[date] = Query(None, description="Date to compute earned premium as of"),
    db: Session = Depends(get_db),
):
    """Actual burning cost from the already-booked book for every (Product,
    Network, age band, gender) combination actually present - finer than
    /burning-cost-by-product-network, so a New Business case's own age/
    gender mix can be re-priced against the book's real experience for
    that exact slice rather than one flat average (see
    price_case_against_burning_cost). Returns an empty list (not an error)
    when Members/Claims/a rate card haven't been uploaded yet.
    """
    try:
        results = _run_analysis(db, as_of=as_of or _get_stored_as_of(db))
    except HTTPException:
        return []
    rate_cards = _rate_card_dicts(db)
    return summarize_burning_cost_by_product_network_age_gender(results, rate_cards)


@router.get("/clients", response_model=List[str])
def list_clients(db: Session = Depends(get_db)):
    """Distinct client (contract, falling back to master_contract) names in
    the currently-uploaded book - powers the client picker for a
    client-level insights drill-down.
    """
    contracts = {c for (c,) in db.query(models.PortfolioMember.contract).all() if c}
    master_contracts = {
        mc for (mc, c) in db.query(models.PortfolioMember.master_contract, models.PortfolioMember.contract).all()
        if mc and not c
    }
    return sorted(contracts | master_contracts)


@router.get("/master-clients", response_model=List[str])
def list_master_clients(db: Session = Depends(get_db)):
    """Distinct MASTER policy/group names - e.g. one master with 3
    subgroups appears once here, not 3 times - for a master-level-first
    client picker (see master_client filter/group_by). Uses the same
    resolve_master_client() priority as the analysis itself: the uploaded
    Subgroup->Master mapping first, falling back to the raw
    master_contract/contract fields only where no mapping entry exists.
    """
    subgroup_master_by_name: Dict[str, str] = {
        normalize_subgroup_key(sm.subgroup_name): sm.master_name for sm in db.query(models.SubgroupMasterMapping).all()
    }
    rows = db.query(
        models.PortfolioMember.contract, models.PortfolioMember.master_contract, models.PortfolioMember.master_client_name
    ).all()
    masters = {
        resolve_master_client(
            {"contract": contract, "master_contract": master_contract, "master_client_name": master_client_name},
            subgroup_master_by_name,
        )
        for contract, master_contract, master_client_name in rows
    }
    masters.discard(None)
    return sorted(masters)


@router.get("/filter-options", response_model=Dict[str, List[str]])
def portfolio_filter_options(db: Session = Depends(get_db)):
    """Distinct values actually present in the current book for each
    filterable dimension (product/network/region/nationality_zone/gender/
    relation) - powers dropdown filters that can only ever be set to a
    real, matchable value instead of free-text that silently matches
    nothing on a typo or case mismatch.

    Deliberately does NOT run the full rate-card-pricing + claims-matching
    analysis (_run_analysis) - none of these 6 fields need that (no
    standard_premium or actual_claims involved), and this endpoint fires
    on every Portfolio Analysis page load plus after every upload, so
    running the expensive full pipeline here made the whole screen feel
    like it hung on a real multi-thousand-member book.
    """
    members = _member_dicts(db)
    if not members:
        raise HTTPException(status_code=400, detail="No portfolio members uploaded yet")
    group_product_by_name: Dict[str, str] = {
        gp.group_name: gp.product for gp in db.query(models.GroupProductMapping).all()
    }

    products, networks, regions, zones, genders, relations = set(), set(), set(), set(), set(), set()
    for m in members:
        if is_out_of_scope_network_type(m.get("network_type_raw")):
            continue
        network = map_network_type(m.get("network_type_raw"))
        if network:
            networks.add(network)
        product = resolve_group_product(m, group_product_by_name)
        if product:
            products.add(product)
        if m.get("region"):
            regions.add(m["region"])
        if m.get("nationality_zone"):
            zones.add(m["nationality_zone"])
        if m.get("gender"):
            genders.add(m["gender"])
        if m.get("relation"):
            relations.add(m["relation"])

    return {
        "product": sorted(products),
        "network": sorted(networks),
        "region": sorted(regions),
        "nationality_zone": sorted(zones),
        "gender": sorted(genders),
        "relation": sorted(relations),
    }
