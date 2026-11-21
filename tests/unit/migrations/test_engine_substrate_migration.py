"""Migration pins for the engine substrate (Engine Arc P1 — T1.1/T1.2/T1.3).

Covers acceptance gates AC1 (two files, distinct prefixes, throwaway
SQLite apply, IF NOT EXISTS, DOWN section, TEXT strings), AC2 (model
~/migration index lockstep, no ``_ensure_postgres_columns`` mirror) and
the migration-DELTA half of AC8 (+2 unique prefixes — asserted as a
DELTA, never an absolute count).

Fence discipline: throwaway in-memory SQLite engines only; no daemon
boot; no PostgreSQL connection; no filesystem writes outside tmp.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from daemon.repositories.engine.models import EngineJob, EngineRun

REPO_ROOT = Path(__file__).resolve().parents[3]
VERSIONS_DIR = REPO_ROOT / "daemon" / "migrations" / "versions"

ENGINE_JOBS_SQL = VERSIONS_DIR / "20261121_120000_create_engine_jobs.sql"
ENGINE_RUNS_SQL = VERSIONS_DIR / "20261121_120001_create_engine_runs.sql"

MANAGER_PY = REPO_ROOT / "daemon" / "manager.py"

MODEL_INDEX_NAMES = {
    "engine_jobs": {
        "uq_engine_jobs_service_tag",
        "idx_engine_jobs_category",
        "idx_engine_jobs_trigger",
    },
    "engine_runs": {
        "idx_engine_runs_job_started",
        "idx_engine_runs_outcome",
    },
}


def _up_section(sql: str) -> str:
    return sql.split("-- UP", 1)[1].split("-- DOWN", 1)[0]


def _down_section(sql: str) -> str:
    return sql.split("-- DOWN", 1)[1]


def _statements(up: str) -> list[str]:
    out: list[str] = []
    for chunk in up.split(";"):
        body = "\n".join(
            line
            for line in chunk.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ).strip()
        if body:
            out.append(body)
    return out


def _apply(sql_path: Path, engine) -> None:
    with engine.begin() as conn:
        for stmt in _statements(_up_section(sql_path.read_text())):
            conn.exec_driver_sql(stmt)


# ── AC1: file shape ────────────────────────────────────────────────────


class TestMigrationFiles:
    def test_both_files_exist_with_distinct_prefixes(self):
        assert ENGINE_JOBS_SQL.is_file()
        assert ENGINE_RUNS_SQL.is_file()
        p1 = ENGINE_JOBS_SQL.name.split("_create_")[0]
        p2 = ENGINE_RUNS_SQL.name.split("_create_")[0]
        assert p1 != p2
        assert p1 == "20261121_120000"
        assert p2 == "20261121_120001"

    def test_engine_prefixes_are_unique_in_the_versions_dir(self):
        """DELTA assertion (never an absolute file count).

        The two engine prefixes must appear exactly once each across the
        whole versions directory, and no OTHER file may share them.
        """
        for prefix in ("20261121_120000", "20261121_120001"):
            hits = [p.name for p in VERSIONS_DIR.glob(f"{prefix}_*.sql")]
            assert len(hits) == 1, f"prefix {prefix} is not unique: {hits}"

    @pytest.mark.parametrize("sql_path", [ENGINE_JOBS_SQL, ENGINE_RUNS_SQL])
    def test_every_create_carries_if_not_exists(self, sql_path):
        up = _up_section(sql_path.read_text())
        creates = re.findall(r"^\s*CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+.*$", up, re.M)
        assert creates, "no CREATE statements found"
        for stmt in creates:
            assert "IF NOT EXISTS" in stmt.upper(), stmt

    @pytest.mark.parametrize("sql_path", [ENGINE_JOBS_SQL, ENGINE_RUNS_SQL])
    def test_down_section_drops_the_table(self, sql_path):
        down = _down_section(sql_path.read_text())
        assert "DROP TABLE IF EXISTS" in down
        up = _up_section(sql_path.read_text())
        assert "DROP TABLE" not in up.upper()

    @pytest.mark.parametrize("sql_path", [ENGINE_JOBS_SQL, ENGINE_RUNS_SQL])
    def test_no_trailing_comment_semicolon(self, sql_path):
        """The SHATTER class only.

        The runner strips FULL-LINE ``--`` comments before splitting on
        ``;`` (house probe ``tests/migration/test_migration_comment_semicolon.py``
        documents the class split), so a semicolon inside a full-line
        comment is inert. A TRAILING (inline) comment is NOT stripped —
        a semicolon there shatters the statement split on every fresh
        SQLite boot. Only the trailing class is pinned here.
        """
        for lineno, raw in enumerate(sql_path.read_text().splitlines(), start=1):
            stripped = raw.lstrip()
            if stripped.startswith("--"):
                continue  # full-line comment: stripped by the runner, inert
            if "--" not in raw:
                continue
            trailing = raw.split("--", 1)[1]
            assert ";" not in trailing, (
                f"{sql_path.name}:{lineno} trailing comment contains ';'"
            )

    @pytest.mark.parametrize("sql_path", [ENGINE_JOBS_SQL, ENGINE_RUNS_SQL])
    def test_no_varchar_in_the_up_section(self, sql_path):
        up = _up_section(sql_path.read_text()).upper()
        assert "VARCHAR" not in up

    def test_engine_jobs_string_columns_are_text(self):
        up = _up_section(ENGINE_JOBS_SQL.read_text())
        string_cols = (
            "id", "service_tag", "service_name", "category", "module_info",
            "description", "metadata", "trigger_kind", "first_tick_kind",
            "event_kind", "event_filter", "timeout_policy",
            "concurrency_class", "kill_switch_key", "last_run_at",
            "last_outcome", "last_skip_reason", "registered_at", "updated_at",
        )
        for col in string_cols:
            m = re.search(rf"^\s*{col}\s+(\w+)", up, re.M)
            assert m, f"column {col} not found in {ENGINE_JOBS_SQL.name}"
            assert m.group(1).upper() == "TEXT", f"{col} typed {m.group(1)}"

    def test_engine_runs_string_columns_are_text(self):
        up = _up_section(ENGINE_RUNS_SQL.read_text())
        string_cols = (
            "job_id", "service_tag", "started_at", "finished_at", "outcome",
            "skip_reason", "error_class", "error_digest", "boot_id",
        )
        for col in string_cols:
            m = re.search(rf"^\s*{col}\s+(\w+)", up, re.M)
            assert m, f"column {col} not found in {ENGINE_RUNS_SQL.name}"
            assert m.group(1).upper() == "TEXT", f"{col} typed {m.group(1)}"


# ── AC1/AC3: throwaway SQLite apply ────────────────────────────────────


class TestThrowawayApply:
    def test_engine_jobs_applies_and_is_idempotent(self):
        engine = create_engine("sqlite://")
        _apply(ENGINE_JOBS_SQL, engine)
        _apply(ENGINE_JOBS_SQL, engine)  # second application must be a no-op
        insp = inspect(engine)
        assert "engine_jobs" in insp.get_table_names()
        assert {i["name"] for i in insp.get_indexes("engine_jobs")} == (
            MODEL_INDEX_NAMES["engine_jobs"]
        )

    def test_engine_runs_applies_and_is_idempotent(self):
        engine = create_engine("sqlite://")
        _apply(ENGINE_RUNS_SQL, engine)
        _apply(ENGINE_RUNS_SQL, engine)
        insp = inspect(engine)
        assert "engine_runs" in insp.get_table_names()
        assert {i["name"] for i in insp.get_indexes("engine_runs")} == (
            MODEL_INDEX_NAMES["engine_runs"]
        )

    def test_both_tables_together(self):
        engine = create_engine("sqlite://")
        _apply(ENGINE_JOBS_SQL, engine)
        _apply(ENGINE_RUNS_SQL, engine)
        names = set(inspect(engine).get_table_names())
        assert names == {"engine_jobs", "engine_runs"}


# ── AC2: 3-site index lockstep (model ↔ migration) ─────────────────────


class TestIndexLockstep:
    def test_engine_jobs_model_indexes_match_migration_byte_exactly(self):
        sql = ENGINE_JOBS_SQL.read_text()
        for index in EngineJob.__table__.indexes:
            assert index.name in sql, f"migration missing index {index.name}"
            m = re.search(
                rf"CREATE\s+(UNIQUE\s+)?INDEX IF NOT EXISTS {re.escape(index.name)}\s+"
                rf"ON\s+engine_jobs\s*\(([^)]*)\)",
                sql,
                re.IGNORECASE,
            )
            assert m, f"could not parse index {index.name} in the migration"
            cols = tuple(c.strip() for c in m.group(2).split(","))
            model_cols = tuple(c.name for c in index.columns)
            assert cols == model_cols, f"{index.name}: {cols} != {model_cols}"
            assert bool(m.group(1)) is bool(index.unique), (
                f"{index.name}: uniqueness differs (migration "
                f"{bool(m.group(1))} vs model {bool(index.unique)})"
            )

    def test_engine_runs_model_indexes_match_migration_byte_exactly(self):
        sql = ENGINE_RUNS_SQL.read_text()
        for index in EngineRun.__table__.indexes:
            assert index.name in sql, f"migration missing index {index.name}"
            m = re.search(
                rf"CREATE\s+(UNIQUE\s+)?INDEX IF NOT EXISTS {re.escape(index.name)}\s+"
                rf"ON\s+engine_runs\s*\(([^)]*)\)",
                sql,
                re.IGNORECASE,
            )
            assert m, f"could not parse index {index.name} in the migration"
            cols = tuple(c.strip() for c in m.group(2).split(","))
            model_cols = tuple(c.name for c in index.columns)
            assert cols == model_cols, f"{index.name}: {cols} != {model_cols}"

    def test_service_tag_is_unique_in_both_sites(self):
        """AMD-7: service_tag UNIQUE from day one, in model AND migration."""
        uniq = {i.name for i in EngineJob.__table__.indexes if i.unique}
        assert "uq_engine_jobs_service_tag" in uniq
        assert (
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_engine_jobs_service_tag"
            in ENGINE_JOBS_SQL.read_text()
        )

    def test_no_plain_service_tag_index_survives(self):
        """The AMD-7 swap REPLACED the plain index — no redundant duplicate.

        Scoped to CREATE INDEX statements (the migration's prose explains
        the swap and legitimately names the old index id).
        """
        up = _up_section(ENGINE_JOBS_SQL.read_text())
        assert "idx_engine_jobs_service_tag" not in up
        assert "idx_engine_jobs_service_tag" not in {
            i.name for i in EngineJob.__table__.indexes
        }

    def test_no_ensure_postgres_columns_mirror(self):
        """Brand-new tables: create_all OWNS them; no ensure entry."""
        manager = MANAGER_PY.read_text()
        assert "def _ensure_postgres_columns" in manager
        body = manager.split("def _ensure_postgres_columns", 1)[1]
        # The next method definition bounds the ensure body.
        body = body.split("\n    def ", 1)[0]
        assert "engine_jobs" not in body
        assert "engine_runs" not in body


# ── AC2: manager registration ordering ─────────────────────────────────


class TestManagerRegistration:
    def test_engine_models_imported_before_create_all(self):
        manager = MANAGER_PY.read_text()
        reg = manager.index("from .repositories.engine.models import")
        create = manager.index("SQLModel.metadata.create_all(self._engine)")
        assert reg < create, "engine models must be registered before create_all"

    def test_registration_block_sits_beside_the_room_block(self):
        manager = MANAGER_PY.read_text()
        room = manager.index("from .repositories.room.models import")
        engine = manager.index("from .repositories.engine.models import")
        create = manager.index("SQLModel.metadata.create_all(self._engine)")
        assert room < engine < create

    def test_models_register_both_tables_on_metadata(self):
        from sqlmodel import SQLModel

        assert "engine_jobs" in SQLModel.metadata.tables
        assert "engine_runs" in SQLModel.metadata.tables
