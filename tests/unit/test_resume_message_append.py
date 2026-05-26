"""Tests for resume_processing_job message ID uniqueness.

These tests verify that resume_processing_job always generates a fresh UUID
for the message_id parameter, preventing LangGraph's add_messages reducer
from replacing existing messages instead of appending them.

The bug (commit ab23b16): resume_processing_job was reusing the paused
job's message_id, causing LangGraph to replace the original message.
The fix: always generate message_id=str(uuid.uuid4()).
"""

import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


class MockJob:
    """Mock job object for testing resume_processing_job."""

    def __init__(
        self,
        job_id: str = "test-job-123",
        instance_id: str = "test-instance",
        message_id: str | None = "original-msg-123",
    ):
        self.job_id = job_id
        self.instance_id = instance_id
        self.status = "processing"
        self.message = "test message"
        self.job_type = "message"
        self.project_id = "project-1"
        self.queue_id = "queue-1"
        self.job_metadata = {"message_id": message_id} if message_id is not None else {}


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
def mock_manager(mock_job_queue_service, mock_queue_repository):
    """Create mock manager with all required dependencies."""
    manager = MagicMock()
    manager._job_queue_service = mock_job_queue_service
    manager._queue_repository = mock_queue_repository
    manager._process_message_with_tracking = AsyncMock(return_value=MockMessageResult())
    return manager


@pytest.fixture
def instance_manager(mock_manager):
    """Create InstanceManager with mocked dependencies."""
    from daemon.manager import InstanceManager
    from daemon.config import Config

    config = MagicMock(spec=Config)
    manager = InstanceManager.__new__(InstanceManager)
    manager._job_queue_service = mock_manager._job_queue_service
    manager._queue_repository = mock_manager._queue_repository
    manager._process_message_with_tracking = mock_manager._process_message_with_tracking
    return manager


class TestResumeMessageIdUniqueness:
    """Test suite for resume_processing_job message ID uniqueness."""

    @pytest.mark.asyncio
    async def test_resume_generates_unique_message_id(self, instance_manager, mock_manager):
        """Test that resume_processing_job generates a different message_id than the paused job.

        This is the core test for the bug fix: resume should always generate
        a fresh UUID, never reuse the old one.
        """
        original_message_id = "original-msg-123"
        old_job = MockJob(message_id=original_message_id)

        # Mock find_processing_message_jobs_by_instance to return our old job
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[old_job]
        )

        # Call resume_processing_job
        result = await instance_manager.resume_processing_job("test-instance", message="resume")

        # Verify _process_message_with_tracking was called
        mock_manager._process_message_with_tracking.assert_called_once()
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs

        # CRITICAL: message_id should be different from original
        assert call_kwargs["message_id"] != original_message_id, \
            "resume_processing_job should generate a fresh UUID, not reuse the old message_id"

        # Verify it's a valid UUID format
        try:
            uuid.UUID(call_kwargs["message_id"])
        except ValueError:
            pytest.fail(f"message_id should be a valid UUID, got: {call_kwargs['message_id']}")

    @pytest.mark.asyncio
    async def test_resume_always_generates_fresh_uuid(self, instance_manager, mock_manager):
        """Test that multiple resume calls each generate different message_ids.

        Both calls should generate new UUIDs (not reusing each other either).
        """
        old_job = MockJob()

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[old_job]
        )

        # First resume
        result1 = await instance_manager.resume_processing_job("test-instance", message="resume")
        call_kwargs1 = mock_manager._process_message_with_tracking.call_args.kwargs
        message_id1 = call_kwargs1["message_id"]

        # Reset mock for second call
        mock_manager._process_message_with_tracking.reset_mock()

        # Second resume (with new job mock)
        old_job2 = MockJob(job_id="test-job-456")
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[old_job2]
        )

        result2 = await instance_manager.resume_processing_job("test-instance", message="resume")
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
    async def test_resume_message_content_preserved(self, instance_manager, mock_manager):
        """Test that the message text is correctly passed to _process_message_with_tracking.

        Verifies both default "resume" and custom messages are preserved.
        """
        old_job = MockJob()

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[old_job]
        )

        # Test with custom message
        custom_message = "hello world custom resume message"
        result = await instance_manager.resume_processing_job(
            "test-instance", message=custom_message
        )

        mock_manager._process_message_with_tracking.assert_called_once()
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs

        # Verify message text is preserved
        assert call_kwargs["message"] == custom_message

        # Test with default message
        mock_manager._process_message_with_tracking.reset_mock()
        result = await instance_manager.resume_processing_job("test-instance")

        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs
        assert call_kwargs["message"] == "resume"

    @pytest.mark.asyncio
    async def test_resume_silent_mode_no_message_id_reuse(self, instance_manager, mock_manager):
        """Test that silent mode (silent=True) still generates a fresh message_id.

        Even when is_retry=True, the message_id should still be fresh.
        """
        original_message_id = "original-msg-123"
        old_job = MockJob(message_id=original_message_id)

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[old_job]
        )

        # Call with silent=True
        result = await instance_manager.resume_processing_job(
            "test-instance", message="resume", silent=True
        )

        mock_manager._process_message_with_tracking.assert_called_once()
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs

        # Verify message_id is still fresh (not reused)
        assert call_kwargs["message_id"] != original_message_id

        # Verify is_retry=True for silent mode
        assert call_kwargs["is_retry"] is True

        # Verify it's a valid UUID
        try:
            uuid.UUID(call_kwargs["message_id"])
        except ValueError:
            pytest.fail(f"message_id should be a valid UUID, got: {call_kwargs['message_id']}")

    @pytest.mark.asyncio
    async def test_resume_with_no_existing_message_id_in_metadata(self, instance_manager, mock_manager):
        """Test resume when old job has no message_id in metadata (None/empty).

        Even when message_id was already None, resume should still generate a fresh UUID.
        This tests the edge case where job_metadata doesn't contain message_id.
        """
        old_job = MockJob(message_id=None)

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[old_job]
        )

        result = await instance_manager.resume_processing_job("test-instance", message="resume")

        mock_manager._process_message_with_tracking.assert_called_once()
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs

        # Should still generate a fresh UUID
        assert call_kwargs["message_id"] is not None
        try:
            uuid.UUID(call_kwargs["message_id"])
        except ValueError:
            pytest.fail(f"message_id should be a valid UUID, got: {call_kwargs['message_id']}")

    @pytest.mark.asyncio
    async def test_resume_does_not_reuse_old_message_id(self, instance_manager, mock_manager):
        """Test the core regression: resume should NEVER use the old job's message_id.

        This is the definitive test for the bug fix. The old job had message_id="original-msg-123",
        and we verify that _process_message_with_tracking is NOT called with that ID.
        """
        old_message_id = "original-msg-123"
        old_job = MockJob(message_id=old_message_id)

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[old_job]
        )

        # Capture all calls to _process_message_with_tracking
        captured_calls = []

        async def capture_call(*args, **kwargs):
            captured_calls.append(kwargs)
            return MockMessageResult()

        mock_manager._process_message_with_tracking.side_effect = capture_call

        result = await instance_manager.resume_processing_job("test-instance", message="resume")

        # Verify the call was made
        assert len(captured_calls) == 1, "Expected exactly one call to _process_message_with_tracking"
        call_kwargs = captured_calls[0]

        # CRITICAL: The message_id should NOT be the original one
        assert call_kwargs["message_id"] != old_message_id, \
            f"resume_processing_job MUST NOT reuse the old message_id '{old_message_id}'. " \
            "This would cause LangGraph to replace the original message instead of appending."

        # Verify it's a valid UUID
        try:
            uuid.UUID(call_kwargs["message_id"])
        except ValueError:
            pytest.fail(f"message_id should be a valid UUID, got: {call_kwargs['message_id']}")

        # Additional check: it should be obviously different (not just a similar UUID)
        # The old ID was "original-msg-123", new ID should look like a UUID
        assert "-" in call_kwargs["message_id"], "New message_id should be a UUID format"


class TestResumeMessageIdIntegration:
    """Integration-style tests verifying message_id flows correctly through resume."""

    @pytest.mark.asyncio
    async def test_resume_returns_old_message_id_in_result(self, instance_manager, mock_manager):
        """Test that resume returns the OLD message_id in the result dict.

        The result dict contains the old message_id for completion purposes,
        but the _process_message_with_tracking call uses a fresh UUID.
        """
        original_message_id = "original-msg-123"
        old_job = MockJob(message_id=original_message_id)

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[old_job]
        )

        result = await instance_manager.resume_processing_job("test-instance", message="resume")

        # Result should contain the OLD message_id (for completion purposes)
        assert result["message_id"] == original_message_id
        assert result["job_id"] == old_job.job_id

        # But the call to _process_message_with_tracking should have a NEW message_id
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs
        assert call_kwargs["message_id"] != original_message_id

    @pytest.mark.asyncio
    async def test_resume_with_empty_metadata_still_works(self, instance_manager, mock_manager):
        """Test resume when job_metadata is empty dict (no message_id key at all)."""
        old_job = MockJob()
        old_job.job_metadata = {}  # Empty metadata, no message_id

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[old_job]
        )

        result = await instance_manager.resume_processing_job("test-instance", message="resume")

        # Should complete without error
        assert result is not None
        assert "message_id" in result
        assert result["message_id"] is None  # Old job had no message_id

        # Fresh UUID was still used
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs
        try:
            uuid.UUID(call_kwargs["message_id"])
        except ValueError:
            pytest.fail(f"message_id should be a valid UUID, got: {call_kwargs['message_id']}")
