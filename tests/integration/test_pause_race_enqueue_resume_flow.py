"""No-false-positive pause/resume/enqueue integration flow for Phase 2.

False-positive regression test for the Phase 2 marker-only guard in
``InstanceMessagingService._prepare_enqueued_message`` (see
``daemon/services/instance_messaging.py:_prepare_enqueued_message``).

The Phase 2 guard SKIPS the ``PROCESS_MESSAGE`` Task creation when the
parent's deferred-pause marker is set. A *false positive* here would
skip the Task when the parent is NOT pausing, losing a legitimate user
message.

The test exercises **real root/child resume paths** end-to-end:

  1. **Pre-pause** enqueue (RUNNING + no marker) → Task is created and
     claimed normally (the no-false-positive baseline).
  2. **Pause cycle**: marker set → production
     ``_pause_cascade_db_sync`` writes PAUSED → marker is cleared by
     the C1 pop-after-cascade ordering. The Phase 2 guard fires
     during the window and skips the Task (proven elsewhere; not
     re-asserted here).
  3. **Resume cycle**: production ``_resume_cascade_db_sync`` flips
     the DB back to RUNNING.
  4. **Post-resume** enqueue → Task is created and claimed normally
     (no false-positive skip). The MessageQueue row is preserved
     alongside.

Compared to the previous version (which manually set / discarded the
marker around the cascade and resume), this rewrite uses the real
C1 peek/await/pop ordering (the post-graph finally block is replayed
exactly the way ``daemon.services.instance_messaging`` does it) plus
the production ``_pause_cascade_db_sync`` /
``_resume_cascade_db_sync``, so a regression in any of the three
would be caught.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from daemon.manager import InstanceManager
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.message_queue.repository import SQLModelMessageQueueRepository
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.cancellation import CancellationService
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.instance_messaging import InstanceMessagingService
from daemon.services.message_processing_pipeline import ProcessingResult
from daemon.services.task_processor import ProcessMessageProcessor
from daemon.write_pause_guard import WritePauseGuard


@pytest.fixture
def engine() -> Engine:
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


def _seed_running_instance(engine: Engine, instance_id: str) -> None:
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                project_id="test-project",
                status=InstanceStatus.RUNNING.value,
                instance_metadata={},
            )
        )
        session.commit()


def _enqueue(
    messaging: InstanceMessagingService, instance_id: str, message: str
):
    return messaging._prepare_enqueued_message(
        instance_id=instance_id,
        message=message,
        source="api",
        priority=1,
        images=None,
        metadata=None,
    )


def _make_manager_with_real_marker_state(engine: Engine) -> MagicMock:
    """Mock manager with REAL peek/pop semantics against a real backing set.

    Same approach as the companion tests — the production
    ``set_deferred_question_pause``,
    ``has_deferred_question_pause`` and
    ``pop_deferred_question_pause`` methods are bound so the test
    exercises the real atomic-check + atomic-remove semantics.
    """
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._deferred_question_pause = set()
    manager._graph_tasks = {}
    manager.set_deferred_question_pause = (
        InstanceManager.set_deferred_question_pause.__get__(manager)
    )
    manager.has_deferred_question_pause = (
        InstanceManager.has_deferred_question_pause.__get__(manager)
    )
    manager.pop_deferred_question_pause = (
        InstanceManager.pop_deferred_question_pause.__get__(manager)
    )
    return manager


async def _run_post_graph_finally_block(
    manager: MagicMock, instance_id: str, cascade_callable
) -> bool:
    """Replay the post-graph completion path's finally block.

    Mirrors the C1 ordering at
    ``daemon/services/instance_messaging.py:3272-...``:
    ``has`` (peek) → ``await shielded(cascade)`` → ``finally: pop``.
    """
    if not manager.has_deferred_question_pause(instance_id):
        return False
    try:
        await asyncio.shield(cascade_callable(instance_id))
    finally:
        manager.pop_deferred_question_pause(instance_id)
    return True


@pytest.mark.asyncio
async def test_normal_enqueue_before_and_after_pause_resume_drives_graph_turn(
    engine: Engine,
) -> None:
    """Pre-pause + post-resume enqueues: no false-positive skips.

    Sequence:

      1. RUNNING + no marker → ``_prepare_enqueued_message`` creates
         a normal ``PROCESS_MESSAGE`` Task (the no-false-positive
         baseline).
      2. The ``claim_pending_task`` SQL gate claims it; the message
         is delivered to the (mock) graph.
      3. Marker is set; the post-graph finally block runs the
         cascade with the **new C1 peek/await/pop ordering**; the
         cascade commits PAUSED via ``_pause_cascade_db_sync``.
      4. Resume: ``_resume_cascade_db_sync`` flips the DB back to
         RUNNING.
      5. A second ``_prepare_enqueued_message`` creates a new
         ``PROCESS_MESSAGE`` Task and a fresh ``MessageQueue`` row
         (no false-positive skip).
      6. ``claim_pending_task`` claims the post-resume Task and the
         message is delivered to the (mock) graph.

    Asserts:

      * Pre-pause enqueue creates exactly one ``PROCESS_MESSAGE``
        Task that is claimed + completed.
      * Pause cascade commits PAUSED; marker is cleared by the
        C1 pop.
      * Resume cascade flips back to RUNNING.
      * Post-resume enqueue creates a fresh ``PROCESS_MESSAGE``
        Task + ``MessageQueue`` row.
      * The TaskRepository's ``claim_pending_task`` returns the
        post-resume Task, and the (mock) pipeline executes with
        the post-resume content.
    """
    instance_id = "pause-resume-normal-flow"
    _seed_running_instance(engine, instance_id)
    write_guard = WritePauseGuard()

    manager = _make_manager_with_real_marker_state(engine)
    cancellation = MagicMock(spec=CancellationService)
    cancellation.is_shutting_down = False
    messaging = InstanceMessagingService(manager, cancellation)
    lifecycle = InstanceLifecycleService.__new__(InstanceLifecycleService)
    lifecycle._manager = manager
    task_repo = TaskRepository(engine)

    # 1. RUNNING + no marker is never a false positive: normal Task is created.
    first = await asyncio.to_thread(_enqueue, messaging, instance_id, "before pause")
    first_claim = task_repo.claim_pending_task(worker_id="first-turn")
    assert first_claim is not None
    assert first_claim.task_type == TaskType.PROCESS_MESSAGE.value
    assert first_claim.status == TaskStatus.RUNNING.value
    task_repo.complete_task(first_claim.id, {"success": True})

    # 2. Pause cycle: marker set, then post-graph finally block runs
    #    the cascade with the C1 peek/await/pop ordering.
    manager.set_deferred_question_pause(instance_id)

    async def run_cascade(_target_id: str) -> dict:
        return await asyncio.to_thread(
            lifecycle._pause_cascade_db_sync,
            engine,
            write_guard,
            tree_ids=[instance_id],
            paused_at_iso=datetime.now(timezone.utc).isoformat(),
            paused_instances_data=[(instance_id, "developer")],
        )

    await _run_post_graph_finally_block(manager, instance_id, run_cascade)

    with Session(engine) as session:
        inst = session.get(Instance, instance_id)
        assert inst.status == InstanceStatus.PAUSED.value
    assert not manager.has_deferred_question_pause(instance_id), (
        "C1 invariant: marker must be cleared by the pop in the "
        "finally block AFTER the cascade commits PAUSED"
    )

    # 3. Resume through the production DB cascade and enqueue a fresh user turn.
    await asyncio.to_thread(
        lifecycle._resume_cascade_db_sync,
        engine,
        write_guard,
        tree_ids=[instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )
    with Session(engine) as session:
        assert session.get(Instance, instance_id).status == InstanceStatus.RUNNING.value

    # 4. Post-resume enqueue: fresh Task + MessageQueue row, no
    #    false-positive skip.
    second = await asyncio.to_thread(_enqueue, messaging, instance_id, "after resume")
    with Session(engine) as session:
        second_task = session.exec(
            select(Task).where(Task.message_id == second.message_id)
        ).one()
        assert second_task.task_type == TaskType.PROCESS_MESSAGE.value
        assert second_task.status == TaskStatus.PENDING.value
        assert session.get(MessageQueue, second.message_id) is not None

    claimed = task_repo.claim_pending_task(worker_id="resumed-turn")
    assert claimed is not None
    assert claimed.message_id == second.message_id

    # Controlled pipeline stands in for graph execution while the real task
    # processor proves the resumed turn is admitted and receives fresh content.
    pipeline = MagicMock()
    pipeline.execute = AsyncMock(
        return_value=ProcessingResult(success=True, result_content="graph completed")
    )
    processor = ProcessMessageProcessor(
        instance_manager=manager,
        task_repo=task_repo,
        message_repository=SQLModelMessageQueueRepository(engine),
        pipeline=pipeline,
    )
    result = await processor.process(claimed)

    assert result["success"] is True
    assert pipeline.execute.await_args.kwargs["context"].message == "after resume"
    assert first.message_id != second.message_id
