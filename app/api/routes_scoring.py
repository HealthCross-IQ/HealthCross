from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import db_models as models
from app.models import schemas
from app.scoring.engine import ScoringWeights, compute_scorecard
from app.scoring.rules.portfolio_analysis import (
    summarize_burning_cost_overall,
    summarize_population_mix,
    summarize_portfolio,
)

router = APIRouter(prefix="/cases", tags=["scoring"])


def _case_loading_pct(case: models.Case) -> float:
    """This case's own total loading - commission, TPA, HealthCross and
    QIC fees - as a fraction of gross premium. Uses whatever components
    the case carries and falls back to the standard defaults for any it
    doesn't, so a case nobody has set fees on is still priced at a real
    expense level rather than at zero (which would quote pure risk
    premium as if it were the price).
    """
    from app.scoring.rules.new_business_rating import (
        DEFAULT_COMMISSION_PCT,
        QIC_FEE_PCT,
        TPA_FEE_PCT,
        _DEFAULT_HEALTHCROSS_FEE_PCT,
    )

    return (
        (case.commission_pct if case.commission_pct is not None else DEFAULT_COMMISSION_PCT)
        + (case.tpa_fee_pct if case.tpa_fee_pct is not None else TPA_FEE_PCT)
        + (case.hc_fee_pct if case.hc_fee_pct is not None else _DEFAULT_HEALTHCROSS_FEE_PCT)
        + (case.qic_fee_pct if case.qic_fee_pct is not None else QIC_FEE_PCT)
    )


def _book_results(db: Session) -> Optional[list]:
    """The book's own priced member results, or None when Portfolio
    Analysis isn't set up yet. Computed once per scoring run and shared
    between the reference context and the burning cost cube - running the
    whole-book analysis twice for one scorecard is the expensive mistake
    here, not building the cube.
    """
    from app.api.routes_portfolio_analysis import _get_stored_as_of, _run_analysis

    try:
        return _run_analysis(db, as_of=_get_stored_as_of(db))
    except HTTPException:
        return None


def _book_cube(db: Session, results: Optional[list]) -> Optional[dict]:
    """The credibility-blended burning cost cube this case is priced
    against (see app/scoring/rules/burning_cost_cube.py). None whenever
    the book has no experience to price from, in which case the scorecard
    falls back to its old score-derived loading rather than failing.
    """
    from app.api.routes_portfolio_analysis import _rate_card_dicts
    from app.scoring.rules.burning_cost_cube import burning_cost_cube

    if not results:
        return None
    rate_cards = _rate_card_dicts(db)
    if not rate_cards:
        return None
    cube = burning_cost_cube(results, rate_cards)
    return cube if cube["book"]["burning_cost"] is not None else None


def _portfolio_reference(db: Session, case: models.Case, results: Optional[list] = None) -> Optional[dict]:
    """Best-effort 'book reference' context from Portfolio Analysis for this
    case's scorecard - HealthCross's own already-booked members' burning
    cost (matched to this case's own HealthCross quote network(s) when one
    has been uploaded, else the whole book's overall figure) and population
    mix (nationality zone/gender/age), shown alongside the scorecard purely
    for the underwriter to weigh. Never feeds into the composite score
    itself - most New Business cases have no claims of their own to score
    against, and this is real market context, not a substitute for that
    case's own experience. Returns None whenever Portfolio Analysis isn't
    set up yet (no members/claims/rate card uploaded) - this is optional
    supporting context, not a requirement of scoring a case.
    """
    if results is None:
        results = _book_results(db)
    if not results:
        return None

    quoted_networks = sorted({
        b.network_type for b in case.benefit_plans if b.role == "quoted" and b.network_type
    })

    burning_cost = None
    if quoted_networks:
        by_network = {r["network"]: r for r in summarize_portfolio(results, "network")}
        matched = [by_network[n] for n in quoted_networks if n in by_network and by_network[n].get("burning_cost") is not None]
        if matched:
            burning_cost = {
                "basis": "network",
                "networks": [m["network"] for m in matched],
                "rows": [
                    {
                        "network": m["network"],
                        "burning_cost": m["burning_cost"],
                        "member_count": m["member_count"],
                        "earned_member_years": m["earned_member_years"],
                    }
                    for m in matched
                ],
            }
    if burning_cost is None:
        overall = summarize_burning_cost_overall(results)
        if overall is not None:
            burning_cost = {"basis": "whole_book", **overall}

    population_mix = summarize_population_mix(results)

    if burning_cost is None and population_mix is None:
        return None
    return {"burning_cost": burning_cost, "population_mix": population_mix}


def _active_weight_set(db: Session) -> models.ScoringWeightSet:
    weight_set = db.query(models.ScoringWeightSet).filter_by(is_active=True).first()
    if not weight_set:
        raise HTTPException(status_code=500, detail="No active scoring weight set configured")
    return weight_set


def _census_dicts(case: models.Case) -> List[dict]:
    return [
        {
            "age": c.age,
            "gender": c.gender,
            "marital_status": c.marital_status,
            "relation": c.relation,
            "nationality_zone": c.nationality_zone,
        }
        for c in case.census_records
    ]


def _benefit_dicts(case: models.Case) -> List[dict]:
    # Only the existing/incumbent plan feeds the scorecard - a quoted
    # plan uploaded via /quote for comparison purposes shouldn't move the
    # score of the case as submitted.
    return [
        {
            "annual_limit": b.annual_limit,
            "deductible": b.deductible,
            "co_insurance_pct": b.co_insurance_pct,
            "room_type": b.room_type,
            "network_type": b.network_type,
            "maternity_covered": b.maternity_covered,
            "dental_covered": b.dental_covered,
            "optical_covered": b.optical_covered,
            "pre_existing_covered": b.pre_existing_covered,
            "chronic_covered": b.chronic_covered,
            "member_count": b.member_count,
        }
        for b in case.benefit_plans
        if b.role == "existing"
    ]


def _claims_dicts(case: models.Case) -> List[dict]:
    return [{"amount_paid": c.amount_paid, "policy_year": c.policy_year} for c in case.claims_records]


@router.post("/{case_id}/score", response_model=schemas.ScorecardOut)
def score_case(case_id: int, payload: schemas.ScoreRequest, db: Session = Depends(get_db)):
    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    census = _census_dicts(case)
    if not census:
        raise HTTPException(status_code=400, detail="Case has no census data to score")

    benefit_plans = _benefit_dicts(case)
    if not benefit_plans:
        raise HTTPException(status_code=400, detail="Case has no table of benefits to score")

    claims = _claims_dicts(case)

    weight_set = _active_weight_set(db)
    weights = ScoringWeights(
        w_demographic=weight_set.w_demographic,
        w_claims_experience=weight_set.w_claims_experience,
        w_benefit_richness=weight_set.w_benefit_richness,
        w_industry=weight_set.w_industry,
        zone_multipliers={
            "zone_1_asia": weight_set.zone_1_asia_multiplier,
            "zone_2_middle_east": weight_set.zone_2_middle_east_multiplier,
            "zone_3_europe_americas": weight_set.zone_3_europe_americas_multiplier,
            "zone_4_other": weight_set.zone_4_other_multiplier,
        },
        zone_maternity_multipliers={
            "zone_1_asia": weight_set.zone_1_asia_maternity_multiplier,
            "zone_2_middle_east": weight_set.zone_2_middle_east_maternity_multiplier,
            "zone_3_europe_americas": weight_set.zone_3_europe_americas_maternity_multiplier,
        },
        zone_network_multipliers={
            "zone_1_asia": weight_set.zone_1_asia_network_multiplier,
            "zone_2_middle_east": weight_set.zone_2_middle_east_network_multiplier,
            "zone_3_europe_americas": weight_set.zone_3_europe_americas_network_multiplier,
        },
        overage_age_threshold=weight_set.overage_age_threshold,
        overage_loading_cap=weight_set.overage_loading_cap,
    )

    book_results = _book_results(db)
    result = compute_scorecard(
        census=census,
        benefit_plans=benefit_plans,
        claims=claims,
        industry=case.industry,
        weights=weights,
        estimated_annual_premium=payload.estimated_annual_premium,
        cube=_book_cube(db, book_results),
        loading_pct=_case_loading_pct(case),
    )

    portfolio_reference = _portfolio_reference(db, case, results=book_results)
    if portfolio_reference is not None:
        result["details"]["portfolio_reference"] = portfolio_reference

    scorecard = models.Scorecard(
        case_id=case.id,
        weight_set_id=weight_set.id,
        demographic_risk=result["demographic_risk"],
        claims_experience_risk=result["claims_experience_risk"],
        benefit_richness_risk=result["benefit_richness_risk"],
        industry_risk=result["industry_risk"],
        credibility_factor=result["credibility_factor"],
        composite_score=result["composite_score"],
        risk_tier=result["risk_tier"],
        suggested_loading_pct=result["suggested_loading_pct"],
        details=result["details"],
    )
    db.add(scorecard)
    case.status = models.CaseStatus.SCORED
    db.commit()
    db.refresh(scorecard)
    return scorecard


@router.get("/{case_id}/scorecards", response_model=List[schemas.ScorecardOut])
def list_scorecards(case_id: int, db: Session = Depends(get_db)):
    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return (
        db.query(models.Scorecard)
        .filter_by(case_id=case_id)
        .order_by(models.Scorecard.created_at.desc())
        .all()
    )


@router.get("/{case_id}/scorecard", response_model=schemas.ScorecardOut)
def get_latest_scorecard(case_id: int, db: Session = Depends(get_db)):
    scorecard = (
        db.query(models.Scorecard)
        .filter_by(case_id=case_id)
        .order_by(models.Scorecard.created_at.desc())
        .first()
    )
    if not scorecard:
        raise HTTPException(status_code=404, detail="No scorecard found for this case")
    return scorecard
