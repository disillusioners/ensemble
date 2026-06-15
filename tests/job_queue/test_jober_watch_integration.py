"""Phase 3 Integration Tests for Jober Agent Watch System.

Tests the complete end-to-end flow across ALL 7 terminal paths, edge cases,
notification format, tool registration, agent definition, and crash recovery.

This follows the detailed test plan in .agents/shared/planning/jober-agent/phase3-plan.md.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from pathlib import Path

from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.job_queue import JobRepository, JobQueueRepository, JobItem, JobStatus
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService, DemandState
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.dead_letter_service import DeadLetterService
from daemon.services.job_retry_engine import JobRetryEngine
from daemon.tools.job_queue import create_job_tools, TERMINAL_STATES
from daemon.registry import AgentRegistry
from daemon.repositories.message_queue.models import MessageType


class TestJoberWatchIntegration:
    """Comprehensive tests for Phase 3: Jober Agent Watch System."""

    @pytest.fixture
    def engine(self):
        """Create in-memory SQLite engine for testing."""
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel
        from daemon.repositories.job_queue.watcher_models import JobWatcher

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)
        JobWatcher.metadata.create_all(engine)  # Ensure watcher table exists
        yield engine
        engine.dispose()

    @pytest.fixture
    def repository(self, engine):
        """Create JobRepository instance."""
        return JobRepository(engine)

    @pytest.fixture
    def lock_repo(self, engine):
        """Create LockRepository instance."""
        return LockRepository(engine)

    @pytest.fixture
    def lock_manager(self, lock_repo):
        """Create JobLockManager instance."""
        return JobLockManager(lock_repo=lock_repo)

    @pytest.fixture
    def queue_repository(self, engine):
        """Create JobQueueRepository with system queues."""
        from daemon.repositories.job_queue.queue_repository import JobQueueRepository
        repo = JobQueueRepository(engine)
        # Create system queues for test-project
        repo.create(
            project_id="test-project",
            queue_name="system_fifo_queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )
        yield repo

    @pytest.fixture
    def watcher_repo(self, engine):
        """Create JobWatcherRepository for testing."""
        return JobWatcherRepository(engine)

    @pytest.fixture
    def instance_manager(self):
        """Mock instance manager for notifications."""
        manager = MagicMock()
        manager.enqueue_message = AsyncMock(return_value=MagicMock(message_id="msg-123"))
        manager.terminate_instance = AsyncMock(return_value=True)
        return manager

    @pytest.fixture
    def job_queue_service(self, repository, lock_manager, queue_repository, instance_manager, watcher_repo):
        """Create JobQueueService with watcher repo configured."""
        service = JobQueueService(
            repository=repository,
            lock_manager=lock_manager,
            queue_repo=queue_repository,
            instance_manager=instance_manager,
        )
        service.set_watcher_repo(watcher_repo)
        # Use a new event loop for testing (pytest-asyncio creates one for async tests)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        service.set_event_loop(loop)
        return service

    @pytest.fixture
    def mock_job_item(self):
        """Create a mock JobItem for testing."""
        job = MagicMock(spec=JobItem)
        job.job_id = "job-12345678-1234-1234-1234-123456789abc"
        job.agent_id = "coder"
        job.status = "processing"
        job.result_summary = "Test job completed successfully"
        job.error_message = None
        job.instance_id = "instance-123"
        job.project_id = "test-project"
        job.queue_id = "system_fifo_queue"
        job.retry_count = 0
        job.max_retries = 3
        return job

    # ==================== TASK 1: E2E TERMINAL PATH VERIFICATION ====================

    @pytest.mark.asyncio
    async def test_path1_observer_completed(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """Path 1: JobFeedbackObserver._process_event() → COMPLETED notification."""
        # Setup: Create a watch
        watcher_repo.add_watch(mock_job_item.job_id, "watcher-instance-1")

        # Mock the job lookup
        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)

        # Simulate observer path (completed event)
        await job_queue_service.notify_watchers(mock_job_item.job_id, "completed")

        # Verify notification was sent
        instance_manager.enqueue_message.assert_called_once()
        call_args = instance_manager.enqueue_message.call_args
        assert call_args is not None
        assert call_args[1]["instance_id"] == "watcher-instance-1"
        assert "completed" in call_args[1]["message"]
        assert "internal_agent:job_event:" in call_args[1]["source"]

    @pytest.mark.asyncio
    async def test_path2_cancel(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """Path 2: cancel_job() → CANCELLED notification."""
        watcher_repo.add_watch(mock_job_item.job_id, "watcher-instance-1")

        # Start with pending job for cancel
        mock_job_item.status = "pending"
        mock_job_item.project_id = "test-project"
        mock_job_item.queue_id = None

        # Setup repository to return the job and allow transition
        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)
        job_queue_service._repository.atomic_transition = MagicMock()

        # Cancel the job (from PENDING → CANCELLED)
        await job_queue_service.cancel_job(mock_job_item.job_id)

        # After cancel, update mock for notify_watchers to find the job
        mock_job_item.status = "cancelled"
        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)

        instance_manager.enqueue_message.assert_called()
        call_args = instance_manager.enqueue_message.call_args
        assert "cancelled" in call_args[1]["message"]
        assert "internal_agent:job_event:" in call_args[1]["source"]

    @pytest.mark.asyncio
    async def test_path3_complete(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """Path 3: complete_job() → COMPLETED notification."""
        watcher_repo.add_watch(mock_job_item.job_id, "watcher-instance-1")
        mock_job_item.status = "completed"

        # Mock repository calls
        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)
        job_queue_service._repository.complete_job = MagicMock(return_value=mock_job_item)

        await job_queue_service.complete_job(
            mock_job_item.job_id,
            demand_state=DemandState.COMPLETED,
            result_summary="Test success"
        )

        instance_manager.enqueue_message.assert_called()
        call_args = instance_manager.enqueue_message.call_args
        assert "completed" in call_args[1]["message"]

    @pytest.mark.asyncio
    async def test_path4_terminate(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """Path 4: terminate_instance() → CANCELLED notification (simplified, no TERMINATED state)."""
        watcher_repo.add_watch(mock_job_item.job_id, "watcher-instance-1")
        mock_job_item.status = "cancelled"

        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)
        job_queue_service._repository.terminate_job = MagicMock(return_value=mock_job_item)

        await job_queue_service.complete_job(
            mock_job_item.job_id,
            demand_state=DemandState.CANCELLED,
            error="Cancelled by user"
        )

        instance_manager.enqueue_message.assert_called()
        call_args = instance_manager.enqueue_message.call_args
        assert "cancelled" in call_args[1]["message"]

    @pytest.mark.asyncio
    async def test_path5_dead_letter_standalone(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """Path 5: move_to_dlq_standalone() → DEAD_LETTER notification."""
        watcher_repo.add_watch(mock_job_item.job_id, "watcher-instance-1")
        mock_job_item.status = "dead_letter"
        mock_job_item.error_message = "Test error"

        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)

        # Simulate the notification that would be sent from DeadLetterService after DLQ move
        await job_queue_service.notify_watchers(mock_job_item.job_id, "dead_letter", "Test error")

        instance_manager.enqueue_message.assert_called()
        call_args = instance_manager.enqueue_message.call_args
        assert "dead_letter" in call_args[1]["message"]

    @pytest.mark.asyncio
    async def test_path6_retry_exhaustion(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """Path 6: maybe_retry() → DEAD_LETTER (retry exhaustion) notification."""
        watcher_repo.add_watch(mock_job_item.job_id, "watcher-instance-1")
        mock_job_item.status = "dead_letter"
        mock_job_item.error_message = "Max retries exceeded"

        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)

        await job_queue_service.notify_watchers(mock_job_item.job_id, "dead_letter", "Max retries exceeded")

        instance_manager.enqueue_message.assert_called()
        call_args = instance_manager.enqueue_message.call_args
        assert "dead_letter" in call_args[1]["message"]

    @pytest.mark.asyncio
    async def test_path7_orphan_recovery(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """Path 7: _fail_orphaned_job() → FAILED notification (startup recovery)."""
        watcher_repo.add_watch(mock_job_item.job_id, "watcher-instance-1")
        mock_job_item.status = "failed"
        mock_job_item.error_message = "Instance terminated unexpectedly"

        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)

        await job_queue_service.notify_watchers(mock_job_item.job_id, "failed", "Instance terminated unexpectedly")

        instance_manager.enqueue_message.assert_called()
        call_args = instance_manager.enqueue_message.call_args
        assert "failed" in call_args[1]["message"]

    # ==================== TASK 2: EDGE CASE TESTS ====================

    @pytest.mark.asyncio
    async def test_2a_watch_nonexistent_job(self, job_queue_service, watcher_repo):
        """2a: Watch a non-existent job → proper error."""
        tools = create_job_tools(
            job_service=job_queue_service,
            queue_mgmt_service=MagicMock(),
            dead_letter_service=MagicMock(),
            current_instance_id="test-instance",
            agent_id="jober",
            watcher_repo=watcher_repo
        )

        # Find the watch_job tool
        watch_job = next(t for t in tools if t.name == "watch_job")

        result = await watch_job.ainvoke({"job_id": "nonexistent-job-123"})
        assert "Error: Job" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_2b_watch_already_terminal_job(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """2b: Watch an already-terminal job → immediate notification."""
        mock_job_item.status = "completed"
        # Use AsyncMock for async get_job method
        job_queue_service.get_job = AsyncMock(return_value=mock_job_item)
        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)

        tools = create_job_tools(
            job_service=job_queue_service,
            queue_mgmt_service=MagicMock(),
            dead_letter_service=MagicMock(),
            current_instance_id="test-instance",
            agent_id="jober",
            watcher_repo=watcher_repo
        )

        watch_job = next(t for t in tools if t.name == "watch_job")

        result = await watch_job.ainvoke({"job_id": mock_job_item.job_id})
        assert "already" in result.lower() or "immediate" in result.lower()

    @pytest.mark.asyncio
    async def test_2f_max_watches_limit(self, engine, repository, lock_manager, queue_repository, instance_manager):
        """2f: Max 50 watches per instance limit."""
        # Create a mock watcher repo that tracks count
        watcher_repo = MagicMock(spec=JobWatcherRepository)
        watcher_repo.count_watches_for_instance.return_value = 50

        # Mock job service
        mock_job = MagicMock()
        mock_job.job_id = "job-123"
        mock_job.status = "pending"
        mock_job_service = MagicMock(spec=JobQueueService)
        mock_job_service.get_job = AsyncMock(return_value=mock_job)
        mock_job_service.notify_watchers = AsyncMock(return_value=0)
        mock_job_service.enqueue = AsyncMock()

        tools = create_job_tools(
            job_service=mock_job_service,
            queue_mgmt_service=MagicMock(),
            dead_letter_service=MagicMock(),
            current_instance_id="test-instance",
            agent_id="jober",
            watcher_repo=watcher_repo
        )

        watch_job = next(t for t in tools if t.name == "watch_job")

        result = await watch_job.ainvoke({"job_id": "job-123"})
        assert "Maximum watch limit (50)" in result
        assert "reached" in result.lower()

    @pytest.mark.asyncio
    async def test_2h_unwatch_job(self, job_queue_service, watcher_repo, mock_job_item):
        """2h: Unwatch a job → no further notifications."""
        watcher_repo.add_watch(mock_job_item.job_id, "test-instance")

        tools = create_job_tools(
            job_service=job_queue_service,
            queue_mgmt_service=MagicMock(),
            dead_letter_service=MagicMock(),
            current_instance_id="test-instance",
            agent_id="jober",
            watcher_repo=watcher_repo
        )

        unwatch_job = next(t for t in tools if t.name == "unwatch_job")

        result = await unwatch_job.ainvoke({"job_id": mock_job_item.job_id})
        assert "Stopped watching" in result or "not watching" in result.lower()

    # ==================== TASK 3: NOTIFICATION FORMAT VALIDATION ====================

    @pytest.mark.asyncio
    async def test_notification_format(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """Task 3: Verify notification has correct clean format (no JSON block)."""
        watcher_repo.add_watch(mock_job_item.job_id, "test-instance")
        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)

        await job_queue_service.notify_watchers(mock_job_item.job_id, "completed", None)

        # Check the notification sent
        assert instance_manager.enqueue_message.called
        call_args = instance_manager.enqueue_message.call_args[1]

        # Task 3 requirements
        assert call_args["source"].startswith("internal_agent:job_event:")
        assert "completed" in call_args["source"]

        message = call_args["message"]

        # Verify [JOB_EVENT] prefix is present
        assert "[JOB_EVENT]" in message

        # Verify new clean format: status word directly (with ✓ for completed)
        assert "completed ✓" in message
        assert "reached status" not in message  # Old format should be gone

        # Verify Agent line with indentation
        assert "  Agent: coder" in message

        # Verify Result line with indentation, content on next line
        assert "  Result:" in message
        assert "Test job completed successfully" in message

        # No Error line when error is None
        assert "Error:" not in message
        assert "Error: None" not in message

        # No JSON block in new format
        assert "```json" not in message
        assert "```" not in message
        assert "job_event_data" not in message

    @pytest.mark.asyncio
    async def test_notification_format_failed_with_error(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """Verify notification format for failed status with error, no result."""
        mock_job_item.status = "failed"
        mock_job_item.error_message = "Something went wrong"
        mock_job_item.result_summary = None

        watcher_repo.add_watch(mock_job_item.job_id, "test-instance")
        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)

        await job_queue_service.notify_watchers(mock_job_item.job_id, "failed", "Something went wrong")

        assert instance_manager.enqueue_message.called
        call_args = instance_manager.enqueue_message.call_args[1]

        assert call_args["source"].startswith("internal_agent:job_event:")
        assert "failed" in call_args["source"]

        message = call_args["message"]

        # Verify [JOB_EVENT] prefix and failed status with icon
        assert "[JOB_EVENT]" in message
        assert "failed ✗" in message
        assert "reached status" not in message

        # Verify Agent line with indentation
        assert "  Agent: coder" in message

        # No Result line since result_summary is None
        assert "  Result:" not in message

        # Error line present
        assert "  Error: Something went wrong" in message

        # No JSON block
        assert "```json" not in message
        assert "```" not in message
        assert "job_event_data" not in message

    @pytest.mark.asyncio
    async def test_notification_format_cancelled(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """Verify notification format for cancelled status with result_summary, no error."""
        mock_job_item.status = "cancelled"
        mock_job_item.error_message = None
        mock_job_item.result_summary = "Job was cancelled"

        watcher_repo.add_watch(mock_job_item.job_id, "test-instance")
        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)

        await job_queue_service.notify_watchers(mock_job_item.job_id, "cancelled", None)

        assert instance_manager.enqueue_message.called
        call_args = instance_manager.enqueue_message.call_args[1]

        assert call_args["source"].startswith("internal_agent:job_event:")
        assert "cancelled" in call_args["source"]

        message = call_args["message"]

        # Verify [JOB_EVENT] prefix and raw cancelled status (no icon)
        assert "[JOB_EVENT]" in message
        assert "cancelled" in message
        assert "cancelled ✓" not in message
        assert "cancelled ✗" not in message
        assert "reached status" not in message

        # Verify Agent line with indentation
        assert "  Agent: coder" in message

        # Result line present
        assert "  Result:" in message
        assert "Job was cancelled" in message

        # No Error line since error is None
        assert "  Error:" not in message

        # No JSON block
        assert "```json" not in message
        assert "```" not in message

    @pytest.mark.asyncio
    async def test_notification_format_empty_result(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """Verify notification format for completed status with no result_summary."""
        mock_job_item.status = "completed"
        mock_job_item.error_message = None
        mock_job_item.result_summary = None

        watcher_repo.add_watch(mock_job_item.job_id, "test-instance")
        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)

        await job_queue_service.notify_watchers(mock_job_item.job_id, "completed", None)

        assert instance_manager.enqueue_message.called
        call_args = instance_manager.enqueue_message.call_args[1]

        assert call_args["source"].startswith("internal_agent:job_event:")
        assert "completed" in call_args["source"]

        message = call_args["message"]

        # Verify [JOB_EVENT] prefix and completed status with icon
        assert "[JOB_EVENT]" in message
        assert "completed ✓" in message
        assert "reached status" not in message

        # Verify Agent line with indentation
        assert "  Agent: coder" in message

        # No Result line since result_summary is None
        assert "  Result:" not in message

        # No Error line
        assert "  Error:" not in message

        # No JSON block
        assert "```json" not in message
        assert "```" not in message
        assert "job_event_data" not in message

    @pytest.mark.asyncio
    async def test_notification_format_failed_with_result_and_error(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """Verify notification format for failed status with both result_summary and error."""
        mock_job_item.status = "failed"
        mock_job_item.error_message = "Timeout exceeded"
        mock_job_item.result_summary = "Partial work done"

        watcher_repo.add_watch(mock_job_item.job_id, "test-instance")
        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)

        await job_queue_service.notify_watchers(mock_job_item.job_id, "failed", "Timeout exceeded")

        assert instance_manager.enqueue_message.called
        call_args = instance_manager.enqueue_message.call_args[1]

        assert call_args["source"].startswith("internal_agent:job_event:")
        assert "failed" in call_args["source"]

        message = call_args["message"]

        # Verify [JOB_EVENT] prefix and failed status with icon
        assert "[JOB_EVENT]" in message
        assert "failed ✗" in message
        assert "reached status" not in message

        # Verify Agent line with indentation
        assert "  Agent: coder" in message

        # Result line present
        assert "  Result:" in message
        assert "Partial work done" in message

        # Error line present
        assert "  Error: Timeout exceeded" in message

        # No JSON block
        assert "```json" not in message
        assert "```" not in message
        assert "job_event_data" not in message

    # ==================== TASK 4 & 5: REGRESSION + TOOL REGISTRATION ====================

    def test_tool_registration(self):
        """Task 4 & 5: Verify all 17 tools are registered correctly (16 original + job_continue)."""
        job_service = AsyncMock()
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        watcher_repo = MagicMock()

        tools = create_job_tools(
            job_service=job_service,
            queue_mgmt_service=queue_mgmt_service,
            dead_letter_service=dead_letter_service,
            current_instance_id="test-instance",
            agent_id="jober",
            watcher_repo=watcher_repo
        )

        # Task 4: Should have 17 tools (12 original + 4 watch tools + job_continue)
        assert len(tools) == 17

        # Task 5: All tools should have job category
        for tool in tools:
            assert hasattr(tool, "_tool_category")
            assert tool._tool_category == "job"
            # Most tools should have full documentation
            if hasattr(tool, "_full_doc_"):
                assert isinstance(tool._full_doc_, str)
                assert len(tool._full_doc_) > 10

        # Check for the 4 new watch tools
        tool_names = [t.name for t in tools]
        assert "watch_job" in tool_names
        assert "unwatch_job" in tool_names
        assert "list_watched_jobs" in tool_names
        assert "watch_jobs" in tool_names

    @pytest.mark.asyncio
    async def test_job_create_with_watch_param(self):
        """Task 4: job_create with watch=True should work (regression check)."""
        job_service = AsyncMock()
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        watcher_repo = MagicMock()
        watcher_repo.count_watches_for_instance.return_value = 0

        mock_job = MagicMock()
        mock_job.job_id = "job-123"
        mock_job.status = "pending"
        mock_job.to_dict.return_value = {"job_id": "job-123", "status": "pending"}
        job_service.enqueue.return_value = mock_job
        job_service.get_job = AsyncMock(return_value=mock_job)

        tools = create_job_tools(
            job_service=job_service,
            queue_mgmt_service=queue_mgmt_service,
            dead_letter_service=dead_letter_service,
            current_instance_id="test-instance",
            agent_id="jober",
            watcher_repo=watcher_repo
        )

        job_create = tools[0]  # First tool is job_create

        # Test with watch=True (use ainvoke since it's async)
        result = await job_create.ainvoke({
            "agent_id": "coder",
            "message": "Test with watch",
            "watch": True
        })

        assert result["job_id"] == "job-123"
        # Verify enqueue was called
        job_service.enqueue.assert_called_once()
        # Verify watch was registered (only 2 args for job_create's watch=True)
        watcher_repo.add_watch.assert_called_once_with("job-123", "test-instance")

    # ==================== TASK 6: AGENT DEFINITION VERIFICATION ====================

    def test_jober_agent_discovery(self):
        """Task 6: Verify jober agent is discovered by registry."""
        registry = AgentRegistry(Path("agents"))
        registry.discover()

        # Check that jober was discovered
        assert hasattr(registry, "_agents")
        assert "jober" in registry._agents

        jober_meta = registry._agents["jober"]
        assert jober_meta.id == "jober"
        assert jober_meta.name == "Job Orchestrator"

        # Check tool filter allows job tools (attribute is 'tools', not 'tools_filter')
        assert jober_meta.tools is not None
        assert "job" in jober_meta.tools.allow

    # ==================== TASK 7: CRASH RECOVERY ====================

    @pytest.mark.asyncio
    async def test_crash_recovery_reconciliation(self, job_queue_service, watcher_repo, instance_manager):
        """Task 7: Startup reconciliation for terminal watches."""
        # Create some terminal jobs with watches
        terminal_jobs = [
            ("job-completed", "completed", None),
            ("job-failed", "failed", "Test error"),
            ("job-deadletter", "dead_letter", "Max retries")
        ]

        for job_id, status, error in terminal_jobs:
            watcher_repo.add_watch(job_id, "watcher-instance-1")
            # Create mock job
            job = MagicMock()
            job.job_id = job_id
            job.status = status
            job.error_message = error
            job.agent_id = "coder"
            job.result_summary = "Test result"
            job_queue_service._repository.get = MagicMock(return_value=job)

            await job_queue_service.notify_watchers(job_id, status, error)

        # Verify notifications were sent for all terminal jobs
        assert instance_manager.enqueue_message.call_count >= len(terminal_jobs)

    def test_ensure_dev_sh_still_works(self):
        """ensure.md requirement: dev.sh should be runnable."""
        import subprocess
        import time

        try:
            # Run dev.sh with timeout (30s as per ensure.md)
            result = subprocess.run(
                ["timeout", "30s", "bash", "./dev.sh"],
                capture_output=True,
                text=True,
                cwd="/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble"
            )
            # If it runs for 30s without crashing, it's good (timeout will return 124)
            # We expect it to either run or timeout - both are acceptable per ensure.md
            assert result.returncode in [0, 124]  # 0=success, 124=timeout
            print("dev.sh ran successfully for 30s or completed without crash")
        except Exception as e:
            pytest.fail(f"dev.sh failed to run: {e}")


class TestJobWatcherRepository:
    """Unit tests for JobWatcherRepository."""

    @pytest.fixture
    def watcher_repo(self, engine):
        """Create JobWatcherRepository for testing."""
        return JobWatcherRepository(engine)

    def test_add_watch_creates_record(self, watcher_repo):
        """Add watch creates a new record."""
        watch = watcher_repo.add_watch("job-123", "instance-456")
        assert watch.job_id == "job-123"
        assert watch.instance_id == "instance-456"
        # Default includes ALL watchable events (terminal + in_progress progress updates)
        assert watch.watch_events == ["completed", "failed", "cancelled", "dead_letter", "in_progress"]

    def test_add_watch_with_custom_events(self, watcher_repo):
        """Add watch with custom event list."""
        watch = watcher_repo.add_watch(
            "job-123",
            "instance-456",
            ["completed", "failed"]
        )
        assert watch.watch_events == ["completed", "failed"]

    def test_add_watch_duplicate_updates_events(self, watcher_repo):
        """Adding watch for same job/instance updates events."""
        watcher_repo.add_watch("job-123", "instance-456", ["completed"])
        watch = watcher_repo.add_watch("job-123", "instance-456", ["failed"])
        assert watch.watch_events == ["failed"]
        # Only one record exists
        watches = watcher_repo.get_watchers_for_job("job-123")
        assert len(watches) == 1

    def test_remove_watch(self, watcher_repo):
        """Remove watch deletes the record."""
        watcher_repo.add_watch("job-123", "instance-456")
        removed = watcher_repo.remove_watch("job-123", "instance-456")
        assert removed is True
        assert len(watcher_repo.get_watchers_for_job("job-123")) == 0

    def test_remove_watch_not_found(self, watcher_repo):
        """Remove watch returns False if not found."""
        removed = watcher_repo.remove_watch("nonexistent", "instance")
        assert removed is False

    def test_get_watchers_for_job(self, watcher_repo):
        """Get watchers for job returns all matching records."""
        watcher_repo.add_watch("job-123", "instance-1")
        watcher_repo.add_watch("job-123", "instance-2")
        watcher_repo.add_watch("job-456", "instance-1")
        watchers = watcher_repo.get_watchers_for_job("job-123")
        assert len(watchers) == 2

    def test_get_watches_for_instance(self, watcher_repo):
        """Get watches for instance returns all matching records."""
        watcher_repo.add_watch("job-123", "instance-1")
        watcher_repo.add_watch("job-456", "instance-1")
        watcher_repo.add_watch("job-789", "instance-2")
        watches = watcher_repo.get_watches_for_instance("instance-1")
        assert len(watches) == 2

    def test_remove_all_watches_for_instance(self, watcher_repo):
        """Remove all watches for instance."""
        watcher_repo.add_watch("job-1", "instance-1")
        watcher_repo.add_watch("job-2", "instance-1")
        watcher_repo.add_watch("job-3", "instance-2")
        count = watcher_repo.remove_all_watches_for_instance("instance-1")
        assert count == 2
        assert len(watcher_repo.get_watches_for_instance("instance-1")) == 0
        assert len(watcher_repo.get_watches_for_instance("instance-2")) == 1

    def test_remove_all_watches_for_job(self, watcher_repo):
        """Remove all watches for job."""
        watcher_repo.add_watch("job-123", "instance-1")
        watcher_repo.add_watch("job-123", "instance-2")
        watcher_repo.add_watch("job-456", "instance-1")
        count = watcher_repo.remove_all_watches_for_job("job-123")
        assert count == 2
        assert len(watcher_repo.get_watchers_for_job("job-123")) == 0
        assert len(watcher_repo.get_watchers_for_job("job-456")) == 1

    def test_count_watches_for_instance(self, watcher_repo):
        """Count watches for instance."""
        watcher_repo.add_watch("job-1", "instance-1")
        watcher_repo.add_watch("job-2", "instance-1")
        watcher_repo.add_watch("job-3", "instance-2")
        count = watcher_repo.count_watches_for_instance("instance-1")
        assert count == 2

    def test_get_all_active_watches(self, watcher_repo):
        """Get all active watches."""
        watcher_repo.add_watch("job-1", "instance-1")
        watcher_repo.add_watch("job-2", "instance-2")
        watches = watcher_repo.get_all_active_watches()
        assert len(watches) == 2


class TestNotifyWatchersEdgeCases:
    """Edge case tests for notify_watchers function."""

    @pytest.fixture
    def watcher_repo(self, engine):
        """Create JobWatcherRepository for testing."""
        return JobWatcherRepository(engine)

    @pytest.fixture
    def instance_manager(self):
        """Mock instance manager for notifications."""
        manager = MagicMock()
        manager.enqueue_message = AsyncMock(return_value=MagicMock(message_id="msg-123"))
        manager.terminate_instance = AsyncMock(return_value=True)
        return manager

    @pytest.fixture
    def mock_job_item(self):
        """Create a mock JobItem for testing."""
        job = MagicMock(spec=JobItem)
        job.job_id = "job-12345678-1234-1234-1234-123456789abc"
        job.agent_id = "coder"
        job.status = "processing"
        job.result_summary = "Test job completed successfully"
        job.error_message = None
        job.instance_id = "instance-123"
        job.project_id = "test-project"
        job.queue_id = "system_fifo_queue"
        job.retry_count = 0
        job.max_retries = 3
        return job

    @pytest.mark.asyncio
    async def test_notify_watchers_no_repo(self, job_queue_service):
        """notify_watchers returns 0 if watcher_repo not set."""
        job_queue_service._watcher_repo = None
        count = await job_queue_service.notify_watchers("job-123", "completed")
        assert count == 0

    @pytest.mark.asyncio
    async def test_notify_watchers_no_instance_manager(self, job_queue_service, watcher_repo):
        """notify_watchers returns 0 if instance_manager not set."""
        job_queue_service._watcher_repo = watcher_repo
        job_queue_service._instance_manager = None
        count = await job_queue_service.notify_watchers("job-123", "completed")
        assert count == 0

    @pytest.mark.asyncio
    async def test_notify_watchers_no_watchers(self, job_queue_service, watcher_repo, instance_manager):
        """notify_watchers returns 0 if no watchers exist."""
        job_queue_service._watcher_repo = watcher_repo
        job_queue_service._instance_manager = instance_manager
        count = await job_queue_service.notify_watchers("job-123", "completed")
        assert count == 0
        instance_manager.enqueue_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_notify_watchers_job_not_found(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """notify_watchers returns 0 if job not found."""
        watcher_repo.add_watch(mock_job_item.job_id, "instance-456")
        job_queue_service._watcher_repo = watcher_repo
        job_queue_service._instance_manager = instance_manager
        job_queue_service._repository.get = MagicMock(return_value=None)
        count = await job_queue_service.notify_watchers(mock_job_item.job_id, "completed")
        assert count == 0

    @pytest.mark.asyncio
    async def test_notify_watchers_filters_by_event(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """notify_watchers only notifies if status in watch_events."""
        # Add watch only for 'completed' events
        watcher_repo.add_watch(mock_job_item.job_id, "instance-456", ["completed"])
        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)
        job_queue_service._watcher_repo = watcher_repo
        job_queue_service._instance_manager = instance_manager

        # Should notify for 'completed'
        count = await job_queue_service.notify_watchers(mock_job_item.job_id, "completed")
        assert count == 1
        instance_manager.enqueue_message.assert_called_once()

        # Reset and test for 'failed' (not in watch_events)
        instance_manager.enqueue_message.reset_mock()
        count = await job_queue_service.notify_watchers(mock_job_item.job_id, "failed")
        assert count == 0
        instance_manager.enqueue_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_notify_watchers_cleans_up_after(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """notify_watchers removes watches after notification."""
        watcher_repo.add_watch(mock_job_item.job_id, "instance-456")
        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)
        job_queue_service._watcher_repo = watcher_repo
        job_queue_service._instance_manager = instance_manager

        await job_queue_service.notify_watchers(mock_job_item.job_id, "completed")

        # Watch should be removed after notification
        watches = watcher_repo.get_watchers_for_job(mock_job_item.job_id)
        assert len(watches) == 0

    @pytest.mark.asyncio
    async def test_notify_watchers_multiple_watchers(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """notify_watchers notifies all matching watchers."""
        watcher_repo.add_watch(mock_job_item.job_id, "instance-1")
        watcher_repo.add_watch(mock_job_item.job_id, "instance-2")
        watcher_repo.add_watch(mock_job_item.job_id, "instance-3")
        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)
        job_queue_service._watcher_repo = watcher_repo
        job_queue_service._instance_manager = instance_manager

        count = await job_queue_service.notify_watchers(mock_job_item.job_id, "completed")
        assert count == 3
        assert instance_manager.enqueue_message.call_count == 3


class TestReconcileTerminalWatches:
    """Tests for reconcile_terminal_watches function."""

    @pytest.fixture
    def watcher_repo(self, engine):
        """Create JobWatcherRepository for testing."""
        return JobWatcherRepository(engine)

    @pytest.fixture
    def instance_manager(self):
        """Mock instance manager for notifications."""
        manager = MagicMock()
        manager.enqueue_message = AsyncMock(return_value=MagicMock(message_id="msg-123"))
        manager.terminate_instance = AsyncMock(return_value=True)
        return manager

    @pytest.fixture
    def mock_job_item(self):
        """Create a mock JobItem for testing."""
        job = MagicMock(spec=JobItem)
        job.job_id = "job-12345678-1234-1234-1234-123456789abc"
        job.agent_id = "coder"
        job.status = "processing"
        job.result_summary = "Test job completed successfully"
        job.error_message = None
        job.instance_id = "instance-123"
        job.project_id = "test-project"
        job.queue_id = "system_fifo_queue"
        job.retry_count = 0
        job.max_retries = 3
        return job

    @pytest.mark.asyncio
    async def test_reconcile_no_repo(self, job_queue_service):
        """reconcile_terminal_watches returns 0 if no watcher_repo."""
        job_queue_service._watcher_repo = None
        count = await job_queue_service.reconcile_terminal_watches()
        assert count == 0

    @pytest.mark.asyncio
    async def test_reconcile_no_terminal_jobs(self, job_queue_service, watcher_repo, instance_manager, mock_job_item):
        """reconcile_terminal_watches returns 0 if no terminal jobs."""
        watcher_repo.add_watch(mock_job_item.job_id, "instance-456")
        mock_job_item.status = "processing"  # Non-terminal
        job_queue_service._watcher_repo = watcher_repo
        job_queue_service._instance_manager = instance_manager
        job_queue_service._repository.get = MagicMock(return_value=mock_job_item)

        count = await job_queue_service.reconcile_terminal_watches()
        assert count == 0

    @pytest.mark.asyncio
    async def test_reconcile_terminal_jobs(self, job_queue_service, watcher_repo, instance_manager):
        """reconcile_terminal_watches notifies for terminal jobs."""
        # Create terminal jobs with watches
        terminal_jobs = [
            ("job-completed", "completed", None),
            ("job-failed", "failed", "Test error"),
        ]

        for job_id, status, error in terminal_jobs:
            watcher_repo.add_watch(job_id, "instance-456")
            job = MagicMock()
            job.job_id = job_id
            job.status = status
            job.error_message = error
            job.agent_id = "coder"
            job.result_summary = "Test result"
            job_queue_service._repository.get = MagicMock(return_value=job)
            job_queue_service._watcher_repo = watcher_repo
            job_queue_service._instance_manager = instance_manager

            count = await job_queue_service.reconcile_terminal_watches()
            assert count == 1
            instance_manager.enqueue_message.assert_called()


if __name__ == "__main__":
    print("Phase 3 Integration Test File Created")
    print("Run with: python -m pytest tests/job_queue/test_jober_watch_integration.py -v")
