"""Tests for the A3 eligible-PENDING sweep (Batch A of WC wake/resilience).

A3 (Batch A, 2026-09-11): a periodic asyncio sweep
(``daemon/services/eligible_pending_sweep.py``) is the load-bearing
systemic backstop for every "miss-reason" that can leave a task
eligible but un-notified:

* Born-deferred-then-flipped (A1 / A2 catch the normal flow; A3
  heals the case where the flip committed but the notify was lost)
* Pool-busy at creation
* Notify lost to daemon restart
* Watchdog notices (A5 covers the wedge; A3 catches any other
  notice path that misses the notify)

ALWAYS ON (no env flag). The interval + age threshold are tuning
knobs (``ServicesConfig.eligible_pending_sweep_interval_seconds``
default 90s; ``..._min_pending_age_seconds`` default 60s), but the
sweep itself is structural infrastructure, NOT a user-togglable.

Test surface (this file):

* **sweep_finds_eligible_pending_and_notifies_pool** — happy path:
  one eligible PENDING task → ``notify_work()`` fires once.
* **sweep_skips_deferred_pending_tasks** — is_deferred=True rows
  are NOT eligible (the sweep is for non-defer candidates only;
  Pattern (g) autopromote handles the defer lane).
* **sweep_skips_running_tasks** — RUNNING tasks are NOT eligible
  (they have non-NULL ``last_heartbeat_at``; the helper filters
  them out).
* **sweep_skips_tasks_younger_than_threshold** — fresh enqueues
  below the age threshold are left alone to avoid racing with the
  natural claim path.
* **sweep_idempotent_on_no_eligible_rows** — empty DB → zero
  notifies, zero errors; counters advance cleanly.
* **sweep_calls_notify_once_per_tick_when_pool_wired** — multiple
  eligible rows on one tick → ONE notify (the pool's condition
  variable already saw the wake; double-notify is wasted).
* **sweep_silent_when_worker_pool_none** — legacy test fixture
  (no pool) → no notify, no raise; DEBUG log only.
* **sweep_continues_on_notify_work_failure** — a notify_work that
  raises is caught; the sweep advances counters without aborting.
* **sweep_lifecycle_start_idempotent** — start() called twice →
  one task alive.
* **sweep_lifecycle_stop_clean** — stop() cancels the task and the
  loop exits cleanly.
* **census_stays_23_1_0** — A3 fix is method-call-only on existing
  surfaces; no new writer / creator / mint site.

Harness: file-backed SQLite per the recipe in
``tests/job_queue/test_defer_self_witness_watchdog.py``. The
``TaskRepository`` is real (against the test engine); only the
``worker_pool`` is a ``MagicMock`` shim that records
``notify_work()`` calls.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401  (registers Instance)
import daemon.repositories.job_queue.models  # noqa: F401  (registers JobItem)
import daemon.repositories.task.models  # noqa: F401  (registers Task)
from daemon.repositories.task.models import TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.eligible_pending_sweep import (
    DEFAULT_MIN_PENDING_AGE_SECONDS,
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    EligiblePendingSweepService,
)


# ─── Engine + repo helpers ──────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    """File-backed SQLite (NullPool + WAL + busy_timeout) per recipe."""
    db_path = tmp_path / "a3_eligible_pending_sweep.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_pending_task(
    engine: Engine,
    *,
    instance_id: str,
    created_at: datetime | None = None,
    is_deferred: bool = False,
) -> int:
    """Insert a PENDING Task. ``last_heartbeat_at IS NULL`` so the
    sweep's helper ``list_pending_tasks_older_than`` sees it.

    Returns the integer PK. ``created_at`` is backdated past the
    age threshold (120s ago by default) so the row is eligible.
    """
    now = (created_at or datetime.now(timezone.utc))
    # Append the instance_id to the work_id so concurrent
    # inserts within the same millisecond don't collide on the
    # UNIQUE constraint — file-backed SQLite serializes via the
    # WAL + busy_timeout, but the work_id is unique per row.
    wid = f"wid-a3-{now.timestamp()}-{is_deferred}-{instance_id}"
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred, is_background)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     :retry_count, :created_at, :cancel_requested,
                     :retry_scheduled, :work_id, :is_deferred, :is_background)
                """
            ),
            {
                "task_type": "process_message",
                "instance_id": instance_id,
                "message_id": None,
                "status": TaskStatus.PENDING.value,
                "retry_count": 0,
                "created_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": wid,
                "is_deferred": is_deferred,
                "is_background": False,
            },
        )
        return result.lastrowid


def _seed_running_task(
    engine: Engine,
    *,
    instance_id: str,
    created_at: datetime | None = None,
) -> int:
    """Insert a RUNNING Task with ``last_heartbeat_at`` set so it is
    NOT a candidate for ``list_pending_tasks_older_than`` (the
    helper filters on NULL heartbeat)."""
    now = (created_at or datetime.now(timezone.utc))
    wid = f"wid-a3-running-{now.timestamp()}"
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, last_heartbeat_at,
                     cancel_requested, retry_scheduled, work_id,
                     is_deferred, is_background)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     :retry_count, :created_at, :last_heartbeat_at,
                     :cancel_requested, :retry_scheduled, :work_id,
                     :is_deferred, :is_background)
                """
            ),
            {
                "task_type": "process_message",
                "instance_id": instance_id,
                "message_id": None,
                "status": "running",
                "retry_count": 0,
                "created_at": now,
                "last_heartbeat_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": wid,
                "is_deferred": False,
                "is_background": False,
            },
        )
        return result.lastrowid


def _seed_completed_task(
    engine: Engine,
    *,
    instance_id: str,
    created_at: datetime | None = None,
) -> int:
    """Insert a COMPLETED Task — NOT eligible (status filter)."""
    now = (created_at or datetime.now(timezone.utc))
    wid = f"wid-a3-completed-{now.timestamp()}"
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred, is_background)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     :retry_count, :created_at, :cancel_requested,
                     :retry_scheduled, :work_id, :is_deferred, :is_background)
                """
            ),
            {
                "task_type": "process_message",
                "instance_id": instance_id,
                "message_id": None,
                "status": TaskStatus.COMPLETED.value,
                "retry_count": 0,
                "created_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": wid,
                "is_deferred": False,
                "is_background": False,
            },
        )
        return result.lastrowid


# ─── Happy path ─────────────────────────────────────────────────────────


class TestA3EligiblePendingSweep:
    """A3: the sweep finds eligible PENDING tasks and notifies the
    worker pool. The four miss-reasons (born-deferred-then-flipped,
    pool-busy at creation, notify lost to crash, watchdog notices)
    collapse onto the same shape — an aged, is_deferred=False,
    NULL-heartbeat PENDING task. The sweep catches all of them.
    """

    @pytest.mark.asyncio
    async def test_sweep_finds_eligible_pending_and_notifies_pool(
        self, engine
    ):
        """Happy path: one aged PENDING task → one notify_work call."""
        now = datetime.now(timezone.utc)
        _seed_pending_task(
            engine,
            instance_id="iid-a3-1",
            created_at=now - timedelta(seconds=120),
            is_deferred=False,
        )

        worker_pool = MagicMock()
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=worker_pool,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
        )
        stats = await service.sweep_once()

        assert stats["eligible"] == 1, (
            f"Sweep must find the aged PENDING task; got eligible="
            f"{stats['eligible']!r}, stats={stats!r}"
        )
        assert stats["notified"] == 1, (
            f"Sweep must dispatch notify_work() once; got notified="
            f"{stats['notified']!r}"
        )
        assert worker_pool.notify_work.call_count == 1, (
            "worker_pool.notify_work() must be called exactly once "
            "for the one eligible row"
        )

    @pytest.mark.asyncio
    async def test_sweep_skips_deferred_pending_tasks(self, engine):
        """is_deferred=True rows are NOT eligible (the sweep is for
        non-defer candidates only; Pattern (g) autopromote handles
        the defer lane)."""
        now = datetime.now(timezone.utc)
        _seed_pending_task(
            engine,
            instance_id="iid-a3-defer",
            created_at=now - timedelta(seconds=120),
            is_deferred=True,
        )

        worker_pool = MagicMock()
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=worker_pool,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
        )
        stats = await service.sweep_once()

        assert stats["eligible"] == 0, (
            f"Deferred PENDING rows are NOT eligible for the A3 "
            f"sweep (Pattern (g) owns the defer lane); got "
            f"eligible={stats['eligible']!r}"
        )
        assert worker_pool.notify_work.call_count == 0

    @pytest.mark.asyncio
    async def test_sweep_skips_running_tasks(self, engine):
        """RUNNING tasks (last_heartbeat_at NOT NULL) are filtered
        by the ``list_pending_tasks_older_than`` helper. The sweep
        never sees them."""
        now = datetime.now(timezone.utc)
        _seed_running_task(
            engine,
            instance_id="iid-a3-running",
            created_at=now - timedelta(seconds=120),
        )

        worker_pool = MagicMock()
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=worker_pool,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
        )
        stats = await service.sweep_once()

        assert stats["eligible"] == 0, (
            f"RUNNING tasks are NOT candidates — the helper's "
            f"NULL-heartbeat filter excludes them; got eligible="
            f"{stats['eligible']!r}"
        )
        assert worker_pool.notify_work.call_count == 0

    @pytest.mark.asyncio
    async def test_sweep_skips_tasks_younger_than_threshold(
        self, engine
    ):
        """Fresh enqueues below the age threshold are left alone
        to avoid racing with the natural claim path."""
        now = datetime.now(timezone.utc)
        _seed_pending_task(
            engine,
            instance_id="iid-a3-fresh",
            created_at=now - timedelta(seconds=10),  # 10s old
            is_deferred=False,
        )

        worker_pool = MagicMock()
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=worker_pool,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=60,  # 60s threshold
        )
        stats = await service.sweep_once()

        assert stats["eligible"] == 0, (
            f"Fresh enqueues (10s old < 60s threshold) must be "
            f"left alone; got eligible={stats['eligible']!r}"
        )
        assert worker_pool.notify_work.call_count == 0

    @pytest.mark.asyncio
    async def test_sweep_idempotent_on_no_eligible_rows(self, engine):
        """Empty DB → zero notifies, zero errors; counters advance
        cleanly (one tick per call)."""
        worker_pool = MagicMock()
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=worker_pool,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
        )

        # Three sequential ticks — each one increments the tick
        # counter and keeps eligible / notified at zero.
        stats_1 = await service.sweep_once()
        stats_2 = await service.sweep_once()
        stats_3 = await service.sweep_once()

        for stats, label in (
            (stats_1, "first"),
            (stats_2, "second"),
            (stats_3, "third"),
        ):
            assert stats["eligible"] == 0, (
                f"{label} tick: empty DB → eligible=0; "
                f"got eligible={stats['eligible']!r}"
            )
            assert stats["notified"] == 0, (
                f"{label} tick: empty DB → notified=0; "
                f"got notified={stats['notified']!r}"
            )

        counters = service.counters()
        assert counters["ticks"] == 3
        assert counters["eligible_total"] == 0
        assert counters["notified_total"] == 0
        assert counters["errors_total"] == 0
        assert worker_pool.notify_work.call_count == 0

    @pytest.mark.asyncio
    async def test_sweep_calls_notify_once_per_tick_when_pool_wired(
        self, engine
    ):
        """Multiple eligible rows on one tick → ONE notify (the
        pool's condition variable already saw the wake; double-
        notify is wasted). The sweep's value is per-tick, not
        per-row."""
        now = datetime.now(timezone.utc)
        # Five aged eligible rows on three different instances.
        for i in range(5):
            _seed_pending_task(
                engine,
                instance_id=f"iid-a3-multi-{i}",
                created_at=now - timedelta(seconds=120),
                is_deferred=False,
            )

        worker_pool = MagicMock()
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=worker_pool,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
        )
        stats = await service.sweep_once()

        assert stats["eligible"] == 5, (
            f"Sweep must see all 5 eligible rows; got eligible="
            f"{stats['eligible']!r}"
        )
        assert stats["notified"] == 1, (
            f"Sweep must dispatch ONE notify_work call per tick "
            f"(not per-row); got notified={stats['notified']!r}"
        )
        assert worker_pool.notify_work.call_count == 1

    @pytest.mark.asyncio
    async def test_sweep_silent_when_worker_pool_none(self, engine):
        """Legacy test fixture (no pool) → no notify, no raise;
        DEBUG log only. Counters still advance cleanly."""
        now = datetime.now(timezone.utc)
        _seed_pending_task(
            engine,
            instance_id="iid-a3-no-pool",
            created_at=now - timedelta(seconds=120),
            is_deferred=False,
        )

        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=None,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
        )
        stats = await service.sweep_once()

        assert stats["eligible"] == 1
        assert stats["notified"] == 0, (
            f"No pool wired → no notify dispatched; got notified="
            f"{stats['notified']!r}"
        )
        assert stats["cumulative_errors"] == 0, (
            "Sweep must not raise when worker_pool is None; the "
            "DEBUG-only path is silent"
        )

    @pytest.mark.asyncio
    async def test_sweep_continues_on_notify_work_failure(
        self, engine, caplog
    ):
        """A notify_work that raises is caught; the sweep advances
        counters without aborting."""
        import logging

        now = datetime.now(timezone.utc)
        _seed_pending_task(
            engine,
            instance_id="iid-a3-blip",
            created_at=now - timedelta(seconds=120),
            is_deferred=False,
        )

        worker_pool = MagicMock()
        worker_pool.notify_work.side_effect = RuntimeError("pool blip")
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=worker_pool,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
        )

        with caplog.at_level(logging.WARNING):
            stats = await service.sweep_once()

        # Eligible was found, notify_work was attempted but raised;
        # counters reflect the attempt + the failure.
        assert stats["eligible"] == 1
        assert stats["notified"] == 0, (
            f"notify_work raising MUST NOT count as notified; "
            f"got notified={stats['notified']!r}"
        )
        assert stats["cumulative_errors"] == 1, (
            f"notify_work raising MUST bump cumulative_errors; "
            f"got cumulative_errors={stats['cumulative_errors']!r}"
        )

        # A subsequent tick must still work.
        worker_pool.notify_work.side_effect = None
        stats2 = await service.sweep_once()
        assert stats2["cumulative_errors"] == 1, (
            f"Second tick has a healthy pool → no new errors; "
            f"got cumulative_errors={stats2['cumulative_errors']!r}"
        )
        assert stats2["notified"] == 1, (
            f"Second tick heals the row; got notified="
            f"{stats2['notified']!r}"
        )


# ─── Lifecycle ───────────────────────────────────────────────────────────


class TestA3SweepLifecycle:
    """A3 lifecycle: ``start()`` / ``stop()`` mirror the
    ``WaitingChildrenWatchdog`` pattern (asyncio.create_task +
    cancel/await on shutdown)."""

    @pytest.mark.asyncio
    async def test_sweep_lifecycle_start_idempotent(self, engine):
        """start() called twice → one task alive (idempotent)."""
        worker_pool = MagicMock()
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=worker_pool,
            interval_seconds=1,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
        )
        service.start()
        first_task = service._task
        assert first_task is not None
        assert not first_task.done()

        # Second start — silent no-op.
        service.start()
        assert service._task is first_task, (
            "start() must be idempotent — second call leaves the "
            "task ref unchanged"
        )

        await service.stop()
        assert service._task is None, (
            "stop() must clear the task ref"
        )

    @pytest.mark.asyncio
    async def test_sweep_lifecycle_stop_clean(self, engine):
        """stop() cancels the task and the loop exits cleanly."""
        worker_pool = MagicMock()
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=worker_pool,
            interval_seconds=1,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
        )
        service.start()
        # Yield once so the loop body has a chance to run.
        await asyncio.sleep(0)
        # stop() must cancel + await cleanly.
        await service.stop()

    @pytest.mark.asyncio
    async def test_sweep_stop_safe_when_not_started(self, engine):
        """stop() called on a never-started service is silent
        no-op (does not raise)."""
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=MagicMock(),
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
        )
        await service.stop()  # no raise


# ─── Census static guard ────────────────────────────────────────────────


class TestA3ConstitutionStatic:
    """A3 fix is read-only at the DB level + a pool method call;
    no new ``admission_state`` writes, no new JobItem creators,
    no new ``work_id`` mints land. Census stays at 23/1/0."""

    def test_census_stays_23_1_0(self):
        """The A3 fix does not add new writers/creators/mints.
        Census stays at 23/1/0."""
        from daemon.job_state import constitution

        assert (
            len(constitution.KNOWN_ADMISSION_STATE_WRITERS) == 23
        ), (
            f"A3 introduced new admission_state writers — census "
            f"drifted from 23 to "
            f"{len(constitution.KNOWN_ADMISSION_STATE_WRITERS)}"
        )
        assert len(constitution.KNOWN_JOBITEM_CREATORS) == 1, (
            f"A3 introduced new JobItem creators — census drifted "
            f"from 1 to "
            f"{len(constitution.KNOWN_JOBITEM_CREATORS)}"
        )
        assert len(constitution.KNOWN_MINT_SITES) == 0, (
            f"A3 introduced new work_id mints — census drifted "
            f"from 0 to {len(constitution.KNOWN_MINT_SITES)}"
        )

    def test_a3_module_uses_existing_helpers(self):
        """The sweep MUST use ``TaskRepository.list_pending_tasks_older_than``
        (no new query method introduced)."""
        from pathlib import Path

        prod_path = (
            Path(__file__).parent.parent.parent
            / "daemon"
            / "services"
            / "eligible_pending_sweep.py"
        )
        contents = prod_path.read_text()
        assert "list_pending_tasks_older_than" in contents, (
            "A3 sweep MUST use the canonical "
            "list_pending_tasks_older_than helper — no new query "
            "method is allowed (the helper is the single source of "
            "truth for PENDING + NULL heartbeat + aged-past-grace)"
        )
        assert "self._worker_pool.notify_work()" in contents, (
            "A3 sweep MUST call the canonical notify_work seam — "
            "same primitive as task creation and A2 autopromote"
        )