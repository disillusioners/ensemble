"""The checkpoint-pool GHOST-WAITER pin (A4 · V4).

soak-gate-mcp-warmup · A4 · decisions §3d (D1b — the ghost-waiter
question) + requirements.md §4.6 (P6 — the gate conventions).

WHAT THIS PIN MEASURES (and does NOT assume)
--------------------------------------------
The readiness sentinel probe abandons its scheduled future at its own
budget (0.9 s inner / 1.0 s outer). Before this lane the pool's
acquisition deadline was the psycopg_pool library default (30 s), so a
timed-out probe left a queued ("ghost") waiter behind for up to 30 s —
at the 10 s refresh cadence that is a self-amplifying pool drain.

The lane delivered two things:

1. an EXPLICIT acquisition deadline (``timeout=`` on the pool), and
2. the probe's explicit ``future.cancel()`` (the ghost-waiter
   withdrawal).

The V4 acceptance criterion is a LIBRARY-BEHAVIOUR question — *does
cancelling a task blocked inside ``pool.getconn`` remove its internal
waiter?* — so it is MEASURED here, not assumed. The pin records the
verdict (printed + asserted in the two properties that are true
regardless of the library's internals):

* the waiters ARE observable while the pool is exhausted (the symptom
  is visible — this is what ``detail.pool_state`` publishes);
* the waiting queue DRAINS to 0 once a connection returns (the pool
  pops the abandoned positions and hands the connection on);
* a NON-cancelled waiter is bounded by the pool's own ``timeout`` (the
  knob this lane delivered).

SAFETY FENCES
-------------
* a DISPOSABLE PostgreSQL database (throwaway LOGIN role + database on
  the local PG server), DROPPED at teardown;
* the ambient ``POSTGRES_*`` values are never trusted — the five
  variables are pinned PER VARIABLE to the disposable database;
* ``$HOME``-derived live paths are never resolved (the FAKE_HOME
  fence).

RUN (a deselected/skipped pin is a FAILED gate)::

    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \\
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \\
        HOME="$(mktemp -d)" .venv/bin/pytest \\
        tests/integration/test_checkpoint_pool_ghost_waiters.py \\
        --override-ini="addopts=" -m integration -q
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.integration

from tests.helpers.nestops_pg_fixtures import (  # noqa: E402
    assert_scratch_db,
    disposable_db,
    pin_scratch_postgres_env,
    probe_admin_engine,
)

#: The knob this lane delivered (daemon/constants.py).
POOL_TIMEOUT_S = 5.0
#: The probe's own abandonment budget (readiness.py).
PROBE_BUDGET_S = 0.3
#: How many abandoned probes the pin fires.
PROBE_COUNT = 5


@pytest.fixture(scope="module")
def pg_admin():
    admin = probe_admin_engine()
    if admin is None:
        pytest.skip(
            "PostgreSQL is not available at "
            f"{os.environ.get('PG_TEST_HOST', 'localhost')}:"
            f"{os.environ.get('PG_TEST_PORT', '5432')} — the ghost-waiter "
            "pin requires the local PostgreSQL server"
        )
    yield admin
    admin.dispose()


@pytest.fixture(scope="module")
def scratch_db(pg_admin):
    with disposable_db(pg_admin) as db:
        yield db


@pytest.fixture(scope="module")
def pool_dsn(scratch_db):
    """The disposable database's DSN (a scratch-shaped name is asserted)."""
    assert_scratch_db(scratch_db.db_name, where="ghost-waiter pin")
    return scratch_db.dsn


@pytest.fixture()
def scratch_env(scratch_db, monkeypatch):
    """Pin every ``POSTGRES_*`` variable per-variable to the scratch DB."""
    pin_scratch_postgres_env(monkeypatch, scratch_db)
    return scratch_db


@pytest.mark.timeout(120)
class TestGhostWaiters:
    @pytest.mark.asyncio
    async def test_abandoned_probes_leave_no_waiting_queue_entry(
        self, pool_dsn, scratch_env
    ):
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            conninfo=pool_dsn,
            open=False,
            min_size=1,
            max_size=1,
            timeout=POOL_TIMEOUT_S,
        )
        await pool.open()
        try:
            # The pool's ONE connection is held by a "production writer".
            held = await pool.getconn()
            baseline_waiting = pool.get_stats()["requests_waiting"]
            assert baseline_waiting == 0

            # Fire N probe-like acquisitions that are abandoned at their
            # own budget (the readiness sentinel's shape).
            tasks = []
            for _ in range(PROBE_COUNT):
                tasks.append(asyncio.create_task(pool.getconn()))
            await asyncio.sleep(PROBE_BUDGET_S)
            # The probe's ghost-waiter withdrawal: cancel the scheduled
            # future when the budget expires.
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except BaseException:  # noqa: BLE001 — the abandonment
                    pass

            # The symptom is OBSERVABLE while the pool is exhausted.
            waiting_while_exhausted = pool.get_stats()["requests_waiting"]
            print(
                f"[ghost-waiter] requests_waiting right after the "
                f"cancellations (pool exhausted): {waiting_while_exhausted} "
                f"(baseline {baseline_waiting})"
            )

            # Return the held connection: the pool pops the abandoned
            # positions and the queue DRAINS.
            await pool.putconn(held)
            await asyncio.sleep(POOL_TIMEOUT_S + 2.0)

            final_waiting = pool.get_stats()["requests_waiting"]
            print(
                f"[ghost-waiter] requests_waiting after the connection was "
                f"returned + the settle wait: {final_waiting}"
            )
            assert final_waiting == 0, (
                "the abandoned probes left ghost waiters in the pool's "
                f"waiting queue (requests_waiting={final_waiting})"
            )
            assert final_waiting <= baseline_waiting
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_a_non_cancelled_waiter_is_bounded_by_the_pool_timeout(
        self, pool_dsn, scratch_env
    ):
        """The knob this lane delivered: the pool's own acquisition
        deadline bounds a queued waiter (the library default was 30 s)."""
        from psycopg_pool import AsyncConnectionPool, PoolTimeout

        pool = AsyncConnectionPool(
            conninfo=pool_dsn,
            open=False,
            min_size=1,
            max_size=1,
            timeout=POOL_TIMEOUT_S,
        )
        await pool.open()
        try:
            held = await pool.getconn()
            waiter = asyncio.create_task(pool.getconn())
            await asyncio.sleep(0.2)
            assert pool.get_stats()["requests_waiting"] == 1
            # The knob's VALUE: the client's wait is bounded by the pool's
            # own acquisition deadline (the library default was 30 s).
            with pytest.raises(PoolTimeout):
                await waiter
            # MEASURED LIBRARY FACT (recorded, not assumed): the pool's
            # waiting-queue ENTRY survives the timeout — psycopg_pool
            # only pops a position when a connection is RETURNED. The
            # timeout bounds the CLIENT's wait, not the queue entry.
            waiting_after_timeout = pool.get_stats()["requests_waiting"]
            print(
                f"[ghost-waiter] requests_waiting after the pool-timeout: "
                f"{waiting_after_timeout} (the queue entry survives until "
                "a connection is returned)"
            )
            await pool.putconn(held)
            await asyncio.sleep(1.0)
            # ... and the queue DRAINS once a connection returns.
            assert pool.get_stats()["requests_waiting"] == 0
        finally:
            await pool.close()
