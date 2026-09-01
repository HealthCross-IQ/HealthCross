import re
import string
from collections import defaultdict
from datetime import date
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.ingestion.benefits import parse_table_of_benefits
from app.ingestion.benefits_ocr import is_scanned_pdf, parse_benefits_pdf_ocr
from app.ingestion.benefits_pdf import (
    parse_benefits_pdf,
    parse_benefits_pdf_text_fallback,
    to_benefit_plan_fields,
)
from app.ingestion.census import parse_census
from app.ingestion.claims import parse_claims
from app.ingestion.claims_ledger import parse_claims_ledger
from app.ingestion.claims_report import parse_claims_report
from app.ingestion.daman_tob import extract_all_rows as extract_daman_tob_rows
from app.ingestion.international_tob import extract_benefit_rows as extract_generic_benefit_rows
from app.ingestion.labeled_row_benefits_pdf import parse_labeled_row_benefits_pdf
from app.ingestion.plan_details import parse_plan_details
from app.ingestion.quote_pdf import parse_benefit_tables_only, parse_quote_pdf
from app.ingestion.upload_sniffer import sniff_upload_kind
from app.models import db_models as models
from app.models import schemas
from app.api.routes_new_business_rating import maybe_auto_requote
from app.reference.benefit_category_mapping import build_standard_summary_from_rows, to_case_benefit_plan_fields
from app.scoring.rules.benefits_summary import STANDARD_FIELDS

from app.scoring.rules.portfolio_analysis import ACCOUNT_IBNR_TAIL_DAYS, FULL_POLICY_TERM_DAYS
from app.book import repository as book_repo

router = APIRouter(prefix="/cases", tags=["cases"])


def _get_case_or_404(db: Session, case_id: int) -> models.Case:
    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


_CATEGORY_FROM_FILENAME_RE = re.compile(r"cat(?:egor|egro)?y?[\s_-]+([A-Za-z])(?:[\s_.]|$)", re.IGNORECASE)


def _infer_category_from_filename(filename: str) -> Optional[str]:
    """Best-effort fallback for 'append' mode (one file per category) when
    a file's own content never literally spells out its category letter -
    common when the insurer's own tier naming (e.g. Cigna's "SmartCare Plan
    1/2/3", or a bespoke "CAT VIP") doesn't match the broker's own A/B/C/D
    category lettering at all, but the filename itself does (e.g.
    "Category_B.pdf", or the real-world typo "Categroy_A.pdf"). Never
    overrides a category the parser actually found in the document.
    """
    match = _CATEGORY_FROM_FILENAME_RE.search(filename)
    return match.group(1).upper() if match else None


# Placeholder names a fallback parser assigns when the document itself
# gives no real plan/tier name to use - the same for every such upload,
# so nothing worth keeping once a real category letter is known instead.
_GENERIC_PLAN_NAMES = {"Base Plan", "Text extract (verify against source - table structure not recognized)"}


@router.post("", response_model=schemas.CaseOut)
def create_case(payload: schemas.CaseCreate, db: Session = Depends(get_db)):
    case = models.Case(**payload.model_dump())
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


@router.get("", response_model=List[schemas.CaseOut])
def list_cases(db: Session = Depends(get_db)):
    return db.query(models.Case).order_by(models.Case.submitted_at.desc()).all()


# Registered BEFORE /{case_id}: FastAPI matches routes in definition
# order, so a literal path that could also parse as a case id has to
# come first or /{case_id} swallows it and 422s on the int conversion.
@router.get("/renewal-summary")
def get_renewal_summary(
    as_of: Optional[date] = Query(None, description="Date the policy year is measured as elapsed to"),
    db: Session = Depends(get_db),
):
    """Every renewal case with its own loss ratio and the increase Method
    1 quotes for it - so the renewal list itself says which cases need
    attention, rather than each having to be opened to find out.

    The increase is Method 1's, produced by the same function the case's
    own Renewal Bench calls, so the board and the case cannot disagree.
    It carries no target loss ratio: the board used to re-price every
    account to a house target, which is a different question from what
    the account costs, and the two answers did not match.

    Claims to date are measured against premium EARNED to date, both
    over the same elapsed part of the policy year. Neither side is
    projected.

    They used to be summed straight off the ledger and divided by a FULL
    year's premium, which is not a loss ratio - a part-year numerator
    over a whole-year denominator understates every account in
    proportion to how much of the year is left. Safran, ten and a half
    months in, read 86% and suggested giving 6% BACK.

    The first correction annualised the claims instead. That fixes the
    mismatch and it is the wrong half to fix: annualising asserts the
    rest of the year will look like the part observed, which on KIKO -
    where one family carried 9% of the claims - is exactly the
    assumption that fails. Earning the premium down is a measurement;
    projecting the claims up is a guess. This does the first.

    IBNR uses the same 30-day paid run-rate tail the portfolio Loss
    Ratio board uses (see ibnr_for_member), so the two boards cannot
    disagree about what a year cost.
    """
    from app.api.case_loading import renewal_loading
    from app.api.routes_analysis import _case_renewal_rating
    from app.scoring.rules.renewal_rating import DEFAULT_INFLATION_PCT, renewal_from_loss_ratio

    cases = (
        db.query(models.Case)
        .filter(models.Case.business_type == "existing")
        .order_by(models.Case.renewal_date.is_(None), models.Case.renewal_date.asc())
        .all()
    )

    reference = as_of or date.today()
    out = []
    for case in cases:
        paid = sum((e.final_amount or 0.0) for e in case.claims_ledger_entries
                   if "outstanding" not in str(e.claim_status or "").lower())
        outstanding = sum((e.final_amount or 0.0) for e in case.claims_ledger_entries
                          if "outstanding" in str(e.claim_status or "").lower())

        # How much of the policy year has run. Capped at a full term:
        # past expiry the year is complete and both sides are final.
        elapsed = None
        if case.policy_start_date:
            end = min(reference, case.renewal_date or reference)
            elapsed = (end - case.policy_start_date).days + 1
            elapsed = min(elapsed, FULL_POLICY_TERM_DAYS) if elapsed > 0 else None
        earned_fraction = (elapsed / FULL_POLICY_TERM_DAYS) if elapsed else 1.0

        # Same 30-day paid run-rate tail the Loss Ratio board uses, and
        # nil once the term has run its full year - by then claims have
        # had long enough to filter through.
        ibnr = 0.0
        if paid and elapsed and elapsed < FULL_POLICY_TERM_DAYS:
            ibnr = round(paid / elapsed * ACCOUNT_IBNR_TAIL_DAYS, 2)
        incurred = paid + outstanding + ibnr

        annual_premium = case.current_annual_premium
        premium = annual_premium * earned_fraction if annual_premium else None
        # This account's own loading. A board of many accounts must not go
        # blank because one has no fee split, so the row still reports its
        # GROSS ratio - which needs no loading - and says its net one is
        # waiting on the split rather than showing a ratio struck at an
        # expense level nobody entered.
        loading, loading_problems = renewal_loading(case)
        net_premium = premium * (1 - loading) if (premium and loading is not None) else None
        gross_lr = (incurred / premium) if premium else None
        net_lr = (incurred / net_premium) if net_premium else None

        # The increase on this board IS Method 1's, because it is the same
        # function that produces it - not the same formula reimplemented.
        #
        # The board used to run a second formula of its own: claims put on
        # a full-year footing, multiplied by trend, divided by a house
        # target loss ratio, then grossed up. Method 1 adds inflation in
        # POINTS to the loss ratio, divides by (1 - loading), applies no
        # target and floors the ask at 9%. Two formulas cannot agree - and
        # matching the arithmetic would not have been enough either, since
        # Method 1 reads the account off the BOOK where it is on it, while
        # the board reads the case's own ledger. The list is where an
        # underwriter decides which case to open, so it was sending them
        # in on a figure the case itself then contradicted.
        #
        # _case_renewal_rating reads the book once for the whole board
        # (the analysis behind it is cached), so calling it per case is a
        # lookup rather than a re-analysis.
        rating = _case_renewal_rating(case)
        required_gross = None
        increase_pct = None
        if rating and not rating.get("pricing_blocked"):
            required_gross = rating.get("required_premium")
            increase_pct = rating.get("renewal_increase_pct")
        elif rating is None and gross_lr is not None and annual_premium and loading is not None:
            # Not on the book and no rating of its own - the same ladder,
            # off this board's own measured ratio.
            ladder = renewal_from_loss_ratio(
                gross_lr, annual_premium,
                inflation_pts=DEFAULT_INFLATION_PCT,
                loading_pct=loading,
            )
            required_gross = ladder["required_premium"]
            increase_pct = ladder["renewal_increase_pct"]

        out.append({
            "id": case.id,
            "company_name": case.company_name,
            "broker_name": case.broker_name,
            "renewal_date": case.renewal_date.isoformat() if case.renewal_date else None,
            "status": case.status.value if hasattr(case.status, "value") else case.status,
            "member_count": len(case.census_records),
            "incurred_claims": round(incurred, 2),
            # The build-up, so the figure can be checked against a
            # spreadsheet line by line rather than trusted whole.
            "paid": round(paid, 2),
            "outstanding": round(outstanding, 2),
            "ibnr": round(ibnr, 2),
            "current_annual_premium": annual_premium,
            "earned_premium": round(premium, 2) if premium else None,
            "earned_fraction": round(earned_fraction, 4),
            "elapsed_days": elapsed,
            "claim_count": len(case.claims_ledger_entries),
            "loading_pct": round(loading, 4) if loading is not None else None,
            # Which accounts are waiting on their fee split, so the board
            # can point at them rather than just showing a gap.
            "loading_missing": [p["field"] for p in loading_problems] or None,
            "gross_loss_ratio": round(gross_lr, 4) if gross_lr is not None else None,
            "net_loss_ratio": round(net_lr, 4) if net_lr is not None else None,
            "required_premium": round(required_gross, 2) if required_gross else None,
            "suggested_increase_pct": increase_pct,
            "portfolio_master_client": case.portfolio_master_client,
        })
    return {
        # The ladder's own inflation, in POINTS - not a multiplicative
        # trend, and not a target loss ratio. A renewal is priced to what
        # the account costs plus inflation, grossed up for its own
        # loading, floored at the house minimum. There is no target ratio
        # in it and the board no longer advertises one.
        "inflation_pts": DEFAULT_INFLATION_PCT,
        "as_of": reference,
        "cases": out,
    }


@router.get("/{case_id}", response_model=schemas.CaseOut)
def get_case(case_id: int, db: Session = Depends(get_db)):
    return _get_case_or_404(db, case_id)


@router.patch("/{case_id}", response_model=schemas.CaseOut)
def update_case(case_id: int, payload: schemas.CaseUpdate, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(case, field, value)
    db.commit()
    db.refresh(case)
    return case


@router.delete("/{case_id}", status_code=204)
def delete_case(case_id: int, db: Session = Depends(get_db)):
    """Deletes a case and everything filed under it (census, benefit plans,
    claims, scorecards, quotes) - Case's own relationships all cascade, so
    this is a single delete, not a manual multi-table cleanup.
    """
    case = _get_case_or_404(db, case_id)
    db.delete(case)
    db.commit()


@router.post("/{case_id}/census", response_model=List[schemas.CensusRecordOut])
def upload_census(
    case_id: int,
    file: UploadFile = File(...),
    mode: str = Query(
        "replace",
        description=(
            "'replace' (default): this file is the WHOLE census - wipe out any "
            "previously-uploaded census rows first. 'merge-dates': this file only "
            "carries policy/member start-end dates (some brokers export enrollment "
            "dates as a separate 'endorsement' file from the main member roster) - "
            "match each row to an already-uploaded census row by its employee ref "
            "and fill in just the date columns, leaving every other field on the "
            "existing rows untouched."
        ),
    ),
    db: Session = Depends(get_db),
):
    case = _get_case_or_404(db, case_id)
    try:
        parsed = parse_census(file.file, file.filename, default_policy_start_date=case.policy_start_date)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse census file: {exc}")
    if not parsed:
        raise HTTPException(status_code=400, detail="No census rows found in file")

    if mode == "merge-dates":
        existing = db.query(models.CensusRecord).filter_by(case_id=case.id).all()
        if not existing:
            raise HTTPException(
                status_code=400,
                detail="No existing census to merge dates into - upload the main census file first.",
            )
        by_ref = {r.employee_ref: r for r in existing if r.employee_ref}
        date_fields = ("policy_start_date", "policy_end_date", "member_start_date", "member_end_date")
        for row in parsed:
            record = by_ref.get(row.get("employee_ref"))
            if not record:
                continue
            for field in date_fields:
                value = row.get(field)
                if value is not None:
                    setattr(record, field, value)
        db.commit()
        for record in existing:
            db.refresh(record)
        maybe_auto_requote(case.id, db)
        return existing

    # A re-upload replaces the case's census entirely rather than piling on
    # top of a previous one - otherwise re-uploading the same file (e.g.
    # after fixing a typo) silently multiplies the member count.
    existing = db.query(models.CensusRecord).filter_by(case_id=case.id).all()
    if existing:
        # Snapshot the relation mix (Employees/Spouses/Children/...) BEFORE
        # it's replaced, so Census Movement (Renewal Bench) can compare
        # this "expiring" state against whatever's uploaded next as the
        # "renewal" census - see GET /{case_id}/census-movement.
        relation_counts: dict = defaultdict(int)
        for r in existing:
            relation_counts[r.relation or "Unspecified"] += 1
        db.query(models.CensusSnapshot).filter_by(case_id=case.id).delete()
        db.add_all(
            models.CensusSnapshot(case_id=case.id, relation=relation, member_count=count)
            for relation, count in relation_counts.items()
        )
    db.query(models.CensusRecord).filter_by(case_id=case.id).delete()

    records = [models.CensusRecord(case_id=case.id, **row) for row in parsed]
    db.add_all(records)
    db.commit()
    for record in records:
        db.refresh(record)
    maybe_auto_requote(case.id, db)
    return records


@router.get("/{case_id}/renewal-premium")
def get_renewal_premium(
    case_id: int,
    trend_pct: float = Query(0.10, description="Medical inflation between the experience period and the policy period"),
    loading_pct: Optional[float] = Query(None, description="Total loading as a fraction. Defaults to the case's own fee components."),
    non_recurring_claims: float = Query(0.0, description="Cost judged not to repeat - a completed maternity, a one-off event on a member who has left"),
    forward_provision: float = Query(0.0, description="Expected but not-yet-incurred exposure to add back - e.g. a maternity provision for the members still on risk"),
    as_of: Optional[date] = Query(None, description="Reference date for IBNR's elapsed-days calculation - defaults to today"),
    db: Session = Depends(get_db),
):
    """The renewal price for the members who are actually renewing, built
    from this account's own experience.

    Leavers' claims are excluded: they do not carry forward, and
    charging the continuing members for them prices the account on risk
    it no longer holds. Who left is read off each member's own cover
    dates rather than by matching the expiring roster to the renewal
    census - that matching falls back to guessing on relation, gender
    and age, and on Safran it did all of the work, reporting 93 leavers
    taking half the year's claims when the census simply carried no
    dates of birth. A renewal price is the last place to accept a
    guessed split. IBNR uses the same 30-day tail rule the Loss Ratio
    board uses, so a renewal quote and the loss ratio for the same
    account can't disagree about what the year cost.

    `non_recurring_claims` and `forward_provision` are the underwriter's
    two judgement inputs and are deliberately separate rather than netted:
    a completed pregnancy comes out of the base because it is over, and a
    maternity provision goes back in because the group still contains
    members who may deliver. Both appear as their own line in the
    build-up.
    """
    from app.api.case_loading import renewal_loading
    from app.scoring.rules.expected_cost_pricing import renewal_premium_from_experience
    from app.scoring.rules.portfolio_analysis import ACCOUNT_IBNR_TAIL_DAYS, FULL_POLICY_TERM_DAYS

    from app.scoring.rules.portfolio_analysis import group_claims_by_beneficiary
    from app.scoring.rules.renewal_intake import account_members, claims_by_member_status

    case = _get_case_or_404(db, case_id)
    if not case.portfolio_master_client:
        raise HTTPException(
            status_code=400,
            detail=(
                "This renewal price needs a case opened from the book - the split between "
                "continuing and leaving members is read off the Membership export's own cover "
                "dates. Open this renewal from the Renewal Due List."
            ),
        )
    book = book_repo.members(db)
    account = account_members(book, case.portfolio_master_client, book_repo.subgroup_master_by_name(db))
    if not account:
        raise HTTPException(
            status_code=404,
            detail=f"No members on the book for '{case.portfolio_master_client}'",
        )
    claim_rows = [
        {"patient_id": pid, "date_of_treatment": dot, "final_amount": amt, "claim_status": st}
        for pid, dot, amt, st in db.query(
            models.PortfolioClaimEntry.patient_id,
            models.PortfolioClaimEntry.date_of_treatment,
            models.PortfolioClaimEntry.final_amount,
            models.PortfolioClaimEntry.claim_status,
        ).all()
    ]
    split = claims_by_member_status(account, group_claims_by_beneficiary(claim_rows))

    reference = as_of or date.today()
    start = case.policy_start_date
    days = (min(reference, case.renewal_date or reference) - start).days + 1 if start else None
    # Same elapsed-days convention as the Loss Ratio board (inclusive, and
    # capped at the full term once the policy has expired), so a renewal
    # quote and the loss ratio for one account measure the year the same
    # way even though they apply the tail differently - see
    # renewal_premium_from_experience.
    if days is not None:
        days = min(days, FULL_POLICY_TERM_DAYS) if days > 0 else None

    # This account's own loading, or no price. Same gate as the renewal
    # rating: a quote built on an assumed expense level is part invented,
    # and this endpoint returns a premium an underwriter sends out.
    effective_loading = loading_pct
    if effective_loading is None:
        effective_loading, loading_problems = renewal_loading(case)
        if loading_problems:
            return {
                "gross_premium": None,
                "risk_premium": None,
                "increase_pct": None,
                "expiring_premium": case.current_annual_premium,
                "elapsed_days": days,
                "pricing_blocked": True,
                "pricing_problems": loading_problems,
            }

    priced = renewal_premium_from_experience(
        continuing_incurred=split["continuing"]["incurred"],
        elapsed_days=days,
        ibnr_tail_days=ACCOUNT_IBNR_TAIL_DAYS,
        loading_pct=effective_loading,
        trend_pct=trend_pct,
        non_recurring_claims=non_recurring_claims,
        forward_provision=forward_provision,
        # The population being priced is the one still on risk at the
        # term's end - joiners who are staying are already in it.
        member_count=split["continuing"]["member_count"],
    )

    expiring_premium = case.current_annual_premium
    priced["expiring_premium"] = expiring_premium
    priced["increase_pct"] = (
        round((priced["gross_premium"] / expiring_premium - 1) * 100, 1)
        if expiring_premium else None
    )
    priced["elapsed_days"] = days
    priced["movement"] = {
        "as_at": split["as_at"],
        "continuing_count": split["continuing"]["member_count"],
        "leaver_count": split["leaving"]["member_count"],
        "total_incurred": split["total"]["incurred"],
        "continuing_incurred": split["continuing"]["incurred"],
        "leaver_incurred": split["leaving"]["incurred"],
        "leaver_claims_share": split["leaver_share_of_claims"],
    }
    return priced


@router.post("/detect-upload-kind")
def detect_upload_kind(file: UploadFile = File(...)):
    """Best-effort guess at which upload slot this file belongs in
    (census/benefits/claims/claims-ledger/quote), for the case workspace's
    single drag-drop "Quick upload" zone - see app/ingestion/
    upload_sniffer.py. Never actually parses or stores anything; the
    result is always shown back to the user to confirm or correct before
    any real upload happens.
    """
    result = sniff_upload_kind(file.file, file.filename)
    return {"filename": file.filename, **result}


@router.get("/{case_id}/completeness", response_model=schemas.CaseCompletenessOut)
def get_case_completeness(case_id: int, db: Session = Depends(get_db)):
    """At-a-glance status of what's been uploaded for this case, so the
    case workspace can show a checklist instead of requiring a click into
    every tab just to see what's still missing - cheap COUNT queries
    only, no need to load the actual rows.
    """
    case = _get_case_or_404(db, case_id)

    census_count = db.query(models.CensusRecord).filter_by(case_id=case.id).count()
    existing_benefit_plan_count = (
        db.query(models.BenefitPlan).filter_by(case_id=case.id, role="existing").count()
    )
    quoted_benefit_plan_count = (
        db.query(models.BenefitPlan).filter_by(case_id=case.id, role="quoted").count()
    )
    claims_record_count = db.query(models.ClaimsRecord).filter_by(case_id=case.id).count()
    claims_report_count = db.query(models.ClaimsReport).filter_by(case_id=case.id).count()
    claims_ledger_entry_count = db.query(models.ClaimsLedgerEntry).filter_by(case_id=case.id).count()
    scorecard_count = db.query(models.Scorecard).filter_by(case_id=case.id).count()

    latest_scorecard = (
        db.query(models.Scorecard)
        .filter_by(case_id=case.id)
        .order_by(models.Scorecard.created_at.desc())
        .first()
    )

    has_census = census_count > 0
    has_benefits = existing_benefit_plan_count > 0

    return schemas.CaseCompletenessOut(
        census_count=census_count,
        existing_benefit_plan_count=existing_benefit_plan_count,
        quoted_benefit_plan_count=quoted_benefit_plan_count,
        claims_record_count=claims_record_count,
        claims_report_count=claims_report_count,
        claims_ledger_entry_count=claims_ledger_entry_count,
        scorecard_count=scorecard_count,
        has_census=has_census,
        has_benefits=has_benefits,
        has_quote=quoted_benefit_plan_count > 0,
        has_claims=(claims_record_count + claims_report_count) > 0,
        has_claims_ledger=claims_ledger_entry_count > 0,
        has_scorecard=scorecard_count > 0,
        ready_to_score=has_census and has_benefits,
        latest_risk_tier=latest_scorecard.risk_tier if latest_scorecard else None,
    )


@router.get("/{case_id}/benefit-plans", response_model=List[schemas.BenefitPlanOut])
def list_benefit_plans(case_id: int, db: Session = Depends(get_db)):
    """Every existing- and quoted-role benefit plan on this case, with its
    id - used by the category-mapping UI (see PUT .../benefits/{plan_id}/
    match) to build its existing-plan/quoted-plan pickers, since
    GET /benefits-summary and /benefits-comparison don't expose plan ids.
    """
    case = _get_case_or_404(db, case_id)
    return case.benefit_plans


@router.post("/{case_id}/benefits", response_model=List[schemas.BenefitPlanOut])
def upload_benefits(
    case_id: int,
    file: UploadFile = File(...),
    mode: str = Query(
        "replace",
        description=(
            "'replace' (default): this file is the WHOLE table of benefits - wipe out any "
            "previously-uploaded existing-role plans first. 'append': this file covers only "
            "ONE category (some insurers split each category into its own file) - keep other "
            "categories' plans, only replacing a plan that shares this file's category letter."
        ),
    ),
    category: Optional[str] = Query(
        None,
        description=(
            "This file's own category letter (e.g. 'A'), when the underwriter picks it explicitly "
            "at upload time rather than relying on the document's content or filename to carry it - "
            "authoritative when given, overriding whatever the parser itself would have detected. "
            "Only applied when this file produced exactly one plan."
        ),
    ),
    db: Session = Depends(get_db),
):
    case = _get_case_or_404(db, case_id)
    is_pdf = file.filename.lower().endswith(".pdf")

    try:
        if is_pdf and is_scanned_pdf(file.file):
            ocr_result = parse_benefits_pdf_ocr(file.file, file.filename)
            plans = [
                models.BenefitPlan(
                    case_id=case.id,
                    plan_name="OCR extract (verify against source)",
                    source_format="pdf-ocr",
                    standard_summary=ocr_result["summary"],
                    raw_ocr_text=ocr_result["raw_ocr_text"],
                )
            ]
        elif is_pdf:
            tier_summaries = parse_benefits_pdf(file.file, file.filename)
            if tier_summaries:
                plans = [
                    models.BenefitPlan(case_id=case.id, **to_benefit_plan_fields(tier, summary))
                    for tier, summary in tier_summaries.items()
                ]
            else:
                # No Bupa/Sukoon-style bordered tier table found - try the
                # QIC/HealthCROSS Global "Plan - CAT X" layout instead (the
                # same one app/ingestion/quote_pdf.py handles for a NEW
                # quote), since an existing/incumbent benefits document from
                # that same insurer family uses this layout too, just with
                # different row-label wording and no premium table.
                file.file.seek(0)
                cat_style_plans = parse_benefit_tables_only(file.file, file.filename)
                if cat_style_plans:
                    plans = [
                        models.BenefitPlan(
                            case_id=case.id,
                            plan_name=tier_header,
                            category=entry.get("category"),
                            network_type=entry.get("network"),
                            annual_limit=entry.get("annual_limit"),
                            maternity_limit=entry.get("maternity_limit"),
                            maternity_covered=entry.get("maternity_limit") is not None,
                            dental_covered=entry.get("dental_covered", False),
                            optical_covered=entry.get("optical_covered", False),
                            pre_existing_covered=entry.get("pre_existing_covered", False),
                            chronic_covered=entry.get("chronic_covered", False),
                            source_format="pdf-cat-style",
                            standard_summary=entry.get("standard_summary"),
                        )
                        for tier_header, entry in cat_style_plans.items()
                    ]
                else:
                    # Neither the Bupa-style nor CAT-style table shape
                    # matched - try Daman's own "Schedule of Benefits"
                    # layout (e.g. "Uselect Bronze/Silver/Gold"): one tier
                    # per FILE, with most benefit rows carrying a Network
                    # AND a Non-network % side by side rather than one flat
                    # value. Returns None when this isn't that document
                    # family either, so it falls through to the next parser.
                    file.file.seek(0)
                    try:
                        daman_plan = extract_daman_tob_rows(file.file, file.filename)
                    except Exception:
                        daman_plan = None
                    if daman_plan:
                        daman_summary = build_standard_summary_from_rows(daman_plan["rows"])
                        plans = [
                            models.BenefitPlan(
                                case_id=case.id,
                                plan_name=daman_plan["plan_name"],
                                source_format="pdf-daman-tob",
                                standard_summary=daman_summary,
                                **to_case_benefit_plan_fields(daman_summary),
                            )
                        ]
                    else:
                        # Not Bupa/CAT-style/Daman-shaped - try the "labeled
                        # 3-column row" layout (e.g. MaxHealth/MaxMed's
                        # "MAXMED Neuron <TIER> GROUP" docs): one category
                        # per FILE rather than one tier per column, with a
                        # benefit label/value/description row structure
                        # instead. Returns None (not just empty) when this
                        # isn't that document family either, so it falls
                        # through cleanly to the crude text scan below.
                        file.file.seek(0)
                        labeled_row_plan = parse_labeled_row_benefits_pdf(file.file, file.filename)
                        if labeled_row_plan:
                            plans = [
                                models.BenefitPlan(
                                    case_id=case.id,
                                    plan_name=labeled_row_plan["plan_name"],
                                    category=labeled_row_plan.get("category"),
                                    network_type=labeled_row_plan.get("network"),
                                    annual_limit=labeled_row_plan.get("annual_limit"),
                                    maternity_limit=labeled_row_plan.get("maternity_limit"),
                                    maternity_covered=labeled_row_plan.get("maternity_covered", False),
                                    dental_covered=labeled_row_plan.get("dental_covered", False),
                                    optical_covered=labeled_row_plan.get("optical_covered", False),
                                    pre_existing_covered=labeled_row_plan.get("pre_existing_covered", False),
                                    chronic_covered=labeled_row_plan.get("chronic_covered", False),
                                    source_format="pdf-labeled-row",
                                    standard_summary=labeled_row_plan.get("standard_summary"),
                                )
                            ]
                        else:
                            # Not Bupa/CAT-style/Maxmed-shaped, but still a
                            # real bordered table (e.g. Sukoon's single-tier
                            # "Category 1" layout, or the international
                            # insurers' generic label|value|clarification
                            # tables) - the detailed comparison's own parser
                            # already handles these well (app/ingestion/
                            # international_tob.py), so map its raw rows
                            # onto the fixed 11-field standard summary via
                            # the same category matching used there, rather
                            # than falling all the way to the crude scan below.
                            file.file.seek(0)
                            try:
                                generic_rows = extract_generic_benefit_rows(file.file, file.filename)
                            except Exception:
                                generic_rows = []
                            generic_summary = build_standard_summary_from_rows(generic_rows) if generic_rows else {}
                            if generic_summary:
                                plans = [
                                    models.BenefitPlan(
                                        case_id=case.id,
                                        plan_name="Base Plan",
                                        source_format="pdf-generic-table",
                                        standard_summary=generic_summary,
                                        **to_case_benefit_plan_fields(generic_summary),
                                    )
                                ]
                            else:
                                # Real extractable text, but no bordered
                                # table recognizable at all (e.g. a layout
                                # that uses whitespace alignment rather than
                                # actual table lines) - fall back to the
                                # same label-anchored nearby-value scan used
                                # for scanned/OCR'd PDFs, just against this
                                # PDF's real text instead of an OCR'd image
                                # (see app/ingestion/benefits_pdf.py's
                                # parse_benefits_pdf_text_fallback for why
                                # this is safer than a bespoke table parser
                                # here).
                                file.file.seek(0)
                                fallback_result = parse_benefits_pdf_text_fallback(file.file, file.filename)
                                plans = [
                                    models.BenefitPlan(
                                        case_id=case.id,
                                        plan_name="Text extract (verify against source - table structure not recognized)",
                                        source_format="pdf-text-fallback",
                                        standard_summary=fallback_result["summary"],
                                        raw_ocr_text=fallback_result["raw_text"],
                                    )
                                ]
        else:
            parsed = parse_table_of_benefits(file.file, file.filename)
            plans = [models.BenefitPlan(case_id=case.id, source_format=file.filename.rsplit(".", 1)[-1].lower(), **row) for row in parsed]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse table of benefits: {exc}")
    if not plans:
        raise HTTPException(status_code=400, detail="No benefit plans found in file")

    if len(plans) == 1 and category and category.strip():
        # The underwriter's own explicit choice at upload time always wins -
        # more reliable than guessing from the document's content or
        # filename, and the only way to be sure the category this file
        # replaces (in append mode) is exactly right.
        plans[0].category = category.strip().upper()

    if mode == "append":
        # One file per category (some insurers ship each category's table of
        # benefits as its own separate document) - only replace a plan that
        # shares this file's category letter, so uploading category B's file
        # doesn't wipe out category A's plan uploaded a moment ago. A plan
        # with no detected category can't be matched this way, so it's just
        # added alongside whatever's already there - unless the filename
        # itself carries the category letter (e.g. "Category_B.pdf") and
        # this file produced exactly one plan, since the document's own
        # content commonly uses the insurer's own tier naming (e.g. Cigna's
        # "SmartCare Plan 1/2/3") rather than the broker's own A/B/C/D
        # lettering at all.
        if len(plans) == 1 and not plans[0].category:
            plans[0].category = _infer_category_from_filename(file.filename)
        # A generic fallback parser's own placeholder name ("Base Plan",
        # etc.) reads the same for every category uploaded this way,
        # leaving them indistinguishable at a glance even once each has
        # its own category letter - swap it for the category once known.
        if len(plans) == 1 and plans[0].category and plans[0].plan_name in _GENERIC_PLAN_NAMES:
            plans[0].plan_name = f"Category {plans[0].category}"
        categories_in_upload = {p.category for p in plans if p.category}
        if categories_in_upload:
            db.query(models.BenefitPlan).filter(
                models.BenefitPlan.case_id == case.id,
                models.BenefitPlan.role == "existing",
                models.BenefitPlan.category.in_(categories_in_upload),
            ).delete(synchronize_session=False)
            # This upload resolved a real category for itself, confirming
            # the underwriter is doing a proper one-category-per-file batch
            # - any OTHER existing-role plan still sitting around with no
            # category at all is leftover scaffolding from before a
            # category could be resolved (e.g. an OCR/parse failure, or a
            # manually-added plan from an older build that never set its
            # own category field), not something worth keeping alongside
            # a fully-categorized set.
            db.query(models.BenefitPlan).filter(
                models.BenefitPlan.case_id == case.id,
                models.BenefitPlan.role == "existing",
                models.BenefitPlan.category.is_(None),
            ).delete(synchronize_session=False)
    else:
        # Replace, not accumulate - see the census upload for why. Only this
        # case's EXISTING-role plans are replaced; a previously-uploaded quote
        # (role="quoted", see /quote below) is untouched so the two can be
        # compared side by side.
        db.query(models.BenefitPlan).filter_by(case_id=case.id, role="existing").delete()

    db.add_all(plans)
    db.commit()
    for plan in plans:
        db.refresh(plan)
    maybe_auto_requote(case.id, db)
    return plans


@router.post("/{case_id}/quote", response_model=List[schemas.BenefitPlanOut])
def upload_quote(case_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Uploads a NEW insurer's quotation for this case (category premiums +
    table of benefits) so it can be compared against the existing plan
    uploaded via /benefits - see GET /premium-by-category and
    GET /benefits-comparison. Currently targets the QIC/HealthCROSS Global
    "Full Category Premium Calculation" quote layout
    (app/ingestion/quote_pdf.py).
    """
    case = _get_case_or_404(db, case_id)
    try:
        parsed = parse_quote_pdf(file.file, file.filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse quote: {exc}")
    if not parsed:
        raise HTTPException(status_code=400, detail="No quoted categories found in file")

    plans = [
        models.BenefitPlan(
            case_id=case.id,
            role="quoted",
            source_format="pdf-quote",
            plan_name=f"{entry.get('plan_name') or 'Quoted'} - CAT {entry['category']}" if entry.get("category") else entry.get("plan_name"),
            category=entry.get("category"),
            network_type=entry.get("network"),
            member_count=entry.get("member_count"),
            gross_premium=entry.get("gross_premium"),
            annual_limit=entry.get("annual_limit"),
            maternity_limit=entry.get("maternity_limit"),
            maternity_covered=entry.get("maternity_limit") is not None,
            dental_covered=entry.get("dental_covered", False),
            optical_covered=entry.get("optical_covered", False),
            pre_existing_covered=entry.get("pre_existing_covered", False),
            chronic_covered=entry.get("chronic_covered", False),
            standard_summary=entry.get("standard_summary"),
        )
        for entry in parsed
    ]

    # Replace, not accumulate - only this case's quoted-role plans; the
    # existing-role plans from /benefits are untouched. Any existing-role
    # plan's manual category mapping (see PUT .../benefits/{plan_id}/match)
    # pointing at one of the quoted rows about to be deleted has to be
    # cleared first - otherwise it's left pointing at a row that no longer
    # exists once the new quote's rows replace them.
    old_quoted_ids = [
        row.id for row in db.query(models.BenefitPlan.id).filter_by(case_id=case.id, role="quoted")
    ]
    if old_quoted_ids:
        db.query(models.BenefitPlan).filter(
            models.BenefitPlan.case_id == case.id,
            models.BenefitPlan.matched_quote_plan_id.in_(old_quoted_ids),
        ).update({"matched_quote_plan_id": None}, synchronize_session=False)
    db.query(models.BenefitPlan).filter_by(case_id=case.id, role="quoted").delete()

    db.add_all(plans)
    db.commit()
    for plan in plans:
        db.refresh(plan)
    return plans


@router.put("/{case_id}/benefits/{plan_id}/match", response_model=schemas.BenefitPlanOut)
def set_benefit_plan_match(
    case_id: int, plan_id: int, payload: schemas.BenefitPlanMatchUpdate, db: Session = Depends(get_db)
):
    """Pins which quoted-role plan an existing-role plan should line up
    against in GET /benefits-comparison, for the cases where an insurer's
    own category naming doesn't match HealthCross's quote categories
    closely enough for the automatic match (see
    app/api/routes_analysis.py's _match_quoted_plan) to find it - e.g. an
    incumbent's "Bronze/Silver/Gold" tiers against a quote's "CAT A/B/C".
    Pass quoted_plan_id: null to clear a manual mapping and fall back to
    the automatic match again.
    """
    case = _get_case_or_404(db, case_id)
    plan = db.query(models.BenefitPlan).filter_by(id=plan_id, case_id=case.id, role="existing").first()
    if not plan:
        raise HTTPException(status_code=404, detail="Existing-role benefit plan not found on this case")

    if payload.quoted_plan_id is not None:
        quoted_plan = db.query(models.BenefitPlan).filter_by(
            id=payload.quoted_plan_id, case_id=case.id, role="quoted"
        ).first()
        if not quoted_plan:
            raise HTTPException(status_code=404, detail="Quoted benefit plan not found on this case")

    plan.matched_quote_plan_id = payload.quoted_plan_id
    db.commit()
    db.refresh(plan)
    return plan


@router.put("/{case_id}/benefits/{plan_id}/summary", response_model=schemas.BenefitPlanOut)
def update_benefit_plan_summary(
    case_id: int, plan_id: int, payload: schemas.BenefitSummaryUpdate, db: Session = Depends(get_db)
):
    """Manual corrections to a benefit plan's standard 12-field summary -
    mainly for OCR-extracted plans (scanned table-of-benefits PDFs, see
    app/ingestion/benefits_ocr.py), where automatic extraction is
    best-effort and rarely used often enough to be worth chasing every
    insurer's own label wording. Only known STANDARD_FIELDS keys are
    accepted; an empty/blank value clears that field back to unresolved
    rather than storing blank text as if it came from the source document.
    """
    case = _get_case_or_404(db, case_id)
    plan = db.query(models.BenefitPlan).filter_by(id=plan_id, case_id=case.id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Benefit plan not found on this case")

    summary = dict(plan.standard_summary or {})
    for field, value in payload.fields.items():
        if field not in STANDARD_FIELDS:
            continue
        if value is None or not value.strip():
            summary.pop(field, None)
        else:
            summary[field] = value.strip()
    plan.standard_summary = summary
    if payload.plan_name is not None and payload.plan_name.strip():
        plan.plan_name = payload.plan_name.strip()
    if payload.category is not None:
        plan.category = payload.category.strip() or None
    if payload.nb_product is not None:
        plan.nb_product = payload.nb_product.strip() or None
    if payload.nb_network is not None:
        plan.nb_network = payload.nb_network.strip() or None
    if payload.nb_tpa is not None:
        plan.nb_tpa = payload.nb_tpa.strip() or None
    db.commit()
    db.refresh(plan)
    maybe_auto_requote(case.id, db)
    return plan


@router.post("/{case_id}/benefits/manual", response_model=schemas.BenefitPlanOut)
def add_manual_benefit_plan(case_id: int, payload: schemas.ManualBenefitPlanCreate, db: Session = Depends(get_db)):
    """Adds a brand-new, blank existing-role benefit plan with every
    standard-summary field unresolved - for a scanned table of benefits
    OCR couldn't usefully read at all (see app/ingestion/benefits_ocr.py),
    where PUT .../summary's field-by-field corrections aren't enough to
    start from because there's nothing worth correcting. The underwriter
    fills it in entirely by hand via that same edit flow afterward.
    """
    case = _get_case_or_404(db, case_id)
    plan = models.BenefitPlan(
        case_id=case.id,
        role="existing",
        plan_name=payload.plan_name.strip() or "New plan",
        category=(payload.category or "").strip() or None,
        source_format="manual",
        standard_summary={},
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    maybe_auto_requote(case.id, db)
    return plan


@router.post("/{case_id}/benefits/{plan_id}/duplicate", response_model=schemas.BenefitPlanOut)
def duplicate_benefit_plan(case_id: int, plan_id: int, db: Session = Depends(get_db)):
    """Copies an existing-role benefit plan's summary fields and New
    Business picks into a brand-new category - for when most categories on
    a case share the same benefits and only a few fields differ, so the
    underwriter can duplicate a filled-in category and correct just the
    differences instead of re-entering everything from scratch.
    """
    case = _get_case_or_404(db, case_id)
    source = db.query(models.BenefitPlan).filter_by(id=plan_id, case_id=case.id, role="existing").first()
    if not source:
        raise HTTPException(status_code=404, detail="Existing-role benefit plan not found on this case")

    used_letters = {
        (p.category or "").strip().upper()
        for p in db.query(models.BenefitPlan).filter_by(case_id=case.id, role="existing").all()
    }
    next_letter = next((c for c in string.ascii_uppercase if c not in used_letters), None)

    plan = models.BenefitPlan(
        case_id=case.id,
        role="existing",
        plan_name=f"Category {next_letter}" if next_letter else f"Copy of {source.plan_name}",
        category=next_letter,
        source_format=source.source_format,
        standard_summary=dict(source.standard_summary or {}),
        nb_product=source.nb_product,
        nb_network=source.nb_network,
        nb_tpa=source.nb_tpa,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    maybe_auto_requote(case.id, db)
    return plan


@router.delete("/{case_id}/benefits/{plan_id}", status_code=204)
def delete_benefit_plan(case_id: int, plan_id: int, db: Session = Depends(get_db)):
    """Removes a single existing-role benefit plan/category - the
    companion to POST .../benefits/manual, for undoing an accidentally
    added or duplicate category. Quoted-role plans (from a HealthCross
    quote upload) aren't deletable here - re-upload the quote instead, so
    premium data always comes from a real quote file, never a stray
    manual delete.
    """
    case = _get_case_or_404(db, case_id)
    plan = db.query(models.BenefitPlan).filter_by(id=plan_id, case_id=case.id, role="existing").first()
    if not plan:
        raise HTTPException(status_code=404, detail="Existing-role benefit plan not found on this case")
    db.delete(plan)
    db.commit()


@router.post("/{case_id}/claims", response_model=Union[List[schemas.ClaimsRecordOut], schemas.ClaimsReportOut])
def upload_claims(case_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    is_pdf = file.filename.lower().endswith(".pdf")

    if is_pdf:
        try:
            parsed = parse_claims_report(file.file, file.filename)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not parse claims report: {exc}")

        # parse_claims_report also returns intermediate fields (e.g. the
        # opening/closing gender split) that ClaimsReport doesn't store as
        # its own columns - opening_members/closing_members already carry
        # the totals that matter downstream.
        report_fields = {k: v for k, v in parsed.items() if k in models.ClaimsReport.__table__.columns.keys()}

        # Multiple years' reports can coexist for the same case (see
        # GET /claims-reports and /claims-report-comparison) - a renewing
        # group's DHA report from a prior policy year is real history, not
        # a stale duplicate of this year's, so it isn't wiped out just
        # because a new one came in. Re-uploading a correction for the
        # SAME report period (e.g. a re-issued version of this year's
        # report) still replaces just that one row rather than piling up
        # near-duplicates.
        new_period_start = report_fields.get("report_period_start")
        if new_period_start is not None:
            db.query(models.ClaimsReport).filter_by(
                case_id=case.id, report_period_start=new_period_start
            ).delete()
            # A report whose own period couldn't be parsed on an earlier
            # upload (e.g. a date format the parser didn't yet recognize)
            # can't be matched by period - but it's almost certainly a
            # stale duplicate of THIS one now that a re-upload parsed
            # successfully, so replace it too, UNLESS this case already
            # has multiple real report-years on file: with 2+ existing
            # reports a null-period row could legitimately be one of
            # those years' own report that just failed to parse, and
            # deleting it then would lose real history rather than a
            # duplicate.
            existing_report_count = db.query(models.ClaimsReport).filter_by(case_id=case.id).count()
            if existing_report_count < 2:
                db.query(models.ClaimsReport).filter_by(
                    case_id=case.id, report_period_start=None
                ).delete()
        report = models.ClaimsReport(case_id=case.id, **report_fields)
        db.add(report)
        db.commit()
        db.refresh(report)
        return report

    try:
        parsed = parse_claims(file.file, file.filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse claims file: {exc}")

    # Replace, not accumulate - see the census upload for why.
    db.query(models.ClaimsRecord).filter_by(case_id=case.id).delete()

    records = [models.ClaimsRecord(case_id=case.id, **row) for row in parsed]
    db.add_all(records)
    db.commit()
    for record in records:
        db.refresh(record)
    return records


@router.post("/{case_id}/claims-ledger", response_model=List[schemas.ClaimsLedgerEntryOut])
def upload_claims_ledger(case_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Uploads a raw per-claim-line claims ledger (e.g. the "ServicePlan"
    format) for an existing-business renewal - see
    app/scoring/rules/claims_ledger_analysis.py and renewal_rating.py for
    what this powers. Distinct from /claims, which handles a simpler
    generic claims spreadsheet or a pre-aggregated DHA-style report.
    """
    case = _get_case_or_404(db, case_id)
    try:
        parsed = parse_claims_ledger(file.file, file.filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse claims ledger: {exc}")
    if not parsed:
        raise HTTPException(status_code=400, detail="No claims ledger rows found in file")

    # Replace, not accumulate - see the census upload for why.
    db.query(models.ClaimsLedgerEntry).filter_by(case_id=case.id).delete()

    entries = [models.ClaimsLedgerEntry(case_id=case.id, **row) for row in parsed]
    db.add_all(entries)
    db.commit()
    for entry in entries:
        db.refresh(entry)
    return entries


@router.post("/{case_id}/plan-details", response_model=schemas.PlanDetailsOut)
def upload_plan_details(case_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    try:
        extracted = parse_plan_details(file.file, file.filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse plan details: {exc}")

    for field, value in extracted.items():
        setattr(case, field, value)
    db.commit()
    db.refresh(case)

    return schemas.PlanDetailsOut(
        broker_name=case.broker_name,
        industry=case.industry,
        existing_insurer=case.existing_insurer,
        years_with_existing_insurer=case.years_with_existing_insurer,
        target_premium=case.target_premium,
        claims_available=case.claims_available,
        renewal_date=case.renewal_date,
        region=case.region,
        updated_fields=sorted(extracted.keys()),
    )
