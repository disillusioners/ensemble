"""PostgreSQL-backed tests for the /readyz probe SQL (Auto-Restart Phase 1).

The readiness composite's two engine-bound probes — ``SELECT 1`` and
the SQL-side queue-heartbeat max-age aggregate — are pure SQL against
the real schema. These tests verify both run correctly against the
actual PostgreSQL ``task`` table shape (column names, status literal,
naive-TIMESTAMP session semantics): see
``daemon/services/readiness.py`` for why the age is computed inside
SQL rather than in Python.

Marker: ``postgres`` (auto-applied by ``tests/postgres/conftest.py``);
run with ``pytest tests/postgres/ --override-ini="addopts=" -m postgres``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import asyncio
from sqlalchemy import text

import daemon.repositories.task.models  # noqa: F401 — register Task in metadata
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.services.readiness import (
    evaluate_queue_freshness,
    make_checkpoint_saver_probe,
    make_db_probe,
    make_queue_probe,
)

_TEST_INSTANCE = "readiness-probe-test-instance"


def _insert_task(
    conn,
    *,
    status: str,
    heartbeat: datetime,
    work_id: str,
) -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        text(
            """
            INSERT INTO task (task_type, instance_id, message_id, status,
                              retry_count, created_at, cancel_requested,
                              retry_scheduled, work_id, is_deferred,
                              is_background, worker_id, started_at,
                              last_heartbeat_at)
            VALUES (:task_type, :instance_id, :message_id, :status,
                    :retry_count, :created_at, :cancel_requested,
                    :retry_scheduled, :work_id, :is_deferred,
                    :is_background, :worker_id, :started_at,
                    :last_heartbeat_at)
            """
        ),
        {
            "task_type": TaskType.PROCESS_MESSAGE.value,
            "instance_id": _TEST_INSTANCE,
            "message_id": None,
            "status": status,
            "retry_count": 0,
            "created_at": now,
            "cancel_requested": False,
            "retry_scheduled": False,
            "work_id": work_id,
            "is_deferred": False,
            "is_background": False,
            "worker_id": None,
            "started_at": now,
            "last_heartbeat_at": heartbeat,
        },
    )


def _clear_tasks(conn) -> None:
    conn.execute(text("DELETE FROM task WHERE instance_id = :iid"), {"iid": _TEST_INSTANCE})


def test_db_probe_select_one(pg_engine):
    """SELECT 1 probe succeeds against the live PG engine."""
    probe = make_db_probe(pg_engine)
    assert probe() is True


def test_queue_probe_empty_running_set_is_none(pg_engine):
    """No RUNNING tasks → MAX() is NULL → age None (= fresh)."""
    with pg_engine.begin() as conn:
        _clear_tasks(conn)

    probe = make_queue_probe(pg_engine)
    assert probe().max_age_seconds is None

    fresh, age = evaluate_queue_freshness(None, threshold_seconds=120)
    assert fresh is True
    assert age is None


def test_queue_probe_returns_newest_running_age(pg_engine):
    """Age of the newest RUNNING heartbeat — non-RUNNING rows ignored.

    Also the timezone regression guard: the probe binds an aware-UTC
    timestamp (as the worker heartbeat thread does) and must compute
    an age near 30s, NOT skewed by the PG session timezone (e.g. 7h
    off on a +07 session — the Python-side subtraction bug this
    SQL-side aggregate replaced).
    """
    now = datetime.now(timezone.utc)
    fresh_beat = now - timedelta(seconds=30)
    stale_completed_beat = now - timedelta(seconds=9999)

    with pg_engine.begin() as conn:
        _clear_tasks(conn)
        # A RUNNING task with a 30s-old heartbeat — must set the age.
        _insert_task(
            conn,
            status=TaskStatus.RUNNING.value,
            heartbeat=fresh_beat,
            work_id="wq-running-fresh",
        )
        # A COMPLETED task with a very old heartbeat — must be ignored.
        _insert_task(
            conn,
            status=TaskStatus.COMPLETED.value,
            heartbeat=stale_completed_beat,
            work_id="wq-completed-old",
        )

    age = make_queue_probe(pg_engine)().max_age_seconds
    assert age is not None
    assert 25 <= age <= 35, f"30s-old RUNNING heartbeat, got age={age}"

    fresh, evaluated_age = evaluate_queue_freshness(age, threshold_seconds=120)
    assert fresh is True
    assert evaluated_age == age


def test_queue_probe_stale_running_heartbeat_degrades(pg_engine):
    """A RUNNING task whose heartbeat exceeds the threshold degrades."""
    now = datetime.now(timezone.utc)
    stale_beat = now - timedelta(seconds=180)  # > 120s threshold

    with pg_engine.begin() as conn:
        _clear_tasks(conn)
        _insert_task(
            conn,
            status=TaskStatus.RUNNING.value,
            heartbeat=stale_beat,
            work_id="wq-running-stale",
        )

    age = make_queue_probe(pg_engine)().max_age_seconds
    assert age is not None
    assert 175 <= age <= 185, f"180s-old RUNNING heartbeat, got age={age}"

    fresh, _ = evaluate_queue_freshness(age, threshold_seconds=120)
    assert fresh is False


# ── Stop-frozen heartbeat amnesty (r-20260929-170301-0cb2), PG SQL ────────


def test_pg_amnesty_all_frozen_pre_boot_beats_read_fresh(pg_engine):
    """Incident shape on real PG: every RUNNING beat predates the boot

    epoch → excluded from MAX() → age None → fresh (promote commits
    with a busy daemon). The no-epoch probe over the SAME rows still
    reads stale — the legacy semantics that failed the promote.
    """
    now = datetime.now(timezone.utc)
    frozen_beat = now - timedelta(seconds=180)
    boot = now - timedelta(seconds=125)  # booted AFTER the freeze

    with pg_engine.begin() as conn:
        _clear_tasks(conn)
        _insert_task(
            conn,
            status=TaskStatus.RUNNING.value,
            heartbeat=frozen_beat,
            work_id="wq-frozen-pre-boot",
        )

    legacy = make_queue_probe(pg_engine)()
    assert legacy.max_age_seconds is not None
    assert legacy.max_age_seconds >= 175
    fresh, _ = evaluate_queue_freshness(
        legacy.max_age_seconds, threshold_seconds=120
    )
    assert fresh is False  # the incident, on legacy semantics

    amnestied = make_queue_probe(pg_engine, boot_epoch=boot.replace(tzinfo=None))()
    assert amnestied.max_age_seconds is None
    fresh, age = evaluate_queue_freshness(None, threshold_seconds=120)
    assert fresh is True and age is None


def test_pg_signal_preservation_post_boot_stale_still_degrades(pg_engine):
    """INVARIANT on real PG: a post-boot beat going stale still counts."""
    now = datetime.now(timezone.utc)
    boot = now - timedelta(hours=1)  # continuously up — beat is post-boot
    stale_beat = now - timedelta(seconds=180)

    with pg_engine.begin() as conn:
        _clear_tasks(conn)
        _insert_task(
            conn,
            status=TaskStatus.RUNNING.value,
            heartbeat=stale_beat,
            work_id="wq-stale-post-boot",
        )

    result = make_queue_probe(pg_engine, boot_epoch=boot.replace(tzinfo=None))()
    assert result.max_age_seconds is not None
    assert result.max_age_seconds >= 175
    fresh, _ = evaluate_queue_freshness(
        result.max_age_seconds, threshold_seconds=120
    )
    assert fresh is False


def test_pg_inflight_turns_advisory_count(pg_engine):
    """Advisory count on real PG: fresh post-boot beats only."""
    now = datetime.now(timezone.utc)
    boot = now - timedelta(seconds=125)

    with pg_engine.begin() as conn:
        _clear_tasks(conn)
        _insert_task(
            conn,
            status=TaskStatus.RUNNING.value,
            heartbeat=now - timedelta(seconds=10),
            work_id="wq-inflight-fresh",
        )
        _insert_task(
            conn,
            status=TaskStatus.RUNNING.value,
            heartbeat=now - timedelta(seconds=180),
            work_id="wq-inflight-stale-post",
        )
        _insert_task(
            conn,
            status=TaskStatus.RUNNING.value,
            heartbeat=now - timedelta(seconds=200),  # frozen pre-boot
            work_id="wq-inflight-frozen-pre",
        )

    result = make_queue_probe(
        pg_engine, boot_epoch=boot.replace(tzinfo=None), freshness_threshold_seconds=120
    )()
    assert result.inflight_turns == 1
    assert result.max_age_seconds is not None
    assert 5 <= result.max_age_seconds <= 20  # the 10s beat sets the MAX


# ── incident 2026-10-10: checkpoint-saver probe on real PG ──────────────────
#
# These tests verify the new readiness component (``checkpoint_saver``)
# behaves correctly against the production-shaped pool-backed
# ``AsyncPostgresSaver``. They use the ``real_pg_checkpointer(..., pool=True)``
# variant introduced in incident-2026-10-10 fix; the ``postgres`` marker is
# applied at module scope (this entire file is /postgres-only).
#
# Marked properly-skipped when PostgreSQL is unreachable — never a silent
# mock. See ``tests/helpers/checkpoint_prune_pg.py::require_postgres``.


@pytest.fixture
async def pool_backed_checkpointer():
    """Pool-backed production-shaped saver harness (incident 2026-10-10)."""
    from tests.helpers.checkpoint_prune_pg import (
        create_disposable_db,
        drop_database,
        require_postgres,
    )

    require_postgres()
    dbname, dsn = await create_disposable_db()
    try:
        from tests.helpers.checkpoint_prune_pg import real_pg_checkpointer

        async with real_pg_checkpointer(dbname, dsn, pool=True) as (saver, pool, adapter):
            yield saver, pool, adapter
    finally:
        await drop_database(dbname)


@pytest.mark.asyncio
async def test_pg_checkpoint_saver_probe_passes_on_healthy_pool(
    pool_backed_checkpointer,
):
    """Pool.check() succeeds against a healthy production-shaped pool.

    This is the GREEN path the incident's blind spot missed — readiness
    must report green here (was: would have lied because the readiness
    DB probe used a fresh engine connection and ignored the saver's
    pool entirely).
    """
    saver, _pool, adapter = pool_backed_checkpointer

    probe = make_checkpoint_saver_probe(adapter)
    # Run in a thread (the probe is sync; runs against the captured loop).
    result = await asyncio.to_thread(probe)
    assert result is True


@pytest.mark.asyncio
async def test_pg_checkpoint_saver_probe_passes_after_pool_recycles_dead_conn(
    pool_backed_checkpointer,
):
    """Server-side terminate of pool conns → pool.check() still passes.

    The pool's ``check=`` callback detects the dead one and the pool
    transparently replaces it on the next acquire. We exercise this
    by killing one connection via ``pg_terminate_backend`` and
    re-running the probe.

    NOTE: this is a real-PG integration test — it acquires, terminates,
    re-acquires. Requires PG_TEST_HOST env (default localhost:5432,
    same as the rest of tests/postgres/).
    """
    from daemon.services.readiness import make_checkpoint_saver_probe

    _saver, _pool, adapter = pool_backed_checkpointer
    probe = make_checkpoint_saver_probe(adapter)

    # Probe is healthy before tampering.
    assert await asyncio.to_thread(probe) is True

    # Terminate one pool connection server-side. The pool's min_size
    # is 1, so one kill covers the active conn. We use the adapter's
    # SEPARATE asyncpg pool to issue the terminate — clean PG
    # session, distinct from the saver pool we want to kill.
    apg_pool = adapter._pool
    async with apg_pool.acquire() as c:
        await c.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid() "
            "LIMIT 1"
        )

    # Pool.check() must still pass — the pool detected the dead conn
    # and the next acquire replaces it. (We rely on the pool's
    # check= callback to detect and replace; this test verifies the
    # end-to-end contract.)
    result = await asyncio.to_thread(probe)
    assert result is True
