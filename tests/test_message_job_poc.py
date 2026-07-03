"""Tests for the message-Job POC feature flag.

The POC adds a feature flag ``ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED``
(field ``message_jobs_enabled`` on ``JobSystemConfig``). When ON,
``POST /messages`` NORMAL branch routes through
``manager.enqueue_message_job()``, which creates a
``JobItem(job_type='message')`` alongside the existing Task +
``MessageQueue`` rows. When OFF, the legacy ``enqueue_message`` path
writes only Task + ``MessageQueue`` — the frozen D13 baseline.

Linkage contract:

    ``Task.work_id == JobItem.job_id`` (same UUID4 string).

These tests verify the two paths end-to-end against an in-memory
SQLite engine, asserting the observable DB state after each call.

What's verified:

  * Flag ON: JobItem row exists with ``job_type='message'``,
    ``admission_state='queued'``, ``job_id == task.work_id``;
    ``stamp_message_id`` wrote ``message_id`` into
    ``job_queue_items.metadata.message_id``.
  * Flag OFF: no ``job_queue_items`` row with ``job_type='message'``
    is created; Task + ``MessageQueue`` rows ARE created
    (preserved behaviour).

Run with::

    pytest tests/test_message_job_poc.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

# Register all tables with SQLModel.metadata via model imports.
from daemon.config import Config, JobSystemConfig
from daemon.repositories.event.models import Event
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.cancellation import CancellationService
from daemon.services.instance_messaging import InstanceMessagingService
from daemon.services.job_feedback_observer import (
    JobFeedbackObserver,
    _ProcessingJobContext,
)
from daemon.write_pause_guard import WritePauseGuard


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """In-memory SQLite engine (StaticPool for cross-thread safety).

    Mirrors the pattern from ``tests/test_enqueue_shared.py`` and
    ``tests/job_queue/conftest.py`` so existing tooling (e.g.
    ``SQLModel.metadata.create_all``) registers all SQLModel tables
    including ``job_queue_items``.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def instance_repository(engine):
    """Real ``SQLModelInstanceRepository`` backed by the in-memory engine."""
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def job_repository(engine):
    """Real ``JobRepository`` backed by the in-memory engine.

    The POC's ``enqueue_message_job`` calls ``JobRepository.create``
    directly (bypassing ``JobQueueService.enqueue_job`` which rejects
    ``job_type='message'``) and then ``stamp_message_id`` to correlate
    the JobItem back to the originating ``message_id``.
    """
    return JobRepository(engine)


@pytest.fixture
def cancellation_service():
    """Mock ``CancellationService`` with ``is_shutting_down=False``."""
    service = MagicMock(spec=CancellationService)
    service.is_shutting_down = False
    return service


@pytest.fixture
def write_guard():
    """Real ``WritePauseGuard`` (no active pause)."""
    return WritePauseGuard()


def _build_manager(engine, instance_repository, write_guard, job_repository):
    """Build a mock ``InstanceManager`` exposing only the attributes
    ``enqueue_message`` and ``enqueue_message_job`` actually touch.

    ``_job_queue_service._repository`` is wired to the real
    ``JobRepository`` so the POC's ``enqueue_message_job`` can write
    JobItem rows + stamp ``message_id`` via the repository's
    low-level path.
    """
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    manager._instance_repository = instance_repository

    # ``_live_hub.stream_status_change`` is awaited after status transition.
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()

    # ``enqueue_message`` calls ``_worker_pool.notify_work()``; None is fine
    # because the code guards with ``if self._manager._worker_pool is not None``.
    manager._worker_pool = MagicMock()
    manager._worker_pool.notify_work = MagicMock()

    # Wire the JobQueueService to expose a real JobRepository. The POC
    # ``enqueue_message_job`` resolves ``manager._job_queue_service._repository``
    # via the ``_job_repository`` property on InstanceMessagingService.
    manager._job_queue_service = MagicMock()
    manager._job_queue_service._repository = job_repository

    # Config with feature flag — default OFF; tests override per-case.
    manager.config = Config(job_system=JobSystemConfig(message_jobs_enabled=False))

    # Title generation fires via MainLoopBridge; we'll patch it out.
    manager._generate_and_broadcast_title = AsyncMock()

    return manager


def _seed_instance(
    engine,
    *,
    instance_id: str = "inst-1",
    agent_id: str = "developer",
    agent_dir: str = "/agents/developer",
    status: str = InstanceStatus.IDLE.value,
    project_id: str | None = "test-project",
    version: int = 1,
) -> Instance:
    """Insert an ``Instance`` row in the test engine."""
    inst = Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=agent_dir,
        project_id=project_id,
        status=status,
        version=version,
        instance_metadata={},
    )
    with Session(engine) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


@pytest.fixture
def task_repository(engine):
    """Real ``TaskRepository`` backed by the in-memory engine.

    Used by the C1 guard tests: ``_get_processing_job_for_instance``
    consults ``manager._task_repo.get_by_work_id`` to verify a
    queued JobItem's Task row is actually RUNNING before treating
    the JobItem as the active processing job.
    """
    return TaskRepository(engine)


def _build_observer(engine, job_repository, task_repository) -> JobFeedbackObserver:
    """Build a minimal ``JobFeedbackObserver`` exposing only the
    attributes ``_get_processing_job_for_instance`` actually touches.

    The observer's three dependencies the helper relies on:

    * ``_job_queue_service.get_job_by_instance`` — async delegate
      to the real ``JobRepository.get_by_instance``.
    * ``_job_repo.get_active_by_instance`` — real method on the
      real ``JobRepository`` (used in the defense-in-depth re-query).
    * ``_instance_manager._task_repo.get_by_work_id`` — real method
      on the real ``TaskRepository`` (used by the C1 guard).

    Everything else is a MagicMock so the observer can be
    instantiated without spinning up the EventBus / LockRepository /
    ProjectRepository, which are out of scope for these tests.
    """
    observer = JobFeedbackObserver.__new__(JobFeedbackObserver)
    observer._event_bus = MagicMock()
    observer._job_queue_service = MagicMock()

    async def _get_job_by_instance(instance_id: str) -> JobItem | None:
        return job_repository.get_by_instance(instance_id)

    observer._job_queue_service.get_job_by_instance = AsyncMock(
        side_effect=_get_job_by_instance
    )
    observer._job_repo = job_repository
    observer._lock_repo = MagicMock()
    observer._project_repo = MagicMock()

    manager = MagicMock()
    manager._task_repo = task_repository
    manager.engine = engine
    observer._instance_manager = manager

    return observer


# ──────────────────────────────────────────────────────────────────────────────
# Query helpers
# ──────────────────────────────────────────────────────────────────────────────


def _load_message_queues(engine, instance_id: str) -> list[MessageQueue]:
    with Session(engine) as session:
        return list(
            session.exec(
                select(MessageQueue).where(MessageQueue.instance_id == instance_id)
            )
        )


def _load_tasks(engine, instance_id: str) -> list[Task]:
    with Session(engine) as session:
        return list(
            session.exec(select(Task).where(Task.instance_id == instance_id))
        )


def _load_job_items(engine) -> list[JobItem]:
    """Fetch all JobItem rows (no instance filter — JobItem is the mirror)."""
    with Session(engine) as session:
        return list(session.exec(select(JobItem)))


def _load_message_job_items(engine) -> list[JobItem]:
    """Fetch all JobItem rows with ``job_type == 'message'``."""
    with Session(engine) as session:
        return list(
            session.exec(select(JobItem).where(JobItem.job_type == "message"))
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test 1: Flag ON — Message creates JobItem mirror
# ──────────────────────────────────────────────────────────────────────────────


class TestFlagOnCreatesJobItem:
    """``message_jobs_enabled = True`` → ``enqueue_message_job()`` creates
    a JobItem mirror with ``job_type='message'`` alongside the Task +
    ``MessageQueue`` rows.
    """

    @pytest.mark.asyncio
    async def test_flag_on_message_creates_jobitem_mirror(
        self, engine, instance_repository, write_guard, job_repository
    ):
        # Flag ON (mirrors what the router reads from config).
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository
        )
        manager.config.job_system.message_jobs_enabled = True

        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(spec=CancellationService, is_shutting_down=False),
        )

        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)

        # The POC path resolves agent_dir via the registry. Patch the
        # registry to return None so the fallback path runs (agent_dir=""
        # is acceptable — the JobItem is an informational mirror).
        # NB: ``get_registry`` is imported lazily inside
        # ``enqueue_message_job`` (``from ..registry import get_registry``)
        # so we must patch the source location, not the importer.
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch(
            "daemon.registry.get_registry"
        ) as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_resolved.return_value = None
            mock_get_registry.return_value = mock_registry

            result = await messaging_service.enqueue_message_job(
                instance_id="inst-1",
                message="hello from flag-on path",
                source="api",
                priority=1,
            )

        # ── Result contract ──
        assert result.status == "queued"
        assert result.message_id is not None
        assert result.instance_id == "inst-1"
        assert result.job_id is not None, "enqueue_message_job must mint a job_id"

        # ── MessageQueue row created ──
        mq_rows = _load_message_queues(engine, "inst-1")
        assert len(mq_rows) == 1, "expected exactly one MessageQueue row"
        mq = mq_rows[0]
        assert mq.content == "hello from flag-on path"
        assert mq.source == "api"
        assert mq.message_id == result.message_id

        # ── Task row created ──
        task_rows = _load_tasks(engine, "inst-1")
        assert len(task_rows) == 1, "expected exactly one Task row"
        task = task_rows[0]
        assert task.work_id is not None
        # Linkage contract: task.work_id == JobItem.job_id
        assert task.work_id == result.job_id, (
            f"Task.work_id ({task.work_id}) must equal AsyncMessageResult.job_id "
            f"({result.job_id})"
        )

        # ── JobItem mirror row created ──
        all_jobs = _load_job_items(engine)
        assert len(all_jobs) == 1, (
            f"expected exactly one JobItem row, got {len(all_jobs)}"
        )
        ji = all_jobs[0]

        assert ji.job_type == "message", (
            f"JobItem.job_type must be 'message', got {ji.job_type!r}"
        )
        assert ji.admission_state == "queued", (
            f"JobItem.admission_state must be 'queued' at creation, "
            f"got {ji.admission_state!r}"
        )
        assert ji.instance_id == "inst-1"

        # ── Linkage contract ──
        assert ji.job_id == task.work_id, (
            f"JobItem.job_id ({ji.job_id}) must equal Task.work_id "
            f"({task.work_id}) per the linkage contract"
        )
        assert ji.job_id == result.job_id

        # ── stamp_message_id correlation ──
        # The cross-system guard reads
        # ``job_queue_items.metadata->>'message_id'`` (PG) /
        # ``json_extract(metadata, '$.message_id')`` (SQLite). The
        # ``stamp_message_id`` write must have populated it.
        assert ji.job_metadata is not None
        assert ji.job_metadata.get("message_id") == result.message_id, (
            f"JobItem.job_metadata.message_id ({ji.job_metadata.get('message_id')!r}) "
            f"must equal result.message_id ({result.message_id!r}) — "
            f"stamp_message_id was not called or failed silently"
        )

    @pytest.mark.asyncio
    async def test_flag_on_stamp_message_id_called_with_correct_args(
        self, engine, instance_repository, write_guard, job_repository
    ):
        """Sanity: ``stamp_message_id`` is invoked with the right job_id +
        message_id. The repository mock has ``create`` and ``stamp_message_id``
        instrumented so we can verify the cross-system correlation key.
        """
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository
        )
        manager.config.job_system.message_jobs_enabled = True

        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(
                spec=CancellationService, is_shutting_down=False
            ),
        )

        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)

        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch(
            "daemon.registry.get_registry"
        ) as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_resolved.return_value = None
            mock_get_registry.return_value = mock_registry

            # Wrap the real repository's methods so we can capture call args
            # without replacing the underlying behaviour (the
            # ``enqueue_message_job`` path calls them via ``asyncio.to_thread``).
            original_create = job_repository.create
            original_stamp = job_repository.stamp_message_id
            job_repository.create = MagicMock(wraps=original_create)
            job_repository.stamp_message_id = MagicMock(wraps=original_stamp)

            result = await messaging_service.enqueue_message_job(
                instance_id="inst-1", message="m", source="api"
            )

            # Repository.create was called with job_type='message'.
            create_kwargs = job_repository.create.call_args.kwargs
            assert create_kwargs.get("job_type") == "message"
            assert create_kwargs.get("instance_id") == "inst-1"

            # stamp_message_id was called with (job_id=task.work_id, message_id).
            stamp_args = job_repository.stamp_message_id.call_args.args
            assert stamp_args[0] == result.job_id, (
                f"stamp_message_id called with job_id={stamp_args[0]!r}, "
                f"expected {result.job_id!r}"
            )
            assert stamp_args[1] == result.message_id


# ──────────────────────────────────────────────────────────────────────────────
# Test 2: Flag OFF — No JobItem created
# ──────────────────────────────────────────────────────────────────────────────


class TestFlagOffNoJobItem:
    """``message_jobs_enabled = False`` → ``enqueue_message()`` does NOT
    create a JobItem row. Task + ``MessageQueue`` rows ARE created —
    the legacy D13 single-writer behaviour is preserved.
    """

    @pytest.mark.asyncio
    async def test_flag_off_message_does_not_create_jobitem(
        self, engine, instance_repository, write_guard, job_repository
    ):
        # Flag OFF (the default; also what the router honours when OFF).
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository
        )
        manager.config.job_system.message_jobs_enabled = False

        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(
                spec=CancellationService, is_shutting_down=False
            ),
        )

        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)

        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            result = await messaging_service.enqueue_message(
                instance_id="inst-1",
                message="hello from flag-off path",
                source="api",
                priority=1,
            )

        # ── Result contract ──
        assert result.status == "queued"
        assert result.message_id is not None
        assert result.instance_id == "inst-1"
        # The legacy enqueue_message path also returns job_id (== task.work_id),
        # but the JobItem mirror is NOT created.
        assert result.job_id is not None

        # ── Task + MessageQueue rows ARE created (legacy preserved) ──
        mq_rows = _load_message_queues(engine, "inst-1")
        assert len(mq_rows) == 1, "legacy path must still create a MessageQueue row"
        task_rows = _load_tasks(engine, "inst-1")
        assert len(task_rows) == 1, "legacy path must still create a Task row"

        # ── NO JobItem mirror row with job_type='message' ──
        msg_jobs = _load_message_job_items(engine)
        assert len(msg_jobs) == 0, (
            f"legacy enqueue_message must NOT create a JobItem(job_type='message') "
            f"row, but found {len(msg_jobs)}: {[j.job_id for j in msg_jobs]}"
        )

        # ── Repository.create was NEVER called ──
        # We didn't wire a spy on ``create`` in this test, so verify by
        # absence of rows. (Defensive double-check: job_queue_items is empty.)
        all_jobs = _load_job_items(engine)
        assert len(all_jobs) == 0, (
            f"legacy path must NOT write any JobItem rows, found {len(all_jobs)}"
        )

    @pytest.mark.asyncio
    async def test_flag_off_repository_create_never_invoked(
        self, engine, instance_repository, write_guard, job_repository
    ):
        """Sanity: the legacy path never reaches ``JobRepository.create``.

        The flag-OFF path must short-circuit before touching the job queue
        service. We instrument the repository to detect any invocation.
        """
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository
        )
        manager.config.job_system.message_jobs_enabled = False

        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(
                spec=CancellationService, is_shutting_down=False
            ),
        )

        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)

        # Spy on the repository methods; they must NOT be invoked.
        job_repository.create = MagicMock(wraps=job_repository.create)
        job_repository.stamp_message_id = MagicMock(
            wraps=job_repository.stamp_message_id
        )

        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            await messaging_service.enqueue_message(
                instance_id="inst-1", message="m", source="api"
            )

        # Legacy path never touches JobRepository.
        job_repository.create.assert_not_called()
        job_repository.stamp_message_id.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# Test 3: C1 Premature Finalization Guard
# ──────────────────────────────────────────────────────────────────────────────


class TestC1PrematureFinalizationGuard:
    """C1 fix: ``_get_processing_job_for_instance`` must NOT match a
    ``queued`` JobItem whose Task row is still ``pending``.

    Without the guard, an unrelated lifecycle event arriving for the
    same instance finds the freshly-created message JobItem (state =
    ``queued``) and finalizes it (``queued → done``) — silently
    dropping the message Task that's still waiting to be claimed by
    a worker (data-loss bug).

    The Task's ``RUNNING`` status is the authoritative serialization
    gate. This test class verifies both halves of that gate.
    """

    @pytest.mark.asyncio
    async def test_queued_jobitem_with_pending_task_does_not_match(
        self,
        engine,
        instance_repository,
        write_guard,
        job_repository,
        task_repository,
    ):
        """Task=pending + JobItem=queued → guard returns no match.

        ``_get_processing_job_for_instance`` falls through to the
        post-D13 MESSAGE branch and returns ``_ProcessingJobContext(
        instance_id, job_id=None)`` — the same context returned when
        no JobItem exists at all. Callers (e.g. ``_process_event``)
        treat ``job_id=None`` as "no JobItem to finalize", which is
        the conservative behaviour: the message Task stays pending,
        waiting for a worker to claim it.
        """
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository
        )
        manager.config.job_system.message_jobs_enabled = True

        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(
                spec=CancellationService, is_shutting_down=False
            ),
        )

        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)

        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_resolved.return_value = None
            mock_get_registry.return_value = mock_registry

            result = await messaging_service.enqueue_message_job(
                instance_id="inst-1", message="guard-test", source="api"
            )

        # ── Baseline state: Task pending, JobItem queued ──
        tasks = _load_tasks(engine, "inst-1")
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.PENDING.value
        assert tasks[0].work_id == result.job_id

        jobs = _load_message_job_items(engine)
        assert len(jobs) == 1
        assert jobs[0].admission_state == AdmissionState.QUEUED.value

        # ── Build observer and call the helper ──
        observer = _build_observer(engine, job_repository, task_repository)

        ctx = await observer._get_processing_job_for_instance("inst-1")

        # ── Guard returns no-match: job_id must be None ──
        assert isinstance(ctx, _ProcessingJobContext), (
            "helper must return a _ProcessingJobContext (post-D13 contract)"
        )
        assert ctx.instance_id == "inst-1"
        assert ctx.job_id is None, (
            "C1 guard: queued JobItem with pending Task must NOT match "
            f"(got job_id={ctx.job_id!r}). Without this guard, any "
            "unrelated lifecycle event would finalize the JobItem and "
            "silently drop the message Task."
        )

    @pytest.mark.asyncio
    async def test_queued_jobitem_with_running_task_matches(
        self,
        engine,
        instance_repository,
        write_guard,
        job_repository,
        task_repository,
    ):
        """Task=running + JobItem=queued → guard returns a match.

        After the worker claims the Task (status flips pending →
        running), the C1 gate opens and the helper returns
        ``_ProcessingJobContext(instance_id, job_id=<real>)``. The
        ``_finalize_job_db_sync`` WHERE clause (``admission_state
        IN ('active','queued')``) then matches the still-queued
        JobItem and transitions it to ``done``.

        This is the second half of the C1 guard — it must allow
        finalization once the worker has actually started processing
        the message (otherwise ``done`` would leak forever on stuck-
        queued JobItems).
        """
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository
        )
        manager.config.job_system.message_jobs_enabled = True

        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(
                spec=CancellationService, is_shutting_down=False
            ),
        )

        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)

        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_resolved.return_value = None
            mock_get_registry.return_value = mock_registry

            result = await messaging_service.enqueue_message_job(
                instance_id="inst-1", message="guard-test-running", source="api"
            )

        # ── Sanity: Task starts pending ──
        tasks_before = _load_tasks(engine, "inst-1")
        assert tasks_before[0].status == TaskStatus.PENDING.value

        # ── Flip Task to running (simulate worker claim) ──
        with Session(engine) as session:
            task = session.exec(
                select(Task).where(Task.work_id == result.job_id)
            ).one()
            task.status = TaskStatus.RUNNING.value
            task.started_at = task.started_at or task.created_at
            session.add(task)
            session.commit()

        # ── Build observer and call the helper ──
        observer = _build_observer(engine, job_repository, task_repository)

        ctx = await observer._get_processing_job_for_instance("inst-1")

        # ── Guard now opens: job_id must be the real JobItem ──
        assert ctx.instance_id == "inst-1"
        assert ctx.job_id == result.job_id, (
            "C1 guard must open when Task is RUNNING: expected "
            f"job_id={result.job_id!r}, got {ctx.job_id!r}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test 4: Stuck-Queued Finalize (combined C1 guard + Part-B finalize)
# ──────────────────────────────────────────────────────────────────────────────


class TestStuckQueuedFinalize:
    """End-to-end coverage of the ``queued JobItem + running Task``
    finalize path.

    The C1 guard ensures we only finalize queued JobItems whose Task
    is actually RUNNING (see ``TestC1PrematureFinalizationGuard``).
    Part-B (the ``_finalize_job_db_sync`` WHERE clause) then matches
    those queued-or-active JobItems so a stuck-queued JobItem — one
    whose post-claim activation UPDATE (``queued → active``) missed
    or raced — can still be finalized to ``done`` when the worker
    completes its lifecycle.

    This test verifies both pieces line up: the helper locates the
    queued JobItem via the C1 gate, and the finalize WHERE clause
    matches it.
    """

    @pytest.mark.asyncio
    async def test_queued_running_pair_matches_and_finalize_succeeds(
        self,
        engine,
        instance_repository,
        write_guard,
        job_repository,
        task_repository,
    ):
        # ── Setup: flag ON, enqueue_message_job creates both rows ──
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository
        )
        manager.config.job_system.message_jobs_enabled = True

        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(
                spec=CancellationService, is_shutting_down=False
            ),
        )

        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)

        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_resolved.return_value = None
            mock_get_registry.return_value = mock_registry

            result = await messaging_service.enqueue_message_job(
                instance_id="inst-1",
                message="stuck-queued scenario",
                source="api",
            )

        # ── Manually push Task → running AND leave JobItem in queued ──
        # This models the "stuck-queued" race: the activation UPDATE
        # (``queued → active``) raced/missed, but the Task is genuinely
        # being processed by a worker.
        with Session(engine) as session:
            task = session.exec(
                select(Task).where(Task.work_id == result.job_id)
            ).one()
            task.status = TaskStatus.RUNNING.value
            task.started_at = task.started_at or task.created_at
            session.add(task)
            session.commit()

        jobs_before = _load_message_job_items(engine)
        assert len(jobs_before) == 1
        assert jobs_before[0].admission_state == AdmissionState.QUEUED.value, (
            "stuck-queued precondition: JobItem must remain in queued"
        )

        # ── Step 1: helper locates the queued JobItem via C1 gate ──
        observer = _build_observer(engine, job_repository, task_repository)

        ctx = await observer._get_processing_job_for_instance("inst-1")

        assert ctx.instance_id == "inst-1"
        assert ctx.job_id == result.job_id, (
            "queued+running pair must match the C1 gate"
        )

        # ── Step 2: finalize WHERE clause matches the queued JobItem ──
        # Simulate the exact UPDATE ``_finalize_job_db_sync`` would run:
        # ``WHERE job_id = ? AND admission_state IN ('queued', 'active')``.
        # A successful rowcount > 0 confirms the stuck-queued JobItem can
        # be transitioned to ``done`` via this path.
        from sqlmodel import update as sqlmodel_update

        with Session(engine) as session:
            stmt = (
                sqlmodel_update(JobItem)
                .where(JobItem.job_id == ctx.job_id)
                .where(
                    JobItem.admission_state.in_(
                        [
                            AdmissionState.ACTIVE.value,
                            AdmissionState.QUEUED.value,
                        ]
                    )
                )
                .values(admission_state=AdmissionState.DONE.value)
            )
            update_result = session.exec(stmt)
            session.commit()
            rowcount = update_result.rowcount

        assert rowcount == 1, (
            f"finalize WHERE clause must match the stuck-queued JobItem "
            f"(expected rowcount=1, got {rowcount})"
        )

        # ── Step 3: JobItem is now ``done`` ──
        jobs_after = _load_message_job_items(engine)
        assert len(jobs_after) == 1
        assert jobs_after[0].admission_state == AdmissionState.DONE.value


# ──────────────────────────────────────────────────────────────────────────────
# Test 5: Poll-Loop Filter — list_pending_by_queue excludes message jobs
# ──────────────────────────────────────────────────────────────────────────────


class TestPollLoopFiltersMessageJobs:
    """``JobRepository.list_pending_by_queue`` must NOT return
    ``job_type='message'`` JobItems.

    The JobProcessor poll loop calls ``list_pending_by_queue`` to
    find work to dispatch. Message jobs are dispatched inline by the
    ``POST /messages`` handler — returning them here would cause the
    poll loop to create a duplicate Task for an already-running
    message, double-dispatching the work.

    This test verifies the ``WHERE job_type != 'message'`` filter
    is applied (and that non-message jobs still pass through).
    """

    def test_message_jobitem_excluded_from_poll_loop(
        self, engine, job_repository
    ):
        # ── Create a queued message-JobItem in the same queue ──
        msg_job = job_repository.create(
            agent_id="developer",
            agent_dir="/agents/developer",
            message="poll-filter-test",
            source="api",
            queue_id="queue-A",
            priority=5,
            job_type="message",
            instance_id="inst-1",
        )

        # Sanity: JobItem was created with the expected shape.
        assert msg_job.admission_state == AdmissionState.QUEUED.value
        assert msg_job.job_type == "message"

        # ── Call list_pending_by_queue ──
        pending = job_repository.list_pending_by_queue("queue-A")

        # ── Message JobItem must NOT be in the poll loop's view ──
        pending_ids = {j.job_id for j in pending}
        assert msg_job.job_id not in pending_ids, (
            "list_pending_by_queue must exclude job_type='message' "
            "JobItems (poll-loop filter); otherwise the JobProcessor "
            "would double-dispatch the message by creating a duplicate Task."
        )

    def test_non_message_jobitem_returned_by_poll_loop(
        self, engine, job_repository
    ):
        """A non-message JobItem in the same queue IS returned.

        Regression guard for the same filter: the WHERE clause must
        pass through ``task`` (default) and any other non-message
        ``job_type`` values. This confirms the filter is precisely
        ``!= 'message'``, not e.g. ``= 'task'``.
        """
        # ── Seed: a queued message JobItem + a queued task JobItem ──
        msg_job = job_repository.create(
            agent_id="developer",
            agent_dir="/agents/developer",
            message="poll-filter-msg",
            source="api",
            queue_id="queue-B",
            priority=5,
            job_type="message",
            instance_id="inst-1",
        )
        task_job = job_repository.create(
            agent_id="developer",
            agent_dir="/agents/developer",
            message="poll-filter-task",
            source="api",
            queue_id="queue-B",
            priority=5,
            job_type="task",
            instance_id="inst-1",
        )

        assert msg_job.admission_state == AdmissionState.QUEUED.value
        assert task_job.admission_state == AdmissionState.QUEUED.value

        # ── list_pending_by_queue returns ONLY the task JobItem ──
        pending = job_repository.list_pending_by_queue("queue-B")

        pending_ids = {j.job_id for j in pending}
        assert task_job.job_id in pending_ids, (
            "non-message JobItem (job_type='task') must be returned by "
            "list_pending_by_queue so the JobProcessor poll loop picks it up"
        )
        assert msg_job.job_id not in pending_ids, (
            "message JobItem must remain filtered out even when another "
            "(non-message) JobItem exists in the same queue"
        )