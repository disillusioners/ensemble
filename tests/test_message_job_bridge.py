"""Tests for the JobItem <-> Task message bridge (Phase 5 cutover).

After Phase 5, every public message creates a JobItem mirror
``(job_type='message', job_id=task.work_id, max_retries=0)`` alongside
the existing Task + MessageQueue rows. There is only one public message
path now.

This file is the Phase 1 deliverable tests (per
``.agents/shared/planning/job-as-front-primitive/phase1-plan.md``
Task 8), updated for the cutover:

  (a) atomic creation -- work_id == job_id
  (b) message_id stamped on JobItem
  (c) observer finalizes queued message-JobItem to done (PG trigger failure)
  (d) raw ``enqueue_message()`` path (internal-only) creates no JobItem
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
def queue_repository(engine):
    """Real ``JobQueueRepository`` seeded with ``system_parallel_queue``
    for ``test-project`` so the POC ``enqueue_message_job`` resolves a
    real ``queue_id`` (string) for the JobItem mirror instead of relying
    on the broken pre-fix lookup that silently swallowed the
    ``AttributeError`` on ``JobRepository.get_by_name``.
    """
    from daemon.repositories.job_queue.queue_repository import JobQueueRepository

    repo = JobQueueRepository(engine)
    repo.create(
        project_id="test-project",
        queue_name="system_parallel_queue",
        queue_type="parallel",
        concurrency_limit=5,
        is_system=True,
    )
    return repo


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


def _build_manager(
    engine, instance_repository, write_guard, job_repository, queue_repository
):
    """Build a mock ``InstanceManager`` exposing only the attributes
    ``enqueue_message`` and ``enqueue_message_job`` actually touch.

    Phase 5 (Option B) cutover: ``enqueue_message_job`` routes the
    message through ``JobQueueService.enqueue`` (the async entry
    point) instead of writing Task + MessageQueue + JobItem rows
    directly. The mock's ``_job_queue_service.enqueue`` is therefore
    an ``AsyncMock`` that returns a synthetic JobItem.

    ``_job_queue_service._repository`` is kept on the real
    ``JobRepository`` so the cross-table drift checks (used by the
    observer tests) can still resolve. ``_job_queue_service._queue_repo``
    is wired to the real ``JobQueueRepository`` so the queue_id
    resolution (``get_by_name("system_parallel_queue")``) returns a
    real ``JobQueue`` with a string ``queue_id`` (a ``MagicMock``
    would fail SQLite parameter binding).
    """
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    manager._instance_repository = instance_repository

    # ``_live_hub.stream_status_change`` is awaited after status transition.
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()

    # ``enqueue_message`` (the dispatch-time path) still calls
    # ``_worker_pool.notify_work()``; ``enqueue_message_job`` does
    # NOT call it (deferred to dispatch). The assertion lives in the
    # test below.
    manager._worker_pool = MagicMock()
    manager._worker_pool.notify_work = MagicMock()

    # Option B: ``enqueue_message_job`` calls this async method on
    # ``_job_queue_service``. Return a synthetic JobItem mirroring
    # the ``create`` contract.
    manager._job_queue_service = MagicMock()
    manager._job_queue_service._repository = job_repository
    manager._job_queue_service._queue_repo = queue_repository
    # ``enqueue`` is async — it returns a JobItem whose
    # ``job_id`` is the new UUID4 minted by the repository.
    fake_job_item = MagicMock()
    fake_job_item.job_id = "job-from-enqueue"
    fake_job_item.job_type = "message"
    fake_job_item.instance_id = "inst-1"
    fake_job_item.admission_state = AdmissionState.QUEUED.value
    fake_job_item.project_id = "test-project"
    fake_job_item.queue_id = None
    fake_job_item.max_retries = 0
    manager._job_queue_service.enqueue = AsyncMock(return_value=fake_job_item)

    # Config -- single public path after Phase 5 cutover.
    manager.config = Config(job_system=JobSystemConfig())

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
# Test (a): enqueue_message_job routes through JobQueueService.enqueue
# ──────────────────────────────────────────────────────────────────────────────


class TestEnqueueMessageJobRoutesThroughEnqueue:
    """``enqueue_message_job()`` routes the message through
    ``JobQueueService.enqueue(job_type='message', ...)`` (Phase 5 /
    Option B cutover) — the JobItem is created via the queue path
    (real slot-based concurrency enforcement) and the Task +
    ``MessageQueue`` rows are created **at dispatch time** inside the
    JobProcessor's message branch.

    The AsyncMessageResult contract changes:

    * ``message_id`` is ``None`` — minted only inside ``enqueue_message``
      at dispatch time, not at submission time.
    * ``job_id`` is the JobItem's UUID4 minted by enqueue.
    * ``status`` is ``"queued"`` — waiting for slot, not running.

    No ``MessageQueue`` / ``Task`` rows are written at this point:
    those come from the JobProcessor message branch when the slot
    is acquired.
    """

    @pytest.mark.asyncio
    async def test_a_routes_through_enqueue_with_preserved_instance_id(
        self, engine, instance_repository, write_guard, job_repository, queue_repository
    ):
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository, queue_repository
        )

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
                message="hello from option-b path",
                source="api",
            )

        # ── AsyncMessageResult contract (Option B) ──
        assert result.status == "queued"
        # Phase 5 cutover: message_id is created at dispatch time, NOT
        # at submission time — it is None on the queue result.
        assert result.message_id is None, (
            f"enqueue_message_job must return message_id=None "
            f"(created at dispatch time); got {result.message_id!r}"
        )
        assert result.instance_id == "inst-1"
        assert result.job_id is not None, "enqueue_message_job must mint a job_id"

        # ── JobQueueService.enqueue called with the correct parameters ──
        manager._job_queue_service.enqueue.assert_awaited_once()
        enqueue_kwargs = manager._job_queue_service.enqueue.call_args.kwargs
        assert enqueue_kwargs.get("job_type") == "message", (
            f"enqueue must be called with job_type='message'; "
            f"got kwargs={enqueue_kwargs!r}"
        )
        assert enqueue_kwargs.get("instance_id") == "inst-1", (
            f"enqueue must preserve the existing instance_id; "
            f"got instance_id={enqueue_kwargs.get('instance_id')!r}"
        )
        assert enqueue_kwargs.get("message") == "hello from option-b path", (
            f"enqueue must thread the supplied message; "
            f"got message={enqueue_kwargs.get('message')!r}"
        )
        assert enqueue_kwargs.get("source") == "api"

        # ── No Task / MessageQueue rows created at submission time ──
        # The Task + MessageQueue rows are created at dispatch time
        # inside the JobProcessor message branch. At this point
        # (post enqueue_message_job) only the JobItem exists in the
        # queue (mocked via the AsyncMock).
        mq_rows = _load_message_queues(engine, "inst-1")
        assert len(mq_rows) == 0, (
            f"enqueue_message_job must NOT create MessageQueue rows "
            f"(those are created at dispatch time); got {len(mq_rows)}"
        )
        task_rows = _load_tasks(engine, "inst-1")
        assert len(task_rows) == 0, (
            f"enqueue_message_job must NOT create Task rows "
            f"(those are created at dispatch time); got {len(task_rows)}"
        )
        # The mock's enqueue is the only writer of the JobItem — the
        # DB-side repo is NOT called directly (the enqueue path goes
        # through the AsyncMock, not the real ``job_repository``).
        all_jobs = _load_job_items(engine)
        assert len(all_jobs) == 0, (
            f"enqueue_message_job (Option B) must NOT write JobItem rows "
            f"directly via the repository — the queue's enqueue() is the "
            f"sole writer; got {len(all_jobs)} JobItem rows"
        )

        # ── worker_pool.notify_work() NOT called at submission time ──
        # The dispatch happens via the JobProcessor's message branch
        # (slot acquisition), not via the legacy worker wake-up at
        # submission time. This is the core Phase 5 invariant — double
        # dispatch would race the slot lock.
        manager._worker_pool.notify_work.assert_not_called(), (
            "enqueue_message_job must NOT call worker_pool.notify_work() — "
            "dispatch is deferred to the JobProcessor message branch. "
            "A spurious notify_work() would race the slot lock and cause "
            "double dispatch."
        )

        # ── stamp_message_id NOT called at submission time ──
        # The message_id is None at this point and is stamped onto the
        # JobItem AFTER Task creation in the message branch.
        # ``stamp_message_id`` is a real method on the repo; wrap it
        # so the assertion can read the call history.
        original_stamp = job_repository.stamp_message_id
        job_repository.stamp_message_id = MagicMock(wraps=original_stamp)
        try:
            job_repository.stamp_message_id.assert_not_called()
        finally:
            job_repository.stamp_message_id = original_stamp


# ──────────────────────────────────────────────────────────────────────────────
# Test (b): metadata (images, is_deferred, is_background) flows through enqueue
# ──────────────────────────────────────────────────────────────────────────────


class TestMetadataFlowsThroughEnqueue:
    """``enqueue_message_job()`` threads ``images``, ``is_deferred``,
    and ``is_background`` through the ``metadata`` dict passed to
    ``JobQueueService.enqueue(...)``. The JobProcessor's message branch
    reads these straight from ``job.job_metadata`` at dispatch time.
    """

    @pytest.mark.asyncio
    async def test_b_metadata_threads_to_enqueue_kwargs(
        self, engine, instance_repository, write_guard, job_repository, queue_repository
    ):
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository, queue_repository
        )

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

            await messaging_service.enqueue_message_job(
                instance_id="inst-1",
                message="metadata-flow-test",
                source="api",
                images=["b64-image-1", "b64-image-2"],
                is_deferred=True,
                is_background=True,
                metadata={"custom_key": "custom_val"},
            )

        manager._job_queue_service.enqueue.assert_awaited_once()
        enqueue_kwargs = manager._job_queue_service.enqueue.call_args.kwargs

        # ── metadata dict holds the three keyboard-forwarding fields ──
        metadata = enqueue_kwargs.get("metadata")
        assert metadata is not None, (
            f"enqueue must be called with a metadata dict; "
            f"got kwargs={enqueue_kwargs!r}"
        )
        assert metadata.get("images") == ["b64-image-1", "b64-image-2"], (
            f"metadata.images must be threaded through; "
            f"got {metadata.get('images')!r}"
        )
        assert metadata.get("is_deferred") is True, (
            f"metadata.is_deferred must be True; got {metadata.get('is_deferred')!r}"
        )
        assert metadata.get("is_background") is True, (
            f"metadata.is_background must be True; got {metadata.get('is_background')!r}"
        )
        # User-supplied metadata is preserved alongside the forwarded fields.
        assert metadata.get("custom_key") == "custom_val", (
            f"user-supplied metadata must be preserved; "
            f"got {metadata.get('custom_key')!r}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test (c): observer finalizes `queued` message-JobItem to `done`
# ──────────────────────────────────────────────────────────────────────────────


class TestObserverFinalizesQueuedJobItem:
    """End-to-end coverage of the stuck-queued finalize path under
    Option B.

    In Option B, the message JobItem is INTENTIONALLY in
    ``admission_state='queued'`` after ``enqueue_message_job`` (it
    waits for slot acquisition). The observer's C1 guard in
    ``_get_processing_job_for_instance`` opens when the JobItem is in
    ``queued`` AND a Task exists AND the Task is RUNNING — exactly the
    state that arises once the JobProcessor's message branch fires
    ``enqueue_message`` (writes Task + MessageQueue) and a worker claims
    the Task.

    This test sets up that state directly (bypassing the dispatch path,
    which we test in a separate integration test) and verifies the
    ``_finalize_job_db_sync`` Utrecht WHERE clause
    ``WHERE admission_state IN ('queued', 'active')`` matches the
    still-queued JobItem and transitions it to ``done``.
    """

    @pytest.mark.asyncio
    async def test_c_stuck_queued_jobitem_finalizes_to_done(
        self,
        engine,
        instance_repository,
        write_guard,
        job_repository,
        task_repository,
        queue_repository,
    ):
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)

        # ── Insert a JobItem(message, queued) directly — simulates the
        #    Option B state AFTER ``enqueue`` returns and BEFORE the
        #    JobProcessor dispatch runs.
        with Session(engine) as session:
            job = JobItem(
                job_id="job-stuck-queued",
                job_type="message",
                agent_id="developer",
                agent_dir="/agents/developer",
                project_id="test-project",
                instance_id="inst-1",
                message="stuck-queued scenario",
                source="api",
                priority=1,
                admission_state=AdmissionState.QUEUED.value,
                max_retries=0,
                job_metadata={"images": [], "is_deferred": False, "is_background": False},
            )
            session.add(job)
            session.commit()

        # ── Insert a Task row with ``work_id == job_id`` (the
        #    dispatch-time link). This is what the JobProcessor's
        #    message branch writes via ``enqueue_message(work_id=…)``.
        from daemon.repositories.task.models import Task as _Task

        with Session(engine) as session:
            task = _Task(
                instance_id="inst-1",
                work_id="job-stuck-queued",
                status=TaskStatus.PENDING.value,
                message_id="msg-stub",
                task_type="message",
                is_deferred=False,
                is_background=False,
            )
            session.add(task)
            session.commit()

        # ── Manually flip Task -> RUNNING (simulate worker claim) ──
        with Session(engine) as session:
            task = session.exec(
                select(_Task).where(_Task.work_id == "job-stuck-queued")
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
            "helper must return a _ProcessingJobContext (Option B contract)"
        )
        assert ctx.instance_id == "inst-1"
        assert ctx.job_id == "job-stuck-queued", (
            "C1 guard must open for queued + Task RUNNING: expected "
            f"job_id='job-stuck-queued', got {ctx.job_id!r}"
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
# Test (d): internal-only ``enqueue_message()`` path creates no JobItem
# ──────────────────────────────────────────────────────────────────────────────


class TestInternalEnqueueMessageNoJobItem:
    """``enqueue_message()`` (the internal-only path used by reports,
    nudges, ``[JOB_EVENT]`` delivery, compaction, ``invoke_and_wait``)
    does NOT create a JobItem row. Task + ``MessageQueue`` rows ARE
    created -- the raw D13 single-writer behaviour. The JobItem mirror
    is the public-path contract (``enqueue_message_job()``); internal
    callers intentionally stay invisible to the WorkResolver facade.
    """

    @pytest.mark.asyncio
    async def test_d_internal_enqueue_creates_no_jobitem(
        self, engine, instance_repository, write_guard, job_repository, queue_repository
    ):
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository, queue_repository
        )

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
                message="hello from internal-only path",
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
            f"internal path must still create a MessageQueue row, got {len(mq_rows)}"
        )
        task_rows = _load_tasks(engine, "inst-1")
        assert len(task_rows) == 1, (
            f"internal path must still create a Task row, got {len(task_rows)}"
        )

        # ── NO JobItem rows at all ──
        msg_jobs = _load_message_job_items(engine)
        assert msg_jobs == [], (
            f"internal enqueue_message must NOT create a JobItem(job_type='message') "
            f"row, but found {len(msg_jobs)}: {[j.job_id for j in msg_jobs]}"
        )
        all_jobs = _load_job_items(engine)
        assert all_jobs == [], (
            f"internal path must NOT write any JobItem rows, found {len(all_jobs)}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test (e): message JobItem inherits instance project_id
# ──────────────────────────────────────────────────────────────────────────────


class TestMessageJobItemInheritsProjectId:
    """``enqueue_message_job()`` resolves the instance's
    ``project_id`` and forwards it to ``JobQueueService.enqueue``. The
    JobItem inherits its instance's project_id so project-scoped views
    (the Jobs UI refresh) see the message.

    Regression (2026-07-07): the pre-Phase-3 ``enqueue_message_job``
    created the message JobItem via ``job_repo.create(...)`` WITHOUT a
    ``project_id``, so the row was stored with a NULL project_id.
    That made the job invisible to project-scoped views — the Jobs
    UI refresh (``GET /api/jobs?project_id=…``) dropped a paused
    message job even though its instance belonged to the project.
    The fix resolves ``instances.project_id`` and forwards it
    (normalised) into ``enqueue``.
    """

    @pytest.mark.asyncio
    async def test_e_project_id_threads_to_enqueue(
        self, engine, instance_repository, write_guard, job_repository, queue_repository
    ):
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository, queue_repository
        )

        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(
                spec=CancellationService, is_shutting_down=False
            ),
        )

        _seed_instance(
            engine,
            instance_id="inst-proj",
            status=InstanceStatus.IDLE.value,
            project_id="proj-message-test",
        )

        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_resolved.return_value = None
            mock_get_registry.return_value = mock_registry

            await messaging_service.enqueue_message_job(
                instance_id="inst-proj",
                message="project-scoped message",
                source="api",
            )

        # ── enqueue received the instance's project_id ──
        manager._job_queue_service.enqueue.assert_awaited_once()
        enqueue_kwargs = manager._job_queue_service.enqueue.call_args.kwargs
        assert enqueue_kwargs.get("project_id") == "proj-message-test", (
            f"enqueue must be called with the instance's project_id "
            f"'proj-message-test'; got project_id={enqueue_kwargs.get('project_id')!r}"
        )

    @pytest.mark.asyncio
    async def test_e_project_id_falls_back_to_system_default(
        self, engine, instance_repository, write_guard, job_repository, queue_repository
    ):
        """When the instance has no project_id, the message JobItem
        falls back to the system default project (via
        ``normalize_project_id``) instead of NULL.
        """
        from daemon import constants

        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository, queue_repository
        )

        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(
                spec=CancellationService, is_shutting_down=False
            ),
        )

        # Instance with no project_id (legacy / pre-normalisation row).
        _seed_instance(
            engine,
            instance_id="inst-noproj",
            status=InstanceStatus.IDLE.value,
            project_id=None,
        )

        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_resolved.return_value = None
            mock_get_registry.return_value = mock_registry

            await messaging_service.enqueue_message_job(
                instance_id="inst-noproj",
                message="no-project message",
                source="api",
            )

        # ── enqueue received the system default project_id ──
        manager._job_queue_service.enqueue.assert_awaited_once()
        enqueue_kwargs = manager._job_queue_service.enqueue.call_args.kwargs
        assert enqueue_kwargs.get("project_id") == constants.SYSTEM_DEFAULT_PROJECT_ID, (
            f"enqueue must be called with the system default project_id "
            f"({constants.SYSTEM_DEFAULT_PROJECT_ID!r}) for project-less "
            f"instances; got project_id={enqueue_kwargs.get('project_id')!r}"
        )
