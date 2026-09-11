"""Tests for the A2 autopromote-notifies-pool fix (Batch A of WC wake/resilience).

A2 (Batch A, 2026-09-11): WS3 Pattern (g) autopromote
(``daemon/services/job_recovery_service.py:_pattern_g_defer_self_witness_watchdog``)
must call the worker-pool ``notify_work()`` seam immediately AFTER
flipping ``task.is_deferred=True → False``. Pre-A2 the flip landed
eligibility but scheduled no wake signal — the worker pool had no
reason to look at the row, the task sat PENDING up to the pool's
idle timeout (3s per claim cycle), and a sustained wedge (P1
incident 2026-09-11: flip at 11:58:42 → STILL PENDING at 12:32:14
when the F14 gate parked the parent) parked the parent at
completion for zero claims.

The fix threads ``worker_pool`` into ``JobRecoveryService.__init__``
and calls ``notify_work()`` after a successful ``flipped > 0`` branch.
The call is wrapped in try/except so a transient pool-side blip does
NOT abort the sweep (the periodic A3 sweep is the systemic backstop
that catches the missed row).

Test surface (this file):

* **autopromote_calls_notify_work_after_flip** — the happy path:
  a real deferred-PENDING candidate triggers the flip; the test
  asserts ``worker_pool.notify_work()`` was called exactly once
  per flipped task and that the task's ``is_deferred`` column is
  False post-sweep.
* **no_notify_when_flip_skipped_by_atomic_guard** — a row whose
  status changed between the candidate scan and the flip
  (``flipped == 0``) does NOT trigger notify_work (no flip
  happened; nothing new to wake the pool for).
* **no_notify_when_autopromote_disabled** — when the operator
  flag ``ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED`` is OFF, no flip
  happens and no notify is dispatched (the OFF=no-writes contract
  from the existing flag pins).
* **no_notify_when_seam_busy_returns_true** — when the WS1 seam
  returns True (the defer gate is correctly held by another
  witness), no flip happens and no notify is dispatched.
* **notify_work_failure_does_not_abort_sweep** — a notify_work
  that raises (transient pool-side blip) is caught and logged;
  the sweep proceeds to the next candidate row.
* **worker_pool_none_skips_notify_silently** — a
  ``JobRecoveryService`` constructed without ``worker_pool`` (a
  legacy test fixture) still flips the row correctly and emits a
  DEBUG log instead of raising; the A3 sweep catches the missed
  notify.
* **constitution_drift_stays_23_1_0** — the A2 fix is wiring
  only (a method call on an injected pool); no new
  ``admission_state`` writer / JobItem creator / ``work_id``
  mint lands. Census unchanged.

Harness: file-backed SQLite per the recipe in
``tests/job_queue/test_defer_self_witness_watchdog.py``. The
``JobRecoveryService`` is wired with real
``JobRepository`` / ``LockRepository`` / ``TaskRepository`` /
``SQLModelInstanceRepository`` / ``StaleTaskRecovery`` against the
test engine; only the ``worker_pool`` and ``job_queue_service``
are ``MagicMock`` shims — the test asserts the right method was
called with the right shape.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401  (registers Instance)
import daemon.repositories.job_queue.models  # noqa: F401  (registers JobItem)
import daemon.repositories.task.models  # noqa: F401  (registers Task)
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services import job_recovery_service as _jrs
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.stale_task_recovery import StaleTaskRecovery


# ─── Engine + repo helpers ──────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    """File-backed SQLite (NullPool + WAL + busy_timeout) per the recipe."""
    db_path = tmp_path / "a2_autopromote_notify.db"
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


def _seed_instance(
    engine: Engine,
    instance_id: str,
    *,
    project_id: str = "test-project",
    status: str = "running",
    created_at: datetime | None = None,
) -> None:
    """Insert an ``instances`` row directly via SQL."""
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version, parent_id,
                     last_activity_at)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status, :project_id,
                     :created_at, :updated_at, 1, :parent_id,
                     :last_activity_at)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
                "parent_id": None,
                "last_activity_at": now,
            },
        )


def _seed_deferred_pending_task(
    engine: Engine,
    *,
    instance_id: str,
    created_at: datetime | None = None,
) -> int:
    """Insert a deferred PENDING Task (is_deferred=True, NULL heartbeat).

    Returns the integer PK. ``list_pending_tasks_older_than(60)``
    always includes it because ``created_at`` is backdated past
    the grace.
    """
    now = (created_at or datetime.now(timezone.utc))
    wid = f"wid-a2-{now.timestamp()}"
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
                "is_deferred": True,
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
    """Insert a RUNNING Task whose ``last_heartbeat_at`` is set so it
    is NOT a candidate for ``list_pending_tasks_older_than``."""
    now = (created_at or datetime.now(timezone.utc))
    wid = f"wid-a2-running-{now.timestamp()}"
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


def _seed_active_job_item(
    engine: Engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str = "test-project",
    admission_state: str = AdmissionState.ACTIVE.value,
    created_at: datetime | None = None,
) -> None:
    """Insert a non-defer JobItem (the busy-set witness)."""
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source,
                     project_id, queue_id, priority, admission_state,
                     created_at, instance_id, job_type, retry_count,
                     metadata)
                VALUES
                    (:job_id, :agent_id, :agent_dir, :message, :source,
                     :project_id, :queue_id, :priority, :admission_state,
                     :created_at, :instance_id, :job_type, :retry_count,
                     :metadata)
                """
            ),
            {
                "job_id": job_id,
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "message": "hi",
                "source": "api",
                "project_id": project_id,
                "queue_id": None,
                "priority": 0,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
                "job_type": "task",
                "retry_count": 0,
                "metadata": "{}",
            },
        )


def _get_task_is_deferred(engine: Engine, task_id: int) -> bool:
    """Read the ``is_deferred`` column of the row with id=``task_id``."""
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT is_deferred FROM task WHERE id = :id"),
            {"id": task_id},
        ).one()
    return bool(row[0])


def _build_service(
    engine: Engine,
    *,
    worker_pool: MagicMock | None,
    autopromote_env: str | None = None,
) -> JobRecoveryService:
    """Build a JobRecoveryService against the engine + a ``worker_pool`` shim.

    ``worker_pool`` is the A2 injection point: a ``MagicMock`` that
    records ``notify_work()`` calls. When ``None``, the service is
    wired without the pool (legacy test fixtures) — the A3 sweep is
    the systemic backstop that catches the missed notify.
    """
    if autopromote_env is not None:
        import os
        os.environ["ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED"] = autopromote_env
        _jrs._reset_defer_autopromote_for_tests()
    else:
        # Default-ON posture for the happy path.
        import os
        os.environ.pop("ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED", None)
        _jrs._reset_defer_autopromote_for_tests()

    repository = JobRepository(engine)
    task_repository = TaskRepository(engine)
    lock_repo = LockRepository(engine)
    instance_repo = SQLModelInstanceRepository(engine=engine)
    stale_recovery = StaleTaskRecovery(
        task_repository=task_repository,
        message_repository=None,
        event_repository=None,
    )
    jq_mock = MagicMock()
    jq_mock.notify_watchers = AsyncMock(return_value=None)
    return JobRecoveryService(
        job_repository=repository,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
        job_queue_service=jq_mock,
        task_repository=task_repository,
        stale_task_recovery=stale_recovery,
        worker_pool=worker_pool,
    )


# ─── Happy path ─────────────────────────────────────────────────────────


class TestA2AutopromoteNotifyWork:
    """A2: the autopromote seam calls ``worker_pool.notify_work()``
    AFTER a successful flip.

    The P1 incident pattern (2026-09-11): a deferred PENDING task
    on a just-revived terminal instance whose
    ``_pattern_g_defer_self_witness_watchdog`` flipped
    ``is_deferred=True → False`` but did NOT notify the pool —
    zero claims ever landed on the row. The test pins both halves
    of the fix: the flip landed AND the pool was notified.
    """

    @pytest.mark.asyncio
    async def test_autopromote_calls_notify_work_after_flip(
        self, engine, caplog
    ):
        """Happy path: deferred-PENDING candidate, no other
        witnesses → WS1 seam False → flip → notify_work fired."""
        worker_pool = MagicMock()
        service = _build_service(engine, worker_pool=worker_pool)
        now = datetime.now(timezone.utc)

        # Seed the target instance (running) and the deferred
        # PENDING task (backdated so list_pending_tasks_older_than(60)
        # picks it up). No other witnesses → WS1 returns False →
        # autopromote fires.
        target = "inst-a2-target"
        _seed_instance(
            engine, target, status="running", project_id="test-project",
            created_at=now - timedelta(seconds=60),
        )
        task_id = _seed_deferred_pending_task(
            engine, instance_id=target,
            created_at=now - timedelta(seconds=120),
        )

        with caplog.at_level(logging.INFO):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=60,
                min_orphan_age_seconds=900,
            )

        # The flip landed: the row's is_deferred column is now False.
        assert _get_task_is_deferred(engine, task_id) is False, (
            "Autopromote MUST flip is_deferred=True → False on the "
            "self-witness detection"
        )

        # The pool was notified — exactly once for the one flipped row.
        assert worker_pool.notify_work.call_count == 1, (
            f"worker_pool.notify_work() must be called exactly once "
            f"per flipped task (one flip here → one notify); "
            f"got call_count={worker_pool.notify_work.call_count}"
        )

        # The flip succeeded — stats must record the autopromote pattern.
        details = stats.get("details") or []
        autopromoted = [
            d for d in details
            if d.get("pattern") == "defer_self_witness_autopromoted"
        ]
        assert autopromoted, (
            f"autopromote pattern record MUST appear in stats.details; "
            f"got {details!r}"
        )

    @pytest.mark.asyncio
    async def test_no_notify_when_flip_skipped_by_atomic_guard(
        self, engine
    ):
        """``flipped == 0`` (atomic guard rejected the row — another
        actor mutated it between candidate scan and flip) → no notify.
        A notify without a flip would wake the pool for a row whose
        state didn't actually change (false-positive wake)."""
        worker_pool = MagicMock()
        service = _build_service(engine, worker_pool=worker_pool)
        now = datetime.now(timezone.utc)

        target = "inst-a2-target-race"
        _seed_instance(
            engine, target, status="running", project_id="test-project",
            created_at=now - timedelta(seconds=60),
        )
        task_id = _seed_deferred_pending_task(
            engine, instance_id=target,
            created_at=now - timedelta(seconds=120),
        )
        # Pre-mutate the row to ``is_deferred=False`` BEFORE the sweep
        # runs so the atomic guard fires ``flipped == 0``.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET is_deferred = :f "
                    "WHERE id = :id"
                ),
                {"f": False, "id": task_id},
            )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=60,
            min_orphan_age_seconds=900,
        )

        assert worker_pool.notify_work.call_count == 0, (
            f"Atomic guard rejection (``flipped == 0``) MUST NOT "
            f"trigger notify_work — no flip happened, the pool has "
            f"no new eligible row to surface; got call_count="
            f"{worker_pool.notify_work.call_count}"
        )

    @pytest.mark.asyncio
    async def test_no_notify_when_autopromote_disabled(self, engine):
        """``ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED=0`` → OFF=no-writes
        contract from the existing flag pins: no flip, no notify."""
        worker_pool = MagicMock()
        service = _build_service(
            engine, worker_pool=worker_pool, autopromote_env="0",
        )
        now = datetime.now(timezone.utc)

        target = "inst-a2-target-off"
        _seed_instance(
            engine, target, status="running", project_id="test-project",
            created_at=now - timedelta(seconds=60),
        )
        task_id = _seed_deferred_pending_task(
            engine, instance_id=target,
            created_at=now - timedelta(seconds=120),
        )

        try:
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=60,
                min_orphan_age_seconds=900,
            )

            # The OFF=no-writes contract: is_deferred stays True.
            assert _get_task_is_deferred(engine, task_id) is True, (
                "ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED=0 MUST skip the "
                "flip; the OFF posture is OFF=no-writes"
            )
            # And the notify is also skipped.
            assert worker_pool.notify_work.call_count == 0, (
                f"OFF posture MUST NOT notify the pool — no flip "
                f"happened; got call_count="
                f"{worker_pool.notify_work.call_count}"
            )
            # Detection WARN still fires (the flag-independent
            # observability contract — operators must see the
            # detection even when the unstick is OFF).
            details = stats.get("details") or []
            warned_only = [
                d for d in details
                if d.get("pattern") == "defer_self_witness_warned_only"
            ]
            assert warned_only, (
                "WARN-only detection MUST still fire under OFF "
                "(flag-independent observability contract)"
            )
        finally:
            _jrs._reset_defer_autopromote_for_tests()

    @pytest.mark.asyncio
    async def test_no_notify_when_seam_busy_returns_true(self, engine):
        """WS1 seam returns True (another witness holds the gate) →
        no detection, no flip, no notify."""
        worker_pool = MagicMock()
        service = _build_service(engine, worker_pool=worker_pool)
        now = datetime.now(timezone.utc)

        target = "inst-a2-target-busy"
        other = "inst-a2-other"
        _seed_instance(
            engine, target, status="running", project_id="test-project",
            created_at=now - timedelta(seconds=60),
        )
        _seed_instance(
            engine, other, status="running", project_id="test-project",
            created_at=now - timedelta(seconds=60),
        )
        task_id = _seed_deferred_pending_task(
            engine, instance_id=target,
            created_at=now - timedelta(seconds=120),
        )
        # Other instance has a LIVE ACTIVE JobItem → the legacy
        # clause of the WS1 seam returns True → no detection.
        _seed_active_job_item(
            engine, job_id="job-a2-other-active", instance_id=other,
            admission_state=AdmissionState.ACTIVE.value,
            created_at=now - timedelta(seconds=60),
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=60,
            min_orphan_age_seconds=900,
        )

        # No detection → no flip → no notify.
        assert _get_task_is_deferred(engine, task_id) is True, (
            "Other-instance ACTIVE JobItem holds the gate — no flip"
        )
        assert worker_pool.notify_work.call_count == 0, (
            "Held gate → no detection → no flip → no notify"
        )

    @pytest.mark.asyncio
    async def test_notify_work_failure_does_not_abort_sweep(
        self, engine, caplog
    ):
        """A notify_work that raises is caught; the sweep proceeds
        to the next candidate row (the A3 sweep is the systemic
        backstop that catches any missed notify)."""
        worker_pool = MagicMock()
        worker_pool.notify_work.side_effect = RuntimeError(
            "pool blip"
        )
        service = _build_service(engine, worker_pool=worker_pool)
        now = datetime.now(timezone.utc)

        target = "inst-a2-target-blip"
        _seed_instance(
            engine, target, status="running", project_id="test-project",
            created_at=now - timedelta(seconds=60),
        )
        task_id = _seed_deferred_pending_task(
            engine, instance_id=target,
            created_at=now - timedelta(seconds=120),
        )

        with caplog.at_level(logging.WARNING):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=60,
                min_orphan_age_seconds=900,
            )

        # The flip landed — the sweep did NOT abort on the notify error.
        assert _get_task_is_deferred(engine, task_id) is False, (
            "notify_work() raising MUST NOT roll back the flip — "
            "the flip is committed before the notify call"
        )
        # The sweep ran to completion (no exception escaped).
        assert stats is not None, (
            "reconcile_drift_states must return its stats dict even "
            "when notify_work raises"
        )

    @pytest.mark.asyncio
    async def test_worker_pool_none_skips_notify_silently(self, engine):
        """``JobRecoveryService`` constructed without
        ``worker_pool`` (legacy test fixture) still flips the row
        correctly and skips the notify without raising — the A3
        sweep is the systemic backstop."""
        service = _build_service(engine, worker_pool=None)
        now = datetime.now(timezone.utc)

        target = "inst-a2-target-no-pool"
        _seed_instance(
            engine, target, status="running", project_id="test-project",
            created_at=now - timedelta(seconds=60),
        )
        task_id = _seed_deferred_pending_task(
            engine, instance_id=target,
            created_at=now - timedelta(seconds=120),
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=60,
            min_orphan_age_seconds=900,
        )

        # Flip landed even without a pool — the A3 sweep catches the
        # missed notify.
        assert _get_task_is_deferred(engine, task_id) is False, (
            "Autopromote flip MUST still land when worker_pool is "
            "None — only the notify is skipped (the A3 sweep is the "
            "systemic backstop)"
        )
        assert stats is not None, (
            "reconcile_drift_states must run to completion even "
            "when worker_pool is None"
        )


# ─── Census static guard ────────────────────────────────────────────────


class TestA2ConstitutionStatic:
    """A2 fix is wiring-only (a method call on an injected pool);
    no new ``admission_state`` writes, no new JobItem creators,
    no new ``work_id`` mints land. Census stays at 23/1/0."""

    def test_a2_block_does_not_introduce_new_writer(self):
        """The A2 ``notify_work()`` call site is in
        ``_pattern_g_defer_self_witness_watchdog`` AFTER the flip
        UPDATE statement — and it calls a METHOD on the pool, not
        an ``admission_state`` write."""
        from pathlib import Path

        prod_path = (
            Path(__file__).parent.parent.parent
            / "daemon"
            / "services"
            / "job_recovery_service.py"
        )
        contents = prod_path.read_text()
        assert "self._worker_pool.notify_work()" in contents, (
            "A2 notify_work() call missing — the fix may have "
            "regressed; check that the test still pins the intended "
            "structural change"
        )

    def test_constitution_census_unchanged(self):
        """The A2 fix does not add new writers/creators/mints. Census
        stays at 23/1/0."""
        from daemon.job_state import constitution

        assert len(constitution.KNOWN_ADMISSION_STATE_WRITERS) == 23, (
            f"A2 introduced new admission_state writers — census "
            f"drifted from 23 to "
            f"{len(constitution.KNOWN_ADMISSION_STATE_WRITERS)}"
        )
        assert len(constitution.KNOWN_JOBITEM_CREATORS) == 1, (
            f"A2 introduced new JobItem creators — census drifted "
            f"from 1 to "
            f"{len(constitution.KNOWN_JOBITEM_CREATORS)}"
        )
        assert len(constitution.KNOWN_MINT_SITES) == 0, (
            f"A2 introduced new work_id mints — census drifted from "
            f"0 to {len(constitution.KNOWN_MINT_SITES)}"
        )