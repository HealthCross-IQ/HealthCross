from sqlalchemy import create_engine, inspect, text

from app.database import Base
from app.db_migrate import auto_migrate_missing_columns


def test_auto_migrate_adds_missing_columns_without_touching_existing_data():
    engine = create_engine("sqlite:///:memory:")

    # Simulate a persistent DB created before `source_format` /
    # `standard_summary` / `raw_ocr_text` existed on the model: only the
    # original columns, plus one row of real data that must survive.
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE cases (id INTEGER PRIMARY KEY)"))
        conn.execute(
            text(
                "CREATE TABLE benefit_plans ("
                "id INTEGER PRIMARY KEY, case_id INTEGER, plan_name TEXT, "
                "annual_limit FLOAT, deductible FLOAT DEFAULT 0"
                ")"
            )
        )
        conn.execute(
            text("INSERT INTO benefit_plans (id, case_id, plan_name, annual_limit) VALUES (1, 1, 'Premier', 500000)")
        )

    # create_all() alone would leave benefit_plans untouched since it
    # already exists - this is the bug auto_migrate_missing_columns fixes.
    Base.metadata.create_all(bind=engine)
    auto_migrate_missing_columns(engine, Base)

    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("benefit_plans")}
    for expected in ("source_format", "standard_summary", "raw_ocr_text", "member_count"):
        assert expected in columns

    with engine.begin() as conn:
        row = conn.execute(text("SELECT case_id, plan_name, annual_limit FROM benefit_plans WHERE id = 1")).one()
    assert row == (1, "Premier", 500000)


def test_auto_migrate_is_idempotent():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    # Running twice against an already-current schema must not error.
    auto_migrate_missing_columns(engine, Base)
    auto_migrate_missing_columns(engine, Base)
