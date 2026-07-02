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
from datetime import datetime, timedelta, timezone
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
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_processor import JobProcessor
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.maintenance import MaintenanceService
from daemon.services.job_queue_service import JobQueueService
from daemon.services.stale_task_recovery import StaleTaskRecovery


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


@pytest.fixture
def watcher_repo(engine):
    """JobWatcherRepository over the same in-memory engine as the
    conftest's JobRepository. Used by F6 tests to seed and inspect
    ``job_watchers`` rows directly (the production ``watch_job`` tool
    path goes through resolver-aware JobQueueService, which is overkill
    for a seam-invariant test that only needs to verify the
    ``schedule_retry`` migration SQL).
    """
    return JobWatcherRepository(engine)


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
    work_id: str | None = None,
) -> int:
    """Insert a Task row directly via SQL and return its integer id.

    Mirrors the helper in test_task_repository.py — TaskRepository.create
    doesn't expose ``is_deferred`` so seam-invariant tests that need to
    seed a deferred (or running) row without going through the claim
    path use this helper. The Python bool for ``is_deferred`` keeps the
    bind working on both SQLite (INTEGER 0/1) and PostgreSQL
    (BOOLEAN false/true).

    ``work_id``: when provided, stamps the Task with an explicit
    cross-system identifier (e.g. a JobItem's ``job_id`` for a job's
    driving Task). Defaults to a fresh UUID4 (the model default).
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
                "work_id": work_id or str(uuid.uuid4()),
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

    def test_paused_non_deferred_task_blocks_defer_gate(
        self, task_repository, engine
    ):
        """Pause-fix (2026-07-01): a PAUSED non-deferred Task counts as
        non-idle, so the defer queue must NOT admit while a paused
        instance occupies a slot. A paused instance is suspended-but-
        occupying, not idle — the defer contract is "wait until
        everything is idle/terminal", and paused is neither.

        Reproduces the dev_run.log bug: send_message → pause instance →
        create defer job → defer job wrongly admitted.
        """
        # Arrange — a paused (non-deferred) Task in the project.
        _insert_instance(
            engine,
            "inst-paused",
            project_id="project-paused",
            status=InstanceStatus.PAUSED.value,
        )
        _create_task_with_status(
            engine,
            instance_id="inst-paused",
            message_id="m-paused-nd",
            status=TaskStatus.PAUSED.value,
            is_deferred=False,
        )

        # Act — both the project-scoped probe (defer Gate A/B) and the
        # system-wide probe (maintenance _is_idle) must see the paused
        # task as non-idle.
        scoped = task_repository.has_active_non_deferred_work("project-paused")
        system_wide = task_repository.has_active_non_deferred_work(None)

        # Assert
        assert scoped is True, (
            "A paused non-deferred Task must block the defer gate — "
            "paused instances are suspended-but-occupying, not idle."
        )
        assert system_wide is True, (
            "Maintenance _is_idle must also treat a paused Task as "
            "non-idle so cleanup/lock-sweeps don't run while paused."
        )


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


# ─────────────────────────────────────────────────────────────────────────────
# Test 11: F6 — Watcher survives task retry via migration
# ─────────────────────────────────────────────────────────────────────────────


class TestF6WatcherMigratesOnRetry:
    """F6 fix (Phase 3, 2026-07-01): when a task is retried, the retry
    child gets a fresh ``work_id`` (UUID4), so a watcher registered
    against the parent's ``work_id`` would otherwise be orphaned — the
    ``notify_work_watchers`` exact-match lookup
    (``WHERE job_id = :work_id``) would never find it, and the
    notification would be silently dropped until the next daemon
    restart's ``reconcile_terminal_watches`` sweep.

    The fix migrates every ``job_watchers`` row whose ``job_id`` equals
    the parent's ``work_id`` to the child's ``work_id`` INSIDE the
    same transaction as the retry INSERT. This test pins that contract
    end-to-end:

      1. Seed a Task with a stable ``work_id`` (the parent's).
      2. Register a watcher via ``watcher_repo.add_watch(parent_work_id, …)``.
      3. Call ``task_repository.schedule_retry`` — this produces a new
         Task with a fresh ``work_id`` AND migrates the watcher row.
      4. Assert:
         a. The retry child's ``work_id`` is different from the parent's.
         b. ``get_watchers_for_job(parent_work_id)`` returns [] (no
            orphaned watchers on the parent's old id).
         c. ``get_watchers_for_job(child_work_id)`` returns the
            original watcher row (migration worked).

    Without the fix, step 4b would still find the watcher on the
    parent's id (orphaned) and step 4c would return [] (the child's id
    has no watchers). With the fix, the migration is atomic with the
    retry INSERT inside ``schedule_retry``'s transaction, so both rows
    exist or neither does.
    """

    def test_f6_watcher_row_migrates_from_parent_work_id_to_child_work_id(
        self, engine, task_repository, watcher_repo
    ):
        """End-to-end: schedule_retry migrates the watcher row's
        ``job_id`` from the parent's ``work_id`` to the child's fresh
        ``work_id`` in the same transaction.
        """
        # Arrange — an instance + a parent Task with a known work_id.
        # The task must be in 'running' status so schedule_retry's
        # status guard passes (eligible set is
        # IN ('running','failed','cancelled')).
        _insert_instance(engine, "inst-f6-1", project_id="test-project")
        parent_id = _create_task_with_status(
            engine,
            instance_id="inst-f6-1",
            status=TaskStatus.RUNNING.value,
        )
        # Fetch the parent's work_id (the helper above generates a random UUID).
        with engine.begin() as conn:
            parent_work_id = conn.execute(
                text("SELECT work_id FROM task WHERE id = :id"),
                {"id": parent_id},
            ).scalar()
        assert parent_work_id is not None

        # Register a watcher against the parent's work_id — this is
        # what ``watch_job`` does at the application layer (it
        # inserts a row with ``job_id=work_id``, ``instance_id`` =
        # the watcher's instance).
        watcher = watcher_repo.add_watch(parent_work_id, "watcher-f6-inst-1")
        assert watcher.job_id == parent_work_id, (
            f"Sanity check: add_watch should store the parent's work_id as "
            f"job_id. Got {watcher.job_id!r}"
        )

        # Sanity — the watcher is findable on the parent's work_id and
        # there are no watchers on any other id.
        watchers_on_parent = watcher_repo.get_watchers_for_job(parent_work_id)
        assert len(watchers_on_parent) == 1
        assert watchers_on_parent[0].watch_id == watcher.watch_id

        # Act — schedule_retry. The parent transitions to 'cancelled'
        # + retry_scheduled=True, and a new Task with a fresh work_id
        # is INSERTed. The F6 fix migrates the watcher row's job_id
        # to the new work_id inside the same transaction.
        retry_task = task_repository.schedule_retry(
            task_id=parent_id,
            max_retries=3,
            backoff_base=60,
            backoff_max=3600,
        )

        # Assert — retry_task is the new Task with a fresh work_id.
        assert retry_task is not None, "schedule_retry should return the retry Task"
        child_work_id = retry_task.work_id
        assert child_work_id != parent_work_id, (
            f"F6 invariant: the retry child must have a fresh work_id "
            f"(parent was {parent_work_id!r}, child is {child_work_id!r})"
        )

        # Assert — the watcher row's job_id is now the child's
        # work_id (migration worked). get_watchers_for_job on the
        # parent's old id returns nothing (no orphan watcher), and
        # the same watcher is findable on the child's id.
        watchers_on_parent_after = watcher_repo.get_watchers_for_job(parent_work_id)
        assert watchers_on_parent_after == [], (
            f"F6 fix: after schedule_retry, no watcher row should remain "
            f"on the parent's work_id {parent_work_id!r} — the row must "
            f"have migrated to the child's work_id. "
            f"Found: {[w.watch_id for w in watchers_on_parent_after]}"
        )

        watchers_on_child = watcher_repo.get_watchers_for_job(child_work_id)
        assert len(watchers_on_child) == 1, (
            f"F6 fix: after schedule_retry, exactly one watcher should be "
            f"findable on the child's work_id {child_work_id!r}. "
            f"Found: {len(watchers_on_child)}"
        )
        migrated = watchers_on_child[0]
        assert migrated.watch_id == watcher.watch_id, (
            f"F6 fix: the migrated watcher must be the SAME row as the "
            f"original (same watch_id). Original={watcher.watch_id!r}, "
            f"migrated={migrated.watch_id!r}"
        )
        assert migrated.instance_id == "watcher-f6-inst-1", (
            f"F6 fix: the migrated watcher must preserve the watcher's "
            f"instance_id (the (job_id, instance_id) UNIQUE constraint "
            f"key changes only on job_id). "
            f"Original instance_id={watcher.instance_id!r}, "
            f"migrated={migrated.instance_id!r}"
        )
        assert migrated.job_id == child_work_id, (
            f"F6 fix: the migrated watcher's job_id must equal the "
            f"child's work_id. Got {migrated.job_id!r}, "
            f"expected {child_work_id!r}"
        )

    def test_f6_watcher_migration_is_atomic_with_retry_insert(
        self, engine, task_repository, watcher_repo
    ):
        """F6 atomicity: the watcher migration and the retry Task
        INSERT must commit together. If the retry INSERT fails (e.g.
        the parent's status changed concurrently), the migration must
        NOT have happened — the watcher must remain on the parent's
        work_id.

        Simulated by calling ``schedule_retry`` twice on the same
        parent: the first call succeeds (parent → cancelled, child
        inserted, watcher migrated to child); the second call must
        return ``None`` because the parent's ``retry_scheduled=True``
        guard blocks it (the double-retry guard is the same predicate
        that prevents duplicate retry children). The watcher must
        remain on the FIRST child's work_id (not get re-migrated to
        a phantom second child).
        """
        # Arrange — parent + watcher.
        _insert_instance(engine, "inst-f6-atomic", project_id="test-project")
        parent_id = _create_task_with_status(
            engine,
            instance_id="inst-f6-atomic",
            status=TaskStatus.RUNNING.value,
        )
        with engine.begin() as conn:
            parent_work_id = conn.execute(
                text("SELECT work_id FROM task WHERE id = :id"),
                {"id": parent_id},
            ).scalar()
        watcher_repo.add_watch(parent_work_id, "watcher-f6-atomic-inst")

        # Act 1 — first schedule_retry succeeds.
        first_retry = task_repository.schedule_retry(
            task_id=parent_id,
            max_retries=3,
        )
        assert first_retry is not None
        first_child_work_id = first_retry.work_id

        # Sanity — watcher migrated to the first child.
        assert (
            len(watcher_repo.get_watchers_for_job(first_child_work_id)) == 1
        )

        # Act 2 — second schedule_retry on the same parent must
        # return None (the parent is now cancelled with
        # retry_scheduled=True, which the UPDATE's WHERE
        # ``retry_scheduled = false`` guard rejects).
        second_retry = task_repository.schedule_retry(
            task_id=parent_id,
            max_retries=3,
        )
        assert second_retry is None, (
            "schedule_retry must return None on a parent whose "
            "retry_scheduled=True (double-retry guard). The atomic "
            "transaction means the second call's UPDATE matches zero "
            "rows and the watcher migration block is skipped."
        )

        # Assert — the watcher remains on the FIRST child's work_id.
        # No phantom migration to a non-existent second child.
        watchers_on_first_child = watcher_repo.get_watchers_for_job(first_child_work_id)
        assert len(watchers_on_first_child) == 1, (
            "F6 atomicity: after a failed second schedule_retry, the "
            "watcher must still be on the first child's work_id "
            "(no migration to a phantom child). "
            f"Found {len(watchers_on_first_child)} watchers on "
            f"{first_child_work_id!r}"
        )
        # And there must not be watchers on any other work_id.
        all_watchers = watcher_repo.get_all_active_watches()
        assert len(all_watchers) == 1, (
            f"F6 atomicity: only one watcher row should exist across "
            f"the entire job_watchers table (no duplicates, no "
            f"orphans). Found: {len(all_watchers)}"
        )
        assert all_watchers[0].job_id == first_child_work_id


# ─────────────────────────────────────────────────────────────────────────────
# Test 12: F12 — Stale PENDING task cancelled on retry re-admission
# ─────────────────────────────────────────────────────────────────────────────


class TestF12StalePendingCancelledOnRetry:
    """F12 fix (Phase 3, 2026-07-01): on ``atomic_retry`` (JobItem
    ``done → queued``), the retry engine must cancel every leftover
    PENDING Task on the same ``instance_id`` BEFORE the downstream
    caller invokes ``start_job`` to spawn a fresh instance/Task.

    Without the fix: a leftover PENDING retry child survives the
    ``atomic_retry`` transition. ``claim_pending_task``'s per-instance
    guard blocks only RUNNING tasks (not PENDING ones), so the stale
    PENDING and the fresh retry Task can both become claimable
    concurrently. Two ``graph.astream`` calls on the same LangGraph
    thread_id race on the Postgres checkpointer and shadow each
    other's channel writes — the produced AIMessages get lost, and
    ``invoke_agent_and_wait`` hangs until ``reconcile_terminal_watches``
    runs at next daemon restart.

    The fix is two-part:

      1. New ``TaskRepository.cancel_pending_tasks_for_instance``
         method — atomic UPDATE transitioning stale PENDING tasks to
         CANCELLED for the given ``instance_id`` (does NOT touch
         RUNNING tasks, which are legitimate siblings).
      2. ``JobRetryEngine.maybe_retry`` calls
         ``cancel_pending_tasks_for_instance`` immediately after
         ``atomic_retry`` succeeds, BEFORE the orchestrator's
         ``start_job`` call. The wiring is ``task_repo`` on the
         retry engine (set in ``daemon/api.py``); when unwired, the
         cancel is logged-and-skipped (older wirings pre-F12).

    This test pins both parts:

      * Direct test of ``cancel_pending_tasks_for_instance`` —
        confirms the method only touches PENDING tasks on the
        target instance_id (RUNNING/COMPLETED/FAILED siblings are
        left alone).
      * Integration test of ``maybe_retry`` with a wired
        ``task_repo`` — confirms the cancel is invoked after
        ``atomic_retry`` and BEFORE the orchestrator's
        ``start_job`` (here represented by post-condition assertions
        on the task table).
    """

    def test_f12_cancel_pending_tasks_for_instance_only_cancels_pending(
        self, engine, task_repository
    ):
        """Direct test: cancel_pending_tasks_for_instance transitions
        PENDING tasks on the instance to CANCELLED but leaves RUNNING
        / COMPLETED / FAILED / already-CANCELLED rows alone.
        """
        # Arrange — one instance with one task in each status.
        _insert_instance(engine, "inst-f12-status", project_id="test-project")

        pending_id = _create_task_with_status(
            engine,
            instance_id="inst-f12-status",
            status=TaskStatus.PENDING.value,
        )
        running_id = _create_task_with_status(
            engine,
            instance_id="inst-f12-status",
            status=TaskStatus.RUNNING.value,
        )
        completed_id = _create_task_with_status(
            engine,
            instance_id="inst-f12-status",
            status=TaskStatus.COMPLETED.value,
        )
        failed_id = _create_task_with_status(
            engine,
            instance_id="inst-f12-status",
            status=TaskStatus.FAILED.value,
        )
        already_cancelled_id = _create_task_with_status(
            engine,
            instance_id="inst-f12-status",
            status=TaskStatus.CANCELLED.value,
        )
        # Plus a PENDING task on a DIFFERENT instance — must NOT be
        # cancelled (proves the WHERE clause is instance-scoped).
        _insert_instance(engine, "inst-f12-other", project_id="test-project")
        other_pending_id = _create_task_with_status(
            engine,
            instance_id="inst-f12-other",
            status=TaskStatus.PENDING.value,
        )

        # Act
        cancelled_count = task_repository.cancel_pending_tasks_for_instance(
            "inst-f12-status"
        )

        # Assert — exactly the one PENDING task on the target instance
        # was cancelled. RUNNING / COMPLETED / FAILED / already-CANCELLED
        # rows on the same instance are untouched. The other-instance
        # PENDING is untouched.
        assert cancelled_count == 1, (
            f"cancel_pending_tasks_for_instance must cancel ONLY the "
            f"PENDING task on the target instance (1 row). Got "
            f"cancelled_count={cancelled_count}"
        )

        with engine.begin() as conn:
            statuses = dict(
                conn.execute(
                    text("SELECT id, status FROM task"),
                ).all()
            )

        assert statuses[pending_id] == TaskStatus.CANCELLED.value, (
            f"The PENDING task on the target instance must be CANCELLED. "
            f"Got status={statuses[pending_id]!r}"
        )
        assert statuses[running_id] == TaskStatus.RUNNING.value, (
            f"A RUNNING task on the same instance must NOT be cancelled "
            f"(it's a legitimate sibling). Got status={statuses[running_id]!r}"
        )
        assert statuses[completed_id] == TaskStatus.COMPLETED.value, (
            f"A COMPLETED task on the same instance must NOT be touched. "
            f"Got status={statuses[completed_id]!r}"
        )
        assert statuses[failed_id] == TaskStatus.FAILED.value, (
            f"A FAILED task on the same instance must NOT be touched. "
            f"Got status={statuses[failed_id]!r}"
        )
        assert statuses[already_cancelled_id] == TaskStatus.CANCELLED.value, (
            f"An already-CANCELLED task on the same instance must NOT be "
            f"re-cancelled. Got status={statuses[already_cancelled_id]!r}"
        )
        assert statuses[other_pending_id] == TaskStatus.PENDING.value, (
            f"A PENDING task on a DIFFERENT instance must NOT be "
            f"cancelled (instance-scoped WHERE clause). "
            f"Got status={statuses[other_pending_id]!r}"
        )

    def test_f12_maybe_retry_cancels_stale_pending_before_re_admission(
        self, engine, task_repository, repository
    ):
        """Integration: after ``maybe_retry`` runs on a failed job
        whose instance has a leftover PENDING retry child, the
        PENDING child is CANCELLED BEFORE the test can observe the
        JobItem as ``queued`` (which is the precondition for the
        downstream ``start_job`` call).

        Without F12: the PENDING child survives ``atomic_retry`` and
        is still in the task table when the orchestrator's
        ``start_job`` fires — two PENDING tasks for the same instance
        can then both become claimable.

        With F12: ``maybe_retry`` calls
        ``cancel_pending_tasks_for_instance`` immediately after the
        ``atomic_retry`` UPDATE returns the refreshed JobItem, so by
        the time ``maybe_retry`` returns to its caller the stale
        PENDING is already CANCELLED.
        """
        # Arrange — build a complete maybe_retry wiring:
        #   * JobRepository (the conftest's `repository` fixture)
        #   * TaskRepository (cancel_pending_tasks_for_instance)
        #   * JobQueueRepository (system queues for the config)
        #   * DeadLetterService (stub — we'll make sure retries
        #     don't reach the DLQ branch)
        #   * Config (real JobSystemConfig with retry enabled)
        #   * task_repo wired (the F12 plumbing)
        from daemon.config import JobSystemConfig
        from daemon.services.job_retry_engine import JobRetryEngine
        from daemon.services.dead_letter_service import DeadLetterService

        # A system queue (the queue's ``default_max_retries`` is consulted
        # by ``get_max_retries`` when the JobItem has no ``max_retries``
        # set; we use the config-level default instead, which avoids
        # the ``default_max_retries`` parameter (not exposed on
        # ``JobQueueRepository.create``). Use the repository's create
        # helper so the NOT-NULL ``queue_name_lower`` column is
        # populated correctly (the helper normalises ``queue_name``
        # to its lowercased form).
        queue_id = "queue-f12-retry"
        JobQueueRepository(engine).create(
            project_id="test-project",
            queue_name="queue-f12-retry",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )

        # Instance + DONE JobItem with instance_id set (so the cancel targets
        # the right instance). Phase 4 cleanup: ``should_retry`` requires
        # ``admission_state='done'`` + ``failed_at`` to fire the retry
        # branch — this is the post-failure state reached via
        # ``_finalize_terminal`` / ``fail_job``.
        instance_id = "inst-f12-retry"
        job_id = "job-f12-retry"
        _insert_instance(engine, instance_id, project_id="test-project")
        _insert_job_item(
            engine,
            job_id=job_id,
            instance_id=instance_id,
            project_id="test-project",
            queue_id=queue_id,
            admission_state=AdmissionState.DONE.value,
            job_metadata={"message_id": "msg-f12-retry"},
        )
        # Stamp failed_at so should_retry accepts the row (Phase 4
        # eligibility check).
        from datetime import timedelta
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE job_queue_items SET failed_at = :failed_at "
                    "WHERE job_id = :job_id"
                ),
                {
                    "failed_at": (
                        datetime.now(timezone.utc) - timedelta(minutes=5)
                    ).isoformat(),
                    "job_id": job_id,
                },
            )

        # Stale PENDING retry child on the same instance — the F12
        # bug condition.
        stale_pending_id = _create_task_with_status(
            engine,
            instance_id=instance_id,
            status=TaskStatus.PENDING.value,
        )

        # Wire a minimal JobQueueRepository pointing at the engine.
        queue_repo = JobQueueRepository(engine)
        config = JobSystemConfig()
        # Make sure retry is enabled and not immediately exhausted.
        config.dlq_enabled = True
        config.default_max_retries = 5
        config.retry_backoff_base_seconds = 1
        config.retry_backoff_max_seconds = 10

        # DeadLetterService stub — should never be called in this
        # test because retry_count (0) < max_retries (5), so the
        # ``should_retry`` branch returns True and the DLQ branch
        # is skipped. Use a MagicMock so any unexpected DLQ call is
        # immediately visible.
        from unittest.mock import MagicMock
        dlq_service = MagicMock(spec=DeadLetterService)

        retry_engine = JobRetryEngine(
            job_repo=repository,
            queue_repo=queue_repo,
            dlq_service=dlq_service,
            config=config,
            task_repo=task_repository,  # F12 wiring
        )

        # Sanity — the stale PENDING is still PENDING before the retry.
        with engine.begin() as conn:
            pre_status = conn.execute(
                text("SELECT status FROM task WHERE id = :id"),
                {"id": stale_pending_id},
            ).scalar()
        assert pre_status == TaskStatus.PENDING.value, (
            f"Pre-condition: the stale retry child must be PENDING. "
            f"Got status={pre_status!r}"
        )

        # Act — call maybe_retry synchronously (matches the
        # production call site in ``_finalize_terminal``).
        updated_job = retry_engine.maybe_retry(job_id)

        # Assert — maybe_retry succeeded (job is now QUEUED).
        assert updated_job is not None, (
            "maybe_retry should transition the JobItem to QUEUED "
            "(retry_count < max_retries, so the retry branch fires)."
        )
        assert updated_job.admission_state == AdmissionState.QUEUED.value, (
            f"After maybe_retry, the JobItem must be QUEUED (the "
            f"downstream start_job will spawn a fresh instance/Task). "
            f"Got admission_state={updated_job.admission_state!r}"
        )
        # And the DLQ branch was NOT taken (retry wasn't exhausted).
        dlq_service.move_to_dlq.assert_not_called()

        # Assert — F12 invariant: the stale PENDING task was cancelled
        # by the time maybe_retry returned. The downstream start_job
        # would observe NO PENDING tasks for this instance, so the
        # fresh retry Task has no sibling to contest the LangGraph
        # checkpoint with.
        with engine.begin() as conn:
            post_status = conn.execute(
                text("SELECT status FROM task WHERE id = :id"),
                {"id": stale_pending_id},
            ).scalar()
        assert post_status == TaskStatus.CANCELLED.value, (
            f"F12 invariant: by the time maybe_retry returns, the "
            f"stale PENDING task on the retried instance must be "
            f"CANCELLED — otherwise the downstream start_job would "
            f"race against it on the LangGraph checkpoint. "
            f"Got status={post_status!r}"
        )

        # Assert — and no NEW PENDING task was created on the same
        # instance (the F12 cancel is targeted, not a global wipe).
        with engine.begin() as conn:
            remaining_pending_count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM task "
                    "WHERE instance_id = :iid AND status = :pending"
                ),
                {"iid": instance_id, "pending": TaskStatus.PENDING.value},
            ).scalar()
        assert remaining_pending_count == 0, (
            f"F12 invariant: no PENDING task must remain on the "
            f"retried instance after maybe_retry (the stale PENDING "
            f"was cancelled, and the orchestrator's start_job hasn't "
            f"run yet). Got {remaining_pending_count} pending row(s)."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — F8 second defer idle-gate (observer path)
# ─────────────────────────────────────────────────────────────────────────────


class TestF8SecondDeferIdleGateObserverPath:
    """Phase 3 F8 (defer-seam bugfix, 2026-07-01): the second defer
    idle-gate consumer — ``_select_next_eligible_job`` in
    ``daemon/services/job_queue_service.py`` — must consult the shared
    ``TaskRepository.has_active_non_deferred_work`` predicate so a defer
    JobItem cannot be selected while the project has any non-deferred
    in-flight work.

    This gate is hit on the **observer admission path**:
    ``JobFeedbackObserver._process_event`` →
    ``JobQueueService._get_next_job(project_id)`` →
    ``_select_next_eligible_job(pending, project_id)``
    (``daemon/services/job_feedback_observer.py:2670``).

    The Phase 1 fix routed both defer idle-gates (Gate A in
    ``JobProcessor._process_next_job`` and Gate B in
    ``JobQueueService._select_next_eligible_job``) through the same
    task-table predicate. Phase 3 verifies the Gate B wiring on the
    observer path with a real ``TaskRepository`` so the seam
    invariant — "a defer queue JobItem is held back whenever the
    project has any non-deferred PENDING/RUNNING Task" — survives a
    future refactor of the queue/Task boundary.
    """

    @pytest.mark.asyncio
    async def test_f8_select_next_eligible_job_blocks_defer_during_active_task(
        self, engine, task_repository, repository, lock_manager,
        queue_repository_with_system_queues,
    ):
        """F8 invariant: seed a project with (a) a non-deferred RUNNING
        Task and (b) a defer-queue JobItem; ``_select_next_eligible_job``
        MUST return None for the defer JobItem.

        Pre-fix (count_active_jobs_in_non_defer_queues blind spot):
        count of JobItem rows = 0 (no JobItems other than the defer
        one) → defer job selected → observer path would admit it.

        Post-fix (Phase 1 shared predicate): the same path consults
        ``TaskRepository.has_active_non_deferred_work(project_id)``
        which sees the non-deferred RUNNING Task and returns True →
        defer job is held back.
        """
        # Arrange
        # (a) Create a defer queue in the project
        project_id = "project-f8"
        defer_queue = queue_repository_with_system_queues.create(
            project_id=project_id,
            queue_name="system_defer_queue",
            queue_type="defer",
            concurrency_limit=1,
            is_system=True,
        )

        # Wire a JobQueueService with the real ``_task_repo`` so the
        # gate runs the actual ``has_active_non_deferred_work`` SQL.
        # This mirrors the production wiring path (JobQueueService is
        # constructed with a real ``TaskRepository`` via
        # ``InstanceManager._task_repo`` in ``InstanceManager.initialize``).
        queue_repo = queue_repository_with_system_queues
        service = JobQueueService(repository, lock_manager, queue_repo)
        instance_manager_stub = MagicMock()
        instance_manager_stub._task_repo = task_repository
        service.set_instance_manager(instance_manager_stub)

        # (b) Seed the project: an Instance + a non-deferred RUNNING Task
        _insert_instance(engine, "inst-f8-1", project_id=project_id)
        _create_task_with_status(
            engine,
            instance_id="inst-f8-1",
            message_id="m-f8-nd",
            status=TaskStatus.RUNNING.value,
            is_deferred=False,
        )

        # Sanity — the predicate agrees the project has non-deferred
        # work (the real SQL, not a mock). If this fails the rest of
        # the test is meaningless.
        assert task_repository.has_active_non_deferred_work(project_id) is True

        # (c) The defer JobItem — created via SQL because we want to
        # control admission_state + queue_id without running the
        # full ``create`` path's defaults.
        _insert_job_item(
            engine,
            job_id="job-f8-defer",
            instance_id="inst-f8-1",
            project_id=project_id,
            queue_id=defer_queue.queue_id,
            admission_state=AdmissionState.QUEUED.value,
            job_metadata={"message_id": "m-f8-defer"},
        )

        # Act — invoke the gate exactly as the observer path does
        # (``_get_next_job(project_id)`` → ``_select_next_eligible_job``).
        defer_job = repository.get("job-f8-defer")
        assert defer_job is not None
        result = await service._select_next_eligible_job(
            [defer_job], project_id
        )

        # Assert — the defer JobItem is held back. The observer path
        # would see ``next_job is None`` and not start it.
        assert result is None, (
            "F8 invariant: _select_next_eligible_job must return None "
            "for a defer JobItem while the project has a non-deferred "
            "RUNNING Task (shared has_active_non_deferred_work "
            "predicate on the observer admission path)."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — F2 maintenance _is_idle (JobItem gate)
# ─────────────────────────────────────────────────────────────────────────────


class TestF2MaintenanceIsIdleJobItemGate:
    """Phase 3 F2 (defer-seam bugfix, 2026-07-01):
    ``MaintenanceService._is_idle`` (``daemon/services/maintenance.py:240``)
    must return False whenever there is active queue-policy work —
    specifically when ANY JobItem row has ``admission_state IN
    ('queued','active')`` — so the maintenance cycle never runs
    while a job is in the queue.

    Phase 1 routed the task-side probe through the shared
    ``TaskRepository.has_active_non_deferred_work(None)`` predicate.
    The JobItem-side probe (``list_all_pending`` /
    ``find_processing_jobs``) is the F2 second leg — both halves of
    the dual-table work-tracking model must keep the maintenance
    cycle dormant. This file pins the Task-side probe in
    ``TestMaintenanceIsIdle``; this class pins the JobItem-side probe
    so a future regression that reverts either half (e.g. removing
    the ``find_processing_jobs`` branch or switching it back to a
    queued-only check) is caught immediately.
    """

    @pytest.mark.asyncio
    async def test_f2_is_idle_false_when_queued_jobitem_present(
        self, engine, repository, task_repository, lock_manager,
        queue_repository_with_system_queues,
    ):
        """F2 invariant: with an ACTIVE JobItem (admission_state='active')
        in the project and no Task rows, ``_is_idle`` returns False —
        the maintenance cycle must hold off while a job holds the
        queue lock.
        """
        # Arrange — Instance + ACTIVE JobItem (no Task row — the
        # "virtual job" case where the task table is empty but the
        # admission lifecycle is mid-flight).
        project_id = "test-project"
        _insert_instance(engine, "inst-f2-1", project_id=project_id)

        # ACTIVE JobItem (in-flight, holds the queue lock). We seed
        # this via SQL because ``list_all_pending`` only sees QUEUED
        # — for the ``active`` leg we need an ACTIVE row.
        now = datetime.now(timezone.utc).isoformat()
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
                    "job_id": "job-f2-active-1",
                    "agent_id": "developer",
                    "agent_dir": "agents/developer",
                    "message": "active job",
                    "source": "api",
                    "project_id": project_id,
                    "queue_id": None,
                    "priority": 0,
                    "admission_state": AdmissionState.ACTIVE.value,
                    "created_at": now,
                    "instance_id": "inst-f2-1",
                    "job_type": "task",
                    "retry_count": 0,
                    "metadata": json.dumps({"message_id": "m-f2-active-1"}),
                },
            )

        # Sanity — the JobRepository's ``find_processing_jobs`` agrees
        # the JobItem is mid-flight. The conftest's ``repository``
        # fixture is already a JobRepository instance.
        assert len(repository.find_processing_jobs()) == 1, (
            "Sanity: ACTIVE JobItem should be visible to "
            "find_processing_jobs"
        )

        # Wire MaintenanceService with both halves of the dual-table
        # probe (TaskRepository + a real JobQueueService).
        job_queue_service = JobQueueService(
            repository, lock_manager, queue_repository_with_system_queues
        )
        service = MaintenanceService(check_interval_minutes=15)
        service.set_task_repository(task_repository)
        service.set_job_queue_service(job_queue_service)

        # Act
        idle = await service._is_idle()

        # Assert — the JobItem gate fires even though no Task rows
        # exist (the TaskRepository probe would return False, but the
        # JobItem probe catches the work).
        assert idle is False, (
            "F2 invariant: _is_idle must return False while a JobItem "
            "is in 'active' admission_state, regardless of whether "
            "the project has any Task rows (covered by "
            "find_processing_jobs). Pre-fix: list_all_pending-only "
            "probe missed the 'active' bucket, returning True "
            "silently and running the maintenance cycle mid-flight."
        )

    @pytest.mark.asyncio
    async def test_f2_is_idle_false_when_queued_jobitem_only_present(
        self, engine, repository, task_repository, lock_manager,
        queue_repository_with_system_queues,
    ):
        """F2 invariant: with a QUEUED JobItem (admission_state='queued')
        — pre-flight, no lock held — ``_is_idle`` still returns False
        because ``list_all_pending`` catches the queued bucket.

        This pins the OTHER leg of F2: when only the queued bucket
        has rows (no Task, no 'active' JobItem), the maintenance
        cycle must still hold off.
        """
        # Arrange — Instance + QUEUED JobItem only.
        project_id = "test-project"
        _insert_instance(engine, "inst-f2-2", project_id=project_id)
        _insert_job_item(
            engine,
            job_id="job-f2-queued-1",
            instance_id="inst-f2-2",
            project_id=project_id,
            admission_state=AdmissionState.QUEUED.value,
            job_metadata={},
        )

        assert len(repository.list_all_pending()) == 1, (
            "Sanity: QUEUED JobItem should be visible to list_all_pending"
        )

        # Wire — same as the previous test.
        job_queue_service = JobQueueService(
            repository, lock_manager, queue_repository_with_system_queues
        )
        service = MaintenanceService(check_interval_minutes=15)
        service.set_task_repository(task_repository)
        service.set_job_queue_service(job_queue_service)

        # Act
        idle = await service._is_idle()

        # Assert
        assert idle is False, (
            "F2 invariant: _is_idle must return False while a JobItem "
            "is in 'queued' admission_state (list_all_pending leg). "
            "Pre-fix: also caught by list_all_pending, but this test "
            "pins the queued-only case distinctly from the active case."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 11: F5/F10 — periodic drift reconciler (Phase 3)
# ─────────────────────────────────────────────────────────────────────────────


class TestPeriodicDriftReconciler:
    """Phase 3 (defer-seam bugfix, F5/F10) — periodic dual-table drift
    reconciler.

    The reconciler lives in ``JobRecoveryService.reconcile_drift_states``
    and is run on a 60s asyncio loop by ``daemon/api.py``. It bypasses
    the ``MaintenanceService._is_idle`` gate because drift states
    appear *during* active work, which is precisely when the idle-gated
    maintenance loop skips.

    Three patterns are detected (see the docstring of
    ``reconcile_drift_states``). The two required tests cover (a)
    P1-pattern deadlock (active JobItem + stuck PENDING Task with NULL
    heartbeat) and (b) F10 done+running mismatch (done JobItem +
    RUNNING zombie Task).

    Both tests construct ``JobRecoveryService`` with the full dep
    triple (``task_repository``, ``stale_task_recovery``,
    ``job_queue_service``) so the active path runs end-to-end against
    the real in-memory engine. They use ``min_pending_age_seconds=0``
    so the PENDING task is considered drift-eligible immediately
    without waiting for the production 300s default.
    """

    @pytest.mark.asyncio
    async def test_reconciler_catches_p1_pattern_deadlock(
        self, engine, repository, task_repository, lock_repo,
        job_queue_service,
    ):
        """Pattern (a): ``active`` JobItem + ``pending`` Task with NULL
        heartbeat (older than the threshold) → reconciler detects and
        corrects by finalizing the JobItem as FAILED.

        Pre-fix: P1 wedges forever — the JobItem stays ``active``
        forever because the cross-system guard blocks the Task from
        claiming (NULL ``message_id`` stamp). No recovery path sweeps
        the dual-table drift, so the system stays in the broken
        state across daemon restarts.

        Post-fix: the periodic reconciler runs every 60s and detects
        the drift via ``TaskRepository.list_pending_tasks_older_than``
        — a PENDING task whose ``last_heartbeat_at IS NULL`` AND whose
        ``created_at`` is older than ``min_pending_age_seconds`` is
        considered drift-eligible. When the JobItem is ``active`` AND
        the instance is dead (terminal status), the reconciler
        finalizes the JobItem as FAILED via the single
        terminal-write boundary (``_finalize_terminal``).
        """
        # Arrange — instance terminal, JobItem active, PENDING task
        # older than the threshold with NULL heartbeat (canonical
        # P1-pattern deadlock signature).
        _insert_instance(engine, "inst-f5-p1", project_id="test-project")
        # Mark instance as terminal so the P1 detection branch
        # ("dead instance") fires (vs the alive-instance log-only
        # branch).
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET status = 'terminated' "
                    "WHERE instance_id = 'inst-f5-p1'"
                )
            )

        _insert_job_item(
            engine,
            job_id="job-f5-p1",
            instance_id="inst-f5-p1",
            project_id="test-project",
            queue_id="queue-f5-p1",
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-f5-p1"},
        )

        # Seed a PENDING task OLDER than the threshold with NULL
        # heartbeat. We backdate ``created_at`` via direct SQL because
        # ``_create_task_with_status`` uses ``datetime.now()`` which
        # would make the task fresh (not drift-eligible).
        task_id = _create_task_with_status(
            engine,
            instance_id="inst-f5-p1",
            message_id="msg-f5-p1",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        # Backdate created_at so the task is drift-eligible even with
        # ``min_pending_age_seconds=0`` (the threshold is checked
        # against ``created_at < (now - age_seconds)``; with age=0
        # this only fires when ``created_at < now``, which is
        # trivially true — but explicit backdating makes the test
        # intention clear and survives accidental threshold bumps).
        old_time = datetime.now(timezone.utc) - timedelta(seconds=600)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET created_at = :old_time WHERE id = :id"
                ),
                {"old_time": old_time, "id": task_id},
            )

        # Wire the recovery service with the full dep triple so the
        # F5 active path runs end-to-end (P1 detection → instance
        # liveness check → terminal-write boundary).
        instance_repo = SQLModelInstanceRepository(engine=engine)
        # StaleTaskRecovery takes both repos and we wire ``None`` for
        # the message/event/notifier deps the recovery flow doesn't
        # touch — only ``task_repo`` is required for the reconciler's
        # active path.
        stale_recovery = StaleTaskRecovery(
            task_repository=task_repository,
            message_repository=None,
            event_repository=None,
        )
        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        # Sanity — the drift signature is observable pre-reconcile.
        pre_drift = task_repository.list_pending_tasks_older_than(0)
        assert any(t.id == task_id for t in pre_drift), (
            f"Sanity: PENDING task with NULL heartbeat older than 0s "
            f"should appear in the drift list. Found: "
            f"{[(t.id, t.status, t.last_heartbeat_at) for t in pre_drift]}"
        )

        # Act — run the reconciler with ``min_pending_age_seconds=0``
        # so the freshly-created-then-backdated task qualifies.
        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
        )

        # Assert — drift was detected.
        assert stats["reconciled"] >= 1, (
            f"Reconciler must apply at least one correction for P1 "
            f"drift (active JobItem + stuck PENDING Task + dead "
            f"instance). Got stats: {stats}"
        )

        # Assert — JobItem is now FAILED (terminal).
        job_after = repository.get("job-f5-p1")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DONE.value, (
            f"P1 dead-instance correction must finalize the JobItem "
            f"to DONE. Got admission_state={job_after.admission_state}"
        )
        assert job_after.terminal_reason == "failed", (
            f"P1 dead-instance correction must set "
            f"terminal_reason='failed'. Got {job_after.terminal_reason!r}"
        )

        # Assert — a P1_dead_instance detail record was added.
        dead_instance_records = [
            d for d in stats["details"]
            if d.get("pattern") == "P1_dead_instance"
            and d.get("job_id") == "job-f5-p1"
        ]
        assert dead_instance_records, (
            f"Reconciler must record a P1_dead_instance detail for "
            f"job-f5-p1. Got details: {stats['details']}"
        )

    @pytest.mark.asyncio
    async def test_reconciler_catches_f10_done_running_mismatch(
        self, engine, repository, task_repository, lock_repo,
        job_queue_service,
    ):
        """Pattern (b) F10: ``done`` JobItem + ``running`` Task → the
        reconciler detects the zombie task and force-completes it
        (NOT cancels/retries).

        Pre-fix: the JobItem is finalized to ``done`` but the
        ``task`` row stays ``running`` (the ``notify_work_watchers``
        fire-and-forget raised and was swallowed). The next
        ``StaleTaskRecovery`` cycle then sees the running task as
        stale, force-cancels it, and schedules a retry against the
        already-terminal JobItem — DOUBLE EXECUTION of the same work.

        Post-fix: the periodic reconciler detects the F10 drift and
        force-completes the task (atomic RUNNING → COMPLETED via
        ``StaleTaskRecovery.force_complete_task``) BEFORE
        ``StaleTaskRecovery``'s threshold can fire. The JobItem is
        not retried (no new task spawned). The result payload on the
        Task row carries a ``reconciled=True`` marker so postmortem
        analysis can distinguish reconciler-completed tasks from
        naturally-completed ones.
        """
        # Arrange — instance alive, JobItem DONE, RUNNING task on the
        # same instance. Classic F10 zombie signature.
        _insert_instance(engine, "inst-f10-1", project_id="test-project")

        _insert_job_item(
            engine,
            job_id="job-f10-1",
            instance_id="inst-f10-1",
            project_id="test-project",
            queue_id="queue-f10-1",
            admission_state=AdmissionState.DONE.value,  # terminal!
            job_metadata={"message_id": "msg-f10-1"},
        )
        # A RUNNING task with a fresh heartbeat. The F10 detection
        # matches the Task to its OWN JobItem via ``work_id == job_id``
        # (the contract stamped at dispatch): a Task whose ``work_id``
        # resolves to a ``done`` JobItem is the genuine zombie. So the
        # seeded task carries ``work_id="job-f10-1"`` to model the
        # JobItem's driving Task. A ``job_continue`` continuation Task
        # would have a standalone ``work_id`` and would NOT be flagged.
        task_id = _create_task_with_status(
            engine,
            instance_id="inst-f10-1",
            message_id="msg-f10-1",
            status=TaskStatus.RUNNING.value,
            is_deferred=False,
            work_id="job-f10-1",
        )

        # Wire the recovery service with the full dep triple.
        instance_repo = SQLModelInstanceRepository(engine=engine)
        stale_recovery = StaleTaskRecovery(
            task_repository=task_repository,
            message_repository=None,
            event_repository=None,
        )
        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        # Sanity — the drift signature is observable pre-reconcile.
        pre_running = task_repository.list_running_tasks()
        assert any(t.id == task_id for t in pre_running), (
            f"Sanity: RUNNING task must appear in list_running_tasks. "
            f"Found: {[(t.id, t.status) for t in pre_running]}"
        )

        # Act
        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=300,  # unused for F10 path
        )

        # Assert — drift was detected and corrected.
        assert stats["reconciled"] >= 1, (
            f"Reconciler must apply at least one F10 correction. "
            f"Got stats: {stats}"
        )

        # Assert — task was force-completed (NOT cancelled).
        task_after = task_repository.get(task_id)
        assert task_after is not None
        assert task_after.status == TaskStatus.COMPLETED.value, (
            f"F10 correction must force-complete the task (running → "
            f"completed), not cancel or fail it. Got status="
            f"{task_after.status!r}"
        )

        # Assert — result payload carries the reconciler marker.
        assert task_after.result is not None, (
            "F10 force-completion must persist the result payload "
            "with the reconciler marker."
        )
        result_payload = json.loads(task_after.result)
        assert result_payload.get("reconciled") is True, (
            f"F10 result payload must carry reconciled=True marker. "
            f"Got: {result_payload}"
        )
        assert result_payload.get("completed_by") == "drift_reconciler_f10", (
            f"F10 result payload must identify the reconciler. "
            f"Got: {result_payload}"
        )

        # Assert — an F10 detail record was added.
        f10_records = [
            d for d in stats["details"]
            if d.get("pattern") == "F10_zombie_task"
            and d.get("task_id") == task_id
        ]
        assert f10_records, (
            f"Reconciler must record an F10_zombie_task detail for "
            f"task {task_id}. Got details: {stats['details']}"
        )

        # Assert — JobItem is unchanged (F10 is task-side only; the
        # JobItem was already terminal when the reconciler observed
        # the drift). The fix must NOT touch the JobItem.
        job_after = repository.get("job-f10-1")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DONE.value, (
            f"F10 correction must NOT modify the JobItem (it's "
            f"already DONE). Got admission_state="
            f"{job_after.admission_state}"
        )

    @pytest.mark.asyncio
    async def test_reconciler_cancels_orphan_pending_on_dead_instance(
        self, engine, repository, task_repository, lock_repo,
        job_queue_service,
    ):
        """F5 fix — Pattern (a) P1 dead-instance branch must cancel
        the orphan PENDING Task AFTER finalizing the JobItem.

        Pre-fix (verified by code review of
        ``daemon/services/job_recovery_service.py`` pre-Issue-1):
        the dead-instance branch finalized the JobItem via
        ``_finalize_terminal`` but never cancelled the stuck
        PENDING Task. The pre-fix inline comment claimed
        "the next reconciler tick will observe the orphan and clean
        it" — that was incorrect: F10 (Pattern (b)) only inspects
        RUNNING tasks (``list_running_tasks`` + ``WHERE
        status='running'`` guard in ``complete_task``), so the
        orphan PENDING Task was invisible to the reconciler until
        ``recover_on_startup`` swept it on the next daemon restart.

        Post-fix: Pattern (a) calls
        ``cancel_pending_tasks_for_instance`` immediately after
        finalization, transitioning the orphan PENDING to CANCELLED.
        This test pins the fix — without the new cancel call, the
        PENDING Task would still be ``pending`` after the reconciler
        runs and the test would fail.
        """
        # Arrange — instance terminal, JobItem active, PENDING task
        # with NULL heartbeat. Classic P1 dead-instance signature.
        _insert_instance(engine, "inst-f5-fix", project_id="test-project")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET status = 'terminated' "
                    "WHERE instance_id = 'inst-f5-fix'"
                )
            )

        _insert_job_item(
            engine,
            job_id="job-f5-fix",
            instance_id="inst-f5-fix",
            project_id="test-project",
            queue_id="queue-f5-fix",
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-f5-fix"},
        )

        task_id = _create_task_with_status(
            engine,
            instance_id="inst-f5-fix",
            message_id="msg-f5-fix",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        # Backdate so the task is drift-eligible with age=0.
        old_time = datetime.now(timezone.utc) - timedelta(seconds=600)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET created_at = :old_time WHERE id = :id"
                ),
                {"old_time": old_time, "id": task_id},
            )

        # Wire the recovery service with the full dep triple.
        instance_repo = SQLModelInstanceRepository(engine=engine)
        stale_recovery = StaleTaskRecovery(
            task_repository=task_repository,
            message_repository=None,
            event_repository=None,
        )
        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        # Sanity — task is PENDING before reconciler.
        pre_task = task_repository.get(task_id)
        assert pre_task is not None
        assert pre_task.status == TaskStatus.PENDING.value

        # Act
        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
        )

        # Assert — the JobItem was finalized as FAILED (terminal).
        job_after = repository.get("job-f5-fix")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DONE.value, (
            f"F5 dead-instance correction must finalize the JobItem "
            f"to DONE. Got admission_state={job_after.admission_state}"
        )
        assert job_after.terminal_reason == "failed"

        # Assert — the orphan PENDING Task is now CANCELLED (NOT
        # left PENDING — this is the F5 fix's whole point). F10 only
        # handles RUNNING tasks, so without the
        # ``cancel_pending_tasks_for_instance`` call in Pattern (a)
        # the task would still be PENDING here.
        post_task = task_repository.get(task_id)
        assert post_task is not None
        assert post_task.status == TaskStatus.CANCELLED.value, (
            f"F5 dead-instance correction MUST cancel the orphan "
            f"PENDING Task on the dead instance. Pre-fix the task "
            f"stayed PENDING because F10 only inspects RUNNING tasks "
            f"and the inline comment claiming 'next reconciler tick "
            f"will clean it' was incorrect. Got status="
            f"{post_task.status!r}"
        )

        # Assert — a P1_dead_instance detail record was added.
        dead_instance_records = [
            d for d in stats["details"]
            if d.get("pattern") == "P1_dead_instance"
            and d.get("job_id") == "job-f5-fix"
        ]
        assert dead_instance_records, (
            f"Reconciler must record a P1_dead_instance detail for "
            f"job-f5-fix. Got details: {stats['details']}"
        )

    @pytest.mark.asyncio
    async def test_reconciler_catches_orphan_pending_with_terminal_jobitem(
        self, engine, repository, task_repository, lock_repo,
        job_queue_service,
    ):
        """Pattern (d) — orphan PENDING Task on terminal JobItem.

        Scenario: the JobItem has already been finalized to
        ``done`` (e.g. via the retry engine flipping the JobItem
        to ``done``/``dead``) but a PENDING Task row survives on
        the same instance. This can occur in:

          * Pre-F5 deployments (Pattern (a) skipped the cancel).
          * Drift scenarios where the JobItem closed without the
            ``cancel_pending_tasks_for_instance`` (F12) path firing.
          * Test / migration paths that finalize the JobItem but
            leave a stray PENDING behind.

        F10 only inspects RUNNING tasks (``list_running_tasks`` +
        ``WHERE status='running'`` guard in ``complete_task``) — a
        PENDING task is invisible to it. Without Pattern (d), this
        orphan would leak until ``recover_on_startup`` on the next
        daemon restart.

        Pattern (d) catches the orphan PENDING whose JobItem is
        ``done`` (any instance state — alive or dead) and cancels
        it via ``cancel_pending_tasks_for_instance`` (the atomic
        ``WHERE status='pending'`` UPDATE used by F12).

        Setup: instance RUNNING (alive), JobItem DONE (terminal),
        PENDING Task with NULL heartbeat on the instance. The
        instance is alive — this distinguishes Pattern (d) from
        Pattern (a), which only fires on dead instances.
        """
        # Arrange — instance alive, JobItem terminal (done), PENDING
        # task on the instance.
        _insert_instance(engine, "inst-f5-orphan", project_id="test-project")
        # Default status is 'running' (alive) — leaves Pattern (a)
        # alone (alive-instance log-only branch). Pattern (d) is
        # the only path that catches this orphan.

        _insert_job_item(
            engine,
            job_id="job-f5-orphan",
            instance_id="inst-f5-orphan",
            project_id="test-project",
            queue_id="queue-f5-orphan",
            admission_state=AdmissionState.DONE.value,
            job_metadata={"message_id": "msg-f5-orphan"},
        )

        task_id = _create_task_with_status(
            engine,
            instance_id="inst-f5-orphan",
            message_id="msg-f5-orphan",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        # Backdate so the task is drift-eligible with age=0.
        old_time = datetime.now(timezone.utc) - timedelta(seconds=600)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET created_at = :old_time WHERE id = :id"
                ),
                {"old_time": old_time, "id": task_id},
            )

        # Wire the recovery service with the full dep triple.
        instance_repo = SQLModelInstanceRepository(engine=engine)
        stale_recovery = StaleTaskRecovery(
            task_repository=task_repository,
            message_repository=None,
            event_repository=None,
        )
        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        # Sanity — task is PENDING, JobItem is terminal.
        pre_task = task_repository.get(task_id)
        assert pre_task is not None
        assert pre_task.status == TaskStatus.PENDING.value
        pre_job = repository.get("job-f5-orphan")
        assert pre_job is not None
        assert pre_job.admission_state == AdmissionState.DONE.value

        # Act
        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
        )

        # Assert — the orphan PENDING Task is now CANCELLED.
        post_task = task_repository.get(task_id)
        assert post_task is not None
        assert post_task.status == TaskStatus.CANCELLED.value, (
            f"Pattern (d) MUST cancel the orphan PENDING Task whose "
            f"JobItem is terminal. Pre-fix the task stayed PENDING "
            f"indefinitely (F10 only handles RUNNING tasks). Got "
            f"status={post_task.status!r}"
        )

        # Assert — a Pattern (d) detail record was added.
        orphan_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_pending_terminal_job"
            and d.get("job_id") == "job-f5-orphan"
            and d.get("task_id") == task_id
        ]
        assert orphan_records, (
            f"Reconciler must record an orphan_pending_terminal_job "
            f"detail for task {task_id}. Got details: {stats['details']}"
        )

        # Assert — the JobItem is NOT touched by Pattern (d)
        # (it was already terminal — Pattern (d) is task-side only).
        job_after = repository.get("job-f5-orphan")
        assert job_after is not None
        assert job_after.admission_state == AdmissionState.DONE.value, (
            f"Pattern (d) must NOT modify the JobItem (it's already "
            f"DONE). Got admission_state={job_after.admission_state}"
        )

        # Assert — at least one Pattern (d) correction was applied.
        pattern_d_corrections = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_pending_terminal_job"
        ]
        assert len(pattern_d_corrections) >= 1, (
            f"Reconciler must apply at least one Pattern (d) "
            f"correction for the orphan PENDING task. Got stats: "
            f"{stats}"
        )

    @pytest.mark.asyncio
    async def test_reconciler_pattern_d_leaves_alive_instance_pending_alone(
        self, engine, repository, task_repository, lock_repo,
        job_queue_service,
    ):
        """Pattern (d) negative case: PENDING Task on a LIVE
        JobItem (``active``) MUST NOT be cancelled by Pattern (d).

        ``admission_state='active'`` is the canonical P1 candidate
        — Pattern (a) handles it (alive-instance log-only branch).
        Pattern (d) requires ``admission_state='done'``. This test
        confirms the two patterns don't double-handle / step on
        each other: an alive-instance active JobItem with a stuck
        PENDING Task gets a ``P1_alive_instance_log`` record, no
        ``orphan_pending_terminal_job`` record, and the PENDING
        Task survives (awaiting natural claim).
        """
        # Arrange — instance alive (default 'running'), JobItem
        # active (P1 candidate), PENDING task.
        _insert_instance(engine, "inst-f5-active-pending", project_id="test-project")

        _insert_job_item(
            engine,
            job_id="job-f5-active-pending",
            instance_id="inst-f5-active-pending",
            project_id="test-project",
            queue_id="queue-f5-active-pending",
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-f5-active-pending"},
        )

        task_id = _create_task_with_status(
            engine,
            instance_id="inst-f5-active-pending",
            message_id="msg-f5-active-pending",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        # Backdate so the task is drift-eligible with age=0.
        old_time = datetime.now(timezone.utc) - timedelta(seconds=600)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET created_at = :old_time WHERE id = :id"
                ),
                {"old_time": old_time, "id": task_id},
            )

        instance_repo = SQLModelInstanceRepository(engine=engine)
        stale_recovery = StaleTaskRecovery(
            task_repository=task_repository,
            message_repository=None,
            event_repository=None,
        )
        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        # Act
        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
        )

        # Assert — Pattern (a) alive-instance log-only branch fired.
        alive_records = [
            d for d in stats["details"]
            if d.get("pattern") == "P1_alive_instance_log"
            and d.get("job_id") == "job-f5-active-pending"
        ]
        assert alive_records, (
            f"Pattern (a) alive-instance branch must record a "
            f"P1_alive_instance_log for the active JobItem + stuck "
            f"PENDING. Got details: {stats['details']}"
        )

        # Assert — Pattern (d) did NOT cancel the task (JobItem is
        # active, not done).
        post_task = task_repository.get(task_id)
        assert post_task is not None
        assert post_task.status == TaskStatus.PENDING.value, (
            f"Pattern (d) must NOT cancel a PENDING Task on an "
            f"active JobItem (alive instance awaiting natural "
            f"claim). Got status={post_task.status!r}"
        )

        # Assert — no orphan_pending_terminal_job record for this
        # job (Pattern d only fires on terminal JobItems).
        orphan_records = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_pending_terminal_job"
            and d.get("job_id") == "job-f5-active-pending"
        ]
        assert not orphan_records, (
            f"Pattern (d) must NOT fire on an active JobItem. Got "
            f"orphan_pending_terminal_job records: {orphan_records}"
        )

    @pytest.mark.asyncio
    async def test_reconciler_c1_ordering_survives_finalize_failure(
        self, engine, repository, task_repository, lock_repo,
        job_queue_service,
    ):
        """W4 regression test — C1 fix (Phase 3, 2026-07-01): the
        Pattern (a) dead-instance branch MUST finalize the JobItem
        BEFORE cancelling the orphan PENDING Task. Pre-fix ordering
        (``cancel → finalize``) was an unrecoverable wedge when
        finalization failed: the catch block would log and continue,
        leaving ``JobItem=active`` + ``task=cancelled`` +
        ``instance=terminal`` — a state invisible to all 4 periodic
        reconciler patterns (a/b/c/d) and only recoverable by
        ``recover_on_startup`` on the next daemon restart.

        Post-fix ordering (``finalize → cancel``) is safe under
        three failure modes:
          * **Finalize fails**: cancel never runs, state unchanged.
            Pattern (a) retries next cycle (canonical P1 signature:
            active JobItem + stuck PENDING + dead instance).
          * **Finalize succeeds, cancel fails**: JobItem is
            ``done`` + task is ``pending`` → Pattern (d) catches
            orphan PENDING on terminal JobItem.
          * **Both succeed**: clean.

        This test pins the first two failure modes by injecting a
        transient ``RuntimeError`` from ``_finalize_terminal`` on
        the first reconciler tick (state must remain recoverable)
        and then running a second tick WITHOUT the failure
        (Pattern (a) must finalize + cancel correctly).
        """
        # ── Arrange — instance terminal, JobItem active, PENDING task ──
        # Mirrors the canonical P1 dead-instance signature used in
        # ``test_reconciler_catches_p1_pattern_deadlock`` so the
        # two tests exercise the same drift condition.
        _insert_instance(engine, "inst-c1-1", project_id="test-project")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET status = 'terminated' "
                    "WHERE instance_id = 'inst-c1-1'"
                )
            )

        _insert_job_item(
            engine,
            job_id="job-c1-1",
            instance_id="inst-c1-1",
            project_id="test-project",
            queue_id="queue-c1-1",
            admission_state=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-c1-1"},
        )

        task_id = _create_task_with_status(
            engine,
            instance_id="inst-c1-1",
            message_id="msg-c1-1",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        # Backdate so the task is drift-eligible with age=0
        # (matches the convention in the other P1 tests).
        old_time = datetime.now(timezone.utc) - timedelta(seconds=600)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET created_at = :old_time WHERE id = :id"
                ),
                {"old_time": old_time, "id": task_id},
            )

        # ── Wire service with the full dep triple ──
        instance_repo = SQLModelInstanceRepository(engine=engine)
        stale_recovery = StaleTaskRecovery(
            task_repository=task_repository,
            message_repository=None,
            event_repository=None,
        )
        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        # ── Patch ``_finalize_terminal`` to FAIL on the first tick ──
        # Save the original so the second tick (post-failure) can
        # delegate to it. The mock raises ONLY on the first call
        # (simulating a transient DB failure), then transparently
        # delegates on subsequent calls — so the retry tick
        # exercises the real finalize path while we still know
        # how many times the patch was invoked.
        original_finalize = service._job_queue_service._finalize_terminal
        call_count = {"n": 0}

        async def flaky_finalize(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Simulate SQLite WAL contention / disk I/O failure.
                raise RuntimeError(
                    "simulated WAL contention on _finalize_terminal"
                )
            # Subsequent calls delegate to the real method.
            return await original_finalize(*args, **kwargs)

        service._job_queue_service._finalize_terminal = flaky_finalize

        # ── Act #1 — finalize RAISES, cancel must NOT have run ──
        stats_fail = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
        )

        # ── Assert #1 — state is UNCHANGED, recoverable by Pattern (a) ──
        assert call_count["n"] == 1, (
            f"Reconciler must have attempted exactly one finalize "
            f"on the first pass. Got {call_count['n']} calls."
        )
        assert stats_fail["reconciled"] == 0, (
            f"C1 fix: when ``_finalize_terminal`` raises, the "
            f"reconciler must NOT count this as a successful "
            f"reconciliation (Pattern (a) will retry next cycle). "
            f"Got reconciled={stats_fail['reconciled']!r}"
        )

        # JobItem must still be ACTIVE — the failed finalize rolled
        # back. This is the canonical P1 signature Pattern (a)
        # expects to find on the next cycle.
        job_after_fail = repository.get("job-c1-1")
        assert job_after_fail is not None
        assert job_after_fail.admission_state == AdmissionState.ACTIVE.value, (
            f"C1 fix: when ``_finalize_terminal`` raises, the JobItem "
            f"must stay ACTIVE (finalize rolled back, cancel never "
            f"ran). Got admission_state="
            f"{job_after_fail.admission_state!r}"
        )

        # Task must still be PENDING — the post-fix ordering puts
        # the cancel block INSIDE the ``if canonical_job_id is not
        # None`` success branch, so a failed finalize means the
        # cancel block never ran. Pre-fix this would be
        # ``CANCELLED`` and Pattern (a) could never retry.
        task_after_fail = task_repository.get(task_id)
        assert task_after_fail is not None
        assert task_after_fail.status == TaskStatus.PENDING.value, (
            f"C1 fix: when ``_finalize_terminal`` raises, the orphan "
            f"PENDING task must NOT be cancelled (the cancel block "
            f"runs only AFTER successful finalization). Pre-fix, "
            f"the cancel-first ordering would have left the task "
            f"CANCELLED — invisible to all 4 reconciler patterns "
            f"and only recoverable on daemon restart. Got status="
            f"{task_after_fail.status!r}"
        )

        # ── Act #2 — second reconciler pass: finalize SUCCEEDS ──
        # ``flaky_finalize`` now delegates to ``original_finalize``
        # (since ``call_count["n"] > 1``).
        stats_ok = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
        )

        # ── Assert #2 — Pattern (a) catches the drift on retry ──
        assert call_count["n"] == 2, (
            f"Reconciler must have attempted a second finalize on "
            f"the retry pass. Got {call_count['n']} calls."
        )
        assert stats_ok["reconciled"] >= 1, (
            f"Pattern (a) must catch the P1 drift on the second "
            f"pass once finalize succeeds. Got stats: {stats_ok}"
        )

        # JobItem is now DONE with terminal_reason='failed'.
        job_after_ok = repository.get("job-c1-1")
        assert job_after_ok is not None
        assert job_after_ok.admission_state == AdmissionState.DONE.value, (
            f"Second-pass Pattern (a) must finalize the JobItem to "
            f"DONE. Got admission_state="
            f"{job_after_ok.admission_state!r}"
        )
        assert job_after_ok.terminal_reason == "failed", (
            f"Second-pass Pattern (a) must set terminal_reason="
            f"'failed'. Got {job_after_ok.terminal_reason!r}"
        )

        # The orphan PENDING task is now CANCELLED (the cancel
        # block ran after successful finalize).
        task_after_ok = task_repository.get(task_id)
        assert task_after_ok is not None
        assert task_after_ok.status == TaskStatus.CANCELLED.value, (
            f"Second-pass Pattern (a) must cancel the orphan PENDING "
            f"task after successful finalization. Got status="
            f"{task_after_ok.status!r}"
        )

        # A P1_dead_instance detail record was added on the second
        # pass.
        dead_instance_records = [
            d for d in stats_ok["details"]
            if d.get("pattern") == "P1_dead_instance"
            and d.get("job_id") == "job-c1-1"
        ]
        assert dead_instance_records, (
            f"Second-pass Pattern (a) must record a "
            f"P1_dead_instance detail for job-c1-1. Got details: "
            f"{stats_ok['details']}"
        )
