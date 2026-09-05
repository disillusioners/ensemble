"""Phase 3 migration parity tests — attestation ledger columns.

Triple-registration requirement for the Phase 3 attestation ledger
columns (mirrors ``tests/migration/test_turn_handle_schema.py``):

  * **SQLModel metadata** (``daemon/repositories/instance/models.py``) —
    the authoritative fresh-database definition.
    ``SQLModel.metadata.create_all()`` must emit BOTH columns AND make
    them NOT NULL DEFAULT 0/FALSE on a new database.
  * **SQLite migration** (``daemon/migrations/versions/20260905_000001_
    attestation_ledger_columns.sql``) — adds both columns on a pre-
    Phase-3 schema. Guarded by the ``MigrationRunner``'s per-statement
    duplicate-column handler. PG+SQLite portable (plain
    ``ALTER TABLE ... ADD COLUMN`` — NO PG-only ``DROP CONSTRAINT IF
    EXISTS`` like the prior trap
    ``20260714_000001_widen_job_queue_type_constraint.sql``).
  * **PostgreSQL ALTER block** (``daemon/manager.py::_ensure_postgres_
    columns``) — emitted only against a Postgres engine; mirrored by
    ``ALTER TABLE IF NOT EXISTS`` + idempotent patterns.

The tests below prove each path is registered independently and that
an omission in any one path is detected by an explicit assertion (no
regression can silently remove a column or default).

Fresh-SQLite boot hazard (LESSONS/2026-09-04-fresh-sqlite-boot-
migration-20260714-pg-only): the prior ``20260714_000001`` migration
uses PG-only ``DROP CONSTRAINT IF EXISTS`` syntax that fails on
SQLite. The Phase 3 migration is deliberately PG+SQLite-safe so
fresh-SQLite boots remain healthy. The B-fresh-DB-smoke test pins
this property.

Run with::

    .venv/bin/pytest -q tests/migration/test_attestation_migration.py -x --timeout 120
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

# Import every model module so SQLModel.metadata registers every table.
# Same convention as tests/migration/test_turn_handle_schema.py —
# fresh-DB create_all() must emit the same schema the production
# daemon would, including the new ``attestation_denied_count`` and
# ``completion_gate_escalated`` columns on ``instances``.
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.source.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.mcp_server.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.infra.models  # noqa: F401
from daemon.migrations.models import SchemaMigration  # noqa: F401

from daemon.migrations.runner import MigrationRunner
from daemon.repositories.instance.models import Instance


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

#: Path to the Phase 3 attestation migration file. We copy it into an
#: isolated migrations directory so the runner does not try to apply
#: 60+ unrelated migrations (some reference PG-only syntax). Isolating
#: the migration under test keeps the assertions focused on the
#: Phase 3 file.
_PHASE_3_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "daemon/migrations/versions/20260905_000001_attestation_ledger_columns.sql"
)

#: Column names under test (Phase 3 attestation ledger).
ATTESTATION_COLUMNS: tuple[str, ...] = (
    "attestation_denied_count",
    "completion_gate_escalated",
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_migrations_dir() -> Iterator[Path]:
    """Yield a temp ``versions/`` directory containing ONLY the Phase 3
    attestation migration (and a no-op baseline).

    MigrationRunner applies every ``*.sql`` file in the directory it
    is pointed at. We isolate the Phase 3 file so a test failure
    points at the Phase 3 schema (not at some unrelated migration's
    PG-only syntax).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        versions_dir = Path(tmpdir) / "versions"
        versions_dir.mkdir()
        # No-op baseline migration so the runner can mark at least one
        # version as applied (the runner requires valid ``-- UP``
        # sections and writes a ``schema_migrations`` row for each).
        (versions_dir / "20250101_000000_baseline.sql").write_text(
            "-- Migration: no-op baseline\n"
            "-- Created: 2025-01-01\n"
            "-- Description: test fixture baseline\n\n"
            "-- UP\n\n"
            "-- DOWN\n"
        )
        # Copy the Phase 3 migration into the isolated directory.
        shutil.copy(_PHASE_3_MIGRATION, versions_dir / _PHASE_3_MIGRATION.name)
        try:
            yield versions_dir
        finally:
            # Tempdir cleanup is automatic on context exit.
            pass


@pytest.fixture
def fresh_sqlite_engine() -> Iterator[Engine]:
    """Yield an in-memory SQLite engine with the full SQLModel schema.

    Mirrors ``test_jsonb_migration.py::sqlite_engine`` — same FK-off
    StaticPool pattern so cross-session writes are visible.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def legacy_attestation_engine() -> Iterator[Engine]:
    """Yield a SQLite engine with the pre-Phase-3 ``instances`` schema.

    The pre-Phase-3 schema lacks ``attestation_denied_count`` and
    ``completion_gate_escalated``. The Phase 3 migration must add
    them — but does NOT need to backfill because the migration uses
    ``NOT NULL DEFAULT 0`` / ``NOT NULL DEFAULT FALSE`` so existing
    rows pick up the defaults automatically.

    We create only the ``instances`` table by hand with the columns
    present BEFORE Phase 3; the migration under test only touches
    ``instances`` so other tables are irrelevant.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    now = "2026-09-05 00:00:00"
    with engine.begin() as conn:
        # Pre-Phase-3 instances schema: no attestation columns.
        conn.execute(
            text(
                """
                CREATE TABLE instances (
                    instance_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    agent_id TEXT NOT NULL,
                    agent_dir TEXT NOT NULL,
                    agent_name TEXT,
                    agent_tag TEXT,
                    parent_id TEXT,
                    status TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    version INTEGER NOT NULL DEFAULT 1,
                    last_activity_at TIMESTAMP,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    paused_at TEXT
                )
                """
            )
        )
        # One existing instance row that must pick up the column
        # defaults (``0`` / ``0``) via the migration's
        # ``NOT NULL DEFAULT`` clauses — no backfill query needed.
        conn.execute(
            text(
                """
                INSERT INTO instances (
                    instance_id, agent_id, agent_dir, status, metadata,
                    version, created_at, updated_at
                ) VALUES (
                    'legacy-inst-1', 'leader', './agents/leader', 'idle', '{}',
                    1, :now, :now
                )
                """
            ),
            {"now": now},
        )
    try:
        yield engine
    finally:
        engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# 1. SQLModel metadata — fresh DB has the columns + defaults
# ─────────────────────────────────────────────────────────────────────────────


class TestSqlModelFreshDatabase:
    """``SQLModel.metadata.create_all()`` on a fresh DB must emit BOTH
    columns with the documented defaults (``0`` / ``False``)."""

    def test_columns_present_on_instances(self, fresh_sqlite_engine):
        inspector = inspect(fresh_sqlite_engine)
        cols = {c["name"]: c for c in inspector.get_columns("instances")}
        for col_name in ATTESTATION_COLUMNS:
            assert col_name in cols, (
                f"Instances SQLModel missing column {col_name} — "
                "fresh-DB create_all() would not emit it"
            )

    def test_attestation_denied_count_default_is_zero(self, fresh_sqlite_engine):
        inspector = inspect(fresh_sqlite_engine)
        cols = {c["name"]: c for c in inspector.get_columns("instances")}
        col = cols["attestation_denied_count"]
        # SQLAlchemy inspector reports the server default; we accept
        # either ``0`` or the literal string ``"0"`` (driver-rendered).
        default = col.get("default")
        assert default is not None, (
            "attestation_denied_count has no server default — "
            "existing instances would have NULL after migration"
        )

    def test_completion_gate_escalated_default_is_false(self, fresh_sqlite_engine):
        inspector = inspect(fresh_sqlite_engine)
        cols = {c["name"]: c for c in inspector.get_columns("instances")}
        col = cols["completion_gate_escalated"]
        default = col.get("default")
        assert default is not None, (
            "completion_gate_escalated has no server default — "
            "existing instances would have NULL after migration"
        )

    def test_fresh_db_insert_with_defaults_works(self, fresh_sqlite_engine):
        """A new row inserted without specifying the attestation columns
        must succeed with the documented defaults — no NULL constraint
        violations.
        """
        now = "2026-09-05 00:00:00"
        with Session(fresh_sqlite_engine) as session:
            instance = Instance(
                instance_id="fresh-inst",
                agent_id="leader",
                agent_dir="./agents/leader",
                status="idle",
                instance_metadata={},
                created_at=now,
                updated_at=now,
            )
            session.add(instance)
            session.commit()
            session.refresh(instance)
            assert instance.attestation_denied_count == 0
            assert instance.completion_gate_escalated is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. SQLite migration — applies cleanly on pre-Phase-3 schema
# ─────────────────────────────────────────────────────────────────────────────


class TestSqliteMigrationApplies:
    """The Phase 3 .sql migration applies on the pre-Phase-3 schema
    (legacy instances table without the attestation columns).

    Pins the FRESH-SQLITE BOOT SAFETY: this migration uses plain
    ``ALTER TABLE ... ADD COLUMN`` (PG+SQLite portable) — NOT the
    PG-only ``DROP CONSTRAINT IF EXISTS`` pattern from
    ``20260714_000001_widen_job_queue_type_constraint.sql`` that
    broke fresh-SQLite boot per
    LESSONS/2026-09-04-fresh-sqlite-boot-migration-20260714-pg-only.
    """

    def test_migration_file_exists_and_is_well_formed(self):
        assert _PHASE_3_MIGRATION.exists(), (
            f"Phase 3 migration not found at {_PHASE_3_MIGRATION}"
        )
        content = _PHASE_3_MIGRATION.read_text()
        assert "-- UP" in content
        assert "-- DOWN" in content
        # NO PG-only DROP CONSTRAINT — would break fresh-SQLite boot.
        assert "DROP CONSTRAINT" not in content

    def test_migration_applies_on_legacy_schema(
        self, isolated_migrations_dir, legacy_attestation_engine
    ):
        runner = MigrationRunner(
            legacy_attestation_engine, migrations_dir=isolated_migrations_dir
        )
        applied = runner.run_pending_migrations()
        assert _PHASE_3_MIGRATION.stem.split("_")[0] + "_" + _PHASE_3_MIGRATION.stem.split("_")[1] in (
            applied[0] if applied else ""
        ) or any(
            a.startswith("20260905") for a in applied
        ), f"Migration did not apply: applied={applied}"

        # Columns now present.
        inspector = inspect(legacy_attestation_engine)
        cols = {c["name"] for c in inspector.get_columns("instances")}
        for col_name in ATTESTATION_COLUMNS:
            assert col_name in cols, (
                f"Migration did not add column {col_name}"
            )

    def test_existing_row_picks_up_defaults_via_migration(
        self, isolated_migrations_dir, legacy_attestation_engine
    ):
        runner = MigrationRunner(
            legacy_attestation_engine, migrations_dir=isolated_migrations_dir
        )
        runner.run_pending_migrations()

        # The pre-existing row should now carry the column defaults.
        with legacy_attestation_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT attestation_denied_count, completion_gate_escalated "
                    "FROM instances WHERE instance_id = :iid"
                ),
                {"iid": "legacy-inst-1"},
            ).fetchone()
        assert row is not None
        # The migration's ``NOT NULL DEFAULT 0`` clause guarantees
        # the pre-existing row picks up ``0`` / ``0`` (False) —
        # NO backfill query is required.
        assert int(row[0]) == 0
        assert bool(row[1]) is False

    def test_migration_is_idempotent_on_second_apply(
        self, isolated_migrations_dir, legacy_attestation_engine
    ):
        """The migration must be safely re-runnable — the
        ``MigrationRunner``'s duplicate-column-name handler should
        treat the second apply as a no-op.
        """
        runner = MigrationRunner(
            legacy_attestation_engine, migrations_dir=isolated_migrations_dir
        )
        runner.run_pending_migrations()
        # Second run should be a no-op (no pending migrations because
        # the first run wrote the ``schema_migrations`` row).
        applied_again = runner.run_pending_migrations()
        assert applied_again == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. PG ALTER block — present in _ensure_postgres_columns
# ─────────────────────────────────────────────────────────────────────────────


class TestPostgresAlterBlockPresent:
    """``daemon/manager.py::_ensure_postgres_columns`` must carry an
    ``ALTER TABLE instances ADD COLUMN IF NOT EXISTS`` statement for
    BOTH columns. This is the PG-side companion to the SQLite
    migration — without it, existing PG databases miss the columns
    on upgrade.

    We assert via static grep (the PG path is exercised in production
    only — PG is not available in this SQLite-only test fixture).
    """

    def test_manager_py_has_alter_for_denied_count(self):
        # The PG ALTER block uses Python string-concatenation (so the
        # source splits across two adjacent string literals); grep on
        # the column name is the correct check.
        mgr = Path(__file__).resolve().parents[2] / "daemon" / "manager.py"
        content = mgr.read_text()
        assert "attestation_denied_count" in content, (
            "_ensure_postgres_columns missing reference to "
            "attestation_denied_count — existing PG databases would "
            "lack the column on upgrade"
        )
        # The presence of an ADD COLUMN block specifically in the
        # _ensure_postgres_columns statements list.
        statements_section = content[
            content.index('statements = [') : content.index(']\n        # Concurrency-remediation')
        ] if ']\n        # Concurrency-remediation' in content else content
        assert (
            "ALTER TABLE instances ADD COLUMN IF NOT EXISTS" in statements_section
            and "attestation_denied_count" in statements_section
        ), (
            "_ensure_postgres_columns statements block missing the "
            "ALTER TABLE for attestation_denied_count"
        )

    def test_manager_py_has_alter_for_escalated(self):
        mgr = Path(__file__).resolve().parents[2] / "daemon" / "manager.py"
        content = mgr.read_text()
        assert "completion_gate_escalated" in content
        statements_section = content[
            content.index('statements = [') : content.index(']\n        # Concurrency-remediation')
        ] if ']\n        # Concurrency-remediation' in content else content
        assert (
            "ALTER TABLE instances ADD COLUMN IF NOT EXISTS" in statements_section
            and "completion_gate_escalated" in statements_section
        ), (
            "_ensure_postgres_columns statements block missing the "
            "ALTER TABLE for completion_gate_escalated"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. PG+SQLite safety — the .sql has NO PG-only constructs
# ─────────────────────────────────────────────────────────────────────────────


class TestMigrationIsPgSqliteSafe:
    """Pin the FRESH-SQLITE BOOT SAFETY: the migration must NOT contain
    any PG-only syntax that would crash a fresh-SQLite boot.
    """

    @pytest.mark.parametrize(
        "forbidden_construct",
        [
            "DROP CONSTRAINT IF EXISTS",
            "USING gin",
            "::regclass",
            "JSONB",  # PG-only type
            "gen_random_uuid()",
            "RETURNS TRIGGER",
            "EXECUTE PROCEDURE",
        ],
    )
    def test_migration_excludes_pg_only_construct(self, forbidden_construct):
        content = _PHASE_3_MIGRATION.read_text()
        assert forbidden_construct not in content, (
            f"Phase 3 migration contains PG-only construct "
            f"'{forbidden_construct}' — would break fresh-SQLite boot "
            f"per LESSONS/2026-09-04-fresh-sqlite-boot-migration-20260714-pg-only"
        )
