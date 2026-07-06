"""Tests for the JobItem <-> Task message bridge (Phase 1, Task 5).

The Job-as-Front-Primitive POC creates a JobItem mirror
``(job_type='message', job_id=task.work_id, max_retries=0)`` alongside
the existing Task + MessageQueue rows when the ``message_jobs_enabled``
feature flag is ON.

This file is the Phase 1 deliverable tests (per
``.agents/shared/planning/job-as-front-primitive/phase1-plan.md``
Task 8):

  (a) atomic creation -- work_id == job_id
  (b) message_id stamped on JobItem
  (c) observer finalizes queued message-JobItem to done (PG trigger failure)
  (d) flag-OFF path creates no JobItem
  (e) max_retries=0 on message JobItems

Run with::

    pytest tests/test_message_job_bridge.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select
from sqlmodel import update as sqlmodel_update

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
# Fixtures (mirroring tests/test_message_job_poc.py verbatim)
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

    # Config with feature flag -- default OFF; tests override per-case.
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

    * ``_job_queue_service.get_job_by_instance`` -- async delegate
      to the real ``JobRepository.get_by_instance``.
    * ``_job_repo.get_active_by_instance`` -- real method on the
      real ``JobRepository`` (used in the defense-in-depth re-query).
    * ``_instance_manager._task_repo.get_by_work_id`` -- real method
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
    """Fetch all JobItem rows (no instance filter -- JobItem is the mirror)."""
    with Session(engine) as session:
        return list(session.exec(select(JobItem)))


def _load_message_job_items(engine) -> list[JobItem]:
    """Fetch all JobItem rows with ``job_type == 'message'``."""
    with Session(engine) as session:
        return list(
            session.exec(select(JobItem).where(JobItem.job_type == "message"))
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test (a): atomic JobItem + Task creation with flag ON, work_id == job_id
# ──────────────────────────────────────────────────────────────────────────────


class TestAtomicCreationWithFlagOn:
    """``message_jobs_enabled = True`` => ``enqueue_message_job()`` creates
    a JobItem mirror with ``job_type='message'`` alongside the Task +
    ``MessageQueue`` rows in one call. The JobItem's ``job_id`` equals
    the Task's ``work_id`` (same UUID4 string) -- the linkage contract.
    """

    @pytest.mark.asyncio
    async def test_a_work_id_equals_job_id(
        self, engine, instance_repository, write_guard, job_repository
    ):
        # Flag ON (mirrors what the router reads from config).
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

        # The POC path resolves agent_dir via the registry. Patch the
        # registry to return None so the fallback path runs (agent_dir=""
        # is acceptable -- the JobItem is an informational mirror).
        # NB: ``get_registry`` is imported lazily inside
        # ``enqueue_message_job`` (``from ..registry import get_registry``)
        # so we must patch the source location, not the importer.
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_resolved.return_value = None
            mock_get_registry.return_value = mock_registry

            result = await messaging_service.enqueue_message_job(
                instance_id="inst-1",
                message="hello from flag-on path",
                source="api",
            )

        # ── Result contract ──
        assert result.status == "queued"
        assert result.message_id is not None
        assert result.instance_id == "inst-1"
        assert result.job_id is not None, "enqueue_message_job must mint a job_id"

        # ── Exactly 1 MessageQueue row ──
        mq_rows = _load_message_queues(engine, "inst-1")
        assert len(mq_rows) == 1, f"expected exactly one MessageQueue row, got {len(mq_rows)}"

        # ── Exactly 1 Task row ──
        task_rows = _load_tasks(engine, "inst-1")
        assert len(task_rows) == 1, f"expected exactly one Task row, got {len(task_rows)}"
        task = task_rows[0]
        assert task.work_id is not None

        # ── Exactly 1 JobItem row with ``job_type='message'`` ──
        all_jobs = _load_job_items(engine)
        assert len(all_jobs) == 1, (
            f"expected exactly one JobItem row, got {len(all_jobs)}"
        )
        ji = all_jobs[0]
        assert ji.job_type == "message", (
            f"JobItem.job_type must be 'message', got {ji.job_type!r}"
        )

        # ── Linkage contract: task.work_id == result.job_id AND ji.job_id == task.work_id ──
        assert task.work_id == result.job_id, (
            f"Task.work_id ({task.work_id}) must equal AsyncMessageResult.job_id "
            f"({result.job_id})"
        )
        assert ji.job_id == task.work_id, (
            f"JobItem.job_id ({ji.job_id}) must equal Task.work_id "
            f"({task.work_id}) per the linkage contract"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test (b): message_id stamped on JobItem
# ──────────────────────────────────────────────────────────────────────────────


class TestMessageIdStamped:
    """``enqueue_message_job()`` must stamp the ``message_id`` onto the
    JobItem's ``metadata`` JSON. The cross-system guard reads
    ``job_queue_items.metadata.message_id`` to resolve an active MESSAGE
    JobItem back to its originating message -- without this stamp the
    correlation is NULL and the cross-system guard cannot match.
    """

    @pytest.mark.asyncio
    async def test_b_message_id_in_job_metadata(
        self, engine, instance_repository, write_guard, job_repository
    ):
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

        # Wrap stamp_message_id BEFORE the call so we can capture call args
        # without replacing the underlying behaviour (the
        # ``enqueue_message_job`` path calls it via ``asyncio.to_thread``).
        original_stamp = job_repository.stamp_message_id
        job_repository.stamp_message_id = MagicMock(wraps=original_stamp)

        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_resolved.return_value = None
            mock_get_registry.return_value = mock_registry

            result = await messaging_service.enqueue_message_job(
                instance_id="inst-1",
                message="message-id-stamp-test",
                source="api",
            )

        # ── message_id present in JobItem.job_metadata ──
        jobs = _load_message_job_items(engine)
        assert len(jobs) == 1
        ji = jobs[0]

        assert ji.job_metadata is not None
        assert ji.job_metadata.get("message_id") == result.message_id, (
            f"JobItem.job_metadata.message_id ({ji.job_metadata.get('message_id')!r}) "
            f"must equal result.message_id ({result.message_id!r}) -- "
            f"stamp_message_id was not called or failed silently"
        )

        # ── stamp_message_id called once with (job_id, message_id) ──
        job_repository.stamp_message_id.assert_called_once()
        stamp_args = job_repository.stamp_message_id.call_args.args
        assert stamp_args[0] == result.job_id, (
            f"stamp_message_id called with job_id={stamp_args[0]!r}, "
            f"expected {result.job_id!r}"
        )
        assert stamp_args[1] == result.message_id, (
            f"stamp_message_id called with message_id={stamp_args[1]!r}, "
            f"expected {result.message_id!r}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test (c): observer finalizes `queued` message-JobItem to `done`
# ──────────────────────────────────────────────────────────────────────────────


class TestObserverFinalizesQueuedJobItem:
    """End-to-end coverage of the stuck-queued finalize path.

    Simulates the PG trigger failure described in the plan: the eager
    ``atomic_transition`` call inside ``enqueue_message_job`` (which flips
    ``queued`` -> ``active``) is patched to raise, so the JobItem stays
    in ``queued``. After the Task is manually flipped to ``RUNNING``
    (simulating a worker claim), the C1 guard in
    ``_get_processing_job_for_instance`` opens because Task=RUNNING and
    returns the JobItem as the processing-job context. The
    ``_finalize_job_db_sync`` WHERE clause ``WHERE admission_state IN
    ('queued', 'active')`` then matches the still-queued JobItem and
    transitions it to ``done``.
    """

    @pytest.mark.asyncio
    async def test_c_stuck_queued_jobitem_finalizes_to_done(
        self,
        engine,
        instance_repository,
        write_guard,
        job_repository,
        task_repository,
    ):
        # ── Flag ON, enqueue_message_job ──
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

        # Patch the eager ``atomic_transition`` call inside
        # ``enqueue_message_job`` to raise, so the JobItem stays in
        # ``queued``. Production code suppresses this exception via the
        # ``except`` block at ``instance_messaging.py:~1331`` (debug log
        # only); message processing is unaffected because the Task row
        # is the authoritative dispatch primitive.
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch("daemon.registry.get_registry") as mock_get_registry, patch.object(
            job_repository,
            "atomic_transition",
            side_effect=RuntimeError(
                "simulated post-claim activation UPDATE missed"
            ),
        ):
            mock_registry = MagicMock()
            mock_registry.get_resolved.return_value = None
            mock_get_registry.return_value = mock_registry

            result = await messaging_service.enqueue_message_job(
                instance_id="inst-1",
                message="stuck-queued scenario",
                source="api",
            )

        # ── Precondition: JobItem stayed in ``queued`` ──
        jobs_before = _load_message_job_items(engine)
        assert len(jobs_before) == 1, (
            f"expected exactly one message JobItem, got {len(jobs_before)}"
        )
        assert jobs_before[0].admission_state == AdmissionState.QUEUED.value, (
            "stuck-queued precondition: JobItem must remain in queued "
            f"(got {jobs_before[0].admission_state!r})"
        )

        # ── Manually flip Task -> RUNNING (simulate worker claim) ──
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

        # ── C1 guard opens because Task is RUNNING ──
        assert isinstance(ctx, _ProcessingJobContext), (
            "helper must return a _ProcessingJobContext (post-D13 contract)"
        )
        assert ctx.instance_id == "inst-1"
        assert ctx.job_id == result.job_id, (
            "C1 guard must open for queued + Task RUNNING: expected "
            f"job_id={result.job_id!r}, got {ctx.job_id!r}"
        )

        # ── Run the exact UPDATE ``_finalize_job_db_sync`` would run ──
        # ``WHERE job_id = ? AND admission_state IN ('queued', 'active')``
        # Mirrors the production WHERE clause that lets a stuck-queued
        # JobItem be finalized to ``done`` without prior activation.
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
            f"finalize WHERE must match stuck-queued JobItem "
            f"(expected rowcount=1, got {rowcount})"
        )

        # ── JobItem is now ``done`` ──
        jobs_after = _load_message_job_items(engine)
        assert len(jobs_after) == 1
        assert jobs_after[0].admission_state == AdmissionState.DONE.value


# ──────────────────────────────────────────────────────────────────────────────
# Test (d): flag-OFF path creates no JobItem
# ──────────────────────────────────────────────────────────────────────────────


class TestFlagOffNoJobItem:
    """``message_jobs_enabled = False`` => ``enqueue_message()`` does NOT
    create a JobItem row. Task + ``MessageQueue`` rows ARE created --
    the legacy D13 single-writer behaviour is preserved byte-identically.
    """

    @pytest.mark.asyncio
    async def test_d_flag_off_creates_no_jobitem(
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
            )

        # ── Result contract ──
        assert result.status == "queued"
        assert result.message_id is not None
        assert result.instance_id == "inst-1"
        assert result.job_id is not None

        # ── MessageQueue + Task rows ARE created (legacy preserved) ──
        mq_rows = _load_message_queues(engine, "inst-1")
        assert len(mq_rows) == 1, (
            f"legacy path must still create a MessageQueue row, got {len(mq_rows)}"
        )
        task_rows = _load_tasks(engine, "inst-1")
        assert len(task_rows) == 1, (
            f"legacy path must still create a Task row, got {len(task_rows)}"
        )

        # ── NO JobItem rows at all ──
        msg_jobs = _load_message_job_items(engine)
        assert msg_jobs == [], (
            f"legacy enqueue_message must NOT create a JobItem(job_type='message') "
            f"row, but found {len(msg_jobs)}: {[j.job_id for j in msg_jobs]}"
        )
        all_jobs = _load_job_items(engine)
        assert all_jobs == [], (
            f"legacy path must NOT write any JobItem rows, found {len(all_jobs)}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test (e): max_retries=0 on message JobItems
# ──────────────────────────────────────────────────────────────────────────────


class TestMaxRetriesZero:
    """``enqueue_message_job()`` must create the JobItem mirror with
    ``max_retries=0`` -- messages are inline-dispatched by the
    ``POST /messages`` handler, so retries are managed by the
    instance-side ``retry_count`` mechanism rather than the JobRetryEngine.
    A default ``max_retries`` (None or 3) would let the JobRetryEngine
    reschedule a message-JobItem after a terminal failure, racing the
    instance's own retry path.
    """

    @pytest.mark.asyncio
    async def test_e_max_retries_zero_on_message_jobitems(
        self, engine, instance_repository, write_guard, job_repository
    ):
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

        # Wrap ``create`` BEFORE the call. ``MagicMock(wraps=...)``
        # delegates to the real method AND records call_args so we
        # can inspect the kwargs after. The production code calls
        # ``job_repo.create`` via ``asyncio.to_thread`` -- the
        # wrapping works regardless of the thread the call runs on.
        original_create = job_repository.create
        job_repository.create = MagicMock(wraps=original_create)

        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_resolved.return_value = None
            mock_get_registry.return_value = mock_registry

            result = await messaging_service.enqueue_message_job(
                instance_id="inst-1",
                message="max-retries-test",
                source="api",
            )

        # ── JobItem has ``max_retries == 0`` (NOT None, NOT 3) ──
        jobs = _load_message_job_items(engine)
        assert len(jobs) == 1
        ji = jobs[0]

        assert ji.max_retries == 0, (
            f"message JobItem must have max_retries=0 "
            f"(got {ji.max_retries!r}) -- a non-zero or None value lets "
            f"the JobRetryEngine reschedule the JobItem after a terminal "
            f"failure, racing the instance's own retry path"
        )
        assert ji.max_retries is not None, (
            f"max_retries must be the explicit integer 0, not None "
            f"(got {ji.max_retries!r})"
        )

        # ── ``job_repository.create`` was called with ``max_retries=0`` ──
        job_repository.create.assert_called()
        create_kwargs = job_repository.create.call_args.kwargs
        assert create_kwargs.get("max_retries") == 0, (
            f"job_repository.create must be called with max_retries=0 "
            f"kwarg (got kwargs={ {k: v for k, v in create_kwargs.items()} })"
        )
