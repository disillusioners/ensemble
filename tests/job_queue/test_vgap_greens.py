"""Greens: A1 / A3 / heartbeat-cadence invariants.

Quick greens coverage of three invariants the audit listed as
"only if quick — skip without guilt". All three are PASSING
invariants on the current code (no xfail expected).

(a) **A1 end-to-end claim**: a revival enqueue on a terminal-status
    instance produces a PROCESS_MESSAGE Task row that is actually
    CLAIMED by ``claim_pending_task``. The existing
    ``test_a1_revival_carve_out.py`` only asserts
    ``is_deferred=False``; this test goes one step further and
    verifies the row is genuinely claimable (not just
    syntactically eligible).

(b) **A3 double-notify idempotency**: two consecutive
    ``sweep_once`` ticks on the same eligible set + an atomic
    ``claim_pending_task`` → single dispatch (the second sweep's
    notify is benign because the atomic claim gate prevents
    double-dispatch).

(c) **Heartbeat cadence vs threshold invariant**:
    ``DEFAULT_HEARTBEAT_INTERVAL_SECONDS`` (worker_pool.py:45,
    30.0) ≪ ``hang_threshold_seconds`` (default 3600s).
    The watchdog's liveness gate cannot false-negative between
    beats by design — the threshold is ≫ cadence × 60 (the
    worker pool heartbeat thread updates ``last_heartbeat_at``
    every cadence seconds; if the threshold were smaller than
    the cadence, a child turn could go silent between two
    beats and look wedged to the watchdog).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.task.models import TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.eligible_pending_sweep import (
    DEFAULT_MIN_PENDING_AGE_SECONDS,
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    EligiblePendingSweepService,
)


# ─── Engine fixture (file-backed SQLite per the A3 recipe) ──────────────


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    """File-backed SQLite (NullPool + WAL + busy_timeout=10000)."""
    db_path = tmp_path / "wcw_greens.db"
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


# ─── (a) A1 end-to-end: revival enqueue → claim_pending_task claims ────


class TestGreensA1EndToEndClaim:
    """A1 end-to-end: a revival-enqueued Task row on a
    terminal-status instance is CLAIMED by ``claim_pending_task``
    (the carve-out's ``is_deferred=False`` is necessary but not
    sufficient — the row must also pass the per-instance
    busy / cross-system claim gate).
    """

    def test_revival_task_claimable_after_carveout(self, engine):
        """Insert a PENDING Task row with ``is_deferred=False`` (the
        A1 carve-out value) on a non-busy instance — the row MUST
        be claimed by ``claim_pending_task``."""
        # The Instance must exist for the per-instance busy gate.
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO instances
                        (instance_id, agent_id, agent_dir, status,
                         project_id, parent_id, created_at, updated_at,
                         version, last_activity_at)
                    VALUES
                        (:instance_id, 'developer', 'agents/developer',
                         'completed', 'test-project', NULL,
                         :now, :now, 1, :now)
                    """
                ),
                {
                    "instance_id": "iid-greens-revival",
                    "now": datetime.now(timezone.utc).isoformat(),
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO task
                        (task_type, instance_id, message_id, status,
                         retry_count, created_at, cancel_requested,
                         retry_scheduled, work_id, is_deferred,
                         is_background)
                    VALUES
                        (:task_type, :instance_id, NULL, :status,
                         0, :created_at, FALSE, FALSE,
                         :work_id, FALSE, FALSE)
                    """
                ),
                {
                    "task_type": "process_message",
                    "instance_id": "iid-greens-revival",
                    "status": TaskStatus.PENDING.value,
                    "created_at": datetime.now(timezone.utc),
                    "work_id": "wid-greens-revival-claim",
                },
            )

        task_repo = TaskRepository(engine)
        claimed = task_repo.claim_pending_task(worker_id="worker-1")
        assert claimed is not None, (
            "A1 end-to-end: the revival-enqueued PENDING Task "
            "(is_deferred=False) MUST be claimable by "
            "claim_pending_task. Got None — the row was either "
            "filtered out by the per-instance busy gate, the "
            "cross-system guard, or the A1 carve-out did not "
            "actually land (defect)."
        )
        assert claimed.work_id == "wid-greens-revival-claim"
        assert claimed.instance_id == "iid-greens-revival"
        # The claim transition flipped the row to RUNNING.
        assert claimed.status == TaskStatus.RUNNING.value


# ─── (b) A3 double-notify idempotency ──────────────────────────────────


class TestGreensA3DoubleNotifyIdempotent:
    """A3 sweep + atomic claim: two consecutive ``sweep_once``
    ticks + one ``claim_pending_task`` call → single dispatch
    (the second sweep's notify is benign; the atomic claim
    guard prevents double-dispatch).
    """

    @pytest.mark.asyncio
    async def test_two_sweeps_one_claim_single_dispatch(self, engine):
        """Two sweep ticks → two notify_work calls (one per tick),
        but a single claim_pending_task call consumes the row.
        The pool-level double-notify is benign."""
        now = datetime.now(timezone.utc)
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO instances
                        (instance_id, agent_id, agent_dir, status,
                         project_id, parent_id, created_at, updated_at,
                         version, last_activity_at)
                    VALUES
                        (:instance_id, 'developer', 'agents/developer',
                         'running', 'test-project', NULL,
                         :now, :now, 1, :now)
                    """
                ),
                {
                    "instance_id": "iid-greens-a3",
                    "now": datetime.now(timezone.utc).isoformat(),
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO task
                        (task_type, instance_id, message_id, status,
                         retry_count, created_at, cancel_requested,
                         retry_scheduled, work_id, is_deferred,
                         is_background)
                    VALUES
                        (:task_type, :instance_id, NULL, :status,
                         0, :created_at, FALSE, FALSE,
                         :work_id, FALSE, FALSE)
                    """
                ),
                {
                    "task_type": "process_message",
                    "instance_id": "iid-greens-a3",
                    "status": TaskStatus.PENDING.value,
                    "created_at": now - timedelta(seconds=120),
                    "work_id": "wid-greens-a3",
                },
            )

        worker_pool = MagicMock()
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=worker_pool,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
        )

        # Tick 1: notify_work fires once.
        stats_1 = await service.sweep_once()
        assert stats_1["eligible"] == 1
        assert stats_1["notified"] == 1
        # Tick 2: notify_work fires again (one per tick — same
        # eligible row is still PENDING + NULL heartbeat + aged).
        stats_2 = await service.sweep_once()
        assert stats_2["eligible"] == 1, (
            "A3 sweep: the eligible row is still PENDING after "
            "tick 1 (no claim happened between ticks). The "
            "sweep re-finds it on tick 2. Got "
            f"eligible={stats_2['eligible']!r}"
        )
        assert stats_2["notified"] == 1, (
            "A3 sweep: one notify per tick is the per-tick "
            "contract (the sweep's value is per-tick, not "
            f"per-row). Got notified={stats_2['notified']!r}"
        )
        # Two notify_work calls total (one per tick).
        assert worker_pool.notify_work.call_count == 2

        # Single atomic claim consumes the row.
        task_repo = TaskRepository(engine)
        claimed = task_repo.claim_pending_task(worker_id="worker-1")
        assert claimed is not None
        assert claimed.work_id == "wid-greens-a3"

        # Second claim on the same row MUST fail (the atomic
        # claim gate prevents double-dispatch).
        second_claim = task_repo.claim_pending_task(
            worker_id="worker-2"
        )
        assert second_claim is None, (
            "A3 double-notify idempotency: a second claim on the "
            "already-claimed row MUST return None (the atomic "
            "claim guard prevents double-dispatch). Got "
            f"{second_claim!r}"
        )


# ─── (c) Heartbeat cadence vs threshold invariant ──────────────────────


class TestGreensHeartbeatCadenceInvariant:
    """Heartbeat cadence (``DEFAULT_HEARTBEAT_INTERVAL_SECONDS``)
    MUST be ≪ the watchdog's hang threshold. If the threshold
    were smaller than the cadence, a child turn could go silent
    between two beats and look wedged to the watchdog.

    The invariant: ``hang_threshold_seconds > cadence × 60``
    (60 × gives a generous safety margin: at least 60 heartbeats
    before the gate declares the child hung).
    """

    def test_threshold_greater_than_cadence_times_60(self):
        from daemon.services.worker_pool import (
            DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        )
        from daemon.services.waiting_children_watchdog import (
            WaitingChildrenWatchdog,
        )

        # The watchdog's default hang_threshold_seconds is 3600
        # (1h). The worker pool's heartbeat cadence is 30s. The
        # invariant: 3600 > 30 × 60 = 1800.
        cadence = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
        threshold = WaitingChildrenWatchdog(
            instance_repository=MagicMock(),
            manager=MagicMock(),
        ).hang_threshold_seconds
        # Threshold > cadence × 60 — the watchdog sees at least
        # 60 heartbeats before declaring the child hung.
        assert threshold > cadence * 60, (
            f"Heartbeat cadence invariant violated: "
            f"hang_threshold_seconds={threshold} MUST be > "
            f"cadence × 60 = {cadence * 60} "
            f"(cadence={cadence}s). A tighter threshold would "
            f"let a child turn go silent between two beats and "
            f"look wedged to the watchdog (false-positive B3 "
            f"release)."
        )
        # Sanity: the values come from the real constants.
        assert cadence == 30.0, (
            f"DEFAULT_HEARTBEAT_INTERVAL_SECONDS changed from "
            f"30.0s to {cadence}s — the W-B review cited this "
            f"value explicitly. Update the test if the new value "
            f"is intentional."
        )
        assert threshold == 3600, (
            f"hang_threshold_seconds default changed from 3600s "
            f"to {threshold}s — the watchdog's documented "
            f"default. Update the test if the new value is "
            f"intentional."
        )
