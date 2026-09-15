"""Migration tests for critical-notes-retrieval Phase 1.

Covers (scoped to the two Phase-1 migration files, NOT the whole
historical chain — fresh-SQLite full-chain boot is a documented
pre-existing trap (20260714_000001 PG-only DROP CONSTRAINT), outside
this feature's scope):

1. **Fresh-SQLite boot end-to-end**: create_all() builds the tables
   WITH the model columns; the runner applies the lifecycle migration
   (duplicate-column swallow) + the drop migration ("no such column"
   swallow); the ledger records both; the table round-trips lifecycle
   fields; the two PARTIAL indexes exist (create_all cannot express
   them — only the migration/mirror can).
2. **Legacy-lineage upgrade**: a pre-Phase-1-shaped critical_notes
   table + a legacy projects.critical_notes JSON column are migrated:
   six columns added, last_reviewed_at backfilled = created_at with the
   explicit NULL-guard, partial indexes created, dead JSON column
   dropped (SQLite >= 3.35).
3. **Idempotency / never re-fires**: second run is a no-op (ledger).
4. **N4 precondition mechanism**: marker parsing, evaluator semantics,
   skip-with-ledger-marker (never fails boot, never re-fires, SQL
   untouched), rollback gating.
5. **PG-mirror parity**: ``_ensure_postgres_columns`` carries the six
   ``ADD COLUMN IF NOT EXISTS`` + both ``CREATE INDEX IF NOT EXISTS ...
   WHERE`` statements + the ``DROP COLUMN IF EXISTS`` — with index
   names byte-identical to the SQLite migration.
6. **Migration idiom**: no ``IF NOT EXISTS`` in the SQLite-side
   lifecycle .sql (ledger + duplicate-column swallow is the
   idempotency mechanism — hard constraint #1).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event, text as sa_text
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.project.models  # noqa: F401  (table registration)
from daemon.migrations.models import SchemaMigration  # noqa: F401
from daemon.migrations.runner import MigrationFile, MigrationRunner
from daemon.repositories.project.models import CriticalNoteModel
from daemon.repositories.project.repository import SQLModelProjectRepository

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_DIR = REPO_ROOT / "daemon" / "migrations" / "versions"

LIFECYCLE_FILE = "20260915_120000_critical_notes_lifecycle.sql"
DROP_FILE = "20260915_120001_drop_projects_critical_notes_json.sql"

LIFECYCLE_COLUMNS = [
    "pinned",
    "pinned_at",
    "pinned_by",
    "superseded_by_id",
    "last_reviewed_at",
    "detail_ref",
]


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path):
    db_path = tmp_path / "cn-migrations.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    return eng


@pytest.fixture
def scoped_versions_dir(tmp_path) -> Path:
    """Copy ONLY the two Phase-1 migrations into a temp versions dir."""
    target = tmp_path / "versions"
    target.mkdir()
    for name in (LIFECYCLE_FILE, DROP_FILE):
        shutil.copy(VERSIONS_DIR / name, target / name)
    return target


def make_runner(eng, versions_dir: Path) -> MigrationRunner:
    return MigrationRunner(eng, migrations_dir=versions_dir)


def _table_columns(eng, table: str) -> list[str]:
    with eng.connect() as conn:
        rows = conn.execute(sa_text(f"PRAGMA table_info({table})")).fetchall()
    return [r[1] for r in rows]


def _sqlite_version(eng) -> tuple[int, ...]:
    with eng.connect() as conn:
        raw = str(conn.execute(sa_text("SELECT sqlite_version()")).scalar())
    return tuple(int(p) for p in raw.split("."))


def _index_sql(eng, name: str) -> str | None:
    with eng.connect() as conn:
        row = conn.execute(
            sa_text("SELECT sql FROM sqlite_master WHERE type='index' AND name=:n"),
            {"n": name},
        ).fetchone()
    return row[0] if row else None


LEGACY_CN_TABLE_SQL = """
CREATE TABLE critical_notes (
    id VARCHAR(32) PRIMARY KEY,
    project_id VARCHAR(32) NOT NULL,
    created_at VARCHAR(64),
    updated_at VARCHAR(64),
    source_agent VARCHAR(64),
    category VARCHAR(32),
    priority VARCHAR(16),
    summary VARCHAR(256),
    reference TEXT
)
"""


@pytest.fixture
def legacy_engine(engine):
    """A fresh engine whose critical_notes/projects predate Phase 1."""
    with engine.begin() as conn:
        conn.execute(sa_text(LEGACY_CN_TABLE_SQL))
        conn.execute(sa_text(
            "CREATE TABLE projects ("
            " project_id VARCHAR(32) PRIMARY KEY,"
            " name VARCHAR(128),"
            " critical_notes JSON DEFAULT '[]')"
        ))
        conn.execute(sa_text(
            "INSERT INTO critical_notes (id, project_id, created_at, updated_at,"
            " source_agent, category, priority, summary, reference)"
            " VALUES ('legacy-1', 'p1', '2026-01-01T00:00:00+00:00',"
            " '2026-01-02T00:00:00+00:00', 'agent', 'risk', 'high', 'Legacy row', NULL)"
        ))
    return engine


# ─── 1. Fresh-SQLite boot end-to-end ─────────────────────────────────────────


class TestFreshSqliteBoot:
    def test_create_all_plus_migrations_boot_and_roundtrip(
        self, engine, scoped_versions_dir
    ):
        SQLModel.metadata.create_all(engine)  # manager boot order: create_all FIRST
        runner = make_runner(engine, scoped_versions_dir)
        applied = runner.run_pending_migrations()  # then pending migrations

        assert set(applied) >= {
            "20260915_120000",
            "20260915_120001",
        }
        # The six lifecycle columns exist on the fresh lineage (model-driven).
        cols = _table_columns(engine, "critical_notes")
        for col in LIFECYCLE_COLUMNS:
            assert col in cols
        # The partial indexes exist (create_all can NOT express them).
        assert _index_sql(engine, "ix_critical_notes_pinned") is not None
        assert _index_sql(engine, "ix_critical_notes_superseded_by_id") is not None
        assert "WHERE pinned = TRUE" in (_index_sql(engine, "ix_critical_notes_pinned") or "")
        assert "WHERE superseded_by_id IS NOT NULL" in (
            _index_sql(engine, "ix_critical_notes_superseded_by_id") or ""
        )
        # Boot-shape round-trip through the repository.
        repo = SQLModelProjectRepository(engine)
        project = repo.create(name="boot-project")
        note = repo.add_critical_note(
            project.project_id, source_agent="leader", category="risk",
            priority="high", summary="Roundtrip probe", detail_ref="DETAIL",
        )
        assert note.pinned is False
        assert note.superseded_by_id is None
        assert note.last_reviewed_at is not None
        row = repo.get_critical_note(project.project_id, note.id)
        assert row.detail_ref == "DETAIL"


# ─── 2. Legacy-lineage upgrade ───────────────────────────────────────────────


class TestLegacyLineageUpgrade:
    def test_columns_added_backfilled_and_json_dropped(self, legacy_engine, scoped_versions_dir):
        SQLModel.metadata.create_all(legacy_engine)  # no-op for existing tables
        runner = make_runner(legacy_engine, scoped_versions_dir)
        runner.run_pending_migrations()

        cols = _table_columns(legacy_engine, "critical_notes")
        for col in LIFECYCLE_COLUMNS:
            assert col in cols, f"legacy lineage missing column {col}"

        # Backfill: last_reviewed_at == created_at for the legacy row.
        with legacy_engine.connect() as conn:
            reviewed = conn.execute(sa_text(
                "SELECT last_reviewed_at FROM critical_notes WHERE id='legacy-1'"
            )).scalar()
        assert reviewed == "2026-01-01T00:00:00+00:00"

        # Partial indexes present with the exact predicates.
        assert "WHERE pinned = TRUE" in (_index_sql(legacy_engine, "ix_critical_notes_pinned") or "")
        assert "WHERE superseded_by_id IS NOT NULL" in (
            _index_sql(legacy_engine, "ix_critical_notes_superseded_by_id") or ""
        )

        # Dead projects.critical_notes JSON column dropped (SQLite >= 3.35).
        if _sqlite_version(legacy_engine) >= (3, 35, 0):
            assert "critical_notes" not in _table_columns(legacy_engine, "projects")

    def test_lifecycle_up_has_no_if_not_exists_sqlite_idiom(self):
        content = (VERSIONS_DIR / LIFECYCLE_FILE).read_text()
        up_section = content.split("-- UP")[1].split("-- DOWN")[0]
        # Hard constraint #1: idempotency via ledger + duplicate-column
        # swallow — NOT via IF NOT EXISTS in the SQLite-side SQL.
        assert "IF NOT EXISTS" not in up_section
        # Exactly SIX ADD COLUMN statements (§5.1 authoritative).
        assert up_section.count("ADD COLUMN") == 6
        # Exactly the two pinned partial indexes.
        assert "CREATE INDEX ix_critical_notes_pinned" in up_section
        assert "CREATE INDEX ix_critical_notes_superseded_by_id" in up_section
        # NULL-guarded backfill (#7).
        assert "WHERE last_reviewed_at IS NULL" in up_section

    def test_down_is_symmetric(self, legacy_engine, scoped_versions_dir):
        if _sqlite_version(legacy_engine) < (3, 35, 0):
            pytest.skip("SQLite < 3.35 cannot DROP COLUMN")
        SQLModel.metadata.create_all(legacy_engine)
        runner = make_runner(legacy_engine, scoped_versions_dir)
        runner.run_pending_migrations()
        runner.rollback_migration("20260915_120000")
        runner.rollback_migration("20260915_120001")

        cols = _table_columns(legacy_engine, "critical_notes")
        for col in LIFECYCLE_COLUMNS:
            assert col not in cols
        assert _index_sql(legacy_engine, "ix_critical_notes_pinned") is None
        assert _index_sql(legacy_engine, "ix_critical_notes_superseded_by_id") is None
        assert "critical_notes" in _table_columns(legacy_engine, "projects")


# ─── 3. Idempotency / never re-fires ─────────────────────────────────────────


class TestLedgerIdempotency:
    def test_second_run_is_a_noop(self, legacy_engine, scoped_versions_dir):
        SQLModel.metadata.create_all(legacy_engine)
        runner = make_runner(legacy_engine, scoped_versions_dir)
        first = runner.run_pending_migrations()
        second = runner.run_pending_migrations()
        assert "20260915_120000" in first
        assert second == []
        status = runner.get_migration_status()
        assert "20260915_120000" in status["applied"]
        assert status["pending"] == []


# ─── 4. N4 precondition mechanism ────────────────────────────────────────────


class _FakeVersionConn:
    """Minimal connection double answering SELECT sqlite_version()."""

    def __init__(self, version: str):
        self._version = version

    def execute(self, stmt, *args, **kwargs):
        text_stmt = str(stmt)
        if "sqlite_version" in text_stmt:
            return _FakeResult((self._version,))
        raise AssertionError(f"unexpected statement: {text_stmt}")


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class TestPreconditionMechanism:
    def test_parse_extracts_marker(self):
        migration = MigrationFile.parse(VERSIONS_DIR / DROP_FILE)
        assert migration.precondition == "sqlite>=3.35.0"

    def test_parse_absent_marker_is_none(self):
        migration = MigrationFile.parse(VERSIONS_DIR / LIFECYCLE_FILE)
        assert migration.precondition is None

    def test_evaluator_semantics(self, engine):
        runner = MigrationRunner(engine)
        satisfied, reason = runner._evaluate_precondition(
            _FakeVersionConn("3.45.2"), "sqlite>=3.35.0"
        )
        assert satisfied and reason == ""
        satisfied, reason = runner._evaluate_precondition(
            _FakeVersionConn("3.34.1"), "sqlite>=3.35.0"
        )
        assert not satisfied
        assert "3.34.1" in reason and "3.35.0" in reason
        # None → unconditional.
        satisfied, reason = runner._evaluate_precondition(_FakeVersionConn("3.34.1"), None)
        assert satisfied
        # Unknown engine fails CLOSED.
        satisfied, reason = runner._evaluate_precondition(
            _FakeVersionConn("3.45.2"), "oracle>=1.0"
        )
        assert not satisfied
        assert "unknown precondition engine" in reason

    def test_skip_records_ledger_never_refires_and_never_fails_boot(
        self, legacy_engine, scoped_versions_dir
    ):
        SQLModel.metadata.create_all(legacy_engine)
        runner = make_runner(legacy_engine, scoped_versions_dir)
        # Simulate an old SQLite: the precondition fails.
        with patch.object(
            runner, "_evaluate_precondition", return_value=(False, "sqlite 3.34.1 < required 3.35.0")
        ):
            applied = runner.run_pending_migrations()

        # Boot did not fail; BOTH migrations are recorded (one executed,
        # one skip-ledger row); neither re-fires.
        status = runner.get_migration_status()
        assert status["pending"] == []
        assert set(status["applied"]) >= {"20260915_120000", "20260915_120001"}
        # The DROP was NOT executed: the dead column is still there.
        assert "critical_notes" in _table_columns(legacy_engine, "projects")

        with Session(legacy_engine) as session:
            row = session.get(SchemaMigration, "20260915_120001")
            assert row is not None
            assert row.skip_reason is not None
            assert row.precondition == "sqlite>=3.35.0"
            executed = session.get(SchemaMigration, "20260915_120000")
            assert executed is not None
            assert executed.skip_reason is None

    def test_rollback_gated_on_same_precondition(
        self, legacy_engine, scoped_versions_dir
    ):
        if _sqlite_version(legacy_engine) < (3, 35, 0):
            pytest.skip("SQLite < 3.35 cannot DROP COLUMN")
        SQLModel.metadata.create_all(legacy_engine)
        runner = make_runner(legacy_engine, scoped_versions_dir)
        runner.run_pending_migrations()
        assert "critical_notes" not in _table_columns(legacy_engine, "projects")

        # DOWN with a FAILING precondition: SQL skipped, ledger cleared.
        with patch.object(
            runner, "_evaluate_precondition", return_value=(False, "sqlite too old")
        ):
            runner.rollback_migration("20260915_120001")
        assert "critical_notes" not in _table_columns(legacy_engine, "projects")
        assert "20260915_120001" not in runner.get_migration_status()["applied"]

        # Re-apply the UP (idempotent — column already dropped; ledger
        # row re-created), then DOWN with a SATISFIED precondition: the
        # SQL actually runs and the column comes back.
        runner2 = make_runner(legacy_engine, scoped_versions_dir)
        migration = MigrationFile.parse(scoped_versions_dir / DROP_FILE)
        runner2.apply_migration(migration)
        runner2.rollback_migration("20260915_120001")
        assert "critical_notes" in _table_columns(legacy_engine, "projects")


# ─── 5. PG-mirror parity (static contract — the runner never runs on PG) ────

class TestPgMirrorParity:
    @staticmethod
    def _manager_source() -> str:
        return (REPO_ROOT / "daemon" / "manager.py").read_text()

    @staticmethod
    def _manager_source_normalized() -> str:
        # The mirror statements are Python string literals split across
        # adjacent lines; strip the quote characters, then whitespace-
        # normalize, so the full SQL text is matchable as one string.
        raw = (REPO_ROOT / "daemon" / "manager.py").read_text()
        return " ".join(raw.replace('"', " ").split())

    def test_six_add_column_if_not_exists_statements(self):
        source = self._manager_source()
        expected = [
            "ALTER TABLE critical_notes ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE critical_notes ADD COLUMN IF NOT EXISTS pinned_at TEXT",
            "ALTER TABLE critical_notes ADD COLUMN IF NOT EXISTS pinned_by TEXT",
            "ALTER TABLE critical_notes ADD COLUMN IF NOT EXISTS superseded_by_id TEXT",
            "ALTER TABLE critical_notes ADD COLUMN IF NOT EXISTS last_reviewed_at TEXT",
            "ALTER TABLE critical_notes ADD COLUMN IF NOT EXISTS detail_ref TEXT",
        ]
        for stmt in expected:
            assert stmt in source, f"PG mirror missing: {stmt}"

    def test_both_partial_indexes_mirrored_byte_identical_names(self):
        source = self._manager_source_normalized()
        assert (
            "CREATE INDEX IF NOT EXISTS ix_critical_notes_pinned "
            "ON critical_notes(project_id, pinned) WHERE pinned = TRUE"
        ) in source
        assert (
            "CREATE INDEX IF NOT EXISTS ix_critical_notes_superseded_by_id "
            "ON critical_notes(superseded_by_id) WHERE superseded_by_id IS NOT NULL"
        ) in source
        # Names match the SQLite migration exactly (both lineages converge).
        lifecycle = (VERSIONS_DIR / LIFECYCLE_FILE).read_text()
        assert "ix_critical_notes_pinned" in lifecycle
        assert "ix_critical_notes_superseded_by_id" in lifecycle

    def test_projects_json_drop_mirrored(self):
        source = self._manager_source()
        assert (
            "ALTER TABLE projects DROP COLUMN IF EXISTS critical_notes" in source
        )

    def test_backfill_null_guard_mirrored(self):
        source = self._manager_source_normalized()
        assert (
            "UPDATE critical_notes SET last_reviewed_at = created_at "
            "WHERE last_reviewed_at IS NULL"
        ) in source
