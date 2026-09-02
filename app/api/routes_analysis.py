import calendar
import io
from collections import Counter
import re
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.api.routes_new_business_rating import maybe_auto_requote
from app.database import get_db
from app.ingestion.premium_summary_rate_card import lookup_rate, parse_premium_summary_rate_card
from app.models import db_models as models
from app.models import schemas
from app.reference.diagnosis_classification import classify_diagnosis_group, flag_diagnosis_group
from app.reference.emirate_regions import region_for_emirate
from app.scoring.rules.benefits_comparison import compare_benefit_summaries, compare_benefit_value
from app.scoring.rules.benefits_summary import NOT_SPECIFIED, build_standard_benefit_summary
from app.scoring.rules.census_summary import census_demographic_summary
from app.scoring.rules.claims_ledger_analysis import (
    category_burning_cost,
    full_months_only,
    monthly_final_amount,
    top_diagnoses_by_final_amount,
    top_patients_by_final_amount,
    top_providers_by_final_amount,
)
from app.scoring.rules.claims_projection import ClaimsProjectionAssumptions, project_annual_claims
from app.scoring.rules.exposed_risk_population import monthly_exposed_risk_population
from app.scoring.rules.portfolio_analysis import (
    DEFAULT_LARGE_CLAIM_THRESHOLDS,
    _is_paid_claim_status,
    claims_above_thresholds,
    recurring_high_cost_members,
    top_claims_by_value,
    top_members_by_total_claims,
    utilization_by_encounter_type,
)
from app.scoring.rules.renewal_bench_metrics import (
    case_claim_kpis,
    census_change_pct_from_snapshots,
    existing_premium_breakdown,
    renewal_drivers,
)
from app.scoring.rules.renewal_rating import (
    DEFAULT_CREDIBILITY_PCT,
    DEFAULT_IBNR_PCT,
    MIN_CREDIBLE_CASE_COUNT,
    RenewalRatingAssumptions,
    _median,
    benchmark_case_against_book,
    calculate_renewal_rating_two_methods,
    pricing_input_problems,
    renewal_from_loss_ratio,
    renewal_loading_problems,
    case_loading_pct,
    dynamic_ibnr_incurred_claims,
    premium_component_breakdown,
)
from app.book import repository as book_repo
from app.api.census_ages import renewal_term_looks_wrong, stale_age_basis
from app.api.case_loading import renewal_loading
from app.book import analysis as book_analysis

router = APIRouter(prefix="/cases", tags=["analysis"])


def _get_case_or_404(db: Session, case_id: int) -> models.Case:
    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _standard_burning_cost_per_member(report: models.ClaimsReport) -> Optional[float]:
    """The same standard formula the single-year claims projection uses
    (see project_annual_claims): the first 6 full months' average paid
    claims, annualized, grossed up for IBNR, then divided by this report's
    own average (opening+closing)/2 population - NOT total_paid divided
    by average members directly, which understates the true annual
    run-rate for a report whose own period doesn't cover a full 12
    months. Returns None if there isn't enough data (fewer than 6 full
    months, or missing opening/closing member counts) to compute it.
    """
    if not report.opening_members or not report.closing_members:
        return None
    full_months = [m["paid"] for m in (report.monthly_paid or []) if not m.get("partial")]
    if len(full_months) < 6:
        return None
    assumptions = ClaimsProjectionAssumptions()
    avg_month = sum(full_months[:6]) / 6
    annualized = avg_month * 12
    with_ibnr = annualized * (1 + assumptions.ibnr_pct)
    avg_report_members = (report.opening_members + report.closing_members) / 2
    return round(with_ibnr / avg_report_members, 2) if avg_report_members else None


def _resolve_claims_report(db: Session, case_id: int, report_id: Optional[int] = None) -> models.ClaimsReport:
    """A case can have more than one claims report uploaded (one per
    policy year - see GET /claims-reports and /claims-report-comparison).
    Every view that used to only ever look at "the" report now takes an
    optional report_id to look at a SPECIFIC year instead; omitting it
    keeps the old behavior of defaulting to the latest one, ordered by
    the report's own period (falling back to upload order for a report
    whose own period date couldn't be parsed).
    """
    if report_id is not None:
        report = db.query(models.ClaimsReport).filter_by(case_id=case_id, id=report_id).first()
        if not report:
            raise HTTPException(status_code=404, detail="Claims report not found for this case")
        return report
    report = (
        db.query(models.ClaimsReport)
        .filter_by(case_id=case_id)
        .order_by(models.ClaimsReport.report_period_start.desc(), models.ClaimsReport.created_at.desc())
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="No claims report uploaded for this case")
    return report


@router.get("/{case_id}/claims-report", response_model=schemas.ClaimsReportOut)
def get_claims_report(case_id: int, report_id: Optional[int] = None, db: Session = Depends(get_db)):
    _get_case_or_404(db, case_id)
    return _resolve_claims_report(db, case_id, report_id)


@router.get("/{case_id}/claims-reports", response_model=List[schemas.ClaimsReportOut])
def list_claims_reports(case_id: int, db: Session = Depends(get_db)):
    """Every claims report uploaded for this case (one per report period,
    e.g. one per policy year for a renewing group) - lets a broker compare
    multiple years side by side (see /claims-report-comparison) or pick a
    specific one to view via the report_id param on the other claims
    endpoints, rather than only ever seeing the latest upload. Sorted
    oldest to newest by the report's own period_start, falling back to
    upload order for a report whose own period date couldn't be parsed.
    """
    _get_case_or_404(db, case_id)
    return (
        db.query(models.ClaimsReport)
        .filter_by(case_id=case_id)
        .order_by(models.ClaimsReport.report_period_start.asc(), models.ClaimsReport.created_at.asc())
        .all()
    )


@router.delete("/{case_id}/claims-reports/{report_id}", status_code=204)
def delete_claims_report(case_id: int, report_id: int, db: Session = Depends(get_db)):
    """Removes one uploaded claims report. Mainly for a stale duplicate
    left behind when an earlier upload's report period couldn't be
    parsed (POST /cases/{id}/claims only replaces a same-period report
    on re-upload - it can't match one whose own period is unknown,
    so re-uploading a corrected file adds a second row instead of
    replacing the first) - or a report uploaded to the wrong case/year
    by mistake.
    """
    _get_case_or_404(db, case_id)
    report = db.query(models.ClaimsReport).filter_by(id=report_id, case_id=case_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Claims report not found for this case")
    db.delete(report)
    db.commit()


@router.get("/{case_id}/claims-report-comparison")
def get_claims_report_comparison(case_id: int, db: Session = Depends(get_db)):
    """Year-over-year comparison across every claims report uploaded for
    this case - lets a broker see how a renewing group's claims experience
    has trended across policy years (total paid, population, top
    diagnoses/providers) rather than only ever seeing the latest year.
    """
    case = _get_case_or_404(db, case_id)
    reports = (
        db.query(models.ClaimsReport)
        .filter_by(case_id=case_id)
        .order_by(models.ClaimsReport.report_period_start.asc(), models.ClaimsReport.created_at.asc())
        .all()
    )
    if not reports:
        raise HTTPException(status_code=404, detail="No claims reports uploaded for this case")

    rows = []
    for report in reports:
        if report.report_period_start:
            year = report.report_period_start.year
        elif report.report_production_date:
            year = report.report_production_date.year
        else:
            year = None

        burning_cost = _standard_burning_cost_per_member(report)

        rows.append(
            {
                "report_id": report.id,
                "year": year,
                "policy_effective_date": report.policy_effective_date,
                "policy_expiry_date": report.policy_expiry_date,
                "report_period_start": report.report_period_start,
                "report_period_end": report.report_period_end,
                "total_paid": report.total_paid,
                "incurred_not_reported": report.incurred_not_reported,
                "opening_members": report.opening_members,
                "closing_members": report.closing_members,
                "burning_cost_per_member": burning_cost,
                "treatment_type_breakdown": report.treatment_type_breakdown,
                "top_diagnoses": sorted(report.diagnosis_breakdown or [], key=lambda d: d["value"], reverse=True)[:5],
                "top_providers": sorted(report.provider_breakdown or [], key=lambda p: p["value"], reverse=True)[:5],
            }
        )

    # % change vs. the immediately preceding row (chronologically) - e.g.
    # "2025 from 2024" - blank for the first year, since there's nothing
    # earlier to compare it against.
    for previous, current in zip(rows, rows[1:]):
        if previous["total_paid"]:
            current["total_paid_pct_change"] = round(
                (current["total_paid"] - previous["total_paid"]) / previous["total_paid"] * 100, 2
            )
        else:
            current["total_paid_pct_change"] = None
        if previous["burning_cost_per_member"]:
            current["burning_cost_pct_change"] = round(
                (current["burning_cost_per_member"] - previous["burning_cost_per_member"])
                / previous["burning_cost_per_member"]
                * 100,
                2,
            )
        else:
            current["burning_cost_pct_change"] = None
    if rows:
        rows[0]["total_paid_pct_change"] = None
        rows[0]["burning_cost_pct_change"] = None

    return {"case_id": case.id, "reports": rows}


@router.get("/{case_id}/claims-projection", response_model=schemas.ClaimsProjectionOut)
def get_claims_projection(
    case_id: int,
    db: Session = Depends(get_db),
    report_id: Optional[int] = None,
    credibility_pct: Optional[float] = None,
    inflation_pct: Optional[float] = None,
    ibnr_pct: Optional[float] = None,
    loading_pct: Optional[float] = None,
):
    """Runs the standing burning-cost formula (see
    app/scoring/rules/claims_projection.py) against this case's latest
    claims report (or a specific year's, via report_id - see
    /claims-reports) and current census member count. Credibility (and
    the other assumptions) can be overridden per call via query params
    since credibility in particular is negotiated per insurer/case
    rather than fixed - the defaults on ClaimsProjectionAssumptions still
    apply when a param is omitted.
    """
    case = _get_case_or_404(db, case_id)
    report = _resolve_claims_report(db, case_id, report_id)

    census_count = len(case.census_records)
    if not census_count:
        raise HTTPException(status_code=400, detail="Case has no census data to size the projection against")

    monthly = report.monthly_paid or []
    full_months = [m["paid"] for m in monthly if not m.get("partial")]
    if len(full_months) < 6:
        raise HTTPException(
            status_code=400,
            detail=f"Need at least 6 full months of claims data, found {len(full_months)}",
        )
    six_months = full_months[:6]
    month_labels = [f"{m['month']} {m['year']}" for m in monthly if not m.get("partial")][:6]

    if not report.opening_members or not report.closing_members:
        raise HTTPException(status_code=400, detail="Claims report is missing opening/closing member counts")

    defaults = ClaimsProjectionAssumptions()
    assumptions = ClaimsProjectionAssumptions(
        credibility_pct=credibility_pct if credibility_pct is not None else defaults.credibility_pct,
        inflation_pct=inflation_pct if inflation_pct is not None else defaults.inflation_pct,
        ibnr_pct=ibnr_pct if ibnr_pct is not None else defaults.ibnr_pct,
        loading_pct=loading_pct if loading_pct is not None else defaults.loading_pct,
    )

    result = project_annual_claims(
        six_month_paid_claims=six_months,
        opening_members=report.opening_members,
        closing_members=report.closing_members,
        current_census_members=census_count,
        assumptions=assumptions,
    )
    result["months_used"] = month_labels
    return result


@router.get("/{case_id}/diagnosis-exposure", response_model=List[schemas.DiagnosisExposureRow])
def get_diagnosis_exposure(case_id: int, report_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Applies the standing chronic/high-exposure classification (see
    app/reference/diagnosis_classification.py) to this case's latest
    claims report's diagnosis breakdown (or a specific year's, via
    report_id - see /claims-reports).
    """
    _get_case_or_404(db, case_id)
    report = _resolve_claims_report(db, case_id, report_id)

    rows = []
    for entry in report.diagnosis_breakdown or []:
        classification = classify_diagnosis_group(entry["label"])
        flags = flag_diagnosis_group(entry["value"], entry["count"], entry["ip_value"], entry["ip_count"])
        rows.append(
            {
                "label": entry["label"],
                "value": entry["value"],
                "count": entry["count"],
                "ip_value": entry["ip_value"],
                "ip_count": entry["ip_count"],
                "avg_per_claim": flags["avg_per_claim"],
                "ip_avg_per_claim": flags["ip_avg_per_claim"],
                "classification": classification["classification"],
                "high_exposure": classification["high_exposure"],
                "note": classification["note"],
                "flags": flags["flags"],
            }
        )
    rows.sort(key=lambda r: r["value"], reverse=True)
    return rows


def _with_percentages(rows: List[dict], value_key: str, total: float) -> List[dict]:
    result = []
    for row in rows:
        copy = dict(row)
        copy["pct_of_total"] = round(100 * copy[value_key] / total, 1) if total else None
        result.append(copy)
    return result


@router.get("/{case_id}/claims-report-breakdown")
def get_claims_report_breakdown(case_id: int, report_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Top 10 providers, IP/OP/Pharmacy/Dental/Optical/Not-Yet-Classified
    treatment-type split (see app/ingestion/claims_report.py - format 1's
    row 14 partitions the whole report by billing method, and each of its
    two rows carries the same treatment-type columns as the diagnosis
    breakdown, so summing them gives a complete, grand-total-accurate
    split), and the Maternity diagnosis grouping's own amount, each as a
    % of the report's total_paid. Where a 6-month claims projection can
    also be run for this case (see /claims-projection), each figure is
    additionally annualized by applying its % share to the projected
    final_projected_claims.

    Defaults to the latest uploaded report; pass report_id (see
    /claims-reports) to view a specific year instead.

    Only available where the source report is DHA Mandated Format
    (format 1) - format 2's row 13 only splits In Network/Out of Network,
    not by treatment type, so treatment_type_breakdown comes back empty
    for those reports (top providers and Maternity still work either way).
    """
    case = _get_case_or_404(db, case_id)
    report = _resolve_claims_report(db, case_id, report_id)
    total = report.total_paid or 0.0

    top_providers = sorted(report.provider_breakdown or [], key=lambda p: p["value"], reverse=True)[:10]
    top_providers = _with_percentages(top_providers, "value", total)

    treatment_type_breakdown = _with_percentages(report.treatment_type_breakdown or [], "value", total)

    maternity = None
    for entry in report.diagnosis_breakdown or []:
        if " ".join(entry["label"].split()).lower() == "pregnancy, childbirth and the puerperium":
            maternity = dict(entry)
            maternity["pct_of_total"] = round(100 * entry["value"] / total, 1) if total else None
            break

    # Row 8's own Employee/Spouse/Dependents split, each column shown as a
    # % of that COLUMN's own total (not the report's grand total) - e.g.
    # what share of all In-Patient claims Spouse accounts for - since
    # that's the comparison a relation-by-relation breakdown is actually
    # for. "Dependents" is everyone who isn't the Employee or Spouse (the
    # census doesn't carry a matching "Dependents" relation value of its
    # own - only employee/spouse/child/other - so those two are folded
    # together here to line up with the report's own three-way split).
    member_type_columns = ["in_patient", "out_patient", "pharmacy", "dental", "optical", "not_yet_classified", "total"]
    member_type_rows = report.claims_by_member_type_value or []
    claims_by_member_type = None
    if member_type_rows:
        column_totals = {col: sum(r[col] for r in member_type_rows) for col in member_type_columns}
        relation_member_counts = {"employee": 0, "spouse": 0, "dependents": 0}
        for c in case.census_records:
            rel = (c.relation or "").strip().lower()
            if rel == "employee":
                relation_member_counts["employee"] += 1
            elif rel == "spouse":
                relation_member_counts["spouse"] += 1
            else:
                relation_member_counts["dependents"] += 1

        claims_by_member_type = []
        for row in member_type_rows:
            entry = dict(row)
            entry["pct_of_column"] = {
                col: (round(100 * row[col] / column_totals[col], 1) if column_totals[col] else None)
                for col in member_type_columns
            }
            entry["member_count"] = relation_member_counts.get(row["relation"].strip().lower())
            claims_by_member_type.append(entry)

    result = {
        "total_paid": report.total_paid,
        "top_providers": top_providers,
        "treatment_type_breakdown": treatment_type_breakdown,
        "maternity": maternity,
        "claims_by_member_type": claims_by_member_type,
    }

    # Annualize each category by its % share of the projected annual
    # claims figure, when there's enough data to run that projection at
    # all (same preconditions as /claims-projection: 6 full months, a
    # census, and opening/closing member counts).
    census_count = len(case.census_records)
    monthly = report.monthly_paid or []
    full_months = [m["paid"] for m in monthly if not m.get("partial")]
    if census_count and len(full_months) >= 6 and report.opening_members and report.closing_members:
        projection = project_annual_claims(
            six_month_paid_claims=full_months[:6],
            opening_members=report.opening_members,
            closing_members=report.closing_members,
            current_census_members=census_count,
        )
        final_projected_claims = projection["final_projected_claims"]
        for row in treatment_type_breakdown:
            row["annualized"] = round(final_projected_claims * row["value"] / total, 2) if total else None
        if maternity:
            maternity["annualized"] = round(final_projected_claims * maternity["value"] / total, 2) if total else None
        if claims_by_member_type:
            # Each relation's own share of the report's total claims,
            # projected for the whole year the same way as everything
            # else, then divided by that relation's own member count -
            # the burning cost per member, by relation.
            grand_total = column_totals["total"]
            for entry in claims_by_member_type:
                annualized_total = (
                    round(final_projected_claims * entry["total"] / grand_total, 2) if grand_total else None
                )
                entry["annualized_total"] = annualized_total
                member_count = entry["member_count"]
                entry["burning_cost_per_member"] = (
                    round(annualized_total / member_count, 2)
                    if annualized_total is not None and member_count
                    else None
                )
        result["final_projected_claims"] = final_projected_claims

    return result


@router.get("/{case_id}/census-summary")
def get_census_summary(case_id: int, db: Session = Depends(get_db)):
    """Plain demographic breakdown of the uploaded census - age bands,
    gender, marital status, relation, and nationality-zone mix - as counts
    and percentages (see app/scoring/rules/census_summary.py). This is the
    underwriter-facing "what does this group look like" view, distinct from
    the risk-scoring engine's demographic multiplier.
    """
    case = _get_case_or_404(db, case_id)
    if not case.census_records:
        raise HTTPException(status_code=404, detail="No census uploaded for this case")

    census = [
        {
            "age": c.age,
            "gender": c.gender,
            "marital_status": c.marital_status,
            "relation": c.relation,
            "nationality_zone": c.nationality_zone,
            "nationality": c.nationality,
        }
        for c in case.census_records
    ]
    return census_demographic_summary(census)


@router.get("/{case_id}/monthly-erp")
def get_monthly_erp(case_id: int, db: Session = Depends(get_db)):
    """Monthly Exposed Risk Population (see
    app/scoring/rules/exposed_risk_population.py) - the actuarial
    per-member-per-month exposure figure for each calendar month of the
    scheme's policy term, accounting for members who joined late or left
    early rather than a flat headcount. Requires the uploaded census to
    carry policy_start_date/policy_end_date (the scheme's fixed term).
    """
    case = _get_case_or_404(db, case_id)
    if not case.census_records:
        raise HTTPException(status_code=404, detail="No census uploaded for this case")

    policy_starts = {c.policy_start_date for c in case.census_records if c.policy_start_date}
    policy_ends = {c.policy_end_date for c in case.census_records if c.policy_end_date}
    if not policy_starts or not policy_ends:
        raise HTTPException(
            status_code=400,
            detail="Census has no policy_start_date/policy_end_date (scheme term) to compute monthly ERP against",
        )

    census = [
        {"member_start_date": c.member_start_date, "member_end_date": c.member_end_date}
        for c in case.census_records
    ]
    return {
        "policy_start_date": min(policy_starts),
        "policy_end_date": max(policy_ends),
        "monthly_erp": monthly_exposed_risk_population(census, min(policy_starts), max(policy_ends)),
    }


#: What an OCR'd plan shows for a field no pattern matched. Deliberately
#: not "Not Covered": the difference between "the document excludes this"
#: and "we could not read this" is the difference between a priced
#: exclusion and an unread page.
OCR_NOT_FOUND = "Not found by OCR - check the raw OCR text below"


def _benefit_summary(plan: models.BenefitPlan) -> dict:
    # None (a plan predating the standard_summary column) falls back to its
    # older per-column fields; an empty dict {} is a deliberate, real state
    # (e.g. a freshly added manual plan - see POST .../benefits/manual)
    # meaning every field is genuinely unresolved, not a signal to fall
    # back to those older columns.
    if plan.standard_summary is not None:
        source = plan.standard_summary
    else:
        source = {
            "network": plan.network_type,
            "annual_limit": f"USD {plan.annual_limit:,.0f}" if plan.annual_limit else None,
            "maternity_limit": f"USD {plan.maternity_limit:,.0f}" if plan.maternity_limit else None,
            "dental": "Covered" if plan.dental_covered else "Not covered",
            "optical": "Covered" if plan.optical_covered else "Not covered",
            "pre_existing_chronic_limit": "Covered" if plan.pre_existing_covered else "Not covered",
        }
    # A field OCR found no value for is not a field the document excluded.
    # Rendering it as "Not Covered" - which this did - stated a benefit
    # decision the source never made, and a document whose labels the
    # patterns didn't recognise came back as a plan covering nothing at
    # all. Say what actually happened instead, and point at the raw OCR
    # text that is attached to the same plan for exactly this purpose.
    not_specified_text = OCR_NOT_FOUND if plan.source_format == "pdf-ocr" else NOT_SPECIFIED
    return build_standard_benefit_summary(source, not_specified_text=not_specified_text)


@router.get("/{case_id}/benefits-summary")
def get_benefits_summary(case_id: int, db: Session = Depends(get_db)):
    """Renders every uploaded EXISTING/incumbent benefit plan/tier for this
    case in the fixed 10-field standard format (see
    app/scoring/rules/benefits_summary.py). A quoted plan uploaded via
    /quote is not included here - see /benefits-comparison.
    """
    case = _get_case_or_404(db, case_id)
    existing_plans = [p for p in case.benefit_plans if p.role == "existing"]
    if not existing_plans:
        raise HTTPException(status_code=404, detail="No table of benefits uploaded for this case")

    return [
        {
            "id": plan.id,
            "plan_name": plan.plan_name,
            "summary": _benefit_summary(plan),
            "category": plan.category,
            "nb_product": plan.nb_product,
            "nb_network": plan.nb_network,
            "nb_tpa": plan.nb_tpa,
            "source_format": plan.source_format,
        }
        for plan in existing_plans
    ]


@router.get("/{case_id}/premium-by-category")
def get_premium_by_category(case_id: int, db: Session = Depends(get_db)):
    """Per-category premium breakdown for the latest uploaded quote (see
    POST /cases/{id}/quote) - members, network, gross premium, and premium
    per member for each category, plus a blended total across categories.
    """
    case = _get_case_or_404(db, case_id)
    quoted_plans = [p for p in case.benefit_plans if p.role == "quoted"]
    if not quoted_plans:
        raise HTTPException(status_code=404, detail="No quote uploaded for this case")

    categories = []
    for plan in quoted_plans:
        premium_per_member = (
            round(plan.gross_premium / plan.member_count, 2)
            if plan.gross_premium is not None and plan.member_count
            else None
        )
        categories.append(
            {
                "category": plan.category,
                "plan_name": plan.plan_name,
                "network": plan.network_type,
                "member_count": plan.member_count,
                "gross_premium": plan.gross_premium,
                "premium_per_member": premium_per_member,
            }
        )

    total_members = sum(c["member_count"] or 0 for c in categories)
    total_premium = sum(c["gross_premium"] or 0 for c in categories)
    blended_premium_per_member = round(total_premium / total_members, 2) if total_members else None

    return {
        "categories": categories,
        "total_members": total_members,
        "total_gross_premium": total_premium,
        "blended_premium_per_member": blended_premium_per_member,
    }


def _resolve_benefit_plan_pairs(
    existing_plans: List[models.BenefitPlan], quoted_plans: List[models.BenefitPlan]
) -> List[tuple]:
    """Returns a list of (existing_plan, quoted_plan, reused) rows for the
    comparison. A single existing plan describing every quoted category
    (the common case: one incumbent table of benefits, several priced
    categories - or a scanned/OCR'd existing plan, which only ever
    produces ONE combined entry regardless of how many categories the
    source document actually has, see app/ingestion/benefits_ocr.py)
    reuses that one plan against every quoted category, same as always.

    With more than one existing plan, position alone can't be trusted -
    neither side's category naming is guaranteed to match (e.g. an
    existing plan's "Bronze"/"Silver"/"Gold" vs a quote's "CAT A"/"CAT
    B") - so each existing plan is paired via, in order: an explicit
    manual mapping (see PUT .../benefits/{plan_id}/match), then an
    automatic category-letter/plan-name match (_match_quoted_plan) among
    quoted plans not already claimed by an earlier existing plan. An
    existing plan that resolves to nothing is left out rather than forced
    into a misleading pairing; any quoted plans still left over once every
    existing plan has been considered fall back to the last existing plan,
    same as the single-plan case, with `reused=True` so the UI can flag it.
    """
    if len(existing_plans) <= 1:
        existing_plan = existing_plans[0]
        return [(existing_plan, quoted_plan, i >= 1) for i, quoted_plan in enumerate(quoted_plans)]

    quoted_by_id = {p.id: p for p in quoted_plans}
    remaining_quoted = list(quoted_plans)
    pairs = []
    unresolved = []
    for existing_plan in existing_plans:
        matched = None
        if existing_plan.matched_quote_plan_id in quoted_by_id:
            candidate = quoted_by_id[existing_plan.matched_quote_plan_id]
            if candidate in remaining_quoted:
                matched = candidate
        if matched is None:
            matched = _match_quoted_plan(existing_plan.category or existing_plan.plan_name, remaining_quoted)
        if matched is not None:
            pairs.append((existing_plan, matched, False))
            remaining_quoted.remove(matched)
        else:
            unresolved.append(existing_plan)

    for existing_plan in unresolved:
        if remaining_quoted:
            pairs.append((existing_plan, remaining_quoted.pop(0), False))

    for quoted_plan in remaining_quoted:
        pairs.append((existing_plans[-1], quoted_plan, True))

    order = {id(p): i for i, p in enumerate(existing_plans)}
    pairs.sort(key=lambda pair: order[id(pair[0])])
    return pairs


@router.get("/{case_id}/benefits-comparison")
def get_benefits_comparison(case_id: int, db: Session = Depends(get_db)):
    """Field-by-field comparison of the existing/incumbent plan(s) against
    the quoted plan(s) for this case (see
    app/scoring/rules/benefits_comparison.py and
    _resolve_benefit_plan_pairs above for how each side's plans are paired
    up). `existing_plan_reused` flags a row where the existing side's
    figures are also shown against another quoted category elsewhere in
    the response, so the UI can make that clear rather than imply a
    dedicated match.
    """
    case = _get_case_or_404(db, case_id)
    existing_plans = [p for p in case.benefit_plans if p.role == "existing"]
    quoted_plans = [p for p in case.benefit_plans if p.role == "quoted"]
    if not existing_plans:
        raise HTTPException(status_code=404, detail="No existing table of benefits uploaded for this case")
    if not quoted_plans:
        raise HTTPException(status_code=404, detail="No quote uploaded for this case")

    comparisons = []
    for existing_plan, quoted_plan, reused in _resolve_benefit_plan_pairs(existing_plans, quoted_plans):
        existing_summary = _benefit_summary(existing_plan)
        quoted_summary = _benefit_summary(quoted_plan)

        fields = compare_benefit_summaries(existing_summary, quoted_summary)
        fields["network"] = compare_benefit_value(existing_plan.network_type, quoted_plan.network_type, "network")

        comparisons.append(
            {
                "existing_plan_id": existing_plan.id,
                "existing_plan_name": existing_plan.plan_name,
                "existing_category": existing_plan.category,
                "quoted_plan_id": quoted_plan.id,
                "quoted_plan_name": quoted_plan.plan_name,
                "quoted_category": quoted_plan.category,
                "quoted_gross_premium": quoted_plan.gross_premium,
                "quoted_member_count": quoted_plan.member_count,
                "existing_plan_reused": reused,
                "fields": fields,
            }
        )
    return comparisons


def _ledger_entry_dicts(entries: List[models.ClaimsLedgerEntry]) -> List[dict]:
    return [
        {
            "patient_id": e.patient_id,
            "claim_id": e.claim_id,
            "final_amount": e.final_amount,
            "diagnosis_code": e.diagnosis_code,
            "diagnosis_description": e.diagnosis_description,
            "ip_op_maternity": e.ip_op_maternity,
            "date_of_treatment": e.date_of_treatment,
            "provider_name": e.provider_name,
            "medical_category": e.medical_category,
            "policy_start_date": e.policy_start_date,
            "policy_end_date": e.policy_end_date,
            "member_start_date": e.member_start_date,
            "member_end_date": e.member_end_date,
        }
        for e in entries
    ]


def _category_letter(text: Optional[str]) -> Optional[str]:
    """Recognizes only an unambiguous single-letter category (e.g. "A",
    "Category A", "Category: A") - anchored to the whole string rather than
    searching for any standalone letter anywhere, so a medical_category
    value that just happens to contain a letter isn't mistaken for one.
    """
    if not text:
        return None
    normalized = text.strip()
    if len(normalized) == 1 and normalized.isalpha():
        return normalized.upper()
    match = re.match(r"^category\s*[:\-]?\s*([a-z])$", normalized, re.IGNORECASE)
    return match.group(1).upper() if match else None


def _match_quoted_plan(category_text: str, quoted_plans: List[models.BenefitPlan]) -> Optional[models.BenefitPlan]:
    """Matches a claims ledger's free-text medical_category value (e.g.
    "Category A", "A", or a plan name) against a quoted BenefitPlan's own
    category letter, so a category-wise burning cost can be compared
    against that category's own quoted premium. Returns None (rather than
    guessing) when nothing lines up - the burning cost is still shown, just
    without a loss-ratio comparison for that row.
    """
    normalized_category = (category_text or "").strip().lower()
    letter = _category_letter(category_text)
    for plan in quoted_plans:
        if plan.category and letter and plan.category.strip().upper() == letter:
            return plan
        if plan.plan_name and plan.plan_name.strip().lower() == normalized_category:
            return plan
    return None


def _full_months_from_ledger(entries: List[models.ClaimsLedgerEntry]) -> List[dict]:
    monthly = monthly_final_amount(_ledger_entry_dicts(entries))
    policy_start = entries[0].policy_start_date
    return full_months_only(
        monthly,
        policy_start_year=policy_start.year if policy_start else None,
        policy_start_month=policy_start.month if policy_start else None,
        policy_start_day=policy_start.day if policy_start else None,
    )


@router.get("/{case_id}/claims-ledger-analysis")
def get_claims_ledger_analysis(
    case_id: int,
    db: Session = Depends(get_db),
    inflation_pct: Optional[float] = None,
    loading_pct: Optional[float] = None,
):
    """Comprehensive analysis over an uploaded claims ledger (see
    POST /cases/{id}/claims-ledger and
    app/scoring/rules/claims_ledger_analysis.py) - top 10 patients and
    diagnoses by final claims amount (diagnoses classified chronic/
    non-chronic via their ICD-10 chapter), the month-wise claims trend,
    and an expected-annual-premium figure grossed up from the average of
    the full months only. Unlike the burning-cost method in
    claims_projection.py, this does NOT rescale by a report's member count
    vs the current census - it's the same group's own experience, so no
    rescaling is needed. inflation_pct/loading_pct are overridable per
    call (defaults 7.5%/28%, same as elsewhere) since both are negotiated
    per case, not fixed.
    """
    case = _get_case_or_404(db, case_id)
    entries = case.claims_ledger_entries
    if not entries:
        raise HTTPException(status_code=404, detail="No claims ledger uploaded for this case")

    entry_dicts = _ledger_entry_dicts(entries)
    monthly = monthly_final_amount(entry_dicts)

    # Monthly Exposed Risk Population (see
    # app/scoring/rules/exposed_risk_population.py) merged in alongside the
    # month-wise claims trend, when the census carries the scheme's
    # policy_start_date/policy_end_date and members' own enrollment dates -
    # gives the real actuarial claims-per-exposed-member-per-month figure
    # instead of just the raw monthly total.
    policy_starts = {c.policy_start_date for c in case.census_records if c.policy_start_date}
    policy_ends = {c.policy_end_date for c in case.census_records if c.policy_end_date}
    if case.census_records and policy_starts and policy_ends:
        census = [
            {"member_start_date": c.member_start_date, "member_end_date": c.member_end_date}
            for c in case.census_records
        ]
        erp_by_month = {
            (r["year"], r["month"]): r["erp"]
            for r in monthly_exposed_risk_population(census, min(policy_starts), max(policy_ends))
        }
        for row in monthly:
            erp = erp_by_month.get((row["year"], row["month"]))
            row["erp"] = erp
            row["cost_per_erp_member"] = round(row["final_amount"] / erp, 2) if erp else None

    result = {
        "top_patients": top_patients_by_final_amount(entry_dicts),
        "top_diagnoses": top_diagnoses_by_final_amount(entry_dicts),
        "top_providers": top_providers_by_final_amount(entry_dicts),
        "monthly_final_amount": monthly,
    }

    full_months = _full_months_from_ledger(entries)
    result["full_months_used"] = full_months
    if full_months:
        defaults = RenewalRatingAssumptions()
        # This account's own entered loading, never the flat house default.
        # It is a renewal working - it divides by (1 - loading) to reach an
        # expected annual premium - so an assumed loading makes part of
        # that premium invented, the same as everywhere else.
        account_loading, loading_problems = renewal_loading(case)
        effective_loading = loading_pct if loading_pct is not None else account_loading
        assumptions = RenewalRatingAssumptions(
            inflation_pct=inflation_pct if inflation_pct is not None else defaults.inflation_pct,
            loading_pct=effective_loading if effective_loading is not None else defaults.loading_pct,
        )
        result["loading_pct_source"] = (
            "query override" if loading_pct is not None
            else "this account's own fee split" if account_loading is not None
            else "house default - no fee split entered on this case"
        )
        result["loading_problems"] = loading_problems or None
        avg_month = sum(m["final_amount"] for m in full_months) / len(full_months)
        annualized = avg_month * 12
        trended = annualized * (1 + assumptions.inflation_pct)
        expected_annual_premium = trended / (1 - assumptions.loading_pct)
        result.update(
            {
                "avg_month": round(avg_month, 2),
                "annualized_incurred_claims": round(annualized, 2),
                "trended_claims": round(trended, 2),
                "expected_annual_premium": round(expected_annual_premium, 2),
                "assumptions_used": {"inflation_pct": assumptions.inflation_pct, "loading_pct": assumptions.loading_pct},
            }
        )

        # Second method: the same average/annualize/trend/load formula, but
        # built from the ERP-normalized per-member rate rather than the raw
        # monthly total - corrects for a group that grew or shrank across
        # the observed months (this ledger's ERP climbs from 120 to 132
        # over its own term, so the plain monthly-total average understates
        # what the per-member rate implies for the group's actual size).
        full_month_keys = {(m["year"], m["month"]) for m in full_months}
        full_months_with_erp = [
            row for row in monthly if (row["year"], row["month"]) in full_month_keys and row.get("erp")
        ]
        if full_months_with_erp:
            avg_cost_per_erp_member = sum(row["cost_per_erp_member"] for row in full_months_with_erp) / len(full_months_with_erp)
            avg_erp = sum(row["erp"] for row in full_months_with_erp) / len(full_months_with_erp)
            annualized_pmpm = avg_cost_per_erp_member * 12 * avg_erp
            trended_pmpm = annualized_pmpm * (1 + assumptions.inflation_pct)
            expected_annual_premium_pmpm = trended_pmpm / (1 - assumptions.loading_pct)
            result.update(
                {
                    "avg_cost_per_erp_member": round(avg_cost_per_erp_member, 2),
                    "avg_erp": round(avg_erp, 2),
                    "annualized_incurred_claims_pmpm": round(annualized_pmpm, 2),
                    "trended_claims_pmpm": round(trended_pmpm, 2),
                    "expected_annual_premium_pmpm": round(expected_annual_premium_pmpm, 2),
                }
            )

        full_month_keys = [(m["year"], m["month"]) for m in full_months]
        category_rows = category_burning_cost(
            entry_dicts, full_month_keys, assumptions.inflation_pct, assumptions.loading_pct
        )
        quoted_plans = [p for p in case.benefit_plans if p.role == "quoted"]
        for row in category_rows:
            matched_plan = _match_quoted_plan(row["category"], quoted_plans) if quoted_plans else None
            if matched_plan:
                row["product"] = matched_plan.plan_name
                row["network"] = matched_plan.network_type
                row["quoted_premium"] = matched_plan.gross_premium
                row["projected_loss_ratio"] = (
                    round(row["projected_annual_claims"] / matched_plan.gross_premium, 4)
                    if matched_plan.gross_premium
                    else None
                )
            else:
                row["product"] = None
                row["network"] = None
                row["quoted_premium"] = None
                row["projected_loss_ratio"] = None
        result["category_burning_cost"] = category_rows
        result["quote_available_for_comparison"] = bool(quoted_plans)
    return result


def _account_rating_from_book(case: models.Case) -> Optional[dict]:
    """This account's renewal experience read off the Portfolio Analysis
    book - the latest membership and claims uploaded - rather than off a
    claims ledger attached to the case.

    The ledger path below is a per-case upload that goes stale the moment
    the book is refreshed, and it answers the question differently in
    three ways that all pushed the renewal ask DOWN:

      - It discards the last month in the file (see full_months_only),
        which is deliberate for averaging a part month but silently drops
        every claim in it. On NOMADA that lost AED 1,953.89 of
        outstanding, reporting 6,671.57 where the ledger held 8,625.46.
      - It annualises claims by averaging whole months and multiplying by
        twelve, asserting the rest of the year looks like the part
        observed.
      - It divides by the case's current_annual_premium, which a renewal
        opened from the book sets to the ANNUALISED premium (headcount x
        each member's full annual rate) rather than what was actually
        charged. On NOMADA that was 114,488 against a booked 90,347, and
        it read the account at 75.6% where it is 95.8%.

    So this uses the house measure instead (renewal_loss_ratio): every
    claim to the day the data covers, a 30-day IBNR tail on the active
    members' own paid run rate, over premium EARNED by those same members
    to that same day. Nothing is discarded and nothing is projected.

    Returns None when the account is not on the book, so the caller falls
    back to the ledger.
    """
    from sqlalchemy.orm import object_session


    db = object_session(case)
    if db is None or not case.company_name:
        return None

    # Deliberately the SAME call the Portfolio Loss Ratio screen makes,
    # not a second implementation of the same arithmetic. Two code paths
    # computing one account's loss ratio is how the renewal card came to
    # read 75.6% on an account the Loss Ratio screen had at 83.6%, and
    # no amount of care in a reimplementation prevents the next drift.
    #
    # The whole book, then matched here - NOT book_analysis.run_analysis's own client
    # filter, which compares the raw contract field with ==. A case named
    # "... MANAGING LLC" against a contract booked "... MANAGING LL" found
    # nothing and the card silently fell back to the stale ledger, which
    # is the same failure wearing a different hat. Matching is on the
    # resolved master client, trimmed and case-folded, exactly as
    # account_members does it.
    rows = book_analysis.account_loss_ratio_rows_for_book(db)
    if not rows:
        return None
    wanted = case.company_name.strip().casefold()
    mine = [r for r in rows if (r.get("master_client") or "").strip().casefold() == wanted]
    if not mine:
        return None

    # One row per policy period; the renewal is about the latest.
    row = max(mine, key=lambda r: r["policy_start_date"])
    if not row.get("earned_premium"):
        return None
    return {
        "source": "Portfolio Loss Ratio (the uploaded book)",
        "as_of": row["as_of"],
        "master_client": row["master_client"],
        "policy_start_date": row["policy_start_date"],
        "premium_basis": row["premium_basis"],
        "member_count": row["member_count"],
        "days": row["days"],
        "expired": row["expired"],
        "paid": row["paid"],
        "outstanding": row["outstanding"],
        "ibnr": row["ibnr"],
        "incurred_claims": row["incurred_claims"],
        "gross_premium": row["gross_premium"],
        "earned_premium": row["earned_premium"],
        "net_premium": row["net_premium"],
        "loading_pct": row["loading_pct"],
        "gross_loss_ratio": row["gross_loss_ratio"],
        "net_loss_ratio": row["net_loss_ratio"],
    }


def _annualised_expiring_premium(case: models.Case) -> dict:
    """A full year at current rates for the headcount that is renewing.

    The premium a renewal is actually quoted against. Three figures are
    in play on a renewal case and they are not interchangeable:

      - the book's GROSS premium, prorated for members who joined or
        left mid-term (NOMADA: 90,347). Right for measuring a loss
        ratio, wrong for quoting a year nobody bought a fraction of.
      - the case record's current_annual_premium, typed or seeded once
        and then left (114,488).
      - this: each renewing member's own existing annual rate, summed
        (103,486).

    Preference is the member-rate table where an underwriter has built
    it, then the book's own active members at their annual rates. Both
    are returned with the source named, because an increase quoted off
    the wrong one is wrong by exactly the ratio between them.
    """
    from sqlalchemy.orm import object_session

    from app.scoring.rules.renewal_intake import (
        account_members,
        continuing_and_leaving,
        member_annual_rate,
    )

    rated = [c.existing_annual_rate for c in case.census_records if c.existing_annual_rate]
    if rated:
        return {
            "total": round(sum(rated), 2),
            "member_count": len(rated),
            "source": "each renewing member's own existing annual rate",
        }

    db = object_session(case)
    if db is not None and case.company_name:
        account = account_members(book_repo.members(db), case.company_name, book_repo.subgroup_master_by_name(db))
        if account:
            active, _ = continuing_and_leaving(account)
            rates = [member_annual_rate(m) or 0.0 for m in active]
            if any(rates):
                return {
                    "total": round(sum(rates), 2),
                    "member_count": len(active),
                    "source": "the book's active members at their annual rates",
                }
    return {"total": None, "member_count": 0, "source": None}


def _comparable_results(db: Session, case_id: int) -> List[dict]:
    """Every OTHER case's renewal rating, for benchmarking this one
    against - minus any whose price is withheld.

    benchmark_case_against_book takes a median of renewal_increase_pct
    across the set, and a withheld price has none, so one unpriceable
    case in the book took the benchmark down for every other case with
    it. Ineligible cases were already skipped here; unpriced ones are
    the same thing arriving by a different route.
    """
    results = []
    for other in db.query(models.Case).filter(models.Case.id != case_id).all():
        result = _case_renewal_rating(other)
        if result is not None and not result.get("pricing_blocked"):
            results.append(result)
    return results


def _renewal_loading(case: models.Case, loading_pct: Optional[float]) -> tuple:
    """The loading a renewal is priced with, and any problem with it.

    Never invents one. An explicit override - the what-if query param on
    /renewal-rating - wins outright. Otherwise the case's own fee split
    is used, and if that split has never been entered the price is
    withheld rather than quoted on the house average: see
    renewal_loading_problems.

    Both renewal paths (the book and the case ledger) resolve the loading
    through here, so neither can be the one that still assumes.
    """
    if loading_pct is not None:
        return loading_pct, []
    fees = (case.tpa_fee_pct, case.commission_pct, case.hc_fee_pct, case.qic_fee_pct)
    return case_loading_pct(*fees), renewal_loading_problems(*fees)


def _blocked_rating(
    problems: List[dict],
    assumptions: dict,
    *,
    current_annual_premium: Optional[float],
    renewal_base_premium: Optional[float],
    actual_loss_ratio: Optional[float],
    rating_source: str,
    case_current_annual_premium: Optional[float],
    ibnr_pct: float,
    from_book: Optional[dict] = None,
    from_book_unavailable: Optional[dict] = None,
    expiring_premium: Optional[dict] = None,
) -> dict:
    """A withheld price, in the SAME shape as a quoted one.

    Nine call sites read this dict - the member-rate table, the bench
    KPIs, the new-business comparison, the report - and a short dict took
    every one of them down with a KeyError, so blocking one bad price
    blanked the whole case. Everything that depends on the price is None;
    everything that does not - the account's own experience, its premium,
    where the figures came from - is still reported, because it is not
    the thing that is wrong.
    """
    blocked = {
        "annualized_incurred_claims": None,
        "current_annual_premium": current_annual_premium,
        "renewal_base_premium": renewal_base_premium,
        "actual_loss_ratio": None if actual_loss_ratio is None else round(actual_loss_ratio, 4),
        "trended_claims": None,
        "credible_claims": None,
        "required_premium": None,
        "renewal_increase_pct": None,
        "assumptions_used": assumptions,
        "annualized_paid_and_outstanding": None,
        "months_used": [],
        "ibnr_detail": {},
        "method_gap": None,
        "method_gap_pct": None,
        "excluded_months": [],
        "excluded_paid": 0.0,
        "excluded_outstanding": 0.0,
        "rating_source": rating_source,
        "from_book": from_book,
        "expiring_premium": expiring_premium,
        "case_current_annual_premium": case_current_annual_premium,
        "premium_disagrees_with_book": False,
        "pricing_blocked": True,
        "pricing_problems": problems,
    }
    if from_book_unavailable is not None:
        blocked["from_book_unavailable"] = from_book_unavailable
    blocked["method_b"] = {**blocked, "ibnr_pct": ibnr_pct}
    return blocked


def _rating_from_book_figures(
    case: models.Case,
    book: dict,
    inflation_pct: Optional[float] = None,
    loading_pct: Optional[float] = None,
    ibnr_pct: Optional[float] = None,
    credibility_pct: Optional[float] = None,
) -> dict:
    """Both scorecard methods, built entirely from the book.

    Method A and Method B differ in ONE thing by design - how they
    reserve for claims incurred but not reported - and that is all they
    should differ in. They used to differ in their data source as well:
    a claims ledger uploaded against the case, annualised off whole
    months, divided by whatever premium the case record carried. On
    NOMADA that produced 75.6% against the book's 83.6%, and correcting
    the panel above them while leaving them alone left the wrong number
    on the card in three more places.

    So both now read the same account the Portfolio Loss Ratio screen
    does: its paid, its outstanding, its days on risk, its own gross
    premium. Method A reserves with the house 30-day tail on the paid
    run rate; Method B with a flat load. Nothing here is measured
    against a premium the account was not charged, and nothing is
    dropped for being a part month.
    """
    days = book["days"] or 0
    paid, outstanding = book["paid"], book["outstanding"]
    scale = (365 / days) if days else 0.0

    effective_ibnr_pct = ibnr_pct if ibnr_pct is not None else DEFAULT_IBNR_PCT
    incurred_a = (paid + outstanding + book["ibnr"]) * scale
    incurred_b = (paid + outstanding) * (1 + effective_ibnr_pct) * scale

    defaults = RenewalRatingAssumptions()
    effective_loading_pct, loading_problems = _renewal_loading(case, loading_pct)
    effective_inflation_pct = inflation_pct if inflation_pct is not None else defaults.inflation_pct

    # What the increase is quoted against: a full year at current rates
    # for the headcount that is actually renewing. The book's gross
    # premium is prorated for members who joined or left mid-term, and a
    # renewal does not cover a prorated year - quoting off it understates
    # the ask on any account with mid-term joiners. NOMADA earned 90,347
    # against an annualised expiring premium of 103,486.
    expiring = _annualised_expiring_premium(case)

    # The house ladder, carried as a RATIO rather than as absolute
    # claims. Both methods measure their own loss ratio against the
    # earned premium the claims actually ran against, then that ratio is
    # trended and grossed up onto the expiring premium.
    #
    # Pricing off absolute claims instead put the numerator on one basis
    # and the denominator on another: annualised claims over the expiring
    # premium read 73.0% where the account runs at 83.6%, because the
    # expiring premium is 14.5% larger than the pro-rata one the claims
    # were earned against. That turned an increase of 18.3% into 1.9%.
    earned = book["earned_premium"]
    lr_a = (paid + outstanding + book["ibnr"]) / earned if earned else 0.0
    lr_b = (paid + outstanding) * (1 + effective_ibnr_pct) / earned if earned else 0.0
    base = expiring["total"] or book["gross_premium"]

    # Every pricing input, checked before anything is priced. A bad fee
    # field does not make the ask slightly wrong, it makes it several
    # times wrong, and an underwriter reading the result has no way to
    # see the input was the cause - so the price is withheld and the
    # problem named instead.
    # A loading that was never entered comes first: it is a question for
    # the underwriter rather than a wrong number, and it is the reason the
    # other checks would be measuring an assumed 33% in the first place.
    problems = loading_problems + pricing_input_problems(
        loss_ratio=lr_a, expiring_annual_premium=base,
        inflation_pts=effective_inflation_pct,
        loading_pct=None if loading_problems else effective_loading_pct,
        member_count=book.get("member_count"),
    )
    if problems:
        return _blocked_rating(
            problems,
            {
                "inflation_pct": effective_inflation_pct,
                "loading_pct": None if loading_problems else effective_loading_pct,
                "credibility_pct": credibility_pct
                if credibility_pct is not None else DEFAULT_CREDIBILITY_PCT,
            },
            current_annual_premium=book["gross_premium"],
            renewal_base_premium=base,
            actual_loss_ratio=lr_a,
            rating_source="book",
            case_current_annual_premium=case.current_annual_premium,
            ibnr_pct=effective_ibnr_pct,
            from_book=book,
            expiring_premium=expiring,
        )
    ladder_a = renewal_from_loss_ratio(lr_a, base, effective_inflation_pct, effective_loading_pct)
    ladder_b = renewal_from_loss_ratio(lr_b, base, effective_inflation_pct, effective_loading_pct)

    two_methods = calculate_renewal_rating_two_methods(
        incurred_a, incurred_b, book["gross_premium"],
        inflation_pct=effective_inflation_pct, loading_pct=effective_loading_pct,
        credibility_pct=credibility_pct if credibility_pct is not None else DEFAULT_CREDIBILITY_PCT,
        renewal_base=base,
    )
    for method, ladder in ((two_methods["method_a"], ladder_a),
                           (two_methods["method_b"], ladder_b)):
        method["actual_loss_ratio"] = ladder["loss_ratio"]
        method["trended_loss_ratio"] = ladder["trended_loss_ratio"]
        method["required_share_of_expiring"] = ladder["required_share_of_expiring"]
        method["experience_share_of_expiring"] = ladder["experience_share_of_expiring"]
        method["experience_increase_pct"] = ladder["experience_increase_pct"]
        method["experience_required_premium"] = ladder["experience_required_premium"]
        method["minimum_increase_pct"] = ladder["minimum_increase_pct"]
        method["floor_applied"] = ladder["floor_applied"]
        method["required_premium"] = ladder["required_premium"]
        method["renewal_increase_pct"] = ladder["renewal_increase_pct"]
        method["renewal_base_premium"] = ladder["expiring_annual_premium"]

    # The calendar months the exposure actually spans, so anything
    # reading len(months_used) for a credibility or frequency
    # denominator still gets a real number.
    start = date.fromisoformat(book["policy_start_date"])
    end = date.fromisoformat(book["as_of"])
    months_used, cursor = [], date(start.year, start.month, 1)
    while cursor <= end:
        months_used.append(f"{cursor.year}-{cursor.month:02d}")
        cursor = date(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)

    annualised_po = (paid + outstanding) * scale
    result = two_methods["method_a"]
    result["annualized_paid_and_outstanding"] = round(annualised_po, 2)
    result["months_used"] = months_used
    result["ibnr_detail"] = {
        "total_paid": paid,
        "total_outstanding": outstanding,
        "elapsed_days": days,
        "ibnr": book["ibnr"],
        "incurred_to_date": book["incurred_claims"],
        "annualized_ibnr": round(book["ibnr"] * scale, 2),
        "annualized_paid": round(paid * scale, 2),
        "annualized_outstanding": round(outstanding * scale, 2),
        "annualization_factor": round(scale, 4),
        "months_count": len(months_used),
        "annualized_incurred_claims": round(incurred_a, 2),
    }
    result["method_b"] = two_methods["method_b"]
    result["method_b"]["annualized_paid_and_outstanding"] = result["annualized_paid_and_outstanding"]
    result["method_b"]["ibnr_pct"] = effective_ibnr_pct
    result["method_b"]["months_used"] = months_used
    result["method_gap"] = round(
        ladder_b["required_premium"] - ladder_a["required_premium"], 2)
    result["method_gap_pct"] = round(
        (ladder_b["required_premium"] / ladder_a["required_premium"] - 1) * 100, 2
    ) if ladder_a["required_premium"] else None
    result["ladder"] = ladder_a
    result["from_book"] = book
    result["rating_source"] = "book"
    result["expiring_premium"] = expiring
    # No month is discarded when reading the book, so there is nothing
    # unaccounted for - said explicitly because the ledger path's own
    # exclusions are what lost NOMADA's August outstanding.
    result["excluded_months"] = []
    result["excluded_paid"] = 0.0
    result["excluded_outstanding"] = 0.0
    # What the CASE RECORD still says, kept separate from the premium
    # the rating actually used - otherwise the mismatch warning prints
    # the book figure on both sides of "these disagree".
    result["case_current_annual_premium"] = case.current_annual_premium
    result["premium_disagrees_with_book"] = bool(
        case.current_annual_premium
        and book["gross_premium"]
        and abs(case.current_annual_premium - book["gross_premium"]) > 1.0
    )
    return result


def _why_no_book_figures(case: models.Case) -> dict:
    """Why this case could not be measured off the book, said out loud.

    Almost always a name that does not match. The case is opened by
    company name and the book keys on master client, and the two are
    typed by different people at different times - "... MANAGING LLC"
    against "... MANAGING LL" is enough. So this names the closest
    accounts on the book rather than leaving an underwriter to guess
    why a card is showing them ledger figures.
    """
    from difflib import get_close_matches

    from sqlalchemy.orm import object_session


    db = object_session(case)
    if db is None:
        return {"reason": "no database session"}
    if not book_repo.has_members(db):
        return {"reason": "No membership has been uploaded to Portfolio Analysis yet."}

    rows = book_analysis.account_loss_ratio_rows_for_book(db)
    names = sorted({r["master_client"] for r in rows if r.get("master_client")})
    if not names:
        return {"reason": "The uploaded book produced no account rows."}
    # Substring first, then fuzzy. The common miss is a name that is a
    # prefix of the other ("NOMADA EVENTS" inside "NOMADA EVENTS
    # ORGANIZING AND MANAGING LLC"), and a long tail of extra words drags
    # the similarity ratio below any sensible cutoff even though it is
    # obviously the same account.
    wanted = (case.company_name or "").strip().casefold()
    close = [n for n in names
             if wanted and (wanted in n.casefold() or n.casefold() in wanted)]
    for name in get_close_matches(case.company_name or "", names, n=5, cutoff=0.5):
        if name not in close:
            close.append(name)
    return {
        "reason": (
            f"This case's company name, '{case.company_name}', matches no account on the book, "
            f"so the figures below come from the case's own claims ledger instead. Rename the case "
            f"to the account's name on the book to price it off the latest upload."
        ),
        "case_company_name": case.company_name,
        "closest_accounts_on_the_book": close,
        "accounts_on_the_book": len(names),
    }


def _apply_increase_override(result: Optional[dict], case: models.Case) -> Optional[dict]:
    """The increase an underwriter is actually quoting, where they have
    said it differs from the one the experience produces.

    A renewal is a negotiation. An account can be held below what its
    claims ask for to keep a relationship, or pushed above it - and the
    portal quoting only the arithmetic means the figure on the renewal
    list, the member rates and the printed review is not the figure being
    sent to the client.

    The override REPLACES the ask everywhere, because a number that is
    quoted on one screen and not another is the bug this whole session
    has been about. It never touches the computed one: both come back,
    with increase_source saying which is being quoted, exactly as the 9%
    floor reports experience_increase_pct beside renewal_increase_pct.
    "This account needs 18%" and "we are asking 12%" are different facts
    and one number cannot carry both.
    """
    if result is None or result.get("pricing_blocked"):
        return result
    override = case.renewal_increase_override_pct
    if override is None:
        result["increase_source"] = "computed"
        return result

    base = result.get("renewal_base_premium")
    result["computed_increase_pct"] = result.get("renewal_increase_pct")
    result["computed_required_premium"] = result.get("required_premium")
    result["renewal_increase_pct"] = round(override, 2)
    result["required_premium"] = round(base * (1 + override / 100), 2) if base else None
    result["increase_source"] = "override"
    result["increase_override_pct"] = round(override, 2)
    return result


def _case_renewal_rating(
    case: models.Case,
    inflation_pct: Optional[float] = None,
    loading_pct: Optional[float] = None,
    ibnr_pct: Optional[float] = None,
    credibility_pct: Optional[float] = None,
) -> Optional[dict]:
    """Shared computation behind GET /{case_id}/renewal-rating and
    GET /{case_id}/renewal-benchmark (which needs every eligible case's
    own rating, not just one, to build a book-wide comparison). Returns
    None rather than raising when a case isn't eligible (no ledger, no
    current_annual_premium, or no full months of claims yet), so a
    book-wide scan can skip ineligible cases instead of every one
    becoming a 404/400 to catch.

    loading_pct defaults to the CASE'S OWN tpa_fee_pct/commission_pct/
    hc_fee_pct/qic_fee_pct split (via case_loading_pct), not the flat
    DEFAULT_LOADING_PCT, so every caller - the renewal rating card, the
    book benchmark, the client summary, the member-rate table - agrees
    on the same required_premium for a given case without each one
    having to remember to look up the case's own fees itself. An
    explicit loading_pct (e.g. a what-if query param on
    /renewal-rating) still overrides it.

    The top-level result is always Method A ("Gross Loss Ratio") - a
    "method_b" key is attached alongside it (Method B/"Burning Cost") for
    the Renewal Bench's side-by-side scorecard. The two methods use
    DIFFERENT IBNR conventions on the same Paid+Outstanding base - see
    calculate_renewal_rating_two_methods and dynamic_ibnr_incurred_claims.
    """
    # The book first, and then nothing else. Both methods are built from
    # the account's own uploaded experience and its own gross premium, so
    # there is no figure anywhere on the card measured against a premium
    # the account was not charged. The ledger path below runs only for a
    # case that is not on the book at all.
    from_book = _account_rating_from_book(case)
    if from_book:
        return _apply_increase_override(_rating_from_book_figures(
            case, from_book, inflation_pct, loading_pct, ibnr_pct, credibility_pct), case)

    entries = case.claims_ledger_entries
    if not entries or not case.current_annual_premium:
        return None
    full_months = _full_months_from_ledger(entries)
    if not full_months:
        return None

    # This month-by-month figure is Paid + Outstanding only (whatever the
    # ledger's own final_amount carries, regardless of claim_status) -
    # Method B's own IBNR (a flat load) is applied to this below. Method A
    # instead needs the paid/outstanding split and elapsed days for its
    # own DYNAMIC IBNR - see dynamic_ibnr_incurred_claims.
    avg_month = sum(m["final_amount"] for m in full_months) / len(full_months)
    annualized_paid_and_outstanding = avg_month * 12

    full_month_keys = {(m["year"], m["month"]) for m in full_months}
    total_paid = 0.0
    total_outstanding = 0.0
    for e in entries:
        d = e.date_of_treatment
        if not d or (d.year, d.month) not in full_month_keys:
            continue
        if _is_paid_claim_status(e.claim_status):
            total_paid += e.final_amount or 0.0
        else:
            total_outstanding += e.final_amount or 0.0

    # What the excluded months hold. full_months_only drops the ledger's
    # last month (it is exported mid-month, so averaging it understates
    # the run rate) - but dropping it from the AVERAGE is not the same as
    # pretending its claims do not exist, and reporting nothing is how
    # AED 1,953.89 of NOMADA's outstanding went missing between the
    # ledger and the card.
    excluded_paid = 0.0
    excluded_outstanding = 0.0
    excluded_months = set()
    for e in entries:
        d = e.date_of_treatment
        if not d or (d.year, d.month) in full_month_keys:
            continue
        excluded_months.add(f"{d.year}-{d.month:02d}")
        if _is_paid_claim_status(e.claim_status):
            excluded_paid += e.final_amount or 0.0
        else:
            excluded_outstanding += e.final_amount or 0.0

    policy_start = entries[0].policy_start_date
    last_year, last_month = max(full_month_keys)
    period_end = date(last_year, last_month, calendar.monthrange(last_year, last_month)[1])
    elapsed_days = (period_end - policy_start).days if policy_start else 0

    dynamic = dynamic_ibnr_incurred_claims(total_paid, total_outstanding, elapsed_days, len(full_months))

    effective_ibnr_pct = ibnr_pct if ibnr_pct is not None else DEFAULT_IBNR_PCT
    incurred_claims_method_b = annualized_paid_and_outstanding * (1 + effective_ibnr_pct)

    defaults = RenewalRatingAssumptions()
    effective_loading_pct, loading_problems = _renewal_loading(case, loading_pct)
    effective_inflation_pct = inflation_pct if inflation_pct is not None else defaults.inflation_pct

    # The same gate as the book path. This branch used to price with
    # whatever case_loading_pct returned, which for an un-configured case
    # is the flat house 33% - so an account off the book was quoted on an
    # assumption while an account on the book was refused for it.
    if loading_problems:
        return _blocked_rating(
            loading_problems,
            {
                "inflation_pct": effective_inflation_pct,
                "loading_pct": None,
                "credibility_pct": credibility_pct
                if credibility_pct is not None else DEFAULT_CREDIBILITY_PCT,
            },
            current_annual_premium=case.current_annual_premium,
            renewal_base_premium=case.current_annual_premium,
            actual_loss_ratio=(
                dynamic["annualized_incurred_claims"] / case.current_annual_premium
                if case.current_annual_premium else None
            ),
            rating_source="case claims ledger",
            case_current_annual_premium=case.current_annual_premium,
            ibnr_pct=effective_ibnr_pct,
            from_book_unavailable=_why_no_book_figures(case),
        )

    two_methods = calculate_renewal_rating_two_methods(
        dynamic["annualized_incurred_claims"], incurred_claims_method_b, case.current_annual_premium,
        inflation_pct=effective_inflation_pct, loading_pct=effective_loading_pct,
        credibility_pct=credibility_pct if credibility_pct is not None else DEFAULT_CREDIBILITY_PCT,
    )
    months_used = [f"{m['year']}-{m['month']:02d}" for m in full_months]

    result = two_methods["method_a"]
    result["annualized_paid_and_outstanding"] = round(annualized_paid_and_outstanding, 2)
    result["ibnr_detail"] = dynamic
    result["months_used"] = months_used

    result["method_b"] = two_methods["method_b"]
    result["method_b"]["annualized_paid_and_outstanding"] = result["annualized_paid_and_outstanding"]
    result["method_b"]["ibnr_pct"] = effective_ibnr_pct
    result["method_b"]["months_used"] = months_used

    result["method_gap"] = two_methods["gap"]
    result["method_gap_pct"] = two_methods["gap_pct"]
    result["excluded_months"] = sorted(excluded_months)
    result["excluded_paid"] = round(excluded_paid, 2)
    result["excluded_outstanding"] = round(excluded_outstanding, 2)

    # Reached only when the account is not on the book, so say why. A
    # card falling back to a case ledger looks identical to one reading
    # the latest upload, and the reader has no way to tell them apart.
    result["rating_source"] = "case claims ledger"
    result["from_book_unavailable"] = _why_no_book_figures(case)
    return _apply_increase_override(result, case)


@router.get("/{case_id}/renewal-rating")
def get_renewal_rating(
    case_id: int,
    db: Session = Depends(get_db),
    inflation_pct: Optional[float] = None,
    loading_pct: Optional[float] = None,
    ibnr_pct: Optional[float] = Query(
        None, description="IBNR load turning Paid+Outstanding into a true Incurred figure - applies equally to both methods, defaults to 10%"
    ),
    credibility_pct: Optional[float] = Query(
        None, description="Method B (Burning Cost) only - credibility weight on the trended incurred claims before grossing up, defaults to 90%"
    ),
):
    """The renewal-increase calculation (app/scoring/rules/renewal_rating.py):
    Incurred claims (Paid + Outstanding from the ledger, plus an IBNR
    load) over the case's current_annual_premium for the actual loss
    ratio, trended for inflation, then grossed up for the commission/OPEX
    loading. Requires both a claims ledger upload and current_annual_premium
    set on the case (PATCH /cases/{id}).

    The response's own "method_b" key carries the SAME incurred-claims
    figure additionally weighted by credibility_pct (see
    calculate_renewal_rating_two_methods) - the Renewal Bench's
    "Burning Cost" scorecard method, alongside this one.
    """
    case = _get_case_or_404(db, case_id)
    # An account on the book needs neither of these: it brings its own
    # claims and its own gross premium. Only the ledger fallback does.
    result = _case_renewal_rating(case, inflation_pct, loading_pct, ibnr_pct, credibility_pct)
    if result is None and not case.claims_ledger_entries:
        raise HTTPException(status_code=404, detail="No claims ledger uploaded for this case")
    if result is None and not case.current_annual_premium:
        raise HTTPException(
            status_code=400,
            detail="Case has no current_annual_premium set - PATCH /cases/{id} with the expiring premium first",
        )
    if result is None:
        raise HTTPException(status_code=400, detail="Not enough full months of claims data to compute a renewal rating")
    return result


@router.get("/{case_id}/renewal-benchmark")
def get_renewal_benchmark(case_id: int, db: Session = Depends(get_db)):
    """This case's own renewal rating (see get_renewal_rating) benchmarked
    against every OTHER case in the book that also has its own computed
    renewal rating (see benchmark_case_against_book) - case-to-case,
    since HealthCross has no external market-rate data source. A
    snapshot, not a trend: most cases only have one year of claims
    history so far, so there's no year-over-year comparison to make yet.
    Same eligibility/error rules as /renewal-rating for this case itself;
    other cases that aren't eligible are just skipped, not errored on.
    """
    case = _get_case_or_404(db, case_id)
    if not case.claims_ledger_entries:
        raise HTTPException(status_code=404, detail="No claims ledger uploaded for this case")
    if not case.current_annual_premium:
        raise HTTPException(
            status_code=400,
            detail="Case has no current_annual_premium set - PATCH /cases/{id} with the expiring premium first",
        )
    this_result = _case_renewal_rating(case)
    if this_result is None:
        raise HTTPException(status_code=400, detail="Not enough full months of claims data to compute a renewal rating")

    # No price, nothing to rank. The case's own result still goes out,
    # carrying the reason - a 500 here would say nothing at all.
    if this_result.get("pricing_blocked"):
        return {"case": this_result, "book": None}

    other_results = _comparable_results(db, case_id)

    return {
        "case": this_result,
        "book": benchmark_case_against_book(this_result, other_results),
    }


@router.get("/{case_id}/renewal-vs-new-business")
def get_renewal_vs_new_business(case_id: int, db: Session = Depends(get_db)):
    """Compares this renewal's own required premium against what the
    current New Business rate card would charge this SAME case's own
    census/category mix - generated automatically (see maybe_auto_requote),
    no manual re-entry required. Distinct from
    /new-business-quote/burning-cost-comparison, which compares an NB
    quote against the whole BOOK's burning cost rather than this case's
    own renewal rating.

    Requires the same eligibility as /renewal-rating (claims ledger +
    current_annual_premium) for the renewal side; the New Business side
    is best-effort - if there's no rate card uploaded yet, or any census
    category still can't be resolved to a Product/Network/TPA (see
    _resolve_auto_quote_categories), new_business is returned as None
    rather than erroring the whole endpoint, since the renewal comparison
    is still useful on its own.
    """
    case = _get_case_or_404(db, case_id)
    # An account priced off the book brings its own claims and its own
    # premium, so neither a case ledger nor a typed premium is required -
    # the same guard /renewal-rating already carries. Demanding them here
    # 404'd the comparison on exactly the renewals it is most useful for.
    renewal = _case_renewal_rating(case)
    if renewal is None and not case.claims_ledger_entries:
        raise HTTPException(status_code=404, detail="No claims ledger uploaded for this case")
    if renewal is None and not case.current_annual_premium:
        raise HTTPException(
            status_code=400,
            detail="Case has no current_annual_premium set - PATCH /cases/{id} with the expiring premium first",
        )
    if renewal is None:
        raise HTTPException(status_code=400, detail="Not enough full months of claims data to compute a renewal rating")

    maybe_auto_requote(case_id, db)
    latest_quote = (
        db.query(models.NewBusinessQuote)
        .filter_by(case_id=case_id)
        .order_by(models.NewBusinessQuote.created_at.desc())
        .first()
    )
    new_business_premium = latest_quote.case_gross_annual_premium if latest_quote else None

    gap = None
    gap_pct = None
    if new_business_premium is not None:
        required = renewal.get("required_premium")
        gap = round(required - new_business_premium, 2) if required is not None else None
        gap_pct = (round((required / new_business_premium - 1) * 100, 2)
                   if (required is not None and new_business_premium) else None)

    return {
        "renewal_required_premium": renewal.get("required_premium"),
        "renewal_required_premium_method_b": (renewal.get("method_b") or {}).get("required_premium"),
        "new_business_module_premium": new_business_premium,
        "new_business_quote_id": latest_quote.id if latest_quote else None,
        "gap": gap,
        "gap_pct": gap_pct,
        # Which picks are actually missing, rather than "set them on the
        # Benefits tab". The rate card needs three per category and
        # naming the ones that are blank is the difference between a
        # complaint and an instruction.
        "missing_for_new_business": (
            _missing_quote_inputs(case, db) if new_business_premium is None else []
        ),
    }


def _missing_quote_inputs(case, db: Session) -> List[dict]:
    """Per category, which of Product / Network / TPA has not been set."""
    quoted = {
        c["category"]: c
        for c in (
            (db.query(models.NewBusinessQuote)
             .filter_by(case_id=case.id)
             .order_by(models.NewBusinessQuote.created_at.desc())
             .first() or type("q", (), {"categories": []})()).categories or []
        )
    }
    categories = sorted({(r.category or "").strip() for r in case.census_records if r.category})
    if not categories:
        return [{"category": None, "missing": ["a census"]}]

    rows = []
    for category in categories:
        design = quoted.get(category) or {}
        missing = [name for name, key in
                   (("Product", "product"), ("Network", "network"), ("TPA", "tpa"))
                   if not design.get(key)]
        if missing:
            rows.append({"category": category, "missing": missing})
    return rows


@router.get("/{case_id}/renewal-bench-summary")
def get_renewal_bench_summary(
    case_id: int,
    db: Session = Depends(get_db),
    underwriter_adjustment_pct: float = Query(0.0, description="Manual underwriter override, in percentage points - the Recommended Renewal Premium hero card's own editable field"),
    authority_threshold_pct: float = Query(15.0, description="How many percentage points of underwriter_adjustment_pct count as 'within authority' before needing escalation - a visible, overridable assumption, not a hidden rule"),
):
    """Everything the Renewal Bench tab's header/KPI-strip/donut/Recommended-
    Premium/Drivers-waterfall sections need in one call, alongside the
    existing /renewal-rating (scorecard), /renewal-vs-new-business, and
    /large-claims/ /census-movement endpoints this tab already uses -
    matching the approved Renewal Bench mockup's "one continuous page"
    layout. Same eligibility as /renewal-rating: a claims ledger and
    current_annual_premium must both be on file.

    The Renewal Drivers waterfall and Recommended Renewal Premium hero
    card are built from Method A ("Gross Loss Ratio") only, since Method A
    is credibility_pct=1.0 by construction (see renewal_rating.py) - the
    full, un-shaded, dynamic-IBNR figure the mockup's own "Standard"-tagged
    method uses. Method B remains visible in the Scorecard section for
    comparison, same as today.
    """
    case = _get_case_or_404(db, case_id)
    if not case.claims_ledger_entries:
        raise HTTPException(status_code=404, detail="No claims ledger uploaded for this case")
    if not case.current_annual_premium:
        raise HTTPException(
            status_code=400,
            detail="Case has no current_annual_premium set - PATCH /cases/{id} with the expiring premium first",
        )
    renewal = _case_renewal_rating(case)
    if renewal is None:
        raise HTTPException(status_code=400, detail="Not enough full months of claims data to compute a renewal rating")

    claims = _case_claim_dicts(case)
    census_member_count = len(case.census_records)
    census_ages = [c.age for c in case.census_records if c.age is not None]
    kpis = case_claim_kpis(
        claims, census_member_count=census_member_count, census_ages=census_ages, months_count=len(renewal["months_used"])
    )
    # The headline figure on the Renewal Bench. Where the account is on
    # the book, this is the book's own gross earned loss ratio - the same
    # number the Portfolio Loss Ratio screen reports - not Method A's,
    # which annualises a case ledger and divides by whatever premium the
    # case record happens to carry. NOMADA read 75.6% here against 83.6%
    # there, and the headline is the number people quote.
    book = renewal.get("from_book")
    if book and book.get("gross_loss_ratio") is not None:
        kpis["actual_loss_ratio"] = book["gross_loss_ratio"]
        kpis["loss_ratio_basis"] = f"gross earned, from the book as of {book['as_of']}"
        kpis["loss_ratio_net"] = book.get("net_loss_ratio")
    else:
        kpis["actual_loss_ratio"] = renewal["actual_loss_ratio"]
        kpis["loss_ratio_basis"] = "Method A, from this case's claims ledger"

    claims_cost_breakdown = utilization_by_encounter_type(claims)

    snapshots = [
        {"relation": s.relation, "member_count": s.member_count}
        for s in db.query(models.CensusSnapshot).filter_by(case_id=case_id).all()
    ]
    current_relation_counts: dict = {}
    for c in case.census_records:
        rel = c.relation or "Unspecified"
        current_relation_counts[rel] = current_relation_counts.get(rel, 0) + 1
    census_change_pct = census_change_pct_from_snapshots(snapshots, current_relation_counts, case.current_annual_premium)

    loading_pct = case_loading_pct(case.tpa_fee_pct, case.commission_pct, case.hc_fee_pct, case.qic_fee_pct)
    # No drivers when the price is withheld: a waterfall of contributions
    # to a number that does not exist is not a partial answer, it is a
    # crash waiting for the first arithmetic step.
    drivers = None if renewal.get("pricing_blocked") else renewal_drivers(
        annualized_incurred_claims=renewal.get("annualized_incurred_claims"),
        trended_claims=renewal.get("trended_claims"),
        current_annual_premium=case.current_annual_premium,
        loading_pct=loading_pct,
        census_change_pct=census_change_pct,
        underwriter_adjustment_pct=underwriter_adjustment_pct,
        authority_threshold_pct=authority_threshold_pct,
    )

    existing_plans = [p for p in case.benefit_plans if p.role == "existing"]
    product = (existing_plans[0].nb_product or existing_plans[0].plan_name) if existing_plans else None
    network = (existing_plans[0].nb_network or existing_plans[0].network_type) if existing_plans else None
    days_to_renewal = (case.renewal_date - date.today()).days if case.renewal_date else None

    # Total paid by policy year, from whatever ClaimsReport history actually
    # exists on this case (see /claims-report-comparison) - NOT a fabricated
    # multi-year loss-ratio series, since a per-year premium isn't tracked
    # historically (only the current one, current_annual_premium). Real
    # claims $ trend, however many years are actually on file - the
    # frontend shows "not enough history yet" rather than a fake chart
    # when fewer than 2 points come back.
    reports = sorted(
        (r for r in case.claims_reports if r.report_period_start and r.total_paid is not None),
        key=lambda r: r.report_period_start,
    )
    claims_trend = [{"year": r.report_period_start.year, "total_paid": r.total_paid} for r in reports]

    existing_premium = existing_premium_breakdown(
        [{"category": c.category, "existing_annual_rate": c.existing_annual_rate} for c in case.census_records]
    )
    # A cross-check, not a silent override: current_annual_premium is the
    # figure actually used everywhere above (loss ratio, drivers, etc) - if
    # the bottom-up rates x headcount total disagrees with it by more than
    # 2%, that's worth flagging to the underwriter as a data-quality signal
    # rather than quietly trusting whichever number happened to be typed in.
    existing_premium["current_annual_premium_on_case"] = case.current_annual_premium
    if existing_premium["total_existing_premium"] and case.current_annual_premium:
        existing_premium["discrepancy_pct"] = round(
            (existing_premium["total_existing_premium"] / case.current_annual_premium - 1) * 100, 2
        )
    else:
        existing_premium["discrepancy_pct"] = None

    return {
        "case_identity": {
            "company_name": case.company_name,
            "broker_name": case.broker_name,
            "member_count": census_member_count,
            "product": product,
            "network": network,
            "renewal_date": case.renewal_date,
            "days_to_renewal": days_to_renewal,
        },
        "kpis": kpis,
        "claims_cost_breakdown": claims_cost_breakdown,
        "claims_trend": claims_trend,
        "existing_premium": existing_premium,
        "drivers": drivers,
    }


def _case_claim_dicts(case: models.Case) -> List[dict]:
    """This case's own claims ledger, reshaped into the same generic claim
    dict (patient_id/diagnosis_description/date_of_treatment/final_amount/
    ip_op_maternity) Portfolio Analysis's own large-claims and
    utilization-by-encounter-type functions already expect (see
    app/scoring/rules/portfolio_analysis.py) - a case's claims ledger is
    the exact same per-claim-line shape as the book-wide claims export,
    just scoped to one case already, so those functions are reused as-is
    rather than reimplementing large-claims/utilization logic a second time.
    """
    return [
        {
            "patient_id": c.patient_id,
            "provider_name": c.provider_name,
            "diagnosis_description": c.diagnosis_description,
            "date_of_treatment": c.date_of_treatment,
            "final_amount": c.final_amount,
            "ip_op_maternity": c.ip_op_maternity,
        }
        for c in case.claims_ledger_entries
    ]


@router.get("/{case_id}/large-claims")
def get_case_large_claims(
    case_id: int,
    db: Session = Depends(get_db),
    top_n_claims: int = Query(10, description="How many of the single largest individual claim lines to return"),
    top_n_members: int = Query(20, description="How many members to return, ranked by their own cumulative claims total"),
    recurring_claim_threshold: float = Query(
        DEFAULT_LARGE_CLAIM_THRESHOLDS[0],
        description="A claim line counts toward 'recurring high-cost members' once it's at or above this AED amount",
    ),
    recurring_min_claim_count: int = Query(
        3, description="A member must have at least this many claim lines at or above recurring_claim_threshold to count as 'recurring'"
    ),
):
    """This case's own large-loss cut - the same four views Portfolio
    Analysis's book-wide /large-claims returns (see its own docstring for
    what each means), scoped to just this one case's claims ledger. Also
    flags a single "one-off" claim: whichever top claim, if any, makes up
    an outsized share of this case's own total incurred claims - a loss
    ratio driven mostly by one event reads very differently than one
    driven by broad claims activity across the group.
    """
    case = _get_case_or_404(db, case_id)
    claims = _case_claim_dicts(case)
    if not claims:
        raise HTTPException(status_code=404, detail="No claims ledger uploaded for this case")

    total_incurred = sum(c.get("final_amount") or 0.0 for c in claims)
    top_claims = top_claims_by_value(claims, top_n=top_n_claims)
    one_off_claim = None
    if top_claims and total_incurred:
        largest = top_claims[0]
        share_pct = largest["final_amount"] / total_incurred * 100
        if share_pct >= 15:  # a single claim this large is worth calling out on its own
            one_off_claim = {**largest, "share_of_total_pct": round(share_pct, 1)}

    return {
        "total_incurred": round(total_incurred, 2),
        "top_claims": top_claims,
        "top_members": top_members_by_total_claims(claims, top_n=top_n_members),
        "threshold_buckets": claims_above_thresholds(claims),
        "recurring_high_cost_members": recurring_high_cost_members(
            claims, claim_threshold=recurring_claim_threshold, min_claim_count=recurring_min_claim_count
        ),
        "one_off_claim": one_off_claim,
    }


@router.get("/{case_id}/executive-summary")
def get_executive_summary(case_id: int, db: Session = Depends(get_db)):
    """One consolidated top-of-page snapshot for EVERY case, new business
    or renewal alike, so an underwriter sees the account's overall
    standing without clicking through the individual tabs. Reuses the
    same computations those tabs already use (renewal rating/benchmark,
    burning cost, census demographics) so nothing here can drift from
    what they show.

    A renewal case (claims ledger + current_annual_premium on file) gets
    loss-ratio/renewal-increase/book-benchmark fields plus a burning-cost
    trend (latest claims report vs. the prior one, if a second year is on
    file). A new-business case (no claims history yet) instead gets its
    quoted target_premium benchmarked per-member against every other
    case in the book that has both a census and a premium on file. Either
    kind always gets the plain census/premium facts.
    """
    case = _get_case_or_404(db, case_id)

    census_count = len(case.census_records)
    census_summary = None
    if census_count:
        census = [
            {
                "age": c.age,
                "gender": c.gender,
                "marital_status": c.marital_status,
                "relation": c.relation,
                "nationality_zone": c.nationality_zone,
                "nationality": c.nationality,
            }
            for c in case.census_records
        ]
        census_summary = census_demographic_summary(census)

    is_renewal = bool(case.claims_ledger_entries and case.current_annual_premium)

    renewal = None
    benchmark = None
    if is_renewal:
        renewal = _case_renewal_rating(case)
        # A withheld price has no increase to rank against the book. The
        # rating still goes out - it carries the account's experience and
        # says why the price is withheld.
        if renewal is not None and not renewal.get("pricing_blocked"):
            other_results = _comparable_results(db, case_id)
            benchmark = benchmark_case_against_book(renewal, other_results)

    reports = (
        db.query(models.ClaimsReport)
        .filter_by(case_id=case_id)
        .order_by(models.ClaimsReport.report_period_start.asc(), models.ClaimsReport.created_at.asc())
        .all()
    )
    burning_cost = None
    if reports:
        latest_bc = _standard_burning_cost_per_member(reports[-1])
        prior_bc = _standard_burning_cost_per_member(reports[-2]) if len(reports) > 1 else None
        if latest_bc is not None:
            burning_cost = {
                "latest_per_member": latest_bc,
                "latest_report_period": (
                    f"{reports[-1].report_period_start} to {reports[-1].report_period_end}"
                    if reports[-1].report_period_start
                    else None
                ),
                "prior_per_member": prior_bc,
                "change_pct": round((latest_bc / prior_bc - 1) * 100, 1) if prior_bc else None,
            }

    new_business_benchmark = None
    if not is_renewal and case.target_premium and census_count:
        this_ppm = round(case.target_premium / census_count, 2)
        other_ppms = []
        for other in db.query(models.Case).filter(models.Case.id != case_id).all():
            other_premium = other.current_annual_premium or other.target_premium
            other_count = len(other.census_records)
            if other_premium and other_count:
                other_ppms.append(other_premium / other_count)
        if other_ppms:
            below = sum(1 for p in other_ppms if p < this_ppm)
            equal = sum(1 for p in other_ppms if p == this_ppm)
            new_business_benchmark = {
                "premium_per_member": this_ppm,
                "book_median_premium_per_member": round(_median(other_ppms), 2),
                "comparable_case_count": len(other_ppms),
                "percentile": round(100 * (below + 0.5 * equal) / len(other_ppms), 1),
                "low_credibility": len(other_ppms) < MIN_CREDIBLE_CASE_COUNT,
            }

    reference_premium = case.current_annual_premium or case.target_premium
    premium_per_member = round(reference_premium / census_count, 2) if census_count and reference_premium else None

    return {
        "case": {
            "id": case.id,
            "company_name": case.company_name,
            "broker_name": case.broker_name,
            "industry": case.industry,
            "existing_insurer": case.existing_insurer,
            "policy_start_date": case.policy_start_date,
            "renewal_date": case.renewal_date,
            "business_type": case.business_type,
        },
        "account_type": "renewal" if is_renewal else "new_business",
        "census_summary": census_summary,
        "premium": {
            "current_annual_premium": case.current_annual_premium,
            "target_premium": case.target_premium,
            "premium_per_member": premium_per_member,
        },
        "renewal": renewal,
        "benchmark": benchmark,
        "burning_cost": burning_cost,
        "new_business_benchmark": new_business_benchmark,
    }


@router.get("/{case_id}/renewal-client-summary")
def get_renewal_client_summary(case_id: int, db: Session = Depends(get_db)):
    """Everything the "Renewal Client Summary" export (Internal and
    External versions, printed from the case overview) needs in one call:
    case identity, this case's renewal rating and book benchmark (see
    get_renewal_rating/get_renewal_benchmark), its premium split into
    Risk Premium/TPA Fee/Commission/HC Fee (see
    premium_component_breakdown - uses the case's own
    tpa_fee_pct/commission_pct/hc_fee_pct if set, the usual defaults
    otherwise), a census demographic summary, and the top claims
    diagnoses by cost. Same eligibility rules as /renewal-rating for the
    renewal-rating/benchmark/premium-breakdown pieces; census and
    diagnoses are included whenever they're available regardless, since
    the census/claims tabs don't require a renewal rating to exist.
    """
    case = _get_case_or_404(db, case_id)

    renewal = None
    benchmark = None
    premium_breakdown = None
    if case.claims_ledger_entries and case.current_annual_premium:
        renewal = _case_renewal_rating(case)
        # A withheld price has no required_premium to split or to rank,
        # and both of these divide by it. The rating itself still goes
        # out - it carries the account's experience and the reason the
        # price is withheld, which is what the summary should show.
        if renewal is not None and not renewal.get("pricing_blocked"):
            other_results = _comparable_results(db, case_id)
            benchmark = benchmark_case_against_book(renewal, other_results)
            premium_breakdown = premium_component_breakdown(
                renewal, case.tpa_fee_pct, case.commission_pct, case.hc_fee_pct, case.qic_fee_pct
            )

    census_summary = None
    if case.census_records:
        census = [
            {
                "age": c.age,
                "gender": c.gender,
                "marital_status": c.marital_status,
                "relation": c.relation,
                "nationality_zone": c.nationality_zone,
                "nationality": c.nationality,
            }
            for c in case.census_records
        ]
        census_summary = census_demographic_summary(census)

    top_diagnoses = None
    if case.claims_ledger_entries:
        top_diagnoses = top_diagnoses_by_final_amount(_ledger_entry_dicts(case.claims_ledger_entries), top_n=5)

    return {
        "case": {
            "id": case.id,
            "company_name": case.company_name,
            "broker_name": case.broker_name,
            "existing_insurer": case.existing_insurer,
            "policy_start_date": case.policy_start_date,
            "renewal_date": case.renewal_date,
        },
        "renewal": renewal,
        "benchmark": benchmark,
        "premium_breakdown": premium_breakdown,
        "census_summary": census_summary,
        "top_diagnoses": top_diagnoses,
    }


def _member_rate_row(member: models.CensusRecord, renewal_increase_pct: Optional[float]) -> dict:
    existing = member.existing_annual_rate
    computed_new_rate = None
    if existing is not None and renewal_increase_pct is not None:
        computed_new_rate = round(existing * (1 + renewal_increase_pct / 100), 2)

    override = member.new_annual_rate_override
    effective_new_rate = override if override is not None else computed_new_rate
    rate_change_pct = None
    if existing not in (None, 0) and effective_new_rate is not None:
        rate_change_pct = round((effective_new_rate / existing - 1) * 100, 2)

    return {
        "census_record_id": member.id,
        "employee_ref": member.employee_ref,
        "category": member.category,
        "age": member.age,
        "gender": member.gender,
        "relation": member.relation,
        "region": region_for_emirate(member.emirates),
        "existing_annual_rate": existing,
        "computed_new_rate": computed_new_rate,
        "new_annual_rate_override": override,
        "effective_new_rate": effective_new_rate,
        "rate_change_pct": rate_change_pct,
    }


def _member_rates_response(case: models.Case, extra: Optional[dict] = None) -> dict:
    """Shared response shape for every /member-rates endpoint (GET, PATCH,
    and the rate-card import below) - "new" is whatever the member's own
    new_annual_rate_override says if set, otherwise existing_annual_rate
    grossed up by the case's own renewal_increase_pct (see
    _member_rate_row), so the per-member table stays in sync with the
    case-level renewal rating without duplicating that calculation.
    case_renewal_increase_pct is None (no auto-computed column,
    override-only) whenever the case doesn't have enough claims history
    for a renewal rating yet - same eligibility as /renewal-rating.
    """
    # No ledger gate. An account priced off the book has no per-case
    # claims ledger, so requiring one left every new rate blank on the
    # renewals that read the book - _case_renewal_rating already decides
    # for itself whether it can price, and says so.
    renewal_increase_pct = None
    renewal = _case_renewal_rating(case)
    if renewal is not None:
        # None when the price is withheld for a bad input, which leaves
        # the per-member table override-only rather than propagating a
        # figure that does not exist.
        renewal_increase_pct = renewal.get("renewal_increase_pct")

    members = [_member_rate_row(m, renewal_increase_pct) for m in case.census_records]
    existing_premium = existing_premium_breakdown(
        [{"category": m.category, "existing_annual_rate": m.existing_annual_rate} for m in case.census_records]
    )
    response = {
        "case_renewal_increase_pct": renewal_increase_pct,
        # Whether that increase is the experience's own or one an
        # underwriter is quoting instead - the grid says which, so a
        # negotiated number is never mistaken for a computed one.
        "increase_source": (renewal or {}).get("increase_source"),
        "computed_increase_pct": (renewal or {}).get("computed_increase_pct"),
        "members": members,
        "existing_premium": existing_premium,
        # Whether these members' ages were struck at a different date to
        # the one the case now carries. The grid buckets by age band, so a
        # stale basis puts members in the wrong band and understates the
        # premium with nothing on screen saying so.
        "age_basis_warning": stale_age_basis(case),
        # A renewal priced into a term that has already started is
        # almost always the EXPIRING term keyed by mistake.
        "past_term_warning": renewal_term_looks_wrong(case),
    }
    # Why there is no increase, when there is none. A grid of dashes is
    # indistinguishable from a grid that has not loaded, and an
    # underwriter has no way to tell "this case needs a claims ledger"
    # from "a fee field on this case is wrong" from "it is broken".
    if renewal_increase_pct is None:
        if renewal is None:
            response["no_increase_reason"] = (
                "No renewal increase yet: this account is not on the uploaded book, and the case "
                "has no claims ledger and expiring premium of its own to rate from. Upload the "
                "book in Portfolio Analysis, or a claims ledger on the Claims tab."
            )
        elif renewal.get("pricing_blocked"):
            problems = " ".join(p["message"] for p in renewal.get("pricing_problems") or [])
            response["no_increase_reason"] = (
                f"No renewal increase: the price is withheld until this is resolved. {problems} "
                f"Correct it on the case record and the new rates fill in."
            )
        else:
            response["no_increase_reason"] = (
                "No renewal increase could be computed for this case, so the new rates are "
                "override-only - type them per member below."
            )
    if extra:
        response.update(extra)
    return response


def _maybe_auto_populate_current_premium(case: models.Case, db: Session) -> None:
    """The first time member rates make a bottom-up existing-premium total
    computable, auto-populate case.current_annual_premium from it rather
    than requiring it be typed in separately - see existing_premium_breakdown.
    Never overwrites an already-set current_annual_premium (a manual entry
    or an earlier auto-populate stands until the case itself is updated via
    PATCH /cases/{id}), and only populates when every rated member actually
    contributes (total_existing_premium > 0)."""
    # Never silently replaces a premium that is already set. The
    # bottom-up total can be a part-rated grid, and one member's 10,000
    # overwriting a typed 500,000 destroys a correct number to fix a
    # stale one. Where the two disagree the Renewal Bench says so and
    # offers to adopt the computed figure - a decision an underwriter
    # takes, not one taken for them.
    if case.current_annual_premium is not None:
        return
    computed = existing_premium_breakdown(
        [{"category": m.category, "existing_annual_rate": m.existing_annual_rate} for m in case.census_records]
    )
    if computed["total_existing_premium"]:
        case.current_annual_premium = computed["total_existing_premium"]
        db.commit()


@router.get("/{case_id}/member-rates")
def get_member_rates(case_id: int, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    return _member_rates_response(case)


@router.patch("/{case_id}/member-rates")
def update_member_rates(case_id: int, rates: List[schemas.MemberRateIn], db: Session = Depends(get_db)):
    """Bulk-saves existing_annual_rate/new_annual_rate_override for a
    batch of this case's own census members in one call, so the
    per-member rate table can save every edited row at once. 404s if any
    census_record_id isn't a member of this case (typo/wrong case
    guard), leaving nothing partially saved.
    """
    case = _get_case_or_404(db, case_id)
    members_by_id = {m.id: m for m in case.census_records}

    for rate in rates:
        if rate.census_record_id not in members_by_id:
            raise HTTPException(
                status_code=404,
                detail=f"Census record {rate.census_record_id} does not belong to case {case_id}",
            )

    for rate in rates:
        member = members_by_id[rate.census_record_id]
        member.existing_annual_rate = rate.existing_annual_rate
        member.new_annual_rate_override = rate.new_annual_rate_override

    db.commit()
    _maybe_auto_populate_current_premium(case, db)
    return _member_rates_response(case)


@router.post("/{case_id}/member-rates/from-book")
def fill_member_rates_from_book(case_id: int, db: Session = Depends(get_db)):
    """Fills existing_annual_rate from HealthCross's own membership,
    matching each census member by their beneficiary ID.

    A renewal case opened from the Renewal Due List carries every
    member's rate across with it. A case whose census was uploaded from a
    broker file does not - broker censuses rarely carry per-member rates
    - and the Existing Premium build-up then reads "0 of 178 members
    rated" against a case record showing millions. The rates exist; they
    are just on the book rather than on the case.

    Non-destructive on purpose. It only fills a rate that is missing, so
    a rate an underwriter has typed or imported is never overwritten by
    the book, and running it twice changes nothing the second time. Re-
    seeding the whole census would fill the rates too, and would throw
    away every other edit made since - which is why this exists instead.
    """
    from app.scoring.rules.renewal_intake import member_annual_rate

    case = _get_case_or_404(db, case_id)
    if not case.census_records:
        raise HTTPException(status_code=400, detail="This case has no census to fill rates on")

    rate_by_beneficiary = {}
    for member in book_repo.members(db):
        beneficiary_id = member.get("beneficiary_id")
        rate = member_annual_rate(member)
        if beneficiary_id and rate:
            # A member can appear on more than one policy year. The
            # renewing term's rate is the one being renewed off, so the
            # latest policy end wins.
            previous = rate_by_beneficiary.get(beneficiary_id)
            end = member.get("policy_end_date")
            if previous is None or (end and (previous[1] is None or end >= previous[1])):
                rate_by_beneficiary[beneficiary_id] = (rate, end)

    filled, already_rated, unmatched = 0, 0, []
    for record in case.census_records:
        if record.existing_annual_rate:
            already_rated += 1
            continue
        found = rate_by_beneficiary.get(record.employee_ref)
        if found:
            record.existing_annual_rate = found[0]
            filled += 1
        else:
            unmatched.append({"census_record_id": record.id, "employee_ref": record.employee_ref})

    db.commit()
    _maybe_auto_populate_current_premium(case, db)
    return _member_rates_response(case, extra={
        "filled_from_book": filled,
        "already_rated": already_rated,
        # Named, not just counted - a member the book has never heard of
        # is a census/roster mismatch worth looking at rather than a
        # number to shrug past.
        "unmatched_count": len(unmatched),
        "unmatched": unmatched[:50],
    })


@router.post("/{case_id}/member-rates/import-rate-card")
async def import_member_rate_card(case_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Bulk-fills existing_annual_rate for every census member matched
    against a QIC/broker "Premium Summary" rate card (see
    app/ingestion/premium_summary_rate_card.py) by the member's own
    category/gender/age - instead of typing well over a hundred rates in
    by hand. A member whose category/age/gender doesn't match any row in
    the file is left untouched and reported back in "unmatched" so gaps
    stay visible rather than silently zeroed. detected_fees is whatever
    Brokerage/TPA/HC percentages the file's own header block states, for
    the caller to offer applying to the case - never auto-applied here,
    since that would silently overwrite the case's own fee split.
    """
    case = _get_case_or_404(db, case_id)
    content = await file.read()
    try:
        parsed = parse_premium_summary_rate_card(io.BytesIO(content))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001 - pandas raises many shapes
        # A workbook pandas cannot open at all used to surface as a 500,
        # which the page rendered as a bare "Import failed" with nothing
        # to act on. The reason is the whole value of the message.
        raise HTTPException(
            status_code=400,
            detail=f"Could not read that workbook: {type(e).__name__}: {e}",
        )
    if not case.census_records:
        raise HTTPException(
            status_code=400,
            detail=("The rate card read fine, but this case has no census - "
                    "there are no members to match rates onto. Upload the census first."),
        )

    matched_count = 0
    unmatched = []
    for member in case.census_records:
        rate = lookup_rate(parsed["rates"], member.category, member.gender, member.age)
        if rate is not None:
            member.existing_annual_rate = rate
            matched_count += 1
        else:
            unmatched.append({
                "census_record_id": member.id,
                "employee_ref": member.employee_ref,
                "category": member.category,
                "gender": member.gender,
                "age": member.age,
            })

    db.commit()
    _maybe_auto_populate_current_premium(case, db)
    return _member_rates_response(case, extra={
        "matched_count": matched_count,
        "unmatched": unmatched,
        "detected_fees": parsed["fees"],
    })


@router.get("/{case_id}/renewal-report")
def get_renewal_report(case_id: int, db: Session = Depends(get_db)):
    """Everything the renewal document needs, from the book.

    One payload for one page. The Renewal Bench prints as seven pages of
    screen cards and leaves an underwriter to assemble the argument from
    figures scattered across them; this assembles it - what the account
    cost, what it therefore needs, and what the claims are made of.
    """
    from app.scoring.rules.portfolio_analysis import group_claims_by_beneficiary
    from app.scoring.rules.renewal_intake import (
        account_members,
        claim_belongs_to_term,
        continuing_and_leaving,
        current_term_members,
        term_member_windows,
    )
    from app.scoring.rules.renewal_repricing import member_claim_ranking

    case = _get_case_or_404(db, case_id)
    rating = _case_renewal_rating(case)
    if rating is None or not rating.get("from_book"):
        # The reason, not just the refusal. A rating of None means the
        # ledger fallback could not run either, so the explanation has to
        # be computed here rather than read off a result that does not
        # exist - otherwise the message is "cannot be reported" with no
        # way to find out why.
        why = (rating or {}).get("from_book_unavailable") or _why_no_book_figures(case)
        raise HTTPException(
            status_code=400,
            detail=("This renewal cannot be reported from the book. "
                    + (why.get("reason") or "")).strip(),
        )
    book = rating["from_book"]

    account = account_members(book_repo.members(db), case.company_name, book_repo.subgroup_master_by_name(db))
    term = current_term_members(account)
    windows = term_member_windows(term)
    claim_rows = [
        {"patient_id": pid, "date_of_treatment": dot, "final_amount": amt,
         "claim_status": st, "diagnosis_code": dc, "diagnosis_description": dd,
         "ip_op_maternity": iom}
        for pid, dot, amt, st, dc, dd, iom in db.query(
            models.PortfolioClaimEntry.patient_id,
            models.PortfolioClaimEntry.date_of_treatment,
            models.PortfolioClaimEntry.final_amount,
            models.PortfolioClaimEntry.claim_status,
            models.PortfolioClaimEntry.diagnosis_code,
            models.PortfolioClaimEntry.diagnosis_description,
            models.PortfolioClaimEntry.ip_op_maternity,
        ).all()
    ]
    by_beneficiary = group_claims_by_beneficiary(claim_rows)

    # Only this account's claims, inside each member's own exposure - the
    # same window rule the loss ratio uses, so the breakdown below sums to
    # the incurred figure above it rather than to something near it.
    own = [
        c for c in claim_rows
        if claim_belongs_to_term(c["patient_id"], c.get("date_of_treatment"), windows)
    ]
    active, leaving = continuing_and_leaving(account)
    leaver_ids = {m.get("beneficiary_id") for m in leaving}
    leaver_claims = sum(c.get("final_amount") or 0.0 for c in own if c["patient_id"] in leaver_ids)
    monthly = [
        {"month": f"{m['year']}-{m['month']:02d}", "paid": m["final_amount"]}
        for m in monthly_final_amount(own)
    ]
    # The keys are diagnosis_description/diagnosis_code, not description/
    # code - reading the wrong ones printed a table of blank names.
    diagnoses = [
        {"label": d.get("diagnosis_description") or d.get("diagnosis_code"),
         "claim_count": d.get("count"), "amount": d.get("value"),
         "chronic": d.get("classification") == "chronic",
         "high_exposure": d.get("high_exposure")}
        for d in top_diagnoses_by_final_amount(own, top_n=8)
    ]
    return {
        "case": {
            "id": case.id,
            "company_name": case.company_name,
            "broker_name": case.broker_name,
            "product": _mode_of([m.get("product_name") for m in term]),
            "renewal_date": case.renewal_date,
        },
        "book": book,
        "ladder": rating.get("ladder"),
        "rating": {
            "required_premium": rating.get("required_premium"),
            "required_share_of_expiring": rating.get("required_share_of_expiring"),
            "renewal_increase_pct": rating.get("renewal_increase_pct"),
            "renewal_base_premium": rating.get("renewal_base_premium"),
            "expiring_premium": rating.get("expiring_premium"),
            "case_current_annual_premium": rating.get("case_current_annual_premium"),
            "premium_disagrees_with_book": rating.get("premium_disagrees_with_book"),
            "assumptions_used": rating.get("assumptions_used"),
            # Why there is no price, when there is none - so the printed
            # page says so instead of omitting its own headline.
            "pricing_blocked": rating.get("pricing_blocked"),
            "pricing_problems": rating.get("pricing_problems"),
            # Whether the ask is the experience's own or one the
            # underwriter is quoting instead. Without these the document
            # printed the overridden premium in its KPI strip and the
            # computed one in its ladder, three inches apart, with nothing
            # saying which was which - two answers on one page.
            "increase_source": rating.get("increase_source"),
            "computed_increase_pct": rating.get("computed_increase_pct"),
            "computed_required_premium": rating.get("computed_required_premium"),
        },
        # The benefits behind the price, for the comprehensive report.
        "benefits": [
            {
                "category": b.category,
                "plan_name": b.plan_name,
                "role": b.role,
                "summary": b.standard_summary or {},
                "member_count": b.member_count,
            }
            for b in sorted(case.benefit_plans, key=lambda b: (b.category or "", b.role or ""))
        ],
        "monthly": monthly,
        "top_diagnoses": diagnoses,
        "top_claimants": member_claim_ranking(term, by_beneficiary, windows, top=8),
        "census": _renewal_census_profile(term),
        "encounter_split": utilization_by_encounter_type(own),
        "population": {
            "active_member_count": len(active),
            "deleted_member_count": len(leaving),
            "leaving": {"incurred": round(leaver_claims, 2)},
        },
    }


def _renewal_census_profile(members: List[dict]) -> dict:
    """The population being renewed, off the book's own roster.

    Age band, relation mix and dependant ratio - the three things that
    move next year's cost independently of this year's claims, and the
    ones an underwriter would otherwise have to open another tab for.
    """
    bands = {"0-17": 0, "18-30": 0, "31-40": 0, "41-50": 0, "51-60": 0, "61+": 0}
    relations: Counter = Counter()
    genders: Counter = Counter()
    ages = []
    for m in members:
        relations[(m.get("relation") or "unspecified").strip().title()] += 1
        genders[(m.get("gender") or "?").strip().upper()[:1]] += 1
        age = m.get("age")
        if age is None:
            continue
        ages.append(age)
        for label, ceiling in (("0-17", 17), ("18-30", 30), ("31-40", 40),
                               ("41-50", 50), ("61+", 200)):
            if age <= ceiling:
                bands["51-60" if 50 < age <= 60 else label] += 1
                break
    employees = sum(v for k, v in relations.items() if k in ("Employee", "Principal", "Staff"))
    dependants = len(members) - employees
    return {
        "member_count": len(members),
        "average_age": round(sum(ages) / len(ages), 1) if ages else None,
        "age_bands": [{"label": k, "count": v} for k, v in bands.items() if v],
        "relations": [{"label": k, "count": v} for k, v in relations.most_common()],
        "genders": [{"label": k, "count": v} for k, v in genders.most_common()],
        # Dependants claim differently from employees, and a ratio above
        # one is the single most reliable signal that next year costs
        # more than headcount suggests.
        "dependant_ratio": round(dependants / employees, 2) if employees else None,
    }


def _mode_of(values):
    present = [v for v in values if v]
    if not present:
        return None
    return Counter(present).most_common(1)[0][0]


@router.get("/{case_id}/renewal-summary.html", response_class=HTMLResponse,
            include_in_schema=False)
def get_renewal_summary_html(case_id: int, db: Session = Depends(get_db)):
    """One page: the loss ratio, the ask, the ladder, the premiums.

    Built from the same payload as the full report, by the same
    functions, so the short document and the long one cannot quote
    different numbers for the same account.
    """
    from app.reports.renewal_report import render_renewal_summary

    return HTMLResponse(render_renewal_summary(get_renewal_report(case_id, db)))


@router.get("/{case_id}/renewal-report.html", response_class=HTMLResponse,
            include_in_schema=False)
def get_renewal_report_html(case_id: int, db: Session = Depends(get_db)):
    """The comprehensive file - the summary, then census, benefits and
    claims, then the basis. Rendered server-side at a URL so opening it
    is a plain window.open with nothing awaited.
    """
    from app.reports.renewal_report import render_renewal_report

    return HTMLResponse(render_renewal_report(get_renewal_report(case_id, db)))
