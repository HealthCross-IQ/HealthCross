from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import routes_admin, routes_cases, routes_feedback, routes_scoring
from app.database import Base, SessionLocal, engine
from app.models import db_models as models


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _ensure_default_weight_set()
    yield


app = FastAPI(title="HealthCross Underwriting Intelligence", version="0.1.0", lifespan=lifespan)

app.include_router(routes_cases.router)
app.include_router(routes_scoring.router)
app.include_router(routes_feedback.router)
app.include_router(routes_admin.router)


@app.get("/health")
def health():
    return {"status": "ok"}
