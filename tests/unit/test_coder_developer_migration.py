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


# ═════════════════════════════════════════════════════════════════════════════
# Part B: Alias-Resolution Crash-Recovery Tests
# ═════════════════════════════════════════════════════════════════════════════
# These tests verify that the backward-compat alias resolution works correctly
# when DB rows still contain the old `agent_id='coder'` value (simulating a
# partial/failed migration where the rename UPDATE never ran).
#
# Registry has AGENT_ID_ALIASES = {"coder": "developer"}, so:
#   resolve_pure_id("coder") → "developer"
#   resolve_pure_id("developer") → "developer"
#   get("developer") → valid AgentMetadata
#   get("coder") → None  (the canonical ID is "developer", not "coder")
#
# Bug that was fixed:
#   instance_lifecycle._restore_instance() and job_queue_service.enqueue() used
#   registry.get(meta.agent_id) DIRECTLY without alias resolution, so a DB row
#   with agent_id='coder' would raise ValueError("Agent not found: coder").
#
# Fix: both call sites now do
#   resolved = registry.resolve_pure_id(agent_id) or agent_id
#   agent_meta = registry.get(resolved)
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


class TestRestoreInstanceWithAlias:
    """Verify _restore_instance() handles stale 'coder' agent_id from DB.

    Simulates a DB row that still has agent_id='coder' (partial migration).
    The fix makes _restore_instance resolve the alias to 'developer' before
    calling registry.get(), so it doesn't raise ValueError.
    """

    def test_restore_instance_with_coder_agent_id_does_not_raise(self):
        """_restore_instance must not raise when DB row has agent_id='coder'.

        Reproducer: a partially-migrated DB where instances.agent_id still
        reads 'coder' (migration was not yet run, or server restarted before
        it could run). Before the fix, registry.get('coder') returned None
        → ValueError("Agent not found: coder"). After the fix, resolve_pure_id
        maps 'coder' → 'developer' and the restore succeeds.
        """
        # ── Mock manager ─────────────────────────────────────────────────────
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

        service = InstanceLifecycleService(mock_manager, mock_cancellation_service)

        # ── Mock Instance row with stale 'coder' agent_id ────────────────────
        mock_meta = MagicMock()
        mock_meta.instance_id = "stale-instance-001"
        mock_meta.agent_id = "coder"           # ← stale value (not yet migrated)
        mock_meta.agent_dir = "/agents/coder"   # ← stale path (not yet migrated)
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
            # Configure the mock registry so resolve_pure_id('coder') → 'developer'
            mock_registry = MagicMock()
            mock_registry.resolve_pure_id.return_value = "developer"  # alias resolution
            mock_registry.get.return_value = None                    # coder not canonical

            # Return valid metadata when asked for 'developer'
            mock_developer_meta = MagicMock()
            mock_developer_meta.path = Path("/agents/developer")
            mock_developer_meta.llm_model = None
            mock_registry.get.side_effect = lambda aid: (
                mock_developer_meta if aid == "developer" else None
            )
            mock_get_registry.return_value = mock_registry

            mock_load_prompt.return_value = ("You are a developer.", 10)
            mock_create_tools.return_value = []
            mock_build_graph.return_value = MagicMock()
            mock_append_ctx.return_value = "You are a developer."

            # ── Execute ────────────────────────────────────────────────────
            # Before the fix: raises ValueError("Agent not found: coder")
            # After the fix: succeeds because 'coder' is resolved to 'developer'
            result = service._restore_instance("stale-instance-001", mock_meta)

            # ── Verify alias resolution was called ─────────────────────────
            # resolve_pure_id must be called with the stale 'coder' value
            mock_registry.resolve_pure_id.assert_called_with("coder")
            # get() must be called with the resolved 'developer', not 'coder'
            mock_registry.get.assert_called_with("developer")
            # The graph must be built and stored in instances dict
            assert result is not None
            mock_build_graph.assert_called_once()
            mock_create_tools.assert_called_once()

    def test_restore_instance_with_developer_agent_id_still_works(self):
        """_restore_instance with canonical 'developer' agent_id still works.

        Sanity check: resolving an already-canonical ID should be a no-op.
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

        service = InstanceLifecycleService(mock_manager, mock_cancellation_service)

        mock_meta = MagicMock()
        mock_meta.instance_id = "fresh-instance-002"
        mock_meta.agent_id = "developer"  # ← already canonical
        mock_meta.agent_dir = "/agents/developer"
        mock_meta.parent_id = None
        mock_meta.instance_metadata = {"mcp_tool_names": []}

        with (
            patch("daemon.services.instance_lifecycle.get_registry") as mock_get_registry,
            patch("daemon.services.instance_lifecycle.append_context_key") as mock_append_ctx,
            patch("daemon.manager.load_and_cache_prompt") as mock_load_prompt,
            patch("daemon.manager.build_instance_graph") as mock_build_graph,
            patch("daemon.manager.create_instance_tools") as mock_create_tools,
        ):
            mock_registry = MagicMock()
            mock_registry.resolve_pure_id.return_value = "developer"
            mock_developer_meta = MagicMock()
            mock_developer_meta.path = Path("/agents/developer")
            mock_developer_meta.llm_model = None
            mock_registry.get.return_value = mock_developer_meta
            mock_get_registry.return_value = mock_registry

            mock_load_prompt.return_value = ("You are a developer.", 10)
            mock_create_tools.return_value = []
            mock_build_graph.return_value = MagicMock()
            mock_append_ctx.return_value = "You are a developer."

            result = service._restore_instance("fresh-instance-002", mock_meta)

            # resolve_pure_id called with 'developer', get called with 'developer'
            mock_registry.resolve_pure_id.assert_called_with("developer")
            mock_registry.get.assert_called_with("developer")
            assert result is not None


class TestJobQueueEnqueueWithAlias:
    """Verify job_queue_service.enqueue() handles stale 'coder' agent_id.

    Both the idempotency path and the regular enqueue path must resolve
    the 'coder' alias before calling registry.get(), otherwise
    ValueError("Agent not found: coder") is raised.
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

    def _make_mock_registry_resolve_coder_to_developer(self):
        """Registry mock: resolve_pure_id('coder')→'developer', get('developer')→valid."""
        registry = MagicMock()
        registry.resolve_pure_id.side_effect = lambda aid: {
            "coder": "developer",
            "developer": "developer",
        }.get(aid, aid)
        mock_meta = MagicMock()
        mock_meta.path = "/agents/developer"
        registry.get.side_effect = lambda aid: (
            mock_meta if aid == "developer" else None
        )
        return registry

    @pytest.mark.asyncio
    async def test_enqueue_with_coder_agent_id_succeeds(
        self, service, mock_repository, mock_queue_repo
    ):
        """enqueue(agent_id='coder') must not raise ValueError.

        Before the fix: registry.get('coder') returns None → ValueError.
        After the fix: resolve_pure_id('coder')→'developer', get('developer')→valid metadata → succeeds.
        """
        expected_job = MagicMock()
        expected_job.job_id = "new-job-from-coder"
        mock_repository.create.return_value = expected_job

        with patch(
            "daemon.services.job_queue_service.get_registry",
            return_value=self._make_mock_registry_resolve_coder_to_developer(),
        ):
            result = await service.enqueue(
                agent_id="coder",          # ← stale value
                message="test message",
                source="api",
            )

        assert result.job_id == "new-job-from-coder"
        mock_repository.create.assert_called_once()
        # The job must be created with the resolved agent_id 'developer', not 'coder'
        call_kwargs = mock_repository.create.call_args.kwargs
        assert call_kwargs["agent_id"] == "developer", (
            f"Expected agent_id='developer' in create(), got {call_kwargs['agent_id']!r}"
        )
        assert call_kwargs["agent_dir"] == "/agents/developer"

    @pytest.mark.asyncio
    async def test_enqueue_with_coder_and_idempotency_key_succeeds(
        self, service, mock_repository, mock_queue_repo
    ):
        """enqueue with idempotency_key and agent_id='coder' must not raise.

        This tests the idempotency path (lines 362-482 in job_queue_service.py)
        which has its own alias-resolution call site.
        """
        expected_job = MagicMock()
        expected_job.job_id = "idempotent-job-from-coder"
        mock_repository.create.return_value = expected_job

        with patch(
            "daemon.services.job_queue_service.get_registry",
            return_value=self._make_mock_registry_resolve_coder_to_developer(),
        ):
            result = await service.enqueue(
                agent_id="coder",              # ← stale value
                message="test message",
                source="api",
                idempotency_key="unique-key-001",  # ← triggers idempotency path
            )

        assert result.job_id == "idempotent-job-from-coder"
        mock_repository.create_or_get_by_idempotency_key.assert_called_once()
        call_kwargs = mock_repository.create_or_get_by_idempotency_key.call_args.kwargs
        assert call_kwargs["agent_id"] == "developer", (
            f"Expected agent_id='developer' in create_or_get_by_idempotency_key(), "
            f"got {call_kwargs['agent_id']!r}"
        )
        assert call_kwargs["agent_dir"] == "/agents/developer"

    @pytest.mark.asyncio
    async def test_enqueue_with_developer_agent_id_still_works(
        self, service, mock_repository, mock_queue_repo
    ):
        """enqueue(agent_id='developer') still works (sanity check).

        Canonical agent_id must not regress — it should still resolve
        correctly and create the job with the right values.
        """
        expected_job = MagicMock()
        expected_job.job_id = "new-job-from-developer"
        mock_repository.create.return_value = expected_job

        with patch(
            "daemon.services.job_queue_service.get_registry",
            return_value=self._make_mock_registry_resolve_coder_to_developer(),
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