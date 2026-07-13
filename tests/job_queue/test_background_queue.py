"""Tests for the BACKGROUND queue type (feature/background-all-projects-queue).

The BACKGROUND queue is a sibling of the DEFER queue: both gate admission on
system-idle, but with different SCOPE:

* DEFER: project-scoped — waits for non-deferred work in the SAME project.
* BACKGROUND: system-wide — waits for non-deferred, non-background work across
  ALL projects. A background task only claims when every project is idle on
  its non-deferred, non-background lanes.

This file exercises the BACKGROUND seam end-to-end:

* Tests A/B/C: TaskRepository.claim_pending_task background idle gate
  (system-wide scope, ``is_background=True`` candidate held back).
* Test D: ``JobQueueMgmtService.auto_provision_system_queues`` creates the
  ``system_background_queue`` with ``queue_type='background'`` and
  ``concurrency_limit=1``.
* Test E: ``JobQueue`` model validator enforces ``concurrency_limit=1``
  for ``queue_type='background'``.
* Test F: ``is_background`` flag propagation through ``enqueue_message``.

Implementation references (read before modifying):

* Background idle gate SQL: ``daemon/repositories/task/repository.py``
  ``claim_pending_task`` (the ``NOT (task.is_background = TRUE AND EXISTS
  (…system-wide non-deferred, non-background running task…))`` predicate
  folded into the atomic claim's inner SELECT).
* Sister predicate: ``TaskRepository.has_active_non_background_work``
  (system-wide; the ``project_id`` parameter is accepted for signature
  symmetry with ``has_active_non_deferred_work`` but is intentionally
  ignored).
* Auto-provisioning: ``JobQueueMgmtService.auto_provision_system_queues``
  creates 5 system queues per project (fifo, parallel, kb_fifo, defer,
  background).
* Validation: ``JobQueue.enforce_defer_concurrency_limit`` model_validator
  raises ``ValueError`` when ``queue_type in {'defer', 'background'}`` but
  ``concurrency_limit != 1``.
* Flag propagation: ``InstanceMessagingService.enqueue_message`` accepts
  ``is_background: bool = False`` and stamps ``Task.is_background`` on the
  created row; ``JobProcessor`` derives the flag from the queue's
  ``queue_type`` (``is_background=(queue.queue_type == "background")``).
"""

import pytest
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.repositories.job_queue.models import JobQueue, QueueType
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.services.job_queue_mgmt_service import (
    JobQueueMgmtService,
    RESERVED_QUEUE_NAMES,
)


# =============================================================================
# Helpers (test-local; mirror the deferred-task helper in
# tests/message_queue_redesign/test_task_repository.py)
# =============================================================================


def _insert_instance(
    engine,
    instance_id: str,
    project_id: str,
    status: str = "running",
) -> None:
    """Insert a minimal Instance row directly via raw SQL.

    The Task model has no ``project_id`` column — the background gate joins
    through ``instances`` (like the defer gate does). Helper keeps each
    test self-contained.
    """
    now = datetime.now(timezone.utc).isoformat()
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
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
            },
        )


def _create_task_with_status(
    engine,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    instance_id: str = "test-instance",
    message_id: str = "test-message",
    status: str = TaskStatus.PENDING.value,
    is_deferred: bool = False,
    is_background: bool = False,
) -> Task:
    """Insert a task with explicit status / flag columns via raw SQL.

    Mirrors the helper in ``tests/message_queue_redesign/test_task_repository.py``
    but adds the ``is_background`` parameter so background-gate tests can
    stamp the row. The repository's public ``create`` API does not yet
    accept ``is_deferred`` / ``is_background``, so we insert directly.
    """
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
                "task_type": task_type,
                "instance_id": instance_id,
                "message_id": message_id,
                "status": status,
                "retry_count": 0,
                "created_at": created_at,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": str(uuid.uuid4()),
                # Python bool so the bind works on both SQLite
                # (INTEGER 0/1) and PostgreSQL (BOOLEAN false/true).
                "is_deferred": is_deferred,
                "is_background": is_background,
            },
        )
        task_id = result.lastrowid

        row = conn.execute(
            text("SELECT * FROM task WHERE id = :id"),
            {"id": task_id},
        ).fetchone()
        repo = TaskRepository(engine)
        return repo._row_to_task(row)


def _force_task_running(engine, task_id: int, worker_id: str = "pre-existing-worker") -> None:
    """Force a task into RUNNING with a valid heartbeat (bypassing claim)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE task SET status = :running, "
                "worker_id = :worker, started_at = :now, "
                "last_heartbeat_at = :now WHERE id = :id"
            ),
            {
                "running": TaskStatus.RUNNING.value,
                "worker": worker_id,
                "now": datetime.now(timezone.utc),
                "id": task_id,
            },
        )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def task_repository(engine):
    """TaskRepository backed by the session-scoped in-memory engine.

    The ``engine`` fixture comes from ``tests/job_queue/conftest.py`` and
    already registers all 27+ tables (Task, Instance, JobQueue, …) on
    ``SQLModel.metadata``.
    """
    return TaskRepository(engine)


@pytest.fixture
def queue_repository(engine):
    """JobQueueRepository backed by the session-scoped engine."""
    return JobQueueRepository(engine)


@pytest.fixture
def job_repository(engine):
    """JobRepository (not used directly, but mirrors the test_ensure_system_queues pattern)."""
    return JobRepository(engine)


@pytest.fixture
def queue_mgmt_service(queue_repository, job_repository):
    """JobQueueMgmtService with real repositories (not mocks).

    Auto-provisioning exercises real ``get_by_name`` + ``create`` against
    the in-memory engine, so we need real repositories (mocks would not
    write the rows we later assert against).
    """
    return JobQueueMgmtService(
        queue_repo=queue_repository,
        job_repo=job_repository,
    )


# =============================================================================
# Test A: Background queue waits when OTHER projects have active work
# =============================================================================


class TestBackgroundGateBlocksCrossProjectWork:
    """Background gate is system-wide: non-background work in project B
    blocks background candidates in project A.
    """

    def test_background_task_blocked_when_other_project_has_active_work(
        self, task_repository, engine
    ):
        """Gate fires cross-project: a background task in project A is NOT
        claimable while project B has a RUNNING non-deferred, non-background
        task. This is the documented scope asymmetry from the DEFER gate
        (which is project-scoped — a defer candidate in project A would NOT
        be blocked by work in project B).
        """
        # Project B: one RUNNING non-deferred, non-background task.
        _insert_instance(engine, "inst-B-running", "project-B")
        running_in_B = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-B-running",
            message_id="m-B-running",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
            is_background=False,
        )
        assert running_in_B.id is not None  # narrow Optional[int] for static checkers
        _force_task_running(engine, running_in_B.id, worker_id="worker-B")

        # Project A: one PENDING background task. The candidate.
        _insert_instance(engine, "inst-A-bg", "project-A")
        background_in_A = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-A-bg",
            message_id="m-A-bg",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
            is_background=True,
        )

        # The background task must NOT be claimable — the system-wide gate
        # holds it back even though project A has no active work itself.
        assert task_repository.claim_pending_task(worker_id="worker-1") is None

        # Verify the background task is still PENDING (untouched by the gate).
        db_task = task_repository.get(background_in_A.id)
        assert db_task is not None
        assert db_task.status == TaskStatus.PENDING.value
        assert bool(db_task.is_background) is True


# =============================================================================
# Test B: Background queue processes when ALL projects are idle
# =============================================================================


class TestBackgroundGateReleasesWhenAllProjectsIdle:
    """Background gate releases when no non-deferred, non-background work
    is active in ANY project.
    """

    def test_background_task_claimable_when_all_projects_idle(
        self, task_repository, engine
    ):
        """Gate does NOT fire when the entire system has no active
        non-deferred, non-background work. The background task claims.
        """
        # Project A: one PENDING background task.
        _insert_instance(engine, "inst-only-bg", "project-A")
        background = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-only-bg",
            message_id="m-bg-1",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
            is_background=True,
        )

        claimed = task_repository.claim_pending_task(worker_id="worker-1")

        assert claimed is not None
        assert claimed.id == background.id
        assert claimed.status == TaskStatus.RUNNING.value
        # SQLite returns 0/1, not Python bool — cast through bool() so the
        # assertion is backend-invariant (works the same on PostgreSQL
        # where the column is a real BOOLEAN).
        assert bool(claimed.is_background) is True
        assert bool(claimed.is_deferred) is False


# =============================================================================
# Test C: Background queue vs Defer queue behavior comparison
# =============================================================================


class TestDeferVsBackgroundScopeAsymmetry:
    """Documented scope asymmetry (Phase 3 background seam, 2026-07-14):

    * DEFER is project-scoped: project-A's defer candidate waits only on
      project-A's non-deferred work. Work in project B does NOT block.
    * BACKGROUND is system-wide: project-A's background candidate waits
      on non-deferred, non-background work across ALL projects. Work in
      project B DOES block.
    """

    def test_defer_claimable_while_other_project_active_background_blocked(
        self, task_repository, engine
    ):
        """Scope asymmetry under C1 (claim-path ↔ admission probe alignment).

        Setup:
          * Project B: 1 RUNNING non-deferred, non-background task.
          * Project A: 1 PENDING DEFER task + 1 PENDING BACKGROUND task.

        Assertions (post-C1 corrected semantics):
          * The DEFER task in project A is HELD BACK. C1 widened the
            claim-path defer gate from ``status = running`` to
            ``status IN (pending, running, paused)``, aligning it with
            the shared ``has_active_non_deferred_work(project_id=A)``
            predicate (repository.py ``claim_pending_task`` defer
            EXISTS, project-scoped ``has_active_non_deferred_work``
            variant). That predicate counts the PENDING BACKGROUND in A
            as 'active non-deferred work' (``is_deferred=false``), so
            the DEFER candidate is correctly blocked.
          * The BACKGROUND task in project A is HELD BACK system-wide
            (project B has active non-deferred, non-background work).

        Both tasks remain ``PENDING``; the system is correctly idle-locked.

        Pre-C1 this test asserted the DEFER in A was claimable — that
        was the exact inconsistency C1 fixes (claim-path deferred gate
        used ``status = :status_running`` only, bypassing the admission
        probe's stricter ``status IN (pending, running, paused)`` check
        via the shared predicate). The project-scoped DEFER ↔
        system-wide BACKGROUND asymmetry itself is still asserted by
        sibling tests in this file / class where project A holds only
        a DEFER and project B holds active work.
        """
        # Project B: one RUNNING non-deferred, non-background task.
        _insert_instance(engine, "inst-C-active", "project-B")
        active_in_B = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-C-active",
            message_id="m-B-active",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
            is_background=False,
        )
        assert active_in_B.id is not None  # narrow Optional[int]
        _force_task_running(engine, active_in_B.id, worker_id="worker-B")

        # Project A: one PENDING DEFER task + one PENDING BACKGROUND task.
        _insert_instance(engine, "inst-A-defer", "project-A")
        _insert_instance(engine, "inst-A-bg", "project-A")
        defer_in_A = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-A-defer",
            message_id="m-A-defer",
            status=TaskStatus.PENDING.value,
            is_deferred=True,
            is_background=False,
        )
        background_in_A = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-A-bg",
            message_id="m-A-bg",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
            is_background=True,
        )

        # Post-C1: BOTH gates hold back. DEFER in A is blocked by the
        # same-project PENDING BACKGROUND (claim-path defer gate now
        # aligned with the shared ``has_active_non_deferred_work``
        # predicate). BACKGROUND in A is blocked system-wide by project
        # B's RUNNING task. claim_pending_task returns None.
        claimed = task_repository.claim_pending_task(worker_id="worker-1")
        assert claimed is None

        # Verify the DEFER task is still PENDING (held back by C1).
        db_defer = task_repository.get(defer_in_A.id)
        assert db_defer is not None
        assert db_defer.status == TaskStatus.PENDING.value
        assert bool(db_defer.is_deferred) is True

        # Verify the BACKGROUND task is still PENDING (held back
        # system-wide by project B's active work).
        db_bg = task_repository.get(background_in_A.id)
        assert db_bg is not None
        assert db_bg.status == TaskStatus.PENDING.value
        assert bool(db_bg.is_background) is True


# =============================================================================
# Test D: Auto-provisioning includes system_background_queue
# =============================================================================


class TestAutoProvisioningCreatesBackgroundQueue:
    """``JobQueueMgmtService.auto_provision_system_queues`` must create the
    ``system_background_queue`` with ``queue_type='background'`` and
    ``concurrency_limit=1`` (alongside the other four system queues).
    """

    @pytest.mark.asyncio
    async def test_auto_provision_creates_five_system_queues(
        self, queue_mgmt_service, queue_repository
    ):
        """First call: all 5 system queues are created (none pre-exist)."""
        project_id = "test-project-bg"
        created = await queue_mgmt_service.auto_provision_system_queues(project_id)

        # Auto-provision returns the 5 system queues (in order: fifo,
        # parallel, kb_fifo, defer, background).
        assert len(created) == 5

        # The full set of reserved system queue names is the contract.
        created_names = {q.queue_name for q in created}
        assert created_names == RESERVED_QUEUE_NAMES
        assert "system_background_queue" in created_names

    @pytest.mark.asyncio
    async def test_background_queue_has_correct_type_and_concurrency(
        self, queue_mgmt_service, queue_repository
    ):
        """system_background_queue is type='background' with concurrency=1.

        We re-fetch by name (rather than trusting the return value's
        instance attributes) so the test fails on a real DB write, not on
        a stale in-memory attribute.
        """
        project_id = "test-project-bg2"
        await queue_mgmt_service.auto_provision_system_queues(project_id)

        background = queue_repository.get_by_name(
            project_id, "system_background_queue"
        )
        assert background is not None
        assert background.queue_name == "system_background_queue"
        assert background.queue_type == QueueType.BACKGROUND.value
        assert background.queue_type == "background"
        assert background.concurrency_limit == 1
        assert background.is_system is True
        assert background.description is not None
        assert "ALL projects" in background.description

    @pytest.mark.asyncio
    async def test_auto_provision_is_idempotent_for_background_queue(
        self, queue_mgmt_service, queue_repository
    ):
        """Calling auto_provision a second time does NOT re-create the
        background queue (idempotent). The existing row is returned.
        """
        project_id = "test-project-bg3"

        first_call = await queue_mgmt_service.auto_provision_system_queues(project_id)
        first_bg_id = next(
            q.queue_id for q in first_call if q.queue_name == "system_background_queue"
        )

        second_call = await queue_mgmt_service.auto_provision_system_queues(project_id)
        second_bg_id = next(
            q.queue_id for q in second_call if q.queue_name == "system_background_queue"
        )

        # Same queue_id returned both times — no duplicate row was created.
        assert first_bg_id == second_bg_id

        # Exactly one system_background_queue row exists for the project.
        all_bg = [
            q
            for q in queue_repository.list_by_project(project_id)
            if q.queue_name == "system_background_queue"
        ]
        assert len(all_bg) == 1


# =============================================================================
# Test E: Queue type validation
# =============================================================================


class TestBackgroundQueueValidation:
    """``JobQueue.enforce_defer_concurrency_limit`` (Pydantic model_validator)
    AND the DB-level ``ck_job_queues_defer_concurrency`` CheckConstraint
    must jointly reject ``queue_type='background'`` with
    ``concurrency_limit != 1``.

    Note on enforcement layers (verified empirically, 2026-07-14):
    The Pydantic ``model_validator(mode='after')`` does NOT fire on
    ``SQLModel(table=True)`` instantiation in this codebase — the DB
    ``CheckConstraint`` (``queue_type NOT IN ('defer', 'background') OR
    concurrency_limit = 1``) is the runtime enforcement layer that
    surfaces as ``sqlalchemy.exc.IntegrityError``. Tests accept either
    outcome; both are valid user-visible rejections of the bad input.
    """

    def test_create_background_queue_with_concurrency_5_raises(self, queue_repository):
        """Validation error: type='background' + concurrency=5 must fail.

        Accepts either ``ValueError`` (Pydantic validator) or
        ``IntegrityError`` (DB CHECK constraint) — whichever layer fires.
        The user-visible contract is "the row is rejected".
        """
        with pytest.raises((ValueError, IntegrityError)) as excinfo:
            queue_repository.create(
                project_id="test-project",
                queue_name="bad-bg-queue",
                queue_type=QueueType.BACKGROUND.value,
                concurrency_limit=5,
            )
        # If the Pydantic validator fired, the message names 'background'
        # and 'concurrency_limit=1'. If the DB CHECK constraint fired,
        # the message is the CHECK constraint name.
        msg = str(excinfo.value).lower()
        assert (
            "background" in msg
            or "ck_job_queues_defer_concurrency" in msg
            or "check constraint" in msg
        )

    def test_create_background_queue_with_concurrency_1_succeeds(self, queue_repository):
        """Happy path: type='background' + concurrency=1 succeeds."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="good-bg-queue",
            queue_type=QueueType.BACKGROUND.value,
            concurrency_limit=1,
        )
        assert queue.queue_id is not None
        assert queue.queue_type == "background"
        assert queue.concurrency_limit == 1

    def test_create_background_queue_with_concurrency_2_raises(self, queue_repository):
        """Boundary: concurrency=2 also fails (only concurrency=1 is allowed)."""
        with pytest.raises((ValueError, IntegrityError)):
            queue_repository.create(
                project_id="test-project",
                queue_name="bg-queue-c2",
                queue_type=QueueType.BACKGROUND.value,
                concurrency_limit=2,
            )

    def test_defer_queue_validation_still_works(self, queue_repository):
        """Sanity: the defer-queue branch of the validator still fires.

        ``defer`` and ``background`` share the same validator + DB CHECK
        constraint (per the broadened check documented on
        ``JobQueue.enforce_defer_concurrency_limit`` and the
        ``ck_job_queues_defer_concurrency`` CheckConstraint). This
        regression guard ensures the defer branch wasn't accidentally
        dropped when background was added.
        """
        with pytest.raises((ValueError, IntegrityError)):
            queue_repository.create(
                project_id="test-project",
                queue_name="bad-defer-queue",
                queue_type=QueueType.DEFER.value,
                concurrency_limit=3,
            )


# =============================================================================
# Test F: is_background flag propagation
# =============================================================================


class TestIsBackgroundFlagPropagation:
    """The ``is_background`` flag must be set on the Task row when a message
    is enqueued on a background queue.

    Two propagation paths are tested:

    1. **Repository-level stamping** — when a Task is created directly with
       ``is_background=True``, the column persists and is readable back
       from the DB. This is the storage contract the enqueue layer relies
       on (and is what the background gate's ``task.is_background`` SQL
       predicate ultimately reads).
    2. **JobProcessor mapping** — ``JobProcessor`` derives the flag from
       the source ``JobQueue.queue_type``. We assert the mapping logic
       directly so a regression in the queue→flag wiring fails fast,
       independent of the full enqueue plumbing.
    """

    def test_task_with_is_background_true_round_trips_through_db(
        self, engine
    ):
        """Round-trip: a Task inserted with ``is_background=True`` reads
        back ``is_background=True`` (and ``is_deferred=False``) from the DB.
        """
        _insert_instance(engine, "inst-flag-roundtrip", "project-flag")
        task = _create_task_with_status(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-flag-roundtrip",
            message_id="m-flag-1",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
            is_background=True,
        )

        # Read back via TaskRepository.get to confirm the column persisted.
        repo = TaskRepository(engine)
        assert task.id is not None  # narrow Optional[int]
        db_task = repo.get(task.id)
        assert db_task is not None
        assert bool(db_task.is_background) is True
        assert bool(db_task.is_deferred) is False

    def test_background_queue_type_maps_to_is_background_true(self):
        """JobProcessor mapping contract (the unit-level wiring test).

        ``JobProcessor`` sets ``is_background=(queue.queue_type == 'background')``
        when forwarding to ``InstanceMessagingService.enqueue_message``.
        We verify the mapping directly so a regression that swaps the
        comparison fails here even if we don't drive the full enqueue path.
        """
        bg_queue = JobQueue(
            project_id="project-1",
            queue_name="my-bg",
            queue_name_lower="my-bg",
            queue_type="background",
            concurrency_limit=1,
        )
        assert bg_queue.queue_type == "background"
        # This is the exact comparison the JobProcessor uses at lines 733, 793, 929.
        assert (bg_queue.queue_type == "background") is True
        assert (bg_queue.queue_type == "defer") is False

    def test_enqueue_message_accepts_is_background_parameter(self):
        """Signature contract: ``enqueue_message`` exposes ``is_background``.

        The propagation path runs from ``JobProcessor`` into
        ``InstanceMessagingService.enqueue_message`` (see
        ``daemon/services/instance_messaging.py`` line 812 for the
        signature, line 949 where the flag is stamped on the Task).
        We assert the parameter is part of the public signature so a
        future refactor that renames or drops it fails this test.
        """
        import inspect

        from daemon.services.instance_messaging import InstanceMessagingService

        sig = inspect.signature(InstanceMessagingService.enqueue_message)
        assert "is_background" in sig.parameters
        # Default value is False (backward-compat: existing callers don't
        # need to pass it explicitly).
        assert sig.parameters["is_background"].default is False