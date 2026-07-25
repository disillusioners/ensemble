"""Tests for the coder -> developer DB rename migration.

Phase 4 of the agent rename (coder -> developer) — data migration that
renames ``agent_id`` (and the related ``agent_dir`` path component)
wherever the old ``coder`` agent identifier was persisted, so existing
rows continue to resolve to the renamed ``agents/developer/`` directory.

The migration is dual-driver:

* **SQLite**: ``MigrationRunner`` in ``daemon/migrations/runner.py``
  consuming ``daemon/migrations/versions/20260626_000001_rename_coder_to_developer.sql``
  — applies UPDATE statements to all five persisted tables.
  ``daemon/repositories/factory.py:run_migrations()`` was the legacy
  Python path; the coder→developer block was removed during phase 4.
* **PostgreSQL**: ``EnsembleManager._ensure_postgres_columns()`` in
  ``daemon/manager.py`` — applies the same UPDATE statements via
  ``self._engine.begin()``. The ``.sql`` migration runner is a NO-OP
  on PostgreSQL, so the equivalent UPDATEs must live in
  ``_ensure_postgres_columns`` (mirrored in this test by reading the
  production ``.sql`` file directly).

These tests verify:

1. The SQLite MigrationRunner correctly renames ``coder`` -> ``developer``.
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
from datetime import datetime, timezone
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

# Subject under test — the SQLite migration is now applied via the SQL
# migration runner over the .sql file in daemon/migrations/versions/.
# ``daemon/repositories/factory.py:run_migrations()`` is no longer the
# production path for the rename (the legacy Python UPDATE block was
# removed during phase 4). The PostgreSQL dual-engine path is handled
# by ``EnsembleManager._ensure_postgres_columns`` in ``daemon/manager.py``.
from daemon.migrations.runner import MigrationRunner


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


# Import SchemaMigration for the isolated-migration helper below. The
# runner does not register it via SQLModel.metadata (it creates the
# schema_migrations table via raw DDL in ``ensure_migrations_table``),
# so the test must import it explicitly when pre-marking other
# migrations as applied.
from daemon.migrations.models import SchemaMigration  # noqa: E402


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
    """Run the SQLite coder→developer migration under test (production function).

    Phase 4 of the rename moved the SQLite migration out of
    ``daemon/repositories/factory.py:run_migrations()`` and into
    ``daemon/migrations/versions/20260626_000001_rename_coder_to_developer.sql``
    which is applied via ``MigrationRunner``. This helper mirrors that
    production path BUT isolates the test to JUST the rename migration by
    pre-marking all other migrations as already-applied. Running the full
    chain would destroy test fixtures: the 20260402 session→instance
    rename performs ``DROP TABLE … ; ALTER TABLE … RENAME`` patterns
    whose ``INSERT INTO … SELECT … FROM old`` data copy no-ops when the
    SQLModel-created test schema already has the new column names (the
    runner treats ``no such column`` as idempotent), then drops the
    original table and loses the seeded test rows.

    Idempotency: the second call short-circuits because the rename
    migration version is already recorded in ``schema_migrations``;
    ``run_pending_migrations`` returns an empty list with no work
    performed.
    """
    runner = MigrationRunner(engine)
    runner.ensure_migrations_table()
    target = next(
        (
            m for m in runner.discover_migrations()
            if "rename" in m.name and "coder" in m.name
        ),
        None,
    )
    if target is None:
        raise RuntimeError(
            "coder→developer migration file not found in "
            "daemon/migrations/versions/"
        )

    # Pre-mark all OTHER migrations as applied so ``run_pending_migrations``
    # only picks up the rename migration. This isolates the test to the
    # rename contract; running the full chain would clobber seeded rows.
    #
    # Dedupe by version: ``discover_migrations`` can return multiple
    # ``MigrationFile`` objects with the same version when a migrations
    # directory contains more than one ``.sql`` file sharing a version
    # prefix (e.g. ``20260628_000002_drop_admission_legacy.sql`` and
    # ``20260628_000002_drop_job_queue_legacy_columns.sql``). Pre-marking
    # both would INSERT two rows with the same ``schema_migrations.version``
    # and fail the UNIQUE constraint on ``version PRIMARY KEY``. Since
    # ``get_pending_migrations`` filters by version equality, marking the
    # first occurrence is enough to short-circuit the duplicate.
    applied = runner.get_applied_versions()
    now_iso = datetime.now(timezone.utc).isoformat()
    seen_versions: set[str] = set()
    with Session(engine) as session:
        for m in runner.discover_migrations():
            if (
                m.version != target.version
                and m.version not in applied
                and m.version not in seen_versions
            ):
                seen_versions.add(m.version)
                session.add(
                    SchemaMigration(
                        version=m.version,
                        name=m.name,
                        applied_at=now_iso,
                        execution_time_ms=0,
                        checksum=m.checksum,
                    )
                )
        session.commit()

    runner.run_pending_migrations()


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

        _run_sqlite_migration(sqlite_engine)

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

        _run_sqlite_migration(sqlite_engine)
        _run_sqlite_migration(sqlite_engine)  # second run must not raise

        assert (
            _fetch_column(
                sqlite_engine, "instances", "instance_id", instance_id, "agent_id"
            )
            == "developer"
        )

    def test_migration_no_coder_rows(self, sqlite_engine: Engine) -> None:
        """Migration on DB with no 'coder' rows -> no errors, tables still empty."""
        # Tables exist (created by fixture) but contain no rows.

        _run_sqlite_migration(sqlite_engine)  # must not raise

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

        _run_sqlite_migration(sqlite_engine)

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


# ═════════════════════════════════════════════════════════════════════════════
# Part B: Coder Agent ID Coverage Tests
# ═════════════════════════════════════════════════════════════════════════════
# These tests verify that DB rows / enqueue requests carrying
# ``agent_id='coder'`` resolve correctly via the registry AFTER the alias
# removal.
#
# Registry now has NO alias mapping (``AGENT_ID_ALIASES = {}``), and ``coder``
# is a real, registered standalone agent at ``agents/coder/``. So:
#   resolve_pure_id("coder") → "coder"          (standalone agent, no alias hop)
#   resolve_pure_id("developer") → "developer"  (canonical agent)
#   get_resolved("coder") → coder AgentMetadata (path=/agents/coder)
#   get_resolved("developer") → developer AgentMetadata (path=/agents/developer)
#
# Coverage scope:
#   - ``_restore_instance()`` must load coder's metadata when DB row has
#     ``agent_id='coder'`` and complete the restore without raising. Today
#     ``coder`` is registered, so the lookup succeeds directly (no alias).
#   - ``job_queue_service.enqueue()`` must create a job with
#     ``agent_id='coder'`` and ``agent_dir=/agents/coder`` when the caller
#     requests the standalone coder agent.
#
# Historical context: before the alias removal these tests asserted that
# ``resolve_pure_id('coder')`` mapped to ``'developer'`` via
# ``AGENT_ID_ALIASES``. That mapping is gone now; the tests pin the
# post-removal contract instead.
# ═════════════════════════════════════════════════════════════════════════════


import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from daemon import constants
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.job_queue_service import JobQueueService


# Test system project ID — mirrors tests/job_queue/conftest.py so the
# alias-resolution enqueue tests can run from tests/unit/ without depending
# on that conftest's autouse fixture.
_TEST_SYSTEM_PROJECT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"


@pytest.fixture(autouse=True)
def _setup_system_default_project():
    """Set SYSTEM_DEFAULT_PROJECT_ID so normalize_project_id() works.

    job_queue_service.enqueue() calls normalize_project_id() internally
    (see daemon/services/job_queue_service.py:351) which raises
    RuntimeError if SYSTEM_DEFAULT_PROJECT_ID is None. The
    tests/job_queue/conftest.py fixture that handles this is NOT
    applied to tests/unit/, so we declare it locally.
    """
    original = constants.SYSTEM_DEFAULT_PROJECT_ID
    constants.SYSTEM_DEFAULT_PROJECT_ID = _TEST_SYSTEM_PROJECT_ID
    try:
        yield
    finally:
        constants.SYSTEM_DEFAULT_PROJECT_ID = original


class TestRestoreInstanceWithCoderAgentId:
    """Verify ``_restore_instance()`` handles ``agent_id='coder'`` correctly.

    After the alias removal, ``coder`` is a standalone registered agent at
    ``agents/coder/``. ``_restore_instance()`` looks up the agent via
    ``registry.get_resolved(meta.agent_id)``, which (with no aliases)
    resolves directly to coder's metadata. The restore must complete
    without raising ``ValueError('Agent not found: coder')``.
    """

    @staticmethod
    def _make_mock_manager() -> tuple[MagicMock, MagicMock]:
        """Build the mock manager + cancellation service used by restore tests.

        Centralizes the boilerplate so the test methods can focus on the
        alias resolution contract. Returns
        ``(mock_manager, mock_cancellation_service)`` since
        ``InstanceLifecycleService`` is constructed with both.
        """
        mock_manager = MagicMock()
        mock_cancellation_service = MagicMock()

        mock_manager._instance_repository = MagicMock()
        mock_manager._project_repository = MagicMock()
        mock_manager._engine = MagicMock()
        mock_manager._live_hub = MagicMock()
        mock_manager._checkpointer = None
        mock_manager._compactor = None
        mock_manager.instances = {}
        mock_manager.prompt_cache = MagicMock()
        mock_manager._mcp_service = None

        mock_config = MagicMock()
        mock_config.queue.llm_retry_transient_attempts = 3
        mock_config.queue.llm_retry_timeout_attempts = 2
        mock_config.llm.base_url = None
        mock_config.llm.api_key = "test-key"
        mock_config.llm.model = "gpt-4"
        mock_config.llm.model_vision = False
        mock_config.llm.temperature = 0.7
        mock_config.llm.request_timeout = 60
        mock_config.limits.graph_recursion_limit = 1000
        mock_manager.config = mock_config

        return mock_manager, mock_cancellation_service

    def test_restore_instance_with_coder_agent_id_does_not_raise(self):
        """``_restore_instance`` with ``agent_id='coder'`` loads coder's metadata.

        After the alias removal, ``coder`` is a registered standalone agent
        at ``agents/coder/``. ``_restore_instance()`` calls
        ``registry.get_resolved(meta.agent_id)`` which returns coder's
        metadata directly (no alias hop). The restore must succeed.

        Before the alias removal this test simulated a stale DB row that
        relied on ``coder`` → ``developer`` alias resolution to succeed.
        The new contract is simpler: ``coder`` resolves to coder, period.
        """
        # ── Mock manager ─────────────────────────────────────────────────────
        mock_manager, mock_cancellation_service = self._make_mock_manager()
        service = InstanceLifecycleService(mock_manager, mock_cancellation_service)

        # ── Mock Instance row with agent_id='coder' (the standalone agent) ──
        mock_meta = MagicMock()
        mock_meta.instance_id = "stale-instance-001"
        mock_meta.agent_id = "coder"           # ← standalone coder agent
        mock_meta.agent_dir = "/agents/coder"  # ← coder's on-disk path
        mock_meta.agent_tag = None             # ← base version (no tag) on restore
        mock_meta.parent_id = None
        mock_meta.instance_metadata = {"mcp_tool_names": []}

        # ── Patch registry and manager helpers ───────────────────────────────
        with (
            patch("daemon.services.instance_lifecycle.get_registry") as mock_get_registry,
            patch("daemon.services.instance_lifecycle.append_context_key") as mock_append_ctx,
            patch("daemon.manager.load_and_cache_prompt") as mock_load_prompt,
            patch("daemon.manager.build_instance_graph") as mock_build_graph,
            patch("daemon.manager.create_instance_tools") as mock_create_tools,
        ):
            # Configure the mock registry: ``_restore_instance`` now calls
            # ``get_version(agent_id, agent_tag)`` first and only falls back
            # to ``get_resolved`` when that returns None (base-version case).
            # We stub ``get_version`` → None so the test exercises the
            # ``get_resolved`` fallback, which returns coder's metadata
            # directly (no alias hop, no separate ``resolve_pure_id`` call).
            mock_registry = MagicMock()
            mock_registry.get_version.return_value = None

            # get_resolved('coder') returns coder's metadata directly.
            mock_coder_meta = MagicMock()
            mock_coder_meta.path = Path("/agents/coder")
            mock_coder_meta.llm_model = None
            mock_registry.get_resolved.side_effect = lambda aid: (
                mock_coder_meta if aid == "coder" else None
            )
            mock_get_registry.return_value = mock_registry

            mock_load_prompt.return_value = ("You are a coder.", 10)
            mock_create_tools.return_value = []
            mock_build_graph.return_value = MagicMock()
            mock_append_ctx.return_value = "You are a coder."

            # ── Execute ────────────────────────────────────────────────────
            # Must succeed because 'coder' resolves to a registered agent.
            result = service._restore_instance("stale-instance-001", mock_meta)

            # ── Verify the registry was consulted with 'coder' ───────────
            # With the alias map empty, ``_restore_instance`` looks up the
            # agent via ``registry.get_resolved`` and uses ``meta.agent_id``
            # directly. No ``resolve_pure_id`` alias hop is needed any more.
            mock_registry.get_resolved.assert_called_with("coder")
            # The graph must be built and stored in instances dict
            assert result is not None
            mock_build_graph.assert_called_once()
            mock_create_tools.assert_called_once()

    def test_restore_instance_with_developer_agent_id_still_works(self):
        """_restore_instance with canonical 'developer' agent_id still works.

        Sanity check: resolving an already-canonical ID should be a no-op.
        """
        mock_manager, mock_cancellation_service = self._make_mock_manager()
        service = InstanceLifecycleService(mock_manager, mock_cancellation_service)

        mock_meta = MagicMock()
        mock_meta.instance_id = "fresh-instance-002"
        mock_meta.agent_id = "developer"  # ← already canonical
        mock_meta.agent_dir = "/agents/developer"
        mock_meta.agent_tag = None         # ← base version (no tag) on restore
        mock_meta.parent_id = None
        mock_meta.instance_metadata = {"mcp_tool_names": []}

        with (
            patch("daemon.services.instance_lifecycle.get_registry") as mock_get_registry,
            patch("daemon.services.instance_lifecycle.append_context_key") as mock_append_ctx,
            patch("daemon.manager.load_and_cache_prompt") as mock_load_prompt,
            patch("daemon.manager.build_instance_graph") as mock_build_graph,
            patch("daemon.manager.create_instance_tools") as mock_create_tools,
        ):
            # ``_restore_instance`` calls ``get_version`` first and falls
            # back to ``get_resolved`` when it returns None. Stub the
            # base-version lookup to None so the fallback is exercised.
            mock_registry = MagicMock()
            mock_registry.get_version.return_value = None
            mock_developer_meta = MagicMock()
            mock_developer_meta.path = Path("/agents/developer")
            mock_developer_meta.llm_model = None
            mock_registry.get_resolved.side_effect = lambda aid: (
                mock_developer_meta if aid == "developer" else None
            )
            mock_get_registry.return_value = mock_registry

            mock_load_prompt.return_value = ("You are a developer.", 10)
            mock_create_tools.return_value = []
            mock_build_graph.return_value = MagicMock()
            mock_append_ctx.return_value = "You are a developer."

            result = service._restore_instance("fresh-instance-002", mock_meta)

            # With the alias map empty, ``_restore_instance`` looks up the
            # agent via ``registry.get_resolved`` and uses ``meta.agent_id``
            # directly. No ``resolve_pure_id`` alias hop is needed any more.
            mock_registry.get_resolved.assert_called_with("developer")
            assert result is not None


class TestJobQueueEnqueueWithCoderAgentId:
    """Verify ``job_queue_service.enqueue()`` handles ``agent_id='coder'``.

    After the alias removal, ``coder`` is a registered standalone agent.
    Both the idempotency path and the regular enqueue path must:
      * resolve ``"coder"`` via ``registry.get_resolved()`` to coder's
        metadata (no alias hop)
      * create the job with ``agent_id="coder"`` and
        ``agent_dir="/agents/coder"``
    """

    @pytest.fixture
    def mock_repository(self):
        """Minimal mock JobRepository for enqueue()."""
        repo = MagicMock()
        repo.find_by_idempotency_key = MagicMock(return_value=None)
        repo.create = MagicMock()

        def _create_or_get_side_effect(**kwargs):
            key = kwargs.get("idempotency_key")
            existing = repo.find_by_idempotency_key(key)
            if existing is not None:
                return existing, False
            new_job = repo.create(**kwargs)
            return new_job, True

        repo.create_or_get_by_idempotency_key = MagicMock(
            side_effect=_create_or_get_side_effect
        )
        return repo

    @pytest.fixture
    def mock_lock_manager(self):
        return MagicMock()

    @pytest.fixture
    def mock_queue_repo(self):
        repo = MagicMock()
        mock_queue = MagicMock()
        mock_queue.queue_id = "system-fifo-queue-id"
        mock_queue.project_id = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"
        mock_queue.queue_name = "system_fifo_queue"

        def get_by_name(project_id, queue_name):
            return mock_queue

        repo.get_by_name = MagicMock(side_effect=get_by_name)
        repo.get = MagicMock(return_value=None)
        return repo

    @pytest.fixture
    def service(self, mock_repository, mock_lock_manager, mock_queue_repo):
        return JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
        )

    def _make_mock_registry_coder_resolves_to_coder(self):
        """Registry mock returning canonical metadata for ``coder`` and ``developer``.

        After the alias removal, ``AGENT_ID_ALIASES`` is empty and ``coder``
        is a real agent at ``/agents/coder``. ``enqueue`` looks up the agent
        via ``registry.get_resolved(agent_id)`` (see
        ``daemon/services/job_queue_service.py:577,689``) — which with no
        aliases is functionally ``registry.get``. The mock therefore:
          * returns coder metadata (path=``/agents/coder``) for
            ``get_resolved("coder")``
          * returns developer metadata (path=``/agents/developer``) for
            ``get_resolved("developer")``
          * returns ``None`` for any other id.
        Mirrors the production ``registry.get_resolved`` semantics with no
        alias hops.
        """
        registry = MagicMock()

        mock_coder_meta = MagicMock()
        mock_coder_meta.path = Path("/agents/coder")
        mock_developer_meta = MagicMock()
        mock_developer_meta.path = Path("/agents/developer")

        def _get_resolved(aid: str):
            if aid == "coder":
                return mock_coder_meta
            if aid == "developer":
                return mock_developer_meta
            return None

        registry.get_resolved.side_effect = _get_resolved
        return registry

    @pytest.mark.asyncio
    async def test_enqueue_with_coder_agent_id_succeeds(
        self, service, mock_repository, mock_queue_repo
    ):
        """``enqueue(agent_id='coder')`` resolves to coder and creates a job.

        After the alias removal, ``coder`` is a registered standalone agent,
        so ``registry.get_resolved('coder')`` returns coder's metadata.
        ``enqueue`` uses this to derive ``agent_id`` and ``agent_dir`` for
        the new job. The job must be created with the coder identity
        (agent_id="coder", agent_dir="/agents/coder"), NOT the developer
        identity.
        """
        expected_job = MagicMock()
        expected_job.job_id = "new-job-from-coder"
        mock_repository.create.return_value = expected_job

        with patch(
            "daemon.services.job_queue_service.get_registry",
            return_value=self._make_mock_registry_coder_resolves_to_coder(),
        ):
            result = await service.enqueue(
                agent_id="coder",          # standalone coder agent
                message="test message",
                source="api",
            )

        assert result.job_id == "new-job-from-coder"
        mock_repository.create.assert_called_once()
        # The job must be created with the resolved coder identity,
        # NOT a developer alias.
        call_kwargs = mock_repository.create.call_args.kwargs
        assert call_kwargs["agent_id"] == "coder", (
            f"Expected agent_id='coder' in create(), got {call_kwargs['agent_id']!r}"
        )
        assert call_kwargs["agent_dir"] == "/agents/coder", (
            f"Expected agent_dir='/agents/coder' in create(), got {call_kwargs['agent_dir']!r}"
        )

    @pytest.mark.asyncio
    async def test_enqueue_with_coder_and_idempotency_key_succeeds(
        self, service, mock_repository, mock_queue_repo
    ):
        """``enqueue`` with ``idempotency_key`` and ``agent_id='coder'`` works.

        Exercises the idempotency code path (``daemon/services/job_queue_service.py``
        around line 577) which independently resolves the agent via
        ``registry.get_resolved``. With the alias removed, ``coder``
        resolves to the standalone coder agent and the new job is
        created with coder identity.
        """
        expected_job = MagicMock()
        expected_job.job_id = "idempotent-job-from-coder"
        mock_repository.create.return_value = expected_job

        with patch(
            "daemon.services.job_queue_service.get_registry",
            return_value=self._make_mock_registry_coder_resolves_to_coder(),
        ):
            result = await service.enqueue(
                agent_id="coder",              # standalone coder agent
                message="test message",
                source="api",
                idempotency_key="unique-key-001",  # ← triggers idempotency path
            )

        assert result.job_id == "idempotent-job-from-coder"
        mock_repository.create_or_get_by_idempotency_key.assert_called_once()
        call_kwargs = mock_repository.create_or_get_by_idempotency_key.call_args.kwargs
        assert call_kwargs["agent_id"] == "coder", (
            f"Expected agent_id='coder' in create_or_get_by_idempotency_key(), "
            f"got {call_kwargs['agent_id']!r}"
        )
        assert call_kwargs["agent_dir"] == "/agents/coder", (
            f"Expected agent_dir='/agents/coder' in create_or_get_by_idempotency_key(), "
            f"got {call_kwargs['agent_dir']!r}"
        )

    @pytest.mark.asyncio
    async def test_enqueue_with_developer_agent_id_still_works(
        self, service, mock_repository, mock_queue_repo
    ):
        """``enqueue(agent_id='developer')`` still works (sanity check).

        Canonical ``developer`` agent_id must not regress — it resolves
        to the developer agent and creates the job with developer
        identity (agent_id="developer", agent_dir="/agents/developer").
        """
        expected_job = MagicMock()
        expected_job.job_id = "new-job-from-developer"
        mock_repository.create.return_value = expected_job

        with patch(
            "daemon.services.job_queue_service.get_registry",
            return_value=self._make_mock_registry_coder_resolves_to_coder(),
        ):
            result = await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
            )

        assert result.job_id == "new-job-from-developer"
        call_kwargs = mock_repository.create.call_args.kwargs
        assert call_kwargs["agent_id"] == "developer"
        assert call_kwargs["agent_dir"] == "/agents/developer"
