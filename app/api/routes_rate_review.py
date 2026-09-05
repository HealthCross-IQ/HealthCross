"""The monthly rate review - see app/scoring/rules/rate_review.py for the
arithmetic. These endpoints hold the three stored things around it: the
parameters the review is judged against, the decisions the team agreed,
and the monthly snapshots each review is validated against.
"""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.book import analysis as book_analysis
from app.book import repository as book_repo
from app.database import get_db
from app.models import db_models as models
from app.scoring.rules import rate_review as rr

router = APIRouter(prefix="/new-business/rate-review", tags=["rate-review"])


# ---------------------------------------------------------------- storage

def stored_parameters(db: Session) -> dict:
    rows = db.query(models.RateReviewParameter).all()
    return rr.parameters_with_defaults({r.key: r.value for r in rows})


def _decision_dict(d: models.RateReviewDecision) -> dict:
    return {
        "id": d.id, "product": d.product, "network_scope": d.network_scope, "network": d.network,
        "from_age": d.from_age, "to_age": d.to_age, "gender": d.gender, "action": d.action,
        "change_pct": d.change_pct, "note": d.note,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def stored_decisions(db: Session, product: Optional[str] = None) -> List[dict]:
    """The agreed decisions. An empty table is seeded once with the
    decisions agreed on the August 2026 Bronze review, so the screen
    opens on the team's actual position; after that the rows are the
    only source."""
    if db.query(models.RateReviewDecision.id).first() is None:
        db.add_all([models.RateReviewDecision(**d) for d in rr.SEED_DECISIONS])
        db.commit()
    q = db.query(models.RateReviewDecision)
    if product:
        q = q.filter(models.RateReviewDecision.product == product)
    return [_decision_dict(d) for d in q.order_by(models.RateReviewDecision.from_age, models.RateReviewDecision.gender).all()]


def _snapshot_dict(s: models.RateReviewSnapshot, with_cells: bool = False) -> dict:
    out = {
        "id": s.id, "product": s.product, "network_scope": s.network_scope, "network": s.network,
        "data_as_of": s.data_as_of.isoformat() if s.data_as_of else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "summary": s.summary, "parameters": s.parameters,
    }
    if with_cells:
        out["cells"] = s.cells
    return out


def _last_snapshot(db: Session, product: str, scope: str, network: Optional[str]) -> Optional[dict]:
    q = (
        db.query(models.RateReviewSnapshot)
        .filter(models.RateReviewSnapshot.product == product, models.RateReviewSnapshot.network_scope == scope)
    )
    q = q.filter(models.RateReviewSnapshot.network == network) if network else q.filter(models.RateReviewSnapshot.network.is_(None))
    s = q.order_by(models.RateReviewSnapshot.created_at.desc(), models.RateReviewSnapshot.id.desc()).first()
    return _snapshot_dict(s, with_cells=True) if s else None


# ---------------------------------------------------------------- review

def _build_review(db: Session, product: str, network_scope: str, network: Optional[str], region: Optional[str], as_of: Optional[date]):
    params = stored_parameters(db)
    if network_scope not in ("all", "excluding", "only"):
        raise HTTPException(status_code=400, detail="network_scope must be all, excluding or only")
    if network_scope == "only" and not network:
        raise HTTPException(status_code=400, detail="network is required when network_scope is 'only'")
    if network_scope == "excluding":
        network = None  # the standard excluding-table: everything but the separate networks
    effective_as_of = as_of or book_repo.stored_as_of(db)
    try:
        results = book_analysis.run_analysis(db, as_of=effective_as_of, require_rate_card=False)
    except HTTPException:
        raise HTTPException(status_code=400, detail="Portfolio Analysis has no membership/claims uploaded - there is no book to review")
    review = rr.review_cells(
        results, product, params, network_scope=network_scope, network=network,
        rate_cards=book_repo.rate_cards(db), region=region,
    )
    if review["totals"]["lives"] == 0:
        raise HTTPException(status_code=404, detail=f"No {product} members in this scope")
    rr.apply_decisions(review, stored_decisions(db, product))
    return review, params, results, effective_as_of


@router.get("")
def get_rate_review(
    product: str = Query("Bronze"),
    network_scope: str = Query("excluding", description="excluding = the product minus the separately-reviewed networks; only = one such network; all = whole product"),
    network: Optional[str] = Query(None),
    region: Optional[str] = Query(None, description="Restrict the card price lookup to one region"),
    as_of: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    review, params, results, effective_as_of = _build_review(db, product, network_scope, network, region, as_of)
    last = _last_snapshot(db, product, review["network_scope"], review["network"])
    members = rr.scope_members(results, product, review["network_scope"], review["network"], params.get("separate_networks") or [])
    review["data_as_of"] = effective_as_of.isoformat() if effective_as_of else None
    review["parameters"] = params
    review["decisions"] = stored_decisions(db, product)
    review["networks"] = rr.network_breakdown(results, product, review["loading_pct"])
    review["relations"] = rr.relation_breakdown(members, review["loading_pct"])
    review["validation"] = rr.validate_against_snapshot(review, last, params, effective_as_of)
    review["last_snapshot"] = {k: v for k, v in last.items() if k != "cells"} if last else None
    review["products"] = sorted({(r.get("product") or "") for r in results if r.get("product")})
    return review


@router.post("/snapshots")
def save_snapshot(
    product: str = Query("Bronze"),
    network_scope: str = Query("excluding"),
    network: Optional[str] = Query(None),
    as_of: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Save this month's review of one scope for next month to compare
    against. Deliberate, not automatic - the history is a record of
    reviews that were actually done."""
    review, params, _, effective_as_of = _build_review(db, product, network_scope, network, None, as_of)
    snap = rr.snapshot_of(review, params, effective_as_of)
    row = models.RateReviewSnapshot(**snap)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _snapshot_dict(row)


@router.get("/snapshots")
def list_snapshots(product: Optional[str] = Query(None), db: Session = Depends(get_db)):
    q = db.query(models.RateReviewSnapshot)
    if product:
        q = q.filter(models.RateReviewSnapshot.product == product)
    return [_snapshot_dict(s) for s in q.order_by(models.RateReviewSnapshot.created_at.desc()).all()]


@router.delete("/snapshots/{snapshot_id}", status_code=204)
def delete_snapshot(snapshot_id: int, db: Session = Depends(get_db)):
    row = db.get(models.RateReviewSnapshot, snapshot_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    db.delete(row)
    db.commit()


# ---------------------------------------------------------------- parameters

class ParametersIn(BaseModel):
    target_loss_ratio: Optional[float] = Field(None, gt=0, lt=2)
    loading_by_product: Optional[dict] = None
    full_credibility_member_years: Optional[float] = Field(None, gt=0)
    min_member_years_to_act: Optional[float] = Field(None, ge=0)
    max_increase_pct: Optional[float] = Field(None, ge=0)
    max_discount_pct: Optional[float] = Field(None, ge=0)
    large_claim_cap: Optional[float] = Field(None, ge=0)
    materiality_pct: Optional[float] = Field(None, ge=0)
    min_relativity: Optional[float] = Field(None, gt=0)
    max_relativity: Optional[float] = Field(None, gt=0)
    age_bands: Optional[List[List[int]]] = None
    separate_networks: Optional[List[str]] = None
    stale_after_days: Optional[int] = Field(None, ge=1)


@router.get("/parameters")
def get_parameters(db: Session = Depends(get_db)):
    rows = {r.key: r for r in db.query(models.RateReviewParameter).all()}
    params = stored_parameters(db)
    return {
        "parameters": params,
        "defaults": rr.DEFAULT_PARAMETERS,
        "updated_at": {k: (rows[k].updated_at.isoformat() if rows[k].updated_at else None) for k in rows},
    }


@router.put("/parameters")
def put_parameters(body: ParametersIn, db: Session = Depends(get_db)):
    """Only the keys sent are changed. A key set to its default is still
    stored - the reviewer chose it, and the updated_at says when."""
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No parameters given")
    if "age_bands" in changes:
        bands = sorted(tuple(b) for b in changes["age_bands"])
        if any(len(b) != 2 or b[0] > b[1] for b in bands):
            raise HTTPException(status_code=400, detail="Each age band must be [from, to] with from <= to")
        for (lo1, hi1), (lo2, hi2) in zip(bands, bands[1:]):
            if lo2 <= hi1:
                raise HTTPException(status_code=400, detail=f"Age bands overlap: {lo1}-{hi1} and {lo2}-{hi2}")
        changes["age_bands"] = [list(b) for b in bands]
    if "loading_by_product" in changes and any(not (0 <= float(v) < 1) for v in changes["loading_by_product"].values()):
        raise HTTPException(status_code=400, detail="Loadings must be fractions between 0 and 1")
    if "min_relativity" in changes or "max_relativity" in changes:
        current = stored_parameters(db)
        lo = changes.get("min_relativity", current["min_relativity"])
        hi = changes.get("max_relativity", current["max_relativity"])
        if lo >= hi:
            raise HTTPException(status_code=400, detail="min_relativity must be below max_relativity")
    existing = {r.key: r for r in db.query(models.RateReviewParameter).all()}
    for key, value in changes.items():
        if key in existing:
            existing[key].value = value
        else:
            db.add(models.RateReviewParameter(key=key, value=value))
    db.commit()
    return get_parameters(db)


@router.delete("/parameters/{key}", status_code=204)
def reset_parameter(key: str, db: Session = Depends(get_db)):
    """Back to the code default for one parameter."""
    if key not in rr.DEFAULT_PARAMETERS:
        raise HTTPException(status_code=404, detail=f"Unknown parameter '{key}'")
    db.query(models.RateReviewParameter).filter(models.RateReviewParameter.key == key).delete()
    db.commit()


# ---------------------------------------------------------------- decisions

class DecisionIn(BaseModel):
    product: str
    network_scope: str = "excluding"
    network: Optional[str] = None
    from_age: int = Field(ge=0)
    to_age: int = Field(ge=0)
    gender: Optional[str] = None
    action: str = "hold"
    change_pct: float = 0.0
    note: Optional[str] = None


def _validate_decision(body: DecisionIn) -> dict:
    if body.network_scope not in ("all", "excluding", "only"):
        raise HTTPException(status_code=400, detail="network_scope must be all, excluding or only")
    if body.network_scope == "only" and not body.network:
        raise HTTPException(status_code=400, detail="network is required for an 'only' decision")
    if body.from_age > body.to_age:
        raise HTTPException(status_code=400, detail="from_age must not exceed to_age")
    if body.action not in ("increase", "discount", "hold", "review"):
        raise HTTPException(status_code=400, detail="action must be increase, discount, hold or review")
    gender = (body.gender or "").strip().upper()[:1] or None
    if gender not in (None, "M", "F"):
        raise HTTPException(status_code=400, detail="gender must be M, F or blank for both")
    if body.action == "hold" and body.change_pct:
        raise HTTPException(status_code=400, detail="A hold carries no change")
    if body.action == "increase" and body.change_pct <= 0:
        raise HTTPException(status_code=400, detail="An increase needs a positive change_pct")
    if body.action == "discount" and body.change_pct >= 0:
        raise HTTPException(status_code=400, detail="A discount needs a negative change_pct")
    data = body.model_dump()
    data["gender"] = gender
    data["network"] = body.network if body.network_scope == "only" else None
    return data


@router.get("/decisions")
def get_decisions(product: Optional[str] = Query(None), db: Session = Depends(get_db)):
    return stored_decisions(db, product)


@router.post("/decisions", status_code=201)
def add_decision(body: DecisionIn, db: Session = Depends(get_db)):
    stored_decisions(db)  # seed first, so a first hand-added row does not suppress the seed
    row = models.RateReviewDecision(**_validate_decision(body))
    db.add(row)
    db.commit()
    db.refresh(row)
    return _decision_dict(row)


@router.put("/decisions/{decision_id}")
def update_decision(decision_id: int, body: DecisionIn, db: Session = Depends(get_db)):
    row = db.get(models.RateReviewDecision, decision_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    for key, value in _validate_decision(body).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return _decision_dict(row)


@router.delete("/decisions/{decision_id}", status_code=204)
def delete_decision(decision_id: int, db: Session = Depends(get_db)):
    row = db.get(models.RateReviewDecision, decision_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    db.delete(row)
    db.commit()


# ---------------------------------------------------------------- nationality

def _scope_key(network_scope: str, network: Optional[str]) -> str:
    return f"only:{network}" if network_scope == "only" else network_scope


@router.get("/nationality")
def get_nationality_factors(
    product: str = Query("Bronze"),
    network_scope: str = Query("excluding"),
    network: Optional[str] = Query(None),
    age_band: Optional[str] = Query(None, description="e.g. 26-35; omit for the whole scope"),
    gender: Optional[str] = Query(None, description="M or F; omit for both"),
    as_of: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Nationality inside one reviewed cell: what each nationality costs
    against the cell's own average, and the factor a quote applies
    within that cell. Normalised to 1.0 on today's mix - it never adds
    to the cell's decision, only redistributes inside it."""
    review, params, results, effective_as_of = _build_review(db, product, network_scope, network, None, as_of)
    members = rr.scope_members(results, product, review["network_scope"], review["network"], params.get("separate_networks") or [])
    bands = [tuple(b) for b in params["age_bands"]]
    if age_band:
        members = [m for m in members if rr._band_label(m.get("age"), bands) == age_band]
    if gender:
        g = gender.strip().upper()[:1]
        members = [m for m in members if (m.get("gender") or "").strip().upper()[:1] == g]
    out = rr.nationality_factors(members, params)
    out.update({
        "product": product, "scope_label": review["scope_label"], "age_band": age_band, "gender": gender,
        "lives": len(members), "data_as_of": effective_as_of.isoformat() if effective_as_of else None,
    })
    cell = next((c for c in review["cells"] if c["age_band"] == age_band and c["gender"] == (gender or "").strip().upper()[:1]), None) if age_band and gender else None
    if cell:
        out["cell"] = {k: cell.get(k) for k in ("current_rate", "decision_action", "decision_change_pct", "rate_after_decision", "gross_loss_ratio")}
        base = cell.get("rate_after_decision") or cell.get("current_rate")
        for row in out["nationalities"] + out["zones"]:
            row["rate_after_decision"] = round(base * row["factor"], 2) if base else None
    return out


@router.get("/reviewed-price/{case_id}")
def get_reviewed_price(case_id: int, as_of: Optional[date] = Query(None), db: Session = Depends(get_db)):
    """A case's census priced on the reviewed card: each member's cell
    rate after the agreed decision, times the member's nationality factor
    within that cell. Sits beside the card price and the risk-based price
    on New Quote - it is what the book, and the team's decisions on it,
    say this population should pay."""
    from app.api.routes_new_business_rating import _case_census_dicts, _resolve_auto_quote_categories

    case = db.get(models.Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    census = _case_census_dicts(case)
    if not census:
        raise HTTPException(status_code=400, detail="Upload a census for this case first")
    categories = _resolve_auto_quote_categories(case, db)
    if not categories:
        raise HTTPException(status_code=400, detail="Each census category needs a Product and Network before this can price")
    design = {c["category"]: c for c in categories}
    products = {c.get("product") for c in categories if c.get("product")}
    if len(products) != 1:
        raise HTTPException(status_code=400, detail="The reviewed price needs every category on one product")
    product = products.pop()

    params = stored_parameters(db)
    separate = params.get("separate_networks") or []
    effective_as_of = as_of or book_repo.stored_as_of(db)
    try:
        results = book_analysis.run_analysis(db, as_of=effective_as_of, require_rate_card=False)
    except HTTPException:
        raise HTTPException(status_code=400, detail="Portfolio Analysis has no membership/claims uploaded - there is no book to price against")
    decisions = stored_decisions(db, product)
    rate_cards = book_repo.rate_cards(db)

    priced_census = [{**m, "network": (design.get(m.get("category")) or {}).get("network")} for m in census]
    scopes = {"excluding"} | {f"only:{n}" for n in separate}
    reviews, factors = {}, {}
    bands = [tuple(b) for b in params["age_bands"]]
    for key in scopes:
        scope, network = ("only", key[5:]) if key.startswith("only:") else ("excluding", None)
        review = rr.review_cells(results, product, params, network_scope=scope, network=network, rate_cards=rate_cards)
        if review["totals"]["lives"] == 0:
            continue
        rr.apply_decisions(review, decisions)
        reviews[key] = review
        members = rr.scope_members(results, product, scope, network, separate)
        needed = {(rr._band_label(m.get("age"), bands), (m.get("gender") or "").strip().upper()[:1])
                  for m in priced_census
                  if (f"only:{(m.get('network') or '').strip()}" if rr._is_separate(m.get("network"), separate) else "excluding") == key}
        for band, g in needed:
            if band and g in rr.GENDERS:
                cell_members = [m for m in members if rr._band_label(m.get("age"), bands) == band and (m.get("gender") or "").strip().upper()[:1] == g]
                factors[(key, band, g)] = rr.nationality_factors(cell_members, params)

    out = rr.reviewed_price_for_census(priced_census, reviews, factors, params)
    by_cat = {}
    for m in out["members"]:
        e = by_cat.setdefault(m.get("category") or "-", {"category": m.get("category") or "-", "members": 0, "priced": 0, "reviewed_premium": 0.0})
        e["members"] += 1
        if m["price"] is not None:
            e["priced"] += 1
            e["reviewed_premium"] += m["price"]
    out["categories"] = [{**e, "reviewed_premium": round(e["reviewed_premium"], 2),
                          "product": product, "network": (design.get(e["category"]) or {}).get("network")} for e in by_cat.values()]
    quote = db.query(models.NewBusinessQuote).filter_by(case_id=case_id).order_by(models.NewBusinessQuote.created_at.desc()).first()
    card = quote.result.get("case_gross_annual_premium") if quote and quote.result else None
    out["rate_card_premium"] = card
    out["gap_pct"] = round((out["reviewed_premium"] / card - 1) * 100, 1) if card and out["reviewed_premium"] else None
    out["product"] = product
    out["data_as_of"] = effective_as_of.isoformat() if effective_as_of else None
    out["decisions_count"] = len(decisions)
    return out
