"""Tests for root-instance self-completion branch in
``ChildReportsService._process_child_completion_and_notify_parent``.

Covers the bug fixed in
``docs/bugs/root-instance-premature-completion-on-pending-message.md``:
a root instance (``parent_id is None``) with ``waiting_for == 0`` but
``pending_count > 0`` was incorrectly transitioning to ``COMPLETED`` and
signalling the job queue prematurely. The fix is the single-guard
restoration in the root branch: if either ``waiting_for > 0`` or
``pending_count > 0``, the instance stays in ``WAITING_CHILDREN``.

These tests also exercise the companion fix (Finding 1): the root
``pending_count`` query must exclude the just-completed ``message_id``
by ID (mirroring ``_should_send_completion_report``) to defend against
the uncommitted-transaction double-count hazard.
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session as SQLModelSession

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.services.child_reports import ChildReportsService


# ─── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_manager():
    """Create a mock InstanceManager with required attributes."""
    manager = MagicMock()
    manager._instance_repository = MagicMock()
    manager._engine = MagicMock()
    manager._live_hub = MagicMock()
    manager._live_hub.stream_lifecycle = AsyncMock()
    manager._live_hub.stream_status_change = AsyncMock()
    # The checkpointer property on ChildReportsService accesses
    # ``self._manager._checkpointer.raw_saver``. Returning a real
    # MagicMock there triggers ``_get_last_assistant_message_raw`` to
    # try to read instance messages, which is not what these tests
    # exercise. Use ``None`` so the property short-circuits to ``[]``.
    manager._checkpointer = None
    manager.write_guard = MagicMock()
    return manager


@pytest.fixture
def mock_events_service():
    """Create a mock EventPublisherService."""
    events = MagicMock()
    events._publish_instance_lifecycle_event = AsyncMock()
    return events


def create_root_instance(instance_id: str = "root-001", waiting_for: int = 0,
                         status: str = InstanceStatus.RUNNING.value) -> MagicMock:
    """Create a mock root (parent-less) instance."""
    instance = MagicMock(spec=Instance)
    instance.instance_id = instance_id
    instance.agent_id = "jober"
    instance.parent_id = None
    instance.waiting_for = waiting_for
    instance.status = status
    instance.instance_metadata = {}
    instance.children = None
    instance.version = 1
    instance.last_activity_at = None
    instance.updated_at = None
    return instance


def create_session_with_pending(pending_count: int, instance: MagicMock) -> MagicMock:
    """Create a mock session whose ``exec()`` returns ``pending_count``.

    The session is a stand-in for the ``WriteGuardSession(Session(engine),
    write_guard)`` context manager. The ``commit`` is a no-op.
    """
    session = MagicMock()
    session.get = MagicMock(return_value=instance)
    exec_result = MagicMock()
    exec_result.scalar_one = MagicMock(return_value=pending_count)
    session.exec = MagicMock(return_value=exec_result)
    session.commit = MagicMock()
    return session


@contextmanager
def patched_session(session: MagicMock):
    """Patch both ``Session`` and ``WriteGuardSession`` to return ``session``."""

    @contextmanager
    def session_ctx():
        yield session

    # WriteGuardSession(engine, guard) is used as ``with WriteGuardSession(...) as session:``.
    # It must be a context manager that yields the same mock session.
    wgs = MagicMock()
    wgs.__enter__ = MagicMock(return_value=session)
    wgs.__exit__ = MagicMock(return_value=False)

    with patch.object(ChildReportsService, "__init__", lambda self, m, e=None: None):
        # Patch the symbols used at the call site in child_reports.py:648
        with patch("daemon.services.child_reports.Session", return_value=MagicMock()):
            with patch("daemon.services.child_reports.WriteGuardSession", return_value=wgs):
                yield session


# ─── Test 1: regression — root with pending_count > 0 should NOT complete ──────


class TestRegressionBug:
    """The bug: root instance with ``waiting_for == 0`` and
    ``pending_count > 0`` was incorrectly transitioning to ``COMPLETED``."""

    @pytest.mark.asyncio
    async def test_root_with_pending_messages_stays_waiting_children(
        self, mock_manager, mock_events_service
    ):
        """ROOT BUG: waiting_for=0, pending_count=1 → WAITING_CHILDREN, not COMPLETED.

        Scenario: a child completion report is still queued in the
        instance's ``MessageQueue`` (``READY``) when the previous message
        finishes. The instance has no parent (``parent_id is None``) and
        ``waiting_for == 0``. The buggy code fell through to ``COMPLETED``
        and prematurely marked the job done.
        """
        instance = create_root_instance(waiting_for=0, status=InstanceStatus.RUNNING.value)
        session = create_session_with_pending(pending_count=1, instance=instance)

        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = mock_manager
        service._events_service = mock_events_service
        service._trigger_title_generation = MagicMock()

        with patched_session(session):
            await service._process_child_completion_and_notify_parent(
                instance_id="root-001", completed_message_id="msg-just-completed"
            )

        # The fix: status must be set to WAITING_CHILDREN (not COMPLETED).
        assert instance.status == InstanceStatus.WAITING_CHILDREN.value, (
            f"Expected WAITING_CHILDREN with pending messages, got {instance.status}"
        )
        session.commit.assert_called()

    @pytest.mark.asyncio
    async def test_root_with_no_pending_messages_completes(
        self, mock_manager, mock_events_service
    ):
        """Happy path: waiting_for=0, pending_count=0 → COMPLETED is correct."""
        instance = create_root_instance(waiting_for=0, status=InstanceStatus.RUNNING.value)
        session = create_session_with_pending(pending_count=0, instance=instance)

        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = mock_manager
        service._events_service = mock_events_service
        service._trigger_title_generation = MagicMock()

        with patched_session(session):
            await service._process_child_completion_and_notify_parent(
                instance_id="root-001", completed_message_id="msg-just-completed"
            )

        # Safe to complete when nothing is pending.
        assert instance.status == InstanceStatus.COMPLETED.value


# ─── Test 2: simple-agent happy path — WAITING_CHILDREN + READY self-continuation


class TestSimpleAgentHappyPath:
    """The merge gate for the original "simple agent stuck" bug.

    A simple agent that enqueues its own next message during a turn
    must end up in ``COMPLETED`` after the worker drains the queue, and
    must be observable as ``RUNNING`` (or at least not stuck) while the
    turn is in progress.
    """

    @pytest.mark.asyncio
    async def test_root_with_pending_then_drained_completes(
        self, mock_manager, mock_events_service
    ):
        """First call: pending_count=1 → WAITING_CHILDREN.
        Second call (after queue drained): pending_count=0 → COMPLETED.
        """
        instance = create_root_instance(waiting_for=0, status=InstanceStatus.RUNNING.value)

        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = mock_manager
        service._events_service = mock_events_service
        service._trigger_title_generation = MagicMock()

        # First: with a pending message
        session = create_session_with_pending(pending_count=1, instance=instance)
        with patched_session(session):
            await service._process_child_completion_and_notify_parent(
                instance_id="root-001", completed_message_id="msg-first"
            )
        assert instance.status == InstanceStatus.WAITING_CHILDREN.value

        # Now simulate the queue being drained and the next message
        # completing: status is WAITING_CHILDREN, pending_count=0.
        instance.status = InstanceStatus.WAITING_CHILDREN.value
        session = create_session_with_pending(pending_count=0, instance=instance)
        with patched_session(session):
            await service._process_child_completion_and_notify_parent(
                instance_id="root-001", completed_message_id="msg-second"
            )
        assert instance.status == InstanceStatus.COMPLETED.value


# ─── Test 3: all-children-done cascade reaches COMPLETED ────────────────────────


class TestAllChildrenDoneCascade:
    """Cascade path: waiting_for decrements to 0 with pending messages,
    then pending messages drain, then instance completes."""

    @pytest.mark.asyncio
    async def test_cascade_drain_to_completed(
        self, mock_manager, mock_events_service
    ):
        instance = create_root_instance(waiting_for=0, status=InstanceStatus.WAITING_CHILDREN.value)

        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = mock_manager
        service._events_service = mock_events_service
        service._trigger_title_generation = MagicMock()

        # Simulate: last child report completed; pending_count=0.
        session = create_session_with_pending(pending_count=0, instance=instance)
        with patched_session(session):
            await service._process_child_completion_and_notify_parent(
                instance_id="root-001", completed_message_id="msg-child-report"
            )

        assert instance.status == InstanceStatus.COMPLETED.value


# ─── Test 4: ID-exclusion fix (Finding 1 hazard) ───────────────────────────────


class TestIdExclusionFix:
    """The root-branch query must exclude the just-completed ``message_id``
    by ID, not just by status. Otherwise, the uncommitted-transaction
    race (where ``message_queue.complete()`` has not yet committed the
    finished message to ``COMPLETED``) would double-count the message
    and wedge the instance in ``WAITING_CHILDREN`` forever.

    We verify the *query string* includes the ``message_id !=`` filter
    by inspecting the ``exec()`` call arguments. (Behavioral testing
    would require an uncommitted-transaction fixture, which is hard to
    set up cleanly; the query inspection is a deterministic check of
    the contract.)
    """

    @pytest.mark.asyncio
    async def test_root_query_excludes_completed_message_id(
        self, mock_manager, mock_events_service
    ):
        instance = create_root_instance(waiting_for=0, status=InstanceStatus.RUNNING.value)
        session = create_session_with_pending(pending_count=0, instance=instance)

        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = mock_manager
        service._events_service = mock_events_service
        service._trigger_title_generation = MagicMock()

        with patched_session(session):
            await service._process_child_completion_and_notify_parent(
                instance_id="root-001", completed_message_id="msg-unique-99"
            )

        # Inspect the SQL emitted to the session. The MessageQueue
        # ``message_id !=`` clause must be present so the just-completed
        # message is excluded regardless of its status.
        exec_calls = session.exec.call_args_list
        assert exec_calls, "Expected at least one session.exec() call"
        sql_blob = " ".join(str(c.args[0]) for c in exec_calls)
        # The SQL fragment must include the `message_id !=` exclusion;
        # the bound parameter is the just-completed message_id (verified
        # at the call site).
        assert "message_id != :message_id_1" in sql_blob, (
            f"Expected the message_id exclusion clause in the SQL. "
            f"Got: {sql_blob}"
        )


# ─── Test 5: transition_status_if avoids clobbering concurrent ERROR/PAUSED ──


class TestTransitionStatusIfClobberAvoidance:
    """The ``transition_status_if`` repository method must refuse to update
    a row whose current status is not in the caller's ``allowed_from`` set.
    This is the atomic guard that prevents the MessageJobHandler pre-pickup
    transition from overwriting a concurrent ``error_reporting.py:170``
    write of ``InstanceStatus.ERROR`` (or any other non-allowed status).

    We exercise the method end-to-end against an in-memory SQLite DB so
    the conditional UPDATE runs for real. The test would catch a
    regression where ``transition_status_if`` is changed to read-then-
    unconditional-update, which would reintroduce the TOCTOU window.
    """

    def _build_instance(self, engine, instance_id: str, status: str) -> "Instance":
        from datetime import datetime, timezone

        from daemon.repositories.instance.models import Instance as InstanceModel
        with SQLModelSession(engine) as session:
            row = InstanceModel(
                instance_id=instance_id,
                agent_id="jober",
                agent_dir="agents/jober",
                parent_id=None,
                status=status,
                waiting_for=0,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            session.add(row)
            session.commit()
        return row

    def _read_status(self, engine, instance_id: str) -> str | None:
        with SQLModelSession(engine) as session:
            row = session.get(Instance, instance_id)
            return row.status if row is not None else None

    def test_transition_allowed_from_status_succeeds(self, tmp_path):
        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )
        from sqlmodel import SQLModel, create_engine

        db_path = tmp_path / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(engine)
        repo = SQLModelInstanceRepository(engine=engine)
        self._build_instance(engine, "inst-1", InstanceStatus.WAITING_CHILDREN.value)

        updated = repo.transition_status_if(
            "inst-1",
            InstanceStatus.RUNNING.value,
            (InstanceStatus.WAITING_CHILDREN.value, InstanceStatus.IDLE.value),
        )
        assert updated is not None
        assert updated.status == InstanceStatus.RUNNING.value
        assert self._read_status(engine, "inst-1") == InstanceStatus.RUNNING.value

    def test_transition_blocked_when_status_not_in_allowed_from(self, tmp_path):
        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )
        from sqlmodel import SQLModel, create_engine

        db_path = tmp_path / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(engine)
        repo = SQLModelInstanceRepository(engine=engine)
        # Simulate a concurrent error_reporting write that set ERROR.
        self._build_instance(engine, "inst-2", InstanceStatus.ERROR.value)

        updated = repo.transition_status_if(
            "inst-2",
            InstanceStatus.RUNNING.value,
            (InstanceStatus.WAITING_CHILDREN.value, InstanceStatus.IDLE.value),
        )
        # The conditional update must NOT have clobbered the ERROR status.
        assert updated is None, (
            f"transition_status_if must return None when current status is "
            f"not in allowed_from; got {updated!r}"
        )
        assert self._read_status(engine, "inst-2") == InstanceStatus.ERROR.value

    def test_transition_blocked_when_instance_missing(self, tmp_path):
        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )
        from sqlmodel import SQLModel, create_engine

        db_path = tmp_path / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(engine)
        repo = SQLModelInstanceRepository(engine=engine)

        updated = repo.transition_status_if(
            "inst-missing",
            InstanceStatus.RUNNING.value,
            (InstanceStatus.WAITING_CHILDREN.value, InstanceStatus.IDLE.value),
        )
        assert updated is None
