"""Tests for the coder -> developer DB rename migration.

Phase 4 of the agent rename (coder -> developer) — data migration that
renames ``agent_id`` (and the related ``agent_dir`` path component)
wherever the old ``coder`` agent identifier was persisted, so existing
rows continue to resolve to the renamed ``agents/developer/`` directory.

The migration is dual-driver:

* **SQLite**: ``daemon/repositories/factory.py:run_migrations()`` —
  applies UPDATE statements to all five persisted tables. The legacy
  ``jobqueue`` table is also updated defensively (try/except).
* **PostgreSQL**: ``EnsembleManager._ensure_postgres_columns()`` in
  ``daemon/manager.py`` — applies the same UPDATE statements via
  ``self._engine.begin()``. The ``.sql`` migration runner is a NO-OP
  on PostgreSQL, so the equivalent UPDATEs must live in
  ``_ensure_postgres_columns`` (mirrored in this test by reading the
  production ``.sql`` file directly).

These tests verify:

1. ``run_migrations()`` correctly renames ``coder`` -> ``developer``.
2. The migration is idempotent (safe to re-run on an already-ran DB).
3. The migration handles empty databases (no rows to update, no error).
4. The migration covers all five persisted tables.
5. The migration produces identical results on both SQLite and PostgreSQL
   (PostgreSQL is probed and skipped gracefully if unavailable).

The legacy ``jobqueue`` table (pre-rename name for ``job_queue_items``)
does not have a SQLModel class — it is only referenced defensively for
legacy DBs, so it is intentionally not seeded in these tests.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Callable

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Register all SQLModel table classes with SQLModel.metadata so that
# ``SQLModel.metadata.create_all(engine)`` produces the production
# schema for the five tables touched by this migration.
from daemon.repositories.instance.models import Instance  # noqa: F401
from daemon.repositories.source.models import InstanceMapping  # noqa: F401
from daemon.repositories.job_queue.models import (  # noqa: F401
    DeadLetterItem,
    JobItem,
)
from daemon.repositories.project.models import Project  # noqa: F401

# Subject under test — the SQLite migration function.
from daemon.repositories.factory import run_migrations


# Path to the production SQLite migration file. The PostgreSQL dual-engine
# test reads this file's -- UP block so the test stays in lockstep with
# the production schema (single source of truth).
_MIGRATION_FILE = (
    Path(__file__).resolve().parent.parent.parent
    / "daemon"
    / "migrations"
    / "versions"
    / "20260626_000001_rename_coder_to_developer.sql"
)


# PostgreSQL connection defaults — mirrors ``tests/postgres/conftest.py``
# so the dual-engine test can stand alone without depending on that
# conftest's session-scoped fixtures.
_PG_HOST = os.environ.get("PG_TEST_HOST", "localhost")
_PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
_PG_DB = os.environ.get("PG_TEST_DB", "ensemble_test")
_PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
_PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
_PG_URL = (
    f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
    f"@{_PG_HOST}:{_PG_PORT}/{_PG_DB}"
)
_PG_SKIP_REASON = (
    f"PostgreSQL test DB not reachable at {_PG_HOST}:{_PG_PORT}/{_PG_DB} "
    f"as user {_PG_USER!r}"
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def sqlite_engine() -> Engine:
    """In-memory SQLite engine with all SQLModel tables registered.

    ``StaticPool`` keeps a single shared connection so the in-memory
    database persists across the test's many ``Session`` open/close
    cycles. ``check_same_thread=False`` is required for cross-thread
    use (matching the pattern in
    ``tests/unit/test_resume_flow_redesign.py``).
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


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _seed_coder_row(engine: Engine, table: str) -> str:
    """Insert a row with ``agent_id='coder'`` into ``table``; return PK.

    Uses SQLModel ORM (not raw INSERT) because several columns have
    Python-side defaults (``Instance.version``, JSONB columns with
    ``default_factory=dict``, ``created_at``/``updated_at`` ISO
    timestamps) that are NOT translated to server-side defaults. The
    ORM applies them transparently on insert.
    """
    rid = f"test-{table}-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        if table == "instances":
            session.add(
                Instance(
                    instance_id=rid,
                    agent_id="coder",
                    agent_dir="/agents/coder",
                    status="idle",
                )
            )
        elif table == "instance_mappings":
            session.add(
                InstanceMapping(
                    mapping_id=rid,
                    source_id=f"src-{rid}",
                    external_user_id="ext-1",
                    agent_instance_id=rid,
                    agent_id="coder",
                    agent_dir="/agents/coder",
                )
            )
        elif table == "job_queue_items":
            session.add(
                JobItem(
                    job_id=rid,
                    agent_id="coder",
                    agent_dir="/agents/coder",
                    message="test message",
                    source="api",
                )
            )
        elif table == "dead_letter_items":
            session.add(
                DeadLetterItem(
                    dlq_id=rid,
                    job_id=rid,
                    agent_id="coder",
                    agent_dir="/agents/coder",
                    message="msg",
                    source="api",
                    project_id="proj-1",
                    queue_id="queue-1",
                    error_message="failed",
                    failed_at="2026-06-26T00:00:00",
                    reason="TEST",
                )
            )
        elif table == "projects":
            session.add(
                Project(
                    project_id=rid,
                    name=f"name-{rid}",
                    creator_agent_id="coder",
                )
            )
        else:
            raise ValueError(f"Unknown table: {table}")
        session.commit()
    return rid


def _fetch_column(
    engine: Engine, table: str, pk_col: str, pk_val: str, target_col: str
):
    """Return ``target_col`` for the row matching ``pk_col = pk_val``."""
    with engine.connect() as conn:
        result = conn.execute(
            text(f"SELECT {target_col} FROM {table} WHERE {pk_col} = :id"),
            {"id": pk_val},
        )
        return result.scalar()


def _read_migration_up_statements() -> list[str]:
    """Extract the ``-- UP`` UPDATE statements from the production .sql file.

    The migration runner splits on ``;`` (see ``daemon/migrations/runner.py``),
    so this helper mirrors that parser — strips pure comment lines and
    drops empty chunks. The result is a list of executable UPDATE
    statements that match the production UPDATEs in
    ``EnsembleManager._ensure_postgres_columns`` for PostgreSQL.
    """
    content = _MIGRATION_FILE.read_text()
    up_text_lines: list[str] = []
    in_up = False
    for line in content.splitlines():
        if line.strip() == "-- UP":
            in_up = True
            continue
        if line.strip() == "-- DOWN":
            break
        if in_up:
            up_text_lines.append(line)
    up_text = "\n".join(up_text_lines)

    statements: list[str] = []
    for raw in up_text.split(";"):
        body_lines = [
            ln for ln in raw.splitlines() if not ln.strip().startswith("--")
        ]
        cleaned = "\n".join(body_lines).strip()
        if cleaned:
            statements.append(cleaned)
    return statements


def _run_sqlite_migration(engine: Engine) -> None:
    """Run the SQLite migration under test (production function)."""
    run_migrations(engine)


def _run_pg_migration(engine: Engine) -> None:
    """Execute the .sql ``-- UP`` block on a PostgreSQL engine.

    This is equivalent to what
    ``EnsembleManager._ensure_postgres_columns`` does in production but
    sourced from the .sql file directly so the test does not need to
    bootstrap a full ``EnsembleManager`` instance. Keeps the test in
    lockstep with the production schema (single source of truth).
    """
    statements = _read_migration_up_statements()
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def _probe_postgres() -> Engine | None:
    """Connect to the test PostgreSQL DB; return engine or ``None``.

    Mirrors ``tests/postgres/conftest.py:_probe_postgres`` so the
    dual-engine test stays self-contained.
    """
    try:
        engine = create_engine(_PG_URL, pool_pre_ping=True, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except (OperationalError, DBAPIError):
        return None
    except Exception:
        return None

    # Create the schema so the seeded rows land in real tables.
    SQLModel.metadata.create_all(engine)
    return engine


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────


class TestCoderDeveloperMigration:
    """Verify the coder -> developer agent_id rename migration."""

    def test_migration_updates_coder_to_developer(self, sqlite_engine: Engine) -> None:
        """Insert row with ``agent_id='coder'`` -> migration -> ``'developer'``."""
        instance_id = _seed_coder_row(sqlite_engine, "instances")
        project_id = _seed_coder_row(sqlite_engine, "projects")

        run_migrations(sqlite_engine)

        assert (
            _fetch_column(
                sqlite_engine, "instances", "instance_id", instance_id, "agent_id"
            )
            == "developer"
        )
        assert (
            _fetch_column(
                sqlite_engine,
                "projects",
                "project_id",
                project_id,
                "creator_agent_id",
            )
            == "developer"
        )

    def test_migration_idempotent(self, sqlite_engine: Engine) -> None:
        """Run migration twice -> no errors, correct final state."""
        instance_id = _seed_coder_row(sqlite_engine, "instances")

        run_migrations(sqlite_engine)
        run_migrations(sqlite_engine)  # second run must not raise

        assert (
            _fetch_column(
                sqlite_engine, "instances", "instance_id", instance_id, "agent_id"
            )
            == "developer"
        )

    def test_migration_no_coder_rows(self, sqlite_engine: Engine) -> None:
        """Migration on DB with no 'coder' rows -> no errors, tables still empty."""
        # Tables exist (created by fixture) but contain no rows.

        run_migrations(sqlite_engine)  # must not raise

        with sqlite_engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM instances")).scalar() == 0
            assert conn.execute(text("SELECT COUNT(*) FROM projects")).scalar() == 0

    def test_migration_covers_all_tables(self, sqlite_engine: Engine) -> None:
        """Insert 'coder' rows in all 5 tables -> migration updates every one."""
        seeded: dict[str, tuple[str, str]] = {
            "instances": (
                "instance_id",
                _seed_coder_row(sqlite_engine, "instances"),
            ),
            "instance_mappings": (
                "mapping_id",
                _seed_coder_row(sqlite_engine, "instance_mappings"),
            ),
            "job_queue_items": (
                "job_id",
                _seed_coder_row(sqlite_engine, "job_queue_items"),
            ),
            "dead_letter_items": (
                "dlq_id",
                _seed_coder_row(sqlite_engine, "dead_letter_items"),
            ),
            "projects": (
                "project_id",
                _seed_coder_row(sqlite_engine, "projects"),
            ),
        }

        run_migrations(sqlite_engine)

        # Each table's rename target column must now read 'developer'.
        for table, (pk_col, pk_val) in seeded.items():
            target_col = (
                "creator_agent_id" if table == "projects" else "agent_id"
            )
            value = _fetch_column(
                sqlite_engine, table, pk_col, pk_val, target_col
            )
            assert value == "developer", (
                f"Expected {target_col}='developer' in {table} after "
                f"migration, got {value!r}"
            )

        # The UPDATE statements also rewrite ``agent_dir`` from
        # ``/agents/coder`` -> ``/agents/developer``. Verify on one
        # representative row per table (instances) so a regression in
        # the REPLACE() clause is caught even though the migration's
        # primary contract is the ``agent_id`` rename.
        instance_id = seeded["instances"][1]
        assert (
            _fetch_column(
                sqlite_engine,
                "instances",
                "instance_id",
                instance_id,
                "agent_dir",
            )
            == "/agents/developer"
        )

    @pytest.mark.parametrize("engine_type", ["sqlite", "postgresql"])
    def test_migration_dual_engine(
        self, engine_type: str, sqlite_engine: Engine
    ) -> None:
        """Same migration contract on SQLite and PostgreSQL.

        PostgreSQL case is skipped cleanly when no test DB is reachable.
        Uses ``instances`` and ``projects`` for seeding — both have no
        inbound FK constraints from other tables in the test schema, so
        the dual-engine test does not need to satisfy FK chains for
        ``source_configs`` or ``job_queues``.
        """
        if engine_type == "sqlite":
            engine = sqlite_engine
            migrate: Callable[[Engine], None] = _run_sqlite_migration
        else:
            engine = _probe_postgres()
            if engine is None:
                pytest.skip(_PG_SKIP_REASON)
            migrate = _run_pg_migration

        try:
            instance_id = _seed_coder_row(engine, "instances")
            project_id = _seed_coder_row(engine, "projects")

            migrate(engine)

            assert (
                _fetch_column(
                    engine, "instances", "instance_id", instance_id, "agent_id"
                )
                == "developer"
            )
            assert (
                _fetch_column(
                    engine,
                    "projects",
                    "project_id",
                    project_id,
                    "creator_agent_id",
                )
                == "developer"
            )
        finally:
            if engine_type == "postgresql":
                # Clean up the seeded rows so the shared PG test DB is
                # not polluted across runs. PostgreSQL FK constraints
                # require ``CASCADE`` because ``instances`` is
                # referenced by ``job_watchers`` and other tables.
                # ``RESTART IDENTITY`` is a no-op here (PKs are
                # application-generated strings) but documents intent.
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "TRUNCATE TABLE instances, projects "
                            "RESTART IDENTITY CASCADE"
                        )
                    )
                engine.dispose()