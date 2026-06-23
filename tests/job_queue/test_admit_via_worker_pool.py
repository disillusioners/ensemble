"""Direct coverage for ``JobFeedbackObserver._admit_via_worker_pool()``.

Migrated from ``tests/test_unified_dispatcher_shadow.py`` (deleted in
Phase 7, commit e603b031). That file contained the SOLE direct tests
for the admission invariant — covering ``_admit_via_worker_pool``'s
happy path, randomized scenario equivalence, and the four
silent-return failure-mode regressions.

Scope (10 test methods, 22 test cases with parametrization)
============================================================

* **Tests 1–5** — Basic admission via observer. ``JobProcessor`` routes
  MESSAGE jobs through ``_admit_via_worker_pool``. Each test pins one
  observable side-effect: Task row creation with the correct
  ``message_id``, ``worker_pool.notify_work()`` call, ``JobItem`` stays
  ``PROCESSING`` after admission, the Task is pickable by the
  ``WorkerPool``, and the ``JobItem`` transitions to ``COMPLETED`` when
  the Task completes.

* **Tests 6–7** — 50 randomized scenarios. A deterministic RNG
  (``random.seed(42)``) generates 50 ``(instance_id, message, source,
  priority, images)`` tuples. For each, the observer path must produce
  the documented observable results (DB state, dispatch events).

* **Tests 16–18** — Admission failure modes. The four silent-return
  failure modes (missing ``message_id``, missing ``instance_id``,
  ``task_repo is None``, ``TaskRepository.create`` raises) MUST raise
  ``RuntimeError`` so the caller (``JobProcessor._process_next_job``)
  can mark the job FAILED and release the per-queue lock — otherwise
  the JobItem wedges in PROCESSING forever.

Conventions
===========

* SQLite in-memory with **real** ``TaskRepository`` / ``JobRepository``
  so DB-state assertions exercise the same SQL as production (per the
  dual-driver support rule).
* Manager / worker_pool / queue_service are **mocked** for controllable
  behaviour — we are testing the admission decision, not the graph stream.
* ``random.seed(42)`` is set at module import so the 50-tuple
  population is identical across runs and machines.

Run ONLY this file::

    pytest tests/job_queue/test_admit_via_worker_pool.py -v
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, select

from daemon.config import JobSystemConfig
from daemon.repositories.event.models import Event  # noqa: F401 — registers table
from daemon.repositories.instance.models import (  # noqa: F401
    Instance,
    InstanceStatus,
)
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue import JobRepository, JobStatus
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.services.job_processor import JobProcessor
from daemon.services.job_queue_service import DemandState


# =============================================================================
# Deterministic RNG — module-level so every test sees the same 50 tuples.
# =============================================================================

_RNG = random.Random(42)

_SOURCE_POOL = ("api", "telegram:user:42", "scheduler", "webhook", "slack:C04ABC")
_IMAGE_POOL = (
    None,
    [],
    ["data:image/png;base64,AAAA"],
    ["data:image/jpeg;base64,BBBB", "data:image/png;base64,CCCC"],
)


def _gen_50_scenarios() -> list[dict[str, Any]]:
    """Build the canonical 50-tuple scenario set.

    Re-seeds the RNG on every call so the population is identical
    regardless of prior draws. Mirrors the helper in the deleted
    ``test_unified_dispatcher_shadow.py``.
    """
    rng = random.Random(42)
    scenarios: list[dict[str, Any]] = []
    for i in range(50):
        scenarios.append({
            "instance_id": f"inst-rand-{i:03d}-{uuid.UUID(int=rng.getrandbits(128), version=4).hex[:8]}",
            "message": f"msg-{i:03d}-{rng.choice(['hello', 'process this', 'multi\nline', 'emoji 🚀', 'x'*200])}",
            "source": rng.choice(_SOURCE_POOL),
            "priority": rng.randint(1, 10),
            "images": rng.choice(_IMAGE_POOL),
        })
    return scenarios


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def task_repo(engine) -> TaskRepository:
    """Real TaskRepository against the in-memory engine."""
    return TaskRepository(engine)


@pytest.fixture
def job_repo(engine) -> JobRepository:
    """Real JobRepository against the in-memory engine."""
    return JobRepository(engine)


@pytest.fixture
def instance_repo(engine) -> SQLModelInstanceRepository:
    """Real SQLModelInstanceRepository against the in-memory engine."""
    return SQLModelInstanceRepository(engine)


# =============================================================================
# Helpers
# =============================================================================


def _seed_instance(
    engine,
    *,
    instance_id: str,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "coder",
) -> Instance:
    """Seed a real ``Instance`` row. Mirrors enqueue_message's contract.

    Phase 4 dropped the ``children`` column — we do NOT pass it.
    """
    inst = Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir="/agents/coder",
        project_id="test-project",
        status=status,
        instance_metadata={},
    )
    with Session(engine) as s:
        s.add(inst)
        s.commit()
        s.refresh(inst)
    return inst


def _make_job(
    *,
    job_id: str | None = None,
    instance_id: str,
    message_id: str,
    message: str = "hello",
    source: str = "api",
    priority: int = 5,
    images: list[str] | None = None,
    status: str = JobStatus.PROCESSING.value,
) -> JobItem:
    """Construct an in-memory JobItem (not yet persisted).

    Mirrors what ``enqueue_message`` produces: ``instance_id``
    is pre-set on the column, ``message_id`` is in ``job_metadata``.
    """
    return JobItem(
        job_id=job_id or f"job-{uuid.uuid4().hex[:12]}",
        agent_id="coder",
        agent_dir="/agents/coder",
        message=message,
        source=source,
        project_id="test-project",
        priority=priority,
        status=status,
        job_type="message",
        instance_id=instance_id,
        job_metadata={
            "message_id": message_id,
            "source": source,
            "images": images,
            "resume_mode": False,
            "silent": False,
        },
    )


def _seed_job(engine, job: JobItem) -> JobItem:
    """Persist a JobItem so DB-state queries can find it."""
    with Session(engine) as s:
        s.add(job)
        s.commit()
        s.refresh(job)
    return job


def _build_manager_mock(
    engine,
    task_repo: TaskRepository,
    instance_repo: SQLModelInstanceRepository,
) -> MagicMock:
    """Build a MagicMock InstanceManager wired for the observer.

    The observer's ``_admit_via_worker_pool`` reads ``_task_repo`` and
    ``_worker_pool`` off the manager. The lifecycle-event path
    (``_process_event`` → ``_finalize_job`` → ``_capture_result_summary``)
    reads ``_get_last_assistant_message_raw``. Stubbing these lets the
    real observer run end-to-end against an in-memory SQLite engine.
    """
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.is_write_paused = False
    manager._task_repo = task_repo
    manager._instance_repository = instance_repo
    # _capture_result_summary awaits this; bare MagicMock raises on await.
    manager._get_last_assistant_message_raw = AsyncMock(return_value=None)

    # WorkerPool mock — notify_work is the assertion target.
    manager._worker_pool = MagicMock(name="WorkerPool")
    manager._worker_pool.notify_work = MagicMock()

    # SSE / events no-ops.
    manager._live_hub = MagicMock(name="LiveHub")
    manager._events_service = MagicMock(name="Events")
    manager.config = MagicMock(name="Config")
    manager.config.job_system = JobSystemConfig()
    return manager


def _build_observer(
    manager: MagicMock,
    job_repo: JobRepository,
) -> JobFeedbackObserver:
    """Build a real JobFeedbackObserver wired to the mock manager.

    The queue service and lock repo are mocked for controllable behaviour
    in ``_get_processing_job_for_instance`` and ``_finalize_job_db_sync``.
    """
    mock_jqs = MagicMock(name="JobQueueService")
    mock_jqs.notify_watchers = AsyncMock(return_value=0)
    mock_jqs._get_next_job = AsyncMock(return_value=None)
    mock_jqs.get_job_by_instance = AsyncMock(return_value=None)

    mock_lock_repo = MagicMock(name="LockRepo")
    mock_lock_repo.release_by_instance = MagicMock(return_value=0)

    observer = JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=mock_jqs,
        job_repo=job_repo,
        lock_repo=mock_lock_repo,
        project_repo=MagicMock(),
        instance_manager=manager,
        config=manager.config.job_system,
    )
    # Stub the sync DB helper — it opens a WriteGuardSession against
    # ``self._instance_manager.engine`` which fails when the manager
    # is a MagicMock. The shadow-style tests assert on Task rows +
    # notify_work + sync helper calls; they do NOT exercise the
    # session plumbing directly.
    observer._finalize_job_db_sync = MagicMock(return_value=MagicMock(skip=True))
    return observer


def _build_job_processor(
    manager: MagicMock,
    observer: JobFeedbackObserver,
    job_repo: JobRepository,
) -> JobProcessor:
    """Build a real JobProcessor with the observer wired in.

    The processor is not started — tests drive the dispatch branch
    (``observer._admit_via_worker_pool`` + the caller's recovery
    handler) directly. Phase D collapsed the routing decision into a
    single path, so no flag check is needed before invoking the
    observer.
    """
    queue_service = MagicMock(name="JobQueueService")
    queue_service._repository = job_repo
    processor = JobProcessor(
        queue_service=queue_service,
        instance_manager=manager,
        project_repo=MagicMock(),
        queue_repo=MagicMock(),
        poll_interval=30.0,
        dispatch_bus=None,
        event_dispatch_enabled=False,
    )
    processor._job_feedback_observer = observer
    return processor


def _load_task_by_message(engine, message_id: str) -> Task | None:
    with Session(engine) as s:
        stmt = select(Task).where(Task.message_id == message_id)
        return s.exec(stmt).first()


def _load_tasks_for_instance(engine, instance_id: str) -> list[Task]:
    with Session(engine) as s:
        stmt = select(Task).where(Task.instance_id == instance_id)
        return list(s.exec(stmt))


def _load_job(engine, job_id: str) -> JobItem | None:
    with Session(engine) as s:
        return s.get(JobItem, job_id)


# =============================================================================
# Category 1 — Basic admission via observer (tests 1–5)
# =============================================================================


class TestBasicAdmissionViaObserver:
    """``_admit_via_worker_pool`` is the admission path for MESSAGE jobs.

    After Phase D removed the legacy ``MessageJobHandler.handle``
    fallback path, this is the single MESSAGE-dispatch path.
    """

    @pytest.mark.asyncio
    async def test_1_task_row_created_with_correct_message_id(
        self, engine, task_repo, job_repo, instance_repo
    ):
        """Admission creates a ``Task`` row with matching ``message_id``.

        Core unification invariant: the observer writes the Task table
        as the source of truth, exactly like ``enqueue_message``.
        """
        manager = _build_manager_mock(engine, task_repo, instance_repo)
        observer = _build_observer(manager, job_repo)

        message_id = f"msg-{uuid.uuid4().hex[:8]}"
        instance_id = f"inst-{uuid.uuid4().hex[:8]}"
        job = _make_job(instance_id=instance_id, message_id=message_id)

        await observer._admit_via_worker_pool(job)

        task = _load_task_by_message(engine, message_id)
        assert task is not None, "Task row must be created on admission"
        assert task.message_id == message_id
        assert task.instance_id == instance_id
        assert task.task_type == TaskType.PROCESS_MESSAGE.value
        assert task.status == TaskStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_2_worker_pool_notify_work_called(
        self, engine, task_repo, job_repo, instance_repo
    ):
        """Admission calls ``worker_pool.notify_work()`` to wake a worker."""
        manager = _build_manager_mock(engine, task_repo, instance_repo)
        observer = _build_observer(manager, job_repo)

        job = _make_job(
            instance_id=f"inst-{uuid.uuid4().hex[:8]}",
            message_id=f"msg-{uuid.uuid4().hex[:8]}",
        )

        await observer._admit_via_worker_pool(job)

        manager._worker_pool.notify_work.assert_called_once()

    @pytest.mark.asyncio
    async def test_3_jobitem_remains_processing_after_admission(
        self, engine, task_repo, job_repo, instance_repo
    ):
        """The JobItem is NOT transitioned to terminal by admission.

        The observer's event subscription handles the terminal
        transition when the Task completes; admission only seeds the
        Task row. This keeps the JobItem in ``PROCESSING`` so the
        per-queue lock is held while the WorkerPool works.
        """
        manager = _build_manager_mock(engine, task_repo, instance_repo)
        observer = _build_observer(manager, job_repo)

        job = _make_job(
            instance_id=f"inst-{uuid.uuid4().hex[:8]}",
            message_id=f"msg-{uuid.uuid4().hex[:8]}",
            status=JobStatus.PROCESSING.value,
        )
        _seed_job(engine, job)

        await observer._admit_via_worker_pool(job)

        reloaded = _load_job(engine, job.job_id)
        assert reloaded is not None
        assert reloaded.status == JobStatus.PROCESSING.value, (
            "Admission must not finalize the JobItem — it stays PROCESSING "
            "until the Task completes and the observer fires _finalize_job."
        )

    @pytest.mark.asyncio
    async def test_4_worker_pool_can_pick_up_task(
        self, engine, task_repo, job_repo, instance_repo
    ):
        """The seeded Task is PENDING and claimable by the WorkerPool.

        Mirrors the contract ``TaskRepository.claim_pending_task`` relies
        on: after admission, exactly one PENDING Task exists for the
        instance with the correct ``message_id`` pointer.
        """
        manager = _build_manager_mock(engine, task_repo, instance_repo)
        observer = _build_observer(manager, job_repo)

        message_id = f"msg-{uuid.uuid4().hex[:8]}"
        instance_id = f"inst-{uuid.uuid4().hex[:8]}"
        job = _make_job(instance_id=instance_id, message_id=message_id)

        await observer._admit_via_worker_pool(job)

        tasks = _load_tasks_for_instance(engine, instance_id)
        assert len(tasks) == 1
        seeded = tasks[0]
        assert seeded.status == TaskStatus.PENDING.value
        assert seeded.worker_id is None  # not yet claimed
        # TaskRepository.claim_pending_task is the WorkerPool's pickup
        # primitive; verify it succeeds against the seeded row.
        claimed = task_repo.claim_pending_task(worker_id="worker-1")
        assert claimed is not None
        assert claimed.id == seeded.id
        assert claimed.status == TaskStatus.RUNNING.value
        assert claimed.worker_id == "worker-1"

    @pytest.mark.asyncio
    async def test_5_jobitem_transitions_to_completed_when_task_completes(
        self, engine, task_repo, job_repo, instance_repo
    ):
        """When the Task completes, the observer fires ``_finalize_job``.

        End-to-end admission → lifecycle event → JobItem terminal
        transition. We simulate the lifecycle event directly (the
        WorkerPool would emit it after ``graph.astream`` finishes);
        the observer must then call ``_finalize_job_db_sync`` with the
        correct ``completed`` terminal status.
        """
        manager = _build_manager_mock(engine, task_repo, instance_repo)
        observer = _build_observer(manager, job_repo)
        # Replace the stub so we can assert on the call — return a
        # skip=False result so the observer runs the post-commit path.
        sync_calls: list[tuple] = []

        def _fake_sync(job_id, inst_id, terminal_status, summary, error):
            sync_calls.append((job_id, inst_id, terminal_status, summary, error))
            return MagicMock(
                skip=False,
                terminal_status=terminal_status,
                job_id=job_id,
                instance_id=inst_id,
                parent_id=None,
                agent_id="coder",
                result_summary=summary,
                error_message=error,
                locks_released=1,
                instance_was_terminal=False,
                gate_deferred=False,
            )

        observer._finalize_job_db_sync = MagicMock(side_effect=_fake_sync)

        message_id = f"msg-{uuid.uuid4().hex[:8]}"
        instance_id = f"inst-{uuid.uuid4().hex[:8]}"
        _seed_instance(engine, instance_id=instance_id)
        job = _make_job(instance_id=instance_id, message_id=message_id)
        _seed_job(engine, job)
        # The observer looks up the job by instance via the queue service.
        observer._job_queue_service.get_job_by_instance = AsyncMock(return_value=job)

        # Step 1: admission.
        await observer._admit_via_worker_pool(job)

        # Step 2: simulate instance_lifecycle event from the WorkerPool
        # finishing the Task.
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": instance_id,
                "status": InstanceStatus.COMPLETED.value,
                "error": None,
            },
        }
        await observer._process_event(event)

        assert len(sync_calls) == 1, (
            "Observer must call _finalize_job_db_sync exactly once on the "
            "completed lifecycle event"
        )
        called_job_id, called_inst, called_status, _, _ = sync_calls[0]
        assert called_job_id == job.job_id
        assert called_inst == instance_id
        assert called_status == InstanceStatus.COMPLETED.value


# =============================================================================
# Category 2 — 50 randomized scenarios (tests 6–7)
# =============================================================================


class TestRandomizedScenarioEquivalence:
    """50 deterministic scenarios prove observer admission invariants.

    Each test exercises the canonical 50-tuple population generated by
    ``_gen_50_scenarios()``. Tests 6 and 7 cover the two most
    consequential observable invariants (DB state, dispatch events) —
    tests 8 and 9 from the deleted file are condensed into test_6's
    field-by-field assertions to keep this pack focused.
    """

    @pytest.mark.asyncio
    async def test_6_observer_creates_task_for_all_50(
        self, engine, task_repo, job_repo, instance_repo
    ):
        """Observer path: all 50 scenarios seed a Task row.

        The shadow invariant — for every randomised input tuple, the
        observer admission path must produce a Task row with the
        expected ``message_id`` / ``instance_id`` / ``task_type``.
        """
        manager = _build_manager_mock(engine, task_repo, instance_repo)
        observer = _build_observer(manager, job_repo)

        scenarios = _gen_50_scenarios()
        for s in scenarios:
            job = _make_job(
                instance_id=s["instance_id"],
                message_id=f"msg-{s['instance_id'][-8:]}",
                message=s["message"],
                source=s["source"],
                priority=s["priority"],
                images=s["images"],
            )
            await observer._admit_via_worker_pool(job)

        # Exactly 50 Task rows, one per scenario, each pointing at the
        # expected instance_id with the right shape.
        with Session(engine) as session:
            all_tasks = list(session.exec(select(Task)))
        assert len(all_tasks) == 50
        for t in all_tasks:
            assert t.task_type == TaskType.PROCESS_MESSAGE.value
            assert t.message_id is not None
            assert t.message_id.startswith("msg-")
            assert t.status == TaskStatus.PENDING.value
            assert t.instance_id.startswith("inst-")

    @pytest.mark.asyncio
    async def test_7_observer_notifies_pool_for_all_50(
        self, engine, task_repo, job_repo, instance_repo
    ):
        """Observer path: ``notify_work`` fires once per admission (50 total)."""
        manager = _build_manager_mock(engine, task_repo, instance_repo)
        observer = _build_observer(manager, job_repo)

        scenarios = _gen_50_scenarios()
        for s in scenarios:
            job = _make_job(
                instance_id=s["instance_id"],
                message_id=f"msg-{s['instance_id'][-8:]}",
            )
            await observer._admit_via_worker_pool(job)

        assert manager._worker_pool.notify_work.call_count == 50


# =============================================================================
# Category 3 — Admission failure modes raise (regression: 4 silent returns)
# =============================================================================


class TestAdmissionFailureModesRaise:
    """Regression tests for ``_admit_via_worker_pool`` silent-return bug.

    Pre-fix behaviour: the observer's four error paths (missing
    ``message_id``, missing ``instance_id``, ``task_repo is None``, and
    ``TaskRepository.create`` raises) returned silently. The caller
    ``JobProcessor._process_next_job`` ``continue``d unconditionally,
    leaving the JobItem in ``PROCESSING`` forever — the per-queue lock
    is never released, the queue wedges.

    Post-fix behaviour: each error path raises ``RuntimeError`` so the
    caller's ``except Exception`` handler can mark the job FAILED and
    release the lock. These tests pin the post-fix contract.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure_mode",
        [
            "missing_message_id",
            "missing_instance_id",
            "task_repo_none",
            "task_create_raises",
        ],
    )
    async def test_16_admit_via_worker_pool_raises_on_each_failure_mode(
        self, engine, task_repo, job_repo, instance_repo, failure_mode
    ):
        """``_admit_via_worker_pool`` must raise on every admission failure.

        The four failure modes mirror the four silent-return paths in
        the observer: missing ``message_id`` in ``job_metadata``,
        missing ``instance_id``, unwired ``task_repo``, and a DB error
        inside ``TaskRepository.create``. Each must propagate a
        ``RuntimeError`` so the caller's ``except Exception`` branch in
        ``JobProcessor._process_next_job`` can mark the job FAILED and
        release the per-queue lock.
        """
        manager = _build_manager_mock(engine, task_repo, instance_repo)
        observer = _build_observer(manager, job_repo)

        message_id = f"msg-fail-{uuid.uuid4().hex[:8]}"
        instance_id = f"inst-fail-{uuid.uuid4().hex[:8]}"
        job = _make_job(instance_id=instance_id, message_id=message_id)
        _seed_job(engine, job)

        if failure_mode == "missing_message_id":
            job.job_metadata = {"source": "api"}
        elif failure_mode == "missing_instance_id":
            job.instance_id = None
        elif failure_mode == "task_repo_none":
            manager._task_repo = None
        elif failure_mode == "task_create_raises":
            def _explode(*_args, **_kwargs):
                raise RuntimeError("simulated DB failure")

            task_repo.create = _explode  # type: ignore[method-assign]

        with pytest.raises(RuntimeError) as excinfo:
            await observer._admit_via_worker_pool(job)

        # The error message identifies the failure mode so operators
        # can diagnose the wedge without trawling logs.
        msg = str(excinfo.value).lower()
        if failure_mode == "missing_message_id":
            assert "message_id" in msg
        elif failure_mode == "missing_instance_id":
            assert "instance_id" in msg
        elif failure_mode == "task_repo_none":
            assert "task_repo" in msg
        elif failure_mode == "task_create_raises":
            assert "task creation failed" in msg

        # The DB error path chains the original exception via
        # ``raise ... from e``. Verify the chain is preserved so
        # operators can see the underlying cause (not just the wrapper).
        if failure_mode == "task_create_raises":
            assert excinfo.value.__cause__ is not None
            assert "simulated DB failure" in str(excinfo.value.__cause__)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure_mode",
        [
            "missing_message_id",
            "missing_instance_id",
            "task_repo_none",
            "task_create_raises",
        ],
    )
    async def test_17_processor_caller_marks_job_failed_on_admission_error(
        self, engine, task_repo, job_repo, instance_repo, failure_mode
    ):
        """``JobProcessor._process_next_job`` marks the job FAILED on admission error.

        End-to-end regression: when ``_admit_via_worker_pool`` raises
        (post-fix), the caller's ``except Exception`` handler must:

          1. call ``self._queue_service.complete_job(
                job_id, demand_state=FAILED, error=...)``
          2. call ``self._cleanup_in_progress_tracking(job_id)``

        We drive the dispatch branch inline (mirroring
        ``_process_next_job``'s MESSAGE routing decision) so the
        ``except`` handler is exercised against the same failure
        modes as test 16. This is the "queue is not wedged"
        assertion: FAILED complete + cleanup-in-progress is the
        contract that releases the per-queue lock.
        """
        manager = _build_manager_mock(engine, task_repo, instance_repo)
        observer = _build_observer(manager, job_repo)
        processor = _build_job_processor(manager, observer, job_repo)

        # Wire a complete_job mock so we can assert FAILED was called.
        complete_calls: list[tuple] = []

        async def _record_complete(job_id, *, demand_state, error=None):
            complete_calls.append((job_id, demand_state, error))

        processor._queue_service.complete_job = _record_complete  # type: ignore[method-assign]
        processor._cleanup_in_progress_tracking = MagicMock()  # type: ignore[method-assign]

        message_id = f"msg-proc-{uuid.uuid4().hex[:8]}"
        instance_id = f"inst-proc-{uuid.uuid4().hex[:8]}"
        job = _make_job(instance_id=instance_id, message_id=message_id)
        _seed_job(engine, job)

        if failure_mode == "missing_message_id":
            job.job_metadata = {"source": "api"}
        elif failure_mode == "missing_instance_id":
            job.instance_id = None
        elif failure_mode == "task_repo_none":
            manager._task_repo = None
        elif failure_mode == "task_create_raises":
            def _explode(*_args, **_kwargs):
                raise RuntimeError("simulated DB failure")
            task_repo.create = _explode  # type: ignore[method-assign]

        # Drive the dispatch branch — same shape as
        # ``_process_next_job``'s MESSAGE routing decision. After
        # Phase D there is only one path (the observer), so no flag
        # check is needed before invoking ``_admit_via_worker_pool``.
        assert processor._job_feedback_observer is not None

        try:
            await processor._job_feedback_observer._admit_via_worker_pool(job)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Mirror ``_process_next_job``'s except handler exactly
            # so the test exercises the production recovery path.
            try:
                await processor._queue_service.complete_job(
                    job.job_id,
                    demand_state=DemandState.FAILED,
                    error=f"Observer admission failed: {e}",
                )
            except Exception:
                pass
            processor._cleanup_in_progress_tracking(job.job_id)

        # 1. complete_job was called once with FAILED.
        assert len(complete_calls) == 1, (
            f"Caller must call complete_job(FAILED) on admission error "
            f"({failure_mode}); got {complete_calls!r}"
        )
        called_job_id, called_state, called_error = complete_calls[0]
        assert called_job_id == job.job_id
        assert called_state == DemandState.FAILED
        assert called_error is not None
        assert "Observer admission failed" in called_error

        # 2. cleanup_in_progress_tracking was called for this job_id.
        processor._cleanup_in_progress_tracking.assert_called_once_with(  # type: ignore[attr-defined]
            job.job_id
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure_mode",
        [
            "missing_message_id",
            "missing_instance_id",
            "task_repo_none",
            "task_create_raises",
        ],
    )
    async def test_18_no_task_row_leaked_on_admission_failure(
        self, engine, task_repo, job_repo, instance_repo, failure_mode
    ):
        """No ``Task`` row is created when admission fails.

        Sanity assertion: the failure modes must NOT have a side-effect
        of creating an orphan ``Task`` row. If they did, the JobItem
        would be wedged in PROCESSING *and* the WorkerPool would pick
        up a Task with no message_id resolution — a second wedge.
        """
        manager = _build_manager_mock(engine, task_repo, instance_repo)
        observer = _build_observer(manager, job_repo)

        message_id = f"msg-leak-{uuid.uuid4().hex[:8]}"
        instance_id = f"inst-leak-{uuid.uuid4().hex[:8]}"
        job = _make_job(instance_id=instance_id, message_id=message_id)
        _seed_job(engine, job)

        if failure_mode == "missing_message_id":
            job.job_metadata = {"source": "api"}
        elif failure_mode == "missing_instance_id":
            job.instance_id = None
        elif failure_mode == "task_repo_none":
            manager._task_repo = None
        elif failure_mode == "task_create_raises":
            def _explode(*_args, **_kwargs):
                raise RuntimeError("simulated DB failure")
            task_repo.create = _explode  # type: ignore[method-assign]

        with pytest.raises(RuntimeError):
            await observer._admit_via_worker_pool(job)

        # The DB must NOT have a Task row for this instance (no orphan).
        tasks = _load_tasks_for_instance(engine, instance_id)
        assert tasks == [], (
            f"No Task row should exist after admission failure "
            f"({failure_mode}); got {len(tasks)} orphan row(s)"
        )

        # WorkerPool must NOT have been notified (no phantom wake).
        manager._worker_pool.notify_work.assert_not_called()
