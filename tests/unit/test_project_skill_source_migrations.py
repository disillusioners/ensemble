"""Unit pack: the lane's three ``.sql`` migrations apply on a fresh SQLite DB.

House rule (``daemon/migrations/README.md``): every new SQLModel table gets
its own ``.sql`` file, and every statement must be verified on a THROWAWAY
SQLite database — the runner is SQLite-only (PostgreSQL gets its schema
from ``create_all`` + ``_ensure_postgres_columns``). This pack proves, for
the Phase-1 lane files:

* the three files apply cleanly on a brand-new SQLite database
  (``CREATE TABLE IF NOT EXISTS`` + indexes, in version order);
* a second run is a no-op (idempotent — the ledger suppresses re-apply);
* the expected tables/columns materialize;
* **no FOREIGN KEY clause** appears in any lane migration (A1: the
  dedicated embeddings table must stay FK-free — the whole point of the
  architect amendment);
* the real ``versions/`` directory discovers WITHOUT raising
  ``DuplicateMigrationVersionError`` and contains the three lane prefixes
  exactly once (the duplicate-prefix guard's positive side), pinning the
  lane migration delta == +3.

The pack deliberately scopes the runner at a TEMP directory holding only
the lane files: a full-directory fresh-SQLite boot is broken by the
pre-existing ``20260714_000001`` migration (documented in the migration
blueprint), which is out of this lane's scope.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from daemon.migrations.runner import MigrationRunner

LANE_MIGRATIONS = [
    "20261031_120000_create_project_skill_files.sql",
    "20261031_120001_create_project_skill_embeddings.sql",
    "20261031_120002_create_project_skill_usage_records.sql",
]

LANE_TABLES = [
    "project_skill_files",
    "project_skill_embeddings",
    "project_skill_usage_records",
]

VERSIONS_DIR = Path(__file__).resolve().parents[2] / "daemon" / "migrations" / "versions"


@pytest.fixture
def lane_migrations_dir(tmp_path) -> Path:
    """A temp migrations dir holding ONLY the three lane files."""
    target = tmp_path / "lane-migrations"
    target.mkdir()
    for name in LANE_MIGRATIONS:
        source = VERSIONS_DIR / name
        assert source.is_file(), f"lane migration missing: {source}"
        shutil.copy(source, target / name)
    return target


@pytest.fixture
def fresh_sqlite(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lane.db'}")
    yield engine
    engine.dispose()


class TestFreshSqliteApply:
    def test_all_three_apply_in_version_order(self, fresh_sqlite, lane_migrations_dir):
        runner = MigrationRunner(fresh_sqlite, lane_migrations_dir)
        applied = runner.run_pending_migrations()
        assert applied == [
            "20261031_120000",
            "20261031_120001",
            "20261031_120002",
        ]

    def test_second_run_is_noop(self, fresh_sqlite, lane_migrations_dir):
        runner = MigrationRunner(fresh_sqlite, lane_migrations_dir)
        runner.run_pending_migrations()
        assert runner.run_pending_migrations() == []  # ledger suppresses re-apply

    def test_expected_tables_and_columns_materialize(self, fresh_sqlite, lane_migrations_dir):
        MigrationRunner(fresh_sqlite, lane_migrations_dir).run_pending_migrations()
        insp = inspect(fresh_sqlite)
        present = set(insp.get_table_names())
        for table in LANE_TABLES:
            assert table in present, f"{table} not created on fresh SQLite"
        file_cols = {c["name"] for c in insp.get_columns("project_skill_files")}
        # P1: no body-bearing column exists to write a body into
        assert "body" not in file_cols
        assert "content" not in file_cols
        assert {"id", "project_id", "root_path", "namespace", "name"}.issubset(file_cols)
        assert {
            "id",
            "skill_id",
            "trigger_query",
            "embedding",
            "created_at",
        } == {c["name"] for c in insp.get_columns("project_skill_embeddings")}
        usage_cols = {c["name"] for c in insp.get_columns("project_skill_usage_records")}
        assert {"skill_id", "project_id", "instance_id", "agent_id"}.issubset(usage_cols)

    def test_no_foreign_key_clause_anywhere(self, lane_migrations_dir):
        """A1: the dedicated tables are FK-FREE by construction."""
        for name in LANE_MIGRATIONS:
            sql = (lane_migrations_dir / name).read_text(encoding="utf-8")
            up_raw = sql.split("-- DOWN")[0]
            executable = "\n".join(
                line for line in up_raw.splitlines() if not line.strip().startswith("--")
            )
            assert "FOREIGN KEY" not in executable.upper(), name
            assert "REFERENCES" not in executable.upper(), name
            # additive-only DDL: no destructive statements in the UP body
            assert "DROP TABLE" not in executable.upper(), name

    def test_no_fk_constraint_materialized_on_sqlite(self, fresh_sqlite, lane_migrations_dir):
        """Belt: introspection agrees — zero FKs on the lane tables."""
        MigrationRunner(fresh_sqlite, lane_migrations_dir).run_pending_migrations()
        insp = inspect(fresh_sqlite)
        for table in LANE_TABLES:
            assert insp.get_foreign_keys(table) == [], table


class TestRepoVersionsRegistry:
    def test_discovery_has_no_duplicate_prefix_and_carries_lane_files(self):
        """The duplicate-prefix guard's positive side over the REAL
        versions dir: no ``DuplicateMigrationVersionError``, and the three
        lane prefixes are present exactly once (+3 lane delta)."""
        engine = create_engine("sqlite:///:memory:")
        try:
            runner = MigrationRunner(engine)  # default versions dir
            discovered = runner.discover_migrations()  # raises on duplicate prefix
        finally:
            engine.dispose()
        versions = [m.version for m in discovered]
        assert len(versions) == len(set(versions))  # no duplicates
        for prefix in ("20261031_120000", "20261031_120001", "20261031_120002"):
            assert versions.count(prefix) == 1, prefix

    def test_lane_sql_columns_match_models(self):
        """Two-artifact rule (SQLite side): every model column appears in
        the lane .sql CREATE TABLE (the PG side is pinned in the PG pack
        via information_schema)."""
        from daemon.repositories.project_skill_file import models

        pairs = {
            "project_skill_files": models.ProjectSkillFile,
            "project_skill_embeddings": models.ProjectSkillEmbedding,
            "project_skill_usage_records": models.ProjectSkillUsageRecord,
        }
        by_table = {
            "project_skill_files": "20261031_120000_create_project_skill_files.sql",
            "project_skill_embeddings": "20261031_120001_create_project_skill_embeddings.sql",
            "project_skill_usage_records": "20261031_120002_create_project_skill_usage_records.sql",
        }
        for table, model in pairs.items():
            sql = (VERSIONS_DIR / by_table[table]).read_text(encoding="utf-8").lower()
            for column in model.__table__.columns.keys():
                assert column.lower() in sql, f"{table}.{column} missing from .sql"

    def test_lane_sql_has_no_destructive_up_statements(self):
        """UP section: additive CREATEs only (no ALTER/DROP)."""
        for name in LANE_MIGRATIONS:
            sql = (VERSIONS_DIR / name).read_text(encoding="utf-8")
            up = sql.split("-- DOWN")[0]
            executable = "\n".join(
                line for line in up.splitlines() if not line.strip().startswith("--")
            ).upper()
            assert "CREATE TABLE IF NOT EXISTS" in executable
            assert "CREATE INDEX IF NOT EXISTS" in executable or "CREATE UNIQUE INDEX" in executable
            assert "DROP " not in executable
            assert "ALTER " not in executable


class TestLedgerRows:
    def test_ledger_records_checksums(self, fresh_sqlite, lane_migrations_dir):
        """Every applied lane migration lands a ledger row carrying a
        checksum — the durable audit trail the house convention relies on."""
        MigrationRunner(fresh_sqlite, lane_migrations_dir).run_pending_migrations()
        with fresh_sqlite.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT version, checksum FROM schema_migrations "
                    "WHERE version LIKE '20261031_12%' ORDER BY version"
                )
            ).fetchall()
        assert [r[0] for r in rows] == [
            "20261031_120000",
            "20261031_120001",
            "20261031_120002",
        ]
        assert all(r[1] for r in rows)  # checksum populated
