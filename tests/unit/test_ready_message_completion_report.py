"""Tests for READY message handling in _should_send_completion_report.

These tests verify the fix for the bug where READY messages were incorrectly
counted as "pending", which blocked completion reports after pause/resume.

Bug: After pause/resume, child's original message is in READY state →
report skipped → parent never notified.

Fix: Only PROCESSING and RETRYING statuses block completion report.
READY no longer blocks.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from contextlib import contextmanager

from daemon.services.child_reports import ChildReportsService
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue, MessageStatus, MessageType


# ─── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_manager():
    """Create a mock InstanceManager with required attributes."""
    manager = MagicMock()
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()
    manager._engine = MagicMock()
    manager._generate_and_broadcast_title = AsyncMock()
    manager._live_hub = MagicMock()
    manager._live_hub.stream_lifecycle = AsyncMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._checkpointer = MagicMock()
    manager.get_instance = AsyncMock()
    manager.config = MagicMock()
    manager.config.llm = MagicMock()
    return manager


@pytest.fixture
def mock_events_service():
    """Create a mock EventPublisherService."""
    events = MagicMock()
    events._publish_instance_lifecycle_event = AsyncMock()
    return events


def create_mock_session(pending_count: int = 0, existing_instance=None, existing_report=None):
    """Create a mock session with configurable pending message count.

    Args:
        pending_count: Number of pending messages (PROCESSING/RETRYING)
        existing_instance: The instance to return from session.get()
        existing_report: Existing completion report to return

    Returns:
        Mock session with exec() returning pending_count
    """
    session = MagicMock()

    # session.get() returns the instance
    def mock_get(cls, instance_id):
        return existing_instance

    session.get = mock_get

    # session.exec() returns pending count and optionally existing report
    exec_result = MagicMock()
    exec_result.scalar_one.return_value = pending_count
    exec_result.first.return_value = existing_report
    session.exec = MagicMock(return_value=exec_result)

    return session


def create_session_context(session):
    """Create a context manager for Session that yields the mock session."""
    @contextmanager
    def mock_session_ctx():
        yield session
    return mock_session_ctx()


def create_child_instance(instance_id: str = "child-123", parent_id: str = "parent-456"):
    """Create a mock child instance."""
    instance = MagicMock(spec=Instance)
    instance.instance_id = instance_id
    instance.agent_id = "developer"
    instance.parent_id = parent_id
    instance.waiting_for = 0
    instance.status = InstanceStatus.COMPLETED.value
    instance.instance_metadata = {}
    instance.children = None
    instance.version = 1
    instance.last_activity_at = None
    return instance


# ─── Core Bug Tests ──────────────────────────────────────────────────────────────


class TestReadyMessageDoesNotBlock:
    """Test suite for the core bug: READY messages should NOT block completion reports.

    These tests verify the exact bug that was fixed: after pause/resume,
    the child's original message is in READY state, which should NOT block
    the completion report from being sent to the parent.
    """

    @pytest.mark.asyncio
    async def test_ready_message_in_queue_report_should_not_be_skipped(self, mock_manager, mock_events_service):
        """CORE BUG FIX: READY message → completion report should NOT be skipped.

        Scenario: Child completes, has READY message in queue from original execution.
        The completion report should proceed (not be skipped).

        This is the exact bug scenario:
        1. Child runs and completes a message (message becomes READY)
        2. Parent pauses before consuming child's report
        3. Child resumes with fresh message (READY message still exists)
        4. Completion report should NOT be blocked by the READY message
        """
        child_instance = create_child_instance()
        session = create_mock_session(pending_count=0, existing_instance=child_instance)

        service = ChildReportsService(
            manager=mock_manager,
            events_service=mock_events_service,
        )

        with patch(
            "daemon.services.child_reports.Session",
            return_value=create_session_context(session)
        ):
            should_send, reason = await service._should_send_completion_report(
                session,
                "child-123",
                completed_message_id="msg-resume-789",

            )

        # Core assertion: report should be sent (READY does NOT block)
        assert should_send is True, f"READY message should NOT block report, but got reason: {reason}"
        assert reason == "all_checks_passed", f"Expected 'all_checks_passed', got: {reason}"

    @pytest.mark.asyncio
    async def test_processing_message_in_queue_report_should_be_skipped(self, mock_manager, mock_events_service):
        """PROCESSING message → completion report SHOULD be skipped.

        Scenario: Child has another message still PROCESSING.
        The completion report should be blocked until that message finishes.
        """
        child_instance = create_child_instance()
        # pending_count=1 means there's a PROCESSING message
        session = create_mock_session(pending_count=1, existing_instance=child_instance)

        service = ChildReportsService(
            manager=mock_manager,
            events_service=mock_events_service,
        )

        with patch(
            "daemon.services.child_reports.Session",
            return_value=create_session_context(session)
        ):
            should_send, reason = await service._should_send_completion_report(
                session,
                "child-123",
                completed_message_id="msg-resume-789",

            )

        # PROCESSING message SHOULD block report
        assert should_send is False, "PROCESSING message should block report"
        assert reason == "pending_messages_exist", f"Expected 'pending_messages_exist', got: {reason}"

    @pytest.mark.asyncio
    async def test_retrying_message_in_queue_report_should_be_skipped(self, mock_manager, mock_events_service):
        """RETRYING message → completion report SHOULD be skipped.

        Scenario: Child has a message that is RETRYING.
        The completion report should be blocked.
        """
        child_instance = create_child_instance()
        # pending_count=1 includes RETRYING messages
        session = create_mock_session(pending_count=1, existing_instance=child_instance)

        service = ChildReportsService(
            manager=mock_manager,
            events_service=mock_events_service,
        )

        with patch(
            "daemon.services.child_reports.Session",
            return_value=create_session_context(session)
        ):
            should_send, reason = await service._should_send_completion_report(
                session,
                "child-123",
                completed_message_id="msg-resume-789",

            )

        # RETRYING message SHOULD block report
        assert should_send is False, "RETRYING message should block report"
        assert reason == "pending_messages_exist"

    @pytest.mark.asyncio
    async def test_no_messages_in_queue_report_should_proceed(self, mock_manager, mock_events_service):
        """No messages → completion report should proceed.

        Scenario: Child has no pending messages.
        The completion report should proceed normally.
        """
        child_instance = create_child_instance()
        session = create_mock_session(pending_count=0, existing_instance=child_instance)

        service = ChildReportsService(
            manager=mock_manager,
            events_service=mock_events_service,
        )

        with patch(
            "daemon.services.child_reports.Session",
            return_value=create_session_context(session)
        ):
            should_send, reason = await service._should_send_completion_report(
                session,
                "child-123",
                completed_message_id="msg-new-789",

            )

        # No messages - report should proceed
        assert should_send is True
        assert reason == "all_checks_passed"


# ─── Edge Case Tests ─────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Test suite for edge cases in READY message handling."""

    @pytest.mark.asyncio
    async def test_multiple_ready_messages_report_should_proceed(self, mock_manager, mock_events_service):
        """Multiple READY messages → report should proceed.

        Scenario: Child has multiple READY messages from previous executions.
        READY messages should not block the completion report.
        """
        child_instance = create_child_instance()
        # pending_count=0 means READY messages are NOT counted
        session = create_mock_session(pending_count=0, existing_instance=child_instance)

        service = ChildReportsService(
            manager=mock_manager,
            events_service=mock_events_service,
        )

        with patch(
            "daemon.services.child_reports.Session",
            return_value=create_session_context(session)
        ):
            should_send, reason = await service._should_send_completion_report(
                session,
                "child-123",
                completed_message_id="msg-resume-789",

            )

        # Multiple READY messages should NOT block
        assert should_send is True, f"Multiple READY messages should NOT block report, got reason: {reason}"
        assert reason == "all_checks_passed"

    @pytest.mark.asyncio
    async def test_ready_plus_processing_messages_report_should_be_skipped(self, mock_manager, mock_events_service):
        """READY + PROCESSING messages → report SHOULD be skipped.

        Scenario: Child has both READY and PROCESSING messages.
        The PROCESSING message should block the completion report.
        """
        child_instance = create_child_instance()
        # pending_count=1 includes PROCESSING (READY not counted)
        session = create_mock_session(pending_count=1, existing_instance=child_instance)

        service = ChildReportsService(
            manager=mock_manager,
            events_service=mock_events_service,
        )

        with patch(
            "daemon.services.child_reports.Session",
            return_value=create_session_context(session)
        ):
            should_send, reason = await service._should_send_completion_report(
                session,
                "child-123",
                completed_message_id="msg-resume-789",

            )

        # PROCESSING message should block (even with READY messages present)
        assert should_send is False
        assert reason == "pending_messages_exist"

    @pytest.mark.asyncio
    async def test_only_completed_messages_report_should_proceed(self, mock_manager, mock_events_service):
        """Only COMPLETED messages → report should proceed.

        Scenario: All messages are COMPLETED.
        The completion report should proceed.
        """
        child_instance = create_child_instance()
        session = create_mock_session(pending_count=0, existing_instance=child_instance)

        service = ChildReportsService(
            manager=mock_manager,
            events_service=mock_events_service,
        )

        with patch(
            "daemon.services.child_reports.Session",
            return_value=create_session_context(session)
        ):
            should_send, reason = await service._should_send_completion_report(
                session,
                "child-123",
                completed_message_id="msg-final-789",

            )

        # Only COMPLETED messages - report should proceed
        assert should_send is True
        assert reason == "all_checks_passed"


# ─── Diagnostic Logging Tests ───────────────────────────────────────────────────


class TestDiagnosticLogging:
    """Test suite for diagnostic logging in skip paths."""

    @pytest.mark.asyncio
    async def test_pending_messages_log_contains_correct_status_info(self, mock_manager, mock_events_service, caplog):
        """Verify skip log mentions PROCESSING/RETRYING but not READY."""
        import logging

        child_instance = create_child_instance()
        session = create_mock_session(pending_count=1, existing_instance=child_instance)

        service = ChildReportsService(
            manager=mock_manager,
            events_service=mock_events_service,
        )

        with patch(
            "daemon.services.child_reports.Session",
            return_value=create_session_context(session)
        ):
            with caplog.at_level(logging.INFO):
                should_send, reason = await service._should_send_completion_report(
                    session,
                    "child-123",
                    completed_message_id="msg-resume-789",
    
                )

        # Verify the log mentions PROCESSING/RETRYING
        log_found = any(
            "PROCESSING/RETRYING" in record.message or
            "pending messages" in record.message.lower()
            for record in caplog.records
        )
        assert log_found, "Expected log to mention pending messages status"

    @pytest.mark.asyncio
    async def test_passing_check_log_contains_all_checks_passed(self, mock_manager, mock_events_service, caplog):
        """Verify passing check log mentions 'all_checks_passed'."""
        import logging

        child_instance = create_child_instance()
        session = create_mock_session(pending_count=0, existing_instance=child_instance)

        service = ChildReportsService(
            manager=mock_manager,
            events_service=mock_events_service,
        )

        with patch(
            "daemon.services.child_reports.Session",
            return_value=create_session_context(session)
        ):
            with caplog.at_level(logging.INFO):
                should_send, reason = await service._should_send_completion_report(
                    session,
                    "child-123",
                    completed_message_id="msg-new-789",
    
                )

        # Verify the log mentions idempotency check passed
        log_found = any(
            "Idempotency check PASSED" in record.message
            for record in caplog.records
        )
        assert log_found, "Expected log to mention 'Idempotency check PASSED'"

    @pytest.mark.asyncio
    async def test_skip_reason_strings_are_specific(self, mock_manager, mock_events_service):
        """Verify each skip path returns a specific, informative reason string."""
        child_instance = create_child_instance()

        # Test 1: pending_messages_exist
        session1 = create_mock_session(pending_count=1, existing_instance=child_instance)
        service = ChildReportsService(
            manager=mock_manager,
            events_service=mock_events_service,
        )

        with patch(
            "daemon.services.child_reports.Session",
            return_value=create_session_context(session1)
        ):
            _, reason1 = await service._should_send_completion_report(
                session1, "child-123", "msg-1"
            )

        # Test 2: no_completed_message_id (edge case - completed_message_id is None)
        session2 = MagicMock()
        exec_result = MagicMock()
        exec_result.scalar_one.return_value = 0
        session2.exec = MagicMock(return_value=exec_result)

        with patch(
            "daemon.services.child_reports.Session",
            return_value=create_session_context(session2)
        ):
            _, reason2 = await service._should_send_completion_report(
                session2, "child-123", None
            )

        # Test 3: no_parent_id
        no_parent_instance = create_child_instance()
        no_parent_instance.parent_id = None
        session3 = create_mock_session(pending_count=0, existing_instance=no_parent_instance)

        with patch(
            "daemon.services.child_reports.Session",
            return_value=create_session_context(session3)
        ):
            _, reason3 = await service._should_send_completion_report(
                session3, "child-123", "msg-3"
            )

        # Verify all reason strings are specific and different
        assert reason1 == "pending_messages_exist"
        assert reason2 == "no_completed_message_id"
        assert reason3 == "no_parent_id"
        assert len({reason1, reason2, reason3}) == 3, "All reason strings should be unique"


# ─── Revive-wedge terminalization: P-TWIN lockstep pin ────────────────────────
# The dead-code twin ``_should_send_completion_report`` and the live
# inlined guard in ``_process_child_completion_db_sync`` must evaluate the
# SAME two-arm predicate. Both call the ONE module-level builder
# ``_mint_guard_suppress_predicate``; a drift in EITHER twin (a locally
# inlined or re-widened predicate) turns the structural pin RED. The
# behavioural half pins the load-bearing consequence: a DELIVERED
# (COMPLETED) prior under a DIFFERENT anchor no longer blocks the twin,
# while a READY prior under a different anchor still does (ARM B).

class TestMintGuardTwinLockstep:
    """gate-matrix.md §0 — dead-code twin pin (COMPLETED prior, different anchor)."""

    def test_both_twins_evaluate_the_one_shared_two_arm_predicate(self):
        import inspect

        from daemon.services import child_reports as module
        from sqlalchemy.dialects import sqlite

        twin_src = inspect.getsource(
            module.ChildReportsService._should_send_completion_report
        )
        live_src = inspect.getsource(
            module.ChildReportsService._process_child_completion_db_sync
        )
        builder_src = inspect.getsource(module._mint_guard_suppress_predicate)

        # The pin is a LOCKSTEP pin: either twin drifting away from the
        # shared builder is a failure, not just a wrong result here.
        assert "_mint_guard_suppress_predicate(" in twin_src, (
            "the dead-code twin no longer evaluates the shared guard"
        )
        assert "_mint_guard_suppress_predicate(" in live_src, (
            "the live inlined guard no longer evaluates the shared guard"
        )
        assert (
            'MessageQueue.source == f"internal_report:{instance_id}:'
            '{completed_message_id}"' in builder_src
        ), "ARM A (same-anchor equality) is missing from the builder"
        assert (
            'MessageQueue.source.startswith(f"internal_report:'
            '{instance_id}:", autoescape=True)' in builder_src
        ), "ARM B (child prefix, colon boundary) is missing from the builder"

        # Character-for-character status sets of the compiled predicate:
        # S-A = ('ready','processing','completed'), S-B excludes COMPLETED.
        sql = str(
            module._mint_guard_suppress_predicate("child-123", "msg-new-789")
            .compile(
                dialect=sqlite.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "'internal_report:child-123:msg-new-789'" in sql
        assert "status IN ('ready', 'processing', 'completed')" in sql
        assert "status IN ('ready', 'processing')" in sql
        assert "ESCAPE '/'" in sql, "autoescape=True must carry the ESCAPE clause"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("prior_status", "expected_send", "expected_reason"),
        [
            (MessageStatus.COMPLETED.value, True, "all_checks_passed"),
            (MessageStatus.READY.value, False, "idempotency_skip"),
        ],
        ids=["delivered-prior-unblocks-a-new-anchor", "outstanding-prior-still-blocks"],
    )
    async def test_twin_guard_splits_status_sets_by_arm(
        self, mock_manager, mock_events_service,
        prior_status, expected_send, expected_reason,
    ):
        # A REAL in-memory session — the guard is a SQL predicate, so a
        # MagicMock ``exec`` (which returns the seeded row unconditionally)
        # cannot prove that a COMPLETED prior under a different anchor is
        # actually filtered out.
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel, Session

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(Instance(
                instance_id="child-123",
                agent_id="developer",
                agent_name="developer",
                agent_dir="/tmp/developer",
                parent_id="parent-456",
                status=InstanceStatus.RUNNING.value,
                version=1,
                instance_metadata={},
            ))
            session.add(MessageQueue(
                message_id="msg-old",
                instance_id="parent-456",
                content="prior report",
                source="internal_report:child-123:anchor-old",
                type=MessageType.COMPLETION_REPORT.value,
                status=prior_status,
            ))
            session.commit()

            service = ChildReportsService(
                manager=mock_manager,
                events_service=mock_events_service,
            )
            should_send, reason = await service._should_send_completion_report(
                session, "child-123", completed_message_id="anchor-new"
            )

        assert (should_send, reason) == (expected_send, expected_reason)
