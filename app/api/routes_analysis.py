import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import db_models as models
from app.models import schemas
from app.reference.diagnosis_classification import classify_diagnosis_group, flag_diagnosis_group
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
from app.scoring.rules.renewal_rating import (
    RenewalRatingAssumptions,
    benchmark_case_against_book,
    calculate_renewal_rating,
    case_loading_pct,
    premium_component_breakdown,
)

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
            "annual_limit": f"USD {plan.annual_limit:,.0f}" if plan.annual_limit else None,
            "maternity_limit": f"USD {plan.maternity_limit:,.0f}" if plan.maternity_limit else None,
            "dental": "Covered" if plan.dental_covered else "Not covered",
            "optical": "Covered" if plan.optical_covered else "Not covered",
            "pre_existing_chronic_limit": "Covered" if plan.pre_existing_covered else "Not covered",
        }
    # OCR (a lower-confidence, best-effort extraction - see
    # app/ingestion/benefits_ocr.py) treats a field it never found a value
    # for as "Not Covered" rather than the more neutral "Not specified in
    # source document" every other, higher-confidence parser uses.
    not_specified_text = "Not Covered" if plan.source_format == "pdf-ocr" else NOT_SPECIFIED
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
        fields["network"] = compare_benefit_value(existing_plan.network_type, quoted_plan.network_type)

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
        assumptions = RenewalRatingAssumptions(
            inflation_pct=inflation_pct if inflation_pct is not None else defaults.inflation_pct,
            loading_pct=loading_pct if loading_pct is not None else defaults.loading_pct,
        )
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


def _case_renewal_rating(
    case: models.Case,
    inflation_pct: Optional[float] = None,
    loading_pct: Optional[float] = None,
) -> Optional[dict]:
    """Shared computation behind GET /{case_id}/renewal-rating and
    GET /{case_id}/renewal-benchmark (which needs every eligible case's
    own rating, not just one, to build a book-wide comparison). Returns
    None rather than raising when a case isn't eligible (no ledger, no
    current_annual_premium, or no full months of claims yet), so a
    book-wide scan can skip ineligible cases instead of every one
    becoming a 404/400 to catch.
    """
    entries = case.claims_ledger_entries
    if not entries or not case.current_annual_premium:
        return None
    full_months = _full_months_from_ledger(entries)
    if not full_months:
        return None

    avg_month = sum(m["final_amount"] for m in full_months) / len(full_months)
    annualized = avg_month * 12

    defaults = RenewalRatingAssumptions()
    assumptions = RenewalRatingAssumptions(
        inflation_pct=inflation_pct if inflation_pct is not None else defaults.inflation_pct,
        loading_pct=loading_pct if loading_pct is not None else defaults.loading_pct,
    )
    result = calculate_renewal_rating(annualized, case.current_annual_premium, assumptions=assumptions)
    result["months_used"] = [f"{m['year']}-{m['month']:02d}" for m in full_months]
    return result


@router.get("/{case_id}/renewal-rating")
def get_renewal_rating(
    case_id: int,
    db: Session = Depends(get_db),
    inflation_pct: Optional[float] = None,
    loading_pct: Optional[float] = None,
):
    """The renewal-increase calculation (app/scoring/rules/renewal_rating.py):
    actual loss ratio (annualized incurred claims from the ledger over the
    case's current_annual_premium), trended for inflation, then grossed up
    for the commission/OPEX loading. Requires both a claims ledger upload
    and current_annual_premium set on the case (PATCH /cases/{id}).
    """
    case = _get_case_or_404(db, case_id)
    if not case.claims_ledger_entries:
        raise HTTPException(status_code=404, detail="No claims ledger uploaded for this case")
    if not case.current_annual_premium:
        raise HTTPException(
            status_code=400,
            detail="Case has no current_annual_premium set - PATCH /cases/{id} with the expiring premium first",
        )
    result = _case_renewal_rating(case, inflation_pct, loading_pct)
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

    other_results = []
    for other in db.query(models.Case).filter(models.Case.id != case_id).all():
        other_result = _case_renewal_rating(other)
        if other_result is not None:
            other_results.append(other_result)

    return {
        "case": this_result,
        "book": benchmark_case_against_book(this_result, other_results),
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
        loading = case_loading_pct(case.tpa_fee_pct, case.commission_pct, case.hc_fee_pct)
        renewal = _case_renewal_rating(case, loading_pct=loading)
        if renewal is not None:
            other_results = []
            for other in db.query(models.Case).filter(models.Case.id != case_id).all():
                other_loading = case_loading_pct(other.tpa_fee_pct, other.commission_pct, other.hc_fee_pct)
                other_result = _case_renewal_rating(other, loading_pct=other_loading)
                if other_result is not None:
                    other_results.append(other_result)
            benchmark = benchmark_case_against_book(renewal, other_results)
            premium_breakdown = premium_component_breakdown(
                renewal, case.tpa_fee_pct, case.commission_pct, case.hc_fee_pct
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
