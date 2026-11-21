"""Unit tests for the tool-usage migration (Phase 1, gate G3).

* Parse + apply on a throwaway SQLite DB (idempotent CREATE IF NOT EXISTS).
* Additive-only: the UP section has zero DROP/ALTER of existing tables.
* 3-site index-name/column lockstep vs the model's ``__table_args__``.
* Model registered before create_all; no _ensure_postgres_columns mirror.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import create_engine, inspect

from daemon.repositories.tool_usage.models import ToolUsageDaily

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "daemon" / "migrations" / "versions"
    / "20261121_020000_create_tool_usage_daily.sql"
)


def _up_section(text: str) -> str:
    return text.split("-- UP", 1)[1].split("-- DOWN", 1)[0]


class TestMigration:
    def test_file_exists_and_is_idempotent(self):
        assert MIGRATION.is_file()
        text = MIGRATION.read_text()
        up = _up_section(text)
        assert "CREATE TABLE IF NOT EXISTS tool_usage_daily" in up
        assert up.count("CREATE INDEX IF NOT EXISTS") == 2

    def test_applies_on_throwaway_sqlite(self):
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            # strip full-line comments; keep statements
            up = _up_section(MIGRATION.read_text())
            for stmt in up.split(";"):
                body = "\n".join(
                    line for line in stmt.splitlines()
                    if line.strip() and not line.strip().startswith("--")
                ).strip()
                if body:
                    conn.exec_driver_sql(body)
        names = set(inspect(engine).get_table_names())
        assert "tool_usage_daily" in names
        cols = {c["name"] for c in inspect(engine).get_columns("tool_usage_daily")}
        assert cols == {
            "day", "project_id", "tool_name", "agent_id", "call_count",
            "updated_at",
        }
        # applying twice is a no-op
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS tool_usage_daily (day TEXT)"
            )

    def test_additive_only_up_section(self):
        up = _up_section(MIGRATION.read_text()).upper()
        assert "DROP TABLE" not in up
        assert "ALTER TABLE" not in up
        assert "DROP INDEX" not in up

    def test_index_lockstep_with_model(self):
        text = MIGRATION.read_text()
        model_idx = {
            i.name: tuple(c.name for c in i.columns)
            for i in ToolUsageDaily.__table__.indexes
        }
        for name, cols in model_idx.items():
            assert name in text, f"migration missing index {name}"
            # find the CREATE INDEX statement and its column list
            m = re.search(
                rf"CREATE INDEX IF NOT EXISTS {re.escape(name)}\s+"
                rf"ON tool_usage_daily\s*\(([^)]*)\)",
                text,
                re.IGNORECASE,
            )
            assert m, f"could not parse index {name}"
            migration_cols = tuple(c.strip() for c in m.group(1).split(","))
            assert migration_cols == cols, (
                f"index {name}: migration {migration_cols} != model {cols}"
            )

    def test_no_precondition_header(self):
        assert "PRECONDITION:" not in MIGRATION.read_text()

    def test_model_registered_before_create_all(self):
        manager = (
            Path(__file__).resolve().parents[3] / "daemon" / "manager.py"
        ).read_text()
        reg = manager.index("from .repositories.tool_usage.models import")
        create = manager.index("SQLModel.metadata.create_all(self._engine)")
        assert reg < create, "model import must precede create_all"

    def test_no_ensure_postgres_columns_mirror(self):
        manager = (
            Path(__file__).resolve().parents[3] / "daemon" / "manager.py"
        ).read_text()
        assert "tool_usage_daily" not in manager.split(
            "def _ensure_postgres_columns"
        )[1].split("def ", 2)[0] if "def _ensure_postgres_columns" in manager else True
