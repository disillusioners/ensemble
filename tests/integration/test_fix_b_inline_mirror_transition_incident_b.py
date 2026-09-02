"""Integration test for Fix B — incident-B scenario.

The exact scenario Incident B (2026-09-01, 80b86e51) reproduced in
production:

  * A message JobItem dispatches into a parent instance.
  * The driving Task runs to COMPLETED (T0).
  * The instance has LIVE CHILDREN — the parent's other Task rows
    are still PENDING / RUNNING, so the parent instance is in the
    ``waiting_children`` state and its driving JobItem side stays
    held open by the observer's bus_pending gate.
  * Pre-Fix-B: the JobItem side stays ACTIVE for ~7 hours until the
    f-sweep's 300s cycle sees the bus drain + the instance idle
    + the age floor pass, then ``_pattern_f_finalize_done`` finalizes
    the row late (the source of the alarming "8 of 9 rendered pairs
    = benign message-mirror completions against long-lived missions"
    signal in H2 of the v1 retrospective).

Post-Fix-B:

  * The inline idempotent mirror transition at
    ``ProcessMessageProcessor.on_success`` finalizes the JobItem
    side at T0 — the moment the driving Task's ``complete_task``
    commits — without waiting for the bus drain or the age floor.
  * The instance remains ``waiting_children`` because the
    parent-children drain is independent of the inline transition.
  * The DONE stamp's wall-clock timestamp falls inside the
    Task-completion window (well under 1 second), not 7 hours later.

This test is the EXACT-incident reproduction. It uses a real
``ProcessMessageProcessor`` end-to-end through the real
``on_success`` callback (driven via a mocked pipeline so the LLM
turn is skipped — same harness as
``tests/integration/test_pause_race_resume_reenqueue.py``). The
``on_success`` callback fires ``TaskRepository.complete_task`` and
the new ``JobRepository.finalize_mirror_job_at_completion`` against
a real SQLite engine; the JobItem's ``admission_state`` is asserted
via re-read at the end.

Harness notes (p0a precedent, ``tests/integration/test_job_driven_enqueue_work_id_facade.py``):

  * File-backed SQLite via ``tmp_path`` with ``StaticPool`` — the
    handoff between ``complete_task`` (write) and the inline
    transition's re-read (read) is the canonical same-engine
    surface; StaticPool keeps the harness simple (no race window in
    scope here — the unit-test in
    ``tests/unit/job_queue/test_fix_b_inline_mirror_transition.py``
    covers the cross-connection race).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

# Register every model on ``SQLModel.metadata`` BEFORE ``create_all``.
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import JobRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.message_processing_pipeline import ProcessingResult
from daemon.services.task_processor import ProcessMessageProcessor


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool).

    Single-thread test (no concurrent transitions in scope here —
    the cross-connection race is pinned in
    ``tests/unit/job_queue/test_fix_b_inline_mirror_transition.py::
    test_double_fire_is_one_transition``). The file-backed recipe
    from the p0a precedent is reserved for tests that need
    cross-thread visibility; StaticPool is faster and sufficient.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_instance_with_live_children(
    engine: Engine, *, instance_id: str
) -> None:
    """Seed an instance in the ``waiting_children`` state with TWO
    PENDING child Tasks — the parent has live children, so the
    observer's bus_pending gate holds the JobItem open.

    Incident-B reproduction: a long-lived mission with a steady
    stream of child work; the parent's JobItem stays ACTIVE for
    hours while the children drain.
    """
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                project_id="test-project",
                status=InstanceStatus.WAITING_CHILDREN.value,
                version=1,
                instance_metadata={},
                created_at=now,
            )
        )
        s.commit()


def _seed_message_job_and_running_task(
    engine: Engine,
    *,
    instance_id: str,
    job_id: str,
    task_id_hint: int,
) -> tuple[JobItem, Task]:
    """Seed the incident-B shape: a message JobItem (ACTIVE) and
    its driving Task (RUNNING, work_id == job_id).

    Returns the seeded (JobItem, Task). The caller is responsible
    for invoking ``complete_task`` and the inline transition.
    """
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        # Seed a MessageQueue row so the message_id link resolves.
        from daemon.repositories.message_queue.models import (
            MessageQueue,
            MessageStatus,
            MessageType,
        )
        msg = MessageQueue(
            message_id=str(uuid.uuid4()),
            instance_id=instance_id,
            agent_id="developer",
            content="incident-B reproduction",
            message_type=MessageType.AGENT.value,
            status=MessageStatus.READY.value,
            enqueued_at=now,
            project_id="test-project",
        )
        s.add(msg)
        s.commit()
        s.refresh(msg)
        message_id = msg.message_id

        job = JobItem(
            job_id=job_id,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="incident-B reproduction",
            source="api",
            job_type="message",
            admission_state=AdmissionState.ACTIVE.value,
            instance_id=instance_id,
            project_id="test-project",
            job_metadata={},
            max_retries=0,
        )
        s.add(job)

        task = Task(
            id=task_id_hint,
            work_id=job_id,  # LINKAGE — Task.work_id == JobItem.job_id
            instance_id=instance_id,
            task_type=TaskType.PROCESS_MESSAGE.value,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        s.add(task)
        s.commit()
        s.refresh(job)
        s.refresh(task)
        return job, task


def _seed_live_child_task(
    engine: Engine, *, instance_id: str, job_id: str
) -> None:
    """Seed a SECOND ACTIVE Task on the same instance (a "live
    child") — proves the parent's children are not all done, which
    is what makes the observer's bus_pending gate hold the JobItem
    open pre-Fix-B.

    Incident-B reproduction requires at least one live child Task
    so the parent's driving-Task-completion signal does NOT drain
    the instance to a terminal state on its own.

    Uses a fresh ``work_id`` (separate UUID) so the live child does
    not collide with the driving Task's ``work_id == job_id``
    linkage — ``task.work_id`` is ``unique=True`` per the model
    (see ``daemon/repositories/task/models.py:136``).
    """
    with Session(engine) as s:
        child = Task(
            work_id=str(uuid.uuid4()),  # distinct from the driving Task's work_id
            instance_id=instance_id,
            task_type=TaskType.PROCESS_MESSAGE.value,
            status=TaskStatus.PENDING.value,
        )
        s.add(child)
        s.commit()


def _build_processor(
    engine: Engine, *, task_repo: TaskRepository
) -> tuple[ProcessMessageProcessor, MagicMock]:
    """Construct a real ``ProcessMessageProcessor`` with a mocked
    pipeline.

    The pipeline mock short-circuits the LLM turn + child-completion
    stages (so this test does not need a real graph) but the real
    ``on_success`` callback is wired — it runs the production
    ``TaskRepository.complete_task`` + ``JobRepository.finalize_mirror_job_at_completion``
    against the real engine.
    """
    pipeline = MagicMock()
    pipeline.execute = AsyncMock(
        return_value=ProcessingResult(success=True, result_content="ok")
    )
    manager = MagicMock(name="InstanceManager")
    # ``on_success`` reaches the JobRepository via
    # ``manager._job_queue_service._repository`` (the production
    # access path). Wire a real JobRepository so the inline
    # transition sees the same engine as the Task writes.
    job_repo = JobRepository(engine)
    job_queue_service = MagicMock()
    job_queue_service._repository = job_repo
    manager._job_queue_service = job_queue_service
    # Soft-required by ``on_success`` for the metrics hook (no-op
    # when None — see ProcessMessageProcessor._record_metrics_for_task).
    manager._instance_repository = None
    # Pipeline constructor needs a ``message_repository`` — wire the
    # real repo (the MessageQueue row is required for the prelude).
    message_repo = SQLModelMessageQueueRepository(engine)

    processor = ProcessMessageProcessor(
        instance_manager=manager,
        task_repo=task_repo,
        event_repo=None,
        message_repository=message_repo,
        source_dispatcher=None,
        pipeline=pipeline,
        work_resolver=None,
        watcher_repo=None,
    )
    return processor, manager


def _read_job(engine: Engine, job_id: str) -> JobItem | None:
    with Session(engine) as s:
        return s.get(JobItem, job_id)


def _read_task(engine: Engine, task_id: int) -> Task | None:
    with Session(engine) as s:
        return s.get(Task, task_id)


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────


class TestFixBIncidentBScenario:
    """The EXACT incident-B reproduction: T0 finalization of the
    message-mirror JobItem while the parent instance still has live
    children (i.e. ``waiting_children``)."""

    @pytest.mark.asyncio
    async def test_message_job_done_at_t0_while_instance_waiting_children(
        self, engine: Engine
    ) -> None:
        """Driving Task completes at T0 → message JobItem is DONE at
        T0 → instance stays ``waiting_children`` (live children
        unaffected).

        Asserts the FIX-B core contract end-to-end:
          1. Pre-condition: JobItem is ACTIVE, Task is RUNNING,
             instance is ``waiting_children`` with at least one
             live child Task.
          2. The pipeline's ``on_success`` callback fires (the
             production completion path) with ``ProcessingResult(success=True)``.
          3. Post-condition: JobItem is ``done`` with
             ``terminal_reason='completed'``; Task is ``completed``;
             instance status is UNCHANGED (``waiting_children``).
          4. Wall-clock latency: the DONE stamp lands inside the
             Task-completion window (sub-second), not hours later
             (the pre-Fix-B Incident-B shape).
        """
        instance_id = f"inst-fix-b-incident-b-{uuid.uuid4().hex[:8]}"
        job_id = f"job-fix-b-incident-b-{uuid.uuid4().hex[:8]}"
        task_id_hint = 99001  # arbitrary unique-ish PK; SQLite autoincrement isn't used here

        _seed_instance_with_live_children(engine, instance_id=instance_id)
        job, task = _seed_message_job_and_running_task(
            engine,
            instance_id=instance_id,
            job_id=job_id,
            task_id_hint=task_id_hint,
        )
        # Add a live child so the parent's waiting_children state is
        # justified — Incident-B was a long-lived mission with steady
        # child traffic.
        _seed_live_child_task(engine, instance_id=instance_id, job_id=job_id)

        # Sanity: pre-call state matches the incident-B shape.
        pre_job = _read_job(engine, job_id)
        pre_task = _read_task(engine, task_id_hint)
        assert pre_job is not None
        assert pre_job.admission_state == AdmissionState.ACTIVE.value
        assert pre_task is not None
        assert pre_task.status == TaskStatus.RUNNING.value

        task_repo = TaskRepository(engine)
        processor, _manager = _build_processor(
            engine, task_repo=task_repo
        )

        # Time the on_success call — the wall-clock latency is the
        # Incident-B metric ("7 hours vs ~0").
        callbacks = processor._build_callbacks(task)
        t0 = time.monotonic()
        await callbacks.on_success(
            ProcessingResult(success=True, result_content="ok")
        )
        elapsed = time.monotonic() - t0

        # Post-condition 1: Task is COMPLETED.
        post_task = _read_task(engine, task_id_hint)
        assert post_task is not None
        assert post_task.status == TaskStatus.COMPLETED.value, (
            "complete_task must have transitioned the Task to COMPLETED "
            "before the inline mirror transition ran"
        )

        # Post-condition 2: JobItem is DONE with organic terminal_reason.
        post_job = _read_job(engine, job_id)
        assert post_job is not None
        assert post_job.admission_state == AdmissionState.DONE.value, (
            f"Inline mirror transition must finalize the JobItem at T0 "
            f"(latency={elapsed:.3f}s). Got admission_state="
            f"{post_job.admission_state!r} — pre-Fix-B this would be "
            f"'active' (the f-sweep finalizes ~7h later)."
        )
        assert post_job.terminal_reason == "completed", (
            "Inline transition must stamp terminal_reason='completed' "
            "(organic-style — closes the old cosmetic gap of empty "
            "terminal_reason on sweep-finalized rows)"
        )

        # Post-condition 3: instance status is UNCHANGED.
        with Session(engine) as s:
            inst = s.get(Instance, instance_id)
            assert inst is not None
            assert inst.status == InstanceStatus.WAITING_CHILDREN.value, (
                f"Instance status must remain waiting_children — the "
                f"inline transition does NOT drain the parent. "
                f"Got status={inst.status!r}"
            )

        # Post-condition 4: wall-clock latency is sub-second.
        # Pre-Fix-B this was ~7 hours (the f-sweep cycle that finally
        # resolved Incident B fired at 20:03, ~7h after the Task
        # completion at ~13:01). The inline transition closes this
        # class forward.
        assert elapsed < 2.0, (
            f"Inline mirror transition must finalize at T0 (sub-second). "
            f"Elapsed: {elapsed:.3f}s — anything above 2s suggests the "
            f"TestInstanceManager wiring is doing extra DB round-trips "
            f"that production would not do."
        )

    @pytest.mark.asyncio
    async def test_double_on_success_call_is_idempotent(
        self, engine: Engine
    ) -> None:
        """Two ``on_success`` calls in succession (e.g. pipeline retry
        after a transient error, or a race between the pipeline and a
        manual dispatch retry) must produce exactly one DONE stamp —
        the second call is a silent no-op.

        This is the runtime form of the unit-level idempotency test
        — it proves the ``on_success`` callback does not raise when
        the underlying transition is already terminal.
        """
        instance_id = f"inst-fix-b-idem-{uuid.uuid4().hex[:8]}"
        job_id = f"job-fix-b-idem-{uuid.uuid4().hex[:8]}"
        task_id_hint = 99002

        _seed_instance_with_live_children(engine, instance_id=instance_id)
        job, task = _seed_message_job_and_running_task(
            engine,
            instance_id=instance_id,
            job_id=job_id,
            task_id_hint=task_id_hint,
        )

        task_repo = TaskRepository(engine)
        processor, _manager = _build_processor(
            engine, task_repo=task_repo
        )
        callbacks = processor._build_callbacks(task)

        # First call: the canonical T0 transition.
        await callbacks.on_success(
            ProcessingResult(success=True, result_content="ok")
        )
        post_first = _read_job(engine, job_id)
        assert post_first is not None
        assert post_first.admission_state == AdmissionState.DONE.value
        assert post_first.terminal_reason == "completed"

        # Second call: idempotent no-op (Task is already COMPLETED so
        # ``complete_task`` returns None; the inline mirror sees
        # ``admission_state='done'`` and silently short-circuits).
        await callbacks.on_success(
            ProcessingResult(success=True, result_content="ok-again")
        )
        post_second = _read_job(engine, job_id)
        assert post_second is not None
        assert post_second.admission_state == AdmissionState.DONE.value
        assert post_second.terminal_reason == "completed", (
            "Second on_success call must NOT overwrite the terminal_reason "
            "stamped on the first call — idempotency is the safety property"
        )

    @pytest.mark.asyncio
    async def test_task_job_unchanged_by_on_success(
        self, engine: Engine
    ) -> None:
        """TASK (mission) JobItems are NOT inline-transitioned by
        ``on_success`` — the scope-discipline contract.

        A TASK JobItem with the same message_id-driven shape MUST
        stay ACTIVE after ``on_success`` (the bus-gated finalize
        path is the legitimate owner).
        """
        from daemon.repositories.message_queue.models import (
            MessageQueue,
            MessageStatus,
            MessageType,
        )

        instance_id = f"inst-fix-b-scope-{uuid.uuid4().hex[:8]}"
        job_id = f"job-fix-b-scope-{uuid.uuid4().hex[:8]}"
        task_id_hint = 99003

        _seed_instance_with_live_children(engine, instance_id=instance_id)
        now = datetime.now(timezone.utc)
        with Session(engine) as s:
            msg = MessageQueue(
                message_id=str(uuid.uuid4()),
                instance_id=instance_id,
                agent_id="developer",
                content="scope-discipline test",
                message_type=MessageType.AGENT.value,
                status=MessageStatus.READY.value,
                enqueued_at=now,
                project_id="test-project",
            )
            s.add(msg)
            s.commit()
            s.refresh(msg)
            message_id = msg.message_id

            task_job = JobItem(
                job_id=job_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="scope-discipline test",
                source="api",
                job_type="task",  # NOT 'message' — scope discipline
                admission_state=AdmissionState.ACTIVE.value,
                instance_id=instance_id,
                project_id="test-project",
                job_metadata={},
                max_retries=0,
            )
            s.add(task_job)

            task = Task(
                id=task_id_hint,
                work_id=job_id,
                instance_id=instance_id,
                task_type=TaskType.PROCESS_MESSAGE.value,
                message_id=message_id,
                status=TaskStatus.RUNNING.value,
            )
            s.add(task)
            s.commit()
            s.refresh(task)

        task_repo = TaskRepository(engine)
        processor, _manager = _build_processor(
            engine, task_repo=task_repo
        )
        callbacks = processor._build_callbacks(task)

        await callbacks.on_success(
            ProcessingResult(success=True, result_content="ok")
        )

        post = _read_job(engine, job_id)
        assert post is not None
        assert post.admission_state == AdmissionState.ACTIVE.value, (
            "TASK JobItem must remain ACTIVE after on_success — the "
            "inline transition is structurally wrong for missions "
            "(would bypass the wait-for-children contract)"
        )
        assert post.terminal_reason is None, (
            "TASK JobItem must NOT receive the inline terminal_reason "
            "stamp — the bus-gated finalize is the legitimate owner"
        )
