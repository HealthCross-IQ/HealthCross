"""Tests for app/main.py's startup seeding of informed defaults - the
nationality-zone/overage priors agreed with underwriting (rather than
neutral 1.0 placeholders) and the insurer -> suggested Product tier
mapping. Uses a fresh temp SQLite DB per test (not the real conftest
`client` fixture) since this exercises app.main's own module-level
SessionLocal directly, the same way its real startup lifespan does.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import db_models as models


def _fresh_session_local(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'seed_test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_ensure_default_weight_set_seeds_informed_zone_and_overage_defaults(tmp_path, monkeypatch):
    import app.main as main

    session_local = _fresh_session_local(tmp_path)
    monkeypatch.setattr(main, "SessionLocal", session_local)

    main._ensure_default_weight_set()

    db = session_local()
    active = db.query(models.ScoringWeightSet).filter_by(is_active=True).first()
    assert active.version == 1
    assert active.zone_1_asia_multiplier == main._SEEDED_ZONE_1_ASIA_MULTIPLIER
    assert active.zone_2_middle_east_maternity_multiplier == main._SEEDED_ZONE_2_MIDDLE_EAST_MATERNITY_MULTIPLIER
    assert active.zone_3_europe_americas_network_multiplier == main._SEEDED_ZONE_3_EUROPE_AMERICAS_NETWORK_MULTIPLIER
    db.close()


def test_ensure_default_weight_set_backfills_an_existing_version_1_set(tmp_path, monkeypatch):
    """An installation that already had a weight set created before these
    seeded values were added (still on the old neutral 1.0 placeholders,
    never recalibrated) should get backfilled on the next startup.
    """
    import app.main as main

    session_local = _fresh_session_local(tmp_path)
    monkeypatch.setattr(main, "SessionLocal", session_local)

    db = session_local()
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

    main._ensure_default_weight_set()

    db = session_local()
    active = db.query(models.ScoringWeightSet).filter_by(is_active=True).first()
    assert active.zone_1_asia_multiplier == main._SEEDED_ZONE_1_ASIA_MULTIPLIER
    db.close()


def test_ensure_default_weight_set_never_touches_a_recalibrated_set(tmp_path, monkeypatch):
    """version > 1 means recalibration (or a manual PATCH) has already
    happened - startup must never overwrite real, learned/adjusted values.
    """
    import app.main as main

    session_local = _fresh_session_local(tmp_path)
    monkeypatch.setattr(main, "SessionLocal", session_local)

    db = session_local()
    db.add(
        models.ScoringWeightSet(
            version=2,
            w_demographic=0.30,
            w_claims_experience=0.35,
            w_benefit_richness=0.20,
            w_industry=0.15,
            zone_1_asia_multiplier=0.5,
            is_active=True,
        )
    )
    db.commit()
    db.close()

    main._ensure_default_weight_set()

    db = session_local()
    active = db.query(models.ScoringWeightSet).filter_by(is_active=True).first()
    assert active.zone_1_asia_multiplier == 0.5  # untouched
    db.close()


def test_ensure_default_insurer_tier_preferences_seeds_once(tmp_path, monkeypatch):
    import app.main as main

    session_local = _fresh_session_local(tmp_path)
    monkeypatch.setattr(main, "SessionLocal", session_local)

    main._ensure_default_insurer_tier_preferences()

    db = session_local()
    prefs = {p.insurer_name: p.suggested_product for p in db.query(models.InsurerTierPreference).all()}
    assert prefs["Allianz"] == "Platinum"
    assert prefs["Cigna Smart Care"] == "Silver"
    assert prefs["Orient"] == "Bronze"
    db.close()


def test_ensure_default_insurer_tier_preferences_never_overwrites_admin_edits(tmp_path, monkeypatch):
    import app.main as main

    session_local = _fresh_session_local(tmp_path)
    monkeypatch.setattr(main, "SessionLocal", session_local)

    db = session_local()
    db.add(models.InsurerTierPreference(insurer_name="Allianz", suggested_product="Gold"))  # admin overrode the seed
    db.commit()
    db.close()

    main._ensure_default_insurer_tier_preferences()

    db = session_local()
    prefs = db.query(models.InsurerTierPreference).all()
    assert len(prefs) == 1
    assert prefs[0].suggested_product == "Gold"
    db.close()
