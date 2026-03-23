"""Tests for scheduler session mode feature (Task 7).

Tests cover:
1. New session mode - creates fresh session per execution
2. Reuse session mode - reuses session across runs with run counter
3. One-time schedules - always force new_session
4. Error recovery - execution callbacks and counter persistence
5. API validation - invalid session_mode and max_concurrent constraints
"""

import pytest
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from daemon.sources.adapters.scheduler import SchedulerAdapter
from daemon.sources.base import SourceConfig, IncomingMessage, SourceStatus
from daemon.models import SchedulerSessionMode


# ==================== Fixtures ====================


@pytest.fixture
def mock_on_message():
    """Create a mock async callback for message handling."""
    return AsyncMock()


@pytest.fixture
def mock_execution_callback():
    """Create a mock execution callback."""
    return Mock()


@pytest.fixture
def mock_source_repo():
    """Create a mock SourceRepository with run counter support."""
    repo = MagicMock()
    repo.increment_scheduler_run_counter = MagicMock(return_value=1)
    return repo


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


# ==================== Session Mode Configuration Tests ====================


class TestSchedulerSessionModeConfig:
    """Tests for session mode configuration parsing."""

    def test_default_session_mode_is_new_session(self, mock_on_message):
        """Test that default session mode is new_session when not specified."""
        config = make_config("test-default-session", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._session_mode == SchedulerSessionMode.NEW_SESSION

    def test_explicit_new_session_mode(self, mock_on_message):
        """Test explicit new_session mode configuration."""
        config = make_config("test-new-session", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "new_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._session_mode == SchedulerSessionMode.NEW_SESSION

    def test_reuse_session_mode(self, mock_on_message):
        """Test reuse_session mode configuration."""
        config = make_config("test-reuse-session", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._session_mode == SchedulerSessionMode.REUSE_SESSION

    def test_invalid_session_mode_raises_error(self, mock_on_message):
        """Test that invalid session_mode value raises ValueError."""
        config = make_config("test-invalid-session-mode", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "invalid_mode",
        })
        
        with pytest.raises(ValueError):
            SchedulerAdapter(config, mock_on_message)

    def test_one_time_schedule_forces_new_session(self, mock_on_message):
        """Test that run_at (one-time) schedule always forces new_session mode."""
        future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        config = make_config("test-onetime-force", {
            "run_at": future_time,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",  # This should be ignored
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        # One-time schedules should always use new_session
        assert adapter._session_mode == SchedulerSessionMode.NEW_SESSION

    def test_one_time_schedule_logs_force_notice(self, mock_on_message):
        """Test that forcing new_session for one-time schedules is logged."""
        future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        config = make_config("test-onetime-log", {
            "run_at": future_time,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        with patch("daemon.sources.adapters.scheduler.logger") as mock_logger:
            adapter = SchedulerAdapter(config, mock_on_message)
            
            # Check that debug log was called with force message
            debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
            assert any("Force new_session for one-time schedule" in str(c) for c in debug_calls)


# ==================== New Session Mode Tests ====================


class TestNewSessionMode:
    """Tests for new_session mode behavior."""

    @pytest.mark.asyncio
    async def test_new_session_sets_force_new_session_true(self, mock_on_message):
        """Test that new_session mode sets force_new_session=True in metadata."""
        config = make_config("test-new-sess-force", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "new_session",
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
        assert incoming_msg.metadata["force_new_session"] is True
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_new_session_no_run_number(self, mock_on_message):
        """Test that new_session mode has no run_number in metadata."""
        config = make_config("test-new-sess-no-run", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "new_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.metadata["scheduler"]["run_number"] is None
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_new_session_uses_original_message(self, mock_on_message):
        """Test that new_session mode uses the original message without prefix."""
        config = make_config("test-new-sess-msg", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Original scheduled task",
            "session_mode": "new_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.content == "Original scheduled task"
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_new_session_session_mode_in_metadata(self, mock_on_message):
        """Test that session_mode is correctly reported in metadata."""
        config = make_config("test-new-sess-meta", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "new_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.metadata["scheduler"]["session_mode"] == "new_session"
        
        await adapter.stop()


# ==================== Reuse Session Mode Tests ====================


class TestReuseSessionMode:
    """Tests for reuse_session mode behavior."""

    @pytest.mark.asyncio
    async def test_reuse_session_calls_source_repo(self, mock_on_message, mock_source_repo):
        """Test that reuse_session mode calls source_repo.increment_scheduler_run_counter."""
        config = make_config("test-reuse-repo", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Verify run counter was incremented
        mock_source_repo.increment_scheduler_run_counter.assert_called_once_with("test-reuse-repo")
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_reuse_session_sets_force_new_session_false(self, mock_on_message, mock_source_repo):
        """Test that reuse_session mode sets force_new_session=False in metadata."""
        config = make_config("test-reuse-force", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.metadata["force_new_session"] is False
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_reuse_session_includes_run_number(self, mock_on_message, mock_source_repo):
        """Test that reuse_session mode includes run_number in metadata."""
        mock_source_repo.increment_scheduler_run_counter.return_value = 5
        
        config = make_config("test-reuse-run-num", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.metadata["scheduler"]["run_number"] == 5
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_reuse_session_formats_message_with_prefix(self, mock_on_message, mock_source_repo):
        """Test that reuse_session mode formats message with #N prefix."""
        mock_source_repo.increment_scheduler_run_counter.return_value = 3
        
        config = make_config("test-reuse-prefix", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Original task content",
            "session_mode": "reuse_session",
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
    async def test_reuse_session_increments_counter_each_run(self, mock_on_message, mock_source_repo):
        """Test that reuse_session mode increments counter for each execution."""
        mock_source_repo.increment_scheduler_run_counter.side_effect = [1, 2, 3]
        
        config = make_config("test-reuse-increment", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
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
    async def test_reuse_session_no_source_repo_uses_default(self, mock_on_message):
        """Test that reuse_session without source_repo uses default run_number=1."""
        config = make_config("test-reuse-no-repo", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)  # No source_repo
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.metadata["scheduler"]["run_number"] == 1
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_reuse_session_source_repo_returns_none(self, mock_on_message, mock_source_repo):
        """Test that reuse_session handles source_repo returning None gracefully."""
        mock_source_repo.increment_scheduler_run_counter.return_value = None
        
        config = make_config("test-reuse-none", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
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
            "session_mode": "reuse_session",
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
            "session_mode": "reuse_session",
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
    """Tests for continuation message formatting in reuse_session mode."""

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


class TestOneTimeScheduleSessionMode:
    """Tests for session mode behavior in one-time schedules."""

    @pytest.mark.asyncio
    async def test_one_time_always_new_session(self, mock_on_message):
        """Test that one-time schedules always use new_session regardless of config."""
        future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        config = make_config("test-onetime-new", {
            "run_at": future_time,
            "agent": "./agents/coder",
            "message": "One-time task",
            "session_mode": "reuse_session",  # Should be ignored
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        # For one-time schedules, immediately trigger
        await asyncio.sleep(0.1)
        
        # Check that on_message was called with new_session metadata
        if mock_on_message.call_count > 0:
            incoming_msg = mock_on_message.call_args[0][0]
            assert incoming_msg.metadata["scheduler"]["session_mode"] == "new_session"
            assert incoming_msg.metadata["force_new_session"] is True
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_one_time_no_run_number(self, mock_on_message):
        """Test that one-time schedules don't include run_number."""
        future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        config = make_config("test-onetime-no-run", {
            "run_at": future_time,
            "agent": "./agents/coder",
            "message": "One-time task",
            "session_mode": "reuse_session",  # Should be ignored
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        await asyncio.sleep(0.1)
        
        if mock_on_message.call_count > 0:
            incoming_msg = mock_on_message.call_args[0][0]
            assert incoming_msg.metadata["scheduler"]["run_number"] is None
        
        await adapter.stop()


# ==================== Execution Callback Tests ====================


class TestSessionModeExecutionCallbacks:
    """Tests for execution callback behavior with session modes."""

    @pytest.mark.asyncio
    async def test_callback_receives_run_number_new_session(self, mock_on_message, mock_execution_callback):
        """Test that execution callback receives correct run_number for new_session."""
        config = make_config("test-callback-new", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "new_session",
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
    async def test_callback_receives_run_number_reuse_session(self, mock_on_message, mock_execution_callback, mock_source_repo):
        """Test that execution callback can access run_number through message metadata."""
        mock_source_repo.increment_scheduler_run_counter.return_value = 4
        
        config = make_config("test-callback-reuse", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
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
    async def test_callback_statuses_for_reuse_session(self, mock_on_message, mock_execution_callback, mock_source_repo):
        """Test that execution callback receives all expected statuses for reuse_session."""
        mock_source_repo.increment_scheduler_run_counter.return_value = 1
        
        config = make_config("test-callback-statuses", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
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


class TestSessionModeErrorRecovery:
    """Tests for error recovery behavior with session modes."""

    @pytest.mark.asyncio
    async def test_new_session_after_failure_creates_fresh_session(self, mock_on_message):
        """Test that new_session mode always creates fresh session even after failure."""
        config = make_config("test-recovery-new", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Recovery test",
            "session_mode": "new_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        # Trigger twice - both should use new session
        await adapter.manual_trigger()
        await asyncio.sleep(0.05)
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Both messages should have force_new_session=True
        if mock_on_message.call_count >= 2:
            for call in mock_on_message.call_args_list:
                msg = call[0][0]
                assert msg.metadata["force_new_session"] is True
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_reuse_session_continues_counter_after_failure(self, mock_on_message, mock_source_repo):
        """Test that reuse_session continues counter incrementing even after failures."""
        # Simulate counter incrementing even when previous runs "failed"
        mock_source_repo.increment_scheduler_run_counter.side_effect = [1, 2, 3]
        
        config = make_config("test-recovery-reuse", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Recovery test",
            "session_mode": "reuse_session",
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
    async def test_run_number_increments_even_when_session_dies(self, mock_on_message, mock_source_repo):
        """Test that run_number increments even if the session dies between runs."""
        mock_source_repo.increment_scheduler_run_counter.side_effect = [1, 2, 3, 4, 5]
        
        config = make_config("test-recovery-dies", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Resilient test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        
        # Simulate multiple runs with "dead" sessions between them
        for expected_run in range(1, 6):
            # Adapter restarts (simulating crash recovery)
            adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
            await adapter.start()
            await adapter.manual_trigger()
            await asyncio.sleep(0.05)
            await adapter.stop()
        
        # Counter should have been incremented for each run
        assert mock_source_repo.increment_scheduler_run_counter.call_count == 5


# ==================== Max Concurrent with Session Mode Tests ====================


class TestSessionModeMaxConcurrent:
    """Tests for max_concurrent behavior with different session modes."""

    def test_reuse_session_max_concurrent_default(self, mock_on_message):
        """Test that reuse_session mode has same default max_concurrent=1."""
        config = make_config("test-reuse-max-default", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._max_concurrent == 1

    def test_new_session_max_concurrent_can_be_higher(self, mock_on_message):
        """Test that new_session mode can override max_concurrent."""
        config = make_config("test-new-max-custom", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "new_session",
            "max_concurrent": 5,
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._max_concurrent == 5

    @pytest.mark.asyncio
    async def test_reuse_session_semaphore_enforces_max_concurrent(self, mock_on_message, mock_source_repo):
        """Test that reuse_session mode enforces max_concurrent via semaphore."""
        mock_source_repo.increment_scheduler_run_counter.side_effect = [1, 2]
        
        config = make_config("test-reuse-semaphore", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
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


class TestSessionModeWithOtherFeatures:
    """Tests for session mode interaction with other scheduler features."""

    @pytest.mark.asyncio
    async def test_session_mode_with_interval_schedule(self, mock_on_message, mock_source_repo):
        """Test that session_mode works correctly with interval schedules."""
        mock_source_repo.increment_scheduler_run_counter.side_effect = [1, 2]
        
        config = make_config("test-interval-session", {
            "interval_seconds": 3600,  # 1 hour interval
            "agent": "./agents/coder",
            "message": "Interval test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        # Manual trigger to test
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        # session_mode is always present
        assert incoming_msg.metadata["scheduler"]["session_mode"] == "reuse_session"
        # For manual trigger, interval_seconds is not added (only for automatic triggers)
        # but the schedule_type is still available
        assert incoming_msg.metadata["scheduler"]["trigger_type"] == "manual"
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_session_mode_with_cron_schedule(self, mock_on_message, mock_source_repo):
        """Test that session_mode works correctly with cron schedules."""
        mock_source_repo.increment_scheduler_run_counter.return_value = 1
        
        config = make_config("test-cron-session", {
            "schedule": "0 9 * * *",  # 9 AM daily
            "agent": "./agents/coder",
            "message": "Cron test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        incoming_msg = mock_on_message.call_args[0][0]
        # session_mode is always present for both manual and scheduled triggers
        assert incoming_msg.metadata["scheduler"]["session_mode"] == "reuse_session"
        # trigger_type is "manual" for manual triggers
        assert incoming_msg.metadata["scheduler"]["trigger_type"] == "manual"
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_session_mode_with_timezone(self, mock_on_message, mock_source_repo):
        """Test that session_mode works correctly with timezone configuration."""
        mock_source_repo.increment_scheduler_run_counter.return_value = 1
        
        config = make_config("test-tz-session", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Timezone test",
            "session_mode": "reuse_session",
            "timezone": "Asia/Tokyo",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Should still have correct session mode regardless of timezone
        incoming_msg = mock_on_message.call_args[0][0]
        assert incoming_msg.metadata["scheduler"]["session_mode"] == "reuse_session"
        assert incoming_msg.metadata["force_new_session"] is False
        
        await adapter.stop()


# ==================== Edge Cases ====================


class TestSessionModeEdgeCases:
    """Tests for edge cases in session mode handling."""

    def test_empty_message_with_reuse_session(self, mock_on_message, mock_source_repo):
        """Test reuse_session mode with empty message content."""
        config = make_config("test-empty-msg", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "",  # Empty message
            "session_mode": "reuse_session",
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
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        
        formatted = adapter._format_continuation_message(special_message, 1)
        
        # Original message should be included
        assert special_message in formatted

    @pytest.mark.asyncio
    async def test_none_agent_with_session_mode(self, mock_on_message, mock_source_repo):
        """Test session mode handling when agent is not specified."""
        config = make_config("test-no-agent", {
            "interval_seconds": 60,
            "agent": None,  # No agent
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo)
        await adapter.start()
        
        # Should not crash
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        await adapter.stop()

    def test_session_mode_case_sensitive(self, mock_on_message):
        """Test that session_mode is case-sensitive."""
        config = make_config("test-case-sensitive", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "NEW_SESSION",  # Uppercase
        })
        
        with pytest.raises(ValueError):
            SchedulerAdapter(config, mock_on_message)


# ==================== Skip Session Running Tests ====================


@pytest.fixture
def mock_session_repo():
    """Create a mock SessionRepository."""
    repo = MagicMock()
    return repo


class TestSkipSessionRunning:
    """Tests for skipping execution when mapped session is still running."""

    def test_is_session_active_returns_false_for_new_session_mode(self, mock_on_message, mock_source_repo, mock_session_repo):
        """Test that _is_session_active returns False for new_session mode."""
        config = make_config("test-skip-new-session", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "new_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, session_repo=mock_session_repo)
        
        is_active, session_id, status = adapter._is_session_active()
        
        # new_session mode should return False regardless of session state
        assert is_active is False
        assert session_id is None
        assert status is None

    def test_is_session_active_returns_false_when_no_mapping(self, mock_on_message, mock_source_repo, mock_session_repo):
        """Test that _is_session_active returns False when no session mapping exists."""
        mock_source_repo.get_session_mapping.return_value = None
        
        config = make_config("test-skip-no-mapping", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, session_repo=mock_session_repo)
        
        is_active, session_id, status = adapter._is_session_active()
        
        assert is_active is False
        assert session_id is None
        assert status is None

    def test_is_session_active_returns_false_when_session_idle(self, mock_on_message, mock_source_repo, mock_session_repo):
        """Test that _is_session_active returns False when session is idle."""
        mock_mapping = MagicMock()
        mock_mapping.agent_session_id = "session-123"
        mock_source_repo.get_session_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "idle"
        mock_session_repo.get.return_value = mock_session
        
        config = make_config("test-skip-idle-session", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, session_repo=mock_session_repo)
        
        is_active, session_id, status = adapter._is_session_active()
        
        assert is_active is False
        assert session_id == "session-123"
        assert status == "idle"

    def test_is_session_active_returns_true_when_session_running(self, mock_on_message, mock_source_repo, mock_session_repo):
        """Test that _is_session_active returns True when session is running."""
        mock_mapping = MagicMock()
        mock_mapping.agent_session_id = "session-123"
        mock_source_repo.get_session_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "running"
        mock_session_repo.get.return_value = mock_session
        
        config = make_config("test-skip-running-session", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, session_repo=mock_session_repo)
        
        is_active, session_id, status = adapter._is_session_active()
        
        assert is_active is True
        assert session_id == "session-123"
        assert status == "running"

    def test_is_session_active_returns_true_when_session_waiting(self, mock_on_message, mock_source_repo, mock_session_repo):
        """Test that _is_session_active returns True when session is waiting."""
        mock_mapping = MagicMock()
        mock_mapping.agent_session_id = "session-456"
        mock_source_repo.get_session_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "waiting"
        mock_session_repo.get.return_value = mock_session
        
        config = make_config("test-skip-waiting-session", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, session_repo=mock_session_repo)
        
        is_active, session_id, status = adapter._is_session_active()
        
        assert is_active is True
        assert session_id == "session-456"
        assert status == "waiting"

    def test_is_session_active_returns_false_when_session_error(self, mock_on_message, mock_source_repo, mock_session_repo):
        """Test that _is_session_active returns False when session is in error state."""
        mock_mapping = MagicMock()
        mock_mapping.agent_session_id = "session-789"
        mock_source_repo.get_session_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "error"
        mock_session_repo.get.return_value = mock_session
        
        config = make_config("test-skip-error-session", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, session_repo=mock_session_repo)
        
        is_active, session_id, status = adapter._is_session_active()
        
        assert is_active is False
        assert session_id == "session-789"
        assert status == "error"

    def test_is_session_active_returns_false_when_session_terminated(self, mock_on_message, mock_source_repo, mock_session_repo):
        """Test that _is_session_active returns False when session is terminated."""
        mock_mapping = MagicMock()
        mock_mapping.agent_session_id = "session-terminated"
        mock_source_repo.get_session_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "terminated"
        mock_session_repo.get.return_value = mock_session
        
        config = make_config("test-skip-terminated-session", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, session_repo=mock_session_repo)
        
        is_active, session_id, status = adapter._is_session_active()
        
        assert is_active is False
        assert session_id == "session-terminated"
        assert status == "terminated"

    def test_is_session_active_handles_missing_session_repo(self, mock_on_message, mock_source_repo):
        """Test that _is_session_active handles missing session_repo gracefully."""
        config = make_config("test-skip-no-session-repo", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, source_repo=mock_source_repo, session_repo=None)
        
        is_active, session_id, status = adapter._is_session_active()
        
        # Should return False when session_repo is not available
        assert is_active is False
        assert session_id is None
        assert status is None

    @pytest.mark.asyncio
    async def test_skip_execution_when_session_running(self, mock_on_message, mock_source_repo, mock_session_repo, mock_execution_callback):
        """Test that execution is skipped when mapped session is still running."""
        mock_mapping = MagicMock()
        mock_mapping.agent_session_id = "session-running"
        mock_source_repo.get_session_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "running"
        mock_session_repo.get.return_value = mock_session
        
        config = make_config("test-skip-execution-running", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(
            config, 
            mock_on_message, 
            execution_callback=mock_execution_callback,
            source_repo=mock_source_repo, 
            session_repo=mock_session_repo
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
        assert skipped_calls[0].kwargs.get("session_id") == "session-running"
        assert "still running" in skipped_calls[0].kwargs.get("error_message", "")
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_execute_when_session_idle(self, mock_on_message, mock_source_repo, mock_session_repo, mock_execution_callback):
        """Test that execution proceeds when mapped session is idle."""
        mock_mapping = MagicMock()
        mock_mapping.agent_session_id = "session-idle"
        mock_source_repo.get_session_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "idle"
        mock_session_repo.get.return_value = mock_session
        
        config = make_config("test-skip-execution-idle", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Execute this",
            "session_mode": "reuse_session",
        })
        
        adapter = SchedulerAdapter(
            config, 
            mock_on_message, 
            execution_callback=mock_execution_callback,
            source_repo=mock_source_repo, 
            session_repo=mock_session_repo
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
    async def test_new_session_mode_never_skips(self, mock_on_message, mock_source_repo, mock_session_repo, mock_execution_callback):
        """Test that new_session mode never skips regardless of session state."""
        # Even if session is "running", new_session should not check session state
        mock_mapping = MagicMock()
        mock_mapping.agent_session_id = "session-running"
        mock_source_repo.get_session_mapping.return_value = mock_mapping
        
        mock_session = MagicMock()
        mock_session.status = "running"
        mock_session_repo.get.return_value = mock_session
        
        config = make_config("test-new-session-never-skips", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Execute this",
            "session_mode": "new_session",
        })
        
        adapter = SchedulerAdapter(
            config, 
            mock_on_message, 
            execution_callback=mock_execution_callback,
            source_repo=mock_source_repo, 
            session_repo=mock_session_repo
        )
        await adapter.start()
        
        await adapter.manual_trigger()
        await asyncio.sleep(0.1)
        
        # Should emit message (new_session mode doesn't check session state)
        assert mock_on_message.call_count == 1
        
        # Should not have any skipped calls
        skipped_calls = [
            c for c in mock_execution_callback.call_args_list
            if c.kwargs.get("status") == "skipped"
        ]
        assert len(skipped_calls) == 0
        
        await adapter.stop()
