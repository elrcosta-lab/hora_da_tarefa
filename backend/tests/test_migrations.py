"""Anti-drift modelo ↔ migrations: toda coluna da migration existe no modelo e vice-versa.

O bug que motivou: AppUser sem updated_at no modelo passava em sqlite
(create_all) e quebrava em Postgres (migration NOT NULL).
"""
import os

import pytest
from sqlalchemy import create_engine, inspect


def test_metadata_matches_migrated_schema(tmp_path):
    from alembic.config import Config
    from alembic import command

    db = tmp_path / "drift.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db}"
    cfg = Config("backend/alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    from app.models import Base

    engine = create_engine(f"sqlite:///{db}")
    insp = inspect(engine)
    try:
        for table in Base.metadata.tables.values():
            if table.name == "alembic_version":
                continue
            db_cols = {c["name"] for c in insp.get_columns(table.name)}
            model_cols = set(table.columns.keys())
            assert model_cols == db_cols, f"{table.name}: modelo={sorted(model_cols)} banco={sorted(db_cols)}"
    finally:
        engine.dispose()
        del os.environ["DATABASE_URL"]
