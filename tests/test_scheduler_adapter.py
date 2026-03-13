"""Unit tests for SchedulerAdapter."""

import pytest
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, AsyncMock, patch
from zoneinfo import ZoneInfo

from daemon.sources.adapters.scheduler import SchedulerAdapter
from daemon.sources.base import SourceConfig, IncomingMessage, SourceStatus


# ==================== Fixtures ====================


@pytest.fixture
def mock_on_message():
    """Create a mock async callback for message handling."""
    return AsyncMock()


@pytest.fixture
def mock_execution_callback():
    """Create a mock execution callback."""
    return Mock()


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
