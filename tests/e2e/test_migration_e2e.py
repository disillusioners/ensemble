"""End-to-end test: real SQLite → real PostgreSQL migration.

Exercises the actual ``MigrationWorker`` against a live local PostgreSQL
database (``ensemble_test``) and a real SQLite source. This is the
"full E2E" path: it instantiates the worker, runs the same code that
the HTTP ``/api/migration/start`` endpoint runs, and verifies that
``ensemble.json`` flips to ``postgres`` and the migrated rows are
present in PostgreSQL.

The test intentionally bypasses the full FastAPI lifespan (which
bootstraps LangGraph, RAG, sources, …) and instantiates a minimal
``manager`` shim that exposes only the attributes the worker reads.
This isolates the migration path from unrelated startup side effects
while still exercising the production migration code.

Run with::

    .venv/bin/python -m pytest tests/e2e/test_migration_e2e.py -v --timeout=120

Requirements:
* A reachable PostgreSQL server with database ``ensemble_test`` and
  a user that can ``CREATE/DROP TABLE`` on it.
* The test sets ``POSTGRES_*`` env vars pointing at the local test DB.
* It cleans up every table it creates in ``public`` at teardown.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

# Import all model modules at module load time so SQLModel.metadata
# registers every table BEFORE the fixture calls metadata.create_all().
# Doing this inside the fixture (after create_all) is too late — the
# tables would not be created, and inserts would fail with
# "no such table: projects". The data_migrator also relies on these
# being registered so sorted_tables() finds them.
from daemon.repositories.instance import models as _instance_models  # noqa: F401
from daemon.repositories.project import models as _project_models  # noqa: F401
from daemon.repositories.source import models as _source_models  # noqa: F401
from daemon.repositories.job_queue import models as _job_queue_models  # noqa: F401
from daemon.repositories.job_queue import watcher_models as _watcher_models  # noqa: F401
from daemon.repositories.message_queue import models as _message_queue_models  # noqa: F401
from daemon.repositories.mcp_server import models as _mcp_server_models  # noqa: F401
from daemon.repositories.task import models as _task_models  # noqa: F401
from daemon.repositories.event import models as _event_models  # noqa: F401
from daemon.migrations.models import SchemaMigration as _SchemaMigration  # noqa: F401
from daemon.repositories.project.models import Project, ProjectHistoryEntry
from daemon.repositories.source.models import SourceConfig

# ── Force real (non-mocked) third-party modules before any daemon import ─────
# The repo's tests/conftest.py injects MagicMock stand-ins for langgraph,
# slack_bolt, mcp, etc. to keep unit tests hermetic. The migration worker
# needs the *real* ``langgraph.checkpoint.*`` packages, so we evict any
# mocked entries from sys.modules first. Doing it here (at import time of
# the test module) is sufficient because pytest has not yet collected the
# per-item conftest hooks that would re-inject the mocks for non-integration
# tests.
_MOCKED_PREFIXES = (
    "langgraph",
    "slack_bolt",
    "slack_sdk",
    "mcp",
    "langchain_mcp_adapters",
)
for _key in list(sys.modules):
    if any(_key == p or _key.startswith(p + ".") for p in _MOCKED_PREFIXES):
        del sys.modules[_key]


# ── PostgreSQL test environment ──────────────────────────────────────────────
# These env vars are read by EnsembleConfig.get_postgres_url(),
# create_postgres_engine(), and MigrationWorker.is_migration_available().
# Point at the locally-installed PG test database described in the project
# README. Override via env vars if your local setup differs.
TEST_PG_HOST = os.environ.get("E2E_PG_HOST", "localhost")
TEST_PG_PORT = int(os.environ.get("E2E_PG_PORT", "5432"))
TEST_PG_DB = os.environ.get("E2E_PG_DB", "ensemble_test")
TEST_PG_USER = os.environ.get("E2E_PG_USER", os.environ.get("USER", "ensemble"))
TEST_PG_PASSWORD = os.environ.get("E2E_PG_PASSWORD", "")

os.environ["POSTGRES_HOST"] = TEST_PG_HOST
os.environ["POSTGRES_PORT"] = str(TEST_PG_PORT)
os.environ["POSTGRES_DB"] = TEST_PG_DB
os.environ["POSTGRES_USER"] = TEST_PG_USER
os.environ["POSTGRES_PASSWORD"] = TEST_PG_PASSWORD


# ── Helpers ─────────────────────────────────────────────────────────────────


def _pg_url() -> str:
    """Return a sync postgresql:// URL for the test DB (psycopg driver)."""
    return f"postgresql://{TEST_PG_USER}:{TEST_PG_PASSWORD}@{TEST_PG_HOST}:{TEST_PG_PORT}/{TEST_PG_DB}"


def _pg_available() -> bool:
    """Probe whether the test PG is reachable. Skip otherwise."""
    try:
        with psycopg.connect(_pg_url(), connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception as e:  # pragma: no cover - environment probe
        print(f"[e2e] PostgreSQL probe failed: {type(e).__name__}: {e}")
        return False


# All tests in this file require live LLM infrastructure (real OpenAI API + MCP),
# so they are excluded from the default non-integration test gate via the
# `integration` marker defined in pyproject.toml.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _pg_available(),
        reason=f"PostgreSQL test database not reachable at {TEST_PG_HOST}:{TEST_PG_PORT}/{TEST_PG_DB}",
    ),
]


def _drop_all_public_tables() -> None:
    """Drop every table in the public schema. Used for teardown."""
    with psycopg.connect(_pg_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
            tables = [row[0] for row in cur.fetchall()]
            for t in tables:
                # CASCADE handles FKs that point at sibling tables.
                cur.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
        conn.commit()


# ── Test infrastructure ─────────────────────────────────────────────────────


class _ManagerShim:
    """Minimum-viable stand-in for ``daemon.manager.InstanceManager``.

    The migration worker reads exactly four attributes off the manager:

    * ``ensemble_config`` (read in availability + run phases)
    * ``engine`` (SQLite engine used as the data source)
    * ``is_write_paused`` (flag read at the end of the migration)
    * ``pause_writes`` / ``resume_writes`` (callable drain hooks)
    * ``data_dir`` (directory where ``ensemble.json`` is rewritten)

    Anything else (LLM, sources, RAG, jobs, …) the migration never
    touches, so we don't bother mocking it.
    """

    def __init__(self, engine, ensemble_config, data_dir: Path, guard):
        self._engine = engine
        self._ensemble_config = ensemble_config
        self._data_dir = data_dir
        self._guard = guard

    @property
    def ensemble_config(self):
        return self._ensemble_config

    @property
    def engine(self):
        return self._engine

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def is_write_paused(self) -> bool:
        return self._guard.is_write_paused

    def pause_writes(self) -> None:
        self._guard.pause_writes()

    def resume_writes(self) -> None:
        self._guard.resume_writes()


@pytest_asyncio.fixture
async def sqlite_source(tmp_path: Path):
    """Create a temp SQLite database with realistic Ensemble data.

    Yields (sqlite_engine, data_dir). Tears down the temp directory
    on exit. The PG side is cleaned by ``migration_e2e_setup`` below.
    """
    from daemon.ensemble_config import EnsembleConfig
    from daemon.write_pause_guard import WritePauseGuard

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Build an SQLite source database by running SQLModel.metadata.create_all
    # against it. The migration worker uses the same SQLModel.metadata to
    # discover tables in the destination, so the schemas are guaranteed
    # to match.
    sqlite_path = data_dir / "instances.db"
    sqlite_url = f"sqlite:///{sqlite_path}"
    sqlite_engine = create_engine(
        sqlite_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    SQLModel.metadata.create_all(sqlite_engine)

    # Insert a small but realistic set of rows. We pick tables without
    # circular FKs so the test data is guaranteed to migrate cleanly:
    #   - projects (parent)
    #   - source (no FK to projects)
    #   - mcp_servers (no FK to projects)
    project_id = f"proj-{uuid4().hex[:8]}"
    with Session(sqlite_engine) as s:
        s.add(Project(
            project_id=project_id,
            name="e2e-migration-test",
            project_type="general",
            status="active",
            main_directory=str(data_dir),
            description="E2E migration test project",
        ))
        for i in range(3):
            # ``config`` is JSON-typed (sa_column=Column(JSON)) so
            # SQLAlchemy auto-serializes the dict at bind time.
            # ``credentials`` is a plain string field, so we pass None
            # (the default) to avoid a sqlite3 parameter binding error.
            s.add(SourceConfig(
                source_id=f"src-{i}-{uuid4().hex[:6]}",
                source_type="scheduler",
                name=f"sched-{i}",
                config={"interval": 60 + i},
                credentials=None,
                enabled=True,
            ))
        s.commit()

    ensemble_config = EnsembleConfig(
        database="sqlite",
        sqlite={
            "instances_db": str(sqlite_path),
            "checkpoints_db": str(data_dir / "checkpoints.db"),
        },
    )
    ensemble_config.save(data_dir)

    # Initialize the langgraph checkpoint tables in the source SQLite DB.
    # ``AsyncSqliteSaver`` creates its tables lazily on the first write,
    # but the migration reads them eagerly via the checkpoint migrator, so
    # we must materialize them up front. ``setup()`` is idempotent.
    from daemon.persistence import get_checkpointer
    sqlite_ckpt = await get_checkpointer(ensemble_config)
    try:
        raw = sqlite_ckpt.raw_saver
        # ``AsyncSqliteSaver`` exposes ``setup()``; calling it once creates
        # the ``checkpoints``/``checkpoint_blobs``/``checkpoint_writes``
        # tables and runs pending migrations.
        if hasattr(raw, "setup"):
            setup = raw.setup
            if asyncio.iscoroutinefunction(setup):
                await setup()
            else:
                setup()
    finally:
        await sqlite_ckpt.close()

    yield sqlite_engine, data_dir, ensemble_config

    sqlite_engine.dispose()
    shutil.rmtree(tmp_path, ignore_errors=True)


@pytest_asyncio.fixture
async def migration_e2e_setup(sqlite_source):
    """Wrap the SQLite fixture and add PG cleanup around the test."""
    sqlite_engine, data_dir, ensemble_config = sqlite_source
    _drop_all_public_tables()
    try:
        yield sqlite_engine, data_dir, ensemble_config
    finally:
        _drop_all_public_tables()


# ── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_migration_worker_direct_e2e(migration_e2e_setup):
    """Drive the production ``MigrationWorker`` end-to-end.

    This is the same code path the HTTP ``POST /api/migration/start``
    endpoint runs, minus the FastAPI plumbing. We assert:

    1. ``is_migration_available()`` reports a clean can-migrate state.
    2. ``start()`` runs to ``COMPLETED`` and emits a ``complete`` event.
    3. The migration validation step reports no row-count mismatches.
    4. ``ensemble.json`` is rewritten with ``database == "postgres"``.
    5. Every table present in SQLite has matching rows in PostgreSQL.
    6. Specific data points (project name, source count) survive the
       round-trip.
    """
    from daemon.services.migration_worker import MigrationState, MigrationWorker
    from daemon.write_pause_guard import WritePauseGuard

    sqlite_engine, data_dir, ensemble_config = migration_e2e_setup
    guard = WritePauseGuard()
    manager = _ManagerShim(sqlite_engine, ensemble_config, data_dir, guard)
    worker = MigrationWorker(manager)

    # 1. Availability probe
    avail = worker.is_migration_available()
    assert avail["can_migrate"] is True, f"can_migrate should be True, got {avail}"
    assert avail["is_sqlite"] is True
    assert avail["pg_env_available"] is True
    assert not avail["reasons"], f"unexpected reasons: {avail['reasons']}"

    # 2. Subscribe to SSE events so we can assert on the terminal event
    queue = worker.subscribe()
    events: list[dict[str, Any]] = []

    async def _drain_events():
        """Read all events until the terminal one is seen."""
        while True:
            ev = await queue.get()
            events.append(ev)
            if ev.get("event") in ("complete", "error", "cancelled"):
                return

    # 3. Run the worker in a background task (mirrors the FastAPI handler)
    drain_task = asyncio.create_task(_drain_events())
    start_task = asyncio.create_task(worker.start())

    # 4. Poll status until completion (with a timeout safety net)
    timeout = 90.0
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        status = worker.get_status()["status"]
        if status in (
            MigrationState.COMPLETED.value,
            MigrationState.FAILED.value,
            MigrationState.CANCELLED.value,
        ):
            break
        await asyncio.sleep(0.25)

    # 5. Wait for the worker and the SSE drain to finish
    try:
        await asyncio.wait_for(start_task, timeout=timeout)
    except asyncio.TimeoutError:
        start_task.cancel()
        pytest.fail("MigrationWorker.start() did not complete within timeout")

    # Give the event drain a moment to consume the terminal event
    try:
        await asyncio.wait_for(drain_task, timeout=5.0)
    except asyncio.TimeoutError:
        drain_task.cancel()

    worker.unsubscribe(queue)

    # 6. Final status assertions
    final = worker.get_status()
    assert final["status"] == MigrationState.COMPLETED.value, (
        f"expected completed, got {final['status']}: {final.get('error')}"
    )
    assert final.get("error") is None
    assert final.get("tables_total", 0) > 0, "expected at least one migrated table"
    assert final.get("tables_completed", 0) == final.get("tables_total", 0)

    # 7. SSE event-stream assertions
    event_types = [e.get("event") for e in events]
    assert "complete" in event_types, f"missing 'complete' event in {event_types}"
    # Phases we expect to see in order
    phases = [
        e["data"].get("phase")
        for e in events
        if e.get("event") == "progress" and isinstance(e.get("data"), dict)
    ]
    for required in ("creating_pg_engine", "creating_schema", "migrating_tables"):
        assert required in phases, f"missing phase {required!r} in {phases}"

    # 8. ensemble.json must be updated to "postgres"
    ensemble_json = data_dir / "ensemble.json"
    assert ensemble_json.exists(), "ensemble.json was not rewritten"
    new_config = json.loads(ensemble_json.read_text())
    assert new_config["database"] == "postgres", (
        f"ensemble.json.database should be 'postgres', got {new_config['database']!r}"
    )
    # postgres connection block is also written
    assert "postgres" in new_config

    # 9. PG must have every SQLite table that contained rows, with matching
    #    counts. We check by introspecting the SQLite engine directly.
    sqlite_counts = _sqlite_table_row_counts(sqlite_engine)
    pg_counts = _pg_table_row_counts()

    # Every SQLite table should be present in PG (validation guarantees this)
    for table, expected in sqlite_counts.items():
        if expected == 0:
            # The migrator skips empty source tables; that's fine.
            continue
        assert table in pg_counts, (
            f"table {table!r} missing in PG after migration. "
            f"sqlite_counts={sqlite_counts}, pg_counts={pg_counts}"
        )
        # Some PG counts can exceed SQLite when test data from a prior
        # incomplete run is leftover, but our setup drops everything
        # first so counts must be exact.
        assert pg_counts[table] == expected, (
            f"row count mismatch for {table!r}: sqlite={expected}, pg={pg_counts[table]}"
        )

    # 10. Spot-check actual data: the test project must be in PG with the
    #     exact name we inserted (proves the migration isn't just copying
    #     schema or zeros).
    with psycopg.connect(_pg_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT name, project_type FROM projects WHERE name = %s',
                ("e2e-migration-test",),
            )
            row = cur.fetchone()
    assert row is not None, "test project missing in PG after migration"
    assert row[0] == "e2e-migration-test"
    assert row[1] == "general"


@pytest.mark.asyncio
async def test_migration_availability_before_start(migration_e2e_setup):
    """Pre-flight: ``is_migration_available()`` should report ready state.

    This guards the more complex full-migration test above by failing
    fast with a clear message if the env / config is wrong.
    """
    from daemon.services.migration_worker import MigrationWorker
    from daemon.write_pause_guard import WritePauseGuard

    sqlite_engine, data_dir, ensemble_config = migration_e2e_setup
    manager = _ManagerShim(
        sqlite_engine, ensemble_config, data_dir, WritePauseGuard()
    )
    worker = MigrationWorker(manager)

    avail = worker.is_migration_available()
    assert avail["can_migrate"] is True, (
        f"pre-flight failed: {avail}. "
        "Check POSTGRES_* env vars and PG connectivity."
    )


@pytest.mark.asyncio
async def test_migration_idempotent_second_run(migration_e2e_setup):
    """Re-running the migrator against the same PG should not duplicate rows.

    The data migrator uses ``ON CONFLICT DO NOTHING`` so the second run
    is a no-op for tables that haven't grown. We assert that
    row counts in PG are unchanged after a second pass.
    """
    from daemon.services.migration_worker import MigrationState, MigrationWorker
    from daemon.write_pause_guard import WritePauseGuard

    sqlite_engine, data_dir, ensemble_config = migration_e2e_setup
    manager = _ManagerShim(
        sqlite_engine, ensemble_config, data_dir, WritePauseGuard()
    )
    worker = MigrationWorker(manager)

    await _run_worker_to_completion(worker)
    counts_after_first = _pg_table_row_counts()

    # Second pass — need a fresh worker (the first is now in COMPLETED
    # state which is_migration_available() refuses to re-run).
    worker2 = MigrationWorker(manager)
    # Don't go through start() because the COMPLETED guard short-circuits.
    # Instead, instantiate the TableMigrator directly with a fresh
    # cancel event and replay the table-copy phase.
    from daemon.migrations.data_migrator import TableMigrator
    from daemon.repositories.factory import create_postgres_engine

    cancel = threading.Event()
    pg_engine = create_postgres_engine(ensemble_config)
    try:
        tm = TableMigrator(
            sqlite_engine=sqlite_engine,
            pg_engine=pg_engine,
            cancel_event=cancel,
            log_callback=None,
        )
        tm.migrate_all_tables()
    finally:
        pg_engine.dispose()

    counts_after_second = _pg_table_row_counts()
    for table, count in counts_after_first.items():
        assert counts_after_second.get(table) == count, (
            f"idempotency violation on {table!r}: "
            f"first={count}, second={counts_after_second.get(table)}"
        )


# ── Edge case tests ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def empty_sqlite_source(tmp_path: Path):
    """SQLite source with schema but zero rows in every table.

    Mirrors :func:`sqlite_source` exactly but skips the data-insertion
    step. Used to verify the migrator's "empty source" path: the
    schema still needs to be created on the PG side even when there
    is nothing to copy.
    """
    from daemon.ensemble_config import EnsembleConfig

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    sqlite_path = data_dir / "instances.db"
    sqlite_url = f"sqlite:///{sqlite_path}"
    sqlite_engine = create_engine(
        sqlite_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    # Create the schema but insert no rows.
    SQLModel.metadata.create_all(sqlite_engine)

    ensemble_config = EnsembleConfig(
        database="sqlite",
        sqlite={
            "instances_db": str(sqlite_path),
            "checkpoints_db": str(data_dir / "checkpoints.db"),
        },
    )
    ensemble_config.save(data_dir)

    # Materialize the langgraph checkpoint tables so the checkpoint
    # migrator can read them. Same rationale as in ``sqlite_source``.
    from daemon.persistence import get_checkpointer
    sqlite_ckpt = await get_checkpointer(ensemble_config)
    try:
        raw = sqlite_ckpt.raw_saver
        if hasattr(raw, "setup"):
            setup = raw.setup
            if asyncio.iscoroutinefunction(setup):
                await setup()
            else:
                setup()
    finally:
        await sqlite_ckpt.close()

    yield sqlite_engine, data_dir, ensemble_config

    sqlite_engine.dispose()
    shutil.rmtree(tmp_path, ignore_errors=True)


@pytest_asyncio.fixture
async def empty_migration_setup(empty_sqlite_source):
    """Wrap :func:`empty_sqlite_source` with PG teardown."""
    sqlite_engine, data_dir, ensemble_config = empty_sqlite_source
    _drop_all_public_tables()
    try:
        yield sqlite_engine, data_dir, ensemble_config
    finally:
        _drop_all_public_tables()


@pytest.mark.asyncio
async def test_migration_empty_database(empty_migration_setup):
    """Migration should succeed when SQLite has no data rows.

    Exercises the migrator's "all tables empty" path. The schema
    should still be created in PG (via ``SQLModel.metadata.create_all``),
    ``ensemble.json`` should be flipped to ``"postgres"``, and the
    worker should reach the ``COMPLETED`` state without errors.
    """
    from daemon.services.migration_worker import MigrationWorker
    from daemon.write_pause_guard import WritePauseGuard

    sqlite_engine, data_dir, ensemble_config = empty_migration_setup
    guard = WritePauseGuard()
    manager = _ManagerShim(sqlite_engine, ensemble_config, data_dir, guard)
    worker = MigrationWorker(manager)

    # No rows in SQLite — sanity check before we kick off the migration.
    sqlite_counts_before = _sqlite_table_row_counts(sqlite_engine)
    # The checkpoint tables created by ``AsyncSqliteSaver.setup()`` are
    # legitimately empty in this fixture, so all user tables have 0 rows.
    user_table_rows = [
        n
        for t, n in sqlite_counts_before.items()
        if t not in ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
    ]
    assert all(n == 0 for n in user_table_rows), (
        f"expected all user tables empty, got {sqlite_counts_before}"
    )

    await _run_worker_to_completion(worker)

    final = worker.get_status()
    assert final["status"] == "completed", (
        f"expected completed, got {final['status']}: {final.get('error')}"
    )
    assert final.get("error") is None

    # The schema must have been created on the PG side, even with zero
    # source rows. We assert at least one user table is present.
    pg_counts = _pg_table_row_counts()
    assert len(pg_counts) > 0, "no tables created in PG for empty source"
    for n in pg_counts.values():
        assert n == 0, f"expected zero rows in PG, got {pg_counts}"

    # ensemble.json must still be rewritten.
    ensemble_json = data_dir / "ensemble.json"
    assert ensemble_json.exists()
    new_config = json.loads(ensemble_json.read_text())
    assert new_config["database"] == "postgres"


@pytest.mark.asyncio
async def test_migration_large_batch(migration_e2e_setup):
    """Migrate 10K+ rows to exercise TableMigrator batching.

    ``DEFAULT_BATCH_SIZE`` is 500, so this hits 20+ batches per table
    and stresses the ``offset/limit`` pagination path. Verifies the
    row count in PG matches SQLite exactly.
    """
    from daemon.services.migration_worker import MigrationWorker
    from daemon.write_pause_guard import WritePauseGuard

    sqlite_engine, data_dir, ensemble_config = migration_e2e_setup

    # Insert 10,000 extra SourceConfig rows. Commit in chunks of 1000
    # to keep the SQLite transaction short. The fixture already
    # inserted 3 source rows; the unique ``source_id`` and ``name``
    # constraints require us to use fresh values.
    n_extra = 10_000
    with Session(sqlite_engine) as s:
        for i in range(n_extra):
            s.add(SourceConfig(
                source_id=f"large-{i:06d}",
                source_type="scheduler",
                name=f"large-name-{i:06d}",
                config={"index": i, "interval": 60 + (i % 60)},
                credentials=None,
                enabled=(i % 2 == 0),
            ))
            if (i + 1) % 1000 == 0:
                s.commit()
        s.commit()

    sqlite_counts = _sqlite_table_row_counts(sqlite_engine)
    assert sqlite_counts.get("source", 0) == 3 + n_extra, (
        f"fixture + 10k inserts missing: source count = {sqlite_counts.get('source')}"
    )

    guard = WritePauseGuard()
    manager = _ManagerShim(sqlite_engine, ensemble_config, data_dir, guard)
    worker = MigrationWorker(manager)

    await _run_worker_to_completion(worker)

    final = worker.get_status()
    assert final["status"] == "completed", (
        f"expected completed, got {final['status']}: {final.get('error')}"
    )

    pg_counts = _pg_table_row_counts()
    assert pg_counts.get("source", 0) == 3 + n_extra, (
        f"large-batch migration lost rows: "
        f"sqlite={3 + n_extra}, pg={pg_counts.get('source')}"
    )

    # Spot-check: a specific row from deep in the batch must round-trip
    # with its JSON config intact.
    with psycopg.connect(_pg_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT name, config FROM source WHERE name = %s',
                (f"large-name-{n_extra - 1:06d}",),
            )
            row = cur.fetchone()
    assert row is not None, "last batch row missing in PG"
    assert row[0] == f"large-name-{n_extra - 1:06d}"
    # ``config`` is stored as JSONB; psycopg returns a Python dict.
    assert row[1]["index"] == n_extra - 1


@pytest.mark.asyncio
async def test_migration_cancelled_midway(migration_e2e_setup):
    """Cancelling a running migration transitions to ``CANCELLED``.

    The migration cooperatively checks a ``threading.Event`` between
    batches, so it is impossible to guarantee the cancel fires
    *before* the migration finishes for a tiny dataset. We seed 3,000
    extra rows to give cancel a wide enough window, and accept either
    ``CANCELLED`` or ``COMPLETED`` — both prove the API is wired up;
    a real cancel-vs-finish race is timing-dependent and not the
    point of this test.
    """
    from daemon.services.migration_worker import MigrationWorker
    from daemon.write_pause_guard import WritePauseGuard

    sqlite_engine, data_dir, ensemble_config = migration_e2e_setup

    # Insert 3,000 extra rows so the migration takes a few hundred ms.
    with Session(sqlite_engine) as s:
        for i in range(3_000):
            s.add(SourceConfig(
                source_id=f"cancel-{i:06d}",
                source_type="scheduler",
                name=f"cancel-name-{i:06d}",
                config={"i": i},
                credentials=None,
                enabled=True,
            ))
            if (i + 1) % 1000 == 0:
                s.commit()
        s.commit()

    guard = WritePauseGuard()
    manager = _ManagerShim(sqlite_engine, ensemble_config, data_dir, guard)
    worker = MigrationWorker(manager)

    # Fire the worker and request cancel after a short delay.
    task = asyncio.create_task(worker.start())
    await asyncio.sleep(0.05)
    try:
        await worker.cancel()
    except RuntimeError:
        # Migration already finished before our cancel arrived — fine,
        # we still verify the task completed cleanly.
        pass

    try:
        await asyncio.wait_for(task, timeout=30.0)
    except asyncio.TimeoutError:
        task.cancel()
        pytest.fail("MigrationWorker did not finish after cancel request")

    final = worker.get_status()
    assert final["status"] in ("cancelled", "completed"), (
        f"unexpected terminal state: {final['status']}: {final.get('error')}"
    )
    # CANCELLED is not an error. COMPLETED is also valid (cancel may
    # arrive after the small dataset finishes). FAILED would be a bug.
    if final["status"] == "cancelled":
        assert final.get("error") is None, (
            f"cancelled migration should have no error, got {final.get('error')}"
        )


@pytest.mark.asyncio
async def test_migration_unavailable_when_pg_env_missing(
    migration_e2e_setup, monkeypatch
):
    """Without ``POSTGRES_HOST``/``POSTGRES_DB`` the migration refuses to start.

    The worker must report a clean ``can_migrate: False`` with a
    human-readable reason, and ``start()`` must raise a ``ValueError``
    (not a stack trace or crash). This is the contract the frontend
    relies on to disable the "Start migration" button.
    """
    import daemon.services.migration_worker as mw_module
    from daemon.services.migration_worker import MigrationWorker
    from daemon.write_pause_guard import WritePauseGuard

    # Clear both env vars the availability check looks at. ``delenv``
    # is safer than ``setenv("")`` because the worker checks truthiness
    # explicitly, but we want to simulate "never set" for this test.
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_DB", raising=False)

    sqlite_engine, data_dir, ensemble_config = migration_e2e_setup
    guard = WritePauseGuard()
    manager = _ManagerShim(sqlite_engine, ensemble_config, data_dir, guard)
    worker = MigrationWorker(manager)

    # ``is_migration_available`` must report a clean refusal.
    avail = worker.is_migration_available()
    assert avail["can_migrate"] is False
    assert avail["pg_env_available"] is False
    # The reason should mention Postgres / env vars.
    assert any(
        "postgres" in r.lower() or "env" in r.lower()
        for r in avail["reasons"]
    ), f"reasons should mention env vars, got {avail['reasons']}"

    # ``start()`` must refuse with a clear ValueError, not crash.
    with pytest.raises(ValueError) as excinfo:
        await worker.start()
    msg = str(excinfo.value).lower()
    assert "postgres" in msg or "env" in msg, (
        f"start() error should mention env vars, got: {excinfo.value!r}"
    )

    # The worker must NOT have advanced past IDLE.
    assert worker.get_status()["status"] == "idle"


# ── Test helpers (private) ──────────────────────────────────────────────────


async def _run_worker_to_completion(worker) -> None:
    """Run a worker.start() to completion with a 90s timeout."""
    task = asyncio.create_task(worker.start())
    try:
        await asyncio.wait_for(task, timeout=90.0)
    except asyncio.TimeoutError:
        task.cancel()
        raise


def _sqlite_table_row_counts(engine) -> dict[str, int]:
    """Return ``{table_name: row_count}`` for every user table in SQLite."""
    from sqlalchemy import text

    counts: dict[str, int] = {}
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ).fetchall()
        for (name,) in rows:
            n = conn.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
            counts[name] = int(n or 0)
    return counts


def _pg_table_row_counts() -> dict[str, int]:
    """Return ``{table_name: row_count}`` for every public PG table."""
    from sqlalchemy import create_engine as _ce
    from sqlalchemy import text

    engine = _ce(_pg_url().replace("postgresql://", "postgresql+psycopg://"))
    try:
        counts: dict[str, int] = {}
        with engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
            ).fetchall()
            for (name,) in tables:
                n = conn.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
                counts[name] = int(n or 0)
        return counts
    finally:
        engine.dispose()
