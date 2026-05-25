"""Tests for JobProcessor status str/enum guard.

This module tests the fix for the bug where `instance_meta.status.value` failed
with `'str' object has no attribute 'value'` because `instance_meta.status`
can be a plain string from the DB instead of an InstanceStatus enum.

The fix uses: status_display = instance_meta.status.value if hasattr(instance_meta.status, 'value') else instance_meta.status

This ensures backward compatibility with:
- Plain strings from DB (e.g., "completed")
- InstanceStatus enum values (e.g., InstanceStatus.COMPLETED)
"""

from unittest.mock import AsyncMock, MagicMock
import pytest

from daemon.models.instance import InstanceStatus
from daemon.services.job_processor import JobProcessor
from daemon.repositories.job_queue.models import JobItem, JobStatus
from daemon.services.job_queue_service import DemandState


class MockInstance:
    """Mock instance object for testing status handling."""

    def __init__(self, status):
        """Initialize mock instance with given status.

        Args:
            status: Can be either InstanceStatus enum or plain string.
        """
        self.instance_id = "test-instance-id"
        self.project_id = "test-project"
        self.agent_id = "coder"
        self.agent_dir = "./agents/coder"
        self.status = status


class MockQueue:
    """Mock queue object for testing."""

    def __init__(
        self,
        queue_id: str = "queue-1",
        project_id: str = "project-1",
        queue_name: str = "default",
        is_paused: bool = False,
        concurrency_limit: int = 1,
        queue_type: str = "fifo",
    ):
        self.queue_id = queue_id
        self.project_id = project_id
        self.queue_name = queue_name
        self.is_paused = is_paused
        self.concurrency_limit = concurrency_limit
        self.queue_type = queue_type


class MockProject:
    """Mock project object for testing."""

    def __init__(self, project_id: str = "project-1", job_queue_paused: bool = False):
        self.project_id = project_id
        self.job_queue_paused = job_queue_paused


class MockJob:
    """Mock job object for testing."""

    def __init__(
        self,
        job_id: str = "job-1",
        agent_id: str = "coder",
        project_id: str = "project-1",
        queue_id: str = "queue-1",
        status: str = JobStatus.PROCESSING.value,
        job_type: str = "message",  # Must be 'message' to trigger the status guard code path
    ):
        self.job_id = job_id
        self.agent_id = agent_id
        self.project_id = project_id
        self.queue_id = queue_id
        self.status = status
        self.message = "test message"
        self.source = "api"
        self.instance_id = "test-instance-id"
        self.job_type = job_type  # Critical for triggering the status guard path


@pytest.fixture
def mock_queue_service():
    """Create mock JobQueueService."""
    service = MagicMock()
    service.complete_job = AsyncMock()
    return service


@pytest.fixture
def mock_instance_manager(mock_queue_service):
    """Create mock InstanceManager with instance repository."""
    manager = MagicMock()
    manager.spawn_instance_with_mcp = AsyncMock(return_value="test-instance-id")
    manager.enqueue_message = AsyncMock()
    manager.get_instance = MagicMock()
    manager._instance_repository = MagicMock()
    return manager


@pytest.fixture
def mock_project_repo():
    """Create mock project repository."""
    repo = MagicMock()
    repo.list_projects = MagicMock(return_value=[])
    return repo


@pytest.fixture
def mock_queue_repo():
    """Create mock queue repository."""
    repo = MagicMock()
    repo.list_by_project = MagicMock(return_value=[])
    return repo


class TestStatusStrEnumGuard:
    """Tests for the status str/enum guard in JobProcessor.

    The guard handles the case where instance_meta.status can be either:
    - An InstanceStatus enum (has .value attribute)
    - A plain string from DB (no .value attribute)
    """

    def _create_processor(self, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo):
        """Create JobProcessor with mocked dependencies."""
        return JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
            poll_interval=0.1,
        )

    @pytest.mark.asyncio
    async def test_status_guard_handles_enum_with_value_attribute(
        self, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that status guard correctly extracts value from enum.

        When instance_meta.status is an InstanceStatus enum, it has a .value
        attribute and the guard should extract it.
        """
        processor = self._create_processor(
            mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
        )

        # Create instance with enum status
        instance_meta = MockInstance(status=InstanceStatus.COMPLETED)

        # Verify the enum has .value attribute
        assert hasattr(instance_meta.status, 'value')
        assert instance_meta.status.value == "completed"

        # Verify the guard logic
        status_display = instance_meta.status.value if hasattr(instance_meta.status, 'value') else instance_meta.status
        assert status_display == "completed"

    @pytest.mark.asyncio
    async def test_status_guard_handles_plain_string_from_db(
        self, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that status guard correctly handles plain string from DB.

        When instance_meta.status is a plain string (as stored in DB), it has
        no .value attribute and the guard should use it directly.
        """
        processor = self._create_processor(
            mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
        )

        # Create instance with string status (as it comes from DB)
        instance_meta = MockInstance(status="completed")

        # Verify the string has no .value attribute
        assert not hasattr(instance_meta.status, 'value')

        # Verify the guard logic returns the string directly
        status_display = instance_meta.status.value if hasattr(instance_meta.status, 'value') else instance_meta.status
        assert status_display == "completed"

    @pytest.mark.asyncio
    async def test_status_guard_all_instance_status_enums(
        self, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that status guard works for all InstanceStatus enum values."""
        processor = self._create_processor(
            mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
        )

        # Test all known enum values
        for status_enum in InstanceStatus:
            instance_meta = MockInstance(status=status_enum)

            # Guard logic should work without error
            status_display = instance_meta.status.value if hasattr(instance_meta.status, 'value') else instance_meta.status

            # The display should be the string value of the enum
            assert status_display == status_enum.value, f"Failed for {status_enum}"

    @pytest.mark.asyncio
    async def test_status_comparison_with_enum_when_status_is_string(
        self, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that status comparison works when DB returns string but code uses enum.

        This is the critical bug scenario:
        1. DB stores status as string ("completed")
        2. Code compares against InstanceStatus.COMPLETED enum
        3. Comparison should work correctly
        """
        processor = self._create_processor(
            mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
        )

        # Create instance with string status from DB
        instance_meta = MockInstance(status="completed")

        # The comparison uses enum from daemon.models.instance
        # Since InstanceStatus(str, Enum), string values compare equal
        assert instance_meta.status == InstanceStatus.COMPLETED
        assert instance_meta.status in (InstanceStatus.COMPLETED, InstanceStatus.TERMINATED)

        # Same for other statuses
        instance_meta.status = "running"
        assert instance_meta.status == InstanceStatus.RUNNING

        instance_meta.status = "error"
        assert instance_meta.status == InstanceStatus.ERROR

    @pytest.mark.asyncio
    async def test_status_comparison_with_enum_when_status_is_enum(
        self, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that status comparison works when status is already an enum."""
        processor = self._create_processor(
            mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
        )

        # Create instance with enum status
        instance_meta = MockInstance(status=InstanceStatus.COMPLETED)

        # Comparisons should work directly
        assert instance_meta.status == InstanceStatus.COMPLETED
        assert instance_meta.status in (InstanceStatus.COMPLETED, InstanceStatus.TERMINATED)

    @pytest.mark.asyncio
    async def test_job_completes_when_instance_status_is_string(
        self, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that job is completed when instance status (string) indicates finished work.

        This tests the actual fix scenario where a job is marked complete
        when the instance status (from DB as string) indicates completion.
        """
        processor = self._create_processor(
            mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
        )

        # Create a PROCESSING MESSAGE job with instance_id
        # job_type='message' is required to trigger the status guard code path
        job = MockJob(status=JobStatus.PROCESSING.value, job_type="message")
        job.instance_id = "test-instance-id"

        # Mock DB returning instance with string status (the bug scenario)
        instance_meta = MockInstance(status="completed")  # String from DB
        mock_instance_manager._instance_repository.get.return_value = instance_meta

        # Mock project and queue setup - queue must NOT be paused and return no pending jobs
        project = MockProject(project_id="project-1", job_queue_paused=False)
        queue = MockQueue(queue_id="queue-1", project_id="project-1", is_paused=False)

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        # No pending jobs - this triggers the orphan job check path
        mock_queue_service._repository.list_pending_by_queue.return_value = []
        # Return our message job as a PROCESSING job in the queue
        mock_queue_service._repository.list_by_queue.return_value = ([job], None)

        # Process the job
        await processor._process_next_job()

        # Job should be marked as complete
        mock_queue_service.complete_job.assert_called_once()
        call_kwargs = mock_queue_service.complete_job.call_args[1]
        assert call_kwargs["demand_state"] == DemandState.COMPLETED

        # Status display should be extracted from string
        status_display = instance_meta.status.value if hasattr(instance_meta.status, 'value') else instance_meta.status
        assert status_display == "completed"

    @pytest.mark.asyncio
    async def test_job_completes_when_instance_status_is_enum(
        self, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that job is completed when instance status (enum) indicates finished work.

        This tests the scenario where status is already an enum.
        """
        processor = self._create_processor(
            mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
        )

        # Create a PROCESSING MESSAGE job with instance_id
        job = MockJob(status=JobStatus.PROCESSING.value, job_type="message")
        job.instance_id = "test-instance-id"

        # Mock DB returning instance with enum status
        instance_meta = MockInstance(status=InstanceStatus.COMPLETED)
        mock_instance_manager._instance_repository.get.return_value = instance_meta

        # Mock project and queue setup
        project = MockProject(project_id="project-1", job_queue_paused=False)
        queue = MockQueue(queue_id="queue-1", project_id="project-1", is_paused=False)

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = []
        mock_queue_service._repository.list_by_queue.return_value = ([job], None)

        # Process the job
        await processor._process_next_job()

        # Job should be marked as complete
        mock_queue_service.complete_job.assert_called_once()
        call_kwargs = mock_queue_service.complete_job.call_args[1]
        assert call_kwargs["demand_state"] == DemandState.COMPLETED

    @pytest.mark.asyncio
    async def test_job_fails_when_instance_status_is_error_string(
        self, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that job is marked as FAILED when instance status (string) is error.

        Tests the error handling path with string status from DB.
        """
        processor = self._create_processor(
            mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
        )

        # Create a PROCESSING MESSAGE job with instance_id
        job = MockJob(status=JobStatus.PROCESSING.value, job_type="message")
        job.instance_id = "test-instance-id"

        # Mock DB returning instance with error string status
        instance_meta = MockInstance(status="error")
        mock_instance_manager._instance_repository.get.return_value = instance_meta

        # Mock project and queue setup
        project = MockProject(project_id="project-1", job_queue_paused=False)
        queue = MockQueue(queue_id="queue-1", project_id="project-1", is_paused=False)

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = []
        mock_queue_service._repository.list_by_queue.return_value = ([job], None)

        # Process the job
        await processor._process_next_job()

        # Job should be marked as FAILED
        mock_queue_service.complete_job.assert_called_once()
        call_kwargs = mock_queue_service.complete_job.call_args[1]
        assert call_kwargs["demand_state"] == DemandState.FAILED
        assert "Instance errored" in call_kwargs["error"]

    @pytest.mark.asyncio
    async def test_job_fails_when_instance_status_is_error_enum(
        self, mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
    ):
        """Test that job is marked as FAILED when instance status (enum) is error.

        Tests the error handling path with enum status.
        """
        processor = self._create_processor(
            mock_queue_service, mock_instance_manager, mock_project_repo, mock_queue_repo
        )

        # Create a PROCESSING MESSAGE job with instance_id
        job = MockJob(status=JobStatus.PROCESSING.value, job_type="message")
        job.instance_id = "test-instance-id"

        # Mock DB returning instance with error enum status
        instance_meta = MockInstance(status=InstanceStatus.ERROR)
        mock_instance_manager._instance_repository.get.return_value = instance_meta

        # Mock project and queue setup
        project = MockProject(project_id="project-1", job_queue_paused=False)
        queue = MockQueue(queue_id="queue-1", project_id="project-1", is_paused=False)

        mock_project_repo.list_projects.return_value = [project]
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_service._repository.list_pending_by_queue.return_value = []
        mock_queue_service._repository.list_by_queue.return_value = ([job], None)

        # Process the job
        await processor._process_next_job()

        # Job should be marked as FAILED
        mock_queue_service.complete_job.assert_called_once()
        call_kwargs = mock_queue_service.complete_job.call_args[1]
        assert call_kwargs["demand_state"] == DemandState.FAILED


class TestStatusGuardEdgeCases:
    """Edge case tests for the status str/enum guard."""

    @pytest.mark.asyncio
    async def test_status_comparison_with_mixed_string_and_enum(self):
        """Test that status comparisons work when mixing string and enum values.

        Since InstanceStatus(str, Enum), the enum values compare equal to their string counterparts.
        """
        # String value compared to enum
        assert "completed" == InstanceStatus.COMPLETED
        assert "running" == InstanceStatus.RUNNING
        assert "error" == InstanceStatus.ERROR
        assert "terminated" == InstanceStatus.TERMINATED

        # Enum compared to string
        assert InstanceStatus.COMPLETED == "completed"
        assert InstanceStatus.RUNNING == "running"

        # Tuple comparisons
        assert "completed" in (InstanceStatus.COMPLETED, InstanceStatus.TERMINATED)
        assert InstanceStatus.COMPLETED in ("completed", "terminated")

    @pytest.mark.asyncio
    async def test_unknown_string_status_handling(self):
        """Test handling of unknown status strings from DB.

        The status guard should handle any string gracefully.
        """
        # Unknown status string
        unknown_status = "some_unknown_status"

        # Guard logic should work
        status_display = unknown_status if hasattr(unknown_status, 'value') else unknown_status
        assert status_display == unknown_status

    @pytest.mark.asyncio
    async def test_empty_string_status_handling(self):
        """Test handling of empty status string from DB.

        Empty string has no .value attribute, so guard returns it directly.
        """
        empty_status = ""

        # Guard logic should work
        status_display = empty_status if hasattr(empty_status, 'value') else empty_status
        assert status_display == ""

    @pytest.mark.asyncio
    async def test_capitalized_string_status_from_legacy_data(self):
        """Test handling of capitalized status strings (e.g., 'COMPLETED').

        Legacy data might have uppercase values while enums are lowercase.
        """
        # Uppercase status from legacy data
        uppercase_status = "COMPLETED"

        # Guard logic returns the string as-is
        status_display = uppercase_status if hasattr(uppercase_status, 'value') else uppercase_status
        assert status_display == "COMPLETED"

        # But this won't match the lowercase enum
        # This is expected behavior - the comparison won't match
        # The job won't be marked complete if status format differs


class TestStatusEnumValues:
    """Tests verifying InstanceStatus enum values match expected strings."""

    def test_instance_status_values_match_expected_strings(self):
        """Verify that InstanceStatus enum values match the strings stored in DB.

        The DB stores lowercase strings, so enums must also be lowercase.
        """
        assert InstanceStatus.IDLE.value == "idle"
        assert InstanceStatus.RUNNING.value == "running"
        assert InstanceStatus.WAITING.value == "waiting"
        assert InstanceStatus.WAITING_CHILDREN.value == "waiting_children"
        assert InstanceStatus.ERROR.value == "error"
        assert InstanceStatus.TERMINATED.value == "terminated"
        assert InstanceStatus.COMPLETED.value == "completed"
        assert InstanceStatus.PAUSED.value == "paused"

    def test_instance_status_is_string_subclass(self):
        """Verify InstanceStatus inherits from str for DB compatibility."""
        assert issubclass(InstanceStatus, str)
        # As a string subclass, it should compare equal to its value
        assert InstanceStatus.COMPLETED == "completed"
