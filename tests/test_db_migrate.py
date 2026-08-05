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


def test_auto_migrate_backfills_existing_rows_with_the_columns_default():
    # Regression test: a persistent SQLite DB created before
    # zone_1_asia_network_multiplier (or any other Column(..., default=...))
    # existed has a real row that predates it. ALTER TABLE ADD COLUMN alone
    # leaves that row's new column as a genuine SQL NULL, not the model's
    # default - which used to crash scoring far away in
    # app/scoring/rules/demographic.py the first time that NULL reached an
    # arithmetic expression expecting a float.
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE scoring_weight_sets (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO scoring_weight_sets (id, version) VALUES (1, 1)"))

    Base.metadata.create_all(bind=engine)
    auto_migrate_missing_columns(engine, Base)

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT zone_1_asia_network_multiplier, overage_age_threshold FROM scoring_weight_sets WHERE id = 1")
        ).one()
    assert row == (1.0, 50)


def test_auto_migrate_backfills_a_column_that_already_existed_before_this_backfill_logic_did():
    # Regression test for a gap in the first version of this backfill: it
    # only ever queued a backfill for a column ADDED during that specific
    # run. A column that was added by an OLDER copy of this same function
    # (i.e. before it did any backfilling at all) is already sitting in
    # the schema as "existing" on the next run, so it would never be
    # revisited and its NULLs would never get fixed no matter how many
    # times auto_migrate_missing_columns ran afterward - exactly what kept
    # happening on a real, long-lived database. Every column with a scalar
    # default must be backfilled whether it's newly added THIS run or one
    # that's existed for a while.
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        # overage_age_threshold already exists here (unlike the test
        # above, where scoring_weight_sets starts with only id/version) -
        # simulating a schema an OLDER auto_migrate_missing_columns already
        # added this column to, before it backfilled anything.
        conn.execute(text(
            "CREATE TABLE scoring_weight_sets (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, "
            "overage_age_threshold INTEGER)"
        ))
        conn.execute(text("INSERT INTO scoring_weight_sets (id, version, overage_age_threshold) VALUES (1, 1, NULL)"))

    Base.metadata.create_all(bind=engine)
    auto_migrate_missing_columns(engine, Base)

    with engine.begin() as conn:
        row = conn.execute(text("SELECT overage_age_threshold FROM scoring_weight_sets WHERE id = 1")).one()
    assert row == (50,)


def test_auto_migrate_never_overwrites_a_real_non_null_value():
    # A recalibrated/manually-edited value is a real number, never NULL -
    # the backfill's WHERE column IS NULL must leave it alone.
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE scoring_weight_sets (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, "
            "overage_age_threshold INTEGER)"
        ))
        conn.execute(text("INSERT INTO scoring_weight_sets (id, version, overage_age_threshold) VALUES (1, 1, 65)"))

    Base.metadata.create_all(bind=engine)
    auto_migrate_missing_columns(engine, Base)

    with engine.begin() as conn:
        row = conn.execute(text("SELECT overage_age_threshold FROM scoring_weight_sets WHERE id = 1")).one()
    assert row == (65,)
