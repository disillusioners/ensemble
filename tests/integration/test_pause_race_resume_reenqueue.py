"""Integration proof that post-cascade messages wait behind the SQL pause gate."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue, MessageStatus
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


def _seed_paused_instance(engine: Engine, instance_id: str) -> None:
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                project_id="test-project",
                status=InstanceStatus.PAUSED.value,
                instance_metadata={},
            )
        )
        session.commit()


def _messaging_service(engine: Engine, write_guard: WritePauseGuard):
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    manager._deferred_question_pause = set()
    cancellation = MagicMock(spec=CancellationService)
    cancellation.is_shutting_down = False
    return InstanceMessagingService(manager, cancellation), manager


@pytest.mark.asyncio
async def test_paused_enqueue_is_claimed_and_delivered_after_resume(
    engine: Engine,
) -> None:
    instance_id = "paused-resume-reenqueue"
    payload = "buffer this until the instance resumes"
    write_guard = WritePauseGuard()
    _seed_paused_instance(engine, instance_id)
    messaging, manager = _messaging_service(engine, write_guard)

    prepared = messaging._prepare_enqueued_message(
        instance_id=instance_id,
        message=payload,
        source="api",
        priority=1,
        images=None,
        metadata=None,
    )

    task_repo = TaskRepository(engine)
    with Session(engine) as session:
        task = session.exec(select(Task).where(Task.instance_id == instance_id)).one()
        message = session.get(MessageQueue, prepared.message_id)
        assert task.task_type == TaskType.PROCESS_MESSAGE.value
        assert task.status == TaskStatus.PENDING.value
        assert message is not None
        assert message.status == MessageStatus.READY.value

    # The production claim SQL sees DB=PAUSED and refuses the pending row.
    assert task_repo.claim_pending_task(worker_id="before-resume") is None

    # Exercise the production resume transaction, not a hand-written status flip.
    lifecycle = InstanceLifecycleService.__new__(InstanceLifecycleService)
    lifecycle._manager = manager
    lifecycle._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    claimed = task_repo.claim_pending_task(worker_id="after-resume")
    assert claimed is not None
    assert claimed.id == task.id
    assert claimed.status == TaskStatus.RUNNING.value

    # Drive the real processor prelude with a controlled pipeline. Loading the
    # claimed Task's MessageQueue content proves the buffered message reaches
    # the graph-delivery boundary after resume.
    pipeline = MagicMock()
    pipeline.execute = AsyncMock(
        return_value=ProcessingResult(success=True, result_content="delivered")
    )
    processor = ProcessMessageProcessor(
        instance_manager=manager,
        task_repo=task_repo,
        message_repository=SQLModelMessageQueueRepository(engine),
        pipeline=pipeline,
    )
    result = await processor.process(claimed)

    assert result["success"] is True
    call_context = pipeline.execute.await_args.kwargs["context"]
    assert call_context.message_id == prepared.message_id
    assert call_context.message == payload
