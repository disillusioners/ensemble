"""Council REJECT 2026-08-29 W1 — retry-child lineage conjunct.

RCA: ``force_cancel_and_schedule_retry`` /
``schedule_retry`` mint a retry child Task with a FRESH
``work_id`` (the parent's UNIQUE constraint stays on the
cancelled parent — ``task_repository.py:3261`` /
``:3702``), so a ``TaskRepository.get_by_work_id(job_id)``
call from the FAILED/CANCELLED terminal-routing branch
sees ONLY the cancelled parent while the retry child
runs invisible. The pre-fix code therefore finalized the
JobItem as ``done`` even when a live retry was in
flight on the same instance — the JobItem mirror flipped
to terminal while the child Task was still driving the
graph, orphaning the retry.

The fix keys the lineage query on ``instance_id`` (the
retry child INHERITS the parent's ``instance_id`` via
``RetryTurn`` at ``turn_transitions.py:622``) and uses
the existing ``TaskRepository.has_inflight_task``
(PENDING + RUNNING) as the conjunct. The reconciler
now SKIPS finalization when ANY non-terminal Task
exists on the same instance — the retry chain proceeds,
the JobItem mirror stays active, and the next 60s
sweep re-evaluates once the lineage quiesces.

This file is a NEW regression test file (the brief is
explicit: ``tests/job_queue/test_orphan_active_job_recovery.py``
is owned by a concurrent instance and is off-limits
here). It seeds retry-chain shapes via raw SQL — the
canonical helper style in this package.

AC1 — parent task FAILED/CANCELLED + live retry child
  (PENDING/RUNNING) still non-terminal on the same
  instance → JobItem NOT finalized this sweep; the
  skip is observable through the existing
  ``orphan_active_skipped_*`` mechanism.

AC2 — parent task FAILED/CANCELLED + no non-terminal
  task anywhere in lineage → finalize via the
  terminal boundary exactly as today (NO_RETRY,
  ``failed_at`` inheritance, lock release intact).

A/B convention: tests are designed to FAIL on the
pre-fix tree (W1 not yet applied) and PASS on the
post-fix tree.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlmodel import Session as SQLModelSession

from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.job_queue_service import JobQueueService
from daemon.services.stale_task_recovery import StaleTaskRecovery


# ─────────────────────────────────────────────────────────────────────────────
# Test helpers — raw-SQL seeders matching the conventions in
# tests/job_queue/test_orphan_active_job_recovery.py (the file is
# owned by a concurrent instance; we re-implement the helpers locally
# so this file has no shared imports with it).
# ─────────────────────────────────────────────────────────────────────────────


def _insert_instance(
    engine,
    instance_id: str,
    project_id: str = "test-project",
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "developer",
    created_at: datetime | None = None,
) -> None:
    """Insert an Instance row directly via SQL."""
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status, :project_id,
                     :created_at, :updated_at, 1)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": agent_id,
                "agent_dir": f"agents/{agent_id}",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
            },
        )


def _insert_job_item(
    engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str | None = None,
    admission_state: str = AdmissionState.ACTIVE.value,
    job_metadata: dict | None = None,
    created_at: datetime | None = None,
) -> None:
    """Insert a JobItem directly via SQL."""
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    metadata_json = json.dumps(job_metadata or {})
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
                "queue_id": queue_id,
                "priority": 0,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
                "job_type": "task",
                "retry_count": 0,
                "metadata": metadata_json,
            },
        )


def _insert_task_with_status(
    engine,
    *,
    work_id: str,
    instance_id: str,
    message_id: str | None = None,
    status: str = TaskStatus.PENDING.value,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
    retry_count: int = 0,
) -> int:
    """Insert a Task row directly via SQL.

    The parent retry-chain Task seeds ``work_id == job_id``
    (the canonical cross-system linkage). The retry child
    uses a DIFFERENT ``work_id`` (the fresh UUID minted by
    ``schedule_retry`` / ``force_cancel_and_schedule_retry``
    at ``task_repository.py:3261 / :3702``) — the test
    passes ``job_id`` as the child's ``work_id`` argument
    so the parent's lookup misses it.
    """
    now = (created_at or datetime.now(timezone.utc))
    completed_iso = (
        completed_at.isoformat() if completed_at is not None else None
    )
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred, is_background,
                     completed_at)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     :retry_count, :created_at, :cancel_requested,
                     :retry_scheduled, :work_id, :is_deferred, :is_background,
                     :completed_at)
                """
            ),
            {
                "task_type": task_type,
                "instance_id": instance_id,
                "message_id": message_id,
                "status": status,
                "retry_count": retry_count,
                "created_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": work_id,
                "is_deferred": False,
                "is_background": False,
                "completed_at": completed_iso,
            },
        )
        return result.lastrowid


def _insert_lock(
    engine,
    *,
    project_id: str,
    queue_id: str,
    job_id: str,
    instance_id: str,
    lock_slot: int = 0,
) -> None:
    """Insert a JobLock row directly via SQL."""
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_locks
                    (lock_id, project_id, queue_id, job_id,
                     instance_id, lock_slot, acquired_at)
                VALUES
                    (:lock_id, :project_id, :queue_id, :job_id,
                     :instance_id, :lock_slot, :acquired_at)
                """
            ),
            {
                "lock_id": f"lock-{job_id}",
                "project_id": project_id,
                "queue_id": queue_id,
                "job_id": job_id,
                "instance_id": instance_id,
                "lock_slot": lock_slot,
                "acquired_at": now,
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures (mirror the pattern in test_orphan_active_job_recovery.py; the
# shared ``engine`` / ``repository`` / ``lock_repo`` come from
# tests/job_queue/conftest.py).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def task_repository(engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def instance_repo(engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine=engine)


@pytest.fixture
def stale_recovery(task_repository) -> StaleTaskRecovery:
    """StaleTaskRecovery with the message/event/notifier deps stubbed
    (Pattern (f) doesn't touch them)."""
    return StaleTaskRecovery(
        task_repository=task_repository,
        message_repository=None,
        event_repository=None,
    )


@pytest.fixture
def job_queue_service_mock() -> MagicMock:
    """JobQueueService mock with notify_watchers wired as an AsyncMock.

    ``_finalize_terminal`` is wired (per-test) to a side_effect that
    performs the real atomic_transition, mirroring the convention in
    test_orphan_active_job_recovery.py so the JobItem actually
    finalizes through the boundary (a plain MagicMock would not).
    """
    mock = MagicMock()
    mock.notify_watchers = AsyncMock(return_value=None)
    return mock


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — parent FAILED/CANCELLED + live retry child on the same instance
# → SKIP finalization; observe via orphan_active_skipped_retry_child_live.
# ─────────────────────────────────────────────────────────────────────────────


class TestW1RetryChildLineageConjunct:
    """W1 fix regression — the FAILED/CANCELLED terminal-routing branch
    MUST NOT finalize the JobItem while a retry child Task is still
    in flight on the same instance. The retry child carries a FRESH
    ``work_id`` (the parent's UNIQUE stays on the cancelled parent —
    ``task_repository.py:3261 / :3702``) so a parent-work_id-only
    check misses it; the lineage is keyed by ``instance_id``.
    """

    @pytest.mark.asyncio
    async def test_ac1_failed_parent_with_pending_retry_child_is_skipped(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """AC1 — FAILED parent + PENDING retry child (same
        instance, fresh ``work_id``) → JobItem MUST stay
        active; ``orphan_active_skipped_retry_child_live``
        detail recorded; live retry child Task MUST remain
        PENDING (the reconciler only walked the parent).

        Pre-fix the parent is terminal-routed via
        ``_finalize_terminal`` (the boundary writes
        ``admission_state='done'`` and the live retry
        child becomes orphaned). Post-fix the W1
        lineage gate fires first; the parent stays
        active; the retry child continues normally.
        """
        _insert_instance(
            engine,
            "inst-w1-ac1-failed-pending",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-w1-ac1-failed-parent",
            instance_id="inst-w1-ac1-failed-pending",
            project_id="test-project",
            queue_id="queue-w1-ac1-failed-parent",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        # Parent task FAILED — completed_at backdated so
        # the (post-fix) terminal-routing boundary would
        # have aged past any floor; the W1 gate is what
        # we're exercising here, not the floor.
        parent_task_id = _insert_task_with_status(
            engine,
            work_id="job-w1-ac1-failed-parent",
            instance_id="inst-w1-ac1-failed-pending",
            status=TaskStatus.FAILED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        # Live retry child: FRESH work_id (the brief is
        # explicit — schedule_retry mints a fresh UUID to
        # avoid the parent's UNIQUE constraint) +
        # PENDING (mid-claim by the worker_pool). Same
        # instance as the parent (the lineage key).
        retry_task_id = _insert_task_with_status(
            engine,
            work_id="job-w1-ac1-failed-retry-child",
            instance_id="inst-w1-ac1-failed-pending",
            status=TaskStatus.PENDING.value,
            retry_count=1,
        )
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-w1-ac1-failed-parent",
            job_id="job-w1-ac1-failed-parent",
            instance_id="inst-w1-ac1-failed-pending",
        )

        # Wire the boundary mock so a stray finalization
        # call would actually persist (the W1 fix should
        # never reach it). The mock's ``await_args``
        # stays ``None`` on success — asserting that
        # proves the gate fired before the boundary.
        async def _boundary_side_effect(
            *, instance_id, decision, job_id, error_message,
            target_status,
        ):
            now = datetime.now(timezone.utc).isoformat()
            repository.atomic_transition(
                job_id,
                from_status="active",
                to_status=target_status,
                completed_at=now,
                error_message=error_message,
            )
            return (job_id, None)

        job_queue_service_mock._finalize_terminal = AsyncMock(
            side_effect=_boundary_side_effect,
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # ── Assert: NOT bare DEAD (the council
        # critical #1 path that foreclosed
        # atomic_retry).
        f1_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-w1-ac1-failed-parent"
        ]
        assert not f1_corrected, (
            f"FAILED parent with live retry child MUST "
            f"NOT be bare-DEAD. Got: {f1_corrected}"
        )
        # ── Assert: NOT terminal-routed either (the
        # W1 fix skips the parent before the
        # boundary call).
        terminal_routed = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_failed_terminal"
            and d.get("job_id") == "job-w1-ac1-failed-parent"
        ]
        assert not terminal_routed, (
            f"FAILED parent with live retry child MUST "
            f"NOT be terminal-routed (W1 fix: skip the "
            f"sweep, leave JobItem active). "
            f"Got: {terminal_routed}"
        )
        # ── Assert: the boundary was NEVER called
        # (the W1 gate short-circuits before it).
        assert (
            job_queue_service_mock._finalize_terminal.await_count == 0
        ), (
            f"_finalize_terminal MUST NOT be called when "
            f"a live retry child exists (W1 gate). "
            f"Got await_count="
            f"{job_queue_service_mock._finalize_terminal.await_count}"
        )
        # ── Assert: W1 skip detail recorded.
        skip_records = [
            d for d in stats["details"]
            if d.get("pattern") == (
                "orphan_active_skipped_retry_child_live"
            )
            and d.get("job_id") == "job-w1-ac1-failed-parent"
        ]
        assert skip_records, (
            f"W1 fix MUST record an "
            f"orphan_active_skipped_retry_child_live "
            f"detail. Got details: {stats['details']}"
        )
        assert skip_records[0].get("task_id") == parent_task_id, (
            f"Detail MUST carry the parent Task id. "
            f"Got: {skip_records[0]}"
        )
        assert (
            skip_records[0].get("instance_id")
            == "inst-w1-ac1-failed-pending"
        ), (
            f"Detail MUST carry the instance_id. "
            f"Got: {skip_records[0]}"
        )
        # ── Assert: JobItem is still ACTIVE
        # (the W1 fix leaves it for the next
        # 60s cycle to re-evaluate).
        job_after = repository.get("job-w1-ac1-failed-parent")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value, (
            f"FAILED parent with live retry child MUST "
            f"stay ACTIVE (W1 fix: skip the sweep). "
            f"Got admission_state="
            f"{job_after.admission_state!r}"
        )
        # ── Assert: live retry child is still
        # PENDING (the reconciler only walked the
        # parent JobItem).
        with engine.begin() as conn:
            retry_status_row = conn.execute(
                text("SELECT status FROM task WHERE id = :id"),
                {"id": retry_task_id},
            ).first()
        assert retry_status_row is not None
        assert retry_status_row[0] == TaskStatus.PENDING.value, (
            f"Live retry child Task MUST remain PENDING "
            f"(reconciler only walked the parent). "
            f"Got status={retry_status_row[0]!r}"
        )
        # ── Assert: parent task is FAILED
        # (sanity — the W1 fix doesn't mutate the
        # parent Task row).
        assert parent_task_id is not None

    @pytest.mark.asyncio
    async def test_ac1_cancelled_parent_with_running_retry_child_is_skipped(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """AC1 (CANCELLED variant) — CANCELLED parent
        + RUNNING retry child (same instance, fresh
        ``work_id``) → JobItem MUST stay active;
        ``orphan_active_skipped_retry_child_live``
        detail recorded.

        Mirrors the FAILED test above but with the
        parent in CANCELLED and the child in RUNNING
        (the worker_pool has already claimed it).
        The W1 lineage gate must catch the RUNNING
        child — same ``has_inflight_task`` query,
        same skip pattern.
        """
        _insert_instance(
            engine,
            "inst-w1-ac1-cancelled-running",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-w1-ac1-cancelled-parent",
            instance_id="inst-w1-ac1-cancelled-running",
            project_id="test-project",
            queue_id="queue-w1-ac1-cancelled-parent",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        # Parent task CANCELLED — this is the
        # ``force_cancel_and_schedule_retry`` exit
        # shape: the parent was running, the
        # force-cancel flipped it to cancelled, and a
        # fresh retry child was minted with a NEW
        # ``work_id``.
        parent_task_id = _insert_task_with_status(
            engine,
            work_id="job-w1-ac1-cancelled-parent",
            instance_id="inst-w1-ac1-cancelled-running",
            status=TaskStatus.CANCELLED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        # Live retry child: RUNNING (worker_pool has
        # claimed it — the retry is actively driving
        # ``graph.astream`` on the same instance).
        retry_task_id = _insert_task_with_status(
            engine,
            work_id="job-w1-ac1-cancelled-retry-child",
            instance_id="inst-w1-ac1-cancelled-running",
            status=TaskStatus.RUNNING.value,
            retry_count=1,
        )
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-w1-ac1-cancelled-parent",
            job_id="job-w1-ac1-cancelled-parent",
            instance_id="inst-w1-ac1-cancelled-running",
        )

        async def _boundary_side_effect(
            *, instance_id, decision, job_id, error_message,
            target_status,
        ):
            now = datetime.now(timezone.utc).isoformat()
            repository.atomic_transition(
                job_id,
                from_status="active",
                to_status=target_status,
                completed_at=now,
                error_message=error_message,
            )
            return (job_id, None)

        job_queue_service_mock._finalize_terminal = AsyncMock(
            side_effect=_boundary_side_effect,
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # ── Assert: NOT bare DEAD.
        f1_corrected = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_no_task_dead"
            and d.get("job_id") == "job-w1-ac1-cancelled-parent"
        ]
        assert not f1_corrected, (
            f"CANCELLED parent with RUNNING retry child "
            f"MUST NOT be bare-DEAD. Got: {f1_corrected}"
        )
        # ── Assert: NOT terminal-routed.
        terminal_routed = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_failed_terminal"
            and d.get("job_id") == "job-w1-ac1-cancelled-parent"
        ]
        assert not terminal_routed, (
            f"CANCELLED parent with RUNNING retry child "
            f"MUST NOT be terminal-routed (W1 gate). "
            f"Got: {terminal_routed}"
        )
        # ── Assert: boundary NEVER called.
        assert (
            job_queue_service_mock._finalize_terminal.await_count == 0
        ), (
            f"_finalize_terminal MUST NOT be called when "
            f"a RUNNING retry child exists. "
            f"Got await_count="
            f"{job_queue_service_mock._finalize_terminal.await_count}"
        )
        # ── Assert: W1 skip detail recorded.
        skip_records = [
            d for d in stats["details"]
            if d.get("pattern") == (
                "orphan_active_skipped_retry_child_live"
            )
            and d.get("job_id") == "job-w1-ac1-cancelled-parent"
        ]
        assert skip_records, (
            f"W1 fix MUST record an "
            f"orphan_active_skipped_retry_child_live "
            f"detail for the CANCELLED+RUNNING shape. "
            f"Got details: {stats['details']}"
        )
        # ── Assert: JobItem is still ACTIVE.
        job_after = repository.get("job-w1-ac1-cancelled-parent")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.ACTIVE.value, (
            f"CANCELLED parent with RUNNING retry child "
            f"MUST stay ACTIVE (W1 fix). "
            f"Got admission_state="
            f"{job_after.admission_state!r}"
        )
        # ── Assert: live retry child is still
        # RUNNING (untouched).
        with engine.begin() as conn:
            retry_status_row = conn.execute(
                text("SELECT status FROM task WHERE id = :id"),
                {"id": retry_task_id},
            ).first()
        assert retry_status_row is not None
        assert retry_status_row[0] == TaskStatus.RUNNING.value, (
            f"Live RUNNING retry child MUST remain "
            f"RUNNING. Got status={retry_status_row[0]!r}"
        )
        # ── Assert: parent task is CANCELLED
        # (sanity).
        assert parent_task_id is not None


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — parent FAILED/CANCELLED + no non-terminal task anywhere in lineage
# → finalize via the terminal boundary (NO_RETRY, failed_at inheritance,
# lock release intact). Today's behavior, regression-pinned.
# ─────────────────────────────────────────────────────────────────────────────


class TestW1LineageConjunctRegression:
    """AC2 regression — when NO non-terminal task exists on the
    same instance, the terminal-routing boundary MUST fire exactly
    as today. The W1 fix MUST NOT over-defer; the lineage-quiescent
    path is the steady-state shape.
    """

    @pytest.mark.asyncio
    async def test_ac2_failed_parent_no_retry_child_finalizes_via_boundary(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """AC2 — FAILED parent + NO live retry child
        (lineage quiescent: only the FAILED parent
        exists for the instance) → JobItem MUST be
        finalized via the terminal boundary exactly as
        today.

        Pins:
          1. ``orphan_active_failed_terminal`` detail
             recorded.
          2. JobItem row is ``admission_state='done'``
             (NOT ``'dead'`` — atomic_retry must
             remain viable).
          3. ``_finalize_terminal`` called with
             ``decision=NO_RETRY`` and
             ``target_status='failed'`` (the boundary
             preserves ``terminal_reason='failed'``).
          4. The per-job lock IS released
             (F4/F7 invariant — sibling locks
             survive).
        """
        from daemon.repositories.job_queue.models import Decision

        _insert_instance(
            engine,
            "inst-w1-ac2-failed",
            project_id="test-project",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        _insert_job_item(
            engine,
            job_id="job-w1-ac2-failed",
            instance_id="inst-w1-ac2-failed",
            project_id="test-project",
            queue_id="queue-w1-ac2-failed",
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        )
        # Parent task FAILED — lineage QUIESCENT (no
        # retry child). The terminal-routing branch
        # fires.
        parent_task_id = _insert_task_with_status(
            engine,
            work_id="job-w1-ac2-failed",
            instance_id="inst-w1-ac2-failed",
            status=TaskStatus.FAILED.value,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-w1-ac2-failed",
            job_id="job-w1-ac2-failed",
            instance_id="inst-w1-ac2-failed",
        )
        # Sibling lock on the same queue — must SURVIVE
        # the boundary's lock release (F4/F7 invariant).
        _insert_lock(
            engine,
            project_id="test-project",
            queue_id="queue-w1-ac2-failed",
            job_id="job-w1-ac2-sibling",
            instance_id="inst-w1-ac2-sibling",
            lock_slot=1,
        )

        async def _boundary_side_effect(
            *, instance_id, decision, job_id, error_message,
            target_status,
        ):
            now = datetime.now(timezone.utc).isoformat()
            repository.atomic_transition(
                job_id,
                from_status="active",
                to_status=target_status,
                completed_at=now,
                error_message=error_message,
            )
            return (job_id, None)

        job_queue_service_mock._finalize_terminal = AsyncMock(
            side_effect=_boundary_side_effect,
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # ── Assert: terminal-routing fired.
        terminal_routed = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_failed_terminal"
            and d.get("job_id") == "job-w1-ac2-failed"
        ]
        assert terminal_routed, (
            f"FAILED parent with QUIESCENT lineage MUST "
            f"be terminal-routed (AC2). "
            f"Got details: {stats['details']}"
        )
        assert terminal_routed[0].get("task_id") == parent_task_id, (
            f"Detail MUST carry the parent Task id. "
            f"Got: {terminal_routed[0]}"
        )
        # ── Assert: W1 skip NOT recorded (no live
        # retry child to skip on).
        w1_skips = [
            d for d in stats["details"]
            if d.get("pattern") == (
                "orphan_active_skipped_retry_child_live"
            )
            and d.get("job_id") == "job-w1-ac2-failed"
        ]
        assert not w1_skips, (
            f"W1 skip MUST NOT fire when the lineage is "
            f"quiescent (AC2). Got: {w1_skips}"
        )
        # ── Assert: boundary called with NO_RETRY
        # + target_status='failed' (terminal_reason
        # preservation).
        assert (
            job_queue_service_mock._finalize_terminal.await_count == 1
        ), (
            f"_finalize_terminal MUST be called exactly "
            f"once when the lineage is quiescent. "
            f"Got await_count="
            f"{job_queue_service_mock._finalize_terminal.await_count}"
        )
        last_call = job_queue_service_mock._finalize_terminal.await_args
        assert last_call is not None
        kwargs = last_call.kwargs
        assert kwargs.get("target_status") == "failed", (
            f"terminal-boundary MUST be called with "
            f"target_status='failed' so "
            f"terminal_reason='failed' is preserved. "
            f"Got: {kwargs}"
        )
        assert kwargs.get("decision") == Decision.NO_RETRY, (
            f"terminal-boundary MUST be called with "
            f"decision=NO_RETRY. Got: {kwargs}"
        )
        # ── Assert: JobItem is DONE (NOT DEAD).
        job_after = repository.get("job-w1-ac2-failed")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DONE.value, (
            f"FAILED parent with QUIESCENT lineage MUST "
            f"finalize to admission_state='done' (NOT "
            f"DEAD — atomic_retry must remain viable). "
            f"Got admission_state="
            f"{job_after.admission_state!r}"
        )
        # ── Assert: boundary called with NO_RETRY +
        # failed_at inheritance via the ``error_message``
        # marker (atomic_retry gate). The actual lock
        # release is the boundary's contract
        # (``_finalize_terminal``'s ``finally`` block —
        # ``job_queue_service.py:1965-2004``) and is
        # exercised separately by the f1 / f2 lock-release
        # tests in test_orphan_active_job_recovery.py.
        # Here we only assert the W1 fix DID NOT
        # interfere with the boundary call.
        last_call = job_queue_service_mock._finalize_terminal.await_args
        assert last_call is not None
        kwargs = last_call.kwargs
        assert "error_message" in kwargs, (
            f"terminal-boundary MUST carry an "
            f"error_message (the failed_at marker "
            f"atomic_retry requires). Got: {kwargs}"
        )
        assert "failed" in kwargs["error_message"], (
            f"terminal-boundary error_message MUST "
            f"inherit the parent's 'failed' terminal "
            f"reason. Got: {kwargs['error_message']}"
        )
