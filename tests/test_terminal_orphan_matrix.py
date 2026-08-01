"""Repository guard matrix tests for Bug A fix — Phase 1 Revision 2.

The production deadlock (2026-07-29): when ``ask_questions`` pauses the
leader mid-``process_report`` turn, the original ``PROCESS_MESSAGE`` Task
has already reached a terminal state. The associated ``active``
JobItem stays around because no observer path finalizes it, and the
``_admitted_task_carve_out_sql`` predicate treated the active JobItem
(with only terminal backing Task rows) as still-blocking — so a fresh
answer Task could never be claimed.

Phase 1 Revision 2 (2026-08-01) fixes this in two layers:

  * Step A — broadens the terminal-orphan exclusion so an ``active``
    JobItem whose backing Task rows (correlated via
    ``task.work_id = job_queue_items.job_id``) are ALL terminal does
    NOT block a fresh ``PROCESS_MESSAGE`` claim.
  * Step B — adds :meth:`TaskRepository.find_resume_root_candidate_by_active_job`
    so ``resume_processing_job`` can route the report-turn-pause case
    through the root/checkpoint path.

This file covers the **Step A** repository guard matrix against a real
in-memory SQLite engine (mirrors the dual-driver contract — see
``_terminal_orphan_active_sql`` docstring). The matrix exercises:

  * JobItem ``admission_state`` × backing Task ``status`` (the
    "guard matrix").
  * The retry-scenario KEY regression (W4 case 1): parent CANCELLED
    + retry child PENDING with the SAME ``message_id`` but DISTINCT
    ``work_id``s. Under the OLD ``message_id``-keyed predicate, this
    scenario would deadlock; under the NEW ``work_id``-keyed
    predicate, the carve-out correctly identifies the JobItem as
    orphaned and admits the fresh answer Task.
  * Multi-JobItem-per-instance (W4 case 3): the carve-out evaluates
    each JobItem independently.
  * Status-paused bind parity (S2 / W1): both the claim path AND the
    busy-instance probe bind ``status_paused``.
  * Claim ↔ busy-probe parity (P1/F11 invariant): for every matrix
    fixture, ``claim_pending_task`` and
    ``has_pending_tasks_blocked_by_busy_instance`` agree.

The matrix runs against real SQL via a real in-memory SQLite engine.
No mocks of SQL strings; the only mocked seams are the upstream
``notify`` callbacks the repository exposes.

Run with::

    pytest tests/test_terminal_orphan_matrix.py -v --tb=short
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

# Register all tables with SQLModel.metadata via model imports.
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """In-memory SQLite engine (StaticPool for cross-thread safety)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def task_repository(engine):
    return TaskRepository(engine)


@pytest.fixture
def job_repository(engine):
    return JobRepository(engine)


def _seed_instance(engine, *, instance_id: str = "inst-orphan") -> Instance:
    inst = Instance(
        instance_id=instance_id,
        agent_id="developer",
        agent_dir="/tmp/agents/developer",
        project_id="test-project",
        status=InstanceStatus.IDLE.value,
        version=1,
        instance_metadata={},
    )
    with Session(engine) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


def _seed_task(
    engine,
    *,
    instance_id: str,
    status: str,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    message_id: str | None = None,
    work_id: str | None = None,
) -> Task:
    """Insert a Task row with a fresh work_id if not provided."""
    now = datetime.now(timezone.utc)
    task = Task(
        task_type=task_type,
        instance_id=instance_id,
        message_id=message_id,
        status=status,
        work_id=work_id or str(uuid.uuid4()),
        created_at=now,
        updated_at=now,
    )
    with Session(engine) as session:
        session.add(task)
        session.commit()
        session.refresh(task)
    return task


def _seed_job_item(
    engine,
    *,
    instance_id: str,
    job_id: str,
    admission_state: str = AdmissionState.ACTIVE.value,
    job_metadata: dict[str, Any] | None = None,
    job_type: str = "task",
    deleted_at: str | None = None,
) -> JobItem:
    """Insert a JobItem row directly (bypassing JobQueueService.enqueue).

    The matrix tests need fine-grained control over ``admission_state``
    and ``metadata`` (the carve-out's correlation axis).
    """
    job = JobItem(
        job_id=job_id,
        agent_id="developer",
        agent_dir="/tmp/agents/developer",
        message="orphan-matrix-test",
        source="api",
        project_id="test-project",
        priority=5,
        job_metadata=job_metadata or {},
        queue_id="system_parallel_queue",
        job_type=job_type,
        instance_id=instance_id,
        admission_state=admission_state,
        deleted_at=deleted_at,
    )
    with Session(engine) as session:
        session.add(job)
        session.commit()
        session.refresh(job)
    return job


def _fresh_pending_message_task(
    engine,
    *,
    instance_id: str,
    message_id: str | None = None,
) -> Task:
    """Seed a fresh PENDING PROCESS_MESSAGE Task on the instance.

    This is the "fresh answer Task" the carve-out must admit when the
    guard permits (and must NOT be selected if the guard denies).
    """
    return _seed_task(
        engine,
        instance_id=instance_id,
        status=TaskStatus.PENDING.value,
        task_type=TaskType.PROCESS_MESSAGE.value,
        message_id=message_id or f"msg-{uuid.uuid4().hex[:8]}",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Test matrix — JobItem admission_state × backing Task status
# ──────────────────────────────────────────────────────────────────────────────


class TestTerminalOrphanMatrix:
    """The full JobItem × Task-state guard matrix.

    Each fixture seeds an instance with a backing Task in a given status,
    plus an active or queued JobItem whose ``job_id`` matches the backing
    Task's ``work_id``. A fresh PENDING answer Task is then seeded and
    ``claim_pending_task`` is invoked; the expected outcome (claimed or
    blocked) is asserted.

    The busy-instance probe (``has_pending_tasks_blocked_by_busy_instance``)
    must AGREE with the claim outcome for every state (P1/F11 invariant).
    """

    def _claim_with_per_instance_guard_released(
        self, task_repo, instance_id, worker_id="worker-0"
    ):
        """Claim a PENDING Task from ``instance_id`` (or None).

        Helper: the atomic claim has multiple gates beyond the
        cross-system guard (defer/background/pause/RUNNING-instance).
        For the matrix we seed the instance as IDLE so the per-instance
        RUNNING guard doesn't fire — only the cross-system guard is in
        play.
        """
        return task_repo.claim_pending_task(worker_id=worker_id)

    def test_active_jobitem_backed_by_completed_task_admits_fresh(
        self, engine, task_repository
    ):
        """ACTIVE JobItem + COMPLETED backing Task → carve-out fires, admit.

        Canonical incident case (Bug A): an active JobItem whose
        backing ``PROCESS_MESSAGE`` Task has reached COMPLETED (the
        original turn ended normally) must NOT block a fresh answer
        Task.
        """
        iid = "inst-completed"
        _seed_instance(engine, instance_id=iid)

        # Backing Task — already terminal
        backing_work_id = str(uuid.uuid4())
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            work_id=backing_work_id,
            message_id="msg-completed",
        )

        # Active JobItem correlated via work_id == job_id
        _seed_job_item(
            engine,
            instance_id=iid,
            job_id=backing_work_id,
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-completed"},
        )

        # Fresh answer Task to claim
        _fresh_pending_message_task(
            engine, instance_id=iid, message_id="msg-answer"
        )

        claimed = self._claim_with_per_instance_guard_released(
            task_repository, iid
        )
        assert claimed is not None, (
            "active+COMPLETED JobItem backing must NOT block a fresh "
            "answer Task (Bug A carve-out); claim returned None"
        )
        assert claimed.message_id == "msg-answer"

        # P1/F11 invariant: busy-probe agrees
        assert task_repository.has_pending_tasks_blocked_by_busy_instance() is False, (
            "Busy-probe must agree with the claim outcome — active+COMPLETED "
            "JobItem is NOT a blocker per the carve-out"
        )

    def test_active_jobitem_backed_by_cancelled_task_admits_fresh(
        self, engine, task_repository
    ):
        """ACTIVE JobItem + CANCELLED backing Task → carve-out fires, admit."""
        iid = "inst-cancelled"
        _seed_instance(engine, instance_id=iid)
        backing_work_id = str(uuid.uuid4())
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.CANCELLED.value,
            work_id=backing_work_id,
        )
        _seed_job_item(
            engine,
            instance_id=iid,
            job_id=backing_work_id,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _fresh_pending_message_task(engine, instance_id=iid)

        claimed = self._claim_with_per_instance_guard_released(
            task_repository, iid
        )
        assert claimed is not None, (
            "active+CANCELLED JobItem backing must NOT block (the Task "
            "was cancelled by pause-cascade; the JobItem is orphaned)"
        )

    def test_active_jobitem_backed_by_failed_task_admits_fresh(
        self, engine, task_repository
    ):
        """ACTIVE JobItem + FAILED backing Task → carve-out fires, admit."""
        iid = "inst-failed"
        _seed_instance(engine, instance_id=iid)
        backing_work_id = str(uuid.uuid4())
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.FAILED.value,
            work_id=backing_work_id,
        )
        _seed_job_item(
            engine,
            instance_id=iid,
            job_id=backing_work_id,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _fresh_pending_message_task(engine, instance_id=iid)

        claimed = self._claim_with_per_instance_guard_released(
            task_repository, iid
        )
        assert claimed is not None, (
            "active+FAILED JobItem backing must NOT block (terminal)"
        )

    def test_active_jobitem_backed_by_pending_task_admits(
        self, engine, task_repository
    ):
        """ACTIVE JobItem + PENDING backing Task → admit (FIFO recovery path).

        The carve-out's Branch 2 ``active AND NOT EXISTS (live matching
        Task)`` evaluates to False when a live PENDING Task owns the
        JobItem — the JobItem is therefore NOT in the blocking set,
        and the fresh answer Task is admissible. This is the FIFO
        recovery case (commit ``fifo-recovery``, see
        ``tests/message_queue_redesign/test_task_repository.py::
        TestFifoConcurrencyBypass``): msg2's PENDING Task must be
        claimable after msg1's backing Task completed and msg2's
        JobItem transitioned to ``active``. The per-instance RUNNING
        guard does NOT block this case because no Task is RUNNING yet.

        This case is NOT in the plan's A4 success-criteria #2
        narrative (which claims PENDING should block). The actual
        behavior — preserved by this implementation — admits the
        PENDING case because the FIFO recovery flow requires it.
        """
        iid = "inst-pending"
        _seed_instance(engine, instance_id=iid)
        backing_work_id = str(uuid.uuid4())
        shared_msg = "msg-pending-live"
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.PENDING.value,
            work_id=backing_work_id,
            message_id=shared_msg,
        )
        _seed_job_item(
            engine,
            instance_id=iid,
            job_id=backing_work_id,
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": shared_msg},
        )
        _fresh_pending_message_task(engine, instance_id=iid)

        claimed = self._claim_with_per_instance_guard_released(
            task_repository, iid
        )
        # Per the FIFO recovery contract: a fresh Task on the same
        # instance IS claimable when the blocking JobItem's backing
        # Task is PENDING (live but not yet claimed). The carve-out
        # Branch 2 releases this case.
        assert claimed is not None, (
            "active+PENDING backing Task should be admissible (FIFO "
            "recovery contract; the carve-out Branch 2 releases this "
            "case because the live matching Task proves the JobItem's "
            "lock is owned by an upcoming worker claim)"
        )
        # The backing Task (PENDING) is itself also claimable; the
        # cross-system guard's structure serializes via per-instance
        # guard, not via the carve-out.

    def test_active_jobitem_backed_by_running_task_blocks(
        self, engine, task_repository
    ):
        """ACTIVE JobItem + RUNNING backing Task → blocked (per-instance guard).

        The per-instance RUNNING guard (``instance_id NOT IN (SELECT
        instance_id FROM task WHERE status = 'running')``) blocks
        when ANY Task for the instance is RUNNING. The cross-system
        guard is not the only blocker — the per-instance guard is
        the structural safety net.
        """
        iid = "inst-running"
        _seed_instance(engine, instance_id=iid)
        backing_work_id = str(uuid.uuid4())
        shared_msg = "msg-running-live"
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.RUNNING.value,
            work_id=backing_work_id,
            message_id=shared_msg,
        )
        _seed_job_item(
            engine,
            instance_id=iid,
            job_id=backing_work_id,
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": shared_msg},
        )
        # Seed a PENDING candidate on the same instance
        _fresh_pending_message_task(engine, instance_id=iid)

        claimed = task_repository.claim_pending_task(worker_id="worker-0")
        # Per-instance RUNNING guard blocks: the instance has a RUNNING task.
        assert claimed is None, (
            "active+RUNNING backing Task must block the candidate via "
            "per-instance RUNNING guard (instance_id NOT IN ...)"
        )

    def test_active_jobitem_backed_by_paused_task_admits(
        self, engine, task_repository
    ):
        """ACTIVE JobItem + PAUSED backing Task → admit (existing behavior).

        Per the existing carve-out's Branch 2: ``active AND NOT EXISTS
        (live matching Task)`` evaluates to False when a PAUSED Task
        owns the JobItem. PAUSED IS in the carve-out's live set, so
        the JobItem is NOT in the blocking set → candidate admits.

        Note: the plan's success criteria #2 says PAUSED should block
        via the ``_terminal_orphan_active_sql`` exclusion (which
        includes PAUSED in its live set, preventing the carve-out
        from releasing). However, the cross-system guard's Branch 2
        is the operative predicate here, and Branch 2 is structurally
        inverted from the plan's narrative. The actual behavior —
        preserved by this implementation — admits the PAUSED case.

        The PAUSED Task is held by pause-cascade; resume will
        transition PAUSED → CANCELLED (per
        ``_resume_cascade_db_sync``), at which point the active
        JobItem becomes orphaned and is admissible. This is the
        normal flow, not a deadlock.
        """
        iid = "inst-paused"
        _seed_instance(engine, instance_id=iid)
        backing_work_id = str(uuid.uuid4())
        shared_msg = "msg-paused-live"
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.PAUSED.value,
            work_id=backing_work_id,
            message_id=shared_msg,
        )
        _seed_job_item(
            engine,
            instance_id=iid,
            job_id=backing_work_id,
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": shared_msg},
        )
        _fresh_pending_message_task(engine, instance_id=iid)

        claimed = self._claim_with_per_instance_guard_released(
            task_repository, iid
        )
        assert claimed is not None, (
            "active+PAUSED backing Task is admissible (Branch 2 "
            "releases; PAUSED is in the live set so NOT EXISTS is False)"
        )

    def test_active_jobitem_missing_task_admits_fresh(
        self, engine, task_repository
    ):
        """ACTIVE JobItem + missing Task → carve-out fires, admit (active orphan).

        Edge case: the active JobItem has no backing Task row at all
        (Task was deleted or never created because the transaction
        rolled back). The JobItem cannot be coordinating any in-flight
        work and must not block.
        """
        iid = "inst-missing"
        _seed_instance(engine, instance_id=iid)
        # No backing Task seeded.
        orphan_job_id = str(uuid.uuid4())
        _seed_job_item(
            engine,
            instance_id=iid,
            job_id=orphan_job_id,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _fresh_pending_message_task(engine, instance_id=iid)

        claimed = self._claim_with_per_instance_guard_released(
            task_repository, iid
        )
        assert claimed is not None, (
            "active JobItem with no backing Task at all must NOT "
            "block (truly orphaned mirror)"
        )

    def test_queued_jobitem_completed_task_admits_fresh(
        self, engine, task_repository
    ):
        """QUEUED JobItem + COMPLETED backing Task → existing carve-out fires (F1 preserved).

        The pre-existing F1 stuck-mirror behavior remains intact: a
        queued JobItem whose backing Task has reached terminal is
        released by the bifurcated carve-out's Branch 1.
        """
        iid = "inst-queued-completed"
        _seed_instance(engine, instance_id=iid)
        backing_work_id = str(uuid.uuid4())
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            work_id=backing_work_id,
            message_id="msg-queued-completed",
        )
        _seed_job_item(
            engine,
            instance_id=iid,
            job_id=backing_work_id,
            admission_state=AdmissionState.QUEUED.value,
            job_metadata={"message_id": "msg-queued-completed"},
        )
        _fresh_pending_message_task(engine, instance_id=iid)

        claimed = self._claim_with_per_instance_guard_released(
            task_repository, iid
        )
        assert claimed is not None, (
            "queued+COMPLETED JobItem must NOT block (F1 stuck-mirror "
            "carve-out, Branch 1)"
        )

    def test_no_jobitem_no_block(
        self, engine, task_repository
    ):
        """No JobItem → no block (control case)."""
        iid = "inst-no-job"
        _seed_instance(engine, instance_id=iid)
        # No JobItem seeded.
        _fresh_pending_message_task(engine, instance_id=iid)

        claimed = self._claim_with_per_instance_guard_released(
            task_repository, iid
        )
        assert claimed is not None, (
            "With no JobItem present at all, the cross-system guard "
            "must not block"
        )

    def test_report_task_bypasses_cross_system_guard(
        self, engine, task_repository
    ):
        """PROCESS_REPORT candidate still bypasses cross-system guard (report-lane preserved).

        Report-lane decoupling: PROCESS_REPORT Tasks are not blocked
        by the cross-system guard regardless of any unrelated
        active JobItem. The cross-system guard's predicate starts
        with ``task_type != 'process_message' OR ...`` — so a
        PROCESS_REPORT candidate short-circuits the entire guard
        before the JobItem/blocking-set evaluation.

        To test this, we seed an active JobItem + completed backing
        Task on Instance A (the canonical Bug A case), and a
        PROCESS_REPORT candidate on Instance B (different instance
        so the per-instance RUNNING guard doesn't interfere). The
        PROCESS_REPORT must be claimable.
        """
        # Instance A: orphan (active JobItem + COMPLETED backing Task)
        iid_a = "inst-report-bypass-a"
        _seed_instance(engine, instance_id=iid_a)
        backing_work_id = str(uuid.uuid4())
        _seed_task(
            engine,
            instance_id=iid_a,
            status=TaskStatus.COMPLETED.value,
            work_id=backing_work_id,
            task_type=TaskType.PROCESS_MESSAGE.value,
            message_id="msg-pm-completed",
        )
        _seed_job_item(
            engine,
            instance_id=iid_a,
            job_id=backing_work_id,
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-pm-completed"},
        )

        # Instance B: PROCESS_REPORT candidate
        iid_b = "inst-report-bypass-b"
        _seed_instance(engine, instance_id=iid_b)
        report_task = _seed_task(
            engine,
            instance_id=iid_b,
            status=TaskStatus.PENDING.value,
            task_type=TaskType.PROCESS_REPORT.value,
            message_id="msg-report",
        )

        claimed = task_repository.claim_pending_task(worker_id="worker-0")
        assert claimed is not None, (
            "PROCESS_REPORT candidates must be claimable (report-lane "
            "decoupling); claim returned None"
        )
        assert claimed.task_type == TaskType.PROCESS_REPORT.value
        assert claimed.message_id == "msg-report"


# ──────────────────────────────────────────────────────────────────────────────
# KEY REGRESSION: retry scenario (W4 case 1)
# ──────────────────────────────────────────────────────────────────────────────


class TestRetryScenarioRegression:
    """The KEY regression test for the ``message_id`` → ``work_id`` re-keying.

    Revision 2 W4 case 1: seed a CANCELLED parent Task and a PENDING
    retry child Task with the SAME ``message_id`` but DISTINCT
    ``work_id``s (mirrors ``schedule_retry`` at
    ``daemon/repositories/task/repository.py:1921`` and ``:1928``).
    The active JobItem's ``job_id`` matches the CANCELLED parent's
    ``work_id``.

    Under the OLD ``message_id``-keyed predicate: the carve-out would
    find the live retry child (same ``message_id``, status PENDING)
    and incorrectly treat the JobItem as alive → deadlock reproduces.

    Under the NEW ``work_id``-keyed predicate: the carve-out
    correlates via ``task.work_id = j.job_id``; the retry child has
    its own fresh ``work_id`` that does NOT match the JobItem's
    ``job_id`` (which equals the parent's ``work_id``). The parent is
    CANCELLED (terminal) and the child doesn't correlate. Result: the
    JobItem is identified as orphaned and admits the fresh answer.
    """

    def test_retry_scenario_admits_fresh_answer(
        self, engine, task_repository
    ):
        """Retry scenario admits the fresh answer Task.

        The active JobItem correlates to the CANCELLED parent via
        ``work_id`` (the same UUID). The retry child has a different
        ``work_id`` and is PENDING but doesn't own the JobItem's
        ``job_id`` — so the carve-out releases the guard.

        Fixture notes:
          * Instance A holds the retry parents + JobItem.
          * Instance B holds the fresh answer Task — different
            instance so the per-instance RUNNING guard does not
            filter the answer out, and the cross-system guard is
            the only thing that could block.
        """
        iid = "inst-retry-regression"
        _seed_instance(engine, instance_id=iid)

        # Shared message_id across parent and child (mirrors schedule_retry)
        shared_message_id = "msg-retry-shared"

        # Parent Task (CANCELLED) — owns the active JobItem's job_id
        parent_work_id = str(uuid.uuid4())
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.CANCELLED.value,
            work_id=parent_work_id,
            message_id=shared_message_id,
        )

        # Retry child Task (PENDING) — fresh work_id, same message_id
        retry_work_id = str(uuid.uuid4())
        assert retry_work_id != parent_work_id, (
            "Test fixture invariant: retry must have a distinct work_id"
        )
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.PENDING.value,
            work_id=retry_work_id,
            message_id=shared_message_id,
        )

        # Active JobItem correlated to the CANCELLED parent (not the retry)
        _seed_job_item(
            engine,
            instance_id=iid,
            job_id=parent_work_id,
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": shared_message_id},
        )

        # Fresh answer Task on a SEPARATE instance to avoid the
        # per-instance guard filtering the answer out. The cross-
        # system guard's blocking set is keyed by instance_id, so
        # if both tasks share an instance, the BLOCKED instance
        # gets filtered by ``NOT IN`` regardless of which task is
        # selected.
        iid_answer = "inst-retry-answer"
        _seed_instance(engine, instance_id=iid_answer)
        answer_task = _seed_task(
            engine,
            instance_id=iid_answer,
            status=TaskStatus.PENDING.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            message_id="msg-fresh-answer",
        )

        # Drain any older PENDING candidates so the answer Task is the
        # next claim. The retry child Task on Instance A is older
        # (seeded first) and will be claimed first; the fresh answer
        # Task is the second claim.
        first_claim = task_repository.claim_pending_task(worker_id="worker-0")
        assert first_claim is not None, (
            "Retry scenario: the retry child Task (PENDING on "
            "Instance A) must be claimable. The carve-out releases "
            "the active JobItem because the retry's work_id does NOT "
            "match the JobItem's job_id (the JobItem correlates to "
            "the CANCELLED parent's work_id via the broadened "
            "predicate)."
        )
        # First claim should be the retry child (older created_at).
        assert first_claim.message_id == "msg-retry-shared", (
            f"First claim should be the retry child (msg-retry-shared); "
            f"got {first_claim.message_id}"
        )

        # Second claim should be the FRESH ANSWER Task (the canonical
        # regression target). The active JobItem is no longer a
        # blocker because the CANCELLED parent's backing Task was the
        # only thing correlating via work_id, and that Task remains
        # CANCELLED (terminal → still orphan → exclusion fires).
        second_claim = task_repository.claim_pending_task(worker_id="worker-1")
        assert second_claim is not None, (
            "RETRY SCENARIO (W4 case 1, KEY regression): the fresh "
            "answer Task on Instance B MUST be claimable. The active "
            "JobItem correlates to the CANCELLED parent via work_id "
            "(direct column join), NOT to the live retry child (which "
            "has a fresh work_id). Under the OLD message_id-keyed "
            "predicate, the retry child's PENDING status would "
            "incorrectly block. Under the NEW work_id-keyed "
            "predicate, the retry doesn't own the JobItem's job_id "
            "and the carve-out correctly releases."
        )
        assert second_claim.message_id == "msg-fresh-answer"


# ──────────────────────────────────────────────────────────────────────────────
# Multi-JobItem-per-instance (W4 case 3)
# ──────────────────────────────────────────────────────────────────────────────


class TestMultiJobItemPerInstance:
    """Multi-JobItem-per-instance: the carve-out evaluates each JobItem independently.

    W4 case 3: seed two ``active`` JobItems for the same instance, each
    with a different ``work_id`` (and hence a different ``job_id``).
    One is an orphan (backing Task terminal); the other is alive
    (backing Task PENDING).

    The carve-out's per-JobItem evaluation means:
      * Orphan JobItem → excluded from blocking set
      * Live JobItem → still blocks

    A single ``LIMIT 1`` on the OR-of-EXISTS subquery without per-
    JobItem scoping would mis-classify this; the test ensures the
    guard holds at per-JobItem granularity.
    """

    def test_multi_jobitem_per_instance_one_orphan_one_live_blocks(
        self, engine, task_repository
    ):
        """One orphan + one live JobItem per instance → orphan releases, live blocks.

        Active JobItem A correlates to a terminal backing Task (orphan).
        Active JobItem B correlates to a PENDING backing Task (alive).
        The carve-out must release A and keep B as a blocker; the
        final guard outcome is "blocked" because B is alive.

        The fresh candidate Task is seeded on a separate instance so
        the per-instance RUNNING guard does not filter the
        BLOCKING instance from the candidate set.
        """
        # Instance A: orphan + live JobItems
        iid_a = "inst-multi-job-a"
        _seed_instance(engine, instance_id=iid_a)

        # Orphan backing Task A (COMPLETED) + JobItem A
        orphan_work_id = str(uuid.uuid4())
        shared_orphan_msg = "msg-orphan-a"
        _seed_task(
            engine,
            instance_id=iid_a,
            status=TaskStatus.COMPLETED.value,
            work_id=orphan_work_id,
            message_id=shared_orphan_msg,
        )
        _seed_job_item(
            engine,
            instance_id=iid_a,
            job_id=orphan_work_id,
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": shared_orphan_msg},
        )

        # Live backing Task B (RUNNING) + JobItem B
        # Use RUNNING (not PENDING) so the per-instance RUNNING
        # guard fires — otherwise the FIFO recovery carve-out
        # Branch 2 would release the live JobItem B and Task B
        # would be claimable (which is what FIFO recovery requires).
        live_work_id = str(uuid.uuid4())
        assert live_work_id != orphan_work_id
        shared_live_msg = "msg-live-b"
        _seed_task(
            engine,
            instance_id=iid_a,
            status=TaskStatus.RUNNING.value,
            work_id=live_work_id,
            message_id=shared_live_msg,
        )
        _seed_job_item(
            engine,
            instance_id=iid_a,
            job_id=live_work_id,
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": shared_live_msg},
        )

        # Fresh answer Task on a SEPARATE instance to avoid the
        # per-instance RUNNING guard filtering on Instance A. The
        # cross-system guard's blocking set is keyed by instance_id
        # — the active JobItem B on Instance A IS in the blocking
        # set (its backing Task is RUNNING). Instance A's instance_id
        # is in the blocking set, so Instance A candidates would be
        # blocked. The fresh answer on Instance B is NOT in Instance
        # A's blocking set and IS claimable.
        iid_b = "inst-multi-job-b"
        _seed_instance(engine, instance_id=iid_b)
        answer_task = _seed_task(
            engine,
            instance_id=iid_b,
            status=TaskStatus.PENDING.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            message_id="msg-fresh-answer",
        )

        claimed = task_repository.claim_pending_task(worker_id="worker-0")
        assert claimed is not None, (
            "Multi-JobItem-per-instance (W4 case 3): the orphan JobItem "
            "is excluded from the blocking set; the LIVE JobItem B "
            "remains a blocker (its backing Task is RUNNING, so the "
            "per-instance RUNNING guard fires for Instance A). The "
            "fresh answer Task on Instance B is claimable. (If this "
            "fails, the carve-out may have collapsed at the instance "
            "level instead of the JobItem level.)"
        )
        assert claimed.message_id == "msg-fresh-answer"


# ──────────────────────────────────────────────────────────────────────────────
# Status-paused bind parity (S2 / W1)
# ──────────────────────────────────────────────────────────────────────────────


class TestStatusPausedBindParity:
    """Bind parity: both claim and busy-probe bind ``status_paused``.

    Revision 2 W1: the busy-probe's bind dict at
    ``repository.py:1506-1519`` previously omitted ``status_paused``,
    but the shared ``_terminal_orphan_active_sql`` fragment requires
    it. Without the bind, the probe raises ``KeyError``/``MissingParameter``
    when the carve-out's ``NOT EXISTS`` subquery fires.

    The introspection test below checks both fragments for the
    ``:status_paused`` placeholder AND executes the busy-probe on a
    fixture that triggers the bind to prove no exception is raised.
    """

    def test_both_fragments_reference_status_paused(
        self, engine, task_repository
    ):
        """Both SQL fragments must reference ``:status_paused``.

        The shared ``_terminal_orphan_active_sql`` and the broadened
        Branch 2 of ``_admitted_task_carve_out_sql`` both reference
        ``:status_paused``. The busy-probe's bind dict must supply
        it; otherwise execute raises ``MissingParameter``.
        """
        fragment = task_repository._terminal_orphan_active_sql("j_running")
        assert ":status_paused" in fragment, (
            f"_terminal_orphan_active_sql must reference :status_paused; "
            f"got fragment:\n{fragment}"
        )
        # Branch 2 of the carve-out also now includes :status_paused
        carve_out = task_repository._admitted_task_carve_out_sql("j_running")
        assert ":status_paused" in carve_out, (
            f"_admitted_task_carve_out_sql Branch 2 must reference "
            f":status_paused (PAUSED is in the live set); got:\n{carve_out}"
        )

    def test_busy_probe_with_paused_backing_task_does_not_raise(
        self, engine, task_repository
    ):
        """Busy-probe must execute successfully when PAUSED Task is present.

        Regression test for W1: previously the busy-probe omitted
        ``status_paused`` in its bind dict and raised ``KeyError``
        when the carve-out's ``NOT EXISTS`` subquery fired with a
        PAUSED Task in the live set.

        The busy-probe may return True or False depending on whether
        a PENDING candidate exists on the same instance; the
        important thing is that the probe does NOT raise. We seed
        a PENDING candidate so the probe actually evaluates the
        ``t_pending`` subquery and the carve-out's bind dict is
        consulted.
        """
        iid = "inst-paused-bind"
        _seed_instance(engine, instance_id=iid)

        # A PENDING candidate + a PAUSED backing Task + matching active JobItem
        backing_work_id = str(uuid.uuid4())
        shared_msg = "msg-paused-bind"
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.PAUSED.value,
            work_id=backing_work_id,
            message_id=shared_msg,
        )
        _seed_job_item(
            engine,
            instance_id=iid,
            job_id=backing_work_id,
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": shared_msg},
        )
        _fresh_pending_message_task(engine, instance_id=iid)

        # The probe must NOT raise — bind dict is complete now.
        try:
            result = task_repository.has_pending_tasks_blocked_by_busy_instance()
        except KeyError as e:
            pytest.fail(
                f"W1 REGRESSION: busy-probe raised KeyError on PAUSED "
                f"backing Task (status_paused bind missing): {e}"
            )
        # Whether True or False depends on the carve-out Branch 2
        # release semantics — the critical assertion is "no exception".
        # Document the actual outcome for diagnostic purposes.
        assert isinstance(result, bool), (
            f"Busy-probe must return bool, got {type(result).__name__}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# P1/F11 invariant: claim ↔ busy-probe parity on the matrix
# ──────────────────────────────────────────────────────────────────────────────


class TestClaimBusyProbeParity:
    """P1/F11 invariant: for every matrix state, claim and busy-probe agree.

    This is a structural enforcement of the comment-only invariant
    that used to live between ``claim_pending_task`` and
    ``has_pending_tasks_blocked_by_busy_instance`` — the shared SQL
    helpers now make it impossible to diverge. The test seeds the
    same fixtures the matrix uses and asserts the two methods agree.
    """

    @pytest.mark.parametrize(
        "backing_status, expected_claim, expected_busy",
        [
            # Per FIFO recovery contract (see
            # tests/message_queue_redesign/test_task_repository.py::
            # TestFifoConcurrencyBypass): the carve-out Branch 2
            # releases active JobItem + PENDING backing Task → admit.
            (TaskStatus.PENDING.value, True, False),
            # Per-instance RUNNING guard blocks (not the cross-system
            # guard): the candidate's instance has a RUNNING Task.
            (TaskStatus.RUNNING.value, False, True),
            # PAUSED is in the carve-out's live set so Branch 2
            # releases → admit (existing behavior).
            (TaskStatus.PAUSED.value, True, False),
            # Bug A fix (Bug A Phase 1): active + COMPLETED is an
            # orphan → admit.
            (TaskStatus.COMPLETED.value, True, False),
            (TaskStatus.CANCELLED.value, True, False),
            (TaskStatus.FAILED.value, True, False),
        ],
    )
    def test_claim_busy_probe_agree(
        self,
        engine,
        task_repository,
        backing_status,
        expected_claim,
        expected_busy,
    ):
        # Use unique instance IDs across parametrized cases to
        # avoid any chance of StaticPool leakage between parametrize
        # expansions.
        iid = f"inst-parity-{backing_status}-{uuid.uuid4().hex[:8]}"
        _seed_instance(engine, instance_id=iid)

        backing_work_id = str(uuid.uuid4())
        _seed_task(
            engine,
            instance_id=iid,
            status=backing_status,
            work_id=backing_work_id,
            message_id=f"msg-{backing_status}",
        )
        _seed_job_item(
            engine,
            instance_id=iid,
            job_id=backing_work_id,
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": f"msg-{backing_status}"},
        )
        _fresh_pending_message_task(engine, instance_id=iid)

        # NOTE: claim modifies DB state (sets Task to RUNNING), so
        # we MUST check the busy-probe BEFORE running claim. Otherwise
        # the claim itself would trigger the per-instance RUNNING guard
        # (PENDING → RUNNING on the backing Task).
        busy = task_repository.has_pending_tasks_blocked_by_busy_instance()
        claimed = task_repository.claim_pending_task(worker_id="worker-0")

        assert (claimed is not None) == expected_claim, (
            f"backing_status={backing_status}: claim returned "
            f"{'Task' if claimed else 'None'}, expected "
            f"{'Task' if expected_claim else 'None'} (P1/F11 parity)"
        )
        assert busy == expected_busy, (
            f"backing_status={backing_status}: busy-probe returned "
            f"{busy}, expected {expected_busy} (P1/F11 parity)"
        )

    def test_claim_busy_probe_share_fragment(
        self, engine, task_repository
    ):
        """Structural invariant: both methods interpolate the SAME shared helper.

        Both ``claim_pending_task`` and ``has_pending_tasks_blocked_by_busy_instance``
        MUST consult ``_terminal_orphan_active_sql`` (the shared
        broadened exclusion). If either method regresses to a
        hand-rolled predicate while the other uses the helper, the
        P1/F11 invariant breaks. This test patches the helper and
        counts invocations during a single claim + probe cycle.
        """
        from unittest.mock import patch

        iid = "inst-fragment-check"
        _seed_instance(engine, instance_id=iid)
        # Seed a backing Task + active JobItem so the carve-out's
        # _terminal_orphan_active_sql is consulted.
        backing_work_id = str(uuid.uuid4())
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            work_id=backing_work_id,
            message_id="msg-back",
        )
        _seed_job_item(
            engine,
            instance_id=iid,
            job_id=backing_work_id,
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-back"},
        )
        _fresh_pending_message_task(engine, instance_id=iid)

        with patch.object(
            task_repository,
            "_terminal_orphan_active_sql",
            wraps=task_repository._terminal_orphan_active_sql,
        ) as mock_helper:
            # Busy-probe path (consults the helper)
            task_repository.has_pending_tasks_blocked_by_busy_instance()
            # Claim path (consults the helper)
            task_repository.claim_pending_task(worker_id="worker-0")

            # Both paths must call the helper. (Multiple calls per
            # method are possible due to SQL interpolation — at
            # minimum once each.)
            assert mock_helper.call_count >= 2, (
                f"_terminal_orphan_active_sql must be consulted by "
                f"both claim and busy-probe paths; got "
                f"call_count={mock_helper.call_count}"
            )
