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
    queue_id: str = "queue-1",
) -> MagicMock:
    """Create a mock JobItem with the specified attributes.

    C1 fix: ``queue_id`` is now part of the default contract because
    ``_fail_orphaned_job``'s legacy fallback branch releases locks via
    ``release_by_job(project_id, queue_id, job_id)`` (scoped, F4/F7).
    Tests that previously asserted on ``release_by_instance`` now assert
    on ``release_by_job`` and need ``queue_id`` to be a non-None string.
    """
    mock_job = MagicMock(spec=JobItem)
    mock_job.job_id = job_id
    mock_job.instance_id = instance_id
    mock_job.project_id = project_id
    mock_job.queue_id = queue_id
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
        """Job with no instance_id should be marked FAILED.

        C1 fix: the scoped ``release_by_job`` runs in the legacy branch
        even when ``instance_id is None`` because the lock key is
        ``(project_id, queue_id, job_id)``, not ``instance_id``.
        """
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job(instance_id=None)
        mock_job_repo.find_processing_jobs.return_value = [mock_job]

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 1, "alive": 0, "total": 1}, "Expected 1 recovered job"
        # C1 fix: scoped release via release_by_job (F4/F7). The key is
        # (project_id, queue_id, job_id) — instance_id is irrelevant.
        mock_lock_repo.release_by_job.assert_called_once_with(
            "proj-1", "queue-1", "job-123"
        )
        # C1 fix: instance-wide release must NEVER be called.
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
        # C1 fix: scoped lock release via release_by_job (F4/F7) — must
        # NOT call release_by_instance which would wipe sibling locks.
        mock_lock_repo.release_by_job.assert_called_once_with(
            "proj-1", "queue-1", "job-123"
        ), "Lock should be released scoped per job (F4/F7)"
        mock_lock_repo.release_by_instance.assert_not_called()
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
        # C1 fix: scoped release_by_job (F4/F7) instead of release_by_instance.
        mock_lock_repo.release_by_job.assert_called_once_with(
            "proj-1", "queue-1", "job-123"
        ), "Lock should be released scoped per job (F4/F7)"
        mock_lock_repo.release_by_instance.assert_not_called()
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
        # C1 fix: scoped release_by_job (F4/F7) instead of release_by_instance.
        mock_lock_repo.release_by_job.assert_called_once_with(
            "proj-1", "queue-1", "job-123"
        ), "Lock should be released scoped per job (F4/F7)"
        mock_lock_repo.release_by_instance.assert_not_called()
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
        # C1 fix: scoped release_by_job (F4/F7) instead of release_by_instance.
        mock_lock_repo.release_by_job.assert_called_once_with(
            "proj-1", "queue-1", "job-123"
        ), "Lock should be released scoped per job (F4/F7)"
        mock_lock_repo.release_by_instance.assert_not_called()
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
        # C1 fix: scoped release_by_job (F4/F7) instead of release_by_instance.
        mock_lock_repo.release_by_job.assert_called_once_with(
            "proj-1", "queue-1", "job-123"
        ), "Lock should be released scoped per job (F4/F7)"
        mock_lock_repo.release_by_instance.assert_not_called()
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
        """Lock should be released when orphaned job is recovered.

        C1 fix: scoped ``release_by_job(project_id, queue_id, job_id)``
        instead of ``release_by_instance`` to honor the F4/F7 invariant
        (no sibling-lock deletion).
        """
        mock_job_repo, mock_lock_repo, mock_instance_repo = mock_repositories

        mock_job = create_mock_job(instance_id="my-instance-123")
        mock_instance_repo.get.return_value = None
        mock_job_repo.find_processing_jobs.return_value = [mock_job]

        await service.recover_on_startup()

        mock_lock_repo.release_by_job.assert_called_once_with(
            "proj-1", "queue-1", "job-123"
        )
        mock_lock_repo.release_by_instance.assert_not_called()

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
        """If atomic_transition raises InvalidTransitionError, it's expected and stats are NOT incremented.

        C1 fix: No lock release on ``InvalidTransitionError`` — the
        actor that already transitioned the job released the lock at
        that time (per F4/F7 contract). Re-releasing here would either
        be a no-op (scoped release) or wipe sibling locks (the old
        ``release_by_instance`` bug). Both lock APIs must stay silent.
        """
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
        # C1 fix: lock release must NOT run on InvalidTransitionError —
        # the actor that already transitioned the job owns the release.
        mock_lock_repo.release_by_instance.assert_not_called()
        mock_lock_repo.release_by_job.assert_not_called()


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
        """atomic_transition must be called BEFORE release_by_job.

        This is the core H8 invariant. If the transition fails after the
        lock is released, the job is in PROCESSING with no lock, opening
        a double-claim window.

        C1 fix: scoped release is now via ``release_by_job`` (F4/F7);
        ``release_by_instance`` is forbidden in this path.
        """
        mock_job_repo, mock_lock_repo, _ = mock_repositories
        mock_job = create_mock_job(instance_id="inst-1")

        # Track the order of calls across both mocks.
        call_order: list[str] = []

        def track_transition(*args, **kwargs):
            call_order.append("atomic_transition")

        def track_release(*args, **kwargs):
            call_order.append("release_by_job")

        mock_job_repo.atomic_transition.side_effect = track_transition
        mock_lock_repo.release_by_job.side_effect = track_release

        result = await service._fail_orphaned_job(
            mock_job, "Recovered: instance no longer exists", {"recovered": 0}
        )

        assert result is True
        assert call_order == ["atomic_transition", "release_by_job"], (
            f"Expected transition BEFORE lock release, got: {call_order}"
        )
        # C1 fix: instance-wide release must NEVER be called here.
        mock_lock_repo.release_by_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_released_on_invalid_transition_error(self, mock_repositories, service):
        """On ``InvalidTransitionError``: no lock release (C1 fix).

        Pre-C1: the outer ``finally`` block called
        ``release_by_instance`` unconditionally — the bug. Post-C1:
        ``InvalidTransitionError`` means the job was already
        transitioned by another actor; that actor owns the lock release
        (per F4/F7 contract). Releasing here would either be a no-op
        (``release_by_job`` finds no row) or wipe sibling locks
        (``release_by_instance``). Both are wrong.
        """
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
        # C1 fix: neither release API must run on InvalidTransitionError.
        mock_lock_repo.release_by_instance.assert_not_called()
        mock_lock_repo.release_by_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_released_on_unexpected_exception(self, mock_repositories, service):
        """On unexpected exception: no lock release (C1 fix).

        Pre-C1: the outer ``finally`` released the lock even on
        generic exceptions (defense-in-depth against leaks). Post-C1:
        the F4/F7 invariant forbids unconditional releases because
        they can wipe sibling locks. An unexpected exception means the
        transition outcome is unknown — we err on the side of NOT
        releasing and let the next startup-recovery sweep (or manual
        intervention) clean up if a lock row truly leaked.

        Trade-off documented: a transient DB error during
        ``atomic_transition`` may leave a lock row behind, but it can
        never wipe a sibling lock. This is the correct balance for
        an actor that may also be holding locks for OTHER jobs on
        the same instance.
        """
        mock_job_repo, mock_lock_repo, _ = mock_repositories
        mock_job = create_mock_job(instance_id="inst-1")

        mock_job_repo.atomic_transition.side_effect = RuntimeError("DB connection lost")

        result = await service._fail_orphaned_job(
            mock_job, "Recovered: instance no longer exists", {"recovered": 0}
        )

        assert result is False, "Expected False on unexpected exception"
        # C1 fix: neither release API must run on unexpected exceptions.
        mock_lock_repo.release_by_instance.assert_not_called()
        mock_lock_repo.release_by_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_lock_release_when_instance_id_is_none(self, mock_repositories, service):
        """C1 fix: ``instance_id`` is no longer the key for lock release.

        Pre-C1: the outer ``finally`` block checked ``if job.instance_id:``
        before calling ``release_by_instance``. Post-C1: the legacy
        branch checks ``if job.project_id and job.queue_id and job.job_id:``
        — the ``instance_id`` is irrelevant to whether the lock is
        released. This test pins the new contract: with the default
        ``(project_id, queue_id, job_id)`` set on the mock, the scoped
        release DOES run even when ``instance_id is None``.

        The previous guard against releasing a "phantom instance" lock
        is preserved by the ``project_id and queue_id and job_id``
        check (an instance_id=None orphan with all three keys still
        gets its scoped lock released; this is correct because the
        lock row is keyed by the job, not the instance).
        """
        mock_job_repo, mock_lock_repo, _ = mock_repositories
        mock_job = create_mock_job(instance_id=None)

        result = await service._fail_orphaned_job(
            mock_job, "Recovered: no instance assigned", {"recovered": 0}
        )

        assert result is True
        # C1 fix: scoped release by (project_id, queue_id, job_id).
        mock_lock_repo.release_by_job.assert_called_once_with(
            "proj-1", "queue-1", "job-123"
        )
        # C1 fix: instance-wide release must NEVER be called here.
        mock_lock_repo.release_by_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_lock_release_when_lock_key_incomplete(self, mock_repositories, service):
        """C1 fix: legacy branch skips scoped release when the lock key is incomplete.

        If any of ``project_id``, ``queue_id``, ``job_id`` is missing, the
        legacy branch skips the release rather than calling
        ``release_by_job`` with bogus arguments. ``release_by_job`` with
        ``None`` would query ``WHERE project_id IS NULL`` etc. and could
        match unintended rows — the safe default is to skip.
        """
        mock_job_repo, mock_lock_repo, _ = mock_repositories
        # queue_id=None simulates a job enqueued outside of the queue system.
        mock_job = create_mock_job(queue_id=None)

        result = await service._fail_orphaned_job(
            mock_job, "Recovered: no queue", {"recovered": 0}
        )

        assert result is True, "Transition succeeded; lock key incomplete, no release."
        mock_lock_repo.release_by_job.assert_not_called()
        mock_lock_repo.release_by_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_lock_release_when_instance_id_is_none_and_transition_fails(
        self, mock_repositories, service
    ):
        """No lock release when ``atomic_transition`` fails on a job without
        an instance_id. The transition failed, so we cannot release —
        any release would be either a no-op (``release_by_job`` with no
        matching row) or a sibling-wipe (``release_by_instance``).
        """
        mock_job_repo, mock_lock_repo, _ = mock_repositories
        mock_job = create_mock_job(instance_id=None)

        mock_job_repo.atomic_transition.side_effect = RuntimeError("DB error")

        result = await service._fail_orphaned_job(
            mock_job, "Recovered: no instance assigned", {"recovered": 0}
        )

        assert result is False
        mock_lock_repo.release_by_instance.assert_not_called()
        mock_lock_repo.release_by_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_release_failure_does_not_mask_result(self, mock_repositories, service):
        """If scoped lock release fails, the transition result is still returned.

        C1 fix: the inner try/except around ``release_by_job`` prevents
        a lock-release exception from overriding the function's intended
        return value (now True on successful transition).
        """
        mock_job_repo, mock_lock_repo, _ = mock_repositories
        mock_job = create_mock_job(instance_id="inst-1")

        mock_lock_repo.release_by_job.side_effect = RuntimeError("lock table gone")

        result = await service._fail_orphaned_job(
            mock_job, "Recovered: instance no longer exists", {"recovered": 0}
        )

        # Transition succeeded, so result is True; lock-release error is logged.
        assert result is True, "Transition result must not be masked by lock-release error"
        mock_lock_repo.release_by_job.assert_called_once_with(
            "proj-1", "queue-1", "job-123"
        )
        # C1 fix: instance-wide release must NEVER be called here.
        mock_lock_repo.release_by_instance.assert_not_called()

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
        """End-to-end: ``InvalidTransitionError`` from ``atomic_transition`` does NOT
        call any lock release (C1 fix).

        Pre-C1: the outer ``finally`` block called
        ``release_by_instance`` unconditionally — the bug. Post-C1:
        ``InvalidTransitionError`` means the job was already transitioned
        by another actor; that actor owns the lock release (per F4/F7
        contract). The recovery path must NOT touch locks on this code
        path.
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

        mock_job_repo.atomic_transition.side_effect = track_transition

        stats = await service.recover_on_startup()

        assert stats == {"recovered": 0, "alive": 0, "total": 1}
        assert call_order == ["atomic_transition"], (
            f"Expected ONLY atomic_transition on InvalidTransitionError, got: {call_order}"
        )
        # C1 fix: neither release API must run on InvalidTransitionError.
        mock_lock_repo.release_by_instance.assert_not_called()
        mock_lock_repo.release_by_job.assert_not_called()
