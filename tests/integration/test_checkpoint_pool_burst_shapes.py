"""The CONCURRENT burst shapes — healthy-busy vs leak (A5(i) / A5(iv)(b)).

soak-gate-mcp-warmup · A1 · requirements.md §5(i) and §5(iv)(b) + §4.4
(P4 — the leak signature).

The incident's shape was THREE streams overlapping on one loop with the
checkpoint pool saturating. This pin reproduces the POOL side of that
shape CONCURRENTLY (the harness acquires and releases connections from
CONCURRENT tasks — never serially) and proves the discrimination the
grace rests on:

* **healthy-busy** — the checked-out count RISES AND FALLS (an
  oscillation with recovery ticks). This is row 1 of the §4.0 table:
  the grace engages, the leak signal stays FALSE, and the daemon-side
  grace verdict is ``grace_active``.
* **leak** — a MONOTONIC rise with NO recovery tick (every tick holds
  one MORE connection than the last). This is the row-3 signature: the
  grace REFUSES to engage, the leak signal is asserted, and the
  component reasons stay reportable.

The pool is the REAL psycopg pool (the P2 ORACLE) on a DISPOSABLE
PostgreSQL database — the leak predicate is measured from the pool
objects, never from ``pg_stat_activity``.

SAFETY FENCES
-------------
* a DISPOSABLE PostgreSQL database (throwaway LOGIN role + database),
  DROPPED at teardown;
* the ambient ``POSTGRES_*`` values are never trusted — the five
  variables are pinned PER VARIABLE to the disposable database;
* ``$HOME``-derived live paths are never resolved.

RUN (a deselected/skipped pin is a FAILED gate)::

    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \\
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \\
        HOME="$(mktemp -d)" .venv/bin/pytest \\
        tests/integration/test_checkpoint_pool_burst_shapes.py \\
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

from daemon.services.readiness import (  # noqa: E402
    FAILURE_CLASS_OK,
    FAILURE_CLASS_PROBE_TIMEOUT,
    READINESS_COMPONENT_NAMES,
    GraceTracker,
)

#: The pool shape mirrors the shipped P2 saver pool (A4's sizing knob).
POOL_MAX_SIZE = 10
#: How many ticks the burst runs.
TICKS = 12


@pytest.fixture(scope="module")
def pg_admin():
    admin = probe_admin_engine()
    if admin is None:
        pytest.skip(
            "PostgreSQL is not available at "
            f"{os.environ.get('PG_TEST_HOST', 'localhost')}:"
            f"{os.environ.get('PG_TEST_PORT', '5432')} — the burst-shape "
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
    assert_scratch_db(scratch_db.db_name, where="burst-shape pin")
    return scratch_db.dsn


@pytest.fixture()
def scratch_env(scratch_db, monkeypatch):
    pin_scratch_postgres_env(monkeypatch, scratch_db)
    return scratch_db


def _degraded_classes():
    classes = {name: FAILURE_CLASS_OK for name in READINESS_COMPONENT_NAMES}
    classes["database"] = FAILURE_CLASS_PROBE_TIMEOUT
    return classes


@pytest.mark.timeout(180)
class TestConcurrentBurstShapes:
    @pytest.mark.asyncio
    async def test_healthy_busy_oscillation_keeps_the_grace(self, pool_dsn, scratch_env):
        """healthy-busy = checked-out RISES AND FALLS (recovery ticks
        present) ⇒ the leak signal stays FALSE and the grace engages."""
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            conninfo=pool_dsn,
            open=False,
            min_size=1,
            max_size=POOL_MAX_SIZE,
            timeout=5.0,
        )
        await pool.open()
        tracker = GraceTracker()
        classes = _degraded_classes()
        held: list = []
        observed_checkedout: list[int] = []
        try:
            # The OSCILLATION: the target concurrency rises and falls
            # (a burst that DRAINS under load and refills between
            # turns — the healthy shape).
            wave = [1, 3, 5, 7, 5, 3, 1, 0, 1, 3, 5, 7]
            for tick, target in enumerate(wave):
                # Release the connections the previous tick kept beyond
                # the current target (the DRAIN).
                while len(held) > target:
                    await pool.putconn(held.pop())
                # Acquire up to the target CONCURRENTLY (never serially).
                want = target - len(held)
                if want > 0:
                    acquired = await asyncio.gather(
                        *[pool.getconn() for _ in range(want)]
                    )
                    held.extend(acquired)
                await asyncio.sleep(0.05)
                stats = pool.get_stats()
                observed_checkedout.append(
                    int(stats["pool_size"]) - int(stats["pool_available"])
                )
                obs = tracker.observe(
                    component_classes=classes,
                    degraded_components=["database"],
                    pool_state_psycopg={
                        "pool_size": int(stats["pool_size"]),
                        "pool_max": int(stats["pool_max"]),
                        "pool_available": int(stats["pool_available"]),
                        "requests_waiting": int(stats["requests_waiting"]),
                    },
                    now=_tick_time(tick),
                )
                assert obs.leak_signal["detected"] is False, (
                    "the healthy-busy shape was classified as a LEAK at "
                    f"tick {tick} (checkedout={observed_checkedout[-1]}, "
                    f"available={stats['pool_available']})"
                )
                assert obs.grace_active is True

            # The shape really did rise AND fall (recovery ticks present).
            assert max(observed_checkedout) > min(observed_checkedout), (
                "the burst never rose — the healthy-busy shape was not "
                f"exercised (observed={observed_checkedout})"
            )
            # A RECOVERY TICK was exercised (the count fell at least
            # once — the healthy shape is an OSCILLATION, not a ramp).
            assert any(
                observed_checkedout[i] < observed_checkedout[i - 1]
                for i in range(1, len(observed_checkedout))
            ), (
                "the burst never FELL — no recovery tick was exercised "
                f"(observed={observed_checkedout})"
            )
            print(
                f"[burst] healthy-busy checkedout series: {observed_checkedout}"
            )
        finally:
            for conn in held:
                await pool.putconn(conn)
            await pool.close()

    @pytest.mark.asyncio
    async def test_monotonic_rise_is_classified_as_a_leak(self, pool_dsn, scratch_env):
        """leak = a MONOTONIC rise with NO recovery tick ⇒ the leak
        signal arms and the grace REFUSES to engage."""
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            conninfo=pool_dsn,
            open=False,
            min_size=1,
            max_size=POOL_MAX_SIZE,
            timeout=5.0,
        )
        await pool.open()
        tracker = GraceTracker()
        classes = _degraded_classes()
        held: list = []
        observed_checkedout: list[int] = []
        leaked = False
        try:
            for tick in range(TICKS):
                # Every tick holds ONE MORE connection — the monotonic
                # rise with NO recovery tick.
                target = min(tick + 1, POOL_MAX_SIZE)
                want = target - len(held)
                if want > 0:
                    acquired = await asyncio.gather(
                        *[pool.getconn() for _ in range(want)]
                    )
                    held.extend(acquired)
                await asyncio.sleep(0.05)
                stats = pool.get_stats()
                observed_checkedout.append(
                    int(stats["pool_size"]) - int(stats["pool_available"])
                )
                obs = tracker.observe(
                    component_classes=classes,
                    degraded_components=["database"],
                    pool_state_psycopg={
                        "pool_size": int(stats["pool_size"]),
                        "pool_max": int(stats["pool_max"]),
                        "pool_available": int(stats["pool_available"]),
                        "requests_waiting": int(stats["requests_waiting"]),
                    },
                    now=_tick_time(tick),
                )
                if int(stats["pool_size"]) - int(stats["pool_available"]) >= POOL_MAX_SIZE:
                    # The pool is EXHAUSTED (every connection checked
                    # out) — the leak class arms and the grace refuses.
                    assert obs.leak_signal["detected"] is True, (
                        "the exhausted pool was NOT classified as a leak "
                        f"at tick {tick} (checkedout={observed_checkedout})"
                    )
                    assert obs.grace_active is False, (
                        "the grace engaged on a LEAK signature"
                    )
                    leaked = True
            assert leaked, (
                "the monotonic rise never exhausted the pool — the leak "
                f"shape was not exercised (checkedout={observed_checkedout})"
            )
            print(
                f"[burst] leak checkedout series: {observed_checkedout}"
            )
        finally:
            for conn in held:
                await pool.putconn(conn)
            await pool.close()


def _tick_time(tick: int):
    """A monotonic per-tick timestamp (the grace clock is wall-clock)."""
    from datetime import datetime, timedelta, timezone

    return datetime(2026, 10, 27, tzinfo=timezone.utc) + timedelta(
        seconds=tick
    )
