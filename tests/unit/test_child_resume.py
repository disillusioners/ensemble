"""Tests for child instance resume in resume_processing_job.

Child instances (sub-agents in a tree) don't have JobQueue entries — they use
WorkerPool directly. When resuming a child instance, the code should call
_process_message_with_tracking directly instead of looking for old jobs.

These tests verify the fix in the `if not old_jobs:` branch of resume_processing_job.
"""

import uuid
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from daemon.manager import InstanceManager
from daemon.config import Config
from daemon.cancellation import CancellationTokenSource
from daemon.repositories.instance.models import InstanceStatus


class MockInstanceMeta:
    """Mock instance metadata returned by _instance_repository.get."""

    def __init__(self, instance_id: str = "test-instance", status: str = InstanceStatus.PAUSED.value):
        self.instance_id = instance_id
        self.status = status


class MockMessageResult:
    """Mock message result returned by _process_message_with_tracking."""

    def __init__(self, content: str = "Resume completed"):
        self.content = content


@pytest.fixture
def mock_job_queue_service():
    """Create mock job queue service with repository."""
    service = MagicMock()
    service._repository = MagicMock()
    service.complete_job = AsyncMock()
    return service


@pytest.fixture
def mock_queue_repository():
    """Create mock queue repository."""
    repo = MagicMock()
    repo.complete = MagicMock()
    return repo


@pytest.fixture
def mock_instance_repository():
    """Create mock instance repository."""
    repo = MagicMock()
    return repo


@pytest.fixture
def mock_manager(mock_job_queue_service, mock_queue_repository, mock_instance_repository):
    """Create mock manager with all required dependencies."""
    manager = MagicMock()
    manager._job_queue_service = mock_job_queue_service
    manager._queue_repository = mock_queue_repository
    manager._instance_repository = mock_instance_repository
    manager._process_message_with_tracking = AsyncMock(return_value=MockMessageResult())
    return manager


@pytest.fixture
def instance_manager(mock_manager):
    """Create InstanceManager with mocked dependencies."""
    config = MagicMock(spec=Config)
    manager = InstanceManager.__new__(InstanceManager)
    manager._job_queue_service = mock_manager._job_queue_service
    manager._queue_repository = mock_manager._queue_repository
    manager._instance_repository = mock_manager._instance_repository
    manager._process_message_with_tracking = mock_manager._process_message_with_tracking
    return manager


class TestChildInstanceResume:
    """Test suite for child instance resume behavior."""

    @pytest.mark.asyncio
    async def test_child_resume_non_silent_target_resume(self, instance_manager, mock_manager):
        """Scenario 1: Child instance resume (non-silent / target resume).

        Verify _process_message_with_tracking is called with correct args
        when resuming a child instance with silent=False.
        """
        instance_id = "child-instance-123"

        # No old jobs (child instance uses WorkerPool)
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        # Instance meta exists with PAUSED status
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id, status=InstanceStatus.PAUSED.value)
        )

        # Call resume_processing_job with silent=False
        result = await instance_manager.resume_processing_job(
            instance_id, message="continue working", silent=False
        )

        # Verify _process_message_with_tracking was called
        mock_manager._process_message_with_tracking.assert_called_once()
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs

        # Verify correct args
        assert call_kwargs["instance_id"] == instance_id
        assert call_kwargs["message"] == "continue working"
        assert call_kwargs["is_retry"] is False  # silent=False means is_retry=False
        assert call_kwargs["message_source"] == "cascade_resume"

        # Verify message_id is a fresh UUID
        try:
            uuid.UUID(call_kwargs["message_id"])
        except ValueError:
            pytest.fail(f"message_id should be a valid UUID, got: {call_kwargs['message_id']}")

        # Verify cancellation_token is a CancellationToken
        assert hasattr(call_kwargs["cancellation_token"], "is_cancelled")

        # Verify return value includes the generated message_id
        assert result["instance_id"] == instance_id
        assert result["job_id"] is None
        assert result["message_id"] == call_kwargs["message_id"]

    @pytest.mark.asyncio
    async def test_child_resume_silent_cascade_resume(self, instance_manager, mock_manager):
        """Scenario 2: Child instance resume (silent / cascade resume).

        Verify is_retry=True when silent=True (cascade resume for children).
        """
        instance_id = "child-instance-456"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        # Instance meta exists with RUNNING status
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id, status=InstanceStatus.RUNNING.value)
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        mock_manager._process_message_with_tracking.assert_called_once()
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs

        # Verify is_retry=True for silent mode
        assert call_kwargs["is_retry"] is True
        assert call_kwargs["message_source"] == "cascade_resume"

        # Verify return value includes the generated message_id
        assert result["instance_id"] == instance_id
        assert result["job_id"] is None
        assert result["message_id"] == call_kwargs["message_id"]

    @pytest.mark.asyncio
    async def test_child_resume_cancelled_error_handling(self, instance_manager, mock_manager):
        """Scenario 3: CancelledError handling.

        Verify that asyncio.CancelledError is caught and returns None
        instead of propagating.
        """
        instance_id = "child-instance-789"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id)
        )

        # Simulate CancelledError from _process_message_with_tracking
        mock_manager._process_message_with_tracking.side_effect = asyncio.CancelledError()

        # Should return None, not raise
        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_child_resume_general_exception_raised(self, instance_manager, mock_manager):
        """Scenario 4: General exception re-raised.

        Verify RuntimeError and other exceptions are re-raised.
        """
        instance_id = "child-instance-error"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id)
        )

        # Simulate RuntimeError
        mock_manager._process_message_with_tracking.side_effect = RuntimeError("something broke")

        # Should re-raise RuntimeError
        with pytest.raises(RuntimeError, match="something broke"):
            await instance_manager.resume_processing_job(
                instance_id, message="resume", silent=True
            )

    @pytest.mark.asyncio
    async def test_child_resume_instance_not_found(self, instance_manager, mock_manager):
        """Scenario 5: Instance not found (meta is None).

        Verify the code handles missing instance metadata gracefully.
        """
        instance_id = "nonexistent-instance"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        # Instance meta is None
        mock_manager._instance_repository.get = MagicMock(return_value=None)

        # Should not crash, still calls _process_message_with_tracking
        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        # Verify it proceeded (even though meta was None)
        mock_manager._process_message_with_tracking.assert_called_once()
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs
        assert call_kwargs["instance_id"] == instance_id

    @pytest.mark.asyncio
    async def test_child_resume_unexpected_state(self, instance_manager, mock_manager):
        """Scenario 6: Instance in unexpected state.

        Verify the code logs a warning but still proceeds when instance
        is in a state other than PAUSED or RUNNING.
        """
        instance_id = "completed-instance"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        # Instance meta with COMPLETED status (unexpected)
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id, status="COMPLETED")
        )

        # Should not crash, still proceeds
        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        # Verify it proceeded despite unexpected state
        mock_manager._process_message_with_tracking.assert_called_once()

    @pytest.mark.asyncio
    async def test_child_resume_fresh_uuid_each_call(self, instance_manager, mock_manager):
        """Scenario 7: Fresh UUID generated for message_id.

        Verify that each call to resume_processing_job generates a different message_id.
        """
        instance_id = "child-instance-duplicate"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id)
        )

        # First resume
        result1 = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )
        call_kwargs1 = mock_manager._process_message_with_tracking.call_args.kwargs
        message_id1 = call_kwargs1["message_id"]

        # Reset mock for second call
        mock_manager._process_message_with_tracking.reset_mock()

        # Second resume
        result2 = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )
        call_kwargs2 = mock_manager._process_message_with_tracking.call_args.kwargs
        message_id2 = call_kwargs2["message_id"]

        # Both should be valid UUIDs and different from each other
        assert message_id1 != message_id2, "Each resume should generate a unique message_id"

        try:
            uuid.UUID(message_id1)
            uuid.UUID(message_id2)
        except ValueError:
            pytest.fail("message_ids should be valid UUIDs")

    @pytest.mark.asyncio
    async def test_child_resume_cancellation_token_created(self, instance_manager, mock_manager):
        """Scenario 8: CancellationTokenSource created.

        Verify that a CancellationTokenSource is created and its token
        is passed to _process_message_with_tracking.
        """
        instance_id = "child-instance-token"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id)
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )

        mock_manager._process_message_with_tracking.assert_called_once()
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs

        # Verify cancellation_token is a real CancellationToken (not just a mock)
        token = call_kwargs["cancellation_token"]
        assert token is not None
        # The token should have the interface of CancellationToken
        assert hasattr(token, "is_cancelled")
        # Fresh token should not be cancelled (is_cancelled is a property, not a method)
        assert token.is_cancelled is False


# ─── Test Class 2: Stale Completion Report Cleanup ─────────────────────────────────


class MockInstanceMetaWithParent:
    """Mock instance metadata with parent_id for testing stale report cleanup."""

    def __init__(
        self,
        instance_id: str = "child-instance-123",
        status: str = InstanceStatus.PAUSED.value,
        parent_id: str = "parent-123"
    ):
        self.instance_id = instance_id
        self.status = status
        self.parent_id = parent_id


class MockStaleReport:
    """Mock stale completion report message."""

    def __init__(self, message_id: str, source: str, parent_id: str = "parent-123"):
        self.message_id = message_id
        self.source = source
        self.instance_id = parent_id  # Parent's queue


class TestStaleCompletionReportCleanup:
    """Test suite for stale completion report cleanup during resume.

    These tests verify that resume_processing_job() cleans up stale completion
    reports from the parent's message queue before resuming a child instance.
    This prevents the parent from receiving outdated notifications with old msg_ids.
    """

    def _create_stale_test_mocks(
        self,
        instance_id: str,
        parent_id: str,
        stale_reports: list = None,
        mock_process_child: bool = True
    ):
        """Create mocks for stale report cleanup tests.

        Args:
            instance_id: The child instance ID
            parent_id: The parent instance ID
            stale_reports: List of stale reports to return from query
            mock_process_child: Whether to mock _process_child_completion_and_notify_parent

        Returns:
            Tuple of (instance_manager, mock_manager, mock_session, session_context)
        """
        from contextlib import contextmanager

        # Create mock job queue service
        mock_job_queue_service = MagicMock()
        mock_job_queue_service._repository = MagicMock()
        mock_job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(return_value=[])

        # Create mock queue repository
        mock_queue_repository = MagicMock()

        # Create mock instance repository
        mock_instance_repository = MagicMock()
        mock_instance_repository.get = MagicMock(
            return_value=MockInstanceMetaWithParent(instance_id=instance_id, parent_id=parent_id)
        )

        # Create mock manager
        mock_manager = MagicMock()
        mock_manager._job_queue_service = mock_job_queue_service
        mock_manager._queue_repository = mock_queue_repository
        mock_manager._instance_repository = mock_instance_repository
        mock_manager._process_message_with_tracking = AsyncMock(return_value=MockMessageResult())

        # Mock _process_child_completion_and_notify_parent if requested
        if mock_process_child:
            mock_manager._process_child_completion_and_notify_parent = AsyncMock()

        # Create mock engine for Session
        mock_engine = MagicMock()

        # Create mock session
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=MockInstanceMetaWithParent(instance_id=instance_id, parent_id=parent_id))
        exec_result = MagicMock()
        exec_result.all = MagicMock(return_value=stale_reports or [])
        mock_session.exec = MagicMock(return_value=exec_result)
        mock_session.delete = MagicMock()

        # Create context manager that yields the mock session
        @contextmanager
        def session_context(*args, **kwargs):
            yield mock_session

        # Create instance manager with all mocks
        instance_manager = InstanceManager.__new__(InstanceManager)
        instance_manager._job_queue_service = mock_job_queue_service
        instance_manager._queue_repository = mock_queue_repository
        instance_manager._instance_repository = mock_instance_repository
        instance_manager._process_message_with_tracking = mock_manager._process_message_with_tracking
        instance_manager._engine = mock_engine  # Enable Session usage

        if mock_process_child:
            instance_manager._process_child_completion_and_notify_parent = mock_manager._process_child_completion_and_notify_parent

        return instance_manager, mock_manager, mock_session, session_context

    @pytest.mark.asyncio
    async def test_stale_reports_are_cleaned_up_during_resume(self):
        """Test 1: Stale reports are cleaned up during resume.

        Scenario:
        1. Child instance has a parent
        2. Parent's queue has a stale completion report (with old msg_id)
        3. resume_processing_job() is called
        4. Stale report should be deleted from parent's queue
        5. Fresh report (with new msg_id) will be created after completion
        """
        instance_id = "child-instance-123"
        parent_id = "parent-123"
        stale_msg_id = "msg-old-stale-123"

        # Create stale report
        stale_report = MockStaleReport(
            message_id=stale_msg_id,
            source=f"internal_report:{instance_id}:{stale_msg_id}",
            parent_id=parent_id
        )

        # Setup manager with mocks
        instance_manager, mock_manager, mock_session, session_context = self._create_stale_test_mocks(
            instance_id=instance_id,
            parent_id=parent_id,
            stale_reports=[stale_report]
        )

        # Patch Session to use our mock
        with patch("daemon.manager.Session", session_context):
            result = await instance_manager.resume_processing_job(
                instance_id, message="resume", silent=False
            )

        # Verify _process_message_with_tracking was called
        mock_manager._process_message_with_tracking.assert_called_once()

        # Verify stale report was deleted
        assert mock_session.delete.called, \
            "Expected session.delete() to be called to remove stale report"
        mock_session.delete.assert_called_with(stale_report)

        # Verify session.commit() was called after deleting stale reports
        assert mock_session.commit.called, \
            "Expected session.commit() to be called after deleting stale reports"

        # Verify _process_child_completion_and_notify_parent was called with fresh message_id
        mock_manager._process_child_completion_and_notify_parent.assert_called_once()
        call_args = mock_manager._process_child_completion_and_notify_parent.call_args
        assert call_args[0][0] == instance_id  # instance_id

        # The second arg should be the fresh message_id (a UUID)
        fresh_msg_id = call_args[0][1]
        try:
            uuid.UUID(fresh_msg_id)
        except ValueError:
            pytest.fail(f"Expected fresh message_id to be a UUID, got: {fresh_msg_id}")

        # Verify force_notify=True
        assert call_args[1]["force_notify"] is True

    @pytest.mark.asyncio
    async def test_multiple_stale_reports_for_same_child_are_all_cleaned_up(self):
        """Test 2: Multiple stale reports for same child are all cleaned up.

        Scenario:
        1. Child instance has multiple stale reports from multiple failed attempts
        2. resume_processing_job() is called
        3. ALL stale reports should be deleted
        4. Only the fresh report should remain after completion
        """
        instance_id = "child-instance-multi"
        parent_id = "parent-multi"

        # Create multiple stale reports with different msg_ids
        stale_report_1 = MockStaleReport(
            message_id="msg-old-attempt-1",
            source=f"internal_report:{instance_id}:msg-old-attempt-1",
            parent_id=parent_id
        )
        stale_report_2 = MockStaleReport(
            message_id="msg-old-attempt-2",
            source=f"internal_report:{instance_id}:msg-old-attempt-2",
            parent_id=parent_id
        )
        stale_report_3 = MockStaleReport(
            message_id="msg-old-attempt-3",
            source=f"internal_report:{instance_id}:msg-old-attempt-3",
            parent_id=parent_id
        )
        stale_reports = [stale_report_1, stale_report_2, stale_report_3]

        # Setup manager with mocks
        instance_manager, mock_manager, mock_session, session_context = self._create_stale_test_mocks(
            instance_id=instance_id,
            parent_id=parent_id,
            stale_reports=stale_reports
        )

        with patch("daemon.manager.Session", session_context):
            result = await instance_manager.resume_processing_job(
                instance_id, message="resume", silent=False
            )

        # Verify all 3 stale reports were deleted
        assert mock_session.delete.call_count == 3, \
            f"Expected 3 stale reports to be deleted, got {mock_session.delete.call_count} calls"

        # Verify session.commit() was called after deleting stale reports
        assert mock_session.commit.called, \
            "Expected session.commit() to be called after deleting stale reports"

        # Verify _process_child_completion_and_notify_parent was called once
        mock_manager._process_child_completion_and_notify_parent.assert_called_once()

    @pytest.mark.asyncio
    async def test_stale_reports_for_other_children_are_not_cleaned_up(self):
        """Test 3: Stale reports for OTHER children are NOT cleaned up.

        Scenario:
        1. Child A has a stale report
        2. Child B has a stale report (but should NOT be returned by query)
        3. Resume child A
        4. Only child A's stale report should be deleted
        5. Child B's stale report should remain (not queried)
        """
        instance_id_a = "child-a"
        instance_id_b = "child-b"
        parent_id = "parent-shared"

        # Create stale reports for both children
        stale_report_a = MockStaleReport(
            message_id="msg-old-a",
            source=f"internal_report:{instance_id_a}:msg-old-a",
            parent_id=parent_id
        )
        # Note: stale_report_b is NOT included in the query results - this simulates
        # the query only returning reports for the specific child being resumed

        # Setup manager - only return child A's report
        instance_manager, mock_manager, mock_session, session_context = self._create_stale_test_mocks(
            instance_id=instance_id_a,
            parent_id=parent_id,
            stale_reports=[stale_report_a]  # Only child A's report
        )

        with patch("daemon.manager.Session", session_context):
            result = await instance_manager.resume_processing_job(
                instance_id_a, message="resume", silent=False
            )

        # Verify only 1 report was deleted (child A's)
        assert mock_session.delete.call_count == 1, \
            f"Expected only 1 stale report to be deleted (child A's), got {mock_session.delete.call_count}"

        # Verify session.commit() was called after deleting stale reports
        assert mock_session.commit.called, \
            "Expected session.commit() to be called after deleting stale reports"

        # Verify the deleted report was child A's
        mock_session.delete.assert_called_with(stale_report_a)

    @pytest.mark.asyncio
    async def test_no_stale_reports_resume_still_works(self):
        """Test 4: No stale reports — resume still works.

        Scenario:
        1. Child instance has no stale reports in parent's queue
        2. resume_processing_job() is called
        3. Fresh report should be created successfully
        4. No errors should occur
        """
        instance_id = "child-no-stale"
        parent_id = "parent-no-stale"

        # Setup manager with no stale reports
        instance_manager, mock_manager, mock_session, session_context = self._create_stale_test_mocks(
            instance_id=instance_id,
            parent_id=parent_id,
            stale_reports=[]  # No stale reports
        )

        with patch("daemon.manager.Session", session_context):
            result = await instance_manager.resume_processing_job(
                instance_id, message="resume", silent=False
            )

        # Verify resume completed successfully
        assert result is not None
        assert result["instance_id"] == instance_id

        # Verify _process_message_with_tracking was called
        mock_manager._process_message_with_tracking.assert_called_once()

        # Verify _process_child_completion_and_notify_parent was called with fresh msg_id
        mock_manager._process_child_completion_and_notify_parent.assert_called_once()
        call_args = mock_manager._process_child_completion_and_notify_parent.call_args
        fresh_msg_id = call_args[0][1]
        try:
            uuid.UUID(fresh_msg_id)
        except ValueError:
            pytest.fail(f"Expected fresh message_id to be a UUID, got: {fresh_msg_id}")

    @pytest.mark.asyncio
    async def test_child_without_parent_no_stale_cleanup(self, instance_manager, mock_manager):
        """Edge case: Child without parent — stale cleanup skipped.

        When a child instance has no parent_id, the stale report cleanup
        should be skipped entirely (no database query).
        """
        instance_id = "orphan-child"

        # Setup: Child has NO parent (parent_id is None) - use regular MockInstanceMeta
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id, status=InstanceStatus.PAUSED.value)
        )

        mock_manager._process_child_completion_and_notify_parent = AsyncMock()

        # Note: No _engine means the cleanup is skipped in test mode
        # The code checks: if hasattr(self, '_engine') and self._engine
        # Since instance_manager doesn't have _engine, it logs debug and skips

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )

        # Verify resume completed successfully
        assert result is not None
        assert result["instance_id"] == instance_id

        # Verify _process_message_with_tracking was called
        mock_manager._process_message_with_tracking.assert_called_once()
