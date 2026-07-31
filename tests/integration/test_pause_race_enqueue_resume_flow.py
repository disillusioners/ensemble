"""No-false-positive pause/resume/enqueue integration flow for Phase 2."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

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


@pytest.mark.asyncio
async def test_normal_enqueue_before_and_after_pause_resume_drives_graph_turn(
    engine: Engine,
) -> None:
    instance_id = "pause-resume-normal-flow"
    _seed_running_instance(engine, instance_id)
    write_guard = WritePauseGuard()

    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    manager._deferred_question_pause = set()
    cancellation = MagicMock(spec=CancellationService)
    cancellation.is_shutting_down = False
    messaging = InstanceMessagingService(manager, cancellation)
    lifecycle = InstanceLifecycleService.__new__(InstanceLifecycleService)
    lifecycle._manager = manager
    task_repo = TaskRepository(engine)

    # RUNNING + no marker is never a false positive: normal Task is created.
    first = await asyncio.to_thread(_enqueue, messaging, instance_id, "before pause")
    first_claim = task_repo.claim_pending_task(worker_id="first-turn")
    assert first_claim is not None
    assert first_claim.task_type == TaskType.PROCESS_MESSAGE.value
    assert first_claim.status == TaskStatus.RUNNING.value
    task_repo.complete_task(first_claim.id, {"success": True})

    # Deferred pause marker precedes the production cascade transaction.
    manager._deferred_question_pause.add(instance_id)
    await asyncio.to_thread(
        lifecycle._pause_cascade_db_sync,
        engine,
        write_guard,
        tree_ids=[instance_id],
        paused_at_iso=datetime.now(timezone.utc).isoformat(),
        paused_instances_data=[(instance_id, "developer")],
    )
    manager._deferred_question_pause.discard(instance_id)
    with Session(engine) as session:
        assert session.get(Instance, instance_id).status == InstanceStatus.PAUSED.value

    # Resume through the production DB cascade and enqueue a fresh user turn.
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
