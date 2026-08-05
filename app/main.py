import datetime
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api import (
    routes_admin,
    routes_analysis,
    routes_cases,
    routes_feedback,
    routes_finance,
    routes_reference_plans,
    routes_scoring,
)
from app.database import Base, SessionLocal, engine
from app.db_migrate import auto_migrate_missing_columns
from app.models import db_models as models

STATIC_DIR = Path(__file__).parent / "static"


def _ensure_default_weight_set() -> None:
    db = SessionLocal()
    try:
        if not db.query(models.ScoringWeightSet).filter_by(is_active=True).first():
            db.add(
                models.ScoringWeightSet(
                    version=1,
                    w_demographic=0.30,
                    w_claims_experience=0.35,
                    w_benefit_richness=0.20,
                    w_industry=0.15,
                    is_active=True,
                    notes="Initial baseline weights",
                )
            )
            db.commit()
    finally:
        db.close()


# The 4 rates HealthCross earns as % of premium (excl. VAT), by sales
# channel and plan tier band - see app/finance/fee_engine.py. Group/
# case-to-case business isn't seeded here at all since it's always a
# manual per-row rate, not a rate-card lookup.
_DEFAULT_FEE_RATES = [
    ("broker", "bronze_silver", 0.065),
    ("broker", "gold_platinum", 0.05),
    ("direct", "bronze_silver", 0.115),
    ("direct", "gold_platinum", 0.10),
]


def _ensure_default_fee_rate_cards() -> None:
    db = SessionLocal()
    try:
        if not db.query(models.FeeRateCard).filter_by(is_active=True).first():
            today = datetime.date.today()
            for channel, tier_band, fee_pct in _DEFAULT_FEE_RATES:
                db.add(
                    models.FeeRateCard(
                        channel=channel,
                        tier_band=tier_band,
                        fee_pct=fee_pct,
                        effective_from=today,
                        is_active=True,
                        notes="Initial baseline rate card",
                    )
                )
            db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    auto_migrate_missing_columns(engine, Base)
    _ensure_default_weight_set()
    _ensure_default_fee_rate_cards()
    yield


app = FastAPI(title="HealthCross Underwriting Intelligence", version="0.1.0", lifespan=lifespan)

app.include_router(routes_cases.router)
app.include_router(routes_scoring.router)
app.include_router(routes_feedback.router)
app.include_router(routes_admin.router)
app.include_router(routes_analysis.router)
app.include_router(routes_reference_plans.router)
app.include_router(routes_finance.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def serve_ui():
    return FileResponse(STATIC_DIR / "index.html")
