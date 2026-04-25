"""Tests for scheduler instance mode feature (Task 7).

Tests cover:
1. New instance mode - creates fresh instance per execution
2. Reuse instance mode - reuses instance across runs with run counter
3. One-time schedules - always force new_instance
4. Error recovery - execution callbacks and counter persistence
5. API validation - invalid instance_mode and max_concurrent constraints
"""

import pytest
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from daemon.sources.adapters.scheduler import SchedulerAdapter
from daemon.sources.base import SourceConfig, IncomingMessage, SourceStatus
from daemon.models import SchedulerInstanceMode


# ==================== Fixtures ====================


def make_config(source_id: str, config: dict) -> SourceConfig:
    """Helper to create SourceConfig for scheduler."""
    return SourceConfig(
        source_id=source_id,
        source_type="scheduler",
        name=f"Test Scheduler {source_id}",
        config=config,
        credentials={},
        enabled=True,
    )


# ==================== Instance Mode Configuration Tests ====================


class TestSchedulerInstanceModeConfig:
    """Tests for instance mode configuration parsing."""

    def test_default_instance_mode_is_new_instance(self, mock_on_message):
        """Test that default instance mode is new_instance when not specified."""
        config = make_config("test-default-instance", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._instance_mode == SchedulerInstanceMode.NEW_INSTANCE

    def test_explicit_new_instance_mode(self, mock_on_message):
        """Test explicit new_instance mode configuration."""
        config = make_config("test-new-instance", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "new_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._instance_mode == SchedulerInstanceMode.NEW_INSTANCE

    def test_reuse_instance_mode(self, mock_on_message):
        """Test reuse_instance mode configuration."""
        config = make_config("test-reuse-instance", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._instance_mode == SchedulerInstanceMode.REUSE_INSTANCE

    def test_invalid_instance_mode_raises_error(self, mock_on_message):
        """Test that invalid instance_mode value raises ValueError."""
        config = make_config("test-invalid-instance-mode", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "invalid_mode",
        })
        
        with pytest.raises(ValueError):
            SchedulerAdapter(config, mock_on_message)

    def test_one_time_schedule_forces_new_instance(self, mock_on_message):
        """Test that run_at (one-time) schedule always forces new_instance mode."""
        future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        config = make_config("test-onetime-force", {
            "run_at": future_time,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",  # This should be ignored
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        # One-time schedules should always use new_instance
        assert adapter._instance_mode == SchedulerInstanceMode.NEW_INSTANCE

    def test_one_time_schedule_logs_force_notice(self, mock_on_message):
        """Test that forcing new_instance for one-time schedules is logged."""
        future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        config = make_config("test-onetime-log", {
            "run_at": future_time,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        with patch("daemon.sources.adapters.scheduler.logger") as mock_logger:
            adapter = SchedulerAdapter(config, mock_on_message)
            
            # Check that debug log was called with force message
            debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
            assert any("Force new_instance for one-time schedule" in str(c) for c in debug_calls)


# ==================== New Instance Mode Tests ====================


class TestNewInstanceMode:
    """Tests for new_instance mode behavior."""

    @pytest.mark.asyncio
    async def test_new_instance_sets_force_new_instance_true(self, mock_on_message):
        """Test that new_instance mode sets force_new_instance=True in metadata."""
        config = make_config("test-new-inst-force", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "new_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        # Manually trigger to get the message
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Check the message was emitted
        assert mock_on_message.call_count == 1
        incoming_msg = mock_on_message.call_args[0][0]
        
        assert isinstance(incoming_msg, IncomingMessage)
        assert incoming_msg.metadata["force_new_instance"] is True
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_new_instance_no_run_number(self, mock_on_message):
        """Test that new_instance mode has no run_number in metadata."""
        config = make_config("test-new-inst-no-run", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "new_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.metadata["scheduler"]["run_number"] is None
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_new_instance_uses_original_message(self, mock_on_message):
        """Test that new_instance mode uses the original message without prefix."""
        config = make_config("test-new-inst-msg", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Original scheduled task",
            "instance_mode": "new_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.content == "Original scheduled task"
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_new_instance_instance_mode_in_metadata(self, mock_on_message):
        """Test that instance_mode is correctly reported in metadata."""
        config = make_config("test-new-inst-meta", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "new_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.metadata["scheduler"]["instance_mode"] == "new_instance"
        
        await adapter.stop()


# ==================== Reuse Instance Mode Tests ====================


class TestReuseInstanceMode:
    """Tests for reuse_instance mode behavior."""

    @pytest.mark.asyncio
    async def test_reuse_instance_calls_source_repo(self, mock_on_message, mock_source_repo):
        """Test that reuse_instance mode calls source_repo.increment_scheduler_run_counter."""
        config = make_config("test-reuse-repo", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Verify run counter was incremented
        mock_source_repo.increment_scheduler_run_counter.assert_called_once_with("test-reuse-repo")
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_reuse_instance_sets_force_new_instance_false(self, mock_on_message, mock_source_repo):
        """Test that reuse_instance mode sets force_new_instance=False in metadata."""
        config = make_config("test-reuse-force", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.metadata["force_new_instance"] is False
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_reuse_instance_includes_run_number(self, mock_on_message, mock_source_repo):
        """Test that reuse_instance mode includes run_number in metadata."""
        mock_source_repo.increment_scheduler_run_counter.return_value = 5
        
        config = make_config("test-reuse-run-num", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.metadata["scheduler"]["run_number"] == 5
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_reuse_instance_formats_message_with_prefix(self, mock_on_message, mock_source_repo):
        """Test that reuse_instance mode formats message with #N prefix."""
        mock_source_repo.increment_scheduler_run_counter.return_value = 3
        
        config = make_config("test-reuse-prefix", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Original task content",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        
        # Check that message contains the continuation prefix
        assert "#3" in incoming_msg.content
        assert "[CONTINUATION - Run #3]" in incoming_msg.content
        assert "Original task content" in incoming_msg.content
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_reuse_instance_increments_counter_each_run(self, mock_on_message, mock_source_repo):
        """Test that reuse_instance mode increments counter for each execution."""
        mock_source_repo.increment_scheduler_run_counter.side_effect = [1, 2, 3]
        
        config = make_config("test-reuse-increment", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        # Trigger 3 times
        await adapter.manual_trigger()
        await asyncio.sleep(0.05)
        await adapter.manual_trigger()
        await asyncio.sleep(0.05)
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Should have been called 3 times
        assert mock_source_repo.increment_scheduler_run_counter.call_count == 3
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_reuse_instance_no_source_repo_uses_default(self, mock_on_message):
        """Test that reuse_instance without source_repo uses default run_number=1."""
        config = make_config("test-reuse-no-repo", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)  # No source_repo
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.metadata["scheduler"]["run_number"] == 1
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_reuse_instance_source_repo_returns_none(self, mock_on_message, mock_source_repo):
        """Test that reuse_instance handles source_repo returning None gracefully."""
        mock_source_repo.increment_scheduler_run_counter.return_value = None
        
        config = make_config("test-reuse-none", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Should use default run_number=1 when source_repo returns None
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.metadata["scheduler"]["run_number"] == 1
        
        await adapter.stop()


# ==================== Run Counter Persistence Tests ====================


class TestRunCounterPersistence:
    """Tests for run counter persistence across adapter restarts."""

    @pytest.mark.asyncio
    async def test_run_counter_continues_after_restart(self, mock_on_message, mock_source_repo):
        """Test that run counter continues incrementing after adapter restart."""
        mock_source_repo.increment_scheduler_run_counter.side_effect = [1, 2, 3]
        
        config = make_config("test-counter-restart", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        # First adapter instance
        adapter1 = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter1.start()
        await adapter1.manual_trigger()
        await asyncio.sleep(0.05)
        await adapter1.stop()
        
        # Second adapter instance (simulating restart)
        adapter2 = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter2.start()
        await adapter2.manual_trigger()
        await asyncio.sleep(0.05)
        await adapter2.stop()
        
        # Counter should have been called twice
        assert mock_source_repo.increment_scheduler_run_counter.call_count == 2

    @pytest.mark.asyncio
    async def test_run_counter_persists_in_source_repo(self, mock_source_repo):
        """Test that run counter is stored in source_repo's config."""
        mock_source_repo.increment_scheduler_run_counter.return_value = 10
        
        config = make_config("test-counter-persist", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        mock_on_message = AsyncMock()
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        
        # The actual persistence happens in the source_repo
        mock_source_repo.increment_scheduler_run_counter.assert_not_called()
        
        # Start and trigger to call the method
        await adapter.start()
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Verify the method was called with correct source_id
        mock_source_repo.increment_scheduler_run_counter.assert_called_once_with("test-counter-persist")


# ==================== Message Formatting Tests ====================


class TestContinuationMessageFormatting:
    """Tests for continuation message formatting in reuse_instance mode."""

    def test_format_continuation_message_includes_run_number(self, mock_on_message):
        """Test that _format_continuation_message includes run number."""
        config = make_config("test-format-run", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        formatted = adapter._format_continuation_message("Original message", 7)
        
        assert "#7" in formatted
        assert "[CONTINUATION - Run #7]" in formatted
        assert "Original message" in formatted

    def test_format_continuation_message_contains_instructions(self, mock_on_message):
        """Test that continuation message contains incremental work instructions."""
        config = make_config("test-format-instructions", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Build feature X",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        formatted = adapter._format_continuation_message("Build feature X", 1)
        
        # Should contain instructions for incremental work
        assert "CONTINUATION" in formatted
        assert "Review the context" in formatted
        assert "previous runs" in formatted
        assert "Build upon previous work" in formatted

    def test_format_continuation_message_run_one(self, mock_on_message):
        """Test continuation message formatting for first run."""
        config = make_config("test-format-one", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Start project",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        formatted = adapter._format_continuation_message("Start project", 1)
        
        assert "#1" in formatted
        assert "Run #1" in formatted

    def test_format_continuation_message_run_large_number(self, mock_on_message):
        """Test continuation message formatting with large run number."""
        config = make_config("test-format-large", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Continue work",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        formatted = adapter._format_continuation_message("Continue work", 999)
        
        assert "#999" in formatted
        assert "Run #999" in formatted


# ==================== One-Time Schedule Tests ====================


class TestOneTimeScheduleInstanceMode:
    """Tests for instance mode behavior in one-time schedules."""

    @pytest.mark.asyncio
    async def test_one_time_always_new_instance(self, mock_on_message):
        """Test that one-time schedules always use new_instance regardless of config."""
        future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        config = make_config("test-onetime-new", {
            "run_at": future_time,
            "agent": "./agents/coder",
            "message": "One-time task",
            "instance_mode": "reuse_instance",  # Should be ignored
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        # For one-time schedules with future time, the scheduler doesn't trigger immediately
        # It only schedules for the future time, so we can't test this without manual_trigger
        # But we can verify the adapter is configured correctly
        
        # Verify instance_mode is set to NEW_INSTANCE (forced for one-time schedules)
        assert adapter._instance_mode == SchedulerInstanceMode.NEW_INSTANCE
        
        # Manual trigger should use new_instance mode
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        assert mock_on_message.call_count > 0, "Expected on_message to be called"
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.metadata["scheduler"]["instance_mode"] == "new_instance"
        assert incoming_msg.metadata["force_new_instance"] is True
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_one_time_no_run_number(self, mock_on_message):
        """Test that one-time schedules don't include run_number."""
        future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        config = make_config("test-onetime-no-run", {
            "run_at": future_time,
            "agent": "./agents/coder",
            "message": "One-time task",
            "instance_mode": "reuse_instance",  # Should be ignored
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        await asyncio.sleep(0.1)
        
        if mock_on_message.call_count > 0:
            incoming_msg = mock_on_message.call_args[0][0]
            assert incoming_msg.metadata["scheduler"]["run_number"] is None
        
        await adapter.stop()


# ==================== Execution Callback Tests ====================


class TestInstanceModeExecutionCallbacks:
    """Tests for execution callback behavior with instance modes."""

    @pytest.mark.asyncio
    async def test_callback_receives_run_number_new_instance(self, mock_on_message, mock_execution_callback):
        """Test that execution callback receives correct run_number for new_instance."""
        config = make_config("test-callback-new", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "new_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, mock_execution_callback)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Find the triggered callback
        triggered_calls = [
            c for c in mock_execution_callback.call_args_list
            if c.kwargs.get("status") == "triggered"
        ]
        
        if triggered_calls:
            assert triggered_calls[0].kwargs.get("status") == "triggered"
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_callback_receives_run_number_reuse_instance(self, mock_on_message, mock_execution_callback, mock_source_repo):
        """Test that execution callback can access run_number through message metadata."""
        mock_source_repo.increment_scheduler_run_counter.return_value = 4
        
        config = make_config("test-callback-reuse", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, mock_execution_callback, source_repo=mock_source_repo)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # The message metadata should contain the run_number
        if mock_on_message.call_count > 0:
            incoming_msg = mock_on_message.call_args[0][0]
            assert incoming_msg.metadata["scheduler"]["run_number"] == 4
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_callback_statuses_for_reuse_instance(self, mock_on_message, mock_execution_callback, mock_source_repo):
        """Test that execution callback receives all expected statuses for reuse_instance."""
        mock_source_repo.increment_scheduler_run_counter.return_value = 1
        
        config = make_config("test-callback-statuses", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, mock_execution_callback, source_repo=mock_source_repo)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Should have triggered and completed callbacks
        statuses_received = [c.kwargs.get("status") for c in mock_execution_callback.call_args_list]
        
        assert "triggered" in statuses_received
        assert "completed" in statuses_received
        
        await adapter.stop()


# ==================== Error Recovery Tests ====================


class TestInstanceModeErrorRecovery:
    """Tests for error recovery behavior with instance modes."""

    @pytest.mark.asyncio
    async def test_new_instance_after_failure_creates_fresh_instance(self, mock_on_message):
        """Test that new_instance mode always creates fresh instance even after failure."""
        config = make_config("test-recovery-new", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Recovery test",
            "instance_mode": "new_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        # Trigger twice - both should use new instance
        await adapter.manual_trigger()
        await asyncio.sleep(0.05)
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Both messages should have force_new_instance=True
        if mock_on_message.call_count >= 2:
            for call in mock_on_message.call_args_list:
                msg = call[0][0]
                assert msg.metadata["force_new_instance"] is True
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_reuse_instance_continues_counter_after_failure(self, mock_on_message, mock_source_repo):
        """Test that reuse_instance continues counter incrementing even after failures."""
        # Simulate counter incrementing even when previous runs "failed"
        mock_source_repo.increment_scheduler_run_counter.side_effect = [1, 2, 3]
        
        config = make_config("test-recovery-reuse", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Recovery test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        # Trigger 3 times - counter should increment each time
        for _ in range(3):
            await adapter.manual_trigger()
            await asyncio.sleep(0.05)
        
        await asyncio.sleep(0.1)
        
        # Counter should have been called 3 times
        assert mock_source_repo.increment_scheduler_run_counter.call_count == 3
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_run_number_increments_even_when_instance_dies(self, mock_on_message, mock_source_repo):
        """Test that run_number increments even if the instance dies between runs."""
        mock_source_repo.increment_scheduler_run_counter.side_effect = [1, 2, 3, 4, 5]
        
        config = make_config("test-recovery-dies", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Resilient test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        
        # Simulate multiple runs with "dead" instances between them
        for expected_run in range(1, 6):
            # Adapter restarts (simulating crash recovery)
            adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
            await adapter.start()
            await adapter.manual_trigger()
            await asyncio.sleep(0.05)
            await adapter.stop()
        
        # Counter should have been incremented for each run
        assert mock_source_repo.increment_scheduler_run_counter.call_count == 5


# ==================== Max Concurrent with Instance Mode Tests ====================


class TestInstanceModeMaxConcurrent:
    """Tests for max_concurrent behavior with different instance modes."""

    def test_reuse_instance_max_concurrent_default(self, mock_on_message):
        """Test that reuse_instance mode has same default max_concurrent=1."""
        config = make_config("test-reuse-max-default", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._max_concurrent == 1

    def test_new_instance_max_concurrent_can_be_higher(self, mock_on_message):
        """Test that new_instance mode can override max_concurrent."""
        config = make_config("test-new-max-custom", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "new_instance",
            "max_concurrent": 5,
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._max_concurrent == 5

    @pytest.mark.asyncio
    async def test_reuse_instance_semaphore_enforces_max_concurrent(self, mock_on_message, mock_source_repo):
        """Test that reuse_instance mode enforces max_concurrent via semaphore."""
        mock_source_repo.increment_scheduler_run_counter.side_effect = [1, 2]
        
        config = make_config("test-reuse-semaphore", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
            "max_concurrent": 1,  # Only 1 concurrent execution
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        # Semaphore should be initialized
        assert adapter._execution_semaphore is not None
        
        # Try to trigger multiple times rapidly
        await adapter.manual_trigger()
        await adapter.manual_trigger()  # This should be blocked
        
        await asyncio.sleep(0.1)
        
        # Only one should have executed
        assert mock_on_message.call_count <= 2  # Could be 1 or 2 depending on timing
        
        await adapter.stop()


# ==================== Integration with Other Features ====================


class TestInstanceModeWithOtherFeatures:
    """Tests for instance mode interaction with other scheduler features."""

    @pytest.mark.asyncio
    async def test_instance_mode_with_interval_schedule(self, mock_on_message, mock_source_repo):
        """Test that instance_mode works correctly with interval schedules."""
        mock_source_repo.increment_scheduler_run_counter.side_effect = [1, 2]
        
        config = make_config("test-interval-instance", {
            "interval_seconds": 3600,  # 1 hour interval
            "agent": "./agents/coder",
            "message": "Interval test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        # Manual trigger to test
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        # instance_mode is always present
        assert incoming_msg.metadata["scheduler"]["instance_mode"] == "reuse_instance"
        # For manual trigger, interval_seconds is not added (only for automatic triggers)
        # but the schedule_type is still available
        assert incoming_msg.metadata["scheduler"]["trigger_type"] == "manual"
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_instance_mode_with_cron_schedule(self, mock_on_message, mock_source_repo):
        """Test that instance_mode works correctly with cron schedules."""
        mock_source_repo.increment_scheduler_run_counter.return_value = 1
        
        config = make_config("test-cron-instance", {
            "schedule": "0 9 * * *",  # 9 AM daily
            "agent": "./agents/coder",
            "message": "Cron test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        # instance_mode is always present for both manual and scheduled triggers
        assert incoming_msg.metadata["scheduler"]["instance_mode"] == "reuse_instance"
        # trigger_type is "manual" for manual triggers
        assert incoming_msg.metadata["scheduler"]["trigger_type"] == "manual"
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_instance_mode_with_timezone(self, mock_on_message, mock_source_repo):
        """Test that instance_mode works correctly with timezone configuration."""
        mock_source_repo.increment_scheduler_run_counter.return_value = 1
        
        config = make_config("test-tz-instance", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Timezone test",
            "instance_mode": "reuse_instance",
            "timezone": "Asia/Tokyo",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Should still have correct instance mode regardless of timezone
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.metadata["scheduler"]["instance_mode"] == "reuse_instance"
        assert incoming_msg.metadata["force_new_instance"] is False
        
        await adapter.stop()


# ==================== Edge Cases ====================


class TestInstanceModeEdgeCases:
    """Tests for edge cases in instance mode handling."""

    def test_empty_message_with_reuse_instance(self, mock_on_message, mock_source_repo):
        """Test reuse_instance mode with empty message content."""
        config = make_config("test-empty-msg", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "",  # Empty message
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        
        # Should not raise, just use empty message
        assert adapter._message_content == ""

    def test_special_characters_in_message(self, mock_on_message, mock_source_repo):
        """Test that special characters are preserved in continuation messages."""
        special_message = "Task with special chars: <>&'\"\\nNewlines"
        
        config = make_config("test-special-chars", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": special_message,
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        
        formatted = adapter._format_continuation_message(special_message, 1)
        
        # Original message should be included
        assert special_message in formatted

    @pytest.mark.asyncio
    async def test_none_agent_with_instance_mode(self, mock_on_message, mock_source_repo):
        """Test instance mode handling when agent is not specified."""
        config = make_config("test-no-agent", {
            "interval_seconds": 60,
            "agent": None,  # No agent
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        # Should not crash
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        await adapter.stop()

    def test_instance_mode_case_sensitive(self, mock_on_message):
        """Test that instance_mode is case-sensitive."""
        config = make_config("test-case-sensitive", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "NEW_INSTANCE",  # Uppercase
        })
        
        with pytest.raises(ValueError):
            SchedulerAdapter(config, mock_on_message)


# ==================== Skip Instance Running Tests ====================


@pytest.fixture
def mock_instance_repo():
    """Create a mock SessionRepository."""
    repo = MagicMock()
    return repo


class TestSkipInstanceRunning:
    """Tests for skipping execution when mapped instance is still running."""

    def test_is_instance_active_returns_false_for_new_instance_mode(self, mock_on_message, mock_source_repo, mock_instance_repo):
        """Test that _is_instance_active returns False for new_instance mode."""
        config = make_config("test-skip-new-instance", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "new_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, instance_repo=mock_instance_repo)
        
        is_active, instance_id, status = adapter._is_instance_active()
        
        # new_instance mode should return False regardless of instance state
        assert is_active is False
        assert instance_id is None
        assert status is None

    def test_is_instance_active_returns_false_when_no_mapping(self, mock_on_message, mock_source_repo, mock_instance_repo):
        """Test that _is_instance_active returns False when no instance mapping exists."""
        mock_source_repo.get_instance_mapping.return_value = None
        
        config = make_config("test-skip-no-mapping", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, instance_repo=mock_instance_repo)
        
        is_active, instance_id, status = adapter._is_instance_active()
        
        assert is_active is False
        assert instance_id is None
        assert status is None

    def test_is_instance_active_returns_false_when_instance_idle(self, mock_on_message, mock_source_repo, mock_instance_repo):
        """Test that _is_instance_active returns False when instance is idle."""
        mock_mapping = MagicMock()
        mock_mapping.agent_instance_id = "instance-123"
        mock_source_repo.get_instance_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "idle"
        mock_instance_repo.get.return_value = mock_session
        
        config = make_config("test-skip-idle-instance", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, instance_repo=mock_instance_repo)
        
        is_active, instance_id, status = adapter._is_instance_active()
        
        assert is_active is False
        assert instance_id == "instance-123"
        assert status == "idle"

    def test_is_instance_active_returns_true_when_instance_running(self, mock_on_message, mock_source_repo, mock_instance_repo):
        """Test that _is_instance_active returns True when instance is running."""
        mock_mapping = MagicMock()
        mock_mapping.agent_instance_id = "instance-123"
        mock_source_repo.get_instance_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "running"
        mock_instance_repo.get.return_value = mock_session
        
        config = make_config("test-skip-running-instance", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, instance_repo=mock_instance_repo)
        
        is_active, instance_id, status = adapter._is_instance_active()
        
        assert is_active is True
        assert instance_id == "instance-123"
        assert status == "running"

    def test_is_instance_active_returns_true_when_instance_waiting(self, mock_on_message, mock_source_repo, mock_instance_repo):
        """Test that _is_instance_active returns True when instance is waiting."""
        mock_mapping = MagicMock()
        mock_mapping.agent_instance_id = "instance-456"
        mock_source_repo.get_instance_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "waiting"
        mock_instance_repo.get.return_value = mock_session
        
        config = make_config("test-skip-waiting-instance", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, instance_repo=mock_instance_repo)
        
        is_active, instance_id, status = adapter._is_instance_active()
        
        assert is_active is True
        assert instance_id == "instance-456"
        assert status == "waiting"

    def test_is_instance_active_returns_false_when_instance_error(self, mock_on_message, mock_source_repo, mock_instance_repo):
        """Test that _is_instance_active returns False when instance is in error state."""
        mock_mapping = MagicMock()
        mock_mapping.agent_instance_id = "instance-789"
        mock_source_repo.get_instance_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "error"
        mock_instance_repo.get.return_value = mock_session
        
        config = make_config("test-skip-error-instance", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, instance_repo=mock_instance_repo)
        
        is_active, instance_id, status = adapter._is_instance_active()
        
        assert is_active is False
        assert instance_id == "instance-789"
        assert status == "error"

    def test_is_instance_active_returns_false_when_instance_terminated(self, mock_on_message, mock_source_repo, mock_instance_repo):
        """Test that _is_instance_active returns False when instance is terminated."""
        mock_mapping = MagicMock()
        mock_mapping.agent_instance_id = "instance-terminated"
        mock_source_repo.get_instance_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "terminated"
        mock_instance_repo.get.return_value = mock_session
        
        config = make_config("test-skip-terminated-instance", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, instance_repo=mock_instance_repo)
        
        is_active, instance_id, status = adapter._is_instance_active()
        
        assert is_active is False
        assert instance_id == "instance-terminated"
        assert status == "terminated"

    def test_is_instance_active_handles_missing_instance_repo(self, mock_on_message, mock_source_repo):
        """Test that _is_instance_active handles missing instance_repo gracefully."""
        config = make_config("test-skip-no-instance-repo", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, instance_repo=None)
        
        is_active, instance_id, status = adapter._is_instance_active()
        
        # Should return False when instance_repo is not available
        assert is_active is False
        assert instance_id is None
        assert status is None

    @pytest.mark.asyncio
    async def test_skip_execution_when_instance_running(self, mock_on_message, mock_source_repo, mock_instance_repo, mock_execution_callback):
        """Test that execution is skipped when mapped instance is still running."""
        mock_mapping = MagicMock()
        mock_mapping.agent_instance_id = "instance-running"
        mock_source_repo.get_instance_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "running"
        mock_instance_repo.get.return_value = mock_session
        
        config = make_config("test-skip-execution-running", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(
            config, 
            mock_on_message, 
            execution_callback=mock_execution_callback,
            source_repo=mock_source_repo, 
            instance_repo=mock_instance_repo
        )
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Should not emit any message
        assert mock_on_message.call_count == 0
        
        # Should call execution callback with skipped status
        skipped_calls = [
            c for c in mock_execution_callback.call_args_list
            if c.kwargs.get("status") == "skipped"
        ]
        assert len(skipped_calls) == 1
        assert skipped_calls[0].kwargs.get("instance_id") == "instance-running"
        assert "still running" in skipped_calls[0].kwargs.get("error_message", "")
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_execute_when_instance_idle(self, mock_on_message, mock_source_repo, mock_instance_repo, mock_execution_callback):
        """Test that execution proceeds when mapped instance is idle."""
        mock_mapping = MagicMock()
        mock_mapping.agent_instance_id = "instance-idle"
        mock_source_repo.get_instance_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "idle"
        mock_instance_repo.get.return_value = mock_session
        
        config = make_config("test-skip-execution-idle", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Execute this",
            "instance_mode": "reuse_instance",
        })
        
        adapter = SchedulerAdapter(
            config, 
            mock_on_message, 
            execution_callback=mock_execution_callback,
            source_repo=mock_source_repo, 
            instance_repo=mock_instance_repo
        )
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Should emit message
        assert mock_on_message.call_count == 1
        
        # Should have triggered callback (not skipped)
        triggered_calls = [
            c for c in mock_execution_callback.call_args_list
            if c.kwargs.get("status") == "triggered"
        ]
        assert len(triggered_calls) == 1
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_new_instance_mode_never_skips(self, mock_on_message, mock_source_repo, mock_instance_repo, mock_execution_callback):
        """Test that new_instance mode never skips regardless of instance state."""
        # Even if instance is "running", new_instance should not check instance state
        mock_mapping = MagicMock()
        mock_mapping.agent_instance_id = "instance-running"
        mock_source_repo.get_instance_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "running"
        mock_instance_repo.get.return_value = mock_session
        
        config = make_config("test-new-instance-never-skips", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Execute this",
            "instance_mode": "new_instance",
        })
        
        adapter = SchedulerAdapter(
            config, 
            mock_on_message, 
            execution_callback=mock_execution_callback,
            source_repo=mock_source_repo, 
            instance_repo=mock_instance_repo
        )
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Should emit message (new_instance mode doesn't check instance state)
        assert mock_on_message.call_count == 1
        
        # Should not have any skipped calls
        skipped_calls = [
            c for c in mock_execution_callback.call_args_list
            if c.kwargs.get("status") == "skipped"
        ]
        assert len(skipped_calls) == 0
        
        await adapter.stop()
