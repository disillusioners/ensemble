"""Seam invariant tests for the defer-queue / job-task dual work-tracking tables.

Phase 1 (defer-seam bugfix, 2026-06-30): these tests pin the invariants that
close the race conditions between ``job_queue_items`` (the JobItem/queue
side) and ``task`` (the unified WorkerPool dispatch side). Both tables
exist in the post-D13 codebase and the seam between them is the
defer-queue idle gate + the cross-system claim guard. The invariants
covered here:

* **P1 (NULL message_id guard)**: a JobItem with no ``message_id`` in its
  metadata must NOT block its own instance's task (the carve-out was
  NULL-safe'd so legacy / dispatch-only JobItems do not self-deadlock).
* **P2 (defer-queue idle gate)**: a defer queue's job is held back while
  the project has any non-deferred in-flight task, regardless of whether
  the non-deferred work has a backing JobItem ("virtual job" case).
* **stamp_message_id**: after a job is admitted to the dispatch path,
  the ``message_id`` is stamped onto the JobItem's ``metadata`` so the
  cross-system guard can correlate it to the matching Task row.
* **is_deferred wiring**: a defer-queue job spawns a Task with
  ``is_deferred=True``.
* **F4/F7 lock isolation**: a per-job lock release (e.g. from cancel) is
  scoped to the releasing job — another job's lock is NOT released.

The tests use SQLite in-memory engine via the ``engine`` fixture from
``tests/job_queue/conftest.py``. No real LLM, no daemon — pure
unit/integration tests over the repositories and a mocked JobProcessor.
"""

import json
import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import text
from sqlmodel import Session as SQLModelSession

from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobLock,
    JobQueue,
    QueueType,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.instance.models import Instance
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_processor import JobProcessor
from daemon.services.maintenance import MaintenanceService


# ─────────────────────────────────────────────────────────────────────────────
# Mock helpers (mirrors the patterns in test_defer_queue.py / test_job_processor.py)
# ─────────────────────────────────────────────────────────────────────────────


class MockProject:
    """Mock project object for JobProcessor flow."""

    def __init__(self, project_id: str, job_queue_paused: bool = False):
        self.project_id = project_id
        self.job_queue_paused = job_queue_paused


class MockQueue:
    """Mock queue object for JobProcessor flow."""

    def __init__(
        self,
        queue_id: str,
        project_id: str,
        queue_name: str = "default",
        is_paused: bool = False,
        concurrency_limit: int = 1,
        queue_type: str = "fifo",
    ):
        self.queue_id = queue_id
        self.project_id = project_id
        self.queue_name = queue_name
        self.is_paused = is_paused
        self.concurrency_limit = concurrency_limit
        self.queue_type = queue_type


class MockJob:
    """Mock JobItem for JobProcessor flow."""

    def __init__(
        self,
        job_id: str,
        agent_id: str = "developer",
        project_id: str = "project-1",
        queue_id: str = "queue-1",
        status: str = AdmissionState.QUEUED.value,
    ):
        self.job_id = job_id
        self.agent_id = agent_id
        self.project_id = project_id
        self.queue_id = queue_id
        self.status = status
        self.message = "test message"
        self.source = "api"
        self.instance_id = None


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — the conftest's `repository` is JobRepository, so we add a
# local TaskRepository fixture for the seam-invariant tests that need
# Task-side operations (claim_pending_task, has_active_non_deferred_work).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def task_repository(engine):
    """TaskRepository over the same in-memory engine as the conftest's
    JobRepository. Tests that exercise the seam need both repositories
    against the same engine so inserts in one are visible in the other.
    """
    return TaskRepository(engine)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers for raw-SQL seeding (matches the patterns in test_task_repository.py)
# ─────────────────────────────────────────────────────────────────────────────


def _insert_instance(
    engine,
    instance_id: str,
    project_id: str = "test-project",
    status: str = "running",
    agent_id: str = "developer",
) -> None:
    """Insert an Instance row directly via SQL.

    Required because the defer-queue gate resolves a task's project_id
    via ``instances.instance_id``; tests that exercise the gate need a
    matching Instance row to make the project-scoping JOIN work.
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
                "agent_id": agent_id,
                "agent_dir": f"agents/{agent_id}",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
            },
        )


def _create_task_with_status(
    engine,
    *,
    instance_id: str = "inst-x",
    message_id: str | None = None,
    status: str = TaskStatus.PENDING.value,
    is_deferred: bool = False,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
) -> int:
    """Insert a Task row directly via SQL and return its integer id.

    Mirrors the helper in test_task_repository.py — TaskRepository.create
    doesn't expose ``is_deferred`` so seam-invariant tests that need to
    seed a deferred (or running) row without going through the claim
    path use this helper. The Python bool for ``is_deferred`` keeps the
    bind working on both SQLite (INTEGER 0/1) and PostgreSQL
    (BOOLEAN false/true).
    """
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     :retry_count, :created_at, :cancel_requested,
                     :retry_scheduled, :work_id, :is_deferred)
                """
            ),
            {
                "task_type": task_type,
                "instance_id": instance_id,
                "message_id": message_id,
                "status": status,
                "retry_count": 0,
                "created_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": str(uuid.uuid4()),
                "is_deferred": is_deferred,
            },
        )
        return result.lastrowid


def _insert_job_item(
    engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str | None = None,
    admission_state: str = AdmissionState.QUEUED.value,
    job_metadata: dict | None = None,
) -> None:
    """Insert a JobItem directly via SQL.

    Used by Test 2 (stamp_message_id) and Test 3 (NULL message_id
    guard). Inserting via SQLModel would be cleaner, but going through
    raw SQL matches the ``metadata`` column naming (the DB column is
    ``metadata``, the Python attribute is ``job_metadata``) and avoids
    any ORM flush surprises around the JSON column.
    """
    now = datetime.now(timezone.utc).isoformat()
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


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Defer queue job wires is_deferred=True
# ─────────────────────────────────────────────────────────────────────────────


class TestDeferQueueJobSpawnsDeferredTask:
    """The seam wiring test: when a defer-queue job is admitted by
    ``JobProcessor._process_next_job``, ``InstanceManager.enqueue_message``
    MUST be called with ``is_deferred=True``. The downstream
    ``InstanceMessagingService._prepare_enqueued_message`` then stamps
    the resulting Task row with ``is_deferred=True`` — this test pins
    the producer-side wiring so a future refactor that drops the
    ``is_deferred=(queue.queue_type == "defer")`` mapping cannot
    silently break the defer-queue lane.
    """

    @pytest.mark.asyncio
    async def test_defer_queue_job_admits_with_is_deferred_true(
        self, engine, repository
    ):
        """When JobProcessor admits a defer-queue job, enqueue_message is
        called with is_deferred=True so the spawned Task row is stamped
        is_deferred=True.
        """
        # Arrange — mocked JobProcessor dependencies with a defer queue
        # in the loop. The queue is type=defer; the mock instance
        # manager captures the kwargs passed to enqueue_message.
        queue_repo = MagicMock()
        project = MockProject("project-defer", job_queue_paused=False)
        queue = MockQueue(
            "queue-defer-1", "project-defer",
            queue_type="defer", is_paused=False,
        )
        job = MockJob("job-defer-1", project_id="project-defer", queue_id="queue-defer-1")
        queue_repo.list_by_project.return_value = [queue]

        mock_project_repo = MagicMock()
        mock_project_repo.list_projects.return_value = [project]

        # Mock queue_service but expose the real ``stamp_message_id``
        # method (callable via the real JobRepository) so the
        # post-enqueue stamp path runs against a real engine. The other
        # repository methods (list_pending_by_queue, start_job) are
        # mocked.
        mock_queue_service = MagicMock()
        mock_queue_service._repository.list_pending_by_queue.return_value = [job]
        mock_queue_service._repository.stamp_message_id = (
            repository.stamp_message_id
        )
        # Gate A defer-idle-check falls back to the legacy
        # ``count_active_jobs_in_non_defer_queues`` when
        # ``_instance_manager._task_repo`` is a Mock. We have no other
        # work in the project, so the count must be 0 (idle) — that
        # lets the defer job proceed to the start_job + enqueue_message
        # path the test is asserting on.
        mock_queue_service._repository.count_active_jobs_in_non_defer_queues.return_value = 0

        # start_job must return a started job (with instance_id) so the
        # processor enters the enqueue_message branch.
        started_job = MagicMock()
        started_job.job_id = "job-defer-1"
        started_job.agent_id = "developer"
        started_job.message = "test message"
        started_job.source = "api"
        started_job.instance_id = "inst-defer-1"
        mock_queue_service.start_job = AsyncMock(return_value=started_job)

        mock_instance_manager = MagicMock()
        mock_instance_manager.spawn_instance_with_mcp = AsyncMock(return_value="inst-defer-1")
        # Return value with message_id so the stamp_message_id branch fires.
        async_result = MagicMock()
        async_result.message_id = "msg-defer-1"
        mock_instance_manager.enqueue_message = AsyncMock(return_value=async_result)

        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=queue_repo,
            poll_interval=0.1,
        )

        # Act
        await processor._process_next_job()

        # Assert — the seam wiring contract. is_deferred MUST equal True
        # because queue.queue_type == "defer". Without this mapping the
        # defer-queue idle gate cannot recognise the spawned Task as a
        # defer task and the seam invariant breaks.
        mock_instance_manager.enqueue_message.assert_called_once()
        call_kwargs = mock_instance_manager.enqueue_message.call_args.kwargs
        assert call_kwargs.get("is_deferred") is True, (
            f"JobProcessor must wire is_deferred=True for defer-queue jobs; "
            f"got kwargs={call_kwargs}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: stamp_message_id stamps JobItem metadata after admission
# ─────────────────────────────────────────────────────────────────────────────


class TestStampMessageIdOnJobItem:
    """After JobProcessor admits a job and mints a ``message_id`` for the
    spawned Task, it stamps that message_id back onto the JobItem's
    ``metadata`` so the cross-system guard in
    ``TaskRepository.claim_pending_task`` can correlate the active
    JobItem with the matching Task row. Without this stamp, the
    ``json_extract(metadata, '$.message_id')`` would return NULL on
    SQLite / ``metadata->>'message_id'`` would return NULL on PostgreSQL
    and the guard's NOT EXISTS carve-out would misfire.
    """

    def test_stamp_message_id_sets_metadata(self, engine, repository):
        """After ``stamp_message_id``, ``json_extract(metadata, '$.message_id')``
        returns the stamped value — the same extraction the cross-system
        guard in ``claim_pending_task`` performs.
        """
        # Arrange — insert a JobItem for an instance, no message_id yet.
        _insert_instance(engine, "inst-stamp-1")
        _insert_job_item(
            engine,
            job_id="job-stamp-1",
            instance_id="inst-stamp-1",
            project_id="test-project",
            queue_id="queue-stamp-1",
            job_metadata={},
        )

        # Act
        repository.stamp_message_id("job-stamp-1", "msg-stamped-1")

        # Assert — the metadata JSON now contains the message_id under
        # the canonical key. We extract via the same SQL fragment the
        # cross-system guard uses (``json_extract`` on SQLite, ``->>``
        # on PostgreSQL) so the test is portable across backends.
        with engine.begin() as conn:
            extracted = conn.execute(
                text(
                    "SELECT CAST(json_extract(metadata, '$.message_id') AS TEXT) "
                    "FROM job_queue_items WHERE job_id = :job_id"
                ),
                {"job_id": "job-stamp-1"},
            ).scalar()
            assert extracted == "msg-stamped-1", (
                f"stamp_message_id did not persist message_id correctly; "
                f"got {extracted!r}"
            )

    def test_stamp_message_id_overwrites_existing_value(self, engine, repository):
        """Re-stamping a job_id with a fresh message_id overwrites the
        previous value — guards against an orchestrator that re-stamps
        after a retry-with-fresh-message_id pattern.
        """
        # Arrange
        _insert_instance(engine, "inst-stamp-2")
        _insert_job_item(
            engine,
            job_id="job-stamp-2",
            instance_id="inst-stamp-2",
            job_metadata={"message_id": "old-id"},
        )

        # Act
        repository.stamp_message_id("job-stamp-2", "new-id")

        # Assert — new value overwrote the old one.
        with engine.begin() as conn:
            extracted = conn.execute(
                text(
                    "SELECT CAST(json_extract(metadata, '$.message_id') AS TEXT) "
                    "FROM job_queue_items WHERE job_id = :job_id"
                ),
                {"job_id": "job-stamp-2"},
            ).scalar()
            assert extracted == "new-id"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: NULL message_id guard — claim must NOT self-deadlock
# ─────────────────────────────────────────────────────────────────────────────


class TestNullMessageIdGuard:
    """Phase 3 P1 fix (2026-06-30): the cross-system guard in
    ``TaskRepository.claim_pending_task`` was made NULL-safe — a JobItem
    with no ``message_id`` in its metadata no longer blocks its own
    instance's task via the unified-dispatcher carve-out. The carve-out
    requires ``json_extract(metadata, '$.message_id') IS NOT NULL``
    before consulting the matching Task row, so a legacy / dispatch-only
    JobItem (no message_id) is correctly treated as a non-blocker.
    """

    def test_null_message_id_job_does_not_block_task(self, task_repository, engine):
        """A JobItem with no message_id stamped into its metadata must
        NOT block a pending Task for the same instance — the
        unified-dispatcher carve-out cannot fire because the JobItem
        has no message_id to match against.
        """
        # Arrange — Instance + JobItem (no message_id stamped) + Task.
        _insert_instance(engine, "inst-null-mid")
        _insert_job_item(
            engine,
            job_id="job-null-mid",
            instance_id="inst-null-mid",
            admission_state=AdmissionState.ACTIVE.value,
            # Empty metadata — no message_id stamp; this is the
            # dispatch-only / legacy JobItem case the NULL-safe guard
            # accommodates.
            job_metadata={},
        )
        task = _create_task_with_status(
            engine,
            instance_id="inst-null-mid",
            message_id="m-task-1",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )

        # Act + Assert — the task MUST be claimable. Before the P1 fix
        # this assertion would fail: the JobItem blocked its own
        # instance's task via the t.message_id = NULL comparison
        # resolving to UNKNOWN, so NOT EXISTS defaulted to TRUE.
        claimed = task_repository.claim_pending_task(worker_id="worker-1")
        assert claimed is not None, (
            "Task must be claimable even when a JobItem for the same "
            "instance has no message_id (NULL-safe cross-system guard)."
        )
        assert claimed.id == task


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: P2 invariant — defer job not admitted during active virtual work
# ─────────────────────────────────────────────────────────────────────────────


class TestDeferIdleGateActiveNonDeferredWork:
    """Phase 1 P2 invariant (2026-06-30): the defer-queue idle gate uses
    the shared ``TaskRepository.has_active_non_deferred_work`` predicate.
    A defer-queue JobItem must NOT be admitted while the project has any
    non-deferred PENDING/RUNNING Task, regardless of whether the
    non-deferred work has a backing JobItem ("virtual job" — the case
    where the task table is the sole dispatch primitive after D13).
    """

    def test_has_active_non_deferred_work_true_with_active_task(
        self, task_repository, engine
    ):
        """A RUNNING non-deferred Task in the project makes the defer
        gate fire — ``has_active_non_deferred_work`` returns True, so
        the defer-queue admission path would block.
        """
        # Arrange — one running non-deferred Task in the project.
        _insert_instance(engine, "inst-p2-A", project_id="project-p2")
        _create_task_with_status(
            engine,
            instance_id="inst-p2-A",
            message_id="m-running-nd",
            status=TaskStatus.RUNNING.value,
            is_deferred=False,
        )

        # Act
        result = task_repository.has_active_non_deferred_work("project-p2")

        # Assert
        assert result is True

    def test_has_active_non_deferred_work_false_when_idle(self, task_repository, engine):
        """With no non-deferred work in the project, the gate releases —
        ``has_active_non_deferred_work`` returns False, so a defer job
        would be admitted.
        """
        # Arrange — empty project (no Task rows).
        _insert_instance(engine, "inst-p2-B", project_id="project-p2b")

        # Act
        result = task_repository.has_active_non_deferred_work("project-p2b")

        # Assert
        assert result is False

    def test_has_active_non_deferred_work_excludes_deferred_tasks(
        self, task_repository, engine
    ):
        """A deferred (is_deferred=True) PENDING Task alone does NOT
        trigger the gate — only non-deferred work holds the defer
        queue back. This pins the seam invariant that ``is_deferred``
        is the project-scoping discriminator.
        """
        # Arrange — only deferred work in the project.
        _insert_instance(engine, "inst-p2-C", project_id="project-p2c")
        _create_task_with_status(
            engine,
            instance_id="inst-p2-C",
            message_id="m-deferred-only",
            status=TaskStatus.PENDING.value,
            is_deferred=True,
        )

        # Act
        result = task_repository.has_active_non_deferred_work("project-p2c")

        # Assert
        assert result is False, (
            "has_active_non_deferred_work must exclude is_deferred=True "
            "tasks — only non-deferred work blocks the defer gate."
        )

    def test_has_active_non_deferred_work_system_wide_probe(self, task_repository, engine):
        """When project_id=None (system-wide probe used by ``_is_idle``),
        ANY non-deferred Task in ANY project triggers True.
        """
        # Arrange — non-deferred Task in some project.
        _insert_instance(engine, "inst-sys-A", project_id="project-sys-A")
        _create_task_with_status(
            engine,
            instance_id="inst-sys-A",
            message_id="m-sys-A",
            status=TaskStatus.RUNNING.value,
            is_deferred=False,
        )

        # Act
        result = task_repository.has_active_non_deferred_work(None)

        # Assert
        assert result is True


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: P1 invariant — defer job completes after idle
# ─────────────────────────────────────────────────────────────────────────────


class TestDeferIdleGateReleasesAfterIdle:
    """Phase 1 P1 invariant (2026-06-30): once a project's non-deferred
    work drains, the defer-queue idle gate must release — ``has_active_
    non_deferred_work`` returns False, and a defer-queue JobItem would
    be admitted by ``_select_next_eligible_job`` / ``_defer_idle_check``.
    """

    def test_gate_releases_after_active_non_deferred_completes(
        self, task_repository, engine
    ):
        """Simulate a complete lifecycle: a running non-deferred task
        blocks the gate, then it completes, then the gate releases.
        """
        # Arrange — one running non-deferred Task.
        _insert_instance(engine, "inst-p1-A", project_id="project-p1")
        task_id = _create_task_with_status(
            engine,
            instance_id="inst-p1-A",
            message_id="m-running-nd",
            status=TaskStatus.RUNNING.value,
            is_deferred=False,
        )

        # Phase 1 — gate fires (active non-defer work present).
        assert task_repository.has_active_non_deferred_work("project-p1") is True

        # Act — complete the running task via the repository (Phase A
        # atomic completion, mirrors what a worker does on success).
        task_repository.complete_task(task_id, {"ok": True})

        # Assert — gate releases.
        assert task_repository.has_active_non_deferred_work("project-p1") is False, (
            "After the only non-deferred RUNNING task completes, the "
            "defer-queue idle gate must release."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: _is_idle returns False during active work
# ─────────────────────────────────────────────────────────────────────────────


class TestMaintenanceIsIdle:
    """``MaintenanceService._is_idle`` consults the shared
    ``TaskRepository.has_active_non_deferred_work(None)`` predicate so
    the maintenance cycle never fires while a Task is in flight. We
    test both the predicate contract directly AND the wired-up
    ``_is_idle`` method to pin the integration.
    """

    @pytest.mark.asyncio
    async def test_is_idle_false_during_active_non_deferred_task(
        self, engine, task_repository
    ):
        """``_is_idle`` returns False when a non-deferred RUNNING Task
        exists — the maintenance cycle must hold off.
        """
        # Arrange — wire MaintenanceService with the real TaskRepository
        # (no JobQueueService or RequestRegistry needed for this path
        # — the predicate fires before the other probes).
        _insert_instance(engine, "inst-mt-1", project_id="project-mt")
        _create_task_with_status(
            engine,
            instance_id="inst-mt-1",
            message_id="m-mt-running",
            status=TaskStatus.RUNNING.value,
            is_deferred=False,
        )

        service = MaintenanceService(check_interval_minutes=15)
        service.set_task_repository(task_repository)

        # Act
        idle = await service._is_idle()

        # Assert
        assert idle is False, (
            "_is_idle must return False while a non-deferred RUNNING "
            "Task exists (shared has_active_non_deferred_work predicate)."
        )

    @pytest.mark.asyncio
    async def test_is_idle_true_when_only_deferred_work_present(
        self, engine, task_repository
    ):
        """``_is_idle`` returns True when only deferred work is present
        — the gate is non-defer-invisible so deferred tasks alone do
        not block maintenance.

        Other probes (JobQueueService, RequestRegistry) are not wired
        here, but the task predicate passes (returns False → not idle)
        so we get to ``return True``.
        """
        # Arrange — only deferred work in the project.
        _insert_instance(engine, "inst-mt-2", project_id="project-mt-2")
        _create_task_with_status(
            engine,
            instance_id="inst-mt-2",
            message_id="m-mt-deferred",
            status=TaskStatus.PENDING.value,
            is_deferred=True,
        )

        service = MaintenanceService(check_interval_minutes=15)
        service.set_task_repository(task_repository)

        # Act
        idle = await service._is_idle()

        # Assert
        assert idle is True, (
            "_is_idle must return True when only deferred work is "
            "present (the defer gate does not count deferred tasks)."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: F4/F7 lock invariant — release_by_job is scoped per job
# ─────────────────────────────────────────────────────────────────────────────


class TestLockReleaseScopedPerJob:
    """Phase 1 forward-looking invariant (F4/F7, deferred to Phase 2 but
    pinned here at the unit level so the LockRepository's per-job
    release primitive is documented as scoped): calling
    ``release_by_job`` for job-B must NOT release job-A's lock. The
    release is keyed on ``(project_id, queue_id, job_id)`` — a different
    ``job_id`` does not match.
    """

    def test_release_by_job_only_releases_target_job(self, engine):
        """Two locks for two different jobs in the same queue. Releasing
        job-B leaves job-A's lock untouched.
        """
        # Arrange
        lock_repo = LockRepository(engine)
        lock_a = lock_repo.acquire(JobLock(
            project_id="proj-lk",
            queue_id="queue-lk",
            job_id="job-A",
            lock_slot=0,
        ))
        lock_b = lock_repo.acquire(JobLock(
            project_id="proj-lk",
            queue_id="queue-lk",
            job_id="job-B",
            lock_slot=1,
        ))

        # Sanity — both locks exist.
        all_locks = lock_repo.get_all_locks()
        assert {lk.lock_id for lk in all_locks} == {lock_a.lock_id, lock_b.lock_id}

        # Act — release job-B only.
        released = lock_repo.release_by_job(
            project_id="proj-lk",
            queue_id="queue-lk",
            job_id="job-B",
        )

        # Assert — release succeeded, job-A's lock survives.
        assert released is True
        remaining = {lk.lock_id for lk in lock_repo.get_all_locks()}
        assert remaining == {lock_a.lock_id}, (
            "release_by_job(job-B) must not release job-A's lock — "
            "the release is scoped per job_id."
        )

    def test_release_by_job_no_match_returns_false(self, engine):
        """``release_by_job`` for a non-existent job_id returns False
        without affecting existing locks (idempotent no-op).
        """
        # Arrange
        lock_repo = LockRepository(engine)
        lock_a = lock_repo.acquire(JobLock(
            project_id="proj-lk2",
            queue_id="queue-lk2",
            job_id="job-A",
            lock_slot=0,
        ))

        # Act
        released = lock_repo.release_by_job(
            project_id="proj-lk2",
            queue_id="queue-lk2",
            job_id="job-DOES-NOT-EXIST",
        )

        # Assert
        assert released is False
        remaining = {lk.lock_id for lk in lock_repo.get_all_locks()}
        assert remaining == {lock_a.lock_id}, (
            "release_by_job with no matching job_id must not release "
            "any lock."
        )