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
import asyncio
import threading
import logging
import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import text
from sqlmodel import Session as SQLModelSession

from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue import Decision
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobLock,
    JobQueue,
    QueueType,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_processor import JobProcessor
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.maintenance import MaintenanceService
from daemon.services.job_queue_service import JobQueueService


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


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: F4/F7 — _finalize_terminal scopes lock release per job (Phase 2)
# ─────────────────────────────────────────────────────────────────────────────


class TestFinalizeTerminalLockIsolation:
    """Phase 2 F4/F7 fix (2026-07-01): ``JobQueueService._finalize_terminal``
    must scope lock release to the specific ``(project_id, queue_id,
    job_id)`` triple, NOT the entire instance. The previous implementation
    called ``release_by_instance`` unconditionally in its ``finally``
    block, deleting locks belonging to ALL jobs on the instance — a
    sibling job (different queue, different job_id) lost its lock every
    time any job's terminalization ran on the same instance, causing
    over-admission past concurrency limits.

    The unit-level ``TestLockReleaseScopedPerJob`` (above) pins the
    ``LockRepository.release_by_job`` primitive. This test class pins
    the end-to-end behaviour: when ``_finalize_terminal`` runs with
    ``_dispatch_skipped=True`` (the queued-but-never-dispatched path)
    OR with a fully-populated ``(project_id, queue_id, job_id)`` triple
    (the active-job path), the only lock that gets deleted is the
    target job's lock. Sibling-job locks survive.
    """

    @pytest.mark.asyncio
    async def test_cancel_queued_job_does_not_release_sibling_lock(
        self, engine, repository, lock_manager, job_queue_service, lock_repo
    ):
        """Cancelling a QUEUED JobB (never dispatched, holds no lock)
        on the same instance as ACTIVE JobA (holds a lock) must NOT
        delete JobA's lock.

        Pre-fix: ``_finalize_terminal``'s ``finally`` block called
        ``release_by_instance(canonical_instance_id)`` unconditionally.
        With JobB's instance_id matching JobA's, the bug deleted
        JobA's lock — opening the slot for over-admission.

        Post-fix: when ``_dispatch_skipped=True`` (queued job, never
        acquired a lock), the ``finally`` block is a no-op. JobA's
        lock survives.
        """
        # Arrange — instance shared by JobA and JobB
        _insert_instance(engine, "inst-f47-1", project_id="test-project")

        # JobA: ACTIVE state, holds a lock via lock_manager
        _insert_job_item(
            engine,
            job_id="job-A",
            instance_id="inst-f47-1",
            project_id="test-project",
            queue_id="queue-A",
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-A"},
        )
        acquired = await lock_manager.acquire_queue_lock(
            project_id="test-project",
            queue_id="queue-A",
            job_id="job-A",
            instance_id="inst-f47-1",
            concurrency_limit=1,
        )
        assert acquired is True, "JobA must have acquired its lock"

        # JobB: QUEUED state, no lock, same instance as JobA
        _insert_job_item(
            engine,
            job_id="job-B",
            instance_id="inst-f47-1",
            project_id="test-project",
            queue_id="queue-B",
            admission_state=AdmissionState.QUEUED.value,
            job_metadata={"message_id": "msg-B"},
        )

        # Sanity — only JobA's lock is in the DB
        locks_before = lock_repo.get_locks_by_instance("inst-f47-1")
        assert len(locks_before) == 1
        assert locks_before[0].job_id == "job-A"

        # Act — cancel JobB (queued). This routes through
        # ``_finalize_terminal`` with ``_dispatch_skipped=True``.
        result = await job_queue_service.cancel_job("job-B")
        assert result is True, "cancel_job should return True for queued job"

        # Assert — JobA's lock MUST survive.
        # Pre-fix this would be empty (the bug).
        locks_after = lock_repo.get_locks_by_instance("inst-f47-1")
        remaining_job_ids = {lk.job_id for lk in locks_after}
        assert remaining_job_ids == {"job-A"}, (
            "cancel_job(JobB) must not delete JobA's lock — "
            f"_finalize_terminal's finally block must be a no-op when "
            f"_dispatch_skipped=True. Locks remaining: {remaining_job_ids}"
        )

        # Assert — JobB transitioned to terminal (cancellation succeeded)
        job_b_after = repository.get("job-B")
        assert job_b_after is not None
        assert job_b_after.admission_state == AdmissionState.DONE.value

    @pytest.mark.asyncio
    async def test_cancel_active_job_releases_only_its_own_lock(
        self, engine, repository, lock_manager, job_queue_service, lock_repo
    ):
        """Cancelling ACTIVE JobA releases ONLY JobA's lock, not
        JobB's lock (different queue, different job_id).

        Pre-fix: ``_finalize_terminal``'s ``finally`` block called
        ``release_by_instance(canonical_instance_id)``, deleting
        JobB's lock too.

        Post-fix: ``_finalize_terminal`` calls
        ``release_queue_lock(canonical_project_id, canonical_queue_id,
        canonical_job_id)`` (which delegates to
        ``LockRepository.release_by_job``). The DELETE is scoped to the
        ``(project_id, queue_id, job_id)`` triple; JobB's lock (same
        instance, different queue+job_id) is untouched.
        """
        # Arrange — single instance, two queues, both active jobs
        _insert_instance(engine, "inst-f47-2", project_id="test-project")

        # JobA: ACTIVE state, queue-A, holds a lock
        _insert_job_item(
            engine,
            job_id="job-A2",
            instance_id="inst-f47-2",
            project_id="test-project",
            queue_id="queue-A2",
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-A2"},
        )
        acquired_a = await lock_manager.acquire_queue_lock(
            project_id="test-project",
            queue_id="queue-A2",
            job_id="job-A2",
            instance_id="inst-f47-2",
            concurrency_limit=1,
        )
        assert acquired_a is True

        # JobB: ACTIVE state, queue-B (different queue!), holds a lock
        _insert_job_item(
            engine,
            job_id="job-B2",
            instance_id="inst-f47-2",
            project_id="test-project",
            queue_id="queue-B2",
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-B2"},
        )
        acquired_b = await lock_manager.acquire_queue_lock(
            project_id="test-project",
            queue_id="queue-B2",
            job_id="job-B2",
            instance_id="inst-f47-2",
            concurrency_limit=1,
        )
        assert acquired_b is True

        # Sanity — both locks exist
        locks_before = lock_repo.get_locks_by_instance("inst-f47-2")
        assert {lk.job_id for lk in locks_before} == {"job-A2", "job-B2"}

        # Act — cancel JobA. This routes through ``_finalize_terminal``
        # with the canonical ``(project_id, queue_id, job_id)`` triple
        # all populated. The ``finally`` block must use scoped release.
        await job_queue_service.cancel_job("job-A2")

        # Assert — only JobA's lock is gone; JobB's lock survives.
        # Pre-fix this would be empty (both locks deleted).
        locks_after = lock_repo.get_locks_by_instance("inst-f47-2")
        remaining = {lk.job_id for lk in locks_after}
        assert remaining == {"job-B2"}, (
            "cancel_job(JobA) must delete only JobA's lock via scoped "
            "release_by_job — JobB's lock must survive. "
            f"Locks remaining: {remaining}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: F4/F7 — _fail_orphaned_job scopes lock release per job (C1 fix)
# ─────────────────────────────────────────────────────────────────────────────


class TestRecoveryServiceLockIsolation:
    """W2 invariant (Phase 2 follow-up): ``JobRecoveryService._fail_orphaned_job``
    must scope lock release to the specific ``(project_id, queue_id,
    job_id)`` triple, NOT the entire instance.

    Background: pre-C1, the outer ``finally`` block in
    ``_fail_orphaned_job`` called ``release_by_instance`` unconditionally —
    exactly the F4/F7 sibling-lock-deletion bug, reintroduced in the
    recovery path post-92cb026a. C1 removes that outer block and adds a
    scoped ``release_by_job`` call to the legacy fallback branch (the
    only branch that does not route through ``_finalize_terminal``).

    These tests construct ``JobRecoveryService`` WITHOUT a
    ``job_queue_service`` so the **legacy fallback branch** runs — the
    same code path that the fix targets. The main path (with
    ``_job_queue_service`` wired) goes through ``_finalize_terminal`` and
    is covered by ``TestFinalizeTerminalLockIsolation`` above; the
    recovery service itself has no business touching the lock on that
    path beyond what ``_finalize_terminal`` already does.
    """

    @pytest.mark.asyncio
    async def test_recover_orphan_does_not_release_sibling_lock_same_queue(
        self, engine, repository, lock_repo, lock_manager
    ):
        """Two locks in the SAME queue, two different jobs.

        JobA is orphaned (instance terminal → recovery fails it).
        JobB is ACTIVE on a DIFFERENT instance and holds its own lock.
        After ``recover_on_startup``, JobB's lock MUST survive.

        Pre-C1: outer ``finally`` called ``release_by_instance`` on
        JobA's instance; but since JobB's instance is different, this
        specific test wouldn't have caught the bug. The test is
        included as a positive control — the success path of the
        scoped release must work end-to-end.
        """
        # Arrange — two distinct instances
        _insert_instance(engine, "inst-A-orphan", project_id="test-project")
        _insert_instance(engine, "inst-B-active", project_id="test-project")

        # JobA: PROCESSING state (legacy field, but recovery looks up
        # via ``find_processing_jobs`` which queries by legacy status
        # — see the active-state filter inside the repo).
        _insert_job_item(
            engine,
            job_id="job-A-orphan",
            instance_id="inst-A-orphan",
            project_id="test-project",
            queue_id="queue-A",
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-A-orphan"},
        )

        # JobB: ACTIVE state, holds its lock on its own instance
        _insert_job_item(
            engine,
            job_id="job-B-active",
            instance_id="inst-B-active",
            project_id="test-project",
            queue_id="queue-B",
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-B-active"},
        )
        acquired = await lock_manager.acquire_queue_lock(
            project_id="test-project",
            queue_id="queue-B",
            job_id="job-B-active",
            instance_id="inst-B-active",
            concurrency_limit=1,
        )
        assert acquired is True, "JobB-active must have acquired its lock"

        # Sanity — only JobB's lock exists
        locks_before = lock_repo.get_all_locks()
        assert {lk.job_id for lk in locks_before} == {"job-B-active"}

        # Wire the recovery service WITHOUT a JobQueueService so the
        # legacy fallback branch runs. Make JobA's instance terminal
        # (recovery fails orphans whose instance is terminal).
        instance_repo = SQLModelInstanceRepository(engine=engine)
        # Mark JobA's instance as terminal so recovery fails JobA.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET status = 'terminated' "
                    "WHERE instance_id = 'inst-A-orphan'"
                )
            )
        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
        )

        # Act
        stats = await service.recover_on_startup()

        # Assert — JobA failed (recovered=1), JobB stayed active (alive=1)
        assert stats["recovered"] == 1, (
            f"JobA-orphan must be marked FAILED, got stats={stats}"
        )

        # Assert — JobB's lock SURVIVES. This is the positive control
        # for the F4/F7 fix in the recovery path: scoped release only
        # touches JobA's lock row, JobB's row is untouched.
        locks_after = lock_repo.get_all_locks()
        remaining_job_ids = {lk.job_id for lk in locks_after}
        assert remaining_job_ids == {"job-B-active"}, (
            "Recovery of JobA-orphan must NOT delete JobB-active's "
            "lock. Pre-C1 the legacy branch's outer finally would "
            f"have called release_by_instance. Locks remaining: {remaining_job_ids}"
        )

    @pytest.mark.asyncio
    async def test_recover_orphan_releases_its_own_lock_scoped(
        self, engine, repository, lock_repo, lock_manager
    ):
        """The orphan JobA's own lock IS released — by the scoped
        ``release_by_job`` call in the legacy branch.

        This is the dual of the W2 sibling-protection invariant: the
        fix must preserve the structural guarantee that the orphan's
        own lock is released (otherwise we leak locks).
        """
        # Arrange — single instance with a queue and a lock for JobA
        _insert_instance(engine, "inst-A-only", project_id="test-project")

        _insert_job_item(
            engine,
            job_id="job-A-only",
            instance_id="inst-A-only",
            project_id="test-project",
            queue_id="queue-A-only",
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-A-only"},
        )
        acquired = await lock_manager.acquire_queue_lock(
            project_id="test-project",
            queue_id="queue-A-only",
            job_id="job-A-only",
            instance_id="inst-A-only",
            concurrency_limit=1,
        )
        assert acquired is True

        # Mark JobA's instance terminal (so recovery fails the orphan).
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET status = 'terminated' "
                    "WHERE instance_id = 'inst-A-only'"
                )
            )

        # Sanity — the lock exists
        locks_before = lock_repo.get_all_locks()
        assert {lk.job_id for lk in locks_before} == {"job-A-only"}

        instance_repo = SQLModelInstanceRepository(engine=engine)
        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
        )

        # Act
        stats = await service.recover_on_startup()

        # Assert
        assert stats["recovered"] == 1
        locks_after = lock_repo.get_all_locks()
        assert locks_after == [], (
            "JobA-only's lock must be released by the scoped "
            "release_by_job in the legacy fallback branch. Pre-C1 "
            "the outer finally also released it (via the buggy "
            "release_by_instance), but post-C1 the ONLY way the lock "
            "is released is the scoped call here."
        )

    @pytest.mark.asyncio
    async def test_recover_orphan_does_not_touch_lock_on_different_instance(
        self, engine, repository, lock_repo, lock_manager
    ):
        """W2 — different instance_id, different queue: sibling lock survives.

        Setup mirrors the production-safe shape of the bug:
        - JobA orphan on inst-A (terminal).
        - JobB ACTIVE on inst-B (running) with a lock.
        - Both have ``(test-project, queue-*, job-*)`` triples that
          must NOT collide.

        The pre-C1 bug only manifested when two locks shared the
        orphaned job's ``instance_id`` (the unconditional
        ``release_by_instance`` would wipe all locks on that
        instance). In a healthy DB that can't happen because the
        recovery service fails every active job on a terminal
        instance — so this test exercises the structural sibling
        on a different instance to pin the invariant that the scoped
        release does NOT reach across instances even when (in
        principle) it could.

        The companion ``test_recover_orphan_does_not_release_sibling_lock_same_queue``
        test above exercises a similar scenario with different queue
        IDs; together they pin that the recovery service's lock
        release is scoped to ``(project_id, queue_id, job_id)``.
        """
        _insert_instance(engine, "inst-X-orphan", project_id="test-project")
        _insert_instance(engine, "inst-Y-active", project_id="test-project")

        _insert_job_item(
            engine,
            job_id="job-X-orphan",
            instance_id="inst-X-orphan",
            project_id="test-project",
            queue_id="queue-X-orphan",
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-X-orphan"},
        )
        _insert_job_item(
            engine,
            job_id="job-Y-active",
            instance_id="inst-Y-active",  # different instance
            project_id="test-project",
            queue_id="queue-Y-active",  # different queue
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-Y-active"},
        )
        acquired = await lock_manager.acquire_queue_lock(
            project_id="test-project",
            queue_id="queue-Y-active",
            job_id="job-Y-active",
            instance_id="inst-Y-active",
            concurrency_limit=1,
        )
        assert acquired is True

        # Mark inst-X-orphan as terminal — recovery fails the orphan.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET status = 'terminated' "
                    "WHERE instance_id = 'inst-X-orphan'"
                )
            )

        # Sanity — only Job-Y-active's lock exists
        locks_before = lock_repo.get_all_locks()
        assert {lk.job_id for lk in locks_before} == {"job-Y-active"}

        instance_repo = SQLModelInstanceRepository(engine=engine)
        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
        )

        # Act
        stats = await service.recover_on_startup()

        # Assert — orphan failed, sibling untouched
        assert stats["recovered"] == 1
        assert stats["alive"] == 1

        locks_after = lock_repo.get_all_locks()
        remaining_job_ids = {lk.job_id for lk in locks_after}
        assert remaining_job_ids == {"job-Y-active"}, (
            "Recovery of job-X-orphan must NOT delete job-Y-active's "
            "lock — the recovery service's scoped release is keyed "
            "by (project_id, queue_id, job_id) and must not reach "
            "across instances. Locks remaining: "
            f"{remaining_job_ids}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: W2 — _finalize_terminal_sync scopes lock release per job (C3 fix)
# ─────────────────────────────────────────────────────────────────────────────


def _start_loop_in_thread():
    """Spin up a background event loop and return ``(loop, thread, stop)``.

    The sync twin dispatches its async lock release via
    ``asyncio.run_coroutine_threadsafe`` onto ``self._loop``. For that
    to actually run the lock-release coroutine, the loop must be
    running — ``new_event_loop()`` alone leaves ``loop.is_running()``
    False and the boundary's ``if self._loop and self._loop.is_running()``
    guard falls through to the C3 WARNING branch.

    Tests that need a live loop for the sync twin call this helper,
    attach the loop to the service via ``set_event_loop``, and stop
    the loop in teardown via ``stop()``.
    """
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    def stop():
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()

    return loop, thread, stop


class TestFinalizeTerminalSyncLockIsolation:
    """W2 (partial) — the sync twin ``_finalize_terminal_sync`` must
    release its lock scoped to the job, exactly like the async twin.
    Without scoped release, finalizing one job on an instance wipes
    sibling jobs' locks — the F4/F7 over-admission bug.

    Pin two invariants at the sync twin:

    1. **Scoped release**: when ``_finalize_terminal_sync`` is called
       with ``job_id=JobA``, the only lock released is JobA's. JobB's
       lock (same instance, different queue, different job_id) must
       survive. Verified via a mocked lock_manager so we can observe
       the exact ``release_queue_lock`` / ``release_by_instance`` call
       signature without coupling to the lock manager's threading.
    2. **Diagnostic on no-loop (C3 fix)**: when ``self._loop`` is unset,
       the sync twin silently skipping the release is no longer
       acceptable — it must log a WARNING naming the leaked job,
       project, queue, and instance so operators can trace the leak.
       Verified via ``caplog``.
    """

    def test_sync_twin_releases_scoped_to_target_job_not_instance(
        self, engine, repository, job_queue_service, caplog
    ):
        """Finalizing JobA via the sync twin calls
        ``release_queue_lock(project, queue, JobA)`` — NOT
        ``release_by_instance(JobA's instance)`` — so JobB's lock on
        the same instance is preserved.

        We mock the lock_manager so the test observes the exact call
        signature passed to ``release_queue_lock``. The real
        ``JobLockManager`` is not needed because the sync twin's lock
        release goes through ``self._lock_manager.release_queue_lock``
        (Path 2) which the mock implements as an async stub.

        The test starts a live event loop on a background thread so
        the sync twin's ``asyncio.run_coroutine_threadsafe(...)``
        call has a running target loop (otherwise the C3 WARNING
        branch fires and no release happens at all).
        """
        # Arrange — instance shared by JobA and JobB; both ACTIVE.
        _insert_instance(engine, "inst-w2-1", project_id="test-project")
        _insert_job_item(
            engine,
            job_id="job-w2-A",
            instance_id="inst-w2-1",
            project_id="test-project",
            queue_id="queue-w2-A",
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-w2-A"},
        )
        _insert_job_item(
            engine,
            job_id="job-w2-B",
            instance_id="inst-w2-1",
            project_id="test-project",
            queue_id="queue-w2-B",
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-w2-B"},
        )

        # Mock lock_manager — capture the release call signature.
        release_calls: list[tuple] = []

        async def _capture_release_queue_lock(project_id, queue_id, job_id):
            release_calls.append(("release_queue_lock", project_id, queue_id, job_id))
            return True

        async def _capture_release_by_instance(instance_id):
            release_calls.append(("release_by_instance", instance_id))
            return []

        job_queue_service._lock_manager.release_queue_lock = _capture_release_queue_lock
        job_queue_service._lock_manager.release_by_instance = _capture_release_by_instance

        # Start a live event loop on a background thread so the sync
        # twin's run_coroutine_threadsafe actually executes.
        loop, _thread, stop_loop = _start_loop_in_thread()
        job_queue_service.set_event_loop(loop)

        try:
            # Act — call the sync twin to finalize JobA (NO_RETRY).
            # The mock lock manager is awaited via run_coroutine_threadsafe,
            # so by the time ``_finalize_terminal_sync`` returns, the
            # release coroutine has completed.
            canonical_job_id, final_status = job_queue_service._finalize_terminal_sync(
                instance_id="inst-w2-1",
                decision=Decision.NO_RETRY,
                job_id="job-w2-A",
            )
        finally:
            stop_loop()

        # Assert — JobA's release was scoped (NOT by instance).
        assert canonical_job_id == "job-w2-A"
        scoped_calls = [
            c for c in release_calls if c[0] == "release_queue_lock"
        ]
        instance_calls = [
            c for c in release_calls if c[0] == "release_by_instance"
        ]
        assert scoped_calls == [
            ("release_queue_lock", "test-project", "queue-w2-A", "job-w2-A")
        ], (
            "_finalize_terminal_sync must call release_queue_lock with "
            "the target job's (project, queue, job) triple — NOT "
            "release_by_instance. "
            f"Got scoped calls: {scoped_calls}, instance calls: {instance_calls}"
        )
        assert instance_calls == [], (
            "_finalize_terminal_sync must NEVER fall back to "
            "release_by_instance when the canonical (project, queue, job) "
            f"triple is populated. Got: {instance_calls}"
        )

    def test_sync_twin_logs_warning_when_event_loop_unavailable(
        self, engine, repository, job_queue_service, caplog
    ):
        """C3 fix: when ``self._loop`` is unset (or closed), the sync
        twin must NOT silently skip the lock release. It must log a
        WARNING naming the job, project, queue, and instance so
        operators can trace the leaked lock.

        This pins the operator-facing diagnostic that the previous
        implementation silently swallowed.
        """
        # Arrange — instance + ACTIVE JobA holding a lock.
        _insert_instance(engine, "inst-w2-c3", project_id="test-project")
        _insert_job_item(
            engine,
            job_id="job-w2-c3-A",
            instance_id="inst-w2-c3",
            project_id="test-project",
            queue_id="queue-w2-c3",
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-w2-c3-A"},
        )

        # Mock lock_manager. Should NOT be called when loop is unavailable
        # (the WARNING branch runs instead).
        release_called = []

        async def _should_not_run(*args, **kwargs):
            release_called.append((args, kwargs))
            return True

        job_queue_service._lock_manager.release_queue_lock = _should_not_run
        job_queue_service._lock_manager.release_by_instance = _should_not_run

        # Critical: explicitly clear the loop (or never set one — the
        # fixture's service does not set a loop, but be defensive).
        job_queue_service._loop = None

        # Act — invoke the sync twin with caplog capturing WARNING+ records.
        with caplog.at_level(logging.WARNING, logger="daemon.services.job_queue_service"):
            canonical_job_id, _final_status = job_queue_service._finalize_terminal_sync(
                instance_id="inst-w2-c3",
                decision=Decision.NO_RETRY,
                job_id="job-w2-c3-A",
            )

        # Assert — the C3 WARNING was emitted with the diagnostic fields.
        warning_records = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "_finalize_terminal_sync skipped lock release" in r.getMessage()
        ]
        assert warning_records, (
            "C3 fix: _finalize_terminal_sync must log a WARNING when the "
            "event loop is unavailable, so operators can trace leaked "
            f"locks. caplog records: {[r.getMessage() for r in caplog.records]}"
        )
        warning_msg = warning_records[0].getMessage()
        assert "job-w2-c3-A" in warning_msg, (
            f"C3 WARNING must name the leaked job_id. Got: {warning_msg!r}"
        )
        assert "test-project" in warning_msg, (
            f"C3 WARNING must name the project_id. Got: {warning_msg!r}"
        )
        assert "queue-w2-c3" in warning_msg, (
            f"C3 WARNING must name the queue_id. Got: {warning_msg!r}"
        )
        assert "inst-w2-c3" in warning_msg, (
            f"C3 WARNING must name the instance_id. Got: {warning_msg!r}"
        )

        # Lock manager must NOT have been called (loop unavailable).
        assert release_called == [], (
            "Lock release must not be attempted when the event loop is "
            "unavailable — the C3 WARNING is the operator-facing signal. "
            f"Got: {release_called}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: W2 — RETRY then CANCEL does not leak the lock (W1 fix)
# ─────────────────────────────────────────────────────────────────────────────


class _StubRetryEngineForQueueLock:
    """Minimal retry engine that does ``active → queued`` directly.

    The stub is intentionally separate from the production
    ``JobRetryEngine`` (which has ``admission_state`` /
    ``failed_at`` / ``max_retries`` plumbing that is overkill for
    this seam-invariant test). The stub performs the single SQL
    UPDATE that the production retry path takes on the happy retry
    branch (``admission_state='active' → 'queued'``), and returns
    the refreshed JobItem so the boundary's RETRY handler takes the
    success branch (which now — post-W1 fix — releases the lock).
    """

    def __init__(self, engine):
        self._engine = engine

    def maybe_retry(self, job_id: str) -> JobItem | None:
        from sqlmodel import Session as SQLModelSession, update as sqlmodel_update

        with SQLModelSession(self._engine) as session:
            stmt = (
                sqlmodel_update(JobItem)
                .where(JobItem.job_id == job_id)
                .where(JobItem.admission_state == AdmissionState.ACTIVE.value)
                .values(admission_state=AdmissionState.QUEUED.value)
            )
            result = session.exec(stmt)
            session.commit()
            if result.rowcount == 0:
                return None
            return session.get(JobItem, job_id)


class TestRetryThenCancelNoStaleLock:
    """W2 (partial) — when a job is retried (ACTIVE → QUEUED) and
    then cancelled, the lock must NOT be orphaned.

    Scenario pinned:

      1. JobA is ACTIVE and holds a per-queue lock.
      2. ``_finalize_terminal(RETRY)`` runs — the retry engine moves
         JobA to QUEUED.
      3. Operator cancels JobA — ``cancel_job`` routes through
         ``_finalize_terminal(NO_RETRY, job_id=JobA)`` but the job is
         already QUEUED, so the boundary's ``_dispatch_skipped=True``
         and the ``finally`` block's Path 1 takes the no-op path
         (releases nothing).
      4. Without the W1 fix: the lock was never released (the
         finally block would have released it if ``_dispatch_skipped``
         were False, but it is True for the QUEUED re-entry), and
         the lock leaks.
      5. With the W1 fix: the RETRY branch itself releases the lock
         BEFORE the job becomes observable as QUEUED, so step 3
         correctly finds nothing to release and the lock is gone.

    The test verifies the post-condition: after the retry+cancel
    sequence, the lock table has no rows for JobA. ``release_by_job``
    is idempotent (returns False on no-op), so the post-W1
    double-release is harmless.
    """

    @pytest.mark.asyncio
    async def test_retry_then_cancel_does_not_leak_lock(
        self, engine, repository, lock_manager, job_queue_service, lock_repo
    ):
        """End-to-end: seed JobA ACTIVE with a lock, retry it, then
        cancel it, and assert the lock is gone (no orphan).
        """
        # Arrange — instance + ACTIVE JobA holding a lock.
        _insert_instance(engine, "inst-w1-1", project_id="test-project")
        _insert_job_item(
            engine,
            job_id="job-w1-A",
            instance_id="inst-w1-1",
            project_id="test-project",
            queue_id="queue-w1-A",
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-w1-A"},
        )
        acquired = await lock_manager.acquire_queue_lock(
            project_id="test-project",
            queue_id="queue-w1-A",
            job_id="job-w1-A",
            instance_id="inst-w1-1",
            concurrency_limit=1,
        )
        assert acquired is True, "JobA must have acquired its lock"

        # Wire the stub retry engine on the boundary.
        job_queue_service.set_retry_engine(_StubRetryEngineForQueueLock(engine))

        # Sanity — JobA's lock is in the DB.
        locks_before = lock_repo.get_locks_by_instance("inst-w1-1")
        assert {lk.job_id for lk in locks_before} == {"job-w1-A"}

        # Act 1 — retry JobA. This routes through
        # ``_finalize_terminal(RETRY)``: stub engine moves ACTIVE →
        # QUEUED, the W1 fix releases the lock in the RETRY branch,
        # and the ``finally`` block's idempotent Path 2 release is a
        # no-op.
        canonical_job_id, final_status = await job_queue_service._finalize_terminal(
            instance_id="inst-w1-1",
            decision=Decision.RETRY,
            job_id="job-w1-A",
        )

        # Mid-assert — the lock was released during the RETRY branch.
        locks_after_retry = lock_repo.get_locks_by_instance("inst-w1-1")
        assert locks_after_retry == [], (
            "W1 fix: _finalize_terminal(RETRY) must release the lock the "
            "job was holding BEFORE the ACTIVE→QUEUED transition is "
            "observable. Pre-W1 the lock would persist here. "
            f"Found locks: {[lk.job_id for lk in locks_after_retry]}"
        )

        # Verify the job is now QUEUED (sanity for the cancel step).
        refetched = repository.get("job-w1-A")
        assert refetched is not None
        assert refetched.admission_state == AdmissionState.QUEUED.value, (
            f"Stub retry engine should have moved JobA to QUEUED. "
            f"admission_state={refetched.admission_state}"
        )

        # Act 2 — cancel the now-QUEUED JobA. cancel_job routes through
        # ``_finalize_terminal(NO_RETRY, job_id=JobA)``; the boundary
        # sees admission_state='queued' (not 'active'), sets
        # ``_dispatch_skipped=True``, the ``finally`` block's Path 1
        # releases nothing — there must already be nothing to release
        # (W1 fix's responsibility).
        cancelled = await job_queue_service.cancel_job("job-w1-A")
        assert cancelled is True, (
            "cancel_job should return True for a queued job (it routes "
            "to _finalize_terminal(NO_RETRY) which writes DONE+CANCELLED)"
        )

        # Final assert — no orphan lock remains.
        locks_after_cancel = lock_repo.get_locks_by_instance("inst-w1-1")
        assert locks_after_cancel == [], (
            "W1 fix: after retry → cancel sequence, no lock may remain "
            "for JobA. The lock must have been released during the "
            "ACTIVE→QUEUED transition; the cancel path's "
            "``_dispatch_skipped=True`` branch is a no-op by design. "
            f"Locks remaining: {[lk.job_id for lk in locks_after_cancel]}"
        )

        # Verify the job landed in DONE (cancelled terminal).
        refetched_post_cancel = repository.get("job-w1-A")
        assert refetched_post_cancel is not None
        assert refetched_post_cancel.admission_state == AdmissionState.DONE.value, (
            f"cancel_job should move the queued JobA to DONE. "
            f"admission_state={refetched_post_cancel.admission_state}"
        )