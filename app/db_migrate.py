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

    with engine.begin() as conn:
        for table in base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # a brand-new table; create_all() already added it

            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                col_type = column.type.compile(dialect=conn.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'))
