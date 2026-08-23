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
from app.ingestion.client_master import parse_client_master
from app.ingestion.group_product_mapping import parse_group_product_mapping
from app.ingestion.portfolio_claims import parse_portfolio_claims
from app.ingestion.portfolio_members import parse_portfolio_members
from app.ingestion.subgroup_mapping import parse_subgroup_mapping
from app.models import db_models as models
from app.models import schemas
from app.reference.network_type_mapping import is_out_of_scope_network_type, map_network_type
from app.scoring.rules.credibility import FULL_CREDIBILITY_MEMBER_YEARS
from app.scoring.rules.portfolio_analysis import (
    DEFAULT_PRICING_CREDIBILITY,
    YEAR_BASES,
    account_calendar_loss_ratio_rows,
    account_loss_ratio_rows,
    nationality_risk_table,
    account_loss_ratio_totals,
    DEFAULT_EXPENSE_RATIO_PCT,
    DEFAULT_LARGE_CLAIM_THRESHOLDS,
    analyze_portfolio_member,
    claims_above_thresholds,
    demographic_summary,
    executive_portfolio_summary,
    group_claims_by_beneficiary,
    normalize_subgroup_key,
    recurring_high_cost_members,
    renewal_due_accounts,
    resolve_group_product,
    resolve_master_client,
    summarize_by_group_size_band,
    summarize_burning_cost_by_age_gender,
    summarize_burning_cost_by_product_network,
    summarize_burning_cost_by_product_network_age_gender,
    summarize_new_vs_renewal,
    summarize_portfolio,
    top_claims_by_value,
    top_members_by_total_claims,
    utilization_by_benefit_category,
    utilization_by_encounter_type,
)
from app.scoring.rules.renewal_intake import (
    account_members,
    census_rows_from_members,
    claim_belongs_to_term,
    current_term_members,
    renewal_intake_profile,
    term_member_windows,
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


@router.post("/client-master/upload", response_model=schemas.PortfolioUploadOut)
def upload_client_master(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Per-master-client reference sheet (Client Name (Master), OPEX,
    Product, Start Date, ...) - principally each client's own real OPEX/
    Loading %, used in place of the flat 33% expense-ratio assumption for
    Combined Ratio wherever a client's own real figure is on file (see
    executive_portfolio_summary's opex_by_client parameter).
    """
    rows = parse_client_master(file.file, file.filename)
    if not rows:
        raise HTTPException(status_code=400, detail="No client rows found in this file")

    db.query(models.ClientMasterInfo).delete()
    db.bulk_insert_mappings(models.ClientMasterInfo, rows)
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
            "nationality": m.nationality,
            "nationality_zone": m.nationality_zone,
            "residence_emirate": m.residence_emirate,
            "region": m.region,
            "actual_gross_premium": m.actual_gross_premium,
            # The Membership export carries BOTH a booked GrossPremium and
            # an ActualGrossPremium; only the latter has ever fed the
            # analysis. Carried through so a caller can report on either -
            # see account_loss_ratio_rows' premium_basis.
            "gross_premium": m.gross_premium,
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
    require_rate_card: bool = True,
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
    if not rate_cards and require_rate_card:
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


@router.get("/executive-summary")
def portfolio_executive_summary(
    as_of: Optional[date] = Query(None, description="Date to compute earned premium as of - defaults to the stored data-as-of date, or today if none is set"),
    policy_year: Optional[str] = Query(None, description="Restrict to members whose own policy started in this year"),
    client: Optional[str] = Query(None, description="Restrict to one client for a client-level drill-down"),
    expense_ratio_pct: float = Query(
        DEFAULT_EXPENSE_RATIO_PCT,
        description="Assumed commission+TPA+admin+HC/management fee load, as a fraction of premium, for the Combined Ratio KPI",
    ),
    filters: Dict[str, str] = Depends(_result_filters),
    db: Session = Depends(get_db),
):
    """"Level 1 - Executive Portfolio": the top-of-page KPI set (Total
    Groups, Total Members, Written/Earned Premium, Incurred Claims, Loss
    Ratio, Combined Ratio, Average Premium per Member) - see
    executive_portfolio_summary for what each one means and how Combined
    Ratio's expense_ratio_pct assumption works. Uses each client's own
    real OPEX/Loading % from the uploaded Client Master sheet wherever
    it's on file (per member, matched by that member's own policy period -
    see resolve_client_opex_pct - for a client whose loading changed
    between renewals), falling back to expense_ratio_pct otherwise.
    """
    results = _run_analysis(db, as_of=as_of or _get_stored_as_of(db), policy_year=policy_year, client=client, filters=filters)
    opex_records_by_client: Dict[str, List[dict]] = defaultdict(list)
    for cm in db.query(models.ClientMasterInfo).all():
        if cm.opex_pct is not None:
            opex_records_by_client[cm.master_client_name].append(
                {"start_date": cm.start_date, "end_date": cm.end_date, "opex_pct": cm.opex_pct}
            )
    return executive_portfolio_summary(
        results, expense_ratio_pct=expense_ratio_pct, opex_records_by_client=opex_records_by_client
    )


@router.get("/account-loss-ratio")
def portfolio_account_loss_ratio(
    as_of: Optional[date] = Query(None, description="Report date to measure elapsed days and earned premium as of - defaults to the stored data-as-of date, or today if none is set"),
    policy_year: Optional[str] = Query(None, description="On underwriting basis, restrict to members whose own policy started in this year. On calendar basis, restrict to this CALENDAR year's rows - a policy incepting in 2025 still contributes to 2026, so the two readings are not interchangeable."),
    client: Optional[str] = Query(None, description="Restrict to one client for a client-level drill-down"),
    default_loading_pct: float = Query(
        DEFAULT_EXPENSE_RATIO_PCT,
        description="Loading (OPEX) fallback for an account with no real figure on file in the uploaded Client Master sheet",
    ),
    year_basis: str = Query(
        "underwriting",
        description="'underwriting' = one row per policy period, each policy year its own cohort (the pricing basis). 'calendar' = one row per calendar year, splitting a year-spanning policy across both years by the days in each (the reporting basis).",
    ),
    premium_basis: str = Query(
        "actual",
        description="Which of the Membership export's own premium columns to build Gross Premium from: 'actual' (ActualGrossPremium - already prorated for each member's own joining/leaving dates, the basis HealthCross underwrites on) or 'booked' (GrossPremium - each member's full annual premium regardless of enrollment).",
    ),
    filters: Dict[str, str] = Depends(_result_filters),
    db: Session = Depends(get_db),
):
    """Per-account loss ratio - one row per account POLICY PERIOD, the
    underwriting view HealthCross tracks its own book on: Paid,
    Outstanding, IBNR, Incurred Claims, Days, Loading, Gross/Earned/Net
    Premium, and both Gross and Net loss ratios. See
    account_loss_ratio_rows for each column's own definition (notably: an
    expired policy reserves no IBNR and earns its FULL annual premium,
    rather than prorating past 100%).

    Loading is each client's own real OPEX % from the uploaded Client
    Master sheet wherever it is on file, resolved per policy period so a
    client whose loading changed between renewals uses the right one for
    each; default_loading_pct is only the fallback.
    """
    # Loss ratio compares each account's own ACTUAL premium against its own
    # ACTUAL claims - the rate card only ever feeds standard_premium (what
    # the card WOULD charge), which no column here uses. Requiring one would
    # block the book's core underwriting view on an unrelated upload.
    #
    # "Year" means different things on the two bases, and applying the
    # wrong one silently answered a different question. On UNDERWRITING
    # basis a year IS the policy period, so the filter belongs upstream,
    # restricting which members are analysed at all. On CALENDAR basis the
    # rows are keyed by calendar year instead, and a policy incepting in
    # 2025 legitimately produces both a 2025 and a 2026 row - so filtering
    # members by inception year there returned every calendar year those
    # policies touched. Calendar basis therefore analyses ALL members and
    # filters the finished ROWS by their own calendar year.
    calendar_basis = year_basis == "calendar"
    results = _run_analysis(
        db, as_of=as_of or _get_stored_as_of(db),
        policy_year=None if calendar_basis else policy_year,
        client=client, filters=filters, require_rate_card=False,
    )
    opex_records_by_client: Dict[str, List[dict]] = defaultdict(list)
    for cm in db.query(models.ClientMasterInfo).all():
        if cm.opex_pct is not None:
            opex_records_by_client[cm.master_client_name].append(
                {"start_date": cm.start_date, "end_date": cm.end_date, "opex_pct": cm.opex_pct}
            )
    effective_as_of = as_of or _get_stored_as_of(db) or date.today()
    if year_basis not in YEAR_BASES:
        raise HTTPException(status_code=400, detail=f"year_basis must be one of {YEAR_BASES}")
    try:
        if year_basis == "calendar":
            # Calendar basis re-matches claims by treatment date per year,
            # so it needs the claim lines themselves rather than the
            # per-member totals already summed against the policy period.
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
            rows = account_calendar_loss_ratio_rows(
                results,
                group_claims_by_beneficiary(claims),
                as_of=effective_as_of,
                opex_records_by_client=opex_records_by_client,
                default_loading_pct=default_loading_pct,
                premium_basis=premium_basis,
            )
            if policy_year:
                rows = [r for r in rows if str(r["calendar_year"]) == str(policy_year)]
        else:
            rows = account_loss_ratio_rows(
                results,
                as_of=effective_as_of,
                opex_records_by_client=opex_records_by_client,
                default_loading_pct=default_loading_pct,
                premium_basis=premium_basis,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "as_of": effective_as_of.isoformat(),
        "premium_basis": premium_basis,
        "year_basis": year_basis,
        "rows": rows,
        "totals": account_loss_ratio_totals(rows),
    }


@router.get("/nationality-risk")
def portfolio_nationality_risk(
    as_of: Optional[date] = Query(None, description="Date to compute earned exposure as of - defaults to the stored data-as-of date"),
    policy_year: Optional[str] = Query(None, description="Restrict to members whose own policy started in this year"),
    full_credibility_member_years: float = Query(
        FULL_CREDIBILITY_MEMBER_YEARS,
        description="Exposure at which a nationality's own experience is trusted completely; below it the rate blends toward its zone",
    ),
    min_relativity: float = Query(0.5, description="Floor on the resulting rating factor"),
    max_relativity: float = Query(2.0, description="Cap on the resulting rating factor"),
    pricing_credibility: float = Query(
        DEFAULT_PRICING_CREDIBILITY,
        description="Credibility at which a nationality is marked ready to price on - below it the factor is still shown, just flagged as resting on partial data",
    ),
    filters: Dict[str, str] = Depends(_result_filters),
    db: Session = Depends(get_db),
):
    """Per-nationality burning cost from the booked book, credibility-
    weighted toward each nationality's own zone - the evidence behind a
    nationality rating factor (see nationality_risk_table).

    Every row carries the exposure behind it and the credibility that
    exposure earned, plus the population's age and gender mix, so a
    nationality that looks expensive can be checked against whether it is
    simply older or more female before its rate is trusted.

    No rate card is required: this compares each nationality's own claims
    against its own exposure and never touches rate-card pricing.
    """
    results = _run_analysis(
        db, as_of=as_of or _get_stored_as_of(db), policy_year=policy_year,
        filters=filters, require_rate_card=False,
    )
    rows = nationality_risk_table(
        results,
        full_credibility_member_years=full_credibility_member_years,
        min_relativity=min_relativity,
        max_relativity=max_relativity,
        pricing_credibility=pricing_credibility,
    )
    return {
        "rows": rows,
        "full_credibility_member_years": full_credibility_member_years,
        "pricing_credibility": pricing_credibility,
        "nationality_count": len(rows),
        "fully_credible_count": sum(1 for r in rows if r["credibility"] >= 1.0),
        "pricing_ready_count": sum(1 for r in rows if r["pricing_ready"]),
    }


@router.get("/renewal-due-list")
def portfolio_renewal_due_list(
    within_days: int = Query(60, description="How many days out from today counts as 'due soon'"),
    as_of: Optional[date] = Query(None, description="Reference date to measure the window from - defaults to today (real calendar time, not the stored data-as-of date, since a policy's own end date doesn't change with data staleness)"),
    db: Session = Depends(get_db),
):
    """Real accounts due for renewal in the coming `within_days` days,
    driven directly by the Membership export's own policy_end_date per
    master client (see renewal_due_accounts) - distinct from a case's own
    manually-set renewal_date in the case-management workflow.
    """
    members = _member_dicts(db)
    if not members:
        raise HTTPException(status_code=400, detail="No portfolio members uploaded yet")
    subgroup_master_by_name = _subgroup_master_by_name(db)
    due = renewal_due_accounts(members, subgroup_master_by_name, within_days=within_days, as_of=as_of)

    # Which of these accounts already have a renewal case open, so the list
    # can offer "Open" rather than "Start" and never silently create a
    # second case for an account someone is already working on.
    cases_by_client = _portfolio_cases_by_client(db)
    for row in due:
        case = cases_by_client.get(_client_key(row["master_client"]))
        row["case_id"] = case.id if case else None
        row["case_status"] = (case.status.value if hasattr(case.status, "value") else case.status) if case else None
    return due


def _client_key(name: Optional[str]) -> str:
    return (name or "").strip().casefold()


def _subgroup_master_by_name(db: Session) -> Dict[str, str]:
    return {
        normalize_subgroup_key(sm.subgroup_name): sm.master_name
        for sm in db.query(models.SubgroupMasterMapping).all()
    }


def _portfolio_cases_by_client(db: Session) -> Dict[str, models.Case]:
    """Cases already opened off the book, keyed by their master client.
    Oldest first so that if an account somehow ended up with two, the
    original (the one carrying the work) is the one the list points at.
    """
    cases = (
        db.query(models.Case)
        .filter(models.Case.portfolio_master_client.isnot(None))
        .order_by(models.Case.id.asc())
        .all()
    )
    by_client: Dict[str, models.Case] = {}
    for case in cases:
        by_client.setdefault(_client_key(case.portfolio_master_client), case)
    return by_client


@router.get("/renewal-intake/{master_client}")
def preview_renewal_intake(master_client: str, db: Session = Depends(get_db)):
    """What opening this account's renewal would pull through from the
    book - headcount, term dates, per-category existing premium - without
    creating anything. Lets the Renewal Due List show the underwriter
    exactly what they're about to get before they commit to a case.
    """
    members = _member_dicts(db)
    if not members:
        raise HTTPException(status_code=400, detail="No portfolio members uploaded yet")
    profile = renewal_intake_profile(members, master_client, _subgroup_master_by_name(db))
    if not profile["member_count"]:
        raise HTTPException(status_code=404, detail=f"No members found for master client '{master_client}'")
    case = _portfolio_cases_by_client(db).get(_client_key(master_client))
    profile["case_id"] = case.id if case else None
    return profile


@router.post("/renewal-intake")
def open_renewal_intake(payload: schemas.RenewalIntakeRequest, db: Session = Depends(get_db)):
    """Open the renewal case for an account already on HealthCross's own
    book, seeded from the book itself rather than re-keyed by hand.

    Idempotent by master client: an account whose case is already open
    returns that same case rather than opening a second one, so clicking
    through from the Renewal Due List twice is harmless. The census is
    only seeded when the case has none - once an underwriter has uploaded
    the renewal census, re-opening the case must not overwrite it with the
    expiring roster again. `reseed_census` forces the refresh explicitly,
    and takes the same before-snapshot as a census upload does so Census
    Movement still has an expiring state to compare against.
    """
    members = _member_dicts(db)
    if not members:
        raise HTTPException(status_code=400, detail="No portfolio members uploaded yet")

    subgroup_master_by_name = _subgroup_master_by_name(db)
    profile = renewal_intake_profile(members, payload.master_client, subgroup_master_by_name)
    if not profile["member_count"]:
        raise HTTPException(status_code=404, detail=f"No members found for master client '{payload.master_client}'")

    account = account_members(members, payload.master_client, subgroup_master_by_name)
    term_members = current_term_members(account)

    case = _portfolio_cases_by_client(db).get(_client_key(payload.master_client))
    created = case is None
    if created:
        case = models.Case(
            # The book carries no broker or industry of its own - those are
            # case-workflow facts, not membership facts - so they're opened
            # as placeholders for the underwriter to set rather than being
            # guessed at. "unknown" industry scores at the neutral 1.0
            # multiplier (see industry_risk), so an unset industry loads
            # nothing either way rather than quietly penalising the account.
            broker_name=payload.broker_name or "To be confirmed",
            company_name=profile["master_client"],
            industry=payload.industry or "unknown",
            region=profile["region"],
            employee_count_declared=profile["member_count"],
            business_type="existing",
            claims_available=True,
            renewal_date=profile["policy_end_date"],
            policy_start_date=profile["policy_start_date"],
            current_annual_premium=profile["annualised_premium"] or None,
            portfolio_master_client=profile["master_client"],
        )
        db.add(case)
        db.flush()

    seeded = 0
    existing_census = db.query(models.CensusRecord).filter_by(case_id=case.id).count()
    if not existing_census or payload.reseed_census:
        if existing_census:
            # Same snapshot-before-replace contract as upload_census, so a
            # reseed can't destroy the expiring state Census Movement needs.
            relation_counts: Dict[str, int] = defaultdict(int)
            for record in db.query(models.CensusRecord).filter_by(case_id=case.id).all():
                relation_counts[record.relation or "Unspecified"] += 1
            db.query(models.CensusSnapshot).filter_by(case_id=case.id).delete()
            db.add_all(
                models.CensusSnapshot(case_id=case.id, relation=relation, member_count=count)
                for relation, count in relation_counts.items()
            )
            db.query(models.CensusRecord).filter_by(case_id=case.id).delete()
        rows = census_rows_from_members(term_members)
        db.add_all(models.CensusRecord(case_id=case.id, **row) for row in rows)
        seeded = len(rows)

    # The book's claims export already holds this account's own experience,
    # line by line - so the claims ledger the Renewal Bench prices off is
    # seeded from it too, rather than asking for a re-upload of claims
    # HealthCross itself produced. Only the renewing term's lines are
    # copied, under the same period rule the Loss Ratio board uses, so the
    # case and the board can't disagree about the same account.
    claims_seeded = 0
    existing_ledger = db.query(models.ClaimsLedgerEntry).filter_by(case_id=case.id).count()
    if not existing_ledger or payload.reseed_census:
        windows = term_member_windows(term_members)
        if windows:
            if existing_ledger:
                db.query(models.ClaimsLedgerEntry).filter_by(case_id=case.id).delete()
            # Chunked rather than one big IN (...): a large master client can
            # carry more beneficiary IDs than SQLite will accept as bound
            # parameters in a single statement.
            beneficiary_ids = list(windows)
            candidates = []
            for start in range(0, len(beneficiary_ids), 500):
                candidates.extend(
                    db.query(models.PortfolioClaimEntry)
                    .filter(models.PortfolioClaimEntry.patient_id.in_(beneficiary_ids[start:start + 500]))
                    .all()
                )
            entries = [
                models.ClaimsLedgerEntry(
                    case_id=case.id,
                    patient_id=c.patient_id,
                    claim_id=c.claim_id,
                    claim_status=c.claim_status,
                    policy_start_date=c.policy_start_date,
                    policy_end_date=c.policy_end_date,
                    member_start_date=c.member_start_date,
                    member_end_date=c.member_end_date,
                    date_of_treatment=c.date_of_treatment,
                    relation=c.relation,
                    ip_op_maternity=c.ip_op_maternity,
                    medical_category=c.medical_category,
                    medical_act=c.medical_act,
                    provider_name=c.provider_name,
                    diagnosis_code=c.diagnosis_code,
                    diagnosis_description=c.diagnosis_description,
                    claimed_amount=c.claimed_amount,
                    final_amount=c.final_amount,
                )
                for c in candidates
                if claim_belongs_to_term(c.patient_id, c.date_of_treatment, windows)
            ]
            db.add_all(entries)
            claims_seeded = len(entries)

    db.commit()
    db.refresh(case)
    return {
        "case": schemas.CaseOut.model_validate(case),
        "created": created,
        "census_seeded": seeded,
        "claims_seeded": claims_seeded,
        "profile": profile,
    }


@router.get("/group-size-bands")
def portfolio_group_size_bands(
    as_of: Optional[date] = Query(None, description="Date to compute earned premium as of - defaults to the stored data-as-of date, or today if none is set"),
    policy_year: Optional[str] = Query(None, description="Restrict to members whose own policy started in this year"),
    client: Optional[str] = Query(None, description="Restrict to one client for a client-level drill-down"),
    filters: Dict[str, str] = Depends(_result_filters),
    db: Session = Depends(get_db),
):
    """Pools loss ratio by group-size credibility band (1-10/11-50/51-100/
    100+ lives) - the same "small groups lean on portfolio experience,
    large groups' own claims carry real credibility" logic underwriting
    already applies informally, made explicit. Also serves as the Group
    Size Distribution view (group_count/member_count/average_group_size
    per band) - see summarize_by_group_size_band for the full rationale.
    """
    results = _run_analysis(db, as_of=as_of or _get_stored_as_of(db), policy_year=policy_year, client=client, filters=filters)
    return {"rows": summarize_by_group_size_band(results)}


@router.get("/new-vs-renewal")
def portfolio_new_vs_renewal(
    as_of: Optional[date] = Query(None, description="Date to compute earned premium as of - defaults to the stored data-as-of date, or today if none is set"),
    filters: Dict[str, str] = Depends(_result_filters),
    db: Session = Depends(get_db),
):
    """New Business vs. Renewal split, classified per master client by how
    many distinct policy years appear for it in this book-wide extract -
    see summarize_new_vs_renewal for the full definition and its
    limitation. Deliberately has no policy_year filter of its own -
    restricting to one policy year first would leave every client with
    only one year visible, making everything look like New Business
    regardless of its real history.
    """
    results = _run_analysis(db, as_of=as_of or _get_stored_as_of(db), filters=filters)
    return {"rows": summarize_new_vs_renewal(results)}


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


@router.get("/demographic-summary")
def portfolio_demographic_summary(
    as_of: Optional[date] = Query(None, description="Date to compute earned premium as of - defaults to the stored data-as-of date, or today if none is set"),
    policy_year: Optional[str] = Query(None, description="Restrict to members whose own policy started in this year"),
    client: Optional[str] = Query(None, description="Restrict to one client for a client-level drill-down"),
    filters: Dict[str, str] = Depends(_result_filters),
    db: Session = Depends(get_db),
):
    """Full population profile of the booked book (in whatever scope the
    filters/policy_year/client above narrow it to) - age bands, gender,
    marital status, relation, nationality zone mix with top nationalities,
    plus Product and Network member counts. The book-wide analogue of a
    single case's own Census tab (see
    app/scoring/rules/portfolio_analysis.py's demographic_summary, which
    reuses census_demographic_summary directly). Raises the same 400s as
    /summary when there's no book/rate card to analyze yet.
    """
    results = _run_analysis(db, as_of=as_of or _get_stored_as_of(db), policy_year=policy_year, client=client, filters=filters)
    return demographic_summary(results)


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


def _master_client_by_beneficiary(db: Session) -> Dict[str, str]:
    """beneficiary_id -> resolved master client name, for attributing a
    raw claim line (which only carries a patient_id, not a master client)
    to the same master client every other Portfolio Analysis view uses -
    see resolve_master_client. Shared by every claims-only view
    (Large Claims, Utilization of Benefits) that needs to roll up or
    filter by master client without a full membership/rate-card join.
    """
    subgroup_master_by_name: Dict[str, str] = {
        normalize_subgroup_key(sm.subgroup_name): sm.master_name for sm in db.query(models.SubgroupMasterMapping).all()
    }
    return {
        m.beneficiary_id: resolve_master_client(
            {"contract": m.contract, "master_contract": m.master_contract, "master_client_name": m.master_client_name},
            subgroup_master_by_name,
        )
        for m in db.query(
            models.PortfolioMember.beneficiary_id, models.PortfolioMember.contract,
            models.PortfolioMember.master_contract, models.PortfolioMember.master_client_name,
        ).all()
        if m.beneficiary_id
    }


def _claim_dicts_for_large_claims(db: Session) -> List[dict]:
    """Every uploaded claim line's own group_name/client_name/provider_name
    are already denormalized onto PortfolioClaimEntry itself (a book-wide
    export carries its own group identity per row - see
    app/ingestion/portfolio_claims.py), but that denormalized client_name
    is the raw SUBGROUP name on the claims export, not the master policy -
    the same subgroup fragmentation _run_analysis resolves away via
    resolve_master_client for every other view. `client_name` here is
    overridden with the resolved master client name (falling back to the
    claim's own raw client_name only for a patient_id with no matching
    PortfolioMember row), so large-claims/high-cost-member analysis rolls
    up by master client just like everything else, instead of splintering
    one group across its own subgroups.
    """
    master_client_by_beneficiary = _master_client_by_beneficiary(db)

    rows = db.query(
        models.PortfolioClaimEntry.patient_id,
        models.PortfolioClaimEntry.group_name,
        models.PortfolioClaimEntry.client_name,
        models.PortfolioClaimEntry.provider_name,
        models.PortfolioClaimEntry.diagnosis_description,
        models.PortfolioClaimEntry.date_of_treatment,
        models.PortfolioClaimEntry.final_amount,
    ).all()
    return [
        {
            "patient_id": patient_id,
            "group_name": group_name,
            "client_name": master_client_by_beneficiary.get(patient_id) or client_name,
            "provider_name": provider_name,
            "diagnosis_description": diagnosis_description,
            "date_of_treatment": date_of_treatment,
            "final_amount": final_amount,
        }
        for patient_id, group_name, client_name, provider_name, diagnosis_description, date_of_treatment, final_amount in rows
    ]


def _claim_dicts_for_utilization(db: Session) -> List[dict]:
    """Every uploaded claim line's own ip_op_maternity/medical_category/
    medical_act/final_amount, plus its resolved master client (see
    _master_client_by_beneficiary) so this can be scoped to one client for
    a client-level report - a Utilization of Benefits view, like Large
    Claims, is purely about the claim lines themselves and needs no
    member/rate-card join otherwise (see _claim_dicts_for_large_claims's
    own docstring).
    """
    master_client_by_beneficiary = _master_client_by_beneficiary(db)

    rows = db.query(
        models.PortfolioClaimEntry.patient_id,
        models.PortfolioClaimEntry.client_name,
        models.PortfolioClaimEntry.ip_op_maternity,
        models.PortfolioClaimEntry.medical_category,
        # Needed to split PARAMEDICAL into physiotherapy vs alternative
        # treatment - the category alone cannot tell them apart.
        models.PortfolioClaimEntry.medical_act,
        models.PortfolioClaimEntry.final_amount,
    ).all()
    return [
        {
            "master_client": master_client_by_beneficiary.get(patient_id) or client_name,
            "ip_op_maternity": ip_op_maternity,
            "medical_category": medical_category,
            "medical_act": medical_act,
            "final_amount": final_amount,
        }
        for patient_id, client_name, ip_op_maternity, medical_category, medical_act, final_amount in rows
    ]


@router.get("/utilization")
def portfolio_utilization(
    master_client: Optional[str] = Query(
        None, description="Restrict to one master client's own claims (for a client-level report) - matches the resolved master client name, same as /large-claims"
    ),
    db: Session = Depends(get_db),
):
    """Utilization of Benefits - which encounter types (Outpatient/
    Inpatient/Maternity) and which benefit categories (Pharmacy/Dental/
    Optical/Mental Health/Physiotherapy/...) are actually driving cost,
    to spot benefit leakage and major cost drivers. See
    utilization_by_encounter_type/utilization_by_benefit_category for the
    category mapping and what's deliberately left out (Chronic
    conditions/Alternative treatments/High-cost specialty treatments have
    no matching field in HealthCross's own export).

    Whole-book by default, independent of any rate card or membership
    upload - purely a claims analysis, same as /large-claims - pass
    master_client to scope it to one client's own claims instead.
    """
    claims = _claim_dicts_for_utilization(db)
    if master_client:
        claims = [c for c in claims if c.get("master_client") == master_client]
    if not claims:
        raise HTTPException(status_code=400, detail="No claims uploaded yet")
    return {
        "by_encounter_type": utilization_by_encounter_type(claims),
        "by_benefit_category": utilization_by_benefit_category(claims),
    }


@router.get("/large-claims")
def large_claims(
    top_n_claims: int = Query(10, description="How many of the single largest individual claim lines to return"),
    top_n_members: int = Query(20, description="How many members to return, ranked by their own cumulative claims total"),
    recurring_claim_threshold: float = Query(
        DEFAULT_LARGE_CLAIM_THRESHOLDS[0],
        description="A claim line counts toward 'recurring high-cost members' once it's at or above this AED amount",
    ),
    recurring_min_claim_count: int = Query(
        3, description="A member must have at least this many claim lines at or above recurring_claim_threshold to count as 'recurring'"
    ),
    master_client: Optional[str] = Query(
        None, description="Restrict to one master client's own claims (for a client-level report) - matches the resolved master client name, same as elsewhere"
    ),
    db: Session = Depends(get_db),
):
    """Large-loss analysis over the uploaded claims book - the usual
    actuarial cut before drilling into loss ratio by segment, since one or
    two catastrophic cases can distort a small or medium-sized group's
    numbers on their own. Four views:

    - top_claims: the single largest individual claim LINES by value.
    - top_members: members ranked by their own cumulative claims total
      across every line (distinct from top_claims - a member can rank
      here through many moderate claims without any one line of theirs
      being large enough to appear there).
    - threshold_buckets: count and total value of claim lines at or above
      each of AED 50K/100K/250K (or whatever custom set is requested,
      via repeated ?threshold= query params - see below).
    - recurring_high_cost_members: members with several SEPARATE large
      claim lines - an ongoing pattern, distinct from one single
      catastrophic claim (see recurring_high_cost_members's own
      docstring for why these are worth telling apart).

    Whole-book by default (independent of any rate card or membership
    upload - purely a claims analysis, works even before those are set
    up) - pass master_client to scope every view above to one master
    client's own claims instead, for a client-level report.
    """
    claims = _claim_dicts_for_large_claims(db)
    if master_client:
        claims = [c for c in claims if c.get("client_name") == master_client]
    if not claims:
        raise HTTPException(status_code=400, detail="No claims uploaded yet")

    return {
        "top_claims": top_claims_by_value(claims, top_n=top_n_claims),
        "top_members": top_members_by_total_claims(claims, top_n=top_n_members),
        "threshold_buckets": claims_above_thresholds(claims),
        "recurring_high_cost_members": recurring_high_cost_members(
            claims, claim_threshold=recurring_claim_threshold, min_claim_count=recurring_min_claim_count
        ),
    }
