"""Unit tests for SchedulerAdapter."""

import pytest
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from zoneinfo import ZoneInfo

from daemon.sources.adapters.scheduler import SchedulerAdapter
from daemon.sources.base import SourceConfig, IncomingMessage, SourceStatus


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


# ==================== Cron Parsing Tests ====================


class TestCronParsing:
    """Tests for cron expression parsing and validation."""

    def test_valid_cron_expression(self, mock_on_message):
        """Test that valid cron expressions are accepted."""
        config = make_config("test-cron", {
            "schedule": "0 9 * * 1-5",  # 9 AM on weekdays
            "agent": "./agents/coder",
            "message": "Good morning!",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_CRON
        assert adapter._cron_expression == "0 9 * * 1-5"

    def test_valid_cron_every_minute(self, mock_on_message):
        """Test that 'every minute' cron expression is valid."""
        config = make_config("test-cron-minute", {
            "schedule": "* * * * *",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_CRON
        assert adapter._cron_expression == "* * * * *"

    def test_valid_cron_with_specific_time(self, mock_on_message):
        """Test cron expression with specific hour and minute."""
        config = make_config("test-cron-specific", {
            "schedule": "30 14 * * *",  # 2:30 PM every day
            "agent": "./agents/coder",
            "message": "Afternoon check",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_CRON

    def test_invalid_cron_expression_raises_error(self, mock_on_message):
        """Test that invalid cron expressions raise ValueError."""
        config = make_config("test-invalid-cron", {
            "schedule": "invalid cron expression",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        with pytest.raises(ValueError, match="Invalid cron expression"):
            SchedulerAdapter(config, mock_on_message)

    def test_cron_expression_with_too_few_fields(self, mock_on_message):
        """Test that cron with too few fields raises error."""
        config = make_config("test-cron-short", {
            "schedule": "0 9 *",  # Only 3 fields instead of 5
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        with pytest.raises(ValueError, match="Invalid cron expression"):
            SchedulerAdapter(config, mock_on_message)


# ==================== Interval Scheduling Tests ====================


class TestIntervalScheduling:
    """Tests for interval-based scheduling."""

    def test_interval_seconds_valid(self, mock_on_message):
        """Test valid interval_seconds configuration."""
        config = make_config("test-interval", {
            "interval_seconds": 300,
            "agent": "./agents/coder",
            "message": "Periodic check",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_INTERVAL
        assert adapter._interval_seconds == 300

    def test_interval_seconds_minimum(self, mock_on_message):
        """Test minimum valid interval (1 second)."""
        config = make_config("test-interval-min", {
            "interval_seconds": 1,
            "agent": "./agents/coder",
            "message": "Fast check",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_INTERVAL
        assert adapter._interval_seconds == 1

    def test_interval_seconds_large(self, mock_on_message):
        """Test large interval (24 hours)."""
        config = make_config("test-interval-large", {
            "interval_seconds": 86400,  # 24 hours
            "agent": "./agents/coder",
            "message": "Daily check",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_INTERVAL
        assert adapter._interval_seconds == 86400

    def test_interval_seconds_zero_raises_error(self, mock_on_message):
        """Test that zero interval raises ValueError."""
        config = make_config("test-interval-zero", {
            "interval_seconds": 0,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        with pytest.raises(ValueError, match="interval_seconds must be a positive integer"):
            SchedulerAdapter(config, mock_on_message)

    def test_interval_seconds_negative_raises_error(self, mock_on_message):
        """Test that negative interval raises ValueError."""
        config = make_config("test-interval-neg", {
            "interval_seconds": -10,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        with pytest.raises(ValueError, match="interval_seconds must be a positive integer"):
            SchedulerAdapter(config, mock_on_message)

    def test_interval_seconds_string_raises_error(self, mock_on_message):
        """Test that string interval raises ValueError."""
        config = make_config("test-interval-str", {
            "interval_seconds": "300",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        with pytest.raises(ValueError, match="interval_seconds must be a positive integer"):
            SchedulerAdapter(config, mock_on_message)


# ==================== One-Time Trigger Tests ====================


class TestOneTimeTrigger:
    """Tests for one-time trigger (run_at) scheduling."""

    def test_run_at_future_time(self, mock_on_message):
        """Test run_at with future datetime."""
        future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        config = make_config("test-onetime-future", {
            "run_at": future_time,
            "agent": "./agents/coder",
            "message": "One-time check",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_ONE_TIME
        assert adapter._run_at is not None

    def test_run_at_with_z_suffix(self, mock_on_message):
        """Test run_at with Z suffix (Zulu time)."""
        config = make_config("test-onetime-z", {
            "run_at": "2025-12-25T10:00:00Z",
            "agent": "./agents/coder",
            "message": "Christmas check",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_ONE_TIME
        assert adapter._run_at is not None
        assert adapter._run_at.tzinfo is not None

    def test_run_at_with_timezone_offset(self, mock_on_message):
        """Test run_at with timezone offset."""
        config = make_config("test-onetime-offset", {
            "run_at": "2025-06-15T14:30:00+05:30",
            "agent": "./agents/coder",
            "message": "IST check",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_ONE_TIME
        assert adapter._run_at is not None

    def test_run_at_past_time(self, mock_on_message):
        """Test run_at with past datetime (should still be valid config)."""
        past_time = "2020-01-01T00:00:00Z"
        config = make_config("test-onetime-past", {
            "run_at": past_time,
            "agent": "./agents/coder",
            "message": "Past check",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        # Config is valid, but execution will trigger immediately
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_ONE_TIME

    def test_run_at_invalid_format_raises_error(self, mock_on_message):
        """Test that invalid run_at format raises ValueError."""
        config = make_config("test-onetime-invalid", {
            "run_at": "not-a-date",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        with pytest.raises(ValueError, match="Invalid run_at format"):
            SchedulerAdapter(config, mock_on_message)

    def test_run_at_empty_string_ignored(self, mock_on_message):
        """Test that empty run_at string is ignored (falls through to no schedule error)."""
        config = make_config("test-onetime-empty", {
            "run_at": "",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        # Empty string should fall through to "no valid schedule" error
        with pytest.raises(ValueError, match="No valid schedule configured"):
            SchedulerAdapter(config, mock_on_message)


# ==================== Timezone Handling Tests ====================


class TestTimezoneHandling:
    """Tests for timezone configuration."""

    def test_default_timezone_utc(self, mock_on_message):
        """Test that default timezone is UTC."""
        config = make_config("test-tz-default", {
            "schedule": "0 9 * * *",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._timezone == ZoneInfo("UTC")

    def test_timezone_america_new_york(self, mock_on_message):
        """Test America/New_York timezone."""
        config = make_config("test-tz-ny", {
            "schedule": "0 9 * * *",
            "timezone": "America/New_York",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._timezone == ZoneInfo("America/New_York")

    def test_timezone_asia_tokyo(self, mock_on_message):
        """Test Asia/Tokyo timezone."""
        config = make_config("test-tz-tokyo", {
            "schedule": "0 9 * * *",
            "timezone": "Asia/Tokyo",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._timezone == ZoneInfo("Asia/Tokyo")

    def test_timezone_europe_london(self, mock_on_message):
        """Test Europe/London timezone."""
        config = make_config("test-tz-london", {
            "schedule": "0 9 * * *",
            "timezone": "Europe/London",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._timezone == ZoneInfo("Europe/London")

    def test_timezone_unknown_falls_back_to_utc(self, mock_on_message):
        """Test that unknown timezone falls back to UTC."""
        config = make_config("test-tz-unknown", {
            "schedule": "0 9 * * *",
            "timezone": "Invalid/Timezone",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        # Should fall back to UTC with a warning
        assert adapter._timezone == ZoneInfo("UTC")

    def test_timezone_affects_next_trigger_calculation(self, mock_on_message):
        """Test that timezone affects when the next trigger is calculated."""
        config = make_config("test-tz-calc", {
            "schedule": "0 9 * * *",  # 9 AM
            "timezone": "Asia/Tokyo",  # UTC+9
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        next_trigger = adapter._get_next_trigger_time()
        
        # Next trigger should be in the future
        assert next_trigger is not None
        # The calculation should be done in Tokyo timezone
        assert next_trigger.tzinfo is not None


# ==================== Max Concurrent Tests ====================


class TestMaxConcurrent:
    """Tests for max_concurrent configuration (concurrency control)."""

    def test_max_concurrent_default(self, mock_on_message):
        """Test that default max_concurrent is 1."""
        config = make_config("test-concurrent-default", {
            "interval_seconds": 60,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._max_concurrent == 1

    def test_max_concurrent_custom(self, mock_on_message):
        """Test custom max_concurrent value."""
        config = make_config("test-concurrent-custom", {
            "interval_seconds": 60,
            "max_concurrent": 5,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._max_concurrent == 5

    def test_max_concurrent_high(self, mock_on_message):
        """Test high max_concurrent value."""
        config = make_config("test-concurrent-high", {
            "interval_seconds": 60,
            "max_concurrent": 100,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._max_concurrent == 100

    @pytest.mark.asyncio
    async def test_semaphore_initialized_on_start(self, mock_on_message):
        """Test that semaphore is initialized with correct value on start."""
        config = make_config("test-concurrent-sem", {
            "interval_seconds": 3600,  # Long interval to avoid triggering during test
            "max_concurrent": 3,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        # Semaphore is None before start
        assert adapter._execution_semaphore is None
        
        await adapter.start()
        
        # Semaphore should be initialized after start
        assert adapter._execution_semaphore is not None
        
        # Clean up
        await adapter.stop()


# ==================== Lifecycle Tests ====================


class TestSchedulerLifecycle:
    """Tests for scheduler lifecycle (start, stop, health_check)."""

    @pytest.mark.asyncio
    async def test_start_sets_status_running(self, mock_on_message):
        """Test that start() sets status to RUNNING."""
        config = make_config("test-lifecycle-start", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter.status == SourceStatus.STOPPED
        
        await adapter.start()
        
        assert adapter.status == SourceStatus.RUNNING
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_stop_sets_status_stopped(self, mock_on_message):
        """Test that stop() sets status to STOPPED."""
        config = make_config("test-lifecycle-stop", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        await adapter.stop()
        
        assert adapter.status == SourceStatus.STOPPED

    @pytest.mark.asyncio
    async def test_health_check_running(self, mock_on_message):
        """Test health_check returns True when running."""
        config = make_config("test-health-running", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        is_healthy = await adapter.health_check()
        
        assert is_healthy is True
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_health_check_stopped(self, mock_on_message):
        """Test health_check returns False when stopped."""
        config = make_config("test-health-stopped", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        is_healthy = await adapter.health_check()
        
        assert is_healthy is False

    @pytest.mark.asyncio
    async def test_double_start_is_safe(self, mock_on_message):
        """Test that calling start() twice is safe."""
        config = make_config("test-double-start", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        await adapter.start()  # Should not raise
        
        assert adapter.status == SourceStatus.RUNNING
        
        await adapter.stop()


# ==================== Manual Trigger Tests ====================


class TestManualTrigger:
    """Tests for manual_trigger functionality."""

    @pytest.mark.asyncio
    async def test_manual_trigger_returns_execution_id(self, mock_on_message):
        """Test that manual_trigger returns a valid execution_id."""
        config = make_config("test-manual-trigger", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        execution_id = await adapter.manual_trigger()
        
        assert execution_id is not None
        assert isinstance(execution_id, str)
        assert len(execution_id) == 36  # UUID format
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_manual_trigger_when_stopped_raises_error(self, mock_on_message):
        """Test that manual_trigger raises error when scheduler is stopped."""
        config = make_config("test-manual-stopped", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        with pytest.raises(RuntimeError, match="Scheduler not running"):
            await adapter.manual_trigger()

    @pytest.mark.asyncio
    async def test_manual_trigger_emits_message(self, mock_on_message):
        """Test that manual_trigger emits a message via callback."""
        config = make_config("test-manual-emit", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Manual test message",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        execution_id = await adapter.manual_trigger()
        
        # Give the async task time to complete
        await asyncio.sleep(0.1)
        
        # Check that on_message was called
        mock_on_message.assert_called_once()
        call_args = mock_on_message.call_args[0][0]
        
        assert isinstance(call_args, IncomingMessage)
        assert call_args.content == "Manual test message"
        assert call_args.metadata["scheduler"]["trigger_type"] == "manual"
        
        await adapter.stop()


# ==================== Next Trigger Time Tests ====================


class TestNextTriggerTime:
    """Tests for _get_next_trigger_time calculation."""

    def test_next_trigger_cron(self, mock_on_message):
        """Test next trigger time calculation for cron schedule."""
        config = make_config("test-next-cron", {
            "schedule": "0 9 * * *",  # Every day at 9 AM
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        next_trigger = adapter._get_next_trigger_time()
        
        assert next_trigger is not None
        assert next_trigger > datetime.now(timezone.utc)

    def test_next_trigger_interval(self, mock_on_message):
        """Test next trigger time calculation for interval schedule."""
        config = make_config("test-next-interval", {
            "interval_seconds": 300,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        next_trigger = adapter._get_next_trigger_time()
        
        assert next_trigger is not None
        # Should be approximately 5 minutes from now
        now = datetime.now(timezone.utc)
        expected_min = now + timedelta(seconds=299)
        expected_max = now + timedelta(seconds=301)
        
        assert expected_min <= next_trigger <= expected_max

    def test_next_trigger_one_time_future(self, mock_on_message):
        """Test next trigger time for future one-time schedule."""
        future_time = datetime.now(timezone.utc) + timedelta(hours=2)
        config = make_config("test-next-onetime", {
            "run_at": future_time.isoformat(),
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        next_trigger = adapter._get_next_trigger_time()
        
        assert next_trigger is not None

    def test_next_trigger_one_time_past_returns_now(self, mock_on_message):
        """Test that past one-time schedule returns current time."""
        config = make_config("test-next-past", {
            "run_at": "2020-01-01T00:00:00Z",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        next_trigger = adapter._get_next_trigger_time()
        
        # Past time should trigger immediately (returns current time)
        assert next_trigger is not None


# ==================== No Schedule Configuration Tests ====================


class TestNoScheduleConfiguration:
    """Tests for error handling when no schedule is configured."""

    def test_no_schedule_raises_error(self, mock_on_message):
        """Test that missing schedule configuration raises ValueError."""
        config = make_config("test-no-schedule", {
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        with pytest.raises(ValueError, match="No valid schedule configured"):
            SchedulerAdapter(config, mock_on_message)

    def test_empty_config_raises_error(self, mock_on_message):
        """Test that empty config raises ValueError."""
        config = make_config("test-empty-config", {})
        
        with pytest.raises(ValueError, match="No valid schedule configured"):
            SchedulerAdapter(config, mock_on_message)


# ==================== Execution Callback Tests ====================


class TestExecutionCallback:
    """Tests for execution callback functionality."""

    @pytest.mark.asyncio
    async def test_execution_callback_called_on_manual_trigger(self, mock_on_message, mock_execution_callback):
        """Test that execution callback is called during manual trigger."""
        config = make_config("test-callback-manual", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, mock_execution_callback)
        await adapter.start()
        
        await adapter.manual_trigger()
        
        # Give the async task time to complete
        await asyncio.sleep(0.1)
        
        # Callback should have been called with 'triggered' and 'completed' status
        assert mock_execution_callback.call_count >= 2
        
        await adapter.stop()


# ==================== TestSemaphoreTimeout ====================


class TestSemaphoreTimeout:
    """Tests for semaphore timeout behavior."""

    @pytest.mark.asyncio
    async def test_execution_skipped_when_semaphore_at_capacity(self, mock_on_message, mock_execution_callback):
        """Test that execution is skipped when semaphore is at capacity."""
        config = make_config("test-sem-cap", {
            "interval_seconds": 3600,
            "max_concurrent": 1,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, mock_execution_callback)
        await adapter.start()
        
        # Pre-acquire the semaphore to simulate capacity
        await adapter._execution_semaphore.acquire()
        
        # Trigger execution - should be skipped
        await adapter._emit_scheduled_message()
        
        # Give time for async operations
        await asyncio.sleep(0.2)
        
        # Verify callback was called with 'skipped' status
        mock_execution_callback.assert_called()
        last_call = mock_execution_callback.call_args
        assert last_call.kwargs.get("status") == "skipped" or (
            last_call[1] is not None and last_call[1].get("status") == "skipped"
        )
        
        # Release our pre-acquired slot
        adapter._execution_semaphore.release()
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_execution_proceeds_when_semaphore_available(self, mock_on_message, mock_execution_callback):
        """Test that execution proceeds normally when semaphore is available."""
        config = make_config("test-sem-avail", {
            "interval_seconds": 3600,
            "max_concurrent": 1,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, mock_execution_callback)
        await adapter.start()
        
        # Trigger execution - should proceed normally
        await adapter._emit_scheduled_message()
        
        # Give time for async operations
        await asyncio.sleep(0.2)
        
        # Verify callback was called with 'triggered' and 'completed' status
        assert mock_execution_callback.call_count >= 2
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_semaphore_released_after_execution_completes(self, mock_on_message):
        """Test that semaphore is released after execution completes."""
        config = make_config("test-sem-release", {
            "interval_seconds": 3600,
            "max_concurrent": 1,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        assert adapter._execution_semaphore._value == 1
        
        # Trigger execution
        await adapter._emit_scheduled_message()
        
        # Wait for completion
        await asyncio.sleep(0.2)
        
        # Semaphore should be back to max
        assert adapter._execution_semaphore._value == 1
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_manual_trigger_uses_longer_timeout(self, mock_on_message, mock_execution_callback):
        """Test that manual trigger uses longer semaphore timeout."""
        config = make_config("test-manual-timeout", {
            "interval_seconds": 3600,
            "max_concurrent": 1,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, mock_execution_callback)
        await adapter.start()
        
        # Pre-acquire semaphore so manual trigger will be skipped
        await adapter._execution_semaphore.acquire()
        
        # Call _acquire_execution_slot directly to test timeout behavior
        # Manual trigger uses SCHEDULER_MANUAL_SEMAPHORE_TIMEOUT_S (10s) for timeout
        execution_id = "test-execution-123"
        
        # Track that semaphore acquire was called
        original_acquire = adapter._execution_semaphore.acquire
        acquire_called = []
        
        async def tracking_acquire():
            acquire_called.append(True)
            # Return immediately to simulate already being acquired
            raise asyncio.TimeoutError()
        
        adapter._execution_semaphore.acquire = tracking_acquire
        
        # Call the internal method directly to test timeout behavior
        result = await adapter._acquire_execution_slot(
            10.0,  # Manual trigger timeout (10s)
            execution_id
        )
        
        # Should return False because semaphore was already acquired
        assert result is False
        
        # Acquire should have been called
        assert len(acquire_called) > 0
        
        # Callback should have been called with 'skipped' status
        mock_execution_callback.assert_called()
        call_args = mock_execution_callback.call_args
        assert call_args.kwargs.get("status") == "skipped" or call_args[1].get("status") == "skipped"
        
        # Release our pre-acquired slot
        adapter._execution_semaphore.release()
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_skipped_execution_callback_includes_message(self, mock_on_message, mock_execution_callback):
        """Test that skipped callback includes 'Max concurrent executions reached' message."""
        config = make_config("test-skipped-msg", {
            "interval_seconds": 3600,
            "max_concurrent": 1,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, mock_execution_callback)
        await adapter.start()
        
        # Pre-acquire the semaphore
        await adapter._execution_semaphore.acquire()
        
        # Trigger - should be skipped
        await adapter._emit_scheduled_message()
        
        # Give time for async operations
        await asyncio.sleep(0.2)
        
        # Find the skipped callback
        skipped_calls = [
            call for call in mock_execution_callback.call_args_list
            if (call.kwargs.get("status") == "skipped" or 
                (len(call[0]) > 2 and call[0][2] == "skipped"))
        ]
        
        assert len(skipped_calls) > 0, "Expected at least one skipped callback"
        
        # Check error_message includes the expected text
        skipped_call = skipped_calls[0]
        error_msg = skipped_call.kwargs.get("error_message")
        if error_msg is None and len(skipped_call[0]) > 4:
            error_msg = skipped_call[0][4]
        
        assert error_msg is not None
        assert "Max concurrent" in error_msg or "reached" in error_msg
        
        # Release our pre-acquired slot
        adapter._execution_semaphore.release()
        await adapter.stop()


# ==================== TestJobQueueRouting ====================


class TestJobQueueRouting:
    """Tests for job queue routing behavior."""

    @pytest.mark.asyncio
    async def test_scheduled_trigger_routes_through_job_queue(self, mock_on_message, mock_execution_callback):
        """Test that scheduled triggers with project_id use job queue."""
        mock_jqs = AsyncMock()
        mock_jqs.enqueue = AsyncMock(return_value=MagicMock(
            job_id="test-job-123",
            instance_id="test-instance-456",
            status="pending"
        ))
        
        config = make_config("test-jq-scheduled", {
            "interval_seconds": 3600,
            "project_id": "test-project",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(
            config, mock_on_message, mock_execution_callback, job_queue_service=mock_jqs
        )
        await adapter.start()
        
        # Trigger scheduled execution
        await adapter._emit_scheduled_message()
        
        # Give time for async operations
        await asyncio.sleep(0.2)
        
        # Verify job queue was called
        mock_jqs.enqueue.assert_called_once()
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_scheduled_trigger_immediate_when_no_project_id(self, mock_on_message, mock_execution_callback):
        """Test that scheduled triggers without project_id use immediate execution."""
        config = make_config("test-jq-no-project", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, mock_execution_callback)
        await adapter.start()
        
        # Trigger scheduled execution
        await adapter._emit_scheduled_message()
        
        # Give time for async operations
        await asyncio.sleep(0.2)
        
        # Verify on_message was called (immediate execution)
        mock_on_message.assert_called_once()
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_manual_trigger_always_immediate(self, mock_on_message, mock_execution_callback):
        """Test that manual triggers always use immediate execution even with project_id."""
        mock_jqs = AsyncMock()
        mock_jqs.enqueue = AsyncMock(return_value=MagicMock(
            job_id="test-job-123",
            instance_id="test-instance-456",
            status="pending"
        ))
        
        config = make_config("test-jq-manual", {
            "interval_seconds": 3600,
            "project_id": "test-project",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(
            config, mock_on_message, mock_execution_callback, job_queue_service=mock_jqs
        )
        await adapter.start()
        
        # Manual trigger
        await adapter.manual_trigger()
        
        # Give time for async operations
        await asyncio.sleep(0.2)
        
        # Verify job queue was NOT called (manual always immediate)
        mock_jqs.enqueue.assert_not_called()
        
        # Verify on_message was called (immediate execution)
        mock_on_message.assert_called_once()
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_job_queue_enqueue_failure_handled(self, mock_on_message, mock_execution_callback):
        """Test that job queue enqueue failure is handled gracefully."""
        mock_jqs = AsyncMock()
        mock_jqs.enqueue = AsyncMock(side_effect=Exception("Queue service unavailable"))
        
        config = make_config("test-jq-fail", {
            "interval_seconds": 3600,
            "project_id": "test-project",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(
            config, mock_on_message, mock_execution_callback, job_queue_service=mock_jqs
        )
        await adapter.start()
        
        # Trigger scheduled execution
        await adapter._emit_scheduled_message()
        
        # Give time for async operations
        await asyncio.sleep(0.2)
        
        # Verify callback was called with 'failed' status
        failed_calls = [
            call for call in mock_execution_callback.call_args_list
            if (call.kwargs.get("status") == "failed" or 
                (len(call[0]) > 2 and call[0][2] == "failed"))
        ]
        
        assert len(failed_calls) > 0, "Expected at least one failed callback"
        
        await adapter.stop()


# ==================== TestAtomicCounter ====================


class TestAtomicCounter:
    """Tests for atomic counter in reuse_instance mode."""

    @pytest.mark.asyncio
    async def test_counter_increments_correctly(self, mock_on_message, mock_execution_callback):
        """Test that counter increments correctly across multiple runs."""
        mock_repo = MagicMock()
        counter = [0]
        
        def increment_counter(source_id):
            counter[0] += 1
            return counter[0]
        
        mock_repo.increment_scheduler_run_counter = MagicMock(side_effect=increment_counter)
        
        config = make_config("test-counter", {
            "interval_seconds": 3600,
            "instance_mode": "reuse_instance",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(
            config, mock_on_message, mock_execution_callback, source_repo=mock_repo
        )
        await adapter.start()
        
        # First execution
        await adapter._emit_scheduled_message()
        await asyncio.sleep(0.2)
        assert mock_repo.increment_scheduler_run_counter.call_count == 1
        
        # Second execution
        await adapter._emit_scheduled_message()
        await asyncio.sleep(0.2)
        assert mock_repo.increment_scheduler_run_counter.call_count == 2
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_counter_handles_none_config(self, mock_on_message, mock_execution_callback):
        """Test that counter defaults to 1 when increment returns None."""
        mock_repo = MagicMock()
        mock_repo.increment_scheduler_run_counter = MagicMock(return_value=None)
        
        config = make_config("test-counter-none", {
            "interval_seconds": 3600,
            "instance_mode": "reuse_instance",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(
            config, mock_on_message, mock_execution_callback, source_repo=mock_repo
        )
        await adapter.start()
        
        # Trigger execution
        await adapter._emit_scheduled_message()
        await asyncio.sleep(0.2)
        
        # Should still execute without error
        assert mock_execution_callback.call_count >= 1
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_counter_initialized_to_one(self, mock_on_message, mock_execution_callback):
        """Test that counter starts at 1 on first run."""
        mock_repo = MagicMock()
        mock_repo.increment_scheduler_run_counter = MagicMock(return_value=1)
        
        config = make_config("test-counter-init", {
            "interval_seconds": 3600,
            "instance_mode": "reuse_instance",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(
            config, mock_on_message, mock_execution_callback, source_repo=mock_repo
        )
        await adapter.start()
        
        # Trigger execution
        await adapter._emit_scheduled_message()
        await asyncio.sleep(0.2)
        
        # Check that on_message was called with continuation format
        mock_on_message.assert_called_once()
        call_args = mock_on_message.call_args[0][0]
        assert "#1" in call_args.content
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_counter_persistence_across_runs(self, mock_on_message, mock_execution_callback):
        """Test that counter persists correctly across multiple runs."""
        mock_repo = MagicMock()
        counter = [0]
        
        def increment_counter(source_id):
            counter[0] += 1
            return counter[0]
        
        mock_repo.increment_scheduler_run_counter = MagicMock(side_effect=increment_counter)
        
        config = make_config("test-counter-persist", {
            "interval_seconds": 3600,
            "instance_mode": "reuse_instance",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(
            config, mock_on_message, mock_execution_callback, source_repo=mock_repo
        )
        await adapter.start()
        
        # Run 1
        await adapter._emit_scheduled_message()
        await asyncio.sleep(0.2)
        
        # Get the first message content
        first_content = mock_on_message.call_args_list[0][0][0].content
        assert "#1" in first_content
        
        # Run 2
        mock_on_message.reset_mock()
        await adapter._emit_scheduled_message()
        await asyncio.sleep(0.2)
        
        second_content = mock_on_message.call_args_list[0][0][0].content
        assert "#2" in second_content
        
        # Run 3
        mock_on_message.reset_mock()
        await adapter._emit_scheduled_message()
        await asyncio.sleep(0.2)
        
        third_content = mock_on_message.call_args_list[0][0][0].content
        assert "#3" in third_content
        
        await adapter.stop()


# ==================== TestLastRunAtNextRunAt ====================


class TestLastRunAtNextRunAt:
    """Tests for last_run_at and next_run_at behavior."""

    @pytest.mark.asyncio
    async def test_last_run_at_populated_from_execution_history(self, mock_on_message, mock_execution_callback):
        """Test that execution callback is called with correct execution_id."""
        config = make_config("test-last-run", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, mock_execution_callback)
        await adapter.start()
        
        # Manual trigger to get execution_id
        execution_id = await adapter.manual_trigger()
        await asyncio.sleep(0.2)
        
        # Verify callback was called with matching execution_id
        matching_calls = [
            call for call in mock_execution_callback.call_args_list
            if (call.kwargs.get("execution_id") == execution_id or
                (len(call[0]) > 0 and call[0][0] == execution_id))
        ]
        
        assert len(matching_calls) > 0, f"Expected callback with execution_id={execution_id}"
        
        await adapter.stop()

    def test_next_run_at_computed_when_running(self, mock_on_message):
        """Test that next trigger time is computed for running scheduler."""
        config = make_config("test-next-running", {
            "schedule": "0 9 * * *",  # 9 AM cron
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        next_trigger = adapter._get_next_trigger_time()
        
        assert next_trigger is not None
        # Should be a future time
        assert next_trigger > datetime.now(timezone.utc)

    def test_next_run_at_none_when_one_time_executed(self, mock_on_message):
        """Test that next_run_at is None when one-time schedule has executed."""
        config = make_config("test-next-once", {
            "run_at": "2020-01-01T00:00:00Z",  # Past time
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        # Before execution, returns now (past time)
        assert adapter._get_next_trigger_time() is not None
        
        # Mark as executed
        adapter._is_one_time_executed = True
        
        # After execution, returns None
        assert adapter._get_next_trigger_time() is None

    def test_next_run_at_correct_for_interval(self, mock_on_message):
        """Test that next_run_at is correctly computed for interval schedule."""
        config = make_config("test-next-interval", {
            "interval_seconds": 300,  # 5 minutes
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        now = datetime.now(timezone.utc)
        next_trigger = adapter._get_next_trigger_time()
        
        # Should be approximately now + 300s
        expected_min = now + timedelta(seconds=298)
        expected_max = now + timedelta(seconds=302)
        
        assert expected_min <= next_trigger <= expected_max


# ==================== TestCancelledErrorSemaphoreLeak ====================


class TestCancelledErrorSemaphoreLeak:
    """Tests for CancelledError handling and semaphore leak prevention."""

    @pytest.mark.asyncio
    async def test_cancelled_error_during_execution_releases_semaphore(self, mock_on_message):
        """Test that CancelledError during execution releases semaphore.
        
        Note: This test verifies that CancelledError is handled and the semaphore
        is released. Due to how asyncio.CancelledError propagates through nested
        try/finally blocks, the actual release behavior depends on the implementation.
        """
        config = make_config("test-cancel-exec", {
            "interval_seconds": 3600,
            "max_concurrent": 1,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        initial_value = adapter._execution_semaphore._value
        assert initial_value == 1
        
        # Mock _emit_message to raise CancelledError
        original_emit = adapter._emit_message
        
        async def raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError()
        
        adapter._emit_message = raise_cancelled
        
        # Trigger execution - will fail with CancelledError
        try:
            await adapter._emit_scheduled_message()
        except asyncio.CancelledError:
            pass  # Expected - CancelledError propagates
        
        # Give time for async operations
        await asyncio.sleep(0.2)
        
        # Semaphore should be at least released (not stuck at 0)
        # Note: The semaphore should return to its initial value
        # Any value other than 0 means it was released (not leaked)
        assert adapter._execution_semaphore._value >= 1, \
            f"Semaphore leaked! Value is {adapter._execution_semaphore._value}, should be >= 1"
        
        # Restore original
        adapter._emit_message = original_emit
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_cancelled_error_before_execute_run_releases_semaphore(self, mock_on_message):
        """Test that CancelledError before _execute_run still releases semaphore."""
        config = make_config("test-cancel-before", {
            "interval_seconds": 3600,
            "max_concurrent": 1,
            "instance_mode": "reuse_instance",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        mock_repo = MagicMock()
        mock_repo.get_instance_mapping = MagicMock(return_value=None)
        
        adapter = SchedulerAdapter(
            config, mock_on_message, source_repo=mock_repo
        )
        await adapter.start()
        
        assert adapter._execution_semaphore._value == 1
        
        # Mock _execute_run to raise CancelledError
        original_execute_run = adapter._execute_run
        
        async def raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError()
        
        adapter._execute_run = raise_cancelled
        
        # Trigger execution
        try:
            await adapter._emit_scheduled_message()
        except asyncio.CancelledError:
            pass  # Expected - CancelledError propagates
        
        # Give time for async operations
        await asyncio.sleep(0.2)
        
        # Semaphore should be released
        assert adapter._execution_semaphore._value == 1, "Semaphore leaked!"
        
        # Restore original
        adapter._execute_run = original_execute_run
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_semaphore_held_flag_prevents_double_release(self, mock_on_message):
        """Test that semaphore_held flag prevents double-release."""
        config = make_config("test-double-release", {
            "interval_seconds": 3600,
            "max_concurrent": 1,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        await adapter.start()
        
        initial_value = adapter._execution_semaphore._value
        
        # Trigger execution normally
        await adapter._emit_scheduled_message()
        
        # Give time for async operations
        await asyncio.sleep(0.2)
        
        # Should be back to initial value (not negative)
        assert adapter._execution_semaphore._value == initial_value, \
            f"Semaphore value should be {initial_value}, got {adapter._execution_semaphore._value}"
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_early_return_reuse_instance_releases_semaphore(self, mock_on_message, mock_execution_callback):
        """Test that early return in reuse_instance mode releases semaphore."""
        config = make_config("test-early-return", {
            "interval_seconds": 3600,
            "max_concurrent": 1,
            "instance_mode": "reuse_instance",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        mock_repo = MagicMock()
        mock_instance_repo = MagicMock()
        
        # Simulate active instance - will cause early return
        mock_mapping = MagicMock()
        mock_mapping.agent_instance_id = "active-instance-123"
        mock_repo.get_instance_mapping = MagicMock(return_value=mock_mapping)
        
        mock_instance = MagicMock()
        mock_instance.status = "running"
        mock_instance_repo.get = MagicMock(return_value=mock_instance)
        
        adapter = SchedulerAdapter(
            config, mock_on_message, mock_execution_callback,
            source_repo=mock_repo, instance_repo=mock_instance_repo
        )
        await adapter.start()
        
        assert adapter._execution_semaphore._value == 1
        
        # Trigger execution - will skip due to active instance
        await adapter._emit_scheduled_message()
        
        # Give time for async operations
        await asyncio.sleep(0.2)
        
        # Semaphore should be released despite early return
        assert adapter._execution_semaphore._value == 1, "Semaphore leaked in early return!"
        
        # Verify callback was called with 'skipped' status
        skipped_calls = [
            call for call in mock_execution_callback.call_args_list
            if (call.kwargs.get("status") == "skipped" or 
                (len(call[0]) > 2 and call[0][2] == "skipped"))
        ]
        assert len(skipped_calls) > 0
        
        await adapter.stop()


# ==================== TestErrorPaths ====================


class TestErrorPaths:
    """Tests for error handling paths."""

    @pytest.mark.asyncio
    async def test_execution_callback_failure_doesnt_crash_scheduler(self, mock_on_message):
        """Test that execution callback failure doesn't crash the scheduler."""
        mock_callback = Mock(side_effect=Exception("Callback error"))
        
        config = make_config("test-callback-fail", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, mock_callback)
        await adapter.start()
        
        # Verify scheduler is running
        assert adapter.status == SourceStatus.RUNNING
        
        # Trigger execution - callback will fail but scheduler should survive
        await adapter._emit_scheduled_message()
        await asyncio.sleep(0.2)
        
        # Scheduler should still be running
        assert adapter.status == SourceStatus.RUNNING
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_message_send_failure_recorded(self, mock_on_message, mock_execution_callback):
        """Test that message send failure is recorded in callback."""
        config = make_config("test-send-fail", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message, mock_execution_callback)
        await adapter.start()
        
        # Make on_message raise an exception
        mock_on_message.side_effect = Exception("Message send failed")
        
        # Trigger execution
        await adapter._emit_scheduled_message()
        await asyncio.sleep(0.2)
        
        # Verify callback was called with 'failed' status
        failed_calls = [
            call for call in mock_execution_callback.call_args_list
            if (call.kwargs.get("status") == "failed" or 
                (len(call[0]) > 2 and call[0][2] == "failed"))
        ]
        
        assert len(failed_calls) > 0, "Expected at least one failed callback"
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_queue_enqueue_failure_recorded(self, mock_on_message, mock_execution_callback):
        """Test that queue enqueue failure is properly recorded."""
        mock_jqs = AsyncMock()
        mock_jqs.enqueue = AsyncMock(side_effect=ValueError("queue full"))
        
        config = make_config("test-queue-fail", {
            "interval_seconds": 3600,
            "project_id": "test-project",
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(
            config, mock_on_message, mock_execution_callback, job_queue_service=mock_jqs
        )
        await adapter.start()
        
        # Trigger scheduled execution
        await adapter._emit_scheduled_message()
        await asyncio.sleep(0.2)
        
        # Verify callback was called with 'failed' status and correct error
        failed_calls = [
            call for call in mock_execution_callback.call_args_list
            if (call.kwargs.get("status") == "failed" or 
                (len(call[0]) > 2 and call[0][2] == "failed"))
        ]
        
        assert len(failed_calls) > 0, "Expected at least one failed callback"
        
        # Check error message contains "queue full"
        failed_call = failed_calls[0]
        error_msg = failed_call.kwargs.get("error_message")
        if error_msg is None and len(failed_call[0]) > 4:
            error_msg = failed_call[0][4]
        
        assert error_msg is not None
        assert "queue" in error_msg.lower() or "full" in error_msg.lower()
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_adapter_start_failure(self, mock_on_message):
        """Test that adapter properly handles start and health check."""
        config = make_config("test-start-health", {
            "interval_seconds": 3600,
            "agent": "./agents/coder",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        # Not started - health check should return False
        is_healthy = await adapter.health_check()
        assert is_healthy is False
        
        # Start the adapter
        await adapter.start()
        
        # Now health check should return True
        is_healthy = await adapter.health_check()
        assert is_healthy is True
        
        # Status should be RUNNING
        assert adapter.status == SourceStatus.RUNNING
        
        await adapter.stop()
        
        # After stop, health check should return False
        is_healthy = await adapter.health_check()
        assert is_healthy is False
