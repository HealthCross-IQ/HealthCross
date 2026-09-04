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
    LOSS_RATIO_GROUP_BY,
    YEAR_BASES,
    account_calendar_loss_ratio_rows,
    account_loss_ratio_rows,
    nationality_risk_table,
    account_loss_ratio_totals,
    loss_ratio_shed_cumulative,
    loss_ratio_shed_impact,
    DEFAULT_EXPENSE_RATIO_PCT,
    DEFAULT_LARGE_CLAIM_THRESHOLDS,
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
    maternity_cost_by_case,
    provider_cost_comparison,
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
from app.book import repository as book_repo
from app.book import analysis as book_analysis

router = APIRouter(prefix="/portfolio-analysis", tags=["portfolio-analysis"])












@router.get("/data-as-of", response_model=schemas.PortfolioDataAsOfOut)
def get_data_as_of(db: Session = Depends(get_db)):
    return schemas.PortfolioDataAsOfOut(data_as_of_date=book_repo.stored_as_of(db))


@router.post("/data-as-of", response_model=schemas.PortfolioDataAsOfOut)
def set_data_as_of(payload: schemas.PortfolioDataAsOfIn, db: Session = Depends(get_db)):
    """Sets the book's own data-as-of (production/extract) date - earned
    premium proration measures elapsed policy time against this date by
    default (see app/scoring/rules/portfolio_analysis.py), rather than
    whatever calendar day the analysis happens to be run on, which can be
    weeks after the data was actually pulled.
    """
    book_repo.set_stored_as_of(db, payload.data_as_of_date)
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
        book_repo.set_stored_as_of(db, data_as_of)
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
        book_repo.set_stored_as_of(db, data_as_of)
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

    # Wholesale-replace, but only the rows the sheet owns. A record an
    # underwriter corrected by hand in the portal survives an upload -
    # discarding it would mean a figure they had just fixed silently
    # reverting to whatever the sheet still says, which is the worst
    # possible failure for a number that drives combined ratio.
    db.query(models.ClientMasterInfo).filter(
        (models.ClientMasterInfo.manually_edited.is_(None))
        | (models.ClientMasterInfo.manually_edited == False)  # noqa: E712 - SQL, not Python
    ).delete(synchronize_session=False)
    db.bulk_insert_mappings(models.ClientMasterInfo, rows)
    db.commit()
    return schemas.PortfolioUploadOut(rows_ingested=len(rows))
















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
    age_band: Optional[str] = Query(
        None,
        description="Restrict to one age band (e.g. '26-35') - the same bands the loaded rate card actually prices by, see age_bands_from_rate_cards.",
    ),
    enrollment_type: Optional[str] = Query(
        None,
        description="Restrict to 'Initial' (enrolled the day the policy incepted) or 'Addition' (joined later, via endorsement).",
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
        "age_band": age_band,
        "enrollment_type": enrollment_type,
    }


@router.get("/summary", response_model=schemas.PortfolioSummaryOut)
def portfolio_summary(
    group_by: str = Query(
        "product",
        description=(
            "One of: product, network, region, nationality_zone, client (subgroup), master_client "
            "(master policy - combines all its subgroups into one row), gender, relation, policy_year, "
            "age_band (the loaded rate card's own age bands - e.g. drill into one Product with "
            "filters.product=Bronze, then group_by=age_band, or stack filters.age_band with "
            "group_by=gender for a fixed age slice)"
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
    products: List[str] = Query(
        [],
        description="Restrict to members on these Products (Platinum/Gold/Silver/Bronze/Group). Omit for every product.",
    ),
    filters: Dict[str, str] = Depends(_result_filters),
    db: Session = Depends(get_db),
):
    results = book_analysis.run_analysis(
        db, as_of=as_of or book_repo.stored_as_of(db), policy_year=policy_year, client=client,
        filters=filters, products=products or None,
    )
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
    results = book_analysis.run_analysis(db, as_of=as_of or book_repo.stored_as_of(db), policy_year=policy_year, client=client, filters=filters)
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
    status: Optional[str] = Query(
        None,
        description="'active' or 'expired' to see only one, omitted for both. Reads each row's own `expired` flag - the same one the row already reports, not a second computation of it.",
    ),
    group_by: str = Query(
        "master_client",
        description="'master_client' (default - combines a client's own subgroups into one row per policy period, the pricing and underwriting basis) or 'client' (breaks the book out by raw subgroup instead).",
    ),
    products: List[str] = Query(
        [],
        description="Restrict to members on these Products (each member's own PRODUCTNAME - Platinum/Gold/Silver/Bronze/Group). Omit for every product. Group is HealthCross's non-SME product line, not one of the four SME tiers - pass products=Platinum&products=Gold&products=Silver&products=Bronze for an SME-only read.",
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

    `status` filters the finished rows rather than the members going in -
    an expired policy's own figures (no more IBNR, a full year earned) are
    already computed correctly regardless of this filter; it only decides
    which of those already-correct rows are shown. Applied after
    `shed_impact`/`shed_cumulative` are computed on the SAME filtered set,
    so "what would the book look like without these accounts" only
    considers accounts actually on screen.
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
    results = book_analysis.run_analysis(
        db, as_of=as_of or book_repo.stored_as_of(db),
        policy_year=None if calendar_basis else policy_year,
        client=client, filters=filters, require_rate_card=False,
    )
    if products:
        results = [r for r in results if r.get("product") in products]
    opex_records_by_client: Dict[str, List[dict]] = defaultdict(list)
    for cm in db.query(models.ClientMasterInfo).all():
        if cm.opex_pct is not None:
            opex_records_by_client[cm.master_client_name].append(
                {"start_date": cm.start_date, "end_date": cm.end_date, "opex_pct": cm.opex_pct}
            )
    effective_as_of = as_of or book_repo.stored_as_of(db) or date.today()
    if year_basis not in YEAR_BASES:
        raise HTTPException(status_code=400, detail=f"year_basis must be one of {YEAR_BASES}")
    if group_by not in LOSS_RATIO_GROUP_BY:
        raise HTTPException(status_code=400, detail=f"group_by must be one of {LOSS_RATIO_GROUP_BY}")
    if group_by != "master_client" and calendar_basis:
        # account_calendar_loss_ratio_rows buckets its own way (by
        # calendar year, splitting a policy across the years it spans)
        # and does not yet take a group_by of its own - refused rather
        # than silently ignored, which would show master-client rows
        # under a "by subgroup" heading.
        raise HTTPException(
            status_code=400,
            detail="group_by='client' is not yet supported on the calendar year basis.",
        )
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
            # Same helper the Renewal Bench scorecard calls, so the two
            # screens cannot report different loss ratios for one account.
            rows = book_analysis.account_loss_ratio_rows_for_book(
                db, client=client, as_of=as_of,
                premium_basis=premium_basis,
                default_loading_pct=default_loading_pct,
                group_by=group_by,
                products=products or None,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if status:
        if status not in ("active", "expired"):
            raise HTTPException(status_code=400, detail="status must be 'active' or 'expired'")
        want_expired = status == "expired"
        rows = [r for r in rows if bool(r.get("expired")) == want_expired]

    return {
        "as_of": effective_as_of.isoformat(),
        "premium_basis": premium_basis,
        "year_basis": year_basis,
        "status": status,
        "group_by": group_by,
        "products": products,
        "rows": rows,
        "totals": account_loss_ratio_totals(rows),
        # What the book's loss ratio becomes if each account is not
        # renewed, and what shedding them one after another actually
        # achieves - see loss_ratio_shed_impact. Returned alongside the
        # rows rather than as a separate call so the "which accounts are
        # worth walking away from?" view is always computed on exactly
        # the same filtered, basis-matched rows shown in the table.
        "shed_impact": loss_ratio_shed_impact(rows, top_n=15),
        "shed_cumulative": loss_ratio_shed_cumulative(rows, max_accounts=10),
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
    products: List[str] = Query(
        [],
        description="Restrict to members on these Products (Platinum/Gold/Silver/Bronze/Group). Omit for every product.",
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
    results = book_analysis.run_analysis(
        db, as_of=as_of or book_repo.stored_as_of(db), policy_year=policy_year,
        filters=filters, require_rate_card=False, products=products or None,
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
    members = book_repo.members(db)
    if not members:
        raise HTTPException(status_code=400, detail="No portfolio members uploaded yet")
    subgroup_master_by_name = book_repo.subgroup_master_by_name(db)
    due = renewal_due_accounts(members, subgroup_master_by_name, within_days=within_days, as_of=as_of)

    # Which of these accounts already have a renewal case open, so the list
    # can offer "Open" rather than "Start" and never silently create a
    # second case for an account someone is already working on.
    cases_by_client = _portfolio_cases_by_client(db)
    for row in due:
        case = cases_by_client.get(_client_key(row["master_client"]))
        row["case_id"] = case.id if case else None
        row["case_status"] = (case.status.value if hasattr(case.status, "value") else case.status) if case else None

    _attach_renewal_risk(db, due, cases_by_client)
    # Worst first, then soonest. A list ordered by date alone says an
    # account renewing in twelve days at 62% needs attention before one
    # renewing in forty-seven at 248%, and the second is the one that
    # needs a conversation started now.
    due.sort(key=lambda r: (r.get("severity_rank", 99), r.get("days_until_renewal", 0)))
    return due


#: Worst first. Matches underwriting_alerts.SEVERITY_ORDER, with two extra
#: states the due list needs and a single account's alert panel does not:
#: an account that cannot be priced at all, and one with nothing to say.
_DUE_SEVERITY_RANK = {"critical": 0, "high": 1, "blocked": 2, "watch": 3, "clear": 4, "unknown": 5}


def _attach_renewal_risk(db: Session, due: List[dict], cases_by_client: Dict[str, models.Case]) -> None:
    """Add each due account's loss ratio and its worst reading, in place.

    The due list used to carry name, headcount, policy end date and days
    to go - nothing about whether the account was a problem. An account
    running at 248% looked exactly like one at 60%, so the only way to
    find the three that would hurt was to open all twenty.

    Everything here is read, not computed: the loss ratio row the Loss
    Ratio screen renders, the alerts the account dashboard renders, and
    the loading block app.api.case_loading imposes. run_analysis is
    cached on the uploaded data, so the whole book costs one pass however
    many accounts are due.
    """
    from app.api.case_loading import is_renewal, renewal_loading
    from app.scoring.rules.account_overview import median
    from app.scoring.rules.underwriting_alerts import alert_counts, underwriting_alerts

    rows = book_analysis.account_loss_ratio_rows_for_book(db)
    if not rows:
        for row in due:
            row.update(_no_risk_reading())
        return

    top_claimants = book_analysis.top_claimant_for_book(db)
    latest_by_client: Dict[str, dict] = {}
    for r in rows:
        key = (r.get("master_client") or "").strip().casefold()
        if key and (key not in latest_by_client
                    or r["policy_start_date"] > latest_by_client[key]["policy_start_date"]):
            latest_by_client[key] = r

    shares = [
        r["outstanding"] / r["incurred_claims"]
        for r in rows if r.get("incurred_claims") and r.get("outstanding") is not None
    ]
    book_median_outstanding_share = median(shares)

    for row in due:
        lr_row = latest_by_client.get((row["master_client"] or "").strip().casefold())
        if lr_row is None:
            row.update(_no_risk_reading())
            continue

        top = top_claimants.get((lr_row["master_client"], date.fromisoformat(lr_row["policy_start_date"])))
        incurred = lr_row.get("incurred_claims") or 0.0
        top_share = (top["incurred"] / incurred) if top and incurred else None

        case = cases_by_client.get(_client_key(row["master_client"]))
        loading_problems = []
        if case is not None and is_renewal(case):
            _, loading_problems = renewal_loading(case)

        alerts = underwriting_alerts(
            lr_row,
            top_claimant_share=top_share,
            top_claimant_amount=top["incurred"] if top else None,
            book_median_outstanding_share=book_median_outstanding_share,
            loading_problems=loading_problems,
        )
        counts = alert_counts(alerts)
        blocked = bool(loading_problems)

        row.update({
            "loss_ratio": lr_row.get("gross_loss_ratio"),
            "loading_is_default": lr_row.get("loading_is_default"),
            "incurred_claims": lr_row.get("incurred_claims"),
            "earned_premium": lr_row.get("earned_premium"),
            "days_elapsed": lr_row.get("days"),
            "alert_counts": counts,
            "alert_count": len(alerts),
            "blocked": blocked,
            # The one line the row shows. The worst alert is the reason
            # this account is where it is in the list, so it is the one
            # worth the space.
            "top_alert": ({"title": alerts[0]["title"], "severity": alerts[0]["severity"],
                           "message": alerts[0]["message"], "action": alerts[0]["action"]}
                          if alerts else None),
            "severity": _due_severity(alerts, blocked),
        })
        row["severity_rank"] = _DUE_SEVERITY_RANK[row["severity"]]


def _due_severity(alerts: List[dict], blocked: bool) -> str:
    """An account nobody can price outranks a merely noisy one, unless
    something critical is already true of it: "we cannot quote this" and
    "this should not be quoted" are both worth interrupting for, and the
    second is the one that changes the answer."""
    if alerts and alerts[0]["severity"] == "critical" and not blocked:
        return "critical"
    if blocked:
        return "blocked"
    if not alerts:
        return "clear"
    return alerts[0]["severity"]


def _no_risk_reading() -> dict:
    """An account with no premium or claims on the book yet. Reported as
    unknown rather than clear - nothing has been read, which is not the
    same as nothing being wrong."""
    return {
        "loss_ratio": None, "loading_is_default": None, "incurred_claims": None,
        "earned_premium": None, "days_elapsed": None,
        "alert_counts": {"critical": 0, "high": 0, "watch": 0}, "alert_count": 0,
        "blocked": False, "top_alert": None,
        "severity": "unknown", "severity_rank": _DUE_SEVERITY_RANK["unknown"],
    }



def _client_key(name: Optional[str]) -> str:
    return (name or "").strip().casefold()




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


@router.get("/loss-ratio-tips")
def get_loss_ratio_tips(
    as_of: Optional[date] = Query(None),
    policy_year: Optional[str] = Query(None),
    large_claim_threshold: float = Query(100_000.0, description="Individual spend above which a member counts as a large claim for the reinsurance tip"),
    case_management_reduction: Optional[float] = Query(None, description="Assumed recovery from case-managing the heaviest claimants, as a fraction"),
    chronic_programme_reduction: Optional[float] = Query(None, description="Assumed recovery from a chronic disease management programme"),
    pharmacy_generic_reduction: Optional[float] = Query(None, description="Assumed recovery from generic substitution and formulary control"),
    provider_steering_reduction: Optional[float] = Query(None, description="Assumed recovery from steering volume to efficient providers"),
    db: Session = Depends(get_db),
):
    """Ranked, quantified findings from this book's own claims - where the
    loss ratio is actually losing money and what to do about each.

    Every opportunity is a stated assumption applied to a measured base,
    and the assumption is returned with the number and is overridable
    here: an underwriter who thinks generic substitution recovers 20%
    rather than 12% should be able to say so and see the ranking change,
    rather than having to trust a figure baked into the code.
    """
    from app.scoring.rules.loss_ratio_tips import loss_ratio_tips

    claims = [
        {
            "patient_id": c.patient_id,
            "claim_status": c.claim_status,
            "final_amount": c.final_amount,
            "medical_category": c.medical_category,
            "ip_op_maternity": c.ip_op_maternity,
            "provider_name": c.provider_name,
            "diagnosis_code": c.diagnosis_code,
            "date_of_treatment": c.date_of_treatment,
        }
        for c in db.query(models.PortfolioClaimEntry).all()
    ]
    if not claims:
        raise HTTPException(status_code=400, detail="No portfolio claims uploaded yet")

    if policy_year:
        claims = [c for c in claims if c["date_of_treatment"] and str(c["date_of_treatment"].year) == policy_year]

    # Account rows drive the re-pricing tip only, and need premium - so a
    # book with claims but no membership still gets every other tip
    # rather than failing outright.
    account_rows: List[dict] = []
    try:
        results = book_analysis.run_analysis(db, as_of=as_of or book_repo.stored_as_of(db), require_rate_card=False)
        opex_by_client: Dict[str, List[dict]] = defaultdict(list)
        for cm in db.query(models.ClientMasterInfo).all():
            if cm.opex_pct is not None:
                opex_by_client[cm.master_client_name].append(
                    {"start_date": cm.start_date, "end_date": cm.end_date, "opex_pct": cm.opex_pct}
                )
        account_rows = account_loss_ratio_rows(
            results,
            as_of=as_of or book_repo.stored_as_of(db) or date.today(),
            opex_records_by_client=opex_by_client,
        )
    except HTTPException:
        pass

    overrides = {
        k: v for k, v in {
            "case_management_reduction": case_management_reduction,
            "chronic_programme_reduction": chronic_programme_reduction,
            "pharmacy_generic_reduction": pharmacy_generic_reduction,
            "provider_steering_reduction": provider_steering_reduction,
        }.items() if v is not None
    }
    return loss_ratio_tips(
        claims,
        account_rows=account_rows,
        assumptions=overrides,
        large_claim_threshold=large_claim_threshold,
    )


@router.get("/burning-cost-cube")
def get_burning_cost_cube(
    as_of: Optional[date] = Query(None),
    policy_year: Optional[str] = Query(None),
    level: Optional[int] = Query(None, description="Only return cells at this depth - 1 is Product alone, 6 the full demographic cell. Omit for every level."),
    min_member_years: float = Query(0.0, description="Hide cells thinner than this much exposure - they are still used as fallbacks, just not listed."),
    db: Session = Depends(get_db),
):
    """The book's own experience as a credibility-blended hierarchy - what
    a member in each demographic cell actually costs, and what they should
    therefore be priced at (see app/scoring/rules/burning_cost_cube.py).

    `own_rate` is what a cell literally cost; `expected_cost` is that rate
    after blending toward its parent. They diverge exactly where the cell
    is thin, which is the point - a three-life cell's raw rate is not a
    price.
    """
    # Age bands come from the rate card where one exists, so a cube cell
    # lines up with the card row that prices it - but the book's own
    # experience is worth showing before pricing is set up, so a missing
    # card falls back to conventional bands rather than 400-ing.
    results, cube = book_analysis.analysis_with_cube(
        db, as_of=as_of or book_repo.stored_as_of(db), policy_year=policy_year, require_rate_card=False
    )
    cells = cube["cells"]
    if level is not None:
        cells = [c for c in cells if c["level"] == level]
    if min_member_years:
        cells = [c for c in cells if c["earned_member_years"] >= min_member_years]
    return {**cube, "cells": cells, "returned_cell_count": len(cells)}


@router.get("/renewal-intake/{master_client}")
def preview_renewal_intake(master_client: str, db: Session = Depends(get_db)):
    """What opening this account's renewal would pull through from the
    book - headcount, term dates, per-category existing premium - without
    creating anything. Lets the Renewal Due List show the underwriter
    exactly what they're about to get before they commit to a case.
    """
    members = book_repo.members(db)
    if not members:
        raise HTTPException(status_code=400, detail="No portfolio members uploaded yet")
    profile = renewal_intake_profile(members, master_client, book_repo.subgroup_master_by_name(db))
    if not profile["member_count"]:
        raise HTTPException(status_code=404, detail=f"No members found for master client '{master_client}'")
    case = _portfolio_cases_by_client(db).get(_client_key(master_client))
    profile["case_id"] = case.id if case else None
    return profile


@router.get("/renewal-claims-split/{master_client}")
def renewal_claims_split(
    master_client: str,
    as_at: Optional[date] = Query(
        None,
        description=(
            "Who counts as continuing, measured at this date. Defaults to the expiring "
            "term's own end date - the question a renewal turns on is not who is covered "
            "today, it is who walks into the new policy year."
        ),
    ),
    as_of: Optional[date] = Query(
        None,
        description=(
            "The day claims are measured to and premium earned to. Defaults to the book's "
            "recorded extract date, or the last day the claims data covers."
        ),
    ),
    db: Session = Depends(get_db),
):
    """The expiring year's claims, split between the members who are
    renewing and the members who are not.

    A renewal quoted off the account's headline loss ratio prices the
    incoming year against a population that includes people who will not
    be in it. Whether that matters is an empirical question this answers:
    if taking the leavers out moves the ratio a long way, last year's
    headline overstates what the renewing population costs; if it barely
    moves, the base rate is the problem and renewing on headcount carries
    it straight into the new year.
    """
    from app.scoring.rules.renewal_intake import claims_by_member_status

    members = book_repo.members(db)
    if not members:
        raise HTTPException(status_code=400, detail="No portfolio members uploaded yet")
    account = account_members(members, master_client, book_repo.subgroup_master_by_name(db))
    if not account:
        raise HTTPException(status_code=404, detail=f"No members found for master client '{master_client}'")

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
    split = claims_by_member_status(account, group_claims_by_beneficiary(claims),
                                    as_at=as_at, as_of=book_repo.measurement_date(db, as_of))
    return {"master_client": master_client, **split}


@router.get("/population-movement/{master_client}")
def population_movement_for_account(master_client: str, db: Session = Depends(get_db)):
    """How the account's population actually moved over the expiring
    term: opening, joiners, leavers, closing, by relation.

    Derived from the roster's own dates rather than by comparing two
    census uploads. A snapshot comparison answers a different question -
    what changed between two UPLOADS - and reports zero movement on an
    account whose census has not been re-uploaded, however many people
    joined and left. Serviceplan lost thirteen members over its term and
    the panel read 178 to 178, change 0.
    """
    from app.scoring.rules.renewal_intake import population_movement

    members = book_repo.members(db)
    if not members:
        raise HTTPException(status_code=400, detail="No portfolio members uploaded yet")
    account = account_members(members, master_client, book_repo.subgroup_master_by_name(db))
    if not account:
        raise HTTPException(status_code=404, detail=f"No members found for master client '{master_client}'")
    return {"master_client": master_client, **population_movement(account)}


@router.get("/account-overview/{master_client}")
def account_overview(
    master_client: str,
    as_of: Optional[date] = Query(
        None,
        description=(
            "The day claims are measured to and premium earned to. Defaults to the "
            "book's recorded extract date."
        ),
    ),
    top: int = Query(5, description="How many of the largest claimants to return"),
    db: Session = Depends(get_db),
):
    """Everything the account dashboard shows, in one payload.

    Assembled from the figures that already exist rather than computed
    again here. The KPI strip is one row of account_loss_ratio_rows - the
    same row the Loss Ratio screen renders - the encounter split is
    utilization_by_encounter_type, the claimants are member_claim_ranking,
    and the readings are underwriting_alerts. A dashboard is the most
    tempting place in a portal to recompute a number "just for the
    summary", and a summary that disagrees with the detail below it is
    worse than no summary at all.

    Two things are worth knowing about the figures on it.

    The loss ratio is against EARNED premium, not annual. K A F ran 130
    days of a 365-day term: 991,265 of annual premium, 353,053 earned,
    877,626 incurred. Putting the annual premium beside part-year claims
    gives 88.5% - a comfortable-looking account that is actually running
    at 248.6%. Both premium figures are returned, and the one the ratio
    divides by is named.

    The top claimant's share is against the account's INCURRED, so it
    reconciles with the incurred figure on the same screen. Incurred
    includes IBNR, which has no claimant, so the share is marginally
    conservative - a member at 25.9% of incurred is a slightly larger
    share of the claims actually filed.
    """
    from app.api.case_loading import is_renewal, renewal_loading
    from app.scoring.rules.account_overview import median
    from app.scoring.rules.account_overview import (
        book_position,
        claims_by_month,
        data_window,
        loss_ratio_by_period,
        monthly_burning_cost,
    )
    from app.scoring.rules.renewal_repricing import member_claim_ranking
    from app.scoring.rules.underwriting_alerts import alert_counts, underwriting_alerts

    members = book_repo.members(db)
    if not members:
        raise HTTPException(status_code=400, detail="No portfolio members uploaded yet")
    account = account_members(members, master_client, book_repo.subgroup_master_by_name(db))
    if not account:
        raise HTTPException(status_code=404, detail=f"No members found for master client '{master_client}'")

    # The whole book in one call: the account's own row AND the population
    # its percentile is measured against come from the same list, so the
    # comparison can never be drawn against a differently-filtered book.
    book_rows = book_analysis.account_loss_ratio_rows_for_book(db, as_of=as_of)
    target = (master_client or "").strip().casefold()
    account_rows = [
        r for r in book_rows
        if (r.get("master_client") or "").strip().casefold() == target
    ]
    # An account that has already renewed has a row per policy period.
    # The KPI strip is about the one currently running - but the others
    # are not dropped: an account that went from 68% to 241% looked
    # exactly like one that has always run at 241%, and those are
    # different conversations.
    row = max(account_rows, key=lambda r: r["policy_start_date"]) if account_rows else None
    periods = loss_ratio_by_period(account_rows)

    current = current_term_members(account)
    windows = term_member_windows(current)
    # EVERY member row the account has, across every policy year - not
    # just the renewing term's. An insurer commonly issues new
    # beneficiary ids at renewal, so scoping the claims read to the
    # current term's ids alone meant last year's claims were never
    # fetched at all, and the "your export only holds one year" note
    # below could not fire on the accounts that most needed it.
    account_ids = {m.get("beneficiary_id") for m in account if m.get("beneficiary_id")}

    claims = [
        {
            "patient_id": patient_id,
            "date_of_treatment": date_of_treatment,
            "final_amount": final_amount,
            "claim_status": claim_status,
            "ip_op_maternity": ip_op_maternity,
            "medical_category": medical_category,
            "diagnosis_description": diagnosis_description,
            "client_name": client_name,
        }
        for (
            patient_id, date_of_treatment, final_amount, claim_status,
            ip_op_maternity, medical_category, diagnosis_description, client_name,
        ) in db.query(
            models.PortfolioClaimEntry.patient_id,
            models.PortfolioClaimEntry.date_of_treatment,
            models.PortfolioClaimEntry.final_amount,
            models.PortfolioClaimEntry.claim_status,
            models.PortfolioClaimEntry.ip_op_maternity,
            models.PortfolioClaimEntry.medical_category,
            models.PortfolioClaimEntry.diagnosis_description,
            models.PortfolioClaimEntry.client_name,
        ).all()
        # The member join first, and the claim file's own client name as
        # a fallback - an insurer commonly issues new beneficiary ids at
        # renewal, so last year's claims can carry ids that appear on no
        # membership row this export holds. Used ONLY to notice that the
        # earlier year exists; nothing priced is scoped this way.
        if patient_id in account_ids
        or (client_name or "").strip().casefold() == target
    ]
    # Same in-term rule member_claim_ranking uses, so the monthly chart,
    # the encounter split and the claimant list all cover exactly the
    # same claims.
    in_term = [
        c for c in claims
        if c["date_of_treatment"]
        and c["patient_id"] in account_ids
        and claim_belongs_to_term(c["patient_id"], c["date_of_treatment"], windows)
    ]
    by_beneficiary = group_claims_by_beneficiary(in_term)

    # An account that renewed has a row per policy period, and the card
    # below only appears when the book holds more than one. Where it
    # holds ONE but the account's own members have claims dated before
    # that period started, the earlier year exists in the claims file and
    # not in the membership export - which is a fact about the upload,
    # not about the account, and silence is the worst way to report it.
    period_start = (
        date.fromisoformat(row["policy_start_date"]) if row and row.get("policy_start_date") else None
    )
    earlier = [
        c for c in claims
        if c["date_of_treatment"] and period_start and c["date_of_treatment"] < period_start
    ] if len(account_rows) < 2 else []
    earlier_member_count = len({
        c["patient_id"] for c in earlier if c.get("patient_id")
    }) if earlier else 0
    earlier_claims = {
        "claim_count": len(earlier),
        "incurred": round(sum(c["final_amount"] or 0.0 for c in earlier), 2),
        "from": min(c["date_of_treatment"] for c in earlier).isoformat() if earlier else None,
        "to": max(c["date_of_treatment"] for c in earlier).isoformat() if earlier else None,
        "member_count": earlier_member_count,
    } if earlier else None

    claimants = member_claim_ranking(current, by_beneficiary, windows, top=top)
    incurred = (row or {}).get("incurred_claims") or 0.0
    top_claimant_amount = claimants[0]["incurred"] if claimants else None
    top_claimant_share = (
        top_claimant_amount / incurred if claimants and incurred else None
    )

    position = book_position(row, book_rows)

    term_start = current[0].get("policy_start_date") if current else None
    term_end = current[0].get("policy_end_date") if current else None
    window = data_window(in_term)
    burning_cost = (
        monthly_burning_cost(in_term, current, term_start, term_end,
                             up_to=book_repo.measurement_date(db, as_of) or window["to"])
        if (term_start and term_end) else []
    )

    case = _portfolio_cases_by_client(db).get(_client_key(master_client))
    loading_problems = []
    if case is not None and is_renewal(case):
        _, loading_problems = renewal_loading(case)

    alerts = underwriting_alerts(
        row,
        top_claimant_share=top_claimant_share,
        top_claimant_amount=top_claimant_amount,
        book_median_outstanding_share=(position or {}).get("book_median_outstanding_share"),
        loading_problems=loading_problems,
    )

    return {
        "master_client": master_client,
        "case_id": case.id if case else None,
        "as_of": (row or {}).get("as_of"),
        # How many policy years the BOOK holds for this account. On screen
        # so "why can I not see last year" answers itself: one year on
        # the book is a fact about the membership export, and the
        # dashboard should not make anyone guess which.
        "policy_years_on_book": len(account_rows),
        "policy": {
            "start_date": (row or {}).get("policy_start_date"),
            "days_elapsed": (row or {}).get("days"),
            "expired": (row or {}).get("expired"),
            "member_count": (row or {}).get("member_count") or len(current),
        },
        # Named rather than left to the reader: which premium the ratio
        # divides by is the whole difference between 88.5% and 248.6%.
        "loss_ratio_basis": "earned_premium",
        "kpis": row,
        "alerts": alerts,
        "alert_counts": alert_counts(alerts),
        "claims_by_month": claims_by_month(in_term),
        # Claims per member per month over the EXPOSED RISK POPULATION -
        # each member counted for the fraction of the month they were
        # actually covered, so a group that grew from 96 to 140 over its
        # term is not credited with 140 lives in month one.
        "monthly_burning_cost": burning_cost,
        "policy_periods": periods,
        # Only ever set when there is exactly one period on the book and
        # the claims file reaches back past it.
        "earlier_claims": earlier_claims,
        "encounter_split": utilization_by_encounter_type(in_term),
        "top_claimants": claimants,
        "top_claimant_share": round(top_claimant_share, 4) if top_claimant_share else None,
        "book_position": position,
        "claims_window": data_window(in_term),
    }


@router.post("/renewal-census-comparison/{master_client}")
async def compare_renewal_census(
    master_client: str,
    file: UploadFile = File(..., description="The census the broker has sent for the renewal"),
    as_at: Optional[date] = Query(None, description="Cut date deciding who is active on the book"),
    as_of: Optional[date] = Query(None, description="Date the year is measured as elapsed to"),
    include: List[str] = Query(
        default_factory=list,
        description="Further master clients to read as part of this account, for a group booked as several contracts",
    ),
    db: Session = Depends(get_db),
):
    """The book's active roster against the census a broker has sent,
    priced both ways.

    A renewal census usually differs from the book, and the difference
    is the account's shape at renewal rather than a reconciliation
    chore: KIKO arrived with 69 names against 71, five leaving - four of
    them one household carrying 9% of the year's claims - and three
    joining with no history at all.

    Both scenarios come back, never one. On the book's roster the
    account is what it has been; on the broker's list it is what it will
    be, and the two beside each other is the only way to see whether the
    difference is worth anything. Nothing is written to the case - this
    reads the file and answers.

    A group is often booked as several contracts and quoted as one.
    ``include`` widens the roster to those contracts; without it, names
    on the census that sit under a sibling contract are reported as
    exactly that rather than counted as joiners - KIKO's census read
    against its lead entity alone shows 26 joiners, of which 23 are
    already on the book.
    """
    from app.ingestion.census import parse_census
    from app.scoring.rules.renewal_intake import compare_against_supplied_census

    members = book_repo.members(db)
    if not members:
        raise HTTPException(status_code=400, detail="No portfolio members uploaded yet")
    subgroups = book_repo.subgroup_master_by_name(db)
    account = account_members(members, master_client, subgroups)
    for sibling in include:
        account += account_members(members, sibling, subgroups)
    if not account:
        raise HTTPException(status_code=404, detail=f"No members found for master client '{master_client}'")

    try:
        rows = parse_census(file.file, file.filename)
    except Exception as exc:  # noqa: BLE001 - the parser raises many shapes
        raise HTTPException(status_code=400, detail=f"Could not read that census: {exc}")
    if not rows:
        raise HTTPException(status_code=400, detail="No member rows found in that file")

    refs = [r.get("employee_ref") for r in rows if r.get("employee_ref")]
    if not refs:
        raise HTTPException(
            status_code=400,
            detail=(
                "That census carries no member reference column, so it cannot be matched against "
                "the book. The comparison needs one identifier per row - Dependent_Insured_Number, "
                "Employee ID or similar."
            ),
        )

    claims = [
        {"patient_id": pid, "date_of_treatment": dot, "final_amount": amt, "claim_status": st}
        for pid, dot, amt, st in db.query(
            models.PortfolioClaimEntry.patient_id,
            models.PortfolioClaimEntry.date_of_treatment,
            models.PortfolioClaimEntry.final_amount,
            models.PortfolioClaimEntry.claim_status,
        ).all()
    ]
    # Where else in the portfolio a census name might already sit, so a
    # sibling contract's member is not reported as a new life.
    on_this_account = {id(m) for m in account}
    elsewhere = {}
    for member in members:
        if id(member) in on_this_account:
            continue
        ref = str(member.get("beneficiary_id") or "").strip()
        if ref:
            elsewhere.setdefault(ref, resolve_master_client(member, subgroups))

    result = compare_against_supplied_census(
        account, refs, group_claims_by_beneficiary(claims),
        as_of=book_repo.measurement_date(db, as_of), cut_date=as_at, elsewhere_in_book=elsewhere,
    )
    return {
        "master_client": master_client,
        "also_included": include,
        "filename": file.filename,
        "rows_read": len(rows),
        "rows_with_a_reference": len(refs),
        **result,
    }


@router.get("/renewal-repricing/{master_client}")
def renewal_repricing(
    master_client: str,
    exclude: List[str] = Query([], description="Beneficiary IDs to hold out of the price"),
    as_at: Optional[date] = Query(None, description="Cut date deciding who is active"),
    trend_pct: float = Query(None, description="Claims inflation carried onto the expiring year"),
    target_loss_ratio: float = Query(None, description="The loss ratio the price is built to land on"),
    loading_pct: float = Query(None, description="Expense loading as a fraction of premium"),
    top: int = Query(15, description="How many of the largest claimants to list"),
    db: Session = Depends(get_db),
):
    """The renewal price, with any set of members held out.

    An account's renewal is usually decided by a handful of people, and a
    total hides them. This ranks the largest claimants with their monthly
    run - so a finished event and a condition still being treated can be
    told apart - and reprices the account without whichever of them the
    underwriter names.

    The price with everybody in is always returned beside it. A figure
    produced by holding someone out is only a price if that member is not
    renewing or their condition is excluded on the renewal terms, and
    showing it on its own invites it to be quoted as though it were.
    """
    from app.scoring.rules.renewal_intake import (
        continuing_and_leaving,
        current_term_members,
        member_annual_rate,
        roster_term_end,
        term_member_windows,
    )
    from app.scoring.rules.renewal_repricing import (
        DEFAULT_TREND_PCT,
        member_claim_ranking,
        reprice,
    )

    members = book_repo.members(db)
    if not members:
        raise HTTPException(status_code=400, detail="No portfolio members uploaded yet")
    account = account_members(members, master_client, book_repo.subgroup_master_by_name(db))
    if not account:
        raise HTTPException(status_code=404, detail=f"No members found for master client '{master_client}'")

    cut_date, warning = (as_at, None) if as_at else roster_term_end(account)
    active, _ = continuing_and_leaving(account, cut_date)
    windows = term_member_windows(current_term_members(account))

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
    by_beneficiary = group_claims_by_beneficiary(claims)

    # The day the data actually runs to, so a month cut half way through
    # is not averaged in as though it were whole. The extract's recorded
    # production date first where one was given on upload, since an
    # export produced on the 15th has an incomplete month even if its
    # last claim happens to fall on the 14th.
    treated = [c["date_of_treatment"] for c in claims if c.get("date_of_treatment")]
    data_to = book_repo.measurement_date(db) or (max(treated) if treated else None)

    current_premium = sum(member_annual_rate(m) or 0.0 for m in active) or None
    effective_loading, loading_source = _account_loading(db, master_client, loading_pct)
    # The account's OWN loss ratio, off the same row the Loss Ratio screen
    # and the renewal rating use. This panel used to annualise the claims
    # itself, off complete months x 12, while the rating annualises by the
    # exposure actually run - the same book claims came out at 1,048,000
    # one way and 905,373 the other.
    experience = _account_experience(db, master_client)
    priced = reprice(
        active, by_beneficiary, windows,
        current_premium=current_premium,
        loading_pct=effective_loading,
        loss_ratio=(experience or {}).get("loss_ratio"),
        exclude=exclude,
        trend_pct=trend_pct if trend_pct is not None else DEFAULT_TREND_PCT,
        data_to=data_to,
    )
    return {
        "master_client": master_client,
        "as_at": cut_date,
        "warning": warning,
        "data_to": data_to,
        "loading_source": loading_source,
        "top_claimants": member_claim_ranking(active, by_beneficiary, windows, top=top),
        **priced,
    }


def _account_experience(db: Session, master_client: str) -> Optional[dict]:
    """The account's own loss ratio, from the row every other screen
    reads it off - never derived a second time here.

    The latest policy period, because a renewal is about the year that is
    running rather than one that closed.
    """
    rows = book_analysis.account_loss_ratio_rows_for_book(db)
    target = (master_client or "").strip().casefold()
    mine = [r for r in rows if (r.get("master_client") or "").strip().casefold() == target]
    if not mine:
        return None
    row = max(mine, key=lambda r: r["policy_start_date"])
    return {
        "loss_ratio": row.get("gross_loss_ratio"),
        "incurred_claims": row.get("incurred_claims"),
        "earned_premium": row.get("earned_premium"),
        "days": row.get("days"),
        "as_of": row.get("as_of"),
    }


def _account_loading(db: Session, master_client: str,
                     override: Optional[float] = None) -> tuple:
    """This account's real loading, and where it came from.

    Never the house average. It used to default to DEFAULT_EXPENSE_RATIO_PCT
    silently, so an account whose fee split was entered at 21.5% was
    repriced at 33% and the panel's own subtitle said so in words nobody
    reads - "priced at a 33.0% loading" beside a case record holding a
    different number.

    Three sources, in order, and None when none of them answer:

      an explicit override, which is the what-if query param;
      the renewal case's own fee split, where a case is open;
      the Client Master sheet's OPEX for this account, which is the same
      figure the Loss Ratio screen divides by.
    """
    from app.api.case_loading import is_renewal, renewal_loading
    from app.scoring.rules.portfolio_analysis import client_opex_pct_on_file

    if override is not None:
        return override, "the loading passed on the request"

    case = _portfolio_cases_by_client(db).get(_client_key(master_client))
    if case is not None and is_renewal(case):
        loading, problems = renewal_loading(case)
        if loading is not None:
            return loading, "this account's own fee split on the case"

    records = book_repo.opex_records_by_client(db)
    rows = book_analysis.account_loss_ratio_rows_for_book(db, client=None)
    policy_start = None
    for row in rows:
        if (row.get("master_client") or "").strip().casefold() == (master_client or "").strip().casefold():
            candidate = date.fromisoformat(row["policy_start_date"])
            if policy_start is None or candidate > policy_start:
                policy_start = candidate
    on_file = client_opex_pct_on_file(master_client, policy_start, records)
    if on_file is not None:
        return on_file, "the Client Master sheet's OPEX for this account"

    return None, None


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
    members = book_repo.members(db)
    if not members:
        raise HTTPException(status_code=400, detail="No portfolio members uploaded yet")

    subgroup_master_by_name = book_repo.subgroup_master_by_name(db)
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
    # An UPLOADED census is the underwriter's own statement of who is
    # renewing and on what category, and the book must never replace it -
    # not even on an explicit reseed. On KIKO a reseed swapped a 67-row
    # uploaded file for 84 rows of book roster whose category was the
    # policy code rather than the broker's own letter, and nothing on
    # screen said the census had changed.
    uploaded_census = db.query(models.CensusRecord).filter_by(
        case_id=case.id, source="upload").count()
    census_protected = bool(uploaded_census)
    if not census_protected and (not existing_census or payload.reseed_census):
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
        db.add_all(models.CensusRecord(case_id=case.id, source="book", **row) for row in rows)
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
    products: List[str] = Query(
        [],
        description="Restrict to members on these Products (Platinum/Gold/Silver/Bronze/Group). Omit for every product.",
    ),
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
    results = book_analysis.run_analysis(
        db, as_of=as_of or book_repo.stored_as_of(db), policy_year=policy_year, client=client,
        filters=filters, products=products or None,
    )
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
    results = book_analysis.run_analysis(db, as_of=as_of or book_repo.stored_as_of(db), filters=filters)
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
    results = book_analysis.run_analysis(db, as_of=as_of or book_repo.stored_as_of(db), policy_year=policy_year, client=client, filters=filters)
    in_scope = [r for r in results if r.get("in_scope", True)]
    out_of_scope_count = len(results) - len(in_scope)
    unmapped_product_count = sum(1 for r in in_scope if not r.get("product"))
    unmapped_network_count = sum(1 for r in in_scope if not r.get("network"))
    rate_cards = book_repo.rate_cards(db)

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
    results = book_analysis.run_analysis(db, as_of=as_of or book_repo.stored_as_of(db), policy_year=policy_year, client=client, filters=filters)
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
    return book_analysis.run_analysis(db, as_of=as_of or book_repo.stored_as_of(db), policy_year=policy_year, client=client, filters=filters)


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
    results = book_analysis.run_analysis(db, as_of=as_of or book_repo.stored_as_of(db), policy_year=policy_year, client=client, filters=filters)
    rate_cards = book_repo.rate_cards(db)
    return summarize_burning_cost_by_age_gender(results, rate_cards)


@router.get("/burning-cost-by-product-network", response_model=List[dict])
def burning_cost_by_product_network(
    as_of: Optional[date] = Query(None, description="Date to compute earned premium as of"),
    products: List[str] = Query(
        [],
        description="Restrict to members on these Products (Platinum/Gold/Silver/Bronze/Group). Omit for every product.",
    ),
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
        results = book_analysis.run_analysis(db, as_of=as_of or book_repo.stored_as_of(db), products=products or None)
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
        results = book_analysis.run_analysis(db, as_of=as_of or book_repo.stored_as_of(db))
    except HTTPException:
        return []
    rate_cards = book_repo.rate_cards(db)
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
    analysis (book_analysis.run_analysis) - none of these 6 fields need that (no
    standard_premium or actual_claims involved), and this endpoint fires
    on every Portfolio Analysis page load plus after every upload, so
    running the expensive full pipeline here made the whole screen feel
    like it hung on a real multi-thousand-member book.
    """
    members = book_repo.members(db)
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






def _claim_dicts_for_utilization(db: Session) -> List[dict]:
    """Every uploaded claim line's own ip_op_maternity/medical_category/
    medical_act/final_amount, plus its resolved master client (see
    book_repo.master_client_by_beneficiary) and resolved Product (see
    book_repo.product_by_beneficiary) so this can be scoped to one client
    or filtered to a set of Products - a Utilization of Benefits view,
    like Large Claims, is purely about the claim lines themselves and
    needs no member/rate-card join otherwise (see book_repo.
    large_claim_lines's own docstring).
    """
    master_client_by_beneficiary = book_repo.master_client_by_beneficiary(db)
    product_by_beneficiary = book_repo.product_by_beneficiary(db)
    network_region_by_beneficiary = book_repo.network_region_by_beneficiary(db)

    rows = db.query(
        models.PortfolioClaimEntry.patient_id,
        models.PortfolioClaimEntry.client_name,
        models.PortfolioClaimEntry.provider_name,
        models.PortfolioClaimEntry.ip_op_maternity,
        models.PortfolioClaimEntry.medical_category,
        # Needed to split PARAMEDICAL into physiotherapy vs alternative
        # treatment - the category alone cannot tell them apart. Also the
        # only field that distinguishes a C-section delivery line from a
        # normal delivery line - see maternity_cost_by_case.
        models.PortfolioClaimEntry.medical_act,
        models.PortfolioClaimEntry.final_amount,
    ).all()
    return [
        {
            "patient_id": patient_id,
            "master_client": master_client_by_beneficiary.get(patient_id) or client_name,
            "product": product_by_beneficiary.get(patient_id),
            "network": (network_region_by_beneficiary.get(patient_id) or {}).get("network"),
            "region": (network_region_by_beneficiary.get(patient_id) or {}).get("region"),
            "provider_name": provider_name,
            "ip_op_maternity": ip_op_maternity,
            "medical_category": medical_category,
            "medical_act": medical_act,
            "final_amount": final_amount,
        }
        for patient_id, client_name, provider_name, ip_op_maternity, medical_category, medical_act, final_amount in rows
    ]


@router.get("/utilization")
def portfolio_utilization(
    master_client: Optional[str] = Query(
        None, description="Restrict to one master client's own claims (for a client-level report) - matches the resolved master client name, same as /large-claims"
    ),
    products: List[str] = Query(
        [],
        description="Restrict to claims from members on these Products (Platinum/Gold/Silver/Bronze/Group). Omit for every product.",
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
    if products:
        claims = [c for c in claims if c.get("product") in products]
    if not claims:
        raise HTTPException(status_code=400, detail="No claims uploaded yet")
    return {
        "by_encounter_type": utilization_by_encounter_type(claims),
        "by_benefit_category": utilization_by_benefit_category(claims),
    }


@router.get("/maternity-cost-by-case")
def portfolio_maternity_cost_by_case(
    master_client: Optional[str] = Query(None, description="Restrict to one master client's own claims"),
    products: List[str] = Query(
        [],
        description="Restrict to claims from members on these Products (Platinum/Gold/Silver/Bronze/Group). Omit for every product.",
    ),
    db: Session = Depends(get_db),
):
    """Maternity cost per CASE (one pregnancy's whole episode - antenatal,
    delivery, postnatal/newborn - not one claim line at a time), split
    Normal delivery vs C-section, and by the delivery's own provider. See
    maternity_cost_by_case for exactly how a case is classified and why
    diagnosis codes aren't used for it.

    Whole-book by default, independent of any rate card or membership
    upload - purely a claims analysis, same as /utilization.
    """
    claims = _claim_dicts_for_utilization(db)
    if master_client:
        claims = [c for c in claims if c.get("master_client") == master_client]
    if products:
        claims = [c for c in claims if c.get("product") in products]
    if not claims:
        raise HTTPException(status_code=400, detail="No claims uploaded yet")
    return maternity_cost_by_case(claims)


@router.get("/provider-cost-comparison")
def portfolio_provider_cost_comparison(
    provider_markers: List[str] = Query(
        ..., description="Match providers whose own name contains any of these (case-insensitive), e.g. provider_markers=Mediclinic"
    ),
    network: Optional[str] = Query(None, description="Scope to one resolved Network (e.g. 'MSH Comprehensive') - both sides of the comparison are scoped the same way"),
    region: Optional[str] = Query(None, description="Scope to one region (Dubai/Abu Dhabi/Northern Emirates)"),
    master_client: Optional[str] = Query(None, description="Restrict to one master client's own claims"),
    db: Session = Depends(get_db),
):
    """Total spend/claim count/average cost per claim at providers whose
    name matches `provider_markers` vs every other provider, scoped to
    one network/region so the comparison is apples-to-apples. NOT a
    burning cost - see provider_cost_comparison's own docstring for why
    that denominator doesn't exist at provider granularity.
    """
    claims = _claim_dicts_for_utilization(db)
    if master_client:
        claims = [c for c in claims if c.get("master_client") == master_client]
    if not claims:
        raise HTTPException(status_code=400, detail="No claims uploaded yet")
    return provider_cost_comparison(claims, tuple(provider_markers), network=network, region=region)


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
    products: List[str] = Query(
        [],
        description="Restrict to claims from members on these Products (Platinum/Gold/Silver/Bronze/Group). Omit for every product.",
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
    claims = book_repo.large_claim_lines(db)
    if master_client:
        claims = [c for c in claims if c.get("client_name") == master_client]
    if products:
        claims = [c for c in claims if c.get("product") in products]
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


@router.get("/annual-limit-exposure")
def get_annual_limit_exposure(
    limit: List[float] = Query(default=[], description="Candidate annual limits in AED. Repeat the param for several; omit for the standard set."),
    master_client: Optional[str] = Query(None, description="Scope to one master client's own members instead of the whole book."),
    members_above: Optional[float] = Query(None, description="Also return the members a limit of this many AED would have cut off, biggest first."),
    db: Session = Depends(get_db),
):
    """How many members in the book would breach a given annual limit,
    and what the limit would not have paid.

    The question behind every annual-limit dropdown, answered off the
    portfolio's own claims instead of by instinct. Claims are measured
    over a rolling 365 days rather than per calendar year - see
    app/scoring/rules/annual_limit_exposure.py for why that distinction
    is the whole point rather than a detail.

    Amounts are AED throughout, including limits written in USD on the
    table of benefits (converted at the peg).
    """
    from app.scoring.rules.annual_limit_exposure import (
        DEFAULT_LIMITS_AED,
        annual_limit_exposure,
        members_above_limit,
    )

    claims = book_repo.large_claim_lines(db)
    if master_client:
        claims = [c for c in claims if c.get("client_name") == master_client]
    if not claims:
        raise HTTPException(status_code=400, detail="No claims uploaded yet")

    report = annual_limit_exposure(claims, limit or DEFAULT_LIMITS_AED)
    if members_above is not None:
        report["members_above_limit"] = members_above_limit(claims, members_above)
    return report


def _client_loading_row(record: models.ClientMasterInfo) -> dict:
    return {
        "id": record.id,
        "master_client_name": record.master_client_name,
        "opex_pct": record.opex_pct,
        "product": record.product,
        "start_date": record.start_date.isoformat() if record.start_date else None,
        "end_date": record.end_date.isoformat() if record.end_date else None,
        "manually_edited": bool(record.manually_edited),
        "source_filename": record.source_filename,
        # Sent so the screen can render a closed window as fixed, rather
        # than letting someone type into it and only then be refused.
        "window_has_closed": _window_has_closed(record),
    }


@router.get("/client-loading")
def get_client_loading(db: Session = Depends(get_db)):
    """Every client's own OPEX/loading record, plus the accounts still
    running on the book default.

    The second list is the point of the screen. An account with premium
    and claims but no OPEX record has its combined ratio measured against
    an assumption, and nothing on any other screen says so - it just
    quietly reads as though the figure were known.
    """
    from app.scoring.rules.portfolio_analysis import DEFAULT_EXPENSE_RATIO_PCT

    records = (
        db.query(models.ClientMasterInfo)
        .order_by(models.ClientMasterInfo.master_client_name, models.ClientMasterInfo.start_date.desc())
        .all()
    )
    on_file = {_normalize_client_key(r.master_client_name) for r in records}

    # Clients carrying real exposure that no record covers. Read off the
    # membership table directly rather than through book_analysis.run_analysis, so the
    # screen still works before an analysis has been run.
    gaps: Dict[str, dict] = {}
    subgroup_master = book_repo.subgroup_master_by_name(db)
    for member in db.query(models.PortfolioMember).all():
        name = resolve_master_client(
            {
                "master_client_name": member.master_client_name,
                "contract": member.contract,
                "master_contract": member.master_contract,
            },
            subgroup_master,
        )
        if not name or _normalize_client_key(name) in on_file:
            continue
        bucket = gaps.setdefault(name, {"master_client_name": name, "lives": 0, "gross_premium": 0.0})
        bucket["lives"] += 1
        bucket["gross_premium"] += member.gross_premium or 0.0

    return {
        "default_opex_pct": DEFAULT_EXPENSE_RATIO_PCT,
        "records": [_client_loading_row(r) for r in records],
        "without_a_record": sorted(
            ({**g, "gross_premium": round(g["gross_premium"], 2)} for g in gaps.values()),
            key=lambda g: -g["gross_premium"],
        ),
    }


def _normalize_client_key(name: Optional[str]) -> str:
    return " ".join(str(name or "").split()).casefold()




@router.post("/client-loading")
def upsert_client_loading(payload: schemas.ClientLoadingIn, db: Session = Depends(get_db)):
    """Create or edit one client's loading record by hand.

    Marks the record manually_edited, which is what keeps it from being
    wiped by the next Client Master upload - and also means this client
    stops tracking the sheet. That trade is deliberate and is stated on
    the screen rather than left to be discovered.
    """
    if not (payload.master_client_name or "").strip():
        raise HTTPException(status_code=400, detail="A client name is required")
    if payload.opex_pct is None:
        raise HTTPException(status_code=400, detail="A loading is required")
    if not 0 <= payload.opex_pct < 1:
        raise HTTPException(
            status_code=400,
            detail="Loading must be a fraction of premium between 0 and 1 (0.265 for 26.5%)",
        )
    if payload.start_date and payload.end_date and payload.end_date <= payload.start_date:
        raise HTTPException(status_code=400, detail="The end date must fall after the start date")

    record = db.get(models.ClientMasterInfo, payload.id) if payload.id else None
    if payload.id and not record:
        raise HTTPException(status_code=404, detail="No such loading record")
    if record is not None and _window_has_closed(record) and not payload.correcting_an_error:
        raise HTTPException(status_code=409, detail=_CLOSED_WINDOW_DETAIL)
    if record is None:
        record = models.ClientMasterInfo()
        db.add(record)

    record.master_client_name = payload.master_client_name.strip()
    record.opex_pct = payload.opex_pct
    record.start_date = payload.start_date
    record.end_date = payload.end_date
    # Product stays whatever it was: it is reference data sourced from the
    # membership export's own PRODUCTNAME column, not something this
    # screen is entitled to overwrite.
    record.manually_edited = True
    db.commit()
    db.refresh(record)
    return _client_loading_row(record)


#: A window whose end date has passed has already been used: any
#: combined ratio reported for that period was measured against the
#: loading in it. Changing it now silently changes a number that has
#: been seen, so it is fixed unless the change is explicitly a
#: correction of a wrong entry.
_CLOSED_WINDOW_DETAIL = (
    "This window has already closed, so the figures reported for it were measured against "
    "this loading. If the loading changed at renewal, add a new window instead. If this "
    "figure was entered wrongly, resend with correcting_an_error set."
)


def _window_has_closed(record: models.ClientMasterInfo, as_of: Optional[date] = None) -> bool:
    """True once this window's own end date is behind us. A window with
    no end date is open-ended and never closes.
    """
    if not record.end_date:
        return False
    return record.end_date < (as_of or date.today())


@router.delete("/client-loading/{record_id}")
def delete_client_loading(
    record_id: int,
    correcting_an_error: bool = Query(
        False,
        description="Required to remove a window that has already closed - see _CLOSED_WINDOW_DETAIL.",
    ),
    db: Session = Depends(get_db),
):
    """Remove one dated record.

    Deletes a single window, never a client's whole history: an earlier
    renewal's loading is what makes a past year's combined ratio
    reproducible, so removing it changes numbers that have already been
    reported. A window that has already closed is fixed for that reason -
    removing it needs correcting_an_error, which is the one legitimate
    case (the figure in it was typed wrongly).
    """
    record = db.get(models.ClientMasterInfo, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="No such loading record")
    if _window_has_closed(record) and not correcting_an_error:
        raise HTTPException(status_code=409, detail=_CLOSED_WINDOW_DETAIL)
    db.delete(record)
    db.commit()
    return {"deleted": record_id}
