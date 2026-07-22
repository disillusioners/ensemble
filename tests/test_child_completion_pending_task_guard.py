"""Regression tests for the child-completion pending-task guard.

Reproduces the premature-completion bug (2026-07-22): a non-root parent
(e.g. the tester) that ends a turn with **0 active children** but a
**PENDING task queued** was marked COMPLETED and reported to its own
parent; when the queued task later ran and the parent truly finished,
the idempotency guard ("already in terminal state") silently dropped
the real final report.

The fix adds a pending-task count to the active-children guard in
``ChildReportsService._process_child_completion_db_sync`` so completion
requires BOTH 0 active children AND 0 queued turns.

These tests construct a real ``ChildReportsService`` against an
in-memory SQLite engine and drive ``_process_child_completion_db_sync``
directly (the DB-sync half that owns the guard), with a minimal manager
mock providing ``.engine`` + ``.write_guard``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Register all tables on SQLModel.metadata before create_all.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.dependency_bus import DependencyWatcherRepository
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus
from daemon.write_pause_guard import WritePauseGuard


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def engine():
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


@pytest.fixture
def service(engine):
    """ChildReportsService with a minimal manager mock (engine + write_guard)."""
    mgr = MagicMock()
    mgr.engine = engine
    mgr.write_guard = WritePauseGuard()
    return ChildReportsService(manager=mgr, events_service=None)


@pytest.fixture(autouse=True)
def _reset_bus_singleton():
    """Clear the module-level bus singleton between tests."""
    set_dependency_bus(None)
    yield
    set_dependency_bus(None)


@pytest.fixture
async def bus(engine):
    """Started DependencyBus; the completion path emits the child's
    terminal event via the bus, so it must be initialized for the
    'completes' test."""
    repo = DependencyWatcherRepository(engine)
    b = DependencyBus(repo)
    await b.start()
    set_dependency_bus(b)
    try:
        yield b
    finally:
        await b.stop()
        set_dependency_bus(None)


# =============================================================================
# Seed helpers
# =============================================================================


def _seed_instance(engine, *, instance_id=None, status=InstanceStatus.RUNNING.value,
                   parent_id=None, invoked_as_tool=False):
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    meta = {"invoked_as_tool": True} if invoked_as_tool else {}
    with Session(engine) as s:
        s.add(Instance(
            instance_id=iid,
            agent_id="tester",
            agent_dir="/tmp/tester",
            agent_name="tester",
            parent_id=parent_id,
            status=status,
            version=1,
            instance_metadata=meta,
        ))
        s.commit()
    return iid


def _seed_task(engine, *, instance_id, status=TaskStatus.PENDING.value,
               task_type=TaskType.PROCESS_REPORT.value, message_id=None):
    with Session(engine) as s:
        s.add(Task(
            task_type=task_type,
            instance_id=instance_id,
            message_id=message_id or f"msg-{uuid.uuid4().hex[:8]}",
            status=status,
            created_at=datetime.now(timezone.utc),
        ))
        s.commit()


# =============================================================================
# Tests
# =============================================================================


class TestPendingTaskGuard:
    """The TOCTOU fix: 0 active children + pending task => defer."""

    def test_zero_children_but_pending_task_defers(self, service, engine):
        """The exact prod scenario: tester ends a turn with all current
        children terminal (active_children==0) while a PROCESS_REPORT turn
        is still PENDING. Must DEFER (not prematurely complete)."""
        parent_id = _seed_instance(engine)
        tester_id = _seed_instance(engine, parent_id=parent_id)
        # One terminal child => active_children == 0.
        _seed_instance(engine, parent_id=tester_id, status=InstanceStatus.COMPLETED.value)
        # One PENDING task for the tester (the queued turn that would spawn
        # more work) => pending_tasks == 1.
        _seed_task(engine, instance_id=tester_id, status=TaskStatus.PENDING.value)

        result = service._process_child_completion_db_sync(
            tester_id, completed_message_id="msg-current", last_content="report"
        )

        assert result.outcome == "child_still_running_defer", (
            f"Expected defer (0 children + 1 pending task), got {result.outcome}"
        )
        # Instance must NOT have been marked terminal.
        with Session(engine) as s:
            inst = s.get(Instance, tester_id)
        assert inst.status == InstanceStatus.RUNNING.value

    async def test_zero_children_zero_pending_completes(self, service, engine, bus):
        """Sanity: 0 active children AND 0 pending tasks => complete
        normally (the fix must not wedge normal completion). Requires the
        bus because the non-root completion path emits the child's
        terminal event via it."""
        parent_id = _seed_instance(engine)
        tester_id = _seed_instance(engine, parent_id=parent_id)
        _seed_instance(engine, parent_id=tester_id, status=InstanceStatus.COMPLETED.value)
        # No pending task for the tester (the only task is its own CURRENT
        # turn, which is RUNNING during db_sync and excluded by the
        # status='pending' filter).

        result = service._process_child_completion_db_sync(
            tester_id, completed_message_id="msg-final", last_content="done"
        )

        assert result.outcome == "regular_child_completed", (
            f"Expected normal completion, got {result.outcome}"
        )

    def test_active_children_still_defers(self, service, engine):
        """Existing behavior preserved: an active (running) child alone
        still defers, independent of pending tasks."""
        parent_id = _seed_instance(engine)
        tester_id = _seed_instance(engine, parent_id=parent_id)
        _seed_instance(engine, parent_id=tester_id, status=InstanceStatus.RUNNING.value)

        result = service._process_child_completion_db_sync(
            tester_id, completed_message_id="msg-x", last_content="..."
        )

        assert result.outcome == "child_still_running_defer"
