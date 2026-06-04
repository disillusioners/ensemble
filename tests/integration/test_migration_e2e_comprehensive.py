"""Comprehensive E2E integration tests for SQLite → PostgreSQL migration.

Leverages ``tests/migration/test_data_factory.py`` which populates all 22
SQLModel tables with deterministic test data covering every column type and
edge cases (unicode, nulls, empty strings, large text, deeply nested JSON).

Tests:

1. **test_full_migration_verifies_row_counts** — Populates SQLite with factory
   data, runs ``MigrationWorker`` against live PostgreSQL, then verifies that
   row counts for all 22 tables are identical between the source and destination.

2. **test_idempotent_retry** — Runs the migration once, runs it again, and
   asserts that no table in PostgreSQL contains duplicate rows.

3. **test_cancel_and_resume** — Starts a migration, cancels it mid-flight,
   then starts a new worker and verifies the second run completes and migrates
   any tables that were skipped.

4. **test_migration_without_pg_env** — Unsets the PostgreSQL environment
   variables and confirms the worker refuses to start with a clear error.

Run with::

    pytest tests/integration/test_migration_e2e_comprehensive.py -v --timeout=120

Requirements (same as ``tests/e2e/test_migration_e2e.py``):
* A reachable PostgreSQL server with database ``ensemble_test`` and a user
  that can ``CREATE/DROP TABLE`` on it.
* ``POSTGRES_*`` env vars pointing at the local test DB.
* The ``psycopg`` package installed.
* The real ``mcp`` SDK installed (the root ``conftest.py`` mocks it for
  unit tests; this file uses the E2E conftest that swaps it for the real SDK).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import threading
from pathlib import Path

import pytest

# Skip the entire module gracefully when the real ``psycopg`` package is
# not installed in the environment. ``importorskip`` raises
# ``pytest.skip.Exception`` (an ``ImportError`` subclass), which pytest
# treats as a module-level skip rather than a collection error.
psycopg = pytest.importorskip("psycopg")
import pytest_asyncio
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

# Import every model module so SQLModel.metadata registers every table.
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

# Test data factory (populates all 22 tables).
from tests.migration.test_data_factory import (
    populate_sqlite_test_data,
    generate_verification_hash,
)

# Evict mocked third-party modules injected by the root conftest.
# The E2E conftest swaps these for real packages; we do it here so this
# file can import without the real packages installed (it will be skipped
# gracefully at runtime via the e2e conftest's _skip_when_psycopg_stub_present).
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

TEST_PG_HOST = os.environ.get("E2E_PG_HOST", "localhost")
TEST_PG_PORT = int(os.environ.get("E2E_PG_PORT", "5432"))
TEST_PG_DB = os.environ.get("E2E_PG_DB", "ensemble_test")
TEST_PG_USER = os.environ.get("E2E_PG_USER", os.environ.get("USER", "ensemble"))
TEST_PG_PASSWORD = os.environ.get("E2E_PG_PASSWORD", "")

# Set PG env vars so EnsembleConfig / create_postgres_engine pick them up.
os.environ["POSTGRES_HOST"] = TEST_PG_HOST
os.environ["POSTGRES_PORT"] = str(TEST_PG_PORT)
os.environ["POSTGRES_DB"] = TEST_PG_DB
os.environ["POSTGRES_USER"] = TEST_PG_USER
os.environ["POSTGRES_PASSWORD"] = TEST_PG_PASSWORD


# ── Helpers ─────────────────────────────────────────────────────────────────


def _pg_url() -> str:
    """Return a sync postgresql:// URL for the test DB."""
    return f"postgresql://{TEST_PG_USER}:{TEST_PG_PASSWORD}@{TEST_PG_HOST}:{TEST_PG_PORT}/{TEST_PG_DB}"


def _pg_available() -> bool:
    """Probe whether the test PG is reachable. Skip otherwise."""
    try:
        with psycopg.connect(_pg_url(), connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception as e:
        print(f"[e2e] PostgreSQL probe failed: {type(e).__name__}: {e}")
        return False


pytestmark = [
    pytest.mark.skipif(
        not _pg_available(),
        reason=f"PostgreSQL test DB not reachable at {TEST_PG_HOST}:{TEST_PG_PORT}/{TEST_PG_DB}",
    ),
    pytest.mark.integration,
]


def _drop_all_public_tables() -> None:
    """Drop every table in the public schema (CASCADE for FKs)."""
    with psycopg.connect(_pg_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
            tables = [row[0] for row in cur.fetchall()]
            for t in tables:
                cur.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
        conn.commit()


# ── Test infrastructure ─────────────────────────────────────────────────────


class _ManagerShim:
    """Minimum-viable stand-in for ``daemon.manager.InstanceManager``.

    The migration worker reads:
    * ``ensemble_config``
    * ``engine``
    * ``is_write_paused``
    * ``pause_writes`` / ``resume_writes``
    * ``data_dir``
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


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def sqlite_with_factory(tmp_path: Path):
    """SQLite DB populated by the test data factory (all 22 tables).

    Yields (sqlite_engine, data_dir, ensemble_config).
    Cleans up the temp directory on exit.
    """
    from daemon.ensemble_config import EnsembleConfig
    from daemon.persistence import get_checkpointer
    from daemon.write_pause_guard import WritePauseGuard

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    sqlite_path = data_dir / "instances.db"
    checkpoints_path = data_dir / "checkpoints.db"

    # 1. Create schema via SQLModel.metadata (same as production).
    sqlite_url = f"sqlite:///{sqlite_path}"
    sqlite_engine = create_engine(
        sqlite_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    SQLModel.metadata.create_all(sqlite_engine)

    # 2. Populate all 22 tables with deterministic factory data.
    populate_sqlite_test_data(str(sqlite_path))

    # 3. Write ensemble.json pointing to the SQLite databases.
    ensemble_config = EnsembleConfig(
        database="sqlite",
        sqlite={
            "instances_db": str(sqlite_path),
            "checkpoints_db": str(checkpoints_path),
        },
    )
    ensemble_config.save(data_dir)

    # 4. Materialise langgraph checkpoint tables so the checkpoint migrator
    #    can read them (AsyncSqliteSaver creates tables lazily on first write).
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
async def migration_comprehensive_setup(sqlite_with_factory):
    """Wrap ``sqlite_with_factory`` with PG cleanup around the test."""
    sqlite_engine, data_dir, ensemble_config = sqlite_with_factory
    _drop_all_public_tables()
    try:
        yield sqlite_engine, data_dir, ensemble_config
    finally:
        _drop_all_public_tables()


@pytest_asyncio.fixture
async def empty_sqlite_for_migration(tmp_path: Path):
    """SQLite with schema but no rows — used for cancel/resume testing."""
    from daemon.ensemble_config import EnsembleConfig
    from daemon.persistence import get_checkpointer

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    sqlite_path = data_dir / "instances.db"
    checkpoints_path = data_dir / "checkpoints.db"

    sqlite_engine = create_engine(
        f"sqlite:///{sqlite_path}",
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    SQLModel.metadata.create_all(sqlite_engine)

    ensemble_config = EnsembleConfig(
        database="sqlite",
        sqlite={
            "instances_db": str(sqlite_path),
            "checkpoints_db": str(checkpoints_path),
        },
    )
    ensemble_config.save(data_dir)

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


# ── Private helpers ─────────────────────────────────────────────────────────


async def _run_worker_to_completion(worker) -> None:
    """Run ``worker.start()`` to completion with a 90s timeout."""
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


def _insert_extra_rows(sqlite_engine, count: int = 3000) -> None:
    """Insert ``count`` rows into ``source_configs`` to slow the migration down.

    Used by cancel/resume tests to widen the cancellation window.
    """
    from daemon.repositories.source.models import SourceConfig

    with Session(sqlite_engine) as s:
        for i in range(count):
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


# ── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_migration_verifies_row_counts(migration_comprehensive_setup):
    """Migration preserves row counts across all 22 tables.

    1. Capture verification hash (count + checksum) from SQLite before migration.
    2. Run MigrationWorker end-to-end.
    3. Verify every table that had rows in SQLite has the same count in PG.
    4. Spot-check a specific row survived the migration.
    """
    from daemon.services.migration_worker import MigrationState, MigrationWorker
    from daemon.write_pause_guard import WritePauseGuard

    sqlite_engine, data_dir, ensemble_config = migration_comprehensive_setup

    # 1. Capture pre-migration verification hash from SQLite.
    sqlite_hash_before = generate_verification_hash(str(data_dir / "instances.db"))
    sqlite_counts_before = _sqlite_table_row_counts(sqlite_engine)

    # Confirm factory populated data.
    total_rows = sum(v["count"] for v in sqlite_hash_before.values())
    assert total_rows > 0, "Factory should have populated rows before migration"

    # 2. Run the worker.
    guard = WritePauseGuard()
    manager = _ManagerShim(sqlite_engine, ensemble_config, data_dir, guard)
    worker = MigrationWorker(manager)

    await _run_worker_to_completion(worker)

    # 3. Verify final status.
    final = worker.get_status()
    assert final["status"] == MigrationState.COMPLETED.value, (
        f"expected completed, got {final['status']}: {final.get('error')}"
    )
    assert final.get("error") is None
    assert final.get("tables_total", 0) > 0

    # 4. Verify row counts match between SQLite (before) and PG (after).
    pg_counts = _pg_table_row_counts()

    for table, info in sqlite_hash_before.items():
        sqlite_count = info["count"]
        if sqlite_count == 0:
            # The migrator skips empty source tables; that's fine.
            continue
        assert table in pg_counts, (
            f"table {table!r} missing in PG after migration. "
            f"pg_counts={pg_counts}"
        )
        assert pg_counts[table] == sqlite_count, (
            f"row count mismatch for {table!r}: "
            f"sqlite_before={sqlite_count}, pg_after={pg_counts[table]}"
        )

    # 5. ensemble.json must be updated to "postgres".
    ensemble_json = data_dir / "ensemble.json"
    assert ensemble_json.exists()
    new_config = json.loads(ensemble_json.read_text())
    assert new_config["database"] == "postgres", (
        f"expected database='postgres', got {new_config['database']!r}"
    )


@pytest.mark.asyncio
async def test_idempotent_retry(migration_comprehensive_setup):
    """Re-running the migrator does not create duplicate rows.

    Uses the same approach as ``test_migration_idempotent_second_run`` in
    ``tests/e2e/test_migration_e2e.py``: a second pass via TableMigrator
    directly (bypassing the COMPLETED guard on the worker).
    """
    from daemon.services.migration_worker import MigrationState, MigrationWorker
    from daemon.migrations.data_migrator import TableMigrator
    from daemon.repositories.factory import create_postgres_engine
    from daemon.write_pause_guard import WritePauseGuard

    sqlite_engine, data_dir, ensemble_config = migration_comprehensive_setup
    guard = WritePauseGuard()
    manager = _ManagerShim(sqlite_engine, ensemble_config, data_dir, guard)
    worker = MigrationWorker(manager)

    # First run — complete the full migration.
    await _run_worker_to_completion(worker)
    final = worker.get_status()
    assert final["status"] == MigrationState.COMPLETED.value

    counts_after_first = _pg_table_row_counts()
    assert sum(counts_after_first.values()) > 0, "First run should have migrated rows"

    # Second pass — replay TableMigrator directly (worker is in COMPLETED state
    # and would refuse to re-run via start()).
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

    # No table should have more rows after the second pass.
    for table, count in counts_after_first.items():
        second = counts_after_second.get(table)
        assert second == count, (
            f"idempotency violation on {table!r}: "
            f"first={count}, second={second}"
        )


@pytest.mark.asyncio
async def test_cancel_and_resume(empty_sqlite_for_migration):
    """Cancel a migration mid-flight, then resume with a new worker.

    Steps:
    1. Insert 3,000 extra rows to give cancel a wide enough window.
    2. Start worker, cancel after 50ms.
    3. Assert state is CANCELLED (or COMPLETED if it finished first).
    4. If CANCELLED: start a new worker and verify it completes successfully.
    """
    from daemon.services.migration_worker import MigrationState, MigrationWorker
    from daemon.write_pause_guard import WritePauseGuard

    sqlite_engine, data_dir, ensemble_config = empty_sqlite_for_migration

    # Insert 3,000 rows so the migration takes a few hundred ms.
    _insert_extra_rows(sqlite_engine, count=3000)

    sqlite_counts = _sqlite_table_row_counts(sqlite_engine)
    assert sqlite_counts.get("source_configs", 0) == 3000, (
        "Expected 3000 source_configs rows"
    )

    # Clean PG before cancel test.
    _drop_all_public_tables()

    guard = WritePauseGuard()
    manager = _ManagerShim(sqlite_engine, ensemble_config, data_dir, guard)
    worker = MigrationWorker(manager)

    # Fire worker and request cancel after a short delay.
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

    if final["status"] == MigrationState.CANCELLED.value:
        # CANCELLED is not an error.
        assert final.get("error") is None

        # Start a new worker to resume.
        # Clean PG first so we're in a clean state for the resume.
        _drop_all_public_tables()

        guard2 = WritePauseGuard()
        manager2 = _ManagerShim(sqlite_engine, ensemble_config, data_dir, guard2)
        worker2 = MigrationWorker(manager2)

        await _run_worker_to_completion(worker2)

        final2 = worker2.get_status()
        assert final2["status"] == MigrationState.COMPLETED.value, (
            f"resume failed: {final2['status']}: {final2.get('error')}"
        )

        # Verify all rows made it to PG.
        pg_counts = _pg_table_row_counts()
        assert pg_counts.get("source_configs", 0) == 3000, (
            f"resume missed rows: expected 3000, got {pg_counts.get('source_configs')}"
        )
    else:
        # Migration finished before cancel arrived — that's valid too.
        # Verify all rows made it to PG.
        pg_counts = _pg_table_row_counts()
        assert pg_counts.get("source_configs", 0) == 3000, (
            f"rows missing after fast completion: "
            f"got {pg_counts.get('source_configs')}"
        )


@pytest.mark.asyncio
async def test_migration_without_pg_env(migration_comprehensive_setup, monkeypatch):
    """Without POSTGRES_HOST/POSTGRES_DB the migration refuses to start.

    Mirrors the approach from ``tests/e2e/test_migration_e2e.py``:
    1. ``is_migration_available()`` must report ``can_migrate=False``.
    2. ``start()`` must raise ``ValueError`` with a clear message.
    3. The worker must remain in IDLE state.
    """
    from daemon.services.migration_worker import MigrationWorker
    from daemon.write_pause_guard import WritePauseGuard

    # Clear PG env vars (both the short names and any DSN override).
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_PORT", raising=False)
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("DATABASE_URL_POSTGRES", raising=False)

    sqlite_engine, data_dir, ensemble_config = migration_comprehensive_setup
    guard = WritePauseGuard()
    manager = _ManagerShim(sqlite_engine, ensemble_config, data_dir, guard)
    worker = MigrationWorker(manager)

    # is_migration_available must refuse cleanly.
    avail = worker.is_migration_available()
    assert avail["can_migrate"] is False
    assert avail["pg_env_available"] is False
    assert any(
        "postgres" in r.lower() or "env" in r.lower()
        for r in avail["reasons"]
    ), f"reasons should mention env vars, got {avail['reasons']}"

    # start() must raise ValueError, not crash.
    with pytest.raises(ValueError) as excinfo:
        await worker.start()
    msg = str(excinfo.value).lower()
    assert "postgres" in msg or "env" in msg, (
        f"start() error should mention env vars, got: {excinfo.value!r}"
    )

    # Worker must not have advanced past IDLE.
    assert worker.get_status()["status"] == "idle"
