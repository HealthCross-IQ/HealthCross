"""Lightweight auto-migration for SQLite: adds any columns a model declares
that an already-existing table is missing.

`Base.metadata.create_all()` only creates tables that don't exist yet - it
never alters a table that's already there, so a persistent local SQLite
file (the default for this app) silently falls behind every time a model
gains a new column, producing "no such column" errors instead of a clear
migration prompt. This isn't a substitute for a real migration tool
(Alembic) on a multi-user/production database, but for a single-file
SQLite dev database it's enough to keep an existing case history usable
across updates without the user needing to delete their data.
"""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeMeta


def auto_migrate_missing_columns(engine: Engine, base: DeclarativeMeta) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    # Collected here rather than run immediately after each ALTER TABLE -
    # interleaving ALTER TABLE (DDL) and UPDATE (DML) statement-by-statement
    # confuses SQLite/pysqlite's legacy autocommit transaction tracking
    # enough to silently drop some of the LATER ALTER TABLEs once the
    # transaction commits (reproduced directly: with the two interleaved,
    # only the first couple of a table's new columns actually persisted;
    # with all ALTER TABLEs run to completion first, every one persists).
    # So every ALTER TABLE runs to completion first, and only once they're
    # all done does a second pass backfill existing rows.
    #
    # Queued for EVERY column with a scalar Python-side default (e.g.
    # Column(Float, default=1.0)) - not just ones added in THIS run. A
    # column added by an OLDER version of this function (before it did any
    # backfilling at all) is already sitting in the schema as "existing",
    # so it would otherwise never be revisited and its NULLs would never
    # get fixed no matter how many times this runs afterward. The `WHERE
    # column IS NULL` below makes this safe to run unconditionally on every
    # startup, for every column, whether newly added or long-standing -
    # a real recalibrated/manually-edited value is never NULL, so it's
    # never touched; only a genuinely-unset row is.
    backfills: list = []  # (table_name, column_name, default_value)

    with engine.begin() as conn:
        for table in base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # a brand-new table; create_all() already added it

            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing_columns:
                    col_type = column.type.compile(dialect=conn.dialect)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'))

                default = column.default
                if default is not None and default.is_scalar:
                    backfills.append((table.name, column.name, default.arg))

    if backfills:
        with engine.begin() as conn:
            for table_name, column_name, default_value in backfills:
                conn.execute(
                    text(f'UPDATE "{table_name}" SET "{column_name}" = :default WHERE "{column_name}" IS NULL'),
                    {"default": default_value},
                )
