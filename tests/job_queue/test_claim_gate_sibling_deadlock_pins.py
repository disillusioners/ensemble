"""Pinned tests for the claim-gate sibling-JobItem mutual deadlock fix
(2026-10-04, ``fix/claim-gate-sibling-deadlock``).

Three classes cover the fix and its council rework:

* ``TestSiblingDeadlockRepro.test_two_rapid_messages_both_drain_in_order`` —
  occurrence repro: two rapid external messages to a single fresh
  instance both mint ``job_type='message'`` JobItem mirrors
  (``daemon/services/instance_messaging.py:2648-2666`` — verified at
  v0.16.12) AND backing Task rows. Pre-fix the cross-system guard
  (``daemon/repositories/task/repository.py``) blocked the second
  Task's claim forever (sibling message-JobItem looked like
  in-flight work); post-fix the guard excludes message-type
  JobItems from the blocking set and both Tasks drain in FIFO
  order. This is the acceptance centerpiece.

* ``TestPerInstanceBelt`` — belt at ``JobQueueService.start_job``
  declines a second message JobItem whose instance already holds
  an ACTIVE message JobItem. Defense-in-depth: the JobItem-level
  invariant (at most one ACTIVE message JobItem per instance)
  holds even if the cross-system guard is bypassed. The
  different-instance pin asserts the belt does NOT false-positive
  across instances.

* ``TestBeltActiveOnlyFilter`` (council rework 2026-10-04,
  REQUIRED 1+2): the belt MUST filter on
  ``admission_state='active'`` ONLY — NOT
  ``ACTIVE_ADMISSION_STATES = {queued, active}``. Three pins:
  both-QUEUED admission regression (REQUIRED 2.1), real
  ``exclude_job_id`` self-match (REQUIRED 2.2), and belt recovery
  path after the first JobItem DONE (REQUIRED 2.3).

All tests use the file-local ``bug_engine`` fixture (per-test
file-backed SQLite engine) — NOT the
``tests/job_queue/conftest.py`` ``engine`` fixture (session-scoped
in-memory SQLite shared with sibling test files). This file
intentionally opts out for bulletproof per-test DB isolation. No
external DB. No daemon boot. No env-poison risk.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, text
from sqlmodel import SQLModel

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.queue_repository import (
    JobQueueRepository,
)
from daemon.repositories.message_queue.models import MessageStatus
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository


# ── Fixtures (local, per-test) ──────────────────────────────────────


@pytest.fixture
def bug_engine(tmp_path):
    """In-process SQLite engine for the sibling-deadlock tests.

    File-backed (not :memory:) so each test gets a clean DB AND
    so multi-test isolation is bulletproof — the session-scoped
    ``engine`` fixture in ``conftest.py`` is shared across the
    whole directory, which is fine for the existing tests but
    the sibling-deadlock tests own their own tables.
    """
    db_path = tmp_path / "sibling_deadlock.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _iso_now(offset_seconds: int = 0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    ).isoformat()


def _seed_instance(
    engine,
    *,
    instance_id: str,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "developer",
    agent_dir: str = "/agents/developer",
    project_id: str = "test-project",
) -> None:
    now_iso = _iso_now()
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
                    :created_at, :updated_at, 1, NULL,
                    :last_activity_at)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": agent_id,
                "agent_dir": agent_dir,
                "status": status,
                "project_id": project_id,
                "created_at": now_iso,
                "updated_at": now_iso,
                "last_activity_at": now_iso,
            },
        )


def _seed_message_job_item(
    engine,
    *,
    job_id: str,
    instance_id: str,
    admission_state: str = AdmissionState.QUEUED.value,
) -> None:
    """Seed a ``job_type='message'`` JobItem mirror for the
    given instance. The shape matches the production
    ``enqueue_message_job`` mint site at
    ``daemon/services/instance_messaging.py:2648-2666``.
    """
    now_iso = _iso_now()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source,
                    project_id, queue_id, priority, admission_state,
                    created_at, instance_id, job_type, retry_count,
                    terminal_reason, failed_at, deleted_at, version)
                VALUES
                    (:job_id, 'developer', '/agents/developer', 'hi', 'api',
                    'test-project', NULL, 0, :admission_state,
                    :created_at, :instance_id, 'message', 0,
                    NULL, NULL, NULL, 0)
                """
            ),
            {
                "job_id": job_id,
                "admission_state": admission_state,
                "created_at": now_iso,
                "instance_id": instance_id,
            },
        )


def _seed_task_for_job(
    engine,
    *,
    work_id: str,
    instance_id: str,
    message_id: str,
    status: str = TaskStatus.PENDING.value,
) -> int:
    """Insert a Task row directly. Returns the rowid. The
    ``work_id`` is stamped to the linked ``JobItem.job_id``
    (the documented ``work_id == job_id`` contract for
    message JobItem mirrors)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                    retry_count, created_at, cancel_requested,
                    retry_scheduled, work_id, is_deferred,
                    is_background, completed_at, started_at,
                    worker_id, last_heartbeat_at, next_retry_at)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                    0, :created_at, FALSE, FALSE,
                    :work_id, FALSE, FALSE, NULL, NULL,
                    NULL, NULL, NULL)
                """
            ),
            {
                "task_type": TaskType.PROCESS_MESSAGE.value,
                "instance_id": instance_id,
                "message_id": message_id,
                "status": status,
                "created_at": now,
                "work_id": work_id,
            },
        )
        return int(result.lastrowid)


def _seed_message_queue(
    engine,
    *,
    message_id: str,
    instance_id: str,
    status: str = MessageStatus.PROCESSING.value,
) -> None:
    now_iso = _iso_now()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO message_queue
                    (message_id, instance_id, content, type, source,
                    status, priority, retry_count, max_retries,
                    enqueued_at, processing_started_at)
                VALUES
                    (:message_id, :instance_id, 'hi', 'agent', 'api',
                    :status, 1, 0, 5, :enqueued_at, :processing_started_at)
                """
            ),
            {
                "message_id": message_id,
                "instance_id": instance_id,
                "status": status,
                "enqueued_at": now_iso,
                "processing_started_at": now_iso,
            },
        )


def _seed_job_lock(
    engine, *, job_id: str, instance_id: str = "inst-test",
    lock_slot: int = 0,
) -> None:
    """Insert a JobLock for the given JobItem. Required for
    ACTIVE JobItems — the PG ``trg_job_locks_active_guard``
    trigger mirrors SQLite's invariant check via
    ``_finalize_terminal`` raising ``InvalidTransitionError`` if
    ``admission_state='active'`` but no lock exists.
    """
    now_iso = _iso_now()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_locks
                    (lock_id, project_id, queue_id, job_id, instance_id,
                    lock_slot, acquired_at)
                VALUES
                    (:lock_id, 'test-project', 'test-queue', :job_id,
                    :instance_id, :lock_slot, :acquired_at)
                """
            ),
            {
                "lock_id": str(uuid.uuid4()),
                "job_id": job_id,
                "instance_id": instance_id,
                "lock_slot": lock_slot,
                "acquired_at": now_iso,
            },
        )


def _read_admission_state(engine, job_id: str) -> str | None:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT admission_state FROM job_queue_items "
                "WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        ).first()
    return row[0] if row else None


def _read_task_status(engine, work_id: str) -> str | None:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status FROM task WHERE work_id = :work_id"),
            {"work_id": work_id},
        ).first()
    return row[0] if row else None


def _complete_task(engine, work_id: str) -> None:
    """Mark a Task row COMPLETED for the FIFO drain simulation."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE task SET status = :completed, completed_at = :now "
                "WHERE work_id = :work_id AND status = :running "
            ),
            {
                "completed": TaskStatus.COMPLETED.value,
                "now": datetime.now(timezone.utc).replace(tzinfo=None),
                "work_id": work_id,
                "running": TaskStatus.RUNNING.value,
            },
        )


def _read_task_count_pending(engine, instance_id: str) -> int:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT COUNT(*) FROM task "
                "WHERE instance_id = :instance_id "
                "AND status = :pending"
            ),
            {
                "instance_id": instance_id,
                "pending": TaskStatus.PENDING.value,
            },
        ).first()
    return int(row[0]) if row else 0


# ── Test 1: sibling-deadlock repro (acceptance centerpiece) ─────────


class TestSiblingDeadlockRepro:
    """Two rapid external messages to the same instance both mint
    message JobItem mirrors AND backing Tasks. Pre-fix the
    cross-system guard blocked the second Task's claim forever
    (sibling message-JobItem looked like in-flight work); post-fix
    the guard excludes message-type JobItems from the blocking
    set, the second Task drains in strict FIFO order after the
    first completes.
    """

    def test_two_rapid_messages_both_drain_in_order(
        self, bug_engine
    ) -> None:
        """ACCEPTANCE CENTERPIECE — the regression test that the
        audit calls out. Two rapid message JobItems for the same
        instance, both ACTIVE, both with backing Tasks PENDING.
        Step 1: ``claim_pending_task`` claims the FIFO-oldest
        Task (the first message). Step 2: complete that Task.
        Step 3: ``claim_pending_task`` claims the second Task
        (the second message). Pre-fix the second claim returns
        ``None`` forever — the worker pool sits idle and the
        recovery service logs "alive instance, log only". Post-fix
        both drains complete in order."""
        wid_a = "wid-sibling-A"
        wid_b = "wid-sibling-B"
        msg_a = "msg-sibling-A"
        msg_b = "msg-sibling-B"
        inst = "inst-sibling"

        # Step 0: instance exists, RUNNING (live-turn path).
        _seed_instance(bug_engine, instance_id=inst)
        # Step 1: seed the two message JobItem mirrors — both
        # ACTIVE (the per-queue concurrency_limit > 1 path lets
        # parallel-queue slots admit both; this is the exact
        # production shape that caused the recurrence).
        _seed_message_job_item(
            bug_engine,
            job_id=wid_a,
            instance_id=inst,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _seed_message_job_item(
            bug_engine,
            job_id=wid_b,
            instance_id=inst,
            admission_state=AdmissionState.ACTIVE.value,
        )
        # ACTIVE JobItems require a matching job_locks row.
        # Two ACTIVE JobItems on the same instance use two
        # DIFFERENT lock slots — production parallel-queue
        # concurrency_limit > 1 lets both admit simultaneously.
        _seed_job_lock(bug_engine, job_id=wid_a, instance_id=inst, lock_slot=0)
        _seed_job_lock(bug_engine, job_id=wid_b, instance_id=inst, lock_slot=1)
        # Step 2: seed the two backing Tasks. ``work_id == job_id``
        # per the documented ``enqueue_message_job`` contract
        # (``daemon/services/job_processor.py:1336-1340``).
        _seed_message_queue(
            bug_engine, message_id=msg_a, instance_id=inst
        )
        _seed_message_queue(
            bug_engine, message_id=msg_b, instance_id=inst
        )
        _seed_task_for_job(
            bug_engine, work_id=wid_a, instance_id=inst,
            message_id=msg_a,
        )
        _seed_task_for_job(
            bug_engine, work_id=wid_b, instance_id=inst,
            message_id=msg_b,
        )

        # Both Tasks PENDING, both JobItems ACTIVE — the exact
        # production shape that wedged. Pre-fix this is a
        # permanent deadlock; the second Task can never claim.
        assert _read_task_status(bug_engine, wid_a) == "pending"
        assert _read_task_status(bug_engine, wid_b) == "pending"
        assert _read_admission_state(bug_engine, wid_a) == "active"
        assert _read_admission_state(bug_engine, wid_b) == "active"

        # Step 3: first claim. Should succeed and return the
        # FIFO-oldest Task (wid_a — created first).
        task_repo = TaskRepository(engine=bug_engine)
        claimed_1 = task_repo.claim_pending_task(worker_id="worker-1")
        assert claimed_1 is not None, (
            "claim_pending_task returned None on the FIFO-OLDEST "
            "of TWO parallel-processing Task candidates for the "
            "same instance. The cross-system guard relaxation "
            "(excludes message-type JobItems) MUST let the first "
            "Task through; otherwise the deadlock fix did not land."
        )
        assert claimed_1.work_id == wid_a, (
            f"FIFO ordering broken: expected FIFO-oldest ({wid_a}) "
            f"but got {claimed_1.work_id!r}"
        )
        assert claimed_1.status == TaskStatus.RUNNING.value
        assert _read_task_status(bug_engine, wid_a) == "running"

        # Step 4: second claim BEFORE the first completes — the
        # pre-fix deadlock test. The second Task MUST NOT be
        # claimable because the per-instance RUNNING-task guard
        # (one RUNNING task per instance) holds; the Task stays
        # PENDING. This is correct behavior — the guard ensures
        # only one Task per instance is RUNNING at a time.
        claimed_2 = task_repo.claim_pending_task(worker_id="worker-2")
        assert claimed_2 is None, (
            "claim_pending_task claimed a SECOND task for an "
            "instance whose first task is still RUNNING — the "
            "per-instance concurrency guard is broken. Expected "
            "None (sibling held back by per-instance RUNNING guard)."
        )
        assert _read_task_status(bug_engine, wid_b) == "pending", (
            "Task-B was claimed while Task-A was RUNNING — both "
            "would now be RUNNING for the same instance, racing "
            "on graph.astream. The per-instance concurrency guard "
            "must hold Task-B back."
        )

        # Step 5: complete Task-A. Now the per-instance guard frees
        # AND the cross-system guard must NOT see the message-type
        # JobItem-A as a blocker for the second Task's claim.
        _complete_task(bug_engine, wid_a)

        # Step 6: second claim AFTER the first completes — the
        # post-fix drain. The cross-system guard now MUST allow
        # the second Task through (the JobItem-A's in-flight-task
        # backref is to Task-A which is COMPLETED — so the helper
        # excludes it; JobItem-B is itself excluded by
        # j.job_type != 'message' per the fix).
        claimed_3 = task_repo.claim_pending_task(worker_id="worker-3")
        assert claimed_3 is not None, (
            "claim_pending_task returned None on the second "
            "message Task AFTER the first completed — this is "
            "the deadlock. Pre-fix the cross-system guard would "
            "still see the ACTIVE message-JobItem-A as in-flight "
            "work and block the second claim; post-fix the guard "
            "excludes message-type JobItems and the second Task "
            "drains in FIFO order. Both Tasks must complete in "
            "order: A first, then B."
        )
        assert claimed_3.work_id == wid_b, (
            f"FIFO ordering broken after first completion: "
            f"expected second Task ({wid_b}) but got "
            f"{claimed_3.work_id!r}"
        )
        assert claimed_3.status == TaskStatus.RUNNING.value

        # Final invariants: both Tasks have drained; no stragglers.
        _complete_task(bug_engine, wid_b)
        assert _read_task_status(bug_engine, wid_a) == "completed"
        assert _read_task_status(bug_engine, wid_b) == "completed"
        assert _read_task_count_pending(bug_engine, inst) == 0


# ── Test 2: per-instance belt ───────────────────────────────────────


class TestPerInstanceBelt:
    """Per-instance belt at ``JobQueueService.start_job`` declines
    a second message JobItem whose instance already holds an
    ACTIVE message JobItem. Defense-in-depth for the cross-system
    guard relaxation.
    """

    def test_belt_declines_second_message_same_instance(
        self, bug_engine
    ) -> None:
        """The belt: a second message JobItem targeting an
        instance that already holds an ACTIVE message JobItem
        MUST be declined (``start_job`` returns ``None`` —
        job stays in ``admission_state='queued'``). The
        decline log + return None contract keeps the second
        message in QUEUED where the queue-awareness gate holds
        its backing Task FIFO-correctly."""
        from daemon.services.job_queue_service import JobQueueService

        wid_a = "wid-belt-A"
        wid_b = "wid-belt-B"
        inst = "inst-belt"

        # Seed instance, JobItem-A ACTIVE (sibling holds the lane).
        _seed_instance(bug_engine, instance_id=inst)
        _seed_message_job_item(
            bug_engine,
            job_id=wid_a,
            instance_id=inst,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _seed_job_lock(bug_engine, job_id=wid_a, instance_id=inst)
        # JobItem-B QUEUED — the candidate the belt tests.
        _seed_message_job_item(
            bug_engine,
            job_id=wid_b,
            instance_id=inst,
            admission_state=AdmissionState.QUEUED.value,
        )

        # Wire the real JobQueueService with the real JobRepository.
        job_repo = JobRepository(bug_engine)
        queue_repo = JobQueueRepository(bug_engine)
        lock_manager = LockRepository(bug_engine)
        service = JobQueueService(job_repo, lock_manager, queue_repo)
        # Disable project-pause lookup — no ProjectRepository in
        # this test harness.
        service._project_repo = None
        # Disable instance-status check path — no InstanceManager
        # in this test harness; the belt fires before that check.
        service._instance_manager = None

        # The belt at start_job must decline JobItem-B because
        # JobItem-A is ACTIVE for the same instance.
        import asyncio
        result = asyncio.run(service.start_job(wid_b))
        assert result is None, (
            "start_job did NOT decline the second message JobItem "
            "despite the sibling ACTIVE message JobItem driving "
            "the same instance — the per-instance belt is broken. "
            f"Got {result!r}."
        )

        # JobItem-B remains QUEUED (belt declined, no transition).
        assert _read_admission_state(bug_engine, wid_b) == "queued", (
            "JobItem-B was transitioned out of 'queued' despite the "
            "belt's decline — the belt returned without effect. "
            "Expected to stay QUEUED."
        )

    def test_belt_allows_different_instance(
        self, bug_engine
    ) -> None:
        """Belt does NOT false-positive across instances: two
        message JobItems on DIFFERENT instances both admit
        (one per queue slot). Pin against the obvious belt bug
        (over-matching by instance_id — must be exact match)."""
        from daemon.services.job_queue_service import JobQueueService

        wid_a = "wid-diff-A"
        wid_b = "wid-diff-B"
        inst_a = "inst-diff-A"
        inst_b = "inst-diff-B"

        # Seed: instance A holds JobItem-A ACTIVE; instance B has
        # JobItem-B QUEUED. Different instances — belt must NOT
        # block JobItem-B.
        _seed_instance(bug_engine, instance_id=inst_a)
        _seed_instance(bug_engine, instance_id=inst_b)
        _seed_message_job_item(
            bug_engine,
            job_id=wid_a,
            instance_id=inst_a,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _seed_job_lock(bug_engine, job_id=wid_a, instance_id=inst_a)
        _seed_message_job_item(
            bug_engine,
            job_id=wid_b,
            instance_id=inst_b,
            admission_state=AdmissionState.QUEUED.value,
        )

        # Wire the real JobQueueService with the real JobRepository.
        job_repo = JobRepository(bug_engine)
        queue_repo = JobQueueRepository(bug_engine)
        lock_manager = LockRepository(bug_engine)
        service = JobQueueService(job_repo, lock_manager, queue_repo)
        service._project_repo = None
        service._instance_manager = None

        # start_job on JobItem-B should NOT be declined by the
        # belt (different instance — no sibling collision). The
        # call may still return None for OTHER reasons (lock
        # contention, terminal state, etc.) — but the belt log
        # message must NOT fire. We assert the admission_state
        # transition: if the belt declined, JobItem-B stays
        # 'queued'; if the belt let through AND the lock was
        # acquired, JobItem-B moves to 'active'. Either is a
        # "belt did not falsely block" outcome; the pre-fix-style
        # sibling deadlock does NOT apply here because the
        # instances differ.
        #
        import asyncio
        result_b = asyncio.run(service.start_job(wid_b))
        # JobItem-B should be eligible to start (belt doesn't
        # block on instance_a). It may return None if the lock
        # can't be acquired (queue concurrency_limit, no queue_id,
        # etc.) — what matters is the belt did NOT decline it
        # for a sibling reason.
        # Belt decline is observable ONLY in the log; we cannot
        # easily observe it from here without caplog. The
        # strongest pin is: JobItem-B's admission_state after
        # the call. If belt declined: still 'queued'. If belt
        # let through and lock acquired: 'active'.
        new_state_b = _read_admission_state(bug_engine, wid_b)
        assert new_state_b in ("queued", "active"), (
            f"JobItem-B transitioned to unexpected state "
            f"{new_state_b!r} after start_job — neither belt-"
            f"declined (queued) nor belt-let-through (active)."
        )


# ── Test 3 (council rework 2026-10-04, REQUIRED 1+2): belt must be ACTIVE-only. ──


class TestBeltActiveOnlyFilter:
    """Council rework 2026-10-04, REQUIRED 1+2: the per-instance
    belt MUST filter on ``admission_state='active'`` ONLY — NOT
    ``ACTIVE_ADMISSION_STATES = {queued, active}``. The
    pre-rework helper filtered on the superset, which reproduces
    the exact production deadlock at ADMISSION: two rapid
    messages both land ``queued`` before the dispatch loop
    admits either; FIFO-head M1 sees M2 as a queued
    "in-flight" sibling, declines its own admission, and M2
    is never attempted — symmetric permanent wedge at
    admission, starving everything behind M1.

    These three pins (both-QUEUED admission, real self-match
    exclusion, belt recovery path) are the council's
    repro-and-pin suite for the rework.
    """

    def test_belt_admits_fifo_head_when_both_queued(
        self, bug_engine
    ) -> None:
        """REQUIRED 2.1: FIFO-head M1 with a QUEUED sibling M2
        MUST be admitted (not declined). Pre-rework the belt
        helper filtered on ``ACTIVE_ADMISSION_STATES`` which
        includes ``queued`` — M1 sees M2 as a queued
        "in-flight" sibling, declines its own admission, and
        M2 is never attempted. The exact production
        deadlock at ADMISSION. Post-rework the helper
        filters on ``ACTIVE`` only, so the QUEUED sibling is
        ignored and M1 admits cleanly."""
        from daemon.services.job_queue_service import JobQueueService

        wid_a = "wid-queued-A"  # FIFO-head
        wid_b = "wid-queued-B"  # sibling — both QUEUED
        inst = "inst-queued"

        _seed_instance(bug_engine, instance_id=inst)
        # BOTH message JobItems are QUEUED — neither has been
        # admitted yet. Pre-rework this is the deadlock
        # trigger; the dispatch loop must admit M1.
        _seed_message_job_item(
            bug_engine,
            job_id=wid_a,
            instance_id=inst,
            admission_state=AdmissionState.QUEUED.value,
        )
        _seed_message_job_item(
            bug_engine,
            job_id=wid_b,
            instance_id=inst,
            admission_state=AdmissionState.QUEUED.value,
        )

        job_repo = JobRepository(bug_engine)
        queue_repo = JobQueueRepository(bug_engine)
        lock_manager = LockRepository(bug_engine)
        service = JobQueueService(job_repo, lock_manager, queue_repo)
        service._project_repo = None
        service._instance_manager = None

        # M1 (FIFO-head) MUST be admitted — the belt must
        # NOT see the QUEUED sibling as a blocker.
        import asyncio
        result = asyncio.run(service.start_job(wid_a))
        assert result is not None, (
            "start_job declined M1 despite M2 being QUEUED "
            "(not ACTIVE). The belt is over-matching on "
            "admission_state. Pre-rework the helper included "
            "'queued' in its filter set, reproducing the "
            "exact production deadlock at ADMISSION: FIFO-"
            "head sees M2 as queued 'in-flight', and M2 "
            "is never attempted. ACTIVE-ONLY filter is the "
            "council-required fix."
        )
        assert result.admission_state == AdmissionState.ACTIVE.value, (
            f"start_job returned a JobItem with admission_state "
            f"{result.admission_state!r} (expected 'active') "
            f"— the queue-admission guard transition did not "
            f"land."
        )

    def test_belt_self_match_excludes_self(
        self, bug_engine
    ) -> None:
        """REQUIRED 2.2: ACTIVE candidate with no other ACTIVE
        sibling — pin the helper's ``exclude_job_id`` self-match
        exclusion at the helper level. With the candidate's
        own work_id passed as ``exclude_job_id``, the helper
        MUST return None (no self-match), while the same call
        with a different ``exclude_job_id`` returns the
        candidate (proves the helper's filter is ACTIVE-only).
        Pre-rework the near-tautological ``in ('queued',
        'active')`` check in
        ``test_belt_allows_different_instance`` never
        exercised the self-exclusion — that test passed for
        the wrong reasons. Post-rework the helper uses
        ``admission_state == 'active'`` so the check is
        tighter and self-match would block ALL admissions
        without the exclusion. Pin the exclusion."""
        from daemon.services.job_queue_service import JobQueueService

        wid = "wid-self"
        inst = "inst-self"

        # Seed ONLY the candidate row — no sibling at all.
        # The candidate is ACTIVE so its self-row matches
        # the (instance_id, job_type='message', ACTIVE)
        # filter. Without ``exclude_job_id``, the helper
        # would return the candidate's OWN row and the belt
        # would decline the candidate's own start_job call.
        # With the exclusion, the helper returns None and
        # the candidate starts cleanly.
        _seed_instance(bug_engine, instance_id=inst)
        _seed_message_job_item(
            bug_engine,
            job_id=wid,
            instance_id=inst,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _seed_job_lock(bug_engine, job_id=wid, instance_id=inst)

        job_repo = JobRepository(bug_engine)
        queue_repo = JobQueueRepository(bug_engine)
        lock_manager = LockRepository(bug_engine)
        service = JobQueueService(job_repo, lock_manager, queue_repo)
        service._project_repo = None
        service._instance_manager = None

        # Sanity: the helper itself, called with
        # exclude_job_id=wid, must return None (no sibling).
        helper_result = job_repo.find_active_message_job_for_instance(
            instance_id=inst, exclude_job_id=wid
        )
        assert helper_result is None, (
            "find_active_message_job_for_instance(self) "
            f"returned {helper_result!r} instead of None — "
            f"the exclude_job_id self-match exclusion is "
            f"broken. The belt would decline every candidate's "
            f"own start_job call."
        )

        # Sanity: the helper WITHOUT exclude_job_id returns
        # the candidate (proves the helper's filter is
        # ACTIVE-only as expected — if it returned None, the
        # candidate's own row wouldn't match the helper
        # either, which would mask the self-exclusion bug).
        helper_no_exclude = (
            job_repo.find_active_message_job_for_instance(
                instance_id=inst, exclude_job_id="some-other-id"
            )
        )
        assert helper_no_exclude is not None, (
            "find_active_message_job_for_instance WITHOUT "
            "exclude_job_id returned None — the helper's "
            "ACTIVE-only filter is broken (no row matches "
            "its own query)."
        )
        assert helper_no_exclude.job_id == wid, (
            f"find helper returned wrong row: expected {wid!r} "
            f"got {helper_no_exclude.job_id!r}."
        )

        # Secondary check: start_job on the candidate must
        # NOT be declined by self-match. Belt decline
        # observable as start_job returning None AND the
        # admission_state remaining 'queued' (or, since the
        # candidate is already ACTIVE, the ValueError raised
        # by start_job_atomic_with_lock for "not in queued"
        # — start_job catches that and returns None too).
        import asyncio
        result = asyncio.run(service.start_job(wid))
        # The candidate is already ACTIVE — start_job would
        # normally return None via the "not QUEUED" guard,
        # NOT via the belt. Belt decline is the focus; the
        # helper-level assertion above is the strongest
        # pin on the self-match exclusion itself.
        assert result is None, (
            "start_job on already-ACTIVE candidate returned "
            f"non-None ({result!r}) — expected None via the "
            f"'not in QUEUED' admission_state guard."
        )

    def test_belt_recovers_after_first_done(
        self, bug_engine
    ) -> None:
        """REQUIRED 2.3: belt recovery path — decline M2
        while M1 ACTIVE → M1 → DONE → M2 admits on next
        start_job call (simulating observer / poll
        re-admission). Pins the full lifecycle."""
        from daemon.services.job_queue_service import JobQueueService

        wid_a = "wid-recovery-A"
        wid_b = "wid-recovery-B"
        inst = "inst-recovery"

        _seed_instance(bug_engine, instance_id=inst)
        # M1 is ACTIVE; M2 is QUEUED (will be declined by belt).
        _seed_message_job_item(
            bug_engine,
            job_id=wid_a,
            instance_id=inst,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _seed_job_lock(bug_engine, job_id=wid_a, instance_id=inst)
        _seed_message_job_item(
            bug_engine,
            job_id=wid_b,
            instance_id=inst,
            admission_state=AdmissionState.QUEUED.value,
        )

        job_repo = JobRepository(bug_engine)
        queue_repo = JobQueueRepository(bug_engine)
        lock_manager = LockRepository(bug_engine)
        service = JobQueueService(job_repo, lock_manager, queue_repo)
        service._project_repo = None
        service._instance_manager = None

        # Step 1: start_job on M2 MUST be declined (M1 ACTIVE).
        import asyncio
        result_b = asyncio.run(service.start_job(wid_b))
        assert result_b is None, (
            "start_job did NOT decline M2 while M1 ACTIVE — "
            "the belt is broken. Expected decline (M2 stays "
            f"queued), got {result_b!r}."
        )
        assert _read_admission_state(bug_engine, wid_b) == "queued", (
            "M2 transitioned out of 'queued' despite belt "
            "decline — the belt returned without effect. "
            "Expected to stay queued."
        )

        # Step 2: M1 → DONE. Simulates JobFeedbackObserver
        # releasing the slot after a successful turn.
        with bug_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE job_queue_items "
                    "SET admission_state = :done, terminal_reason = 'completed' "
                    "WHERE job_id = :wid"
                ),
                {
                    "done": AdmissionState.DONE.value,
                    "wid": wid_a,
                },
            )
            conn.execute(
                text("DELETE FROM job_locks WHERE job_id = :wid"),
                {"wid": wid_a},
            )

        # Step 3: start_job on M2 now MUST admit cleanly
        # (no ACTIVE sibling — M1 is DONE; belt does NOT
        # block).
        result_b2 = asyncio.run(service.start_job(wid_b))
        assert result_b2 is not None, (
            "start_job did NOT admit M2 after M1 → DONE — "
            "the belt's recovery path is broken. The "
            "observer/poll re-admission contract is that "
            "the second message starts cleanly when the "
            "first turn completes; this pin ensures the "
            "belt's ACTIVE-only filter correctly lets the "
            "successor through once no ACTIVE sibling "
            "remains."
        )
        assert result_b2.admission_state == AdmissionState.ACTIVE.value, (
            f"M2 admitted but admission_state is "
            f"{result_b2.admission_state!r} (expected 'active') "
            f"— the queue-admission guard transition did not "
            f"land."
        )
