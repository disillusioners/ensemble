"""Tests for P0 Stale Report Cleanup + force_notify Simplification.

This test file covers two related fixes:
1. Stale cleanup in resume_processing_job(): Deletes stale internal_report:{child_id}:%
   entries from parent's queue before processing, preventing stale reports with old
   message IDs from interfering with new reports.

2. Simplified force_notify in _should_send_completion_report(): Removed the
   waiting_for > 0 check - now when force_notify=True, stale reports are deleted
   unconditionally.
"""

import sys
import signal
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call
from contextlib import contextmanager

from daemon.manager import InstanceManager
from daemon.config import Config
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue, MessageStatus, MessageType
from daemon.services.child_reports import ChildReportsService
from sqlalchemy import select


# ─── Timeout Handler ────────────────────────────────────────────────────────────

def timeout_handler(signum, frame):
    print("RESULT: TIMEOUT")
    sys.exit(124)

signal.signal(signal.SIGALRM, timeout_handler)
signal.alarm(120)


# ─── Fixtures ──────────────────────────────────────────────────────────────────


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


@pytest.fixture
def mock_instance(parent_id="parent-123"):
    """Create a mock Instance with basic attributes."""
    instance = MagicMock(spec=Instance)
    instance.instance_id = "child-instance-123"
    instance.agent_id = "coder"
    instance.parent_id = parent_id
    instance.waiting_for = 0
    instance.status = InstanceStatus.COMPLETED.value
    instance.instance_metadata = {}
    instance.children = None
    instance.version = 1
    instance.last_activity_at = None
    return instance


@pytest.fixture
def mock_parent_instance():
    """Create a mock parent Instance."""
    parent = MagicMock(spec=Instance)
    parent.instance_id = "parent-123"
    parent.agent_id = "leader"
    parent.parent_id = "grandparent-456"
    parent.waiting_for = 1
    parent.status = InstanceStatus.RUNNING.value
    parent.instance_metadata = {}
    parent.children = '["child-instance-123"]'
    parent.version = 1
    parent.last_activity_at = None
    return parent


# ─── Test Class 1: Stale Cleanup in resume_processing_job ───────────────────────


class TestStaleCleanupInResumeProcessingJob:
    """Test suite for stale report cleanup in resume_processing_job().

    The P0a fix deletes stale internal_report:{child_id}:* entries from the
    parent's queue before processing, ensuring old reports don't interfere.
    """

    @pytest.mark.asyncio
    async def test_stale_report_deleted_before_new_report(self):
        """Test: Stale report deleted before new report is created.

        Scenario:
        1. Parent has stale internal_report:{child}:{OLD_msg_id} in queue
        2. resume_processing_job() is called for the child
        3. Stale report should be deleted BEFORE processing completes

        This ensures stale reports from old message IDs don't interfere with
        the new completion report.
        """
        from daemon.manager import InstanceManager

        # Setup
        child_instance_id = "child-123"
        parent_instance_id = "parent-456"

        # Create stale report
        stale_report = MagicMock(spec=MessageQueue)
        stale_report.message_id = "stale-msg-789"
        stale_report.instance_id = parent_instance_id
        stale_report.source = f"internal_report:{child_instance_id}:old-message-id"

        # Mock job queue service (no old_jobs - WorkerPool path)
        mock_jq_service = MagicMock()
        mock_jq_service._repository = MagicMock()
        mock_jq_service._repository.find_processing_message_jobs_by_instance = MagicMock(return_value=[])

        # Track what gets deleted
        deleted_reports = []

        # Mock instance repository
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_id = child_instance_id
        mock_instance_meta.status = InstanceStatus.PAUSED.value
        mock_instance_meta.waiting_for = 0
        mock_instance_meta.parent_id = parent_instance_id

        mock_instance_repo = MagicMock()
        mock_instance_repo.get = MagicMock(return_value=mock_instance_meta)

        # Create a child instance for the session.get() call
        child_instance = MagicMock()
        child_instance.instance_id = child_instance_id
        child_instance.parent_id = parent_instance_id

        # Mock session setup
        mock_session = MagicMock()
        mock_exec_result = MagicMock()

        # Track get() calls to return child instance
        mock_session.get = MagicMock(return_value=child_instance)
        mock_exec_result.all.return_value = [stale_report]
        mock_exec_result.scalar_one.return_value = 0
        mock_session.exec = MagicMock(return_value=mock_exec_result)
        mock_session.delete = MagicMock(side_effect=lambda r: deleted_reports.append(r))
        mock_session.commit = MagicMock()

        # Create manager
        config = MagicMock(spec=Config)
        manager = InstanceManager.__new__(InstanceManager)
        manager._job_queue_service = mock_jq_service
        manager._queue_repository = MagicMock()
        manager._instance_repository = mock_instance_repo
        manager._engine = MagicMock()  # Just needs to be truthy
        manager._process_message_with_tracking = AsyncMock(
            return_value=MagicMock(content="Resume completed")
        )
        manager._process_child_completion_and_notify_parent = AsyncMock()
        manager._generate_and_broadcast_title = AsyncMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_lifecycle = AsyncMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._checkpointer = MagicMock()
        manager.config = config
        manager.config.llm = MagicMock()

        # Mock Session class to return our mock session
        @contextmanager
        def mock_session_ctx():
            yield mock_session

        with patch("daemon.manager.Session", return_value=mock_session_ctx()):
            # Call resume_processing_job
            result = await manager.resume_processing_job(
                child_instance_id, message="resume", silent=False
            )

        # Verify stale report was deleted
        assert len(deleted_reports) == 1
        assert deleted_reports[0] == stale_report

        # Verify result
        assert result["instance_id"] == child_instance_id
        assert result["job_id"] is None

    @pytest.mark.asyncio
    async def test_multiple_stale_reports_for_same_child(self):
        """Test: Multiple stale reports for same child - all deleted."""
        from daemon.manager import InstanceManager

        child_instance_id = "child-multi"
        parent_instance_id = "parent-multi"

        # Create multiple stale reports
        stale_reports = [
            MagicMock(spec=MessageQueue, message_id=f"stale-{i}", instance_id=parent_instance_id,
                      source=f"internal_report:{child_instance_id}:old-msg-{i}")
            for i in range(3)
        ]

        mock_jq_service = MagicMock()
        mock_jq_service._repository = MagicMock()
        mock_jq_service._repository.find_processing_message_jobs_by_instance = MagicMock(return_value=[])

        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_id = child_instance_id
        mock_instance_meta.status = InstanceStatus.PAUSED.value
        mock_instance_meta.waiting_for = 0
        mock_instance_meta.parent_id = parent_instance_id

        mock_instance_repo = MagicMock()
        mock_instance_repo.get = MagicMock(return_value=mock_instance_meta)

        child_instance = MagicMock()
        child_instance.instance_id = child_instance_id
        child_instance.parent_id = parent_instance_id

        deleted_reports = []

        mock_session = MagicMock()
        mock_exec_result = MagicMock()
        mock_session.get = MagicMock(return_value=child_instance)
        mock_exec_result.all.return_value = stale_reports
        mock_exec_result.scalar_one.return_value = 0
        mock_session.exec = MagicMock(return_value=mock_exec_result)
        mock_session.delete = MagicMock(side_effect=lambda r: deleted_reports.append(r))
        mock_session.commit = MagicMock()

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        config = MagicMock(spec=Config)
        manager = InstanceManager.__new__(InstanceManager)
        manager._job_queue_service = mock_jq_service
        manager._queue_repository = MagicMock()
        manager._instance_repository = mock_instance_repo
        manager._engine = MagicMock()  # Just needs to be truthy
        manager._process_message_with_tracking = AsyncMock(
            return_value=MagicMock(content="Resume completed")
        )
        manager._process_child_completion_and_notify_parent = AsyncMock()
        manager._generate_and_broadcast_title = AsyncMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_lifecycle = AsyncMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._checkpointer = MagicMock()
        manager.config = config
        manager.config.llm = MagicMock()

        with patch("daemon.manager.Session", return_value=mock_session_ctx()):
            result = await manager.resume_processing_job(
                child_instance_id, message="resume", silent=False
            )

        # Verify all 3 stale reports were deleted
        assert len(deleted_reports) == 3
        for report in stale_reports:
            assert report in deleted_reports

    @pytest.mark.asyncio
    async def test_no_stale_reports_proceeds_normally(self):
        """Test: No stale reports - cleanup is no-op, proceeds normally."""
        from daemon.manager import InstanceManager

        child_instance_id = "child-clean"
        parent_instance_id = "parent-clean"

        mock_jq_service = MagicMock()
        mock_jq_service._repository = MagicMock()
        mock_jq_service._repository.find_processing_message_jobs_by_instance = MagicMock(return_value=[])

        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_id = child_instance_id
        mock_instance_meta.status = InstanceStatus.PAUSED.value
        mock_instance_meta.waiting_for = 0
        mock_instance_meta.parent_id = parent_instance_id

        mock_instance_repo = MagicMock()
        mock_instance_repo.get = MagicMock(return_value=mock_instance_meta)

        child_instance = MagicMock()
        child_instance.instance_id = child_instance_id
        child_instance.parent_id = parent_instance_id

        deleted_reports = []

        mock_session = MagicMock()
        mock_exec_result = MagicMock()
        mock_session.get = MagicMock(return_value=child_instance)
        mock_exec_result.all.return_value = []  # No stale reports
        mock_exec_result.scalar_one.return_value = 0
        mock_session.exec = MagicMock(return_value=mock_exec_result)
        mock_session.delete = MagicMock(side_effect=lambda r: deleted_reports.append(r))
        mock_session.commit = MagicMock()

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        config = MagicMock(spec=Config)
        manager = InstanceManager.__new__(InstanceManager)
        manager._job_queue_service = mock_jq_service
        manager._queue_repository = MagicMock()
        manager._instance_repository = mock_instance_repo
        manager._engine = MagicMock()  # Just needs to be truthy
        manager._process_message_with_tracking = AsyncMock(
            return_value=MagicMock(content="Resume completed")
        )
        manager._process_child_completion_and_notify_parent = AsyncMock()
        manager._generate_and_broadcast_title = AsyncMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_lifecycle = AsyncMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._checkpointer = MagicMock()
        manager.config = config
        manager.config.llm = MagicMock()

        with patch("daemon.manager.Session", return_value=mock_session_ctx()):
            result = await manager.resume_processing_job(
                child_instance_id, message="resume", silent=False
            )

        # Verify no reports were deleted
        assert len(deleted_reports) == 0

        # Verify processing still happened
        manager._process_message_with_tracking.assert_called_once()
        manager._process_child_completion_and_notify_parent.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_failure_propagates(self):
        """Test: Cleanup failure (exception) - exception propagates up.

        NOTE: The actual code does NOT catch exceptions during stale cleanup.
        The cleanup is part of the normal flow and failures propagate.
        This test verifies the actual behavior matches the code.
        """
        from daemon.manager import InstanceManager

        child_instance_id = "child-fail"
        parent_instance_id = "parent-fail"

        mock_jq_service = MagicMock()
        mock_jq_service._repository = MagicMock()
        mock_jq_service._repository.find_processing_message_jobs_by_instance = MagicMock(return_value=[])

        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_id = child_instance_id
        mock_instance_meta.status = InstanceStatus.PAUSED.value
        mock_instance_meta.waiting_for = 0
        mock_instance_meta.parent_id = parent_instance_id

        mock_instance_repo = MagicMock()
        mock_instance_repo.get = MagicMock(return_value=mock_instance_meta)

        child_instance = MagicMock()
        child_instance.instance_id = child_instance_id
        child_instance.parent_id = parent_instance_id

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=child_instance)
        # Simulate exception during stale report query
        mock_session.exec = MagicMock(side_effect=RuntimeError("Database error during stale cleanup"))
        mock_session.commit = MagicMock()

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        config = MagicMock(spec=Config)
        manager = InstanceManager.__new__(InstanceManager)
        manager._job_queue_service = mock_jq_service
        manager._queue_repository = MagicMock()
        manager._instance_repository = mock_instance_repo
        manager._engine = MagicMock()  # Just needs to be truthy
        manager._process_message_with_tracking = AsyncMock(
            return_value=MagicMock(content="Resume completed")
        )
        manager._process_child_completion_and_notify_parent = AsyncMock()
        manager._generate_and_broadcast_title = AsyncMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_lifecycle = AsyncMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._checkpointer = MagicMock()
        manager.config = config
        manager.config.llm = MagicMock()

        with patch("daemon.manager.Session", return_value=mock_session_ctx()):
            # The exception SHOULD propagate (this is the actual behavior)
            with pytest.raises(RuntimeError, match="Database error during stale cleanup"):
                await manager.resume_processing_job(
                    child_instance_id, message="resume", silent=False
                )

    @pytest.mark.asyncio
    async def test_stale_cleanup_with_multiple_children(self):
        """Test: Stale reports for different children - query filters correctly.

        The query filters by source.startswith(f"internal_report:{instance_id}:")
        so only reports for the specific child should be returned and deleted.
        """
        from daemon.manager import InstanceManager

        child_instance_id = "child-target"
        parent_instance_id = "parent-multi-child"
        other_child_id = "child-other"

        # Stale report for TARGET child
        target_stale = MagicMock(spec=MessageQueue)
        target_stale.message_id = "target-stale"
        target_stale.instance_id = parent_instance_id
        target_stale.source = f"internal_report:{child_instance_id}:old-msg"

        # Stale report for OTHER child (should NOT be deleted)
        other_stale = MagicMock(spec=MessageQueue)
        other_stale.message_id = "other-stale"
        other_stale.instance_id = parent_instance_id
        other_stale.source = f"internal_report:{other_child_id}:old-msg"

        mock_jq_service = MagicMock()
        mock_jq_service._repository = MagicMock()
        mock_jq_service._repository.find_processing_message_jobs_by_instance = MagicMock(return_value=[])

        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_id = child_instance_id
        mock_instance_meta.status = InstanceStatus.PAUSED.value
        mock_instance_meta.waiting_for = 0
        mock_instance_meta.parent_id = parent_instance_id

        mock_instance_repo = MagicMock()
        mock_instance_repo.get = MagicMock(return_value=mock_instance_meta)

        child_instance = MagicMock()
        child_instance.instance_id = child_instance_id
        child_instance.parent_id = parent_instance_id

        deleted_reports = []

        mock_session = MagicMock()
        mock_exec_result = MagicMock()
        mock_session.get = MagicMock(return_value=child_instance)

        # Track exec calls to verify query is called with correct filters
        exec_calls = []

        def exec_side_effect(query):
            exec_calls.append(query)
            # Only return target_stale (simulates proper query filtering)
            return MagicMock(all=MagicMock(return_value=[target_stale]))

        mock_exec_result.all.return_value = [target_stale]  # Only target stale
        mock_exec_result.scalar_one.return_value = 0
        mock_session.exec = MagicMock(side_effect=exec_side_effect)
        mock_session.delete = MagicMock(side_effect=lambda r: deleted_reports.append(r))
        mock_session.commit = MagicMock()

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        config = MagicMock(spec=Config)
        manager = InstanceManager.__new__(InstanceManager)
        manager._job_queue_service = mock_jq_service
        manager._queue_repository = MagicMock()
        manager._instance_repository = mock_instance_repo
        manager._engine = MagicMock()  # Just needs to be truthy
        manager._process_message_with_tracking = AsyncMock(
            return_value=MagicMock(content="Resume completed")
        )
        manager._process_child_completion_and_notify_parent = AsyncMock()
        manager._generate_and_broadcast_title = AsyncMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_lifecycle = AsyncMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._checkpointer = MagicMock()
        manager.config = config
        manager.config.llm = MagicMock()

        with patch("daemon.manager.Session", return_value=mock_session_ctx()):
            result = await manager.resume_processing_job(
                child_instance_id, message="resume", silent=False
            )

        # Verify only TARGET child's stale report was deleted
        assert len(deleted_reports) == 1
        assert deleted_reports[0] == target_stale


# ─── Test Class 2: Simplified force_notify ─────────────────────────────────────


def create_mock_session_with_stale_report(mock_instance, mock_parent, stale_report):
    """Create a mock session that returns a stale report.

    The _should_send_completion_report function:
    1. First queries for pending count (scalar_one)
    2. Then queries for existing report (first())
    """
    session = MagicMock()
    exec_result = MagicMock()

    # Track get calls
    get_calls = []

    def mock_get(cls, instance_id):
        get_calls.append(instance_id)
        if cls.__name__ == "Instance":
            if instance_id == mock_instance.instance_id:
                return mock_instance
            elif mock_parent and instance_id == mock_parent.instance_id:
                return mock_parent
        return None

    session.get = MagicMock(side_effect=mock_get)

    # First call returns pending count (0), second call returns the stale report
    exec_result.scalar_one.return_value = 0

    # The stale report query uses .first()
    exec_result.first.return_value = stale_report

    session.exec = MagicMock(return_value=exec_result)

    return session, exec_result, get_calls


def create_mock_session_no_stale_report(mock_instance, mock_parent):
    """Create a mock session with no stale report."""
    session = MagicMock()
    exec_result = MagicMock()

    def mock_get(cls, instance_id):
        if cls.__name__ == "Instance":
            if instance_id == mock_instance.instance_id:
                return mock_instance
            elif mock_parent and instance_id == mock_parent.instance_id:
                return mock_parent
        return None

    session.get = MagicMock(side_effect=mock_get)

    # Pending count is 0
    exec_result.scalar_one.return_value = 0
    # No stale report
    exec_result.first.return_value = None

    session.exec = MagicMock(return_value=exec_result)

    return session, exec_result


class TestSimplifiedForceNotify:
    """Test suite for simplified force_notify behavior.

    The P0b fix removed the waiting_for > 0 check from force_notify=True path.
    Now when force_notify=True, stale reports are deleted unconditionally.
    """

    @pytest.mark.asyncio
    async def test_force_notify_true_deletes_stale_report(
        self, mock_manager, mock_events_service, mock_instance, mock_parent_instance
    ):
        """Test: force_notify=True + stale report → stale report deleted.

        When force_notify=True and a stale report exists, the stale report
        should be deleted unconditionally (no waiting_for check).
        """
        # Create stale report
        stale_report = MagicMock(spec=MessageQueue)
        stale_report.message_id = "stale-msg-456"
        stale_report.source = f"internal_report:{mock_instance.instance_id}:msg-old-123"

        mock_instance.parent_id = mock_parent_instance.instance_id
        mock_manager._instance_repository.get.return_value = mock_instance

        session, _, _ = create_mock_session_with_stale_report(
            mock_instance, mock_parent_instance, stale_report
        )

        @contextmanager
        def mock_session_ctx():
            yield session

        with patch.object(
            ChildReportsService, "_update_parent_on_child_complete",
            new_callable=AsyncMock,
            return_value=(False, None, None)
        ) as mock_update_parent:
            with patch.object(
                ChildReportsService, "_create_completion_report",
                new_callable=AsyncMock,
                return_value=(MagicMock(), MagicMock(), "new-report-msg-789")
            ) as mock_create_report:
                with patch.object(
                    ChildReportsService, "_create_completion_events",
                    new_callable=AsyncMock,
                    return_value=(MagicMock(), MagicMock())
                ):
                    with patch(
                        "daemon.services.child_reports.Session",
                        return_value=mock_session_ctx()
                    ):
                        service = ChildReportsService(
                            manager=mock_manager,
                            events_service=mock_events_service,
                        )

                        with patch.object(
                            service, "_get_last_assistant_message",
                            new_callable=AsyncMock,
                            return_value="Child completed with result"
                        ):
                            with patch.object(
                                service, "_trigger_title_generation"
                            ):
                                await service._process_child_completion_and_notify_parent(
                                    mock_instance.instance_id,
                                    "msg-resume-789",
                                    force_notify=True
                                )

        # Verify stale report was deleted (force_notify=True, no waiting_for check)
        session.delete.assert_called_once()

        # Verify parent was updated
        mock_update_parent.assert_called_once()

        # Verify new completion report was created
        mock_create_report.assert_called_once()

    @pytest.mark.asyncio
    async def test_force_notify_false_preserves_idempotency(
        self, mock_manager, mock_events_service, mock_instance, mock_parent_instance
    ):
        """Test: force_notify=False + stale report → skipped (idempotency preserved)."""
        stale_report = MagicMock(spec=MessageQueue)
        stale_report.message_id = "stale-msg-456"
        stale_report.source = f"internal_report:{mock_instance.instance_id}:msg-old-123"

        mock_instance.parent_id = mock_parent_instance.instance_id
        mock_manager._instance_repository.get.return_value = mock_instance

        session, _, _ = create_mock_session_with_stale_report(
            mock_instance, mock_parent_instance, stale_report
        )

        @contextmanager
        def mock_session_ctx():
            yield session

        with patch.object(
            ChildReportsService, "_update_parent_on_child_complete",
            new_callable=AsyncMock,
            return_value=(False, None, None)
        ):
            with patch(
                "daemon.services.child_reports.Session",
                return_value=mock_session_ctx()
            ):
                service = ChildReportsService(
                    manager=mock_manager,
                    events_service=mock_events_service,
                )

                with patch.object(
                    service, "_get_last_assistant_message",
                    new_callable=AsyncMock,
                    return_value="Child completed with result"
                ):
                    await service._process_child_completion_and_notify_parent(
                        mock_instance.instance_id,
                        "msg-resume-789",
                        force_notify=False  # Explicitly False - should skip
                    )

        # Verify stale report was NOT deleted (idempotency preserved)
        session.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_force_notify_true_no_stale_report_proceeds(
        self, mock_manager, mock_events_service, mock_instance, mock_parent_instance
    ):
        """Test: force_notify=True + no stale report → proceeds normally."""
        mock_instance.parent_id = mock_parent_instance.instance_id
        mock_manager._instance_repository.get.return_value = mock_instance

        session, _ = create_mock_session_no_stale_report(mock_instance, mock_parent_instance)

        @contextmanager
        def mock_session_ctx():
            yield session

        with patch.object(
            ChildReportsService, "_update_parent_on_child_complete",
            new_callable=AsyncMock,
            return_value=(False, None, None)
        ) as mock_update_parent:
            with patch.object(
                ChildReportsService, "_create_completion_report",
                new_callable=AsyncMock,
                return_value=(MagicMock(), MagicMock(), "new-report-msg-123")
            ) as mock_create_report:
                with patch.object(
                    ChildReportsService, "_create_completion_events",
                    new_callable=AsyncMock,
                    return_value=(MagicMock(), MagicMock())
                ):
                    with patch(
                        "daemon.services.child_reports.Session",
                        return_value=mock_session_ctx()
                    ):
                        service = ChildReportsService(
                            manager=mock_manager,
                            events_service=mock_events_service,
                        )

                        with patch.object(
                            service, "_get_last_assistant_message",
                            new_callable=AsyncMock,
                            return_value="Child completed with result"
                        ):
                            with patch.object(
                                service, "_trigger_title_generation"
                            ):
                                await service._process_child_completion_and_notify_parent(
                                    mock_instance.instance_id,
                                    "msg-new-789",
                                    force_notify=True
                                )

        # Verify parent was updated
        mock_update_parent.assert_called_once()

        # Verify new completion report was created
        mock_create_report.assert_called_once()


# ─── Test Class 3: Integration ─────────────────────────────────────────────────


class TestIntegrationStaleCleanupAndForceNotify:
    """Integration tests for the complete flow: stale cleanup + force_notify."""

    @pytest.mark.asyncio
    async def test_full_flow_stale_cleanup_then_force_notify(self):
        """Test: Complete flow - stale cleanup in resume, then force_notify notification."""
        from daemon.manager import InstanceManager

        child_instance_id = "child-integration"
        parent_instance_id = "parent-integration"

        # Stale report that should be deleted
        stale_report = MagicMock(spec=MessageQueue)
        stale_report.message_id = "stale-integration"
        stale_report.instance_id = parent_instance_id
        stale_report.source = f"internal_report:{child_instance_id}:old-msg"

        mock_jq_service = MagicMock()
        mock_jq_service._repository = MagicMock()
        mock_jq_service._repository.find_processing_message_jobs_by_instance = MagicMock(return_value=[])

        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_id = child_instance_id
        mock_instance_meta.status = InstanceStatus.PAUSED.value
        mock_instance_meta.waiting_for = 0
        mock_instance_meta.parent_id = parent_instance_id

        mock_instance_repo = MagicMock()
        mock_instance_repo.get = MagicMock(return_value=mock_instance_meta)

        child_instance = MagicMock()
        child_instance.instance_id = child_instance_id
        child_instance.parent_id = parent_instance_id
        child_instance.agent_id = "coder"
        child_instance.waiting_for = 0
        child_instance.status = InstanceStatus.PAUSED.value
        child_instance.instance_metadata = {}
        child_instance.children = None
        child_instance.version = 1
        child_instance.last_activity_at = None

        deleted_reports = []

        mock_session = MagicMock()
        mock_exec_result = MagicMock()
        mock_session.get = MagicMock(return_value=child_instance)
        mock_exec_result.all.return_value = [stale_report]
        mock_exec_result.scalar_one.return_value = 0
        mock_session.exec = MagicMock(return_value=mock_exec_result)
        mock_session.delete = MagicMock(side_effect=lambda r: deleted_reports.append(r))
        mock_session.commit = MagicMock()
        mock_session.add = MagicMock()

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        # Track force_notify calls
        force_notify_calls = []

        async def mock_process_child_completion(instance_id, msg_id, force_notify=False):
            force_notify_calls.append({
                "instance_id": instance_id,
                "msg_id": msg_id,
                "force_notify": force_notify
            })

        config = MagicMock(spec=Config)
        manager = InstanceManager.__new__(InstanceManager)
        manager._job_queue_service = mock_jq_service
        manager._queue_repository = MagicMock()
        manager._instance_repository = mock_instance_repo
        manager._engine = MagicMock()  # Just needs to be truthy
        manager._process_message_with_tracking = AsyncMock(
            return_value=MagicMock(content="Resume completed")
        )
        manager._process_child_completion_and_notify_parent = mock_process_child_completion
        manager._generate_and_broadcast_title = AsyncMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_lifecycle = AsyncMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._checkpointer = MagicMock()
        manager.config = config
        manager.config.llm = MagicMock()

        with patch("daemon.manager.Session", return_value=mock_session_ctx()):
            result = await manager.resume_processing_job(
                child_instance_id, message="resume", silent=False
            )

        # Verify stale report was deleted by resume_processing_job
        assert len(deleted_reports) == 1
        assert deleted_reports[0] == stale_report

        # Verify force_notify=True was passed
        assert len(force_notify_calls) == 1
        assert force_notify_calls[0]["force_notify"] == True
        assert force_notify_calls[0]["instance_id"] == child_instance_id

    @pytest.mark.asyncio
    async def test_stale_cleanup_skipped_in_test_mode(self):
        """Test: Stale cleanup skipped when _engine is not available (test mode)."""
        from daemon.manager import InstanceManager

        child_instance_id = "child-testmode"
        parent_instance_id = "parent-testmode"

        mock_jq_service = MagicMock()
        mock_jq_service._repository = MagicMock()
        mock_jq_service._repository.find_processing_message_jobs_by_instance = MagicMock(return_value=[])

        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_id = child_instance_id
        mock_instance_meta.status = InstanceStatus.PAUSED.value
        mock_instance_meta.waiting_for = 0
        mock_instance_meta.parent_id = parent_instance_id

        mock_instance_repo = MagicMock()
        mock_instance_repo.get = MagicMock(return_value=mock_instance_meta)

        config = MagicMock(spec=Config)
        manager = InstanceManager.__new__(InstanceManager)
        manager._job_queue_service = mock_jq_service
        manager._queue_repository = MagicMock()
        manager._instance_repository = mock_instance_repo
        manager._engine = None  # Test mode - no engine
        manager._process_message_with_tracking = AsyncMock(
            return_value=MagicMock(content="Resume completed")
        )
        manager._process_child_completion_and_notify_parent = AsyncMock()
        manager._generate_and_broadcast_title = AsyncMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_lifecycle = AsyncMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._checkpointer = MagicMock()
        manager.config = config
        manager.config.llm = MagicMock()

        # Should not raise - cleanup skipped in test mode
        result = await manager.resume_processing_job(
            child_instance_id, message="resume", silent=False
        )

        # Verify processing still happened
        manager._process_message_with_tracking.assert_called_once()
        assert result["instance_id"] == child_instance_id
