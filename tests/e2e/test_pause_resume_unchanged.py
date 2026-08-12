"""Directed no-behaviour-drift E2E for the named pause/resume wrappers.

The original pause-during-report scenario is intentionally reused rather than
inventing a smaller fixture.  That keeps this test a second safety net around
the exact five orphan conditions covered by
``test_pause_during_report_turn_then_resume`` while exercising the refactored
cascade methods directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.message_queue.models import MessageStatus
from daemon.repositories.report_injection.models import ReportInjectionState
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.write_pause_guard import WritePauseGuard

from tests.e2e import test_pause_during_report_turn_then_resume as baseline


@pytest.fixture
def engine() -> Engine:
    """Real SQLite engine matching the existing directed E2E fixture."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record) -> None:  # noqa: ANN001
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Importing the baseline module registers all mirror models before this
    # create_all call (including JobWatcher and ReportInjection).
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def write_guard() -> WritePauseGuard:
    return WritePauseGuard()


@pytest.fixture
def lifecycle_service(
    engine: Engine, write_guard: WritePauseGuard
) -> InstanceLifecycleService:
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    manager._task_repo = TaskRepository(engine=engine)
    service._manager = manager
    return service


def _instance_status(engine: Engine, instance_id: str) -> str:
    with Session(engine) as session:
        row = session.get(Instance, instance_id)
        assert row is not None
        return row.status


def test_pause_resume_cascade_behavior_is_unchanged(
    lifecycle_service: InstanceLifecycleService,
    engine: Engine,
    write_guard: WritePauseGuard,
) -> None:
    """Run the production pause/report/resume boundary and all orphan checks."""
    scenario: dict[str, Any] = baseline._seed_running_pause_during_report_scenario(engine)
    instance_id = scenario["instance_id"]
    work_id = scenario["work_id"]
    message_id = scenario["message_id"]
    answer_message_id = scenario["answer_message_id"]

    assert baseline._read_task_status(engine, work_id) == TaskStatus.RUNNING.value
    assert baseline._read_message_status(engine, message_id) == MessageStatus.PROCESSING.value
    assert baseline._read_job_item_admission(engine, work_id) == AdmissionState.ACTIVE.value
    assert baseline._read_lock_count(engine, work_id) == 1

    pause_result = lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[instance_id],
        paused_at_iso=scenario["paused_at"],
        paused_instances_data=[(instance_id, "developer")],
    )
    assert pause_result.updated_ids == [instance_id]
    assert _instance_status(engine, instance_id) == InstanceStatus.PAUSED.value
    assert baseline._read_task_status(engine, work_id) == TaskStatus.PAUSED.value
    # Suspension is deliberately task-level; mirrors remain in-flight until
    # RESUME_TURN retires the old turn.
    assert baseline._read_message_status(engine, message_id) == MessageStatus.PROCESSING.value
    assert baseline._read_lock_count(engine, work_id) == 1

    answer_work_id = baseline._seed_answer_message_and_task(
        engine,
        instance_id=instance_id,
        answer_message_id=answer_message_id,
    )
    assert baseline._read_task_status(engine, answer_work_id) == TaskStatus.PENDING.value

    resume_result = lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )
    assert resume_result.updated_ids == [instance_id]
    assert resume_result.resumed_task_work_ids == [work_id]
    assert _instance_status(engine, instance_id) == InstanceStatus.RUNNING.value

    # Phase 4b/4c: the resumed Task is now PENDING (was CANCELLED
    # pre-migration) and the reconciler is a no-op for non-terminal
    # tasks — the message_queue row, JobItem admission, job_locks
    # row, and report_injection all stay in their pre-resume state
    # until the WorkerPool's natural claim+complete path drives the
    # terminal transition.
    assert baseline._read_task_status(engine, work_id) == TaskStatus.PENDING.value
    assert baseline._read_message_status(engine, message_id) == MessageStatus.PROCESSING.value
    assert baseline._read_message_processing_task_id(engine, message_id) is None
    assert baseline._read_job_item_admission(engine, work_id) == AdmissionState.ACTIVE.value
    assert baseline._read_lock_count(engine, work_id) == 1
    assert baseline._count_report_injections(
        engine,
        parent_instance_id=instance_id,
        state=ReportInjectionState.PENDING.value,
    ) == 1
    assert baseline._read_job_watcher_count(engine, work_id) == 1
    assert baseline._read_task_status(engine, answer_work_id) == TaskStatus.PENDING.value

    # The fresh answer remains independently deliverable after the old turn is
    # cancelled, exactly as in the baseline E2E.
    baseline._force_complete_answer(
        engine,
        work_id=answer_work_id,
        message_id=answer_message_id,
    )
    assert baseline._read_task_status(engine, answer_work_id) == TaskStatus.COMPLETED.value
    assert baseline._read_message_status(engine, answer_message_id) == MessageStatus.COMPLETED.value
