import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import db_models as models


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    db = testing_session_local()
    db.add(
        models.ScoringWeightSet(
            version=1,
            w_demographic=0.30,
            w_claims_experience=0.35,
            w_benefit_richness=0.20,
            w_industry=0.15,
            is_active=True,
        )
    )
    db.commit()
    db.close()

    test_client = TestClient(app)
    test_client.db_session_local = testing_session_local  # lets tests insert fixture rows directly
    yield test_client

    app.dependency_overrides.clear()
