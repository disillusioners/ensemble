"""Tests for the root-instance carve-out guard in ChildReportsService.

The carve-out guard (daemon/services/child_reports.py lines 1017-1052)
prevents a root instance from being stuck in WAITING_CHILDREN when its
own message queue has stale/duplicate messages (pending_count > 0) BUT
the instance's MESSAGE job is already in a terminal state (completed,
failed, cancelled, dead_letter). Without this guard, the task-claim race
that produced the stale messages would leave the instance permanently
stuck with no code path to clear it.

These tests exercise the carve-out against a real in-memory SQLite
engine (StaticPool for cross-thread safety) with minimal manager mocks,
following the pattern in tests/test_deadlock_fix.py.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Model imports — required so SQLModel.metadata sees the tables when
# create_all() runs on the test engine.
from daemon.repositories.event.models import Event, EventKind  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import JobItem, JobStatus
from daemon.repositories.message_queue.models import (  # noqa: F401
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.task.models import Task  # noqa: F401
from daemon.services.child_reports import ChildReportsService
from daemon.services.correlation_manager import set_correlation_manager
from daemon.write_pause_guard import WritePauseGuard


# ─────────────────────────────────────────────────────────────────────────────
# Engine + service helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine (StaticPool for cross-thread safety)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(autouse=True)
def _reset_correlation_manager():
    """Ensure no CorrelationManager singleton leaks between tests.

    The legacy ``waiting_for`` fallback path is required for the carve-out
    test (CM is None → falls through to ``instance.waiting_for`` read).
    """
    set_correlation_manager(None)
    yield
    set_correlation_manager(None)


def _build_child_reports_service(engine: Engine) -> ChildReportsService:
    """Build a real ``ChildReportsService`` with a mock manager that
    exposes only the attributes ``_process_child_completion_db_sync`` needs.

    Mirrors the helper in tests/test_deadlock_fix.py — uses ``__new__`` to
    skip ``__init__`` and bind attributes manually.
    """
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._checkpointer = None
    manager._live_hub = None  # SSE no-op (guarded on truthiness)
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None  # lifecycle event publish is guarded
    return service


def _seed_root_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    waiting_for: int = 0,
) -> str:
    """Insert a root Instance row (parent_id=None)."""
    iid = instance_id or f"root-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        inst = Instance(
            instance_id=iid,
            agent_id="leader",
            agent_name="leader",
            agent_dir="/tmp/leader",
            parent_id=None,
            status=status,
            waiting_for=waiting_for,
            version=1,
            instance_metadata={},
            children="[]",
        )
        session.add(inst)
        session.commit()
    return iid


def _seed_message(
    engine: Engine,
    *,
    instance_id: str,
    message_id: str | None = None,
    status: str = MessageStatus.READY.value,
) -> str:
    """Insert a MessageQueue row for the given instance."""
    mid = message_id or f"msg-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        msg = MessageQueue(
            message_id=mid,
            instance_id=instance_id,
            content="stale duplicate message",
            type=MessageType.HUMAN.value,
            status=status,
        )
        session.add(msg)
        session.commit()
    return mid


def _seed_message_job(
    engine: Engine,
    *,
    instance_id: str,
    status: str = JobStatus.COMPLETED.value,
) -> str:
    """Insert a JobItem with job_type='message' for the given instance."""
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        job = JobItem(
            job_id=job_id,
            agent_id="leader",
            agent_dir="/tmp/leader",
            message="test message",
            source="api",
            job_type="message",
            status=status,
            instance_id=instance_id,
        )
        session.add(job)
        session.commit()
    return job_id


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestRootCarveOutTerminalJob:
    """Carve-out guard: root instance with stale pending messages AND a
    terminal MESSAGE job should NOT be set to WAITING_CHILDREN."""

    def test_carve_out_skips_waiting_children_when_message_job_terminal(
        self, engine: Engine
    ):
        """A root instance with pending_count > 0 in its own queue but
        whose MESSAGE job is already ``completed`` should hit the
        carve-out guard: return ``root_skipped_terminal_job`` and leave
        the instance status untouched (still ``running``).
        """
        # Arrange
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_message(engine, instance_id=root_id, status=MessageStatus.READY.value)
        _seed_message_job(engine, instance_id=root_id, status=JobStatus.COMPLETED.value)
        completed_message_id = "msg-already-completed-other"

        # Act
        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id=completed_message_id,
            last_content="some assistant text",
        )

        # Assert: carve-out guard fired
        assert result.outcome == "root_skipped_terminal_job"
        assert result.instance_id == root_id
        assert result.parent_id is None

        # Instance status MUST remain unchanged
        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst is not None
            assert inst.status == InstanceStatus.RUNNING.value, (
                f"Expected status=running (carve-out should not commit), "
                f"got status={inst.status}"
            )

    def test_carve_out_triggers_for_failed_message_job(self, engine: Engine):
        """The carve-out applies to ANY terminal MESSAGE job status, not
        just ``completed``. A ``failed`` MESSAGE job should also trigger it.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_message(engine, instance_id=root_id, status=MessageStatus.READY.value)
        _seed_message_job(engine, instance_id=root_id, status=JobStatus.FAILED.value)

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "root_skipped_terminal_job"
        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.RUNNING.value

    def test_carve_out_triggers_for_cancelled_message_job(self, engine: Engine):
        """A ``cancelled`` MESSAGE job is also terminal — the carve-out
        should still fire.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_message(engine, instance_id=root_id, status=MessageStatus.READY.value)
        _seed_message_job(
            engine, instance_id=root_id, status=JobStatus.CANCELLED.value
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "root_skipped_terminal_job"
        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.RUNNING.value

    def test_carve_out_triggers_for_dead_letter_message_job(self, engine: Engine):
        """A ``dead_letter`` MESSAGE job is also terminal — the carve-out
        should still fire. ``dead_letter`` is the final state for jobs that
        exhausted all retries and were moved to the DLQ; treating it as
        non-terminal would leave root instances stuck in WAITING_CHILDREN
        forever (no consumer will ever re-process a DLQ'd job).
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_message(engine, instance_id=root_id, status=MessageStatus.READY.value)
        _seed_message_job(
            engine, instance_id=root_id, status=JobStatus.DEAD_LETTER.value
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "root_skipped_terminal_job"
        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.RUNNING.value


class TestRootPendingMessagesNormalPath:
    """Companion tests: when the MESSAGE job is NOT terminal, the carve-out
    MUST NOT fire and the normal ``root_waiting_children`` path should run.
    These guard against the carve-out over-firing and breaking the normal
    pending-messages branch."""

    def test_normal_path_sets_waiting_children_when_job_processing(
        self, engine: Engine
    ):
        """A root instance with pending_count > 0 AND a non-terminal
        (processing) MESSAGE job should proceed to set WAITING_CHILDREN
        and return ``root_waiting_children`` — the carve-out must not
        over-fire.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_message(engine, instance_id=root_id, status=MessageStatus.READY.value)
        # Non-terminal job — carve-out must NOT fire
        _seed_message_job(
            engine, instance_id=root_id, status=JobStatus.PROCESSING.value
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "root_waiting_children"
        assert result.instance_id == root_id

        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.WAITING_CHILDREN.value
