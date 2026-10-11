"""G2.2 — auth migrations: runner UP/DOWN on scratch SQLite + PG
``create_all`` index parity (oauth2-auth Phase 2, AMEND v1.3 / B5).

The migration runner is deliberately SQLite-ONLY
(``daemon/migrations/runner.py:721-726`` — blueprint invariant), so the
gate splits:

* **SQLite leg** (this file, isolated ``versions/`` dir — the
  ``test_attestation_migration`` precedent): all FOUR auth ``.sql``
  files apply + revert cleanly via ``MigrationRunner``; every ledger
  row registers the file's true SHA-256 checksum; the tables + the two
  sweep indexes exist after UP and are gone after DOWN.
* **PG leg**: ``SQLModel.metadata.create_all`` (the same path the
  manager runs) materializes the four tables on the scratch
  ``ensemble_test`` database, INCLUDING the two indexes — pinned here
  AND by name parity against the ``.sql`` files (the drift_events
  index-parity precedent: the model ``__table_args__`` is the canonical
  source; the ``.sql`` ``CREATE INDEX`` names MUST be byte-identical).

Also pins the plan-overview §12 Confirm-at-implementation item 1
answer: comment-labeled EMPTY reserved stubs (the D5.5 form) apply as
no-ops WITH a true checksum registered, and roll back cleanly — a
zero-byte ``-- DOWN`` section (no label comment) would refuse rollback
("has no DOWN section").
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from daemon.migrations.runner import (
    MigrationError,
    MigrationFile,
    MigrationRunner,
)
from daemon.repositories.auth_session.models import (
    AuthBootstrapConsumption,
    AuthSession,
    AuthState,
    BearerToken,
)
from tests.unit.auth_pg_scratch_helper import (
    scratch_pg_engine_or_skip,  # noqa: F401 — skip-if-unreachable PG leg
)

_VERSIONS_DIR = Path("daemon/migrations/versions")
AUTH_MIGRATIONS = [
    "20261011_030000_create_auth_sessions.sql",
    "20261011_030001_create_auth_states.sql",
    "20261011_030002_create_auth_bearer_tokens.sql",
    "20261011_030003_create_auth_bootstrap_consumption.sql",
]
AUTH_TABLE_NAMES = [
    "auth_sessions",
    "auth_states",
    "auth_bearer_tokens",
    "auth_bootstrap_consumption",
]


def _version(filename: str) -> str:
    """The runner's version = the YYYYMMDD_HHMMSS prefix, not the stem."""
    return filename[:15]
EXPECTED_INDEXES = {
    "auth_sessions": "idx_auth_sessions_expires_at",
    "auth_bearer_tokens": "idx_auth_bearer_tokens_expires_at",
}


@pytest.fixture
def isolated_auth_versions_dir() -> Iterator[Path]:
    """Temp ``versions/`` dir holding ONLY the four auth migrations
    (house precedent: a failing test must point at the auth files, not
    at unrelated PG-only migration syntax)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        versions_dir = Path(tmpdir) / "versions"
        versions_dir.mkdir()
        for name in AUTH_MIGRATIONS:
            shutil.copy(_VERSIONS_DIR / name, versions_dir / name)
        yield versions_dir


@pytest.fixture
def fresh_sqlite(isolated_auth_versions_dir) -> Iterator[Engine]:
    """Fresh in-memory SQLite bound to the isolated dir (StaticPool so
    the runner's separate Sessions share the one database)."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    engine.dispose()


class TestRunnerUpAndDownSqlite:
    def test_all_four_apply_cleanly(self, fresh_sqlite, isolated_auth_versions_dir):
        runner = MigrationRunner(fresh_sqlite, migrations_dir=isolated_auth_versions_dir)
        applied = runner.run_pending_migrations()
        assert set(applied) == {_version(n) for n in AUTH_MIGRATIONS}

        inspector = inspect(fresh_sqlite)
        for table in AUTH_TABLE_NAMES:
            assert inspector.has_table(table), table
        # The two sweep indexes materialize on the SQLite path too.
        for table, index in EXPECTED_INDEXES.items():
            index_names = {ix["name"] for ix in inspector.get_indexes(table)}
            assert index in index_names, f"{index} missing on {table}"

    def test_checksums_registered(self, fresh_sqlite, isolated_auth_versions_dir):
        runner = MigrationRunner(fresh_sqlite, migrations_dir=isolated_auth_versions_dir)
        runner.run_pending_migrations()
        with fresh_sqlite.connect() as conn:
            rows = conn.execute(
                text("SELECT version, checksum FROM schema_migrations")
            ).fetchall()
        by_version = dict(rows)
        for name in AUTH_MIGRATIONS:
            expected = hashlib.sha256(
                (_VERSIONS_DIR / name).read_bytes()
            ).hexdigest()
            assert by_version[_version(name)] == expected, name

    def test_all_four_revert_cleanly(self, fresh_sqlite, isolated_auth_versions_dir):
        runner = MigrationRunner(fresh_sqlite, migrations_dir=isolated_auth_versions_dir)
        runner.run_pending_migrations()
        # Reverse order (tidy teardown semantics).
        for name in reversed(AUTH_MIGRATIONS):
            runner.rollback_migration(_version(name))
        inspector = inspect(fresh_sqlite)
        for table in AUTH_TABLE_NAMES:
            assert not inspector.has_table(table), table
        with fresh_sqlite.connect() as conn:
            remaining = conn.execute(
                text("SELECT version FROM schema_migrations")
            ).fetchall()
        assert remaining == []

    def test_clean_reapply_after_full_revert(self, fresh_sqlite, isolated_auth_versions_dir):
        runner = MigrationRunner(fresh_sqlite, migrations_dir=isolated_auth_versions_dir)
        runner.run_pending_migrations()
        for name in reversed(AUTH_MIGRATIONS):
            runner.rollback_migration(_version(name))
        reapplied = runner.run_pending_migrations()
        assert set(reapplied) == {_version(n) for n in AUTH_MIGRATIONS}
        inspector = inspect(fresh_sqlite)
        assert all(inspector.has_table(t) for t in AUTH_TABLE_NAMES)


class TestPgCreateAllIndexParity:
    """PG leg: ``create_all`` (the manager's real path) materializes the
    four tables WITH the sweep indexes; the model ``__table_args__``
    names match the ``.sql`` ``CREATE INDEX`` names byte-for-byte."""

    def test_pg_tables_and_indexes_materialize(self):
        engine = scratch_pg_engine_or_skip()
        try:
            SQLModel.metadata.create_all(engine, tables=[
                AuthSession.__table__,
                AuthState.__table__,
                BearerToken.__table__,
                AuthBootstrapConsumption.__table__,
            ])
            inspector = inspect(engine)
            for table in AUTH_TABLE_NAMES:
                assert inspector.has_table(table), table
            for table, index in EXPECTED_INDEXES.items():
                pg_indexes = {ix["name"] for ix in inspector.get_indexes(table)}
                assert index in pg_indexes, (
                    f"{index} missing on PG {table} — create_all did not "
                    "emit the model __table_args__ declaration"
                )
        finally:
            engine.dispose()

    def test_model_and_sql_index_name_parity(self):
        """The dual-driver contract (drift_events precedent): model
        ``__table_args__`` ↔ ``.sql`` ``CREATE INDEX`` names identical."""
        model_indexes: dict[str, str] = {
            AuthSession.__tablename__: "idx_auth_sessions_expires_at",
            BearerToken.__tablename__: "idx_auth_bearer_tokens_expires_at",
        }
        for name in AUTH_MIGRATIONS:
            content = (_VERSIONS_DIR / name).read_text()
            table = name.rsplit("_create_", 1)[1].removesuffix(".sql")
            if table not in model_indexes:
                continue
            expected = model_indexes[table]
            assert f"CREATE INDEX IF NOT EXISTS {expected}" in content, name

    def test_sql_files_follow_per_table_convention(self):
        """D2.1/D14: one ``.sql`` per table, ``-- UP``/``-- DOWN``
        present, NO ``MANUAL: TRUE`` marker (D17 — auth tables
        auto-apply on SQLite; a MANUAL marker would be a hidden second
        code path)."""
        for name in AUTH_MIGRATIONS:
            path = _VERSIONS_DIR / name
            content = path.read_text()
            assert "-- UP" in content, name
            assert "-- DOWN" in content, name
            # D17: NO manual-only marker — the runner's OWN detector
            # must classify these as auto-apply (parse-based, not a
            # naive string grep).
            assert MigrationFile.parse(path).manual_only is False, name
        table_names_in_files = {
            name.rsplit("_create_", 1)[1].removesuffix(".sql")
            for name in AUTH_MIGRATIONS
        }
        assert table_names_in_files == set(AUTH_TABLE_NAMES)


class TestEmptyStubBehaviorPlanOverview12Item1:
    """Confirm-at-implementation item 1 (plan-overview §12) — pinned
    answer: D5.5 comment-labeled empty stubs apply as no-ops WITH a
    true SHA-256 checksum registered and roll back cleanly; a zero-byte
    ``-- DOWN`` section refuses rollback ("has no DOWN section")."""

    def test_labeled_empty_stub_applies_reverts_with_checksum(self, fresh_sqlite):
        with tempfile.TemporaryDirectory() as tmpdir:
            versions_dir = Path(tmpdir) / "versions"
            versions_dir.mkdir()
            stub = versions_dir / "20261011_999999_create_grants.sql"
            stub.write_text(
                "-- Migration: reserved — no schema (placeholder stub)\n"
                "-- Created: 2026-10-11\n\n"
                "-- UP\n"
                "-- Reserved seam (P5) — no schema. Intentionally empty body.\n\n"
                "-- DOWN\n"
                "-- Reserved seam — nothing to drop.\n"
            )
            runner = MigrationRunner(fresh_sqlite, migrations_dir=versions_dir)
            assert runner.run_pending_migrations() == ["20261011_999999"]
            with fresh_sqlite.connect() as conn:
                row = conn.execute(
                    text("SELECT checksum FROM schema_migrations WHERE version='20261011_999999'")
                ).scalar_one()
            assert row == hashlib.sha256(stub.read_bytes()).hexdigest()
            runner.rollback_migration("20261011_999999")
            with fresh_sqlite.connect() as conn:
                assert conn.execute(
                    text("SELECT COUNT(*) FROM schema_migrations")
                ).scalar_one() == 0

    def test_zero_byte_down_section_refuses_rollback(self, fresh_sqlite):
        with tempfile.TemporaryDirectory() as tmpdir:
            versions_dir = Path(tmpdir) / "versions"
            versions_dir.mkdir()
            stub = versions_dir / "20261011_999998_create_subject_alias.sql"
            stub.write_text("-- reserved\n\n-- UP\n-- empty body\n\n-- DOWN\n")
            runner = MigrationRunner(fresh_sqlite, migrations_dir=versions_dir)
            assert runner.run_pending_migrations() == ["20261011_999998"]
            with pytest.raises(MigrationError, match="no DOWN section"):
                runner.rollback_migration("20261011_999998")
