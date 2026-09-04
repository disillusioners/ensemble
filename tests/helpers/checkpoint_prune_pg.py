"""Shared harness for the C3 blob-prune test suites (PR4).

Provides a REAL PostgreSQL backend for the reference-aware
``checkpoint_blobs`` prune tests:

* probes PostgreSQL (env-overridable, same ``PG_TEST_*`` convention as
  ``tests/postgres/conftest.py``) and issues a loud ``pytest.skip`` when
  unavailable — we never silently substitute a mock for the real saver;
* creates a DISPOSABLE DATABASE per test (``ensemble_blob_prune_<uuid>``,
  dropped on teardown) so tests are safe under pytest-xdist and never
  touch the shared ``ensemble_test`` schema;
* builds the production-shaped checkpointer stack:
  ``psycopg.AsyncConnection`` → ``AsyncPostgresSaver`` (+ ``setup()``)
  plus an ``asyncpg`` pool → ``PostgresCheckpointerAdapter`` — exactly
  what ``daemon/persistence.py::create_postgres_checkpointer`` builds.

It also exposes ``evict_langgraph_mocks()`` — the repo-standard fixture
body (see ``tests/integration/test_compaction_e2e.py``) that undoes the
root ``tests/conftest.py`` global langgraph mocks so the REAL langgraph
modules load inside pytest.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager

import pytest

PG_HOST = os.environ.get("PG_TEST_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
PG_ADMIN_DB = os.environ.get("PG_TEST_DB", "ensemble_test")

ADMIN_DSN = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_ADMIN_DB}"

# The langgraph module keys the root tests/conftest.py installs as mocks.
LANGGRAPH_MOCK_KEYS = [
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.prebuilt",
    "langgraph.constants",
    "langgraph.checkpoint",
    "langgraph.checkpoint.memory",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
]


def evict_langgraph_mocks() -> dict:
    """Evict the conftest langgraph mocks so the real package imports.

    Returns the saved mapping (pass to :func:`restore_langgraph_mocks`).
    Mirrors the ``restore_langgraph_modules`` fixture pattern used by the
    existing real-langgraph integration tests.

    Deliberately does NOT evict ``daemon.*`` modules: none of
    ``daemon.checkpoint_adapter`` / ``daemon.checkpoint_perf`` /
    ``daemon.services.checkpoint_prune`` import langgraph at module
    level, and evicting them would fork module identity when several of
    our test files share one pytest-xdist worker (a monkeypatch bound to
    the stale module object silently stops affecting production code —
    observed as a real flake in the combined xdist run).
    """
    saved = {}
    for key in LANGGRAPH_MOCK_KEYS:
        if key in sys.modules:
            saved[key] = sys.modules[key]
            del sys.modules[key]
    return saved


def restore_langgraph_mocks(saved: dict) -> None:
    for key in LANGGRAPH_MOCK_KEYS:
        if key in saved:
            sys.modules[key] = saved[key]


def require_postgres() -> None:
    """Loud skip when PostgreSQL is unreachable (never a silent mock).

    Safe to call from sync contexts (module import / sync fixtures).
    """
    import asyncpg

    async def _probe() -> None:
        conn = await asyncpg.connect(ADMIN_DSN, timeout=5)
        await conn.close()

    try:
        asyncio.run(_probe())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"BLOCKING real-saver test SKIPPED: PostgreSQL not available at "
            f"{ADMIN_DSN} ({type(exc).__name__}: {exc}). The C3 blob-prune "
            "gate requires a real PostgreSQL backend — do NOT merge PR4 on "
            "a skip; start PG (docker compose test stack or local) and re-run."
        )


async def create_disposable_db() -> tuple[str, str]:
    """Create a uniquely-named disposable database; return (name, dsn)."""
    import asyncpg

    name = f"ensemble_blob_prune_{uuid.uuid4().hex[:12]}"
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()
    dsn = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{name}"
    return name, dsn


async def drop_database(name: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        await conn.execute(
            f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'
        )
    except Exception:  # noqa: BLE001 — older PG without FORCE
        try:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                name,
            )
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
        except Exception:  # pragma: no cover — best-effort cleanup
            pass
    finally:
        await conn.close()


@asynccontextmanager
async def real_pg_checkpointer(dbname: str, dsn: str):
    """Yield the production-shaped (saver, pool, adapter) stack on a real DB.

    Mirrors ``daemon/persistence.py::create_postgres_checkpointer``:
    psycopg autocommit connection with ``prepare_threshold=0`` +
    ``dict_row`` → ``AsyncPostgresSaver`` + ``setup()``; asyncpg pool for
    the adapter's direct SQL. Everything is closed on exit.
    """
    import asyncpg
    import psycopg
    from psycopg.rows import dict_row

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from daemon.checkpoint_adapter import PostgresCheckpointerAdapter

    saver_conn = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    try:
        saver = AsyncPostgresSaver(conn=saver_conn)
        await saver.setup()
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
        try:
            adapter = PostgresCheckpointerAdapter(saver, pool)
            yield saver, pool, adapter
        finally:
            await pool.close()
    finally:
        await saver_conn.close()


@asynccontextmanager
async def real_pg_checkpointer_separate_pools(dbname: str, dsn: str):
    """Production-TOPOLOGY variant for the concurrency tests: TWO pools.

    The single-pool harness above puts the prune (the adapter's
    destructive DELETE) and every test-side read/staging query on ONE
    asyncpg pool. Real deployments never look like that: the prune runs
    on the maintenance pool connection while OTHER database actors
    (here: the test's staging, verification and the serializable partner
    transaction used to force a real 40001) hold different connections.
    Concurrency behavior — SSI conflict registration, deadlocks, retry
    interleavings — depends on cross-CONNECTION overlap, not on which
    pool object owns the connections, so the race-window tests run
    against this two-pool topology instead.

    Construction per side is identical to
    ``daemon/persistence.py::create_postgres_checkpointer`` (psycopg
    autocommit + ``prepare_threshold=0`` + ``dict_row`` → saver +
    ``setup()``; ``asyncpg.create_pool(dsn, min_size=1, max_size=5)`` →
    adapter), which is exactly how the daemon builds its single
    saver-conn-vs-asyncpg-pool stack.

    Yields ``(saver, prune_pool, prune_adapter, verify_pool,
    verify_adapter)``; everything is closed on exit.
    """
    import asyncpg
    import psycopg
    from psycopg.rows import dict_row

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from daemon.checkpoint_adapter import PostgresCheckpointerAdapter

    saver_conn = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    try:
        saver = AsyncPostgresSaver(conn=saver_conn)
        await saver.setup()
        prune_pool = None
        verify_pool = None
        try:
            prune_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
            verify_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
            yield (
                saver,
                prune_pool,
                PostgresCheckpointerAdapter(saver, prune_pool),
                verify_pool,
                PostgresCheckpointerAdapter(saver, verify_pool),
            )
        finally:
            # Close in reverse dependency order; each close is best-effort
            # so a failure on one does not leak the other.
            for pool in (verify_pool, prune_pool):
                if pool is not None:
                    try:
                        await pool.close()
                    except Exception:  # pragma: no cover — teardown safety
                        pass
    finally:
        await saver_conn.close()
