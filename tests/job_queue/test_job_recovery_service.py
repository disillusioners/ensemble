"""Comprehensive tests for JobRecoveryService."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import JobItem
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.job_state_machine import InvalidTransitionError


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
    async def test_paused_instance_reconciles_processing_job_to_paused(self, mock_repositories, service):
        """C2 fix (Phase 6): PROCESSING job on a PAUSED instance is reconciled
        to PAUSED (not left as PROCESSING).

        This handles two scenarios:
          (a) pre-Phase-2 hack state where pause did not touch the job,
          (b) crash during the pause transition window (instance → PAUSED
              committed but job → PAUSED did not).

        The job must transition PROCESSING → PAUSED via atomic_transition
        so its status matches the instance. The (PROCESSING, PAUSED)
        "pause" entry in TRANSITIONS allows this.
        """
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job()
        mock_instance = create_mock_instance(status="paused")
        mock_job_repo.find_processing_jobs.return_value = [mock_job]
        mock_instance_repo.get.return_value = mock_instance

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 1, "alive": 0, "total": 1}, (
            "PAUSED + PROCESSING should reconcile (counted as recovered)"
        )
        mock_lock_repo.release_by_instance.assert_not_called(), (
            "PAUSED reconciliation should NOT release the lock (job stays "
            "associated with the instance — it will be released on "
            "terminate/complete, not on PAUSED reconcile)"
        )
        mock_job_repo.atomic_transition.assert_called_once()
        call_args = mock_job_repo.atomic_transition.call_args
        assert call_args[0][0] == "job-123", "Expected job_id to match"
        assert call_args.kwargs["from_status"] == "processing", (
            "Expected from_status to be processing"
        )
        assert call_args.kwargs["to_status"] == "paused", (
            "Expected to_status to be paused (PROCESSING → PAUSED reconcile)"
        )

    @pytest.mark.asyncio
    async def test_paused_instance_already_paused_job_is_skipped(
        self, mock_repositories, service
    ):
        """C2 fix (Phase 6): If the job is already PAUSED (not PROCESSING),
        it won't appear in find_processing_jobs() and is therefore not
        visible to recover_on_startup. This test documents that contract —
        the recovery service only handles PROCESSING jobs.
        """
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        # find_processing_jobs only returns PROCESSING jobs. A PAUSED
        # job on a PAUSED instance would not be returned.
        mock_job_repo.find_processing_jobs.return_value = []

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 0, "alive": 0, "total": 0}, (
            "No jobs to recover (PAUSED jobs are filtered upstream)"
        )
        mock_instance_repo.get.assert_not_called()
        mock_job_repo.atomic_transition.assert_not_called()

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
        """If atomic_transition fails with unexpected error, error should be logged but not crash."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job()
        mock_job_repo.find_processing_jobs.return_value = [mock_job]
        mock_instance_repo.get.return_value = None
        mock_job_repo.atomic_transition.side_effect = Exception("DB error")

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 0, "alive": 0, "total": 1}, "Stats should not be incremented on error"

    @pytest.mark.asyncio
    async def test_invalid_transition_error_skips_stats(self, mock_repositories, service):
        """If atomic_transition raises InvalidTransitionError, it's expected and stats are NOT incremented."""
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job()
        mock_job_repo.find_processing_jobs.return_value = [mock_job]
        mock_instance_repo.get.return_value = None
        mock_job_repo.atomic_transition.side_effect = InvalidTransitionError(
            job_id=mock_job.job_id,
            from_state="processing",
            to_state="failed",
        )

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 0, "alive": 0, "total": 1}, "Stats should not be incremented for InvalidTransitionError"
        # Lock should still be released even if transition is skipped
        mock_lock_repo.release_by_instance.assert_called_once_with("inst-1")


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


class TestFailOrphanedJobLockOrdering:
    """H8: Lock release ordering in _fail_orphaned_job.

    The status transition must run FIRST and the lock release must run
    SECOND (in a ``finally`` block). These tests pin down that ordering
    and the guarantee that the lock is always released.
    """

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
    async def test_transition_happens_before_lock_release(self, mock_repositories, service):
        """atomic_transition must be called BEFORE release_by_instance.

        This is the core H8 invariant. If the transition fails after the
        lock is released, the job is in PROCESSING with no lock, opening
        a double-claim window.
        """
        mock_job_repo, mock_lock_repo, _ = mock_repositories
        mock_job = create_mock_job(instance_id="inst-1")

        # Track the order of calls across both mocks.
        call_order: list[str] = []

        def track_transition(*args, **kwargs):
            call_order.append("atomic_transition")

        def track_release(*args, **kwargs):
            call_order.append("release_by_instance")

        mock_job_repo.atomic_transition.side_effect = track_transition
        mock_lock_repo.release_by_instance.side_effect = track_release

        result = await service._fail_orphaned_job(
            mock_job, "Recovered: instance no longer exists", {"recovered": 0}
        )

        assert result is True
        assert call_order == ["atomic_transition", "release_by_instance"], (
            f"Expected transition BEFORE lock release, got: {call_order}"
        )

    @pytest.mark.asyncio
    async def test_lock_released_on_invalid_transition_error(self, mock_repositories, service):
        """Lock must still be released when InvalidTransitionError is raised."""
        mock_job_repo, mock_lock_repo, _ = mock_repositories
        mock_job = create_mock_job(instance_id="inst-1")

        mock_job_repo.atomic_transition.side_effect = InvalidTransitionError(
            job_id=mock_job.job_id,
            from_state="processing",
            to_state="failed",
        )

        result = await service._fail_orphaned_job(
            mock_job, "Recovered: instance no longer exists", {"recovered": 0}
        )

        assert result is False, "Expected False on InvalidTransitionError"
        # Lock must be released in the finally block even though transition
        # was skipped (job is already terminal in this case).
        mock_lock_repo.release_by_instance.assert_called_once_with("inst-1")

    @pytest.mark.asyncio
    async def test_lock_released_on_unexpected_exception(self, mock_repositories, service):
        """Lock must still be released when an unexpected exception is raised.

        Previously a non-ValueError exception (e.g. DB error) skipped lock
        release entirely, leaking the lock. The finally block now guarantees
        release on ALL paths.
        """
        mock_job_repo, mock_lock_repo, _ = mock_repositories
        mock_job = create_mock_job(instance_id="inst-1")

        mock_job_repo.atomic_transition.side_effect = RuntimeError("DB connection lost")

        result = await service._fail_orphaned_job(
            mock_job, "Recovered: instance no longer exists", {"recovered": 0}
        )

        assert result is False, "Expected False on unexpected exception"
        mock_lock_repo.release_by_instance.assert_called_once_with("inst-1"), (
            "Lock must be released even when atomic_transition raises"
        )

    @pytest.mark.asyncio
    async def test_no_lock_release_when_instance_id_is_none(self, mock_repositories, service):
        """release_by_instance must not be called when job has no instance_id."""
        mock_job_repo, mock_lock_repo, _ = mock_repositories
        mock_job = create_mock_job(instance_id=None)

        result = await service._fail_orphaned_job(
            mock_job, "Recovered: no instance assigned", {"recovered": 0}
        )

        assert result is True
        mock_lock_repo.release_by_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_lock_release_when_instance_id_is_none_and_transition_fails(
        self, mock_repositories, service
    ):
        """No instance_id + failing transition: no lock release, no crash."""
        mock_job_repo, mock_lock_repo, _ = mock_repositories
        mock_job = create_mock_job(instance_id=None)

        mock_job_repo.atomic_transition.side_effect = RuntimeError("DB error")

        result = await service._fail_orphaned_job(
            mock_job, "Recovered: no instance assigned", {"recovered": 0}
        )

        assert result is False
        mock_lock_repo.release_by_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_release_failure_does_not_mask_result(self, mock_repositories, service):
        """If lock release itself fails, the transition result is still returned.

        The inner try/except prevents a lock-release exception from
        overriding the function's intended return value.
        """
        mock_job_repo, mock_lock_repo, _ = mock_repositories
        mock_job = create_mock_job(instance_id="inst-1")

        mock_lock_repo.release_by_instance.side_effect = RuntimeError("lock table gone")

        result = await service._fail_orphaned_job(
            mock_job, "Recovered: instance no longer exists", {"recovered": 0}
        )

        # Transition succeeded, so result is True; lock-release error is logged.
        assert result is True, "Transition result must not be masked by lock-release error"
        mock_lock_repo.release_by_instance.assert_called_once_with("inst-1")

    @pytest.mark.asyncio
    async def test_recovered_stat_only_incremented_on_success(self, mock_repositories, service):
        """stats['recovered'] is incremented only when transition succeeds."""
        mock_job_repo, mock_lock_repo, _ = mock_repositories
        mock_job = create_mock_job(instance_id="inst-1")
        stats = {"recovered": 0}

        mock_job_repo.atomic_transition.side_effect = RuntimeError("DB error")

        result = await service._fail_orphaned_job(
            mock_job, "Recovered: instance no longer exists", stats
        )

        assert result is False
        assert stats["recovered"] == 0, "Stats must not increment on failure"

    @pytest.mark.asyncio
    async def test_transition_called_exactly_once(self, mock_repositories, service):
        """atomic_transition must be called exactly once per orphan failure.

        Guard against future regressions that could cause double-transitions.
        """
        mock_job_repo, mock_lock_repo, _ = mock_repositories
        mock_job = create_mock_job(instance_id="inst-1")

        await service._fail_orphaned_job(
            mock_job, "Recovered: instance no longer exists", {"recovered": 0}
        )

        assert mock_job_repo.atomic_transition.call_count == 1

    @pytest.mark.asyncio
    async def test_recover_on_startup_invalid_transition_releases_lock_with_correct_order(
        self, mock_repositories, service
    ):
        """End-to-end: InvalidTransitionError from atomic_transition still releases lock
        via the finally block, and transition is attempted BEFORE release.
        """
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories
        mock_job = create_mock_job(instance_id="inst-1")
        mock_instance = create_mock_instance(status="completed")
        mock_job_repo.find_processing_jobs.return_value = [mock_job]
        mock_instance_repo.get.return_value = mock_instance

        call_order: list[str] = []

        def track_transition(*args, **kwargs):
            call_order.append("atomic_transition")
            raise InvalidTransitionError(
                job_id=mock_job.job_id,
                from_state="processing",
                to_state="failed",
            )

        def track_release(*args, **kwargs):
            call_order.append("release_by_instance")

        mock_job_repo.atomic_transition.side_effect = track_transition
        mock_lock_repo.release_by_instance.side_effect = track_release

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 0, "alive": 0, "total": 1}
        assert call_order == ["atomic_transition", "release_by_instance"], (
            f"Expected transition BEFORE release, got: {call_order}"
        )
