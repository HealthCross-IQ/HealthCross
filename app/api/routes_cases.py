from typing import List, Union

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
from app.reference.benefit_category_mapping import build_standard_summary_from_rows, to_case_benefit_plan_fields

router = APIRouter(prefix="/cases", tags=["cases"])


def _get_case_or_404(db: Session, case_id: int) -> models.Case:
    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


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
        return existing

    # A re-upload replaces the case's census entirely rather than piling on
    # top of a previous one - otherwise re-uploading the same file (e.g.
    # after fixing a typo) silently multiplies the member count.
    db.query(models.CensusRecord).filter_by(case_id=case.id).delete()

    records = [models.CensusRecord(case_id=case.id, **row) for row in parsed]
    db.add_all(records)
    db.commit()
    for record in records:
        db.refresh(record)
    return records


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

    if mode == "append":
        # One file per category (some insurers ship each category's table of
        # benefits as its own separate document) - only replace a plan that
        # shares this file's category letter, so uploading category B's file
        # doesn't wipe out category A's plan uploaded a moment ago. A plan
        # with no detected category can't be matched this way, so it's just
        # added alongside whatever's already there.
        categories_in_upload = {p.category for p in plans if p.category}
        if categories_in_upload:
            db.query(models.BenefitPlan).filter(
                models.BenefitPlan.case_id == case.id,
                models.BenefitPlan.role == "existing",
                models.BenefitPlan.category.in_(categories_in_upload),
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
        # near-duplicates. A report whose own period couldn't be parsed
        # can't be matched this way, so it's simply added rather than
        # risking a delete that removes a different, unrelated year.
        new_period_start = report_fields.get("report_period_start")
        if new_period_start is not None:
            db.query(models.ClaimsReport).filter_by(
                case_id=case.id, report_period_start=new_period_start
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
