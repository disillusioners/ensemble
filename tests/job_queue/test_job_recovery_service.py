"""Comprehensive tests for JobRecoveryService."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import JobItem
from daemon.services.job_recovery_service import JobRecoveryService


def create_mock_job(
    job_id: str = "job-123",
    instance_id: str | None = "inst-1",
    project_id: str = "proj-1",
) -> MagicMock:
    """Create a mock JobItem with the specified attributes."""
    mock_job = MagicMock(spec=JobItem)
    mock_job.job_id = job_id
    mock_job.instance_id = instance_id
    mock_job.project_id = project_id
    return mock_job


def create_mock_instance(
    instance_id: str = "inst-1",
    status: str = "idle",
) -> MagicMock:
    """Create a mock Instance with the specified attributes."""
    mock_instance = MagicMock(spec=Instance)
    mock_instance.instance_id = instance_id
    mock_instance.status = status
    return mock_instance


class TestJobRecoveryStartup:
    """Tests for recover_on_startup method."""

    @pytest.fixture
    def mock_repositories(self):
        """Create mock repositories for each test."""
        mock_job_repo = MagicMock()
        mock_lock_repo = MagicMock()
        mock_instance_repo = MagicMock()
        return mock_job_repo, mock_lock_repo, mock_instance_repo

    @pytest.fixture
    def service(self, mock_repositories):
        """Create JobRecoveryService with mock repositories."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories
        return JobRecoveryService(
            job_repository=mock_job_repo,
            lock_repository=mock_lock_repo,
            instance_repository=mock_instance_repo,
        )

    @pytest.mark.asyncio
    async def test_orphaned_job_with_no_instance(self, mock_repositories, service):
        """Job with no instance_id should be marked FAILED."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job(instance_id=None)
        mock_job_repo.find_processing_jobs.return_value = [mock_job]

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 1, "alive": 0, "total": 1}, "Expected 1 recovered job"
        mock_lock_repo.release_by_instance.assert_not_called()
        mock_job_repo.atomic_transition.assert_called_once()
        call_args = mock_job_repo.atomic_transition.call_args
        assert call_args[0][0] == "job-123", "Expected job_id to match"
        assert call_args.kwargs["from_status"] == "processing", "Expected from_status to be processing"
        assert call_args.kwargs["to_status"] == "failed", "Expected to_status to be failed"

    @pytest.mark.asyncio
    async def test_orphaned_job_with_missing_instance(self, mock_repositories, service):
        """Job whose instance doesn't exist in DB should be marked FAILED."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job()
        mock_job_repo.find_processing_jobs.return_value = [mock_job]
        mock_instance_repo.get.return_value = None

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 1, "alive": 0, "total": 1}, "Expected 1 recovered job"
        mock_lock_repo.release_by_instance.assert_called_once_with("inst-1"), "Lock should be released"
        mock_job_repo.atomic_transition.assert_called_once()
        call_args = mock_job_repo.atomic_transition.call_args
        assert call_args[1]["error_message"] == "Recovered: instance no longer exists"

    @pytest.mark.asyncio
    async def test_orphaned_job_with_completed_instance(self, mock_repositories, service):
        """Job whose instance is completed should be marked FAILED."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job()
        mock_instance = create_mock_instance(status="completed")
        mock_job_repo.find_processing_jobs.return_value = [mock_job]
        mock_instance_repo.get.return_value = mock_instance

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 1, "alive": 0, "total": 1}, "Expected 1 recovered job"
        mock_lock_repo.release_by_instance.assert_called_once_with("inst-1"), "Lock should be released"
        mock_job_repo.atomic_transition.assert_called_once()
        call_args = mock_job_repo.atomic_transition.call_args
        assert call_args[1]["error_message"] == "Recovered: instance is completed"

    @pytest.mark.asyncio
    async def test_orphaned_job_with_error_instance(self, mock_repositories, service):
        """Job whose instance has error should be marked FAILED."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job()
        mock_instance = create_mock_instance(status="error")
        mock_job_repo.find_processing_jobs.return_value = [mock_job]
        mock_instance_repo.get.return_value = mock_instance

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 1, "alive": 0, "total": 1}, "Expected 1 recovered job"
        mock_lock_repo.release_by_instance.assert_called_once_with("inst-1"), "Lock should be released"
        mock_job_repo.atomic_transition.assert_called_once()
        call_args = mock_job_repo.atomic_transition.call_args
        assert call_args[1]["error_message"] == "Recovered: instance is error"

    @pytest.mark.asyncio
    async def test_orphaned_job_with_terminated_instance(self, mock_repositories, service):
        """Job whose instance is terminated should be marked FAILED."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job()
        mock_instance = create_mock_instance(status="terminated")
        mock_job_repo.find_processing_jobs.return_value = [mock_job]
        mock_instance_repo.get.return_value = mock_instance

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 1, "alive": 0, "total": 1}, "Expected 1 recovered job"
        mock_lock_repo.release_by_instance.assert_called_once_with("inst-1"), "Lock should be released"
        mock_job_repo.atomic_transition.assert_called_once()
        call_args = mock_job_repo.atomic_transition.call_args
        assert call_args[1]["error_message"] == "Recovered: instance is terminated"

    @pytest.mark.asyncio
    async def test_orphaned_job_with_failed_instance(self, mock_repositories, service):
        """Job whose instance is failed should be marked FAILED."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job()
        mock_instance = create_mock_instance(status="failed")
        mock_job_repo.find_processing_jobs.return_value = [mock_job]
        mock_instance_repo.get.return_value = mock_instance

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 1, "alive": 0, "total": 1}, "Expected 1 recovered job"
        mock_lock_repo.release_by_instance.assert_called_once_with("inst-1"), "Lock should be released"
        mock_job_repo.atomic_transition.assert_called_once()
        call_args = mock_job_repo.atomic_transition.call_args
        assert call_args[1]["error_message"] == "Recovered: instance is failed"

    @pytest.mark.asyncio
    async def test_alive_job_with_idle_instance(self, mock_repositories, service):
        """Job whose instance is idle should remain PROCESSING."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job()
        mock_instance = create_mock_instance(status="idle")
        mock_job_repo.find_processing_jobs.return_value = [mock_job]
        mock_instance_repo.get.return_value = mock_instance

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 0, "alive": 1, "total": 1}, "Expected job to remain alive"
        mock_lock_repo.release_by_instance.assert_not_called(), "Lock should not be released"
        mock_job_repo.atomic_transition.assert_not_called(), "Job should not be transitioned"

    @pytest.mark.asyncio
    async def test_alive_job_with_running_instance(self, mock_repositories, service):
        """Job whose instance is running should remain PROCESSING."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job()
        mock_instance = create_mock_instance(status="running")
        mock_job_repo.find_processing_jobs.return_value = [mock_job]
        mock_instance_repo.get.return_value = mock_instance

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 0, "alive": 1, "total": 1}, "Expected job to remain alive"
        mock_lock_repo.release_by_instance.assert_not_called(), "Lock should not be released"
        mock_job_repo.atomic_transition.assert_not_called(), "Job should not be transitioned"

    @pytest.mark.asyncio
    async def test_alive_job_with_paused_instance(self, mock_repositories, service):
        """Job whose instance is paused should remain PROCESSING."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job()
        mock_instance = create_mock_instance(status="paused")
        mock_job_repo.find_processing_jobs.return_value = [mock_job]
        mock_instance_repo.get.return_value = mock_instance

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 0, "alive": 1, "total": 1}, "Expected job to remain alive"
        mock_lock_repo.release_by_instance.assert_not_called(), "Lock should not be released"
        mock_job_repo.atomic_transition.assert_not_called(), "Job should not be transitioned"

    @pytest.mark.asyncio
    async def test_alive_job_with_queued_instance(self, mock_repositories, service):
        """Job whose instance is queued should remain PROCESSING."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job()
        mock_instance = create_mock_instance(status="queued")
        mock_job_repo.find_processing_jobs.return_value = [mock_job]
        mock_instance_repo.get.return_value = mock_instance

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 0, "alive": 1, "total": 1}, "Expected job to remain alive"
        mock_lock_repo.release_by_instance.assert_not_called(), "Lock should not be released"
        mock_job_repo.atomic_transition.assert_not_called(), "Job should not be transitioned"

    @pytest.mark.asyncio
    async def test_alive_job_with_waiting_children_instance(self, mock_repositories, service):
        """Job whose instance is waiting_children should remain PROCESSING."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job()
        mock_instance = create_mock_instance(status="waiting_children")
        mock_job_repo.find_processing_jobs.return_value = [mock_job]
        mock_instance_repo.get.return_value = mock_instance

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 0, "alive": 1, "total": 1}, "Expected job to remain alive"
        mock_lock_repo.release_by_instance.assert_not_called(), "Lock should not be released"
        mock_job_repo.atomic_transition.assert_not_called(), "Job should not be transitioned"

    @pytest.mark.asyncio
    async def test_lock_released_for_orphaned_job(self, mock_repositories, service):
        """Lock should be released when orphaned job is recovered."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job(instance_id="my-instance-123")
        mock_instance_repo.get.return_value = None
        mock_job_repo.find_processing_jobs.return_value = [mock_job]

        await service.recover_on_startup()

        mock_lock_repo.release_by_instance.assert_called_once_with("my-instance-123")

    @pytest.mark.asyncio
    async def test_no_processing_jobs(self, mock_repositories, service):
        """When no PROCESSING jobs exist, recovery is a no-op."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job_repo.find_processing_jobs.return_value = []

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 0, "alive": 0, "total": 0}, "Expected empty stats"
        mock_instance_repo.get.assert_not_called(), "Instance repo should not be called"
        mock_lock_repo.release_by_instance.assert_not_called(), "Lock should not be released"
        mock_job_repo.atomic_transition.assert_not_called(), "No jobs should be transitioned"

    @pytest.mark.asyncio
    async def test_recovery_stats(self, mock_repositories, service):
        """recover_on_startup() should return accurate stats."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job1 = create_mock_job(job_id="job-1", instance_id=None)
        mock_job2 = create_mock_job(job_id="job-2", instance_id="inst-2")
        mock_job3 = create_mock_job(job_id="job-3", instance_id="inst-3")
        mock_job4 = create_mock_job(job_id="job-4", instance_id="inst-4")

        mock_instance2 = create_mock_instance(status="completed")
        mock_instance4 = create_mock_instance(status="running")

        mock_job_repo.find_processing_jobs.return_value = [
            mock_job1,
            mock_job2,
            mock_job3,
            mock_job4,
        ]

        def get_instance(instance_id):
            if instance_id == "inst-2":
                return mock_instance2
            if instance_id == "inst-4":
                return mock_instance4
            return None

        mock_instance_repo.get.side_effect = get_instance

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 3, "alive": 1, "total": 4}, "Expected mixed recovery stats"
        assert mock_job_repo.atomic_transition.call_count == 3, "Expected 3 atomic transitions"

    @pytest.mark.asyncio
    async def test_atomic_transition_error_handled(self, mock_repositories, service):
        """If atomic_transition fails, error should be logged but not crash."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job()
        mock_job_repo.find_processing_jobs.return_value = [mock_job]
        mock_instance_repo.get.return_value = None
        mock_job_repo.atomic_transition.side_effect = Exception("DB error")

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 1, "alive": 0, "total": 1}, "Stats should still be updated"


class TestJobRecoveryServiceHelpers:
    """Tests for helper methods in JobRecoveryService."""

    @pytest.fixture
    def service(self):
        """Create JobRecoveryService with mock repositories."""
        mock_job_repo = MagicMock()
        mock_lock_repo = MagicMock()
        mock_instance_repo = MagicMock()
        return JobRecoveryService(
            job_repository=mock_job_repo,
            lock_repository=mock_lock_repo,
            instance_repository=mock_instance_repo,
        )

    def test_is_instance_alive_returns_true_for_alive_statuses(self, service):
        """Test all alive statuses return True."""
        alive_statuses = [
            InstanceStatus.IDLE.value,
            InstanceStatus.RUNNING.value,
            InstanceStatus.PAUSED.value,
            InstanceStatus.QUEUED.value,
            InstanceStatus.WAITING_CHILDREN.value,
        ]

        for status in alive_statuses:
            assert service._is_instance_alive(status) is True, f"Expected {status} to be alive"

    def test_is_instance_alive_returns_false_for_terminal_statuses(self, service):
        """Test terminal statuses return False."""
        terminal_statuses = [
            InstanceStatus.COMPLETED.value,
            InstanceStatus.ERROR.value,
            InstanceStatus.TERMINATED.value,
            InstanceStatus.FAILED.value,
        ]

        for status in terminal_statuses:
            assert service._is_instance_alive(status) is False, f"Expected {status} to not be alive"

    def test_is_instance_alive_returns_false_for_none(self, service):
        """None returns False."""
        assert service._is_instance_alive(None) is False, "Expected None to return False"

    def test_is_instance_terminal_returns_true_for_terminal_statuses(self, service):
        """Test all terminal statuses return True."""
        terminal_statuses = [
            InstanceStatus.COMPLETED.value,
            InstanceStatus.ERROR.value,
            InstanceStatus.TERMINATED.value,
            InstanceStatus.FAILED.value,
        ]

        for status in terminal_statuses:
            assert service._is_instance_terminal(status) is True, f"Expected {status} to be terminal"

    def test_is_instance_terminal_returns_false_for_alive_statuses(self, service):
        """Test alive statuses return False."""
        alive_statuses = [
            InstanceStatus.IDLE.value,
            InstanceStatus.RUNNING.value,
            InstanceStatus.PAUSED.value,
            InstanceStatus.QUEUED.value,
            InstanceStatus.WAITING_CHILDREN.value,
        ]

        for status in alive_statuses:
            assert service._is_instance_terminal(status) is False, f"Expected {status} to not be terminal"

    def test_is_instance_terminal_returns_false_for_none(self, service):
        """None returns False."""
        assert service._is_instance_terminal(None) is False, "Expected None to return False"
