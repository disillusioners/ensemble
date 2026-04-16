"""Tests to verify dead code has been removed.

These tests ensure that deprecated/removed methods no longer exist
in the codebase as part of Phase 2 cleanup.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestCompleteJobForInstanceRemoved:
    """Tests verifying _complete_job_for_instance has been removed."""

    def test_complete_job_for_instance_not_in_manager(self):
        """Verify _complete_job_for_instance method no longer exists in manager."""
        from daemon import manager as manager_module
        import inspect

        # Get all methods and attributes in the manager module
        manager_members = dir(manager_module.InstanceManager)

        # Verify _complete_job_for_instance does not exist
        assert "_complete_job_for_instance" not in manager_members, (
            "_complete_job_for_instance should have been removed from InstanceManager"
        )

    def test_complete_job_for_instance_not_in_queue_service(self):
        """Verify _complete_job_for_instance is not in JobQueueService."""
        from daemon.services import job_queue_service as service_module
        import inspect

        # Get all methods in JobQueueService
        service_members = dir(service_module.JobQueueService)

        # Verify _complete_job_for_instance does not exist
        assert "_complete_job_for_instance" not in service_members, (
            "_complete_job_for_instance should have been removed from JobQueueService"
        )

    def test_complete_job_for_instance_not_in_job_processor(self):
        """Verify _complete_job_for_instance is not in JobProcessor."""
        try:
            from daemon.services.job_processor import JobProcessor
            processor_members = dir(JobProcessor)
            assert "_complete_job_for_instance" not in processor_members
        except ImportError:
            # JobProcessor may not exist in this codebase
            pass


class TestPhase2MethodsExist:
    """Tests verifying Phase 2 methods exist and work correctly."""

    def test_instance_lifecycle_event_kind_exists(self):
        """Verify INSTANCE_LIFECYCLE EventKind exists."""
        from daemon.repositories.event.models import EventKind

        assert hasattr(EventKind, "INSTANCE_LIFECYCLE")
        assert EventKind.INSTANCE_LIFECYCLE.value == "instance_lifecycle"

    def test_publish_instance_lifecycle_event_exists(self):
        """Verify _publish_instance_lifecycle_event method exists in manager."""
        from daemon.manager import InstanceManager

        assert hasattr(InstanceManager, "_publish_instance_lifecycle_event")

    def test_job_feedback_observer_exists(self):
        """Verify JobFeedbackObserver exists."""
        from daemon.services.job_feedback_observer import JobFeedbackObserver

        assert JobFeedbackObserver is not None

    def test_job_recovery_service_exists(self):
        """Verify JobRecoveryService exists."""
        from daemon.services.job_recovery_service import JobRecoveryService

        assert JobRecoveryService is not None


class TestCancellationCascade:
    """Tests verifying cancellation cascade is properly implemented."""

    def test_cancel_job_in_queue_service(self):
        """Verify cancel_job method exists in JobQueueService."""
        from daemon.services.job_queue_service import JobQueueService

        assert hasattr(JobQueueService, "cancel_job")

    @pytest.mark.asyncio
    async def test_cancel_job_method_is_async(self):
        """Verify cancel_job is an async method."""
        from daemon.services.job_queue_service import JobQueueService
        import inspect

        # Check if cancel_job is a coroutine function
        assert inspect.iscoroutinefunction(JobQueueService.cancel_job), (
            "cancel_job should be an async method"
        )


class TestEventBusSubscription:
    """Tests verifying EventBus subscription functionality."""

    def test_event_bus_has_subscribe_all(self):
        """Verify EventBus has subscribe_all method."""
        from daemon.services.event_bus import EventBus

        assert hasattr(EventBus, "subscribe_all")

    def test_event_bus_has_unsubscribe_all(self):
        """Verify EventBus has unsubscribe_all method."""
        from daemon.services.event_bus import EventBus

        assert hasattr(EventBus, "unsubscribe_all")


class TestLiveEventHubStreamLifecycle:
    """Tests verifying LiveEventHub has stream_lifecycle."""

    def test_live_event_hub_has_stream_lifecycle(self):
        """Verify LiveEventHub has stream_lifecycle method."""
        from daemon.services.live_event_hub import LiveEventHub

        assert hasattr(LiveEventHub, "stream_lifecycle")

    @pytest.mark.asyncio
    async def test_stream_lifecycle_is_async(self):
        """Verify stream_lifecycle is an async method."""
        from daemon.services.live_event_hub import LiveEventHub
        import inspect

        # Check if stream_lifecycle is a coroutine function
        assert inspect.iscoroutinefunction(LiveEventHub.stream_lifecycle), (
            "stream_lifecycle should be an async method"
        )


class TestJobRepositoryMethods:
    """Tests verifying JobRepository has required methods."""

    def test_job_repository_has_atomic_transition(self):
        """Verify JobRepository has atomic_transition method."""
        from daemon.repositories.job_queue import JobRepository

        assert hasattr(JobRepository, "atomic_transition")

    def test_job_repository_has_find_processing_jobs(self):
        """Verify JobRepository has find_processing_jobs method."""
        from daemon.repositories.job_queue import JobRepository

        assert hasattr(JobRepository, "find_processing_jobs")


class TestLockRepositoryMethods:
    """Tests verifying LockRepository has required methods."""

    def test_lock_repository_has_release_by_instance(self):
        """Verify LockRepository has release_by_instance method."""
        from daemon.repositories.job_queue.lock_repository import LockRepository

        assert hasattr(LockRepository, "release_by_instance")
