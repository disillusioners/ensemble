"""End-to-end integration tests for the idle-gate deadlock fix (2026-08-10).

This file complements :mod:`tests.job_queue.test_idle_gate_deadlock_fix`
(which holds 23 focused unit tests over the flag-derivation helper and
the four predicate branches). The unit tests prove each component in
isolation; this file exercises the **full real path** the fix touches:

    queue creation → task creation (with flag derived from
    ``queue.queue_type``) → JobItem creation (with the linkage
    contract ``JobItem.job_id == Task.work_id``) → idle-gate predicate.

The test setup mirrors the production ``enqueue_message_job`` flow in
:mod:`daemon.services.instance_messaging` step-by-step:

  1. Create a real ``JobQueue`` via :class:`JobQueueRepository.create` —
     the same call the production path uses.
  2. Create a real ``Instance`` via
     :class:`SQLModelInstanceRepository.create` — the
     ``instances`` row the predicate joins against.
  3. Simulate :func:`_prepare_enqueued_message` by inserting a ``Task``
     row with ``is_deferred`` / ``is_background`` derived from the
     queue's ``queue_type`` via the same helper the production code
     uses (:func:`_derive_task_flags_from_queue_type`).
  4. Insert a ``JobItem`` row with ``job_id == Task.work_id`` and
     ``queue_id`` pointing to the queue — the linkage contract
     documented in the Option B commit (``enqueue_message_job``).
  5. Call the real predicate
     :meth:`TaskRepository.has_active_non_deferred_work` /
     :meth:`TaskRepository.has_active_non_background_work` and assert
     the deadlock-fix exclusion holds.

The companion migration
``daemon/migrations/versions/20260810_000001_fix_idle_gate_stuck_task_flags.sql``
is also exercised directly to verify the backfill statement applies
cleanly on SQLite and produces the documented post-state.

Scenarios covered:

  * **Scenario 1**: defer queue → Task stamped ``is_deferred=True``,
    ``is_background=False``.
  * **Scenario 2**: background queue → Task stamped
    ``is_background=True``, ``is_deferred=False``.
  * **Scenario 3**: fifo/parallel queue → caller flags pass through
    (default both False).
  * **Scenario 4**: the full deadlock cycle — defer queue's PENDING
    task with queued JobItem must NOT count as active non-deferred
    work, while a sibling PROCESSING task DOES count.
  * **Scenario 5**: edge cases for the exclusion — no JobItem, active
    JobItem, queued JobItem, all three in isolation.
  * **Scenario 6**: data migration applies cleanly on a fresh SQLite
    DB and backfills the flag for stuck tasks.

All tests use the session-scoped in-memory SQLite engine from
``tests/job_queue/conftest.py``. No real LLM, no daemon process —
pure repository/service-level integration over the real SQL.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState, QueueType
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.instance_messaging import _derive_task_flags_from_queue_type


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (raw-SQL seeding; mirrors the helpers in
# test_idle_gate_deadlock_fix.py so the test corpus uses one set of
# primitives across files).
# ─────────────────────────────────────────────────────────────────────────────


def _insert_task(
    engine,
    *,
    work_id: str | None = None,
    instance_id: str = "test-instance",
    status: str = TaskStatus.PENDING.value,
    is_deferred: bool = False,
    is_background: bool = False,
) -> int:
    """Insert a Task row directly via raw SQL and return its primary key.

    Mirrors :func:`tests.job_queue.test_idle_gate_deadlock_fix._insert_task`
    — the production ``TaskRepository.create`` does not accept
    ``is_deferred`` / ``is_background`` so we insert the row directly
    with the flags already stamped. Returns the auto-incremented ``id``
    so the test can correlate with the JobItem row's ``work_id``.
    """
    work_id = work_id or str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task (task_type, instance_id, message_id, status,
                                  retry_count, created_at, cancel_requested,
                                  retry_scheduled, work_id, is_deferred, is_background)
                VALUES (:task_type, :instance_id, :message_id, :status,
                        :retry_count, :created_at, :cancel_requested,
                        :retry_scheduled, :work_id, :is_deferred, :is_background)
                """
            ),
            {
                "task_type": TaskType.PROCESS_MESSAGE.value,
                "instance_id": instance_id,
                "message_id": str(uuid.uuid4()),
                "status": status,
                "retry_count": 0,
                "created_at": created_at,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": work_id,
                "is_deferred": is_deferred,
                "is_background": is_background,
            },
        )
        return int(result.lastrowid)


def _insert_job_item(
    engine,
    *,
    job_id: str | None = None,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str | None = None,
    admission_state: str = AdmissionState.QUEUED.value,
    job_type: str = "message",
) -> None:
    """Insert a JobItem row directly via raw SQL.

    Mirrors the helper in :mod:`tests.job_queue.test_idle_gate_deadlock_fix`.
    ``job_id`` is the JobItem's UUID4 — it matches the corresponding
    Task's ``work_id`` (the linkage contract from
    ``enqueue_message_job``'s Option B path).
    """
    job_id = job_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    metadata_json = json.dumps({})
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
                "message": "test",
                "source": "api",
                "project_id": project_id,
                "queue_id": queue_id,
                "priority": 0,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
                "job_type": job_type,
                "retry_count": 0,
                "metadata": metadata_json,
            },
        )


def _create_real_queue(
    queue_repository: JobQueueRepository,
    project_id: str,
    queue_name: str,
    queue_type: str,
) -> str:
    """Create a real :class:`JobQueue` via the repository and return its id.

    Uses the public ``JobQueueRepository.create`` API — the same call
    the production ``enqueue_message_job`` path uses to provision a
    queue at startup. The model's ``enforce_defer_concurrency_limit``
    validator requires ``concurrency_limit=1`` for defer / background
    queues; we honour it here.
    """
    queue = queue_repository.create(
        project_id=project_id,
        queue_name=queue_name,
        queue_type=queue_type,
        concurrency_limit=1,
        is_system=False,
    )
    return queue.queue_id


def _create_real_instance(
    instance_repository: SQLModelInstanceRepository,
    instance_id: str,
    project_id: str,
    status: str = "running",
) -> None:
    """Create a real :class:`Instance` via the repository.

    Instance is the table the task-side predicate joins against
    (``task`` ↔ ``instances`` ↔ ``project_id``). The real
    ``SQLModelInstanceRepository.create`` populates the full column
    set the leader/manager expects (``agent_name``, ``created_at``,
    ``updated_at``) so production read-paths would not reject the row.
    """
    instance_repository.create(
        instance_id=instance_id,
        agent_id="developer",
        agent_dir="agents/developer",
        project_id=project_id,
        status=status,
    )


def _simulate_enqueue_message_job(
    engine,
    queue_id: str,
    queue_type: str,
    instance_id: str,
    project_id: str,
    *,
    caller_is_deferred: bool = False,
    caller_is_background: bool = False,
) -> tuple[str, int]:
    """Simulate the production ``enqueue_message_job`` Task-creation step.

    Mirrors the two key writes inside
    :func:`daemon.services.instance_messaging.enqueue_message_job`:

      1. The flag override via
         :func:`_derive_task_flags_from_queue_type` (Fix 1).
      2. The Task creation in :func:`_prepare_enqueued_message` which
         stamps ``is_deferred`` / ``is_background`` onto the
         ``task`` row.

    Returns the ``(work_id, task_id)`` pair so the caller can mint
    the linked JobItem with the matching ``job_id`` (the linkage
    contract). The JobItem creation itself is left to the caller to
    keep the simulated flow readable per scenario.
    """
    # FIX 1 simulation: derive flags from the queue's queue_type.
    is_deferred, is_background = _derive_task_flags_from_queue_type(
        queue_type,
        is_deferred=caller_is_deferred,
        is_background=caller_is_background,
    )
    work_id = str(uuid.uuid4())
    task_id = _insert_task(
        engine,
        work_id=work_id,
        instance_id=instance_id,
        status=TaskStatus.PENDING.value,
        is_deferred=is_deferred,
        is_background=is_background,
    )
    return work_id, task_id


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def task_repository(engine):
    """TaskRepository backed by the session-scoped in-memory engine."""
    return TaskRepository(engine)


@pytest.fixture
def queue_repository(engine):
    """JobQueueRepository backed by the session-scoped engine."""
    return JobQueueRepository(engine)


@pytest.fixture
def job_repository(engine):
    """JobRepository backed by the session-scoped engine."""
    return JobRepository(engine)


@pytest.fixture
def instance_repository(engine):
    """SQLModelInstanceRepository backed by the session-scoped engine."""
    return SQLModelInstanceRepository(engine)


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 1: defer queue flag propagation
# ─────────────────────────────────────────────────────────────────────────────


class TestDeferQueueFlagPropagation:
    """E2E: a Task created against a defer-typed queue carries
    ``is_deferred=True`` and ``is_background=False`` regardless of the
    caller's flags.

    This is the Fix 1 contract: the queue's ``queue_type`` is the
    single source of truth for the flag, and the production
    ``enqueue_message_job`` overrides the caller-supplied flags via
    :func:`_derive_task_flags_from_queue_type` before stamping them
    on the Task row.
    """

    def test_defer_queue_stamps_deferred_flag(
        self, engine, queue_repository, instance_repository
    ):
        """Scenario 1a: a fresh defer queue → Task is_deferred=True.

        Reproduces the production task-creation path end-to-end:
        create a real defer queue, create a real instance, run the
        ``_derive_task_flags_from_queue_type`` helper on the queue's
        ``queue_type``, and verify the resulting Task row carries
        the correct flag.
        """
        project_id = "proj-defer-1"
        instance_id = "inst-defer-1"
        _create_real_instance(
            instance_repository, instance_id, project_id, status="running"
        )
        queue_id = _create_real_queue(
            queue_repository, project_id, "defer_q_1", QueueType.DEFER.value
        )
        # Sanity: the queue is actually a defer queue.
        queue = queue_repository.get(queue_id)
        assert queue is not None
        assert queue.queue_type == QueueType.DEFER.value

        # Simulate enqueue_message_job's Task creation.
        work_id, task_id = _simulate_enqueue_message_job(
            engine,
            queue_id=queue_id,
            queue_type=queue.queue_type,
            instance_id=instance_id,
            project_id=project_id,
        )

        # Verify the Task row carries the defer flag.
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT is_deferred, is_background, work_id, status "
                    "FROM task WHERE id = :id"
                ),
                {"id": task_id},
            ).first()
        assert row is not None, "Task row not found"
        is_deferred, is_background, row_work_id, status = row
        is_deferred, is_background = bool(is_deferred), bool(is_background)
        assert is_deferred is True, (
            "defer queue must force is_deferred=True on the task"
        )
        assert is_background is False, (
            "defer queue must NOT force is_background=True "
            "(defer ≠ background)"
        )
        assert row_work_id == work_id, "Task.work_id must match the linkage handle"
        assert status == TaskStatus.PENDING.value, "Task starts as PENDING"

    def test_defer_queue_flag_unaffected_by_caller_flags(
        self, engine, queue_repository, instance_repository
    ):
        """Scenario 1b: a defer queue's flag override is INVARIANT to
        the caller's flags. Even if the caller passes ``is_background=
        True`` (a realistic mix-up), the defer queue still forces
        ``is_deferred=True`` and lets the caller's ``is_background``
        through.

        Mirrors the unit test of
        :func:`_derive_task_flags_from_queue_type` — but goes through
        the full ``task`` row so the predicate's column binding
        gets the boolean literal that satisfies the
        SQLite/PostgreSQL duality.
        """
        project_id = "proj-defer-2"
        instance_id = "inst-defer-2"
        _create_real_instance(
            instance_repository, instance_id, project_id, status="running"
        )
        queue_id = _create_real_queue(
            queue_repository, project_id, "defer_q_2", QueueType.DEFER.value
        )

        # Caller explicitly passes is_background=True (a real bug-hot
        # scenario: the caller wanted background-only but ended up on
        # a defer queue). The defer lane must still force its flag.
        work_id, task_id = _simulate_enqueue_message_job(
            engine,
            queue_id=queue_id,
            queue_type=QueueType.DEFER.value,
            instance_id=instance_id,
            project_id=project_id,
            caller_is_background=True,
        )

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT is_deferred, is_background FROM task WHERE id = :id"
                ),
                {"id": task_id},
            ).first()
        assert row is not None
        is_deferred, is_background = row
        is_deferred, is_background = bool(is_deferred), bool(is_background)
        assert is_deferred is True
        assert is_background is True, (
            "caller's is_background=True is preserved on a defer queue"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 2: background queue flag propagation
# ─────────────────────────────────────────────────────────────────────────────


class TestBackgroundQueueFlagPropagation:
    """E2E: a Task created against a background-typed queue carries
    ``is_background=True`` and ``is_deferred=False`` regardless of
    the caller's flags.
    """

    def test_background_queue_stamps_background_flag(
        self, engine, queue_repository, instance_repository
    ):
        """Scenario 2a: a fresh background queue → Task is_background=True.

        Mirrors Scenario 1 but for the background lane. The helper
        mirrors the behavior documented in
        :func:`_derive_task_flags_from_queue_type`.
        """
        project_id = "proj-bg-1"
        instance_id = "inst-bg-1"
        _create_real_instance(
            instance_repository, instance_id, project_id, status="running"
        )
        queue_id = _create_real_queue(
            queue_repository, project_id, "background_q_1", QueueType.BACKGROUND.value
        )
        queue = queue_repository.get(queue_id)
        assert queue is not None
        assert queue.queue_type == QueueType.BACKGROUND.value

        work_id, task_id = _simulate_enqueue_message_job(
            engine,
            queue_id=queue_id,
            queue_type=queue.queue_type,
            instance_id=instance_id,
            project_id=project_id,
        )

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT is_deferred, is_background FROM task WHERE id = :id"
                ),
                {"id": task_id},
            ).first()
        assert row is not None
        is_deferred, is_background = row
        is_deferred, is_background = bool(is_deferred), bool(is_background)
        assert is_background is True, (
            "background queue must force is_background=True on the task"
        )
        assert is_deferred is False, (
            "background queue must NOT force is_deferred=True "
            "(background ≠ defer)"
        )

    def test_background_queue_flag_unaffected_by_caller_flags(
        self, engine, queue_repository, instance_repository
    ):
        """Scenario 2b: background queue's flag override is INVARIANT
        to the caller's flags. Even if the caller passes
        ``is_deferred=True``, the background queue still forces
        ``is_background=True`` and lets the caller's ``is_deferred``
        through.
        """
        project_id = "proj-bg-2"
        instance_id = "inst-bg-2"
        _create_real_instance(
            instance_repository, instance_id, project_id, status="running"
        )
        queue_id = _create_real_queue(
            queue_repository, project_id, "background_q_2", QueueType.BACKGROUND.value
        )

        work_id, task_id = _simulate_enqueue_message_job(
            engine,
            queue_id=queue_id,
            queue_type=QueueType.BACKGROUND.value,
            instance_id=instance_id,
            project_id=project_id,
            caller_is_deferred=True,
        )

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT is_deferred, is_background FROM task WHERE id = :id"
                ),
                {"id": task_id},
            ).first()
        is_deferred, is_background = row
        is_deferred, is_background = bool(is_deferred), bool(is_background)
        assert is_background is True
        assert is_deferred is True, (
            "caller's is_deferred=True is preserved on a background queue"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 3: normal queue flag propagation (unchanged behavior)
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalQueueFlagPropagation:
    """E2E: a Task created against a fifo/parallel queue carries
    ``is_deferred=False, is_background=False`` (caller flags pass
    through unchanged).

    This is the "unchanged behavior" half of Fix 1 — the helper is a
    no-op for normal queues, so making sure the E2E path preserves
    the legacy semantics is part of the regression net.
    """

    @pytest.mark.parametrize("queue_type", [QueueType.FIFO.value, QueueType.PARALLEL.value])
    def test_normal_queue_passes_caller_flags_through(
        self, engine, queue_repository, instance_repository, queue_type
    ):
        """Scenario 3: fifo / parallel queue → Task flags default to
        ``(False, False)`` and the caller's flags pass through.

        The defer/background lanes are the only ones that mandate a
        flag — normal lanes just stamp whatever the caller asked for.
        """
        project_id = f"proj-normal-{queue_type}"
        instance_id = f"inst-normal-{queue_type}"
        _create_real_instance(
            instance_repository, instance_id, project_id, status="running"
        )
        queue_id = _create_real_queue(
            queue_repository, project_id, f"normal_q_{queue_type}", queue_type
        )

        # Default: no caller flags.
        _, task_id = _simulate_enqueue_message_job(
            engine,
            queue_id=queue_id,
            queue_type=queue_type,
            instance_id=instance_id,
            project_id=project_id,
        )

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT is_deferred, is_background FROM task WHERE id = :id"
                ),
                {"id": task_id},
            ).first()
        is_deferred, is_background = row
        is_deferred, is_background = bool(is_deferred), bool(is_background)
        assert is_deferred is False
        assert is_background is False


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 4: idle-gate deadlock scenario (THE KEY TEST)
# ─────────────────────────────────────────────────────────────────────────────


class TestIdleGateDeadlockScenario:
    """The full deadlock cycle reproduced end-to-end.

    A defer queue has a PENDING task whose linked JobItem is still
    ``admission_state='queued'``. Pre-fix, the defer-queue idle gate
    predicate counted this UNCLAIMABLE task as "active non-deferred
    work" and the defer queue never made progress → permanent
    deadlock.

    Post-fix, the predicate excludes PENDING tasks whose linked
    JobItem is still queued. The defer queue's idle gate sees
    "no active non-deferred work" → it admits the JobItem →
    the queue advances.

    This test reproduces the full cycle and asserts the predicate
    returns the deadlock-free value. The companion test
    ``test_running_task_drives_predicate_true`` verifies that the
    exclusion is surgical: a sibling PROCESSING task in the same
    project still drives the predicate to True.
    """

    def test_defer_queue_own_pending_task_excluded(
        self,
        engine,
        task_repository,
        queue_repository,
        instance_repository,
    ):
        """Scenario 4a: the defer queue's OWN PENDING task (with a
        queued JobItem) MUST NOT count as active non-deferred work.

        This is the deadlock case. Pre-fix, the predicate returned
        True for this row and the defer queue stayed wedged forever.
        Post-fix, the predicate returns False and the queue makes
        progress.
        """
        project_id = "proj-deadlock-1"
        instance_id = "inst-deadlock-1"
        queue_id = _create_real_queue(
            queue_repository, project_id, "defer_q_deadlock", QueueType.DEFER.value
        )
        _create_real_instance(
            instance_repository, instance_id, project_id, status="running"
        )

        # Simulate enqueue_message_job: create a Task with the
        # defer flag, then link a JobItem with the same work_id.
        work_id, _task_id = _simulate_enqueue_message_job(
            engine,
            queue_id=queue_id,
            queue_type=QueueType.DEFER.value,
            instance_id=instance_id,
            project_id=project_id,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id=instance_id,
            project_id=project_id,
            queue_id=queue_id,
            admission_state=AdmissionState.QUEUED.value,
        )

        # The defer queue's own pending task (with queued JobItem) must
        # NOT count as active non-deferred work. This is the deadlock
        # fix.
        assert task_repository.has_active_non_deferred_work(project_id) is False
        # System-wide probe also returns False.
        assert task_repository.has_active_non_deferred_work() is False

    def test_running_task_drives_predicate_true(
        self,
        engine,
        task_repository,
        queue_repository,
        instance_repository,
    ):
        """Scenario 4b: a sibling PROCESSING task in the same project
        DOES count as active non-deferred work. The exclusion is
        surgical — only the deferred-and-unclaimable case is removed.
        """
        project_id = "proj-deadlock-2"
        # 1) Defer queue's own PENDING task (deadlock case, excluded).
        queue_id = _create_real_queue(
            queue_repository, project_id, "defer_q_running", QueueType.DEFER.value
        )
        instance_id_defer = "inst-deadlock-2-defer"
        _create_real_instance(
            instance_repository, instance_id_defer, project_id, status="running"
        )
        work_id_defer, _ = _simulate_enqueue_message_job(
            engine,
            queue_id=queue_id,
            queue_type=QueueType.DEFER.value,
            instance_id=instance_id_defer,
            project_id=project_id,
        )
        _insert_job_item(
            engine,
            job_id=work_id_defer,
            instance_id=instance_id_defer,
            project_id=project_id,
            queue_id=queue_id,
            admission_state=AdmissionState.QUEUED.value,
        )

        # 2) A sibling PROCESSING task on a non-defer queue (counts).
        instance_id_running = "inst-deadlock-2-running"
        _create_real_instance(
            instance_repository, instance_id_running, project_id, status="running"
        )
        _insert_task(
            engine,
            instance_id=instance_id_running,
            status=TaskStatus.RUNNING.value,
            is_deferred=False,
        )

        # The RUNNING task alone drives the predicate True.
        assert task_repository.has_active_non_deferred_work(project_id) is True


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 5: edge cases
# ─────────────────────────────────────────────────────────────────────────────


class TestIdleGateEdgeCases:
    """Edge cases for the deadLock-fix exclusion.

    The three documented cases:

      * No JobItem at all → counts (direct-queue task is genuinely
        claimable).
      * JobItem at ``admission_state='active'`` → counts (already
        admitted, claimable).
      * JobItem at ``admission_state='queued'`` + status=PENDING →
        EXCLUDED (the deadlock fix).
    """

    def test_task_without_jobitem_counts_as_active(
        self,
        engine,
        task_repository,
        queue_repository,
        instance_repository,
    ):
        """Scenario 5a: a PENDING task with NO linked JobItem counts
        as active non-deferred work.

        Direct-queue tasks (no JobItem) are genuinely claimable — the
        exclusion must NOT extend to them. The :class:`TaskRepository`
        predicate's ``NOT EXISTS`` subquery is false for direct-queue
        tasks, so the row is correctly counted.
        """
        project_id = "proj-edge-no-ji"
        instance_id = "inst-edge-no-ji"
        _create_real_instance(
            instance_repository, instance_id, project_id, status="running"
        )
        # Plain PENDING task with no JobItem — direct-queue.
        _insert_task(
            engine,
            instance_id=instance_id,
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )

        assert task_repository.has_active_non_deferred_work(project_id) is True

    def test_task_with_active_jobitem_counts_as_active(
        self,
        engine,
        task_repository,
        queue_repository,
        instance_repository,
    ):
        """Scenario 5b: a PENDING task with a JobItem at
        ``admission_state='active'`` counts as active non-deferred work.

        The lock has been acquired and the task is next in line. The
        exclusion only targets the queued-bucket case; an active
        JobItem is geniunely claimable.
        """
        project_id = "proj-edge-active-ji"
        instance_id = "inst-edge-active-ji"
        _create_real_instance(
            instance_repository, instance_id, project_id, status="running"
        )
        queue_id = _create_real_queue(
            queue_repository, project_id, "active_q", QueueType.PARALLEL.value
        )

        work_id, _ = _simulate_enqueue_message_job(
            engine,
            queue_id=queue_id,
            queue_type=QueueType.PARALLEL.value,
            instance_id=instance_id,
            project_id=project_id,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id=instance_id,
            project_id=project_id,
            queue_id=queue_id,
            admission_state=AdmissionState.ACTIVE.value,
        )

        assert task_repository.has_active_non_deferred_work(project_id) is True

    def test_task_with_queued_jobitem_is_excluded(
        self,
        engine,
        task_repository,
        queue_repository,
        instance_repository,
    ):
        """Scenario 5c: PENDING task with a queued JobItem → EXCLUDED.

        This is the deadlock fix's talking case. The task is
        unclaimable (the queue-awareness guard in
        :meth:`TaskRepository.claim_pending_task` blocks it until the
        JobItem leaves the queued bucket), so the predicate must
        exclude it from the idle-gate count.
        """
        project_id = "proj-edge-queued-ji"
        instance_id = "inst-edge-queued-ji"
        _create_real_instance(
            instance_repository, instance_id, project_id, status="running"
        )
        queue_id = _create_real_queue(
            queue_repository, project_id, "queued_q", QueueType.PARALLEL.value
        )

        work_id, _ = _simulate_enqueue_message_job(
            engine,
            queue_id=queue_id,
            queue_type=QueueType.PARALLEL.value,
            instance_id=instance_id,
            project_id=project_id,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id=instance_id,
            project_id=project_id,
            queue_id=queue_id,
            admission_state=AdmissionState.QUEUED.value,
        )

        # The predicate must NOT count this row.
        assert task_repository.has_active_non_deferred_work(project_id) is False


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 6: data migration check
# ─────────────────────────────────────────────────────────────────────────────


class TestMigrationAppliesCleanly:
    """Verifies the backfill migration
    ``20260810_000001_fix_idle_gate_stuck_task_flags.sql`` applies
    cleanly on a fresh SQLite database and produces the documented
    post-state.

    The migration is responsible for re-stamping the flag on tasks
    whose linked JobItem sits on a defer / background queue but whose
    task flag was never set (the pre-fix bug). Production is
    PostgreSQL, but the same SQL runs on SQLite via the dual-driver
    pattern documented in the migration file's header.
    """

    MIGRATION_PATH = (
        Path(__file__).resolve().parents[2]
        / "daemon"
        / "migrations"
        / "versions"
        / "20260810_000001_fix_idle_gate_stuck_task_flags.sql"
    )

    def test_migration_file_exists(self):
        """The migration file referenced by the fix must exist."""
        assert self.MIGRATION_PATH.exists(), (
            f"Migration file not found at expected path: "
            f"{self.MIGRATION_PATH}"
        )
        content = self.MIGRATION_PATH.read_text(encoding="utf-8")
        # Sanity check: the migration contains the key backfill statements.
        assert "is_deferred" in content
        assert "is_background" in content
        assert "queue_type = 'defer'" in content
        assert "queue_type = 'background'" in content

    def test_migration_applies_on_fresh_sqlite(
        self,
        engine,
        queue_repository,
        instance_repository,
    ):
        """The migration applies cleanly on a fresh SQLite DB and
        backfills the flag for stuck tasks.

        Setup: seed the test DB with the bug-state (Task with
        ``is_deferred=False`` but JobItem on a defer queue), parse
        the migration file, apply its UP SQL, and verify the Task
        row was stamped ``is_deferred=True``.

        Uses :class:`MigrationFile.parse` so the test exercises the
        same parse path the production runner uses.
        """
        from daemon.migrations.runner import MigrationFile

        # Verify the migration file parses cleanly.
        migration = MigrationFile.parse(self.MIGRATION_PATH)
        assert migration.version == "20260810_000001"
        assert migration.up_sql  # non-empty
        assert "is_deferred" in migration.up_sql
        assert "is_background" in migration.up_sql

        # Seed the bug-state: a Task with is_deferred=False whose
        # JobItem sits on a defer queue.
        project_id = "proj-migration"
        instance_id = "inst-migration"
        queue_id = _create_real_queue(
            queue_repository, project_id, "defer_q_migration", QueueType.DEFER.value
        )
        _create_real_instance(
            instance_repository, instance_id, project_id, status="running"
        )
        work_id, _ = _simulate_enqueue_message_job(
            engine,
            queue_id=queue_id,
            queue_type=QueueType.DEFER.value,
            instance_id=instance_id,
            project_id=project_id,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id=instance_id,
            project_id=project_id,
            queue_id=queue_id,
            admission_state=AdmissionState.QUEUED.value,
        )

        # Simulate the pre-fix bug: stamp is_deferred=False on the
        # task (the legacy path before Fix 1 would have done this).
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE task SET is_deferred = FALSE WHERE work_id = :wid"),
                {"wid": work_id},
            )

        # Verify the bug-state pre-migration.
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT is_deferred FROM task WHERE work_id = :wid"
                ),
                {"wid": work_id},
            ).first()
        assert row is not None
        assert bool(row[0]) is False, (
            "Pre-migration: the bug-state is correctly seeded "
            "(is_deferred=False on a defer-queue task)"
        )

        # Apply the migration UP SQL.
        with engine.begin() as conn:
            statements = [s.strip() for s in migration.up_sql.split(";") if s.strip()]
            for stmt in statements:
                conn.execute(text(stmt))

        # Verify the post-migration state: is_deferred=True.
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT is_deferred, is_background FROM task WHERE work_id = :wid"
                ),
                {"wid": work_id},
            ).first()
        assert row is not None
        is_deferred, is_background = row
        is_deferred, is_background = bool(is_deferred), bool(is_background)
        assert is_deferred is True, (
            "Post-migration: the defer-queue task must be stamped "
            "is_deferred=True (the backfill target)"
        )
        # is_background is not affected by the defer backfill.
        assert is_background is False

    def test_migration_tolerates_idempotent_reapply(
        self,
        engine,
        queue_repository,
        instance_repository,
    ):
        """Re-applying the migration is a no-op (idempotent).

        The production runner does not guard against re-application —
        operators may re-run apply_migration on a partially-applied
        DB. The migration's WHERE clause filters on
        ``is_deferred = FALSE`` so the second apply is a no-op
        (the row is already TRUE).
        """
        from daemon.migrations.runner import MigrationFile

        migration = MigrationFile.parse(self.MIGRATION_PATH)

        # Seed a Task with is_deferred=True (already at the target
        # state — the backfill should not flip it).
        project_id = "proj-migration-idem"
        instance_id = "inst-migration-idem"
        queue_id = _create_real_queue(
            queue_repository, project_id, "defer_q_idem", QueueType.DEFER.value
        )
        _create_real_instance(
            instance_repository, instance_id, project_id, status="running"
        )
        work_id, _ = _simulate_enqueue_message_job(
            engine,
            queue_id=queue_id,
            queue_type=QueueType.DEFER.value,
            instance_id=instance_id,
            project_id=project_id,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id=instance_id,
            project_id=project_id,
            queue_id=queue_id,
            admission_state=AdmissionState.QUEUED.value,
        )

        # Apply the migration twice — first apply, then re-apply.
        for _ in range(2):
            with engine.begin() as conn:
                statements = [
                    s.strip() for s in migration.up_sql.split(";") if s.strip()
                ]
                for stmt in statements:
                    conn.execute(text(stmt))

        # The Task row remains at is_deferred=True (no flip, no flip).
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT is_deferred FROM task WHERE work_id = :wid"
                ),
                {"wid": work_id},
            ).first()
        assert row is not None
        assert bool(row[0]) is True


        # The Task row remains at is_deferred=True (no flip, no flip).
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT is_deferred FROM task WHERE work_id = :wid"
                ),
                {"wid": work_id},
            ).first()
        assert row is not None
        assert bool(row[0]) is True
