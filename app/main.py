from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api import (
    routes_admin,
    routes_analysis,
    routes_cases,
    routes_feedback,
    routes_new_business_rating,
    routes_portfolio_analysis,
    routes_reference_plans,
    routes_scoring,
)
from app.database import Base, SessionLocal, engine
from app.db_migrate import auto_migrate_missing_columns
from app.models import db_models as models

STATIC_DIR = Path(__file__).parent / "static"


# Informed starting points for the nationality-zone factors and the
# over-50 loading, agreed with underwriting rather than left neutral (1.0)
# to wait on the recalibration loop from scratch - still fully adjustable
# afterward, either by real outcomes accumulating (app/feedback/
# recalibration.py) or directly via PATCH /admin/weights/active.
_SEEDED_ZONE_1_ASIA_MULTIPLIER = 0.90  # very favorable - lowest risk zone
_SEEDED_ZONE_3_EUROPE_AMERICAS_MULTIPLIER = 0.95  # mildly favorable - second-best zone
_SEEDED_ZONE_2_MIDDLE_EAST_MULTIPLIER = 1.15  # highest-risk zone
_SEEDED_ZONE_2_MIDDLE_EAST_MATERNITY_MULTIPLIER = 1.15  # Arab/Middle East maternity exposure -> risky
_SEEDED_ZONE_3_EUROPE_AMERICAS_NETWORK_MULTIPLIER = 1.20  # Europe/Americas on a rich network -> risky

# Demographic and claims experience are the most reliably-captured signals
# a case actually has (census data is always real; industry classification
# often isn't captured correctly), so industry carries less weight than an
# even split would give it, with that difference going to demographic.
_SEEDED_W_DEMOGRAPHIC = 0.35
_SEEDED_W_CLAIMS_EXPERIENCE = 0.35
_SEEDED_W_BENEFIT_RICHNESS = 0.20
_SEEDED_W_INDUSTRY = 0.10


def _ensure_default_weight_set() -> None:
    db = SessionLocal()
    try:
        active = db.query(models.ScoringWeightSet).filter_by(is_active=True).first()
        if not active:
            db.add(
                models.ScoringWeightSet(
                    version=1,
                    w_demographic=_SEEDED_W_DEMOGRAPHIC,
                    w_claims_experience=_SEEDED_W_CLAIMS_EXPERIENCE,
                    w_benefit_richness=_SEEDED_W_BENEFIT_RICHNESS,
                    w_industry=_SEEDED_W_INDUSTRY,
                    zone_1_asia_multiplier=_SEEDED_ZONE_1_ASIA_MULTIPLIER,
                    zone_2_middle_east_multiplier=_SEEDED_ZONE_2_MIDDLE_EAST_MULTIPLIER,
                    zone_3_europe_americas_multiplier=_SEEDED_ZONE_3_EUROPE_AMERICAS_MULTIPLIER,
                    zone_2_middle_east_maternity_multiplier=_SEEDED_ZONE_2_MIDDLE_EAST_MATERNITY_MULTIPLIER,
                    zone_3_europe_americas_network_multiplier=_SEEDED_ZONE_3_EUROPE_AMERICAS_NETWORK_MULTIPLIER,
                    is_active=True,
                    notes="Initial baseline weights, seeded with underwriting judgment on nationality-zone factors",
                )
            )
            db.commit()
        elif active.version == 1:
            # Still the very first auto-created weight set (recalibration
            # always increments the version, so version 1 means it's never
            # been touched) - an installation that already exists from
            # before these seeded values were added would otherwise be
            # stuck on the old neutral (1.0) placeholders forever.
            active.w_demographic = _SEEDED_W_DEMOGRAPHIC
            active.w_claims_experience = _SEEDED_W_CLAIMS_EXPERIENCE
            active.w_benefit_richness = _SEEDED_W_BENEFIT_RICHNESS
            active.w_industry = _SEEDED_W_INDUSTRY
            active.zone_1_asia_multiplier = _SEEDED_ZONE_1_ASIA_MULTIPLIER
            active.zone_2_middle_east_multiplier = _SEEDED_ZONE_2_MIDDLE_EAST_MULTIPLIER
            active.zone_3_europe_americas_multiplier = _SEEDED_ZONE_3_EUROPE_AMERICAS_MULTIPLIER
            active.zone_2_middle_east_maternity_multiplier = _SEEDED_ZONE_2_MIDDLE_EAST_MATERNITY_MULTIPLIER
            active.zone_3_europe_americas_network_multiplier = _SEEDED_ZONE_3_EUROPE_AMERICAS_NETWORK_MULTIPLIER
            db.commit()
    finally:
        db.close()


# Existing-insurer -> suggested starting Product tier for the New Business
# tier-ladder comparison (see app/reference/product_tiers.py). Seeded once;
# admin-editable afterward via the /admin/insurer-tier-preferences
# endpoints as underwriting's own view of each insurer's typical
# positioning shifts, without a code change.
_SEEDED_INSURER_TIER_PREFERENCES = {
    "Allianz": "Platinum",
    "Cigna Global Care": "Platinum",
    "BUPA": "Platinum",
    "Cigna Smart Care": "Silver",
    "Max Health": "Silver",
    "Metlife": "Silver",
    "Hansemekur": "Silver",
    "April": "Silver",
    "MSH": "Silver",
    "Orient": "Bronze",
    "Daman": "Bronze",
    "Sukoon": "Bronze",
    "Liva": "Bronze",
}


def _ensure_default_insurer_tier_preferences() -> None:
    db = SessionLocal()
    try:
        if db.query(models.InsurerTierPreference).first():
            return  # already seeded (or since admin-edited) - never overwrite
        db.add_all(
            [
                models.InsurerTierPreference(insurer_name=name, suggested_product=tier)
                for name, tier in _SEEDED_INSURER_TIER_PREFERENCES.items()
            ]
        )
        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    auto_migrate_missing_columns(engine, Base)
    _ensure_default_weight_set()
    _ensure_default_insurer_tier_preferences()
    yield


app = FastAPI(title="HealthCross Underwriting Intelligence", version="0.1.0", lifespan=lifespan)

app.include_router(routes_cases.router)
app.include_router(routes_scoring.router)
app.include_router(routes_feedback.router)
app.include_router(routes_admin.router)
app.include_router(routes_analysis.router)
app.include_router(routes_reference_plans.router)
app.include_router(routes_new_business_rating.router)
app.include_router(routes_portfolio_analysis.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def serve_ui():
    return FileResponse(STATIC_DIR / "index.html")
