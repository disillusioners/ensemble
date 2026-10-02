"""Unit tests for SchedulerAdapter."""

import pytest
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from zoneinfo import ZoneInfo

from daemon.sources.adapters.scheduler import SchedulerAdapter
from daemon.sources.base import SourceConfig, IncomingMessage, SourceStatus

from tests.conftest import make_config


# ==================== Cron Parsing Tests ====================


class TestCronParsing:
    """Tests for cron expression parsing and validation."""

    def test_valid_cron_expression(self, mock_on_message):
        """Test that valid cron expressions are accepted."""
        config = make_config("test-cron", {
            "schedule": "0 9 * * 1-5",  # 9 AM on weekdays
            "agent": "./agents/developer",
            "message": "Good morning!",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_CRON
        assert adapter._cron_expression == "0 9 * * 1-5"

    def test_valid_cron_every_minute(self, mock_on_message):
        """Test that 'every minute' cron expression is valid."""
        config = make_config("test-cron-minute", {
            "schedule": "* * * * *",
            "agent": "./agents/developer",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_CRON
        assert adapter._cron_expression == "* * * * *"

    def test_valid_cron_with_specific_time(self, mock_on_message):
        """Test cron expression with specific hour and minute."""
        config = make_config("test-cron-specific", {
            "schedule": "30 14 * * *",  # 2:30 PM every day
            "agent": "./agents/developer",
            "message": "Afternoon check",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_CRON

    def test_invalid_cron_expression_raises_error(self, mock_on_message):
        """Test that invalid cron expressions raise ValueError."""
        config = make_config("test-invalid-cron", {
            "schedule": "invalid cron expression",
            "agent": "./agents/developer",
            "message": "Test",
        })
        
        with pytest.raises(ValueError, match="Invalid cron expression"):
            SchedulerAdapter(config, mock_on_message)

    def test_cron_expression_with_too_few_fields(self, mock_on_message):
        """Test that cron with too few fields raises error."""
        config = make_config("test-cron-short", {
            "schedule": "0 9 *",  # Only 3 fields instead of 5
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
            "message": "Periodic check",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_INTERVAL
        assert adapter._interval_seconds == 300

    def test_interval_seconds_minimum(self, mock_on_message):
        """Test minimum valid interval (1 second)."""
        config = make_config("test-interval-min", {
            "interval_seconds": 1,
            "agent": "./agents/developer",
            "message": "Fast check",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_INTERVAL
        assert adapter._interval_seconds == 1

    def test_interval_seconds_large(self, mock_on_message):
        """Test large interval (24 hours)."""
        config = make_config("test-interval-large", {
            "interval_seconds": 86400,  # 24 hours
            "agent": "./agents/developer",
            "message": "Daily check",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_INTERVAL
        assert adapter._interval_seconds == 86400

    def test_interval_seconds_zero_raises_error(self, mock_on_message):
        """Test that zero interval raises ValueError."""
        config = make_config("test-interval-zero", {
            "interval_seconds": 0,
            "agent": "./agents/developer",
            "message": "Test",
        })
        
        with pytest.raises(ValueError, match="interval_seconds must be a positive integer"):
            SchedulerAdapter(config, mock_on_message)

    def test_interval_seconds_negative_raises_error(self, mock_on_message):
        """Test that negative interval raises ValueError."""
        config = make_config("test-interval-neg", {
            "interval_seconds": -10,
            "agent": "./agents/developer",
            "message": "Test",
        })
        
        with pytest.raises(ValueError, match="interval_seconds must be a positive integer"):
            SchedulerAdapter(config, mock_on_message)

    def test_interval_seconds_string_raises_error(self, mock_on_message):
        """Test that string interval raises ValueError."""
        config = make_config("test-interval-str", {
            "interval_seconds": "300",
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
            "message": "One-time check",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_ONE_TIME
        assert adapter._run_at is not None

    def test_run_at_with_z_suffix(self, mock_on_message):
        """Test run_at with Z suffix (Zulu time)."""
        config = make_config("test-onetime-z", {
            "run_at": "2025-12-25T10:00:00Z",
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
            "message": "Past check",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        # Config is valid, but execution will trigger immediately
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_ONE_TIME

    def test_run_at_invalid_format_raises_error(self, mock_on_message):
        """Test that invalid run_at format raises ValueError."""
        config = make_config("test-onetime-invalid", {
            "run_at": "not-a-date",
            "agent": "./agents/developer",
            "message": "Test",
        })
        
        with pytest.raises(ValueError, match="Invalid run_at format"):
            SchedulerAdapter(config, mock_on_message)

    def test_run_at_empty_string_ignored(self, mock_on_message):
        """Test that empty run_at string is ignored (falls through to no schedule error)."""
        config = make_config("test-onetime-empty", {
            "run_at": "",
            "agent": "./agents/developer",
            "message": "Test",
        })
        
        # Empty string should fall through to "no valid schedule" error
        with pytest.raises(ValueError, match="No valid schedule configured"):
            SchedulerAdapter(config, mock_on_message)


# ==================== Timezone Handling Tests ====================


class TestTimezoneHandling:
    """Tests for timezone configuration."""

    def test_default_timezone_utc(self, mock_on_message):
        """Test that default timezone follows the D2 resolution chain.

        Phase 1 / Task 2.4 / ADR-002: with no explicit ``config.timezone``
        and no ``SchedulingConfig.default_timezone`` env override, the
        resolver falls through to host-local detection (``step 3``), then
        to ``datetime.timezone.utc`` (``step 4``) if host detection finds
        nothing. On the test host (``/etc/localtime → /usr/share/zoneinfo/Etc/UTC``),
        the chain resolves to ``Etc/UTC`` — same UTC instant as ``UTC``,
        different ``ZoneInfo`` key. The legacy assertion of literal
        ``ZoneInfo("UTC")`` no longer holds because the chain reaches
        step 3 first on this host.
        """
        config = make_config("test-tz-default", {
            "schedule": "0 9 * * *",
            "agent": "./agents/developer",
            "message": "Test",
        })

        adapter = SchedulerAdapter(config, mock_on_message)

        # Resolution chain result is UTC-like (offset zero) regardless of
        # which rung the chain stopped at — accept either host-local
        # ``Etc/UTC`` or terminal ``UTC``.
        from datetime import timezone as _stdlib_tz
        from zoneinfo import ZoneInfo as _ZI
        assert adapter._timezone in (
            _ZI("Etc/UTC"),
            _ZI("UTC"),
            _stdlib_tz.utc,
        ), f"unexpected tz chain result: {adapter._timezone!r}"

    def test_timezone_america_new_york(self, mock_on_message):
        """Test America/New_York timezone."""
        config = make_config("test-tz-ny", {
            "schedule": "0 9 * * *",
            "timezone": "America/New_York",
            "agent": "./agents/developer",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._timezone == ZoneInfo("America/New_York")

    def test_timezone_asia_tokyo(self, mock_on_message):
        """Test Asia/Tokyo timezone."""
        config = make_config("test-tz-tokyo", {
            "schedule": "0 9 * * *",
            "timezone": "Asia/Tokyo",
            "agent": "./agents/developer",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._timezone == ZoneInfo("Asia/Tokyo")

    def test_timezone_europe_london(self, mock_on_message):
        """Test Europe/London timezone."""
        config = make_config("test-tz-london", {
            "schedule": "0 9 * * *",
            "timezone": "Europe/London",
            "agent": "./agents/developer",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._timezone == ZoneInfo("Europe/London")

    def test_timezone_unknown_falls_back_to_utc(self, mock_on_message):
        """Test that unknown timezone falls back to UTC.

        Phase 1 / Task 2.4 / architecture §4.3: the resolver's terminal
        fallback is ``datetime.timezone.utc`` (NOT ``ZoneInfo("UTC")``).
        ``ZoneInfo("UTC")`` raises ``ZoneInfoNotFoundError`` on a stripped
        container with no system tzdb and no pip tzdata — the C-level
        ``datetime.timezone.utc`` cannot fail. The previous literal
        ``ZoneInfo("UTC")`` assertion encoded the legacy hardcoded fallback
        that phase-1 explicitly obsoletes.
        """
        config = make_config("test-tz-unknown", {
            "schedule": "0 9 * * *",
            "timezone": "Invalid/Timezone",
            "agent": "./agents/developer",
            "message": "Test",
        })

        adapter = SchedulerAdapter(config, mock_on_message)

        # Should fall back to UTC (the resolver's terminal rung) with a
        # warning logged. Accept the C-level datetime.timezone.utc OR
        # a ZoneInfo("UTC") if a future change re-introduces it.
        from datetime import timezone as _stdlib_tz
        assert adapter._timezone in (_stdlib_tz.utc, ZoneInfo("UTC")), (
            f"unexpected tz fallback: {adapter._timezone!r}"
        )

    def test_timezone_affects_next_trigger_calculation(self, mock_on_message):
        """Test that timezone affects when the next trigger is calculated."""
        config = make_config("test-tz-calc", {
            "schedule": "0 9 * * *",  # 9 AM
            "timezone": "Asia/Tokyo",  # UTC+9
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._max_concurrent == 1

    def test_max_concurrent_custom(self, mock_on_message):
        """Test custom max_concurrent value."""
        config = make_config("test-concurrent-custom", {
            "interval_seconds": 60,
            "max_concurrent": 5,
            "agent": "./agents/developer",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        
        assert adapter._max_concurrent == 5

    def test_max_concurrent_high(self, mock_on_message):
        """Test high max_concurrent value."""
        config = make_config("test-concurrent-high", {
            "interval_seconds": 60,
            "max_concurrent": 100,
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
            "message": "Test",
        })
        
        adapter = SchedulerAdapter(config, mock_on_message)
        next_trigger = adapter._get_next_trigger_time()
        
        assert next_trigger is not None

    def test_next_trigger_one_time_past_returns_now(self, mock_on_message):
        """Test that past one-time schedule returns current time."""
        config = make_config("test-next-past", {
            "run_at": "2020-01-01T00:00:00Z",
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
        assert last_call.kwargs.get("status") == "skipped"
        
        # Release our pre-acquired slot
        adapter._execution_semaphore.release()
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_execution_proceeds_when_semaphore_available(self, mock_on_message, mock_execution_callback):
        """Test that execution proceeds normally when semaphore is available."""
        config = make_config("test-sem-avail", {
            "interval_seconds": 3600,
            "max_concurrent": 1,
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
    async def test_acquire_failure_handled_for_manual_trigger(self, mock_on_message, mock_execution_callback):
        """Test that acquire failure is handled for manual trigger."""
        config = make_config("test-manual-timeout", {
            "interval_seconds": 3600,
            "max_concurrent": 1,
            "agent": "./agents/developer",
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
        assert call_args.kwargs.get("status") == "skipped"
        
        # Release our pre-acquired slot
        adapter._execution_semaphore.release()
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_skipped_execution_callback_includes_message(self, mock_on_message, mock_execution_callback):
        """Test that skipped callback includes 'Max concurrent executions reached' message."""
        config = make_config("test-skipped-msg", {
            "interval_seconds": 3600,
            "max_concurrent": 1,
            "agent": "./agents/developer",
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
            if call.kwargs.get("status") == "skipped"
        ]

        assert len(skipped_calls) > 0, "Expected at least one skipped callback"
        
        # Check error_message includes the expected text
        skipped_call = skipped_calls[0]
        error_msg = skipped_call.kwargs.get("error_message")
        
        assert error_msg is not None
        assert "Max concurrent" in error_msg or "reached" in error_msg
        
        # Release our pre-acquired slot
        adapter._execution_semaphore.release()
        await adapter.stop()


# ==================== TestJobQueueRouting ====================
# Phase 5 (cutover): scheduled triggers with project_id route through the
# inline message-Job path (``manager.enqueue_message_job``), NOT through
# ``JobQueueService.enqueue``. The legacy poll-loop path was removed.


class TestJobQueueRouting:
    """Tests for inline message-Job routing (Phase 5 cutover)."""

    @pytest.mark.asyncio
    async def test_scheduled_trigger_routes_through_inline_message_job(
        self, mock_on_message, mock_execution_callback
    ):
        """Test that scheduled triggers with project_id use the inline message-Job path."""
        # Mock manager with the inline message-Job entry point.
        mock_manager = AsyncMock()
        mock_manager.enqueue_message_job = AsyncMock(return_value=MagicMock(
            job_id="test-job-123",
            message_id="test-msg-456",
            instance_id="test-instance-789",
            status="queued",
        ))
        # Mapper calls spawn_instance_with_mcp when no mapping exists.
        mock_manager.spawn_instance_with_mcp = AsyncMock(return_value="test-instance-789")

        # Mock source_repo for InstanceMapper.
        mock_source_repo = MagicMock()
        mock_source_repo.get_instance_mapping = MagicMock(return_value=None)

        config = make_config("test-jq-scheduled", {
            "interval_seconds": 3600,
            "project_id": "test-project",
            "agent": "./agents/developer",
            "message": "Test",
        })

        adapter = SchedulerAdapter(
            config, mock_on_message, mock_execution_callback, manager=mock_manager,
            source_repo=mock_source_repo,
        )
        await adapter.start()

        # Trigger scheduled execution
        await adapter._emit_scheduled_message()

        # Give time for async operations
        await asyncio.sleep(0.2)

        # Verify inline message-Job path was called
        mock_manager.enqueue_message_job.assert_called_once()

        await adapter.stop()

    @pytest.mark.asyncio
    async def test_scheduled_trigger_immediate_when_no_project_id(self, mock_on_message, mock_execution_callback):
        """Test that scheduled triggers without project_id use immediate execution."""
        config = make_config("test-jq-no-project", {
            "interval_seconds": 3600,
            "agent": "./agents/developer",
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
        mock_manager = AsyncMock()
        mock_manager.enqueue_message_job = AsyncMock(return_value=MagicMock(
            job_id="test-job-123",
            message_id="test-msg-456",
            instance_id="test-instance-789",
            status="queued",
        ))
        mock_source_repo = MagicMock()

        config = make_config("test-jq-manual", {
            "interval_seconds": 3600,
            "project_id": "test-project",
            "agent": "./agents/developer",
            "message": "Test",
        })

        adapter = SchedulerAdapter(
            config, mock_on_message, mock_execution_callback, manager=mock_manager,
            source_repo=mock_source_repo,
        )
        await adapter.start()

        # Manual trigger
        await adapter.manual_trigger()

        # Give time for async operations
        await asyncio.sleep(0.2)

        # Verify inline message-Job was NOT called (manual always immediate)
        mock_manager.enqueue_message_job.assert_not_called()

        # Verify on_message was called (immediate execution)
        mock_on_message.assert_called_once()

        await adapter.stop()

    @pytest.mark.asyncio
    async def test_inline_message_job_failure_handled(self, mock_on_message, mock_execution_callback):
        """Test that inline message-Job failure is handled gracefully."""
        mock_manager = AsyncMock()
        mock_manager.enqueue_message_job = AsyncMock(side_effect=Exception("Queue service unavailable"))
        mock_manager.spawn_instance_with_mcp = AsyncMock(return_value="test-instance-fail")
        mock_source_repo = MagicMock()
        mock_source_repo.get_instance_mapping = MagicMock(return_value=None)

        config = make_config("test-jq-fail", {
            "interval_seconds": 3600,
            "project_id": "test-project",
            "agent": "./agents/developer",
            "message": "Test",
        })

        adapter = SchedulerAdapter(
            config, mock_on_message, mock_execution_callback, manager=mock_manager,
            source_repo=mock_source_repo,
        )
        await adapter.start()

        # Trigger scheduled execution
        await adapter._emit_scheduled_message()

        # Give time for async operations
        await asyncio.sleep(0.2)

        # Verify callback was called with 'failed' status
        failed_calls = [
            call for call in mock_execution_callback.call_args_list
            if call.kwargs.get("status") == "failed"
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
    async def test_execution_callback_receives_execution_id(self, mock_on_message, mock_execution_callback):
        """Test that execution callback is called with correct execution_id."""
        config = make_config("test-last-run", {
            "interval_seconds": 3600,
            "agent": "./agents/developer",
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
            if call.kwargs.get("execution_id") == execution_id
        ]
        
        assert len(matching_calls) > 0, f"Expected callback with execution_id={execution_id}"
        
        await adapter.stop()

    def test_next_run_at_computed_when_running(self, mock_on_message):
        """Test that next trigger time is computed for running scheduler."""
        config = make_config("test-next-running", {
            "schedule": "0 9 * * *",  # 9 AM cron
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
        
        try:
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
        finally:
            adapter._emit_message = original_emit
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_cancelled_error_before_execute_run_releases_semaphore(self, mock_on_message):
        """Test that CancelledError before _execute_run still releases semaphore."""
        config = make_config("test-cancel-before", {
            "interval_seconds": 3600,
            "max_concurrent": 1,
            "instance_mode": "reuse_instance",
            "agent": "./agents/developer",
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
        
        try:
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
        finally:
            adapter._execute_run = original_execute_run
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_semaphore_held_flag_prevents_double_release(self, mock_on_message):
        """Test that semaphore_held flag prevents double-release."""
        config = make_config("test-double-release", {
            "interval_seconds": 3600,
            "max_concurrent": 1,
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            if call.kwargs.get("status") == "skipped"
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
            "agent": "./agents/developer",
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
            "agent": "./agents/developer",
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
            if call.kwargs.get("status") == "failed"
        ]

        assert len(failed_calls) > 0, "Expected at least one failed callback"
        
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_inline_message_job_failure_recorded(self, mock_on_message, mock_execution_callback):
        """Test that inline message-Job failure is properly recorded."""
        mock_manager = AsyncMock()
        mock_manager.enqueue_message_job = AsyncMock(side_effect=ValueError("queue full"))
        mock_manager.spawn_instance_with_mcp = AsyncMock(return_value="test-instance-record")
        mock_source_repo = MagicMock()
        mock_source_repo.get_instance_mapping = MagicMock(return_value=None)

        config = make_config("test-queue-fail", {
            "interval_seconds": 3600,
            "project_id": "test-project",
            "agent": "./agents/developer",
            "message": "Test",
        })

        adapter = SchedulerAdapter(
            config, mock_on_message, mock_execution_callback, manager=mock_manager,
            source_repo=mock_source_repo,
        )
        await adapter.start()

        # Trigger scheduled execution
        await adapter._emit_scheduled_message()
        await asyncio.sleep(0.2)

        # Verify callback was called with 'failed' status and correct error
        failed_calls = [
            call for call in mock_execution_callback.call_args_list
            if call.kwargs.get("status") == "failed"
        ]

        assert len(failed_calls) > 0, "Expected at least one failed callback"

        # Check error message contains "queue full"
        failed_call = failed_calls[0]
        error_msg = failed_call.kwargs.get("error_message")

        assert error_msg is not None
        assert "queue" in error_msg.lower() or "full" in error_msg.lower()

        await adapter.stop()

    @pytest.mark.asyncio
    async def test_adapter_start_failure(self, mock_on_message):
        """Test that adapter properly handles start and health check."""
        config = make_config("test-start-health", {
            "interval_seconds": 3600,
            "agent": "./agents/developer",
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


# ==================== Phase-5 TestTzResolution (D2 chain) ====================


class TestTzResolution:
    """D2 default tz chain through the adapter (ADR-002 / architecture §4.3).

    Resolution order: explicit ``config.timezone`` →
    ``ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE`` (SchedulingConfig.default_timezone)
    → host-local detection → terminal ``datetime.timezone.utc`` fallback
    with a loud warning.

    SKELETON ADAPTATIONS vs phase5-plan.md §Task 1 (frozen skeleton):
      * ``adapter._resolved_tz`` → LANDED attribute is ``adapter._timezone``.
      * ``ENSEMBLE_SCHEDULING_DEFAULT_TZ`` → LANDED env var is
        ``ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE`` (field ``default_timezone``
        + pydantic ``env_prefix="ENSEMBLE_SCHEDULING_"``).
      * An explicit host-local stub is used so the test is deterministic on
        any CI host (Risk #8); the module cache is bypassed via
        ``ENSEMBLE_SCHEDULING_HOST_LOCAL_TZ_CACHE_SECONDS=0`` +
        ``_cache_clear_for_tests()``.
    """

    @pytest.fixture(autouse=True)
    def _isolate_tz_chain(self, monkeypatch):
        """Deterministic tz environment: no env default, no TZ, no host cache."""
        monkeypatch.setenv("ENSEMBLE_SCHEDULING_HOST_LOCAL_TZ_CACHE_SECONDS", "0")
        monkeypatch.delenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", raising=False)
        monkeypatch.delenv("TZ", raising=False)
        from daemon.util import tz as tz_module

        tz_module._cache_clear_for_tests()
        yield
        tz_module._cache_clear_for_tests()

    def test_explicit_tz_wins(self, mock_on_message, monkeypatch):
        """Step 1: an explicit config ``timezone`` beats the daemon default."""
        monkeypatch.setenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", "UTC")
        config = make_config("tz-explicit", {
            "schedule": "0 6 * * *",
            "timezone": "Asia/Tokyo",
            "agent": "./agents/developer",
            "message": "x",
        })
        adapter = SchedulerAdapter(config, mock_on_message)
        assert adapter._timezone.key == "Asia/Tokyo"

    def test_env_default_used_when_param_absent(self, mock_on_message, monkeypatch):
        """Step 2: ``ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE`` applies when the config has no timezone."""
        monkeypatch.setenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", "Europe/Berlin")
        config = make_config("tz-env-default", {
            "schedule": "0 6 * * *",
            "agent": "./agents/developer",
            "message": "x",
        })
        adapter = SchedulerAdapter(config, mock_on_message)
        assert adapter._timezone.key == "Europe/Berlin"

    def test_host_local_used_when_env_absent(self, mock_on_message, monkeypatch):
        """Step 3: host-local detection applies when neither explicit nor env default is set."""
        from daemon.util import tz as tz_module

        monkeypatch.setattr(
            tz_module, "_read_etc_localtime_target", lambda: "Australia/Sydney"
        )
        config = make_config("tz-host-local", {
            "schedule": "0 6 * * *",
            "agent": "./agents/developer",
            "message": "x",
        })
        adapter = SchedulerAdapter(config, mock_on_message)
        assert adapter._timezone.key == "Australia/Sydney"

    def test_utc_fallback_warns_loudly(self, mock_on_message, monkeypatch, caplog):
        """Step 4 (terminal): nothing resolvable → ``datetime.timezone.utc`` + LOUD warning."""
        import logging
        from datetime import timezone as _stdlib_tz

        from daemon.util import tz as tz_module

        monkeypatch.setattr(tz_module, "_read_etc_localtime_target", lambda: None)
        config = make_config("tz-utc-fallback", {
            "schedule": "0 6 * * *",
            "agent": "./agents/developer",
            "message": "x",
        })
        with caplog.at_level(logging.WARNING, logger="daemon.util.tz"):
            adapter = SchedulerAdapter(config, mock_on_message)
        # Terminal fallback is the stdlib constant, NOT ZoneInfo("UTC").
        assert adapter._timezone == _stdlib_tz.utc
        assert any("fell back to UTC" in r.getMessage() for r in caplog.records), (
            f"expected a loud UTC-fallback warning, got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )


# ==================== Phase-5 TestDstSemantics (D8, both paths) ====================


class _FrozenDatetime(datetime):
    """``datetime`` subclass with a class-level frozen ``now()``.

    Used to pin the cron path's evaluation instant: the adapter calls
    ``datetime.now(self._timezone)`` (module-global ``datetime``), so
    patching ``daemon.sources.adapters.scheduler.datetime`` with this
    subclass freezes the croniter "now" without touching any other
    machinery. All inherited arithmetic/comparison semantics are intact.
    """

    _frozen: datetime | None = None

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001 — mirrors datetime.now signature
        if tz is not None:
            return cls._frozen.astimezone(tz)
        return cls._frozen


class TestDstSemantics:
    """D8 DST semantics — pinned on BOTH paths (architecture §4.2 / ADR-008):

    (a) cron path: aware datetimes flow into ``croniter>=3.0.0`` with NO
        manual DST arithmetic; the pinned semantics are croniter's
        documented defaults — skip-the-gap on spring-forward,
        FIRST occurrence on fall-back ambiguity.
    (b) one-shot path: naive local times anchor via
        ``daemon.util.tz.anchor_local_to_utc`` — fold=0 on ambiguity,
        shift-forward + loud warning in the gap.

    The 4 tests below are the phase5-plan §Task 1.2 named set; all assert
    documented behavior — NO ``pytest.skip`` (the pack is the merge gate).

    SKELETON ADAPTATION: the frozen skeleton cites
    ``daemon.utils.tz.anchor_local_to_utc``; the landed module is
    ``daemon.util.tz`` (verified: ``daemon/util/tz.py``).
    """

    NY = "America/New_York"

    def test_dst_spring_forward_gap_cron(self, mock_on_message, monkeypatch):
        """2026-03-08 02:30 America/New_York does NOT exist.

        F2 / D8 croniter 6.0.0 defect fix. croniter's broken pre-gap
        math emits a phantom ``03:30 EDT = 07:30Z`` from PRE-DST ``now``;
        the production cron path now goes through
        ``daemon.util.tz.compute_next_cron_fire`` which detects the
        phantom via croniter roundtrip + recovers via the cron
        expression's literal hour/minute (``02:30`` on Mar 8 → shifted
        to the first valid local time = ``03:00 EDT`` = ``07:00Z``).
        """
        import daemon.sources.adapters.scheduler as scheduler_module

        ny = ZoneInfo(self.NY)
        _FrozenDatetime._frozen = datetime(2026, 3, 8, 1, 0, tzinfo=ny)  # pre-transition EST
        monkeypatch.setattr(scheduler_module, "datetime", _FrozenDatetime)

        config = make_config("dst-gap-cron", {
            "schedule": "30 2 * * *",
            "timezone": self.NY,
            "agent": "./agents/developer",
            "message": "x",
        })
        adapter = SchedulerAdapter(config, mock_on_message)
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_CRON

        next_trigger = adapter._get_next_trigger_time()
        # Next-valid local time after 02:30 in the 1h gap = 03:00 EDT = 07:00Z.
        # NOT croniter's pre-fix phantom 03:30 EDT = 07:30Z.
        expected = datetime(2026, 3, 8, 3, 0, tzinfo=ny)
        assert next_trigger == expected, (
            f"expected next-valid {expected.isoformat()}, got {next_trigger}"
        )
        # 03:00 EDT = UTC-4 on the post-transition side.
        assert next_trigger.utcoffset().total_seconds() == -4 * 3600
        assert next_trigger.astimezone(__import__("datetime").timezone.utc).isoformat() == "2026-03-08T07:00:00+00:00"

    def test_dst_fall_back_ambiguity_cron(self, mock_on_message, monkeypatch):
        """2026-11-01 01:30 America/New_York exists TWICE.

        F2 / D8 croniter 6.0.0 defect fix. The production cron path
        uses ``fold_preference="post"`` (later-occurrence / post-DST)
        so cron fires stay continuous in UTC across the fall-back
        (01:30 NY on Nov 1 → 01:30 EST = 06:30Z; continuous with
        01:30 EST on Nov 2 = 06:30Z rather than the fold=0 01:30 EDT
        = 05:30Z which would create a 25h UTC fire vs the next-day
        24h fire).
        """
        import daemon.sources.adapters.scheduler as scheduler_module

        ny = ZoneInfo(self.NY)
        _FrozenDatetime._frozen = datetime(2026, 11, 1, 0, 0, tzinfo=ny)  # still EDT
        monkeypatch.setattr(scheduler_module, "datetime", _FrozenDatetime)

        config = make_config("dst-fold-cron", {
            "schedule": "30 1 * * *",
            "timezone": self.NY,
            "agent": "./agents/developer",
            "message": "x",
        })
        adapter = SchedulerAdapter(config, mock_on_message)

        next_trigger = adapter._get_next_trigger_time()
        # Cron-path post-DST semantic: fold=1 (second occurrence / EST).
        expected = datetime(2026, 11, 1, 1, 30, tzinfo=ny)
        assert next_trigger == expected, (
            f"expected post-DST {expected.isoformat()}, got {next_trigger}"
        )
        # Second occurrence = EST = UTC-5 (the fold=0 first occurrence
        # is UTC-4 EDT — kept by the one-shot anchor via the default
        # ``fold_preference="pre"``).
        assert next_trigger.utcoffset().total_seconds() == -5 * 3600

    def test_dst_spring_forward_gap_one_shot_anchor(self):
        """Nonexistent local time → anchor shifts forward to first valid local + warns.

        F1 fix (next-valid, 2026-10-02): the canonical anchor shifts
        02:30 → 03:00 EDT (the gap-end instant), NOT the pre-F1
        ``naive + gap_seconds`` → 03:30 EDT. Helper:
        ``daemon.util.tz.anchor_local_to_utc`` (architecture §4.2;
        phase1-plan.md §Task 9 + ADR-008).
        """
        from daemon.util.tz import anchor_local_to_utc

        ny = ZoneInfo(self.NY)
        naive = datetime(2026, 3, 8, 2, 30)
        anchored, warning = anchor_local_to_utc(naive, ny)
        # Next-valid local = 03:00 EDT = 07:00Z.
        assert anchored.replace(tzinfo=None) == datetime(2026, 3, 8, 3, 0)
        assert anchored.utcoffset().total_seconds() == -4 * 3600
        assert "shifted-forward" in warning

    def test_dst_fall_back_ambiguity_one_shot_anchor(self):
        """Ambiguous local time → fold=0 (first occurrence, EDT), NO warning.

        The one-shot anchor's default ``fold_preference="pre"`` (ADR-008)
        keeps the pre-DST / first-occurrence semantic on FOLD days.
        01:30 NY on Nov 1 → 01:30 EDT = 05:30Z. The cron path passes
        ``fold_preference="post"`` for its later-occurrence continuity
        (see ``test_dst_fall_back_ambiguity_cron`` above).
        """
        from daemon.util.tz import anchor_local_to_utc

        ny = ZoneInfo(self.NY)
        naive = datetime(2026, 11, 1, 1, 30)
        anchored, warning = anchor_local_to_utc(naive, ny)
        assert anchored.replace(tzinfo=None) == datetime(2026, 11, 1, 1, 30)
        # fold=0 → pre-transition offset (EDT, UTC-4), NOT the second -05:00 pass.
        assert anchored.utcoffset().total_seconds() == -4 * 3600
        assert anchored.fold == 0
        assert warning == ""

    def test_dst_cron_continuous_fires_daily_0600_ny_across_both_transitions(
        self, mock_on_message, monkeypatch
    ):
        """F2 regression: daily 06:00 America/New_York fires ONCE on each
        2026 transition day at the post-DST wall-clock instant.

        Drives the production ``_compute_next_cron_fire`` path (the same
        seam the adapter loop and the REST ``next_run_at`` surface
        consume) and enumerates every fire across a 5-day window
        spanning the spring-forward (Mar 7–11) and fall-back
        (Oct 30–Nov 3) transitions. Pinned to zoneinfo-computed UTC
        instants — ZERO phantom fires, ZERO missed fires.

        Pin (zoneinfo-computed, exact UTC):

            Mar 7  06:00 EST = 11:00Z  (pre-spring-forward)
            Mar 8  06:00 EDT = 10:00Z  (spring-forward day, post-gap)
            Mar 9  06:00 EDT = 10:00Z  (post-spring-forward)
            Mar 10 06:00 EDT = 10:00Z
            Mar 11 06:00 EDT = 10:00Z
            Oct 30 06:00 EDT = 10:00Z  (pre-fall-back)
            Oct 31 06:00 EDT = 10:00Z
            Nov 1  06:00 EST = 11:00Z  (fall-back day, post-fold)
            Nov 2  06:00 EST = 11:00Z  (post-fall-back)
            Nov 3  06:00 EST = 11:00Z
        """
        from datetime import timezone as _stdlib_tz

        from daemon.util.tz import compute_next_cron_fire

        ny = ZoneInfo(self.NY)

        # Spring-forward window: pin every fire from Mar 7 00:00 NY to Mar 12 00:00 NY
        start = datetime(2026, 3, 7, 0, 0, tzinfo=ny)
        fires = []
        cursor = start
        max_iters = 10
        for _ in range(max_iters):
            nxt = compute_next_cron_fire("0 6 * * *", cursor, ny)
            if nxt is None or nxt >= datetime(2026, 3, 12, 0, 0, tzinfo=ny):
                break
            fires.append(nxt)
            cursor = nxt + __import__("datetime").timedelta(seconds=1)

        utc_fires = [f.astimezone(_stdlib_tz.utc).isoformat() for f in fires]
        assert utc_fires == [
            "2026-03-07T11:00:00+00:00",  # 06:00 EST
            "2026-03-08T10:00:00+00:00",  # 06:00 EDT (gap day — single fire, no 05:00 phantom)
            "2026-03-09T10:00:00+00:00",
            "2026-03-10T10:00:00+00:00",
            "2026-03-11T10:00:00+00:00",
        ], f"unexpected spring-forward fire sequence: {utc_fires}"
        # Specifically no phantom at 05:00 EDT = 09:00Z on Mar 8.
        assert "2026-03-08T09:00:00+00:00" not in utc_fires

        # Fall-back window: pin every fire from Oct 30 00:00 NY to Nov 4 00:00 NY
        start = datetime(2026, 10, 30, 0, 0, tzinfo=ny)
        fires = []
        cursor = start
        for _ in range(max_iters):
            nxt = compute_next_cron_fire("0 6 * * *", cursor, ny)
            if nxt is None or nxt >= datetime(2026, 11, 4, 0, 0, tzinfo=ny):
                break
            fires.append(nxt)
            cursor = nxt + __import__("datetime").timedelta(seconds=1)

        utc_fires = [f.astimezone(_stdlib_tz.utc).isoformat() for f in fires]
        assert utc_fires == [
            "2026-10-30T10:00:00+00:00",  # 06:00 EDT
            "2026-10-31T10:00:00+00:00",  # 06:00 EDT (pre-fall-back)
            "2026-11-01T11:00:00+00:00",  # 06:00 EST (fall-back day, post-DST)
            "2026-11-02T11:00:00+00:00",  # 06:00 EST (post-fall-back)
            "2026-11-03T11:00:00+00:00",
        ], f"unexpected fall-back fire sequence: {utc_fires}"
        # Specifically no late fire at 07:00 EST = 12:00Z on Nov 1.
        assert "2026-11-01T12:00:00+00:00" not in utc_fires

    def test_dst_cron_non_dst_zone_control_asia_hcm(self):
        """F2 regression control: a non-DST zone (Asia/Ho_Chi_Minh) is
        unaffected by the cron-path DST fix.

        Daily 06:00 HCM across both 2026 transitions: every fire
        lands on 06:00 local (+07:00) → 23:00Z the previous day.
        One fire per local day, no phantom, no offset drift.
        """
        from datetime import timezone as _stdlib_tz

        from daemon.util.tz import compute_next_cron_fire

        hcm = ZoneInfo("Asia/Ho_Chi_Minh")

        # Mar 7 → Mar 9 NY == Mar 7 → Mar 9 HCM (UTC+7 vs UTC-5)
        cursor = datetime(2026, 3, 7, 0, 0, tzinfo=hcm)
        fires = []
        for _ in range(5):
            nxt = compute_next_cron_fire("0 6 * * *", cursor, hcm)
            if nxt is None:
                break
            fires.append(nxt)
            cursor = nxt + __import__("datetime").timedelta(seconds=1)
        utc_fires = [f.astimezone(_stdlib_tz.utc).isoformat() for f in fires]
        assert utc_fires == [
            "2026-03-06T23:00:00+00:00",
            "2026-03-07T23:00:00+00:00",
            "2026-03-08T23:00:00+00:00",
            "2026-03-09T23:00:00+00:00",
            "2026-03-10T23:00:00+00:00",
        ], f"unexpected HCM fire sequence: {utc_fires}"

    def test_dst_cron_weekly_wed_0400_ny_across_spring_forward(self):
        """F2 regression: weekly Wed 04:00 America/New_York across
        2026-03-11 (the Wednesday following Mar 8 spring-forward)
        lands on Mar 11 04:00 EDT = 08:00Z with NO phantom 03:00 fire.

        Pin (zoneinfo-computed): the only fire in the
        [Mar 7, Mar 25] window is Mar 11 04:00 EDT = 08:00Z; the
        next-week fire is Mar 18 04:00 EDT = 08:00Z.
        """
        from datetime import timezone as _stdlib_tz

        from daemon.util.tz import compute_next_cron_fire

        ny = ZoneInfo(self.NY)
        cursor = datetime(2026, 3, 7, 0, 0, tzinfo=ny)
        fires = []
        for _ in range(4):
            nxt = compute_next_cron_fire("0 4 * * 3", cursor, ny)
            if nxt is None:
                break
            fires.append(nxt)
            cursor = nxt + __import__("datetime").timedelta(seconds=1)
        utc_fires = [f.astimezone(_stdlib_tz.utc).isoformat() for f in fires]
        assert utc_fires == [
            "2026-03-11T08:00:00+00:00",  # Wed 04:00 EDT — no Mar 11 07:00Z phantom
            "2026-03-18T08:00:00+00:00",
            "2026-03-25T08:00:00+00:00",
            "2026-04-01T08:00:00+00:00",  # Apr 1 still EDT (fall-back is Nov)
        ], f"unexpected weekly Wed 04:00 fire sequence: {utc_fires}"
        # Specifically no phantom at 03:00 EDT = 07:00Z on Mar 11.
        assert "2026-03-11T07:00:00+00:00" not in utc_fires

    def test_dst_cron_adapter_loop_daily_0600_ny_across_spring_forward(
        self, mock_on_message, monkeypatch
    ):
        """F2 regression: the SCHEDULER ADAPTER loop path
        (``_get_next_trigger_time``) also returns Mar 8 06:00 EDT
        = 10:00Z (not croniter's pre-fix 05:00 EDT phantom).

        The adapter's cron branch routes through the same
        ``compute_next_cron_fire`` helper — this test pins the
        end-to-end seam from PRE-DST ``now`` to the fixed fire,
        covering any future code regression that bypasses the
        helper in the adapter loop.
        """
        import daemon.sources.adapters.scheduler as scheduler_module

        from datetime import timezone as _stdlib_tz

        ny = ZoneInfo(self.NY)
        _FrozenDatetime._frozen = datetime(2026, 3, 7, 12, 0, tzinfo=ny)  # pre-DST
        monkeypatch.setattr(scheduler_module, "datetime", _FrozenDatetime)

        config = make_config("dst-cron-loop-mar-8", {
            "schedule": "0 6 * * *",
            "timezone": self.NY,
            "agent": "./agents/developer",
            "message": "x",
        })
        adapter = SchedulerAdapter(config, mock_on_message)
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_CRON

        next_trigger = adapter._get_next_trigger_time()
        assert next_trigger == datetime(2026, 3, 8, 6, 0, tzinfo=ny), (
            f"expected Mar 8 06:00 EDT (10:00Z); got {next_trigger}"
        )
        assert (
            next_trigger.astimezone(_stdlib_tz.utc).isoformat()
            == "2026-03-08T10:00:00+00:00"
        )

    def test_dst_cron_adapter_loop_daily_0600_ny_across_fall_back(
        self, mock_on_message, monkeypatch
    ):
        """F2 regression: the adapter loop path on the FALL-BACK side
        returns Nov 1 06:00 EST = 11:00Z (not croniter's pre-fix
        07:00 EST phantom that drops the real fire).
        """
        import daemon.sources.adapters.scheduler as scheduler_module

        from datetime import timezone as _stdlib_tz

        ny = ZoneInfo(self.NY)
        _FrozenDatetime._frozen = datetime(2026, 10, 31, 12, 0, tzinfo=ny)  # pre-fold EDT
        monkeypatch.setattr(scheduler_module, "datetime", _FrozenDatetime)

        config = make_config("dst-cron-loop-nov-1", {
            "schedule": "0 6 * * *",
            "timezone": self.NY,
            "agent": "./agents/developer",
            "message": "x",
        })
        adapter = SchedulerAdapter(config, mock_on_message)
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_CRON

        next_trigger = adapter._get_next_trigger_time()
        assert next_trigger == datetime(2026, 11, 1, 6, 0, tzinfo=ny), (
            f"expected Nov 1 06:00 EST (11:00Z); got {next_trigger}"
        )
        assert (
            next_trigger.astimezone(_stdlib_tz.utc).isoformat()
            == "2026-11-01T11:00:00+00:00"
        )

    def test_dst_cron_daily_23_ny_across_fall_back_post_fire_seed(self):
        """F4 regression: ``0 23 * * *`` America/New_York across the
        2026-11-01 fall-back from the post-fire seed produces EXACTLY
        ONE fire on Nov 1 at 23:00 EST = 04:00Z Nov 2 — no Oct 31 nor
        Nov 2 fire on the same wall-clock.

        Pre-F4 (tester-reproduced ×2): croniter 6.0.0's tz-object-
        identity defect emits the WRONG DATE in the phantom emission
        for ``0 23 * * *`` across the fall-back — the function returned
        Nov 2 23:00 EST (Nov 3 04:00Z), silently skipping the Nov 1
        23:00 EST fire. The reconstruct path inherited croniter's wrong
        date.

        Post-F4 (date source = ``after_aware`` for DOW=``*``): the
        seed-anchor is Oct 31 23:00:01 EDT (just past the prior 23:00
        fire); the literal HH:MM has already passed on the cursor's
        date so the F4 day-advance rule produces Nov 1 23:00 EST =
        04:00Z Nov 2 — the expected fire.

        Pin (zoneinfo-computed, exact UTC) across the fold and a post-
        fire seed:
            Oct 31 23:00 EDT = 03:00Z Nov 1   (from Oct 31 00:00 EDT seed)
            Nov 1  23:00 EST = 04:00Z Nov 2   (the F4 fix — was missed pre-fix)
            Nov 2  23:00 EST = 04:00Z Nov 3
        """
        from datetime import timezone as _stdlib_tz

        from daemon.util.tz import compute_next_cron_fire

        ny = ZoneInfo(self.NY)

        # Leg 1: full sequence across the fold from Oct 31 00:00 NY.
        cursor = datetime(2026, 10, 31, 0, 0, tzinfo=ny)
        fires = []
        for _ in range(4):
            nxt = compute_next_cron_fire("0 23 * * *", cursor, ny)
            if nxt is None or nxt >= datetime(2026, 11, 3, 0, 0, tzinfo=ny):
                break
            fires.append(nxt)
            cursor = nxt + __import__("datetime").timedelta(seconds=1)
        utc_fires = [f.astimezone(_stdlib_tz.utc).isoformat() for f in fires]
        assert utc_fires == [
            "2026-11-01T03:00:00+00:00",  # Oct 31 23:00 EDT = 03:00Z Nov 1
            "2026-11-02T04:00:00+00:00",  # Nov 1 23:00 EST — F4 fix, was missed
            "2026-11-03T04:00:00+00:00",  # Nov 2 23:00 EST
        ], f"unexpected 0 23 fall-back fire sequence: {utc_fires}"

        # Leg 2 (the exact F4 post-fire-seed shape): cursor = Oct 31
        # 23:00:01 EDT just past the prior fire → next fire must be
        # Nov 1 23:00 EST = 04:00Z Nov 2 (not Nov 2 23:00).
        after_aware = datetime(2026, 10, 31, 23, 0, 1, tzinfo=ny)
        nxt = compute_next_cron_fire("0 23 * * *", after_aware, ny)
        assert nxt is not None, "F4 regression — must return a fire"
        assert nxt == datetime(2026, 11, 1, 23, 0, tzinfo=ny), (
            f"F4 regression — expected Nov 1 23:00 EST, got {nxt.isoformat()}"
        )
        assert (
            nxt.astimezone(_stdlib_tz.utc).isoformat()
            == "2026-11-02T04:00:00+00:00"
        )
        # Post-DST fold offset — the second occurrence (EST).
        assert nxt.utcoffset().total_seconds() == -5 * 3600
        # Specifically NOT the pre-fix wrong-date fire (Nov 2 23:00).
        assert nxt.astimezone(_stdlib_tz.utc).isoformat() != (
            "2026-11-03T04:00:00+00:00"
        )

    def test_dst_cron_daily_0_ny_across_spring_forward(self):
        """F4 mirror: ``0 0 * * *`` America/New_York across the
        2026-03-08 spring-forward gap produces EXACTLY ONE fire on
        Mar 8 at 00:00 EST = 05:00Z Mar 8 (pre-gap), no Mar 8 mid-gap
        fire on the same wall-clock.

        Spring-forward gap (02:00–03:00 Mar 8) does not contain 00:00,
        so the literal HH:MM is not gap-affected — the F4 fix's
        day-advance rule is exercised but the gap-shift path is not
        (the literal 00:00 lands on Mar 8 00:00 EST = 05:00Z, no shift
        needed). Pins that the F4 fix is symmetry-correct on the
        spring-forward side as well.

        Pin (zoneinfo-computed, exact UTC):
            Mar 7 00:00 EST = 05:00Z Mar 7
            Mar 8 00:00 EST = 05:00Z Mar 8   (single fire on gap day)
            Mar 9 00:00 EDT = 04:00Z Mar 9
            Mar 10 00:00 EDT = 04:00Z Mar 10
        """
        from datetime import timezone as _stdlib_tz

        from daemon.util.tz import compute_next_cron_fire

        ny = ZoneInfo(self.NY)

        # Cursor = Mar 6 23:59:59 NY so the first fire is Mar 7 00:00
        # (croniter's get_next is strict-after; the cursor must be
        # strictly before the literal HH:MM to land on Mar 7).
        cursor = datetime(2026, 3, 6, 23, 59, 59, tzinfo=ny)
        fires = []
        for _ in range(4):
            nxt = compute_next_cron_fire("0 0 * * *", cursor, ny)
            if nxt is None or nxt >= datetime(2026, 3, 11, 0, 0, tzinfo=ny):
                break
            fires.append(nxt)
            cursor = nxt + __import__("datetime").timedelta(seconds=1)
        utc_fires = [f.astimezone(_stdlib_tz.utc).isoformat() for f in fires]
        assert utc_fires == [
            "2026-03-07T05:00:00+00:00",  # Mar 7 00:00 EST
            "2026-03-08T05:00:00+00:00",  # Mar 8 00:00 EST (pre-gap, single fire)
            "2026-03-09T04:00:00+00:00",  # Mar 9 00:00 EDT
            "2026-03-10T04:00:00+00:00",  # Mar 10 00:00 EDT
        ], f"unexpected 0 0 spring-forward fire sequence: {utc_fires}"
        # Specifically no in-gap phantom (00:00 Mar 8 is pre-gap).
        assert "2026-03-08T06:00:00+00:00" not in utc_fires


# ==================== Phase-5 TestCatchUpSemantics (D3) ====================


class TestCatchUpSemantics:
    """D3 catch-up rules (ADR-003).

    Landed mechanism: ``_get_next_trigger_time`` returns the
    ``PAST_LATENESS_CAP`` sentinel for a one-shot past-due beyond
    ``SchedulingConfig.one_shot_max_lateness_seconds``; the ``_run_schedule``
    loop branches on it, calls ``_record_skipped_execution`` (a SKIPPED
    ``schedule_executions`` row) and does NOT dispatch. The schedule stays
    ARMED (never disabled) so the operator can re-schedule. Cap ``None``
    (the default) = unlimited = always fire (legacy behavior preserved).

    SKELETON ADAPTATION vs the frozen §Task 1.3 skeleton: the skeleton's
    "fires on next wake via mock_on_message callback" phrasing assumed a
    DB-backed loop; the landed adapter-direct seam is the
    ``_get_next_trigger_time`` return contract + ``_record_skipped_execution``
    (the same seam the plan's own §Task 1.2 slots target). The DB-backed
    beyond-cap case runs as the Task 3.3 integration test per the plan.
    """

    CAP_ENV = "ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS"

    def _one_shot_config(self, source_id: str, run_at: datetime):
        return make_config(source_id, {
            "run_at": run_at.astimezone(timezone.utc).isoformat(),
            "agent": "./agents/developer",
            "message": "x",
        })

    def test_one_shot_within_cap_fires_late(self, mock_on_message, monkeypatch):
        """Past-due by 60s with cap=3600 → fires immediately (returns ~now)."""
        monkeypatch.setenv(self.CAP_ENV, "3600")
        now = datetime.now(timezone.utc)
        config = self._one_shot_config("catchup-within", now - timedelta(seconds=60))
        adapter = SchedulerAdapter(config, mock_on_message)
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_ONE_TIME

        next_trigger = adapter._get_next_trigger_time()
        assert next_trigger is not None
        assert next_trigger is not SchedulerAdapter.PAST_LATENESS_CAP
        # Fires NOW (legacy past-due behavior), not at the stale run_at.
        fire_lag = (next_trigger - now).total_seconds()
        assert -5.0 <= fire_lag <= 5.0, f"expected an immediate fire, lag={fire_lag}s"

    def test_one_shot_beyond_cap_no_dispatch_with_reason_marker(
        self, mock_on_message, monkeypatch
    ):
        """Past-due by 2h with cap=60s → PAST_LATENESS_CAP sentinel + SKIPPED row + stays armed."""
        monkeypatch.setenv(self.CAP_ENV, "60")
        now = datetime.now(timezone.utc)
        config = self._one_shot_config("catchup-beyond", now - timedelta(seconds=7200))
        source_repo = MagicMock()
        adapter = SchedulerAdapter(
            config, mock_on_message, source_repo=source_repo,
        )

        next_trigger = adapter._get_next_trigger_time()
        assert next_trigger is SchedulerAdapter.PAST_LATENESS_CAP
        # The stash the loop consumes for the skip record.
        assert adapter._pending_skip_lateness >= 7100
        assert adapter._pending_skip_cap == 60

        # The loop's skip branch: write the SKIPPED audit row (2-call pattern).
        adapter._record_skipped_execution(
            lateness=adapter._pending_skip_lateness, cap=adapter._pending_skip_cap
        )
        start_call = source_repo.record_execution_start.call_args
        assert start_call.kwargs["schedule_id"] == "catchup-beyond"
        assert start_call.kwargs["instance_id"] is None
        assert start_call.kwargs["execution_id"].startswith("skipped-")
        complete_call = source_repo.record_execution_complete.call_args
        assert complete_call.kwargs["status"] == "skipped"
        error_message = complete_call.kwargs["error_message"]
        assert "past_lateness_cap" in error_message
        assert "cap=60" in error_message

        # Schedule stays ARMED: no disable/status write happened (ADR-003).
        source_repo.update_source_status.assert_not_called()
        source_repo.update_source_config.assert_not_called()

    def test_one_shot_default_no_cap_always_fires(self, mock_on_message, monkeypatch):
        """Cap default ``None`` = unlimited → past-due one-shot fires (legacy behavior)."""
        monkeypatch.delenv(self.CAP_ENV, raising=False)
        now = datetime.now(timezone.utc)
        config = self._one_shot_config("catchup-nocap", now - timedelta(seconds=7200))
        adapter = SchedulerAdapter(config, mock_on_message)

        next_trigger = adapter._get_next_trigger_time()
        assert next_trigger is not None
        assert next_trigger is not SchedulerAdapter.PAST_LATENESS_CAP
        fire_lag = (next_trigger - datetime.now(timezone.utc)).total_seconds()
        assert -5.0 <= fire_lag <= 5.0

    @pytest.mark.asyncio
    async def test_w1_guard_same_run_at_re_skip_emits_one_skipped_row(
        self, mock_on_message, monkeypatch
    ):
        """W1 (once-per-run_at) guard pin: same ``run_at`` re-evaluated across
        two loop iterations → exactly ONE SKIPPED ``schedule_executions`` row.

        Adapter loop behavior (D3 + W1): a past-due one-shot beyond
        ``one_shot_max_lateness_seconds`` writes ONE SKIPPED row per
        ``run_at`` value. Re-iterating the loop with the SAME ``run_at``
        (operator hasn't updated yet) MUST NOT spam a new SKIPPED row per
        iteration — the guard ``self._last_skipped_run_at == self._run_at``
        short-circuits the record-write. Future spam-guard regression
        protection (behavioral pin only; no production code change).

        Drives the production ``_run_schedule`` loop with ``asyncio.sleep``
        patched to a no-op and ``stop_event`` triggered after the second
        iteration, then asserts ``record_execution_start.call_count == 1``.
        """
        monkeypatch.setenv(self.CAP_ENV, "60")
        now = datetime.now(timezone.utc)
        config = self._one_shot_config("w1-same", now - timedelta(seconds=7200))
        source_repo = MagicMock()
        adapter = SchedulerAdapter(
            config, mock_on_message, source_repo=source_repo,
        )
        # Patch the sleep in the adapter's loop to a no-op so iterations
        # don't block 5s each (avoids hanging the test wall-clock).
        sleep_calls = []
        async def _fake_sleep(_seconds):  # noqa: ANN001
            sleep_calls.append(_seconds)
            # Trigger the stop event after the second sleep (i.e. after
            # the second skip-iteration's W1 guard sleep) so we exit the
            # loop without infinite-running.
            if len(sleep_calls) >= 2:
                adapter._stop_event.set()
        monkeypatch.setattr(
            "daemon.sources.adapters.scheduler.asyncio.sleep",
            _fake_sleep,
        )

        await adapter._run_schedule()

        # Exactly ONE SKIPPED row across two iterations (first iteration
        # writes the row, second is guard-skipped).
        assert source_repo.record_execution_start.call_count == 1, (
            f"W1 guard failed: expected 1 SKIPPED row for unchanged "
            f"run_at, got {source_repo.record_execution_start.call_count}"
        )
        # Schedule still ARMED — no disable/status/config write happened.
        source_repo.update_source_status.assert_not_called()
        source_repo.update_source_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_w1_guard_changed_run_at_emits_second_skipped_row(
        self, mock_on_message, monkeypatch
    ):
        """W1 guard pin: when ``run_at`` CHANGES mid-loop (operator
        update simulated via ``adapter._run_at = new_run_at``), the
        second SKIPPED row is written for the NEW ``run_at`` — the
        guard resets on run_at change and lets a fresh row through.

        Drives two iterations: initial ``run_at`` → first SKIPPED row;
        then operator changes ``run_at`` to a different past-due
        timestamp; second iteration writes the second SKIPPED row.
        Pinned to ``record_execution_start.call_count == 2``.
        """
        monkeypatch.setenv(self.CAP_ENV, "60")
        now = datetime.now(timezone.utc)
        config = self._one_shot_config("w1-changed", now - timedelta(seconds=7200))
        source_repo = MagicMock()
        adapter = SchedulerAdapter(
            config, mock_on_message, source_repo=source_repo,
        )

        iteration_count = {"n": 0}
        async def _fake_sleep(_seconds):  # noqa: ANN001
            iteration_count["n"] += 1
            # After the FIRST sleep (the first SKIPPED-row path), swap
            # run_at to a different past-due value so the guard sees a
            # change and lets the second row through. Then trigger stop
            # after the SECOND sleep.
            if iteration_count["n"] == 1:
                adapter._run_at = (
                    datetime.now(timezone.utc) - timedelta(seconds=3600)
                )
            elif iteration_count["n"] >= 2:
                adapter._stop_event.set()
        monkeypatch.setattr(
            "daemon.sources.adapters.scheduler.asyncio.sleep",
            _fake_sleep,
        )

        await adapter._run_schedule()

        # Two SKIPPED rows: one per distinct run_at value.
        assert source_repo.record_execution_start.call_count == 2, (
            f"W1 guard mis-handled run_at change: expected 2 SKIPPED "
            f"rows (one per distinct run_at), got "
            f"{source_repo.record_execution_start.call_count}"
        )
        source_repo.update_source_status.assert_not_called()
        source_repo.update_source_config.assert_not_called()

    def test_cron_skips_missed(self, mock_on_message, monkeypatch):
        """Cron past-due by hours → NO catch-up fire; the next OCCURRENCE only."""
        import daemon.sources.adapters.scheduler as scheduler_module

        ny = ZoneInfo("America/New_York")
        # Today's 02:30 already passed (it's noon); a fire-now would be a catch-up.
        _FrozenDatetime._frozen = datetime(2026, 3, 8, 12, 0, tzinfo=ny)
        monkeypatch.setattr(scheduler_module, "datetime", _FrozenDatetime)

        config = make_config("catchup-cron", {
            "schedule": "30 2 * * *",
            "timezone": "America/New_York",
            "agent": "./agents/developer",
            "message": "x",
        })
        adapter = SchedulerAdapter(config, mock_on_message)

        next_trigger = adapter._get_next_trigger_time()
        assert next_trigger is not None
        # The missed 02:30 today is NOT re-fired: next occurrence is TOMORROW.
        assert next_trigger > _FrozenDatetime._frozen
        assert (next_trigger.date(), next_trigger.hour, next_trigger.minute) == (
            datetime(2026, 3, 9).date(), 2, 30
        )


# ==================== Phase-5 TestIdempotencyRestart (D4) ====================


def _make_job_repo():
    """A real in-memory ``JobRepository`` over just the ``job_queue_items`` table.

    Used as the atomic D4 substrate: ``JobQueueService.enqueue`` claims a key
    via ``JobRepository.create_or_get_by_idempotency_key`` (the partial UNIQUE
    index ``idx_job_idempotency``); funneling the adapter's emitted key through
    the SAME repository method proves the at-most-one-row guarantee without
    booting the full service stack.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from daemon.repositories.job_queue.models import JobItem

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    JobItem.__table__.create(engine, checkfirst=True)
    from daemon.repositories.job_queue import JobRepository

    return JobRepository(engine)


def _stub_manager_with_job_repo(job_repo):
    """Manager stub whose ``enqueue_message_job`` funnels into the REAL
    ``create_or_get_by_idempotency_key`` (the exact atomic claim the
    production ``JobQueueService.enqueue`` path makes for keyed enqueues).
    Returns ``(manager, mapper_repo, results)`` — ``results`` collects the
    stub's return objects (each carries the substrate-assigned ``job_id``).
    """
    import uuid

    manager = MagicMock()
    mapper_repo = MagicMock()
    mapper_repo.get_instance_mapping = MagicMock(return_value=None)
    manager.spawn_instance_with_mcp = AsyncMock(return_value="inst-idem-1")
    results: list = []

    async def _enqueue(**kwargs):
        key = kwargs.get("idempotency_key")
        job, _created = job_repo.create_or_get_by_idempotency_key(
            agent_id="developer",
            agent_dir="/agents/developer",
            message=kwargs.get("message", "x"),
            source="scheduler",
            project_id="p",
            priority=5,
            # Cron path passes NO key — mint a fresh unique key so the row
            # never collapses (mirrors production: unkeyed enqueues always
            # create fresh rows).
            idempotency_key=key if key else f"unkeyed-{uuid.uuid4()}",
        )
        result = MagicMock(job_id=job.job_id, message_id="m-1", instance_id="inst-idem-1", status="queued")
        results.append(result)
        return result

    manager.enqueue_message_job = AsyncMock(side_effect=_enqueue)
    return manager, mapper_repo, results


async def _route_once(adapter, manager, mapper_repo) -> tuple[dict, object]:
    """Drive the adapter's real inline dispatch path once.

    Returns ``(enqueue_kwargs, result)`` — the result carries the
    substrate-assigned ``job_id``.
    """
    adapter._manager = manager
    adapter._source_repo = mapper_repo
    result = await adapter._route_via_job_queue("exec-1", "scheduled message", {"agent": "./agents/developer"})
    return manager.enqueue_message_job.await_args.kwargs, result


class TestIdempotencyRestart:
    """D4 no-double-dispatch (architecture §1.2/§1.3, ADR-004).

    Pins: (a) the key is derived from ``self._run_at.isoformat()`` — stable
    across adapter re-instantiation (simulated restart) — NOT from
    ``next_trigger`` (which returns ``now`` for past-due one-shots and would
    mutate the key every cycle/boot); (b) the cron path emits NO key so
    recurring fires keep minting fresh JobItems; (c) a 5s-retry duplicate
    enqueue collapses onto the SAME JobItem row (real
    ``create_or_get_by_idempotency_key`` substrate). No ``pytest.skip`` —
    the pack is the merge gate.

    SKELETON ADAPTATION vs the frozen §Task 1.4 skeleton: the skeleton
    asserted "at-most-one JobItem lands in job_queue_items" inside this
    DB-free adapter file; the landed split is — the adapter OWNS the key
    derivation (asserted byte-for-byte here) and the queue substrate owns
    the collapse, so the duplicate-dispatch leg is proven by funneling the
    adapter's real key through the production atomic claim
    (``JobRepository.create_or_get_by_idempotency_key`` — the exact call
    ``JobQueueService.enqueue`` makes) instead of a hand-rolled stub.
    """

    RUN_AT_ISO = "2030-01-15T09:00:00+00:00"

    def _one_shot_config(self, source_id: str):
        return make_config(source_id, {
            "run_at": self.RUN_AT_ISO,
            "agent": "./agents/developer",
            "message": "x",
            "project_id": "p",
        })

    @pytest.mark.asyncio
    async def test_no_double_dispatch_after_restart(
        self, mock_on_message
    ):
        """Simulated restart: two adapters from the SAME config emit the SAME
        key (byte-for-byte ``scheduler:{source_id}:{run_at.isoformat()}``),
        and the substrate holds exactly ONE JobItem row.
        """
        job_repo = _make_job_repo()
        config = self._one_shot_config("idem-restart")

        # Two adapter instantiations from the identical persisted config —
        # exactly what a daemon restart does (config re-parsed in __init__).
        manager1, mapper_repo1, results1 = _stub_manager_with_job_repo(job_repo)
        adapter1 = SchedulerAdapter(config, mock_on_message, manager=manager1, source_repo=mapper_repo1)
        manager2, mapper_repo2, results2 = _stub_manager_with_job_repo(job_repo)
        adapter2 = SchedulerAdapter(config, mock_on_message, manager=manager2, source_repo=mapper_repo2)

        kwargs1, _r1 = await _route_once(adapter1, manager1, mapper_repo1)
        kwargs2, _r2 = await _route_once(adapter2, manager2, mapper_repo2)

        expected_key = f"scheduler:idem-restart:{self.RUN_AT_ISO}"
        assert kwargs1["idempotency_key"] == expected_key
        assert kwargs2["idempotency_key"] == expected_key

        # At-most-one JobItem landed for the key across the "restart".
        from sqlmodel import Session, select

        from daemon.repositories.job_queue.models import JobItem

        with Session(job_repo.engine) as session:
            rows = session.exec(select(JobItem)).all()
        assert len(rows) == 1, f"expected 1 JobItem after restart, got {len(rows)}"
        assert rows[0].idempotency_key == expected_key
        # Both attempts were handed the SAME (single) JobItem.
        assert results1[0].job_id == results2[0].job_id == rows[0].job_id

    @pytest.mark.asyncio
    async def test_cron_not_affected_by_idempotency_key(self, mock_on_message):
        """Architecture §1.3: cron fires emit NO key — two fires → TWO rows.

        Emitting the key unconditionally on the shared ``_route_via_job_queue``
        path would collapse every future cron fire into one JobItem → silent
        schedule death.
        """
        job_repo = _make_job_repo()
        manager, mapper_repo, _results = _stub_manager_with_job_repo(job_repo)
        config = make_config("idem-cron", {
            "schedule": "0 6 * * *",
            "agent": "./agents/developer",
            "message": "x",
            "project_id": "p",
        })
        adapter = SchedulerAdapter(config, mock_on_message, manager=manager, source_repo=mapper_repo)
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_CRON

        kwargs1 = await _route_once(adapter, manager, mapper_repo)
        # Two distinct cron cycles (distinct next_trigger values) — the second fire.
        adapter._manager = manager
        adapter._source_repo = mapper_repo
        await adapter._route_via_job_queue("exec-2", "scheduled message", {"agent": "./agents/developer"})
        kwargs2 = manager.enqueue_message_job.await_args.kwargs

        # THE gate: the cron path must not emit the key at all.
        assert "idempotency_key" not in kwargs1
        assert "idempotency_key" not in kwargs2

        # No collapse: two fires → two distinct JobItems.
        from sqlmodel import Session, select

        from daemon.repositories.job_queue.models import JobItem

        with Session(job_repo.engine) as session:
            rows = session.exec(select(JobItem)).all()
        assert len(rows) == 2
        assert rows[0].job_id != rows[1].job_id

# ───────────────────── Phase-6 TestEnqueueMessageJobDedup (D4 close) ─────────────────────


class TestEnqueueMessageJobDedupRealPath:
    """D4 acceptance: REAL ``InstanceMessagingService.enqueue_message_job``
    called TWICE with the SAME idempotency key collapses to ONE Task row,
    ONE MessageQueue row, and ONE JobItem row — with the surviving
    Task's ``work_id`` equal to the JobItem's ``job_id`` (the linkage
    contract).

    This replaces the previous ``_stub_manager_with_job_repo``-based
    substrate-only regression, which bypassed the
    ``enqueue_message_job`` seam (Task-B + MQ-B written by
    ``_prepare_enqueued_message`` BEFORE the substrate claim) and so
    could not detect the phantom-Task-B / MQ-B double-dispatch bug the
    D4 fix closes.

    Uses a real in-memory SQLite engine + real ``JobRepository`` /
    ``JobQueueRepository`` / ``TaskRepository`` / ``MessageQueueRepository``
    — the exact production stack (mirrors the fixture pattern in
    ``tests/job_queue/test_option_b_message_routing.py``).
    """

    RUN_AT_ISO = "2030-01-15T09:00:00+00:00"
    PROJECT_ID = "test-project"
    INSTANCE_ID = "inst-dedup-1"

    @pytest.fixture
    def dedup_engine(self):
        """Fresh in-memory SQLite engine with all tables registered."""
        import importlib

        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel

        # Import each model module so its SQLModel subclass registers on
        # the shared SQLModel.metadata before create_all runs.
        importlib.import_module("daemon.repositories.task.models")
        importlib.import_module("daemon.repositories.message_queue.models")
        importlib.import_module("daemon.repositories.instance.models")
        importlib.import_module("daemon.repositories.job_queue.models")
        importlib.import_module("daemon.repositories.event.models")

        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(eng)
        yield eng
        eng.dispose()

    def _build_stack(self, engine):
        """Wire the full real-messaging stack used by ``enqueue_message_job``.

        Returns ``(manager, messaging_service, job_repo, task_repo, mq_repo)``
        — ``manager`` is a ``MagicMock`` carrying the real engine,
        write_guard, and repositories that ``enqueue_message_job`` touches.
        """
        from unittest.mock import MagicMock

        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )
        from daemon.repositories.job_queue.lock_repository import LockRepository
        from daemon.repositories.job_queue.queue_repository import (
            JobQueueRepository,
        )
        from daemon.repositories.job_queue.repository import JobRepository
        from daemon.repositories.message_queue.repository import (
            SQLModelMessageQueueRepository,
        )
        from daemon.repositories.task.repository import TaskRepository
        from daemon.services.cancellation import CancellationService
        from daemon.services.job_lock_manager import JobLockManager
        from daemon.services.job_queue_service import JobQueueService
        from daemon.write_pause_guard import WritePauseGuard

        instance_repo = SQLModelInstanceRepository(engine)
        job_repo = JobRepository(engine)
        lock_repo = LockRepository(engine)
        queue_repo = JobQueueRepository(engine)
        task_repo = TaskRepository(engine)
        mq_repo = SQLModelMessageQueueRepository(engine)
        jq_service = JobQueueService(
            job_repo, JobLockManager(lock_repo=lock_repo), queue_repo
        )
        jq_service._project_repo = None

        # Seed the system_parallel_queue enqueue_message_job falls back to
        # when no queue_id is supplied (default path).
        queue_repo.create(
            project_id=self.PROJECT_ID,
            queue_name="system_parallel_queue",
            queue_type="parallel",
            concurrency_limit=3,
            is_system=True,
        )
        queue_repo.create(
            project_id=self.PROJECT_ID,
            queue_name="system_fifo_queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )

        # Seed the target Instance row.
        from sqlmodel import Session

        from daemon.repositories.instance.models import Instance, InstanceStatus

        inst = Instance(
            instance_id=self.INSTANCE_ID,
            agent_id="developer",
            agent_dir="agents/developer",
            project_id=self.PROJECT_ID,
            status=InstanceStatus.IDLE.value,
            version=1,
            instance_metadata={},
        )
        with Session(engine) as session:
            session.add(inst)
            session.commit()
            session.refresh(inst)

        # Mock manager facade carrying the real repo references the
        # ``enqueue_message_job`` code path reads through.
        manager = MagicMock()
        manager.engine = engine
        manager.write_guard = WritePauseGuard()
        manager._instance_repository = instance_repo
        manager._queue_repository = mq_repo
        manager._project_repository = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._worker_pool = MagicMock()
        manager._worker_pool.notify_work = MagicMock()
        manager._job_queue_service = jq_service
        manager._task_repo = task_repo
        manager._generate_and_broadcast_title = MagicMock()

        from daemon.services.instance_messaging import InstanceMessagingService

        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(
                spec=CancellationService, is_shutting_down=False
            ),
        )
        return (
            manager,
            messaging_service,
            job_repo,
            task_repo,
            mq_repo,
            queue_repo,
        )

    @pytest.mark.asyncio
    async def test_dedup_collapse_real_path(self, dedup_engine):
        """REAL ``enqueue_message_job`` × 2 with the SAME idempotency key
        collapses to ONE Task + ONE MQ + ONE JobItem with work_id ==
        job_id. The second call returns the REAL JobItem's id.
        """
        (
            _manager,
            svc,
            job_repo,
            task_repo,
            mq_repo,
            _queue_repo,
        ) = self._build_stack(dedup_engine)

        key = f"scheduler:dedup-realpath:{self.RUN_AT_ISO}"

        result1 = await svc.enqueue_message_job(
            instance_id=self.INSTANCE_ID,
            message="scheduled message",
            source="scheduler",
            idempotency_key=key,
        )
        result2 = await svc.enqueue_message_job(
            instance_id=self.INSTANCE_ID,
            message="scheduled message",
            source="scheduler",
            idempotency_key=key,
        )

        # The substrate collapsed: exactly ONE JobItem row for the key.
        from sqlmodel import Session, select

        from daemon.repositories.job_queue.models import JobItem
        from daemon.repositories.message_queue.models import MessageQueue
        from daemon.repositories.task.models import Task

        with Session(dedup_engine) as session:
            job_rows = session.exec(select(JobItem)).all()
            task_rows = session.exec(select(Task)).all()
            mq_rows = session.exec(select(MessageQueue)).all()
        assert len(job_rows) == 1, (
            f"expected 1 JobItem after dedup, got {len(job_rows)}"
        )
        # Exactly ONE live (PENDING) Task — the real Task-A. The phantom
        # Task-B was cancelled by the D4 compensation (its row survives
        # as CANCELLED for audit; cancel_task routes through the
        # turn-reconciler wrapper, which preserves the row).
        pending_tasks = [
            t for t in task_rows if t.status == "pending"
        ]
        assert len(pending_tasks) == 1, (
            f"expected 1 PENDING Task after dedup (no phantom), got "
            f"{len(pending_tasks)}: "
            f"{[(t.id, t.work_id, t.status) for t in task_rows]}"
        )
        # Any non-PENDING Task row (the cancelled phantom) must be
        # terminal CANCELLED — never PENDING, RUNNING, or PAUSED.
        non_pending = [
            t for t in task_rows if t.status != "pending"
        ]
        for phantom in non_pending:
            assert phantom.status == "cancelled", (
                f"phantom Task-{phantom.id} must be CANCELLED after the "
                f"D4 compensation, got {phantom.status}"
            )
        assert len(mq_rows) == 1, (
            f"expected 1 MessageQueue after dedup (no orphan), got {len(mq_rows)}"
        )

        # Linkage contract: Task.work_id == JobItem.job_id on the
        # SURVIVING (PENDING) Task row.
        surviving_task = pending_tasks[0]
        surviving_job = job_rows[0]
        assert surviving_task.work_id == surviving_job.job_id, (
            "Task.work_id must equal JobItem.job_id (linkage contract)"
        )

        # The SECOND call returns the REAL JobItem id, not a phantom UUID.
        assert result1.job_id == surviving_job.job_id
        assert result2.job_id == surviving_job.job_id, (
            "dedup hit must return the REAL JobItem id (not the phantom UUID)"
        )

        # Cross-system correlation: JobItem.metadata.message_id points at
        # the surviving MQ row (the ORIGINAL message from the FIRST call).
        stamped_id = (surviving_job.job_metadata or {}).get("message_id")
        assert stamped_id == surviving_task.message_id, (
            f"JobItem.metadata.message_id must match the surviving "
            f"Task.message_id (cross-system correlation); "
            f"stamped={stamped_id}, task={surviving_task.message_id}"
        )

    @pytest.mark.asyncio
    async def test_dedup_crash_window_no_orphan_rows_survive(self, dedup_engine):
        """Crash-window variant: SECOND call hits dedup and its compensation
        runs. No orphaned PENDING Task or MQ row survives.

        Mirrors the production dedup-hit path directly: the first call
        completes fully (JobItem-A + Task-A + MQ-A persist); the second
        call runs the prelude (Task-B + MQ-B are written), then the
        substrate hits the partial UNIQUE index and returns JobItem-A —
        the D4 compensation must cancel Task-B and delete MQ-B so no
        orphan survives.
        """
        (
            _manager,
            svc,
            job_repo,
            task_repo,
            mq_repo,
            _queue_repo,
        ) = self._build_stack(dedup_engine)

        key = f"scheduler:dedup-crashwin:{self.RUN_AT_ISO}"

        # First call — full path (writes + substrate insert + stamp).
        result1 = await svc.enqueue_message_job(
            instance_id=self.INSTANCE_ID,
            message="scheduled message",
            source="scheduler",
            idempotency_key=key,
        )
        job_a_id = result1.job_id
        mq_a_id = result1.message_id

        # Second call — hits the dedup path. Compensation runs.
        result2 = await svc.enqueue_message_job(
            instance_id=self.INSTANCE_ID,
            message="scheduled message",
            source="scheduler",
            idempotency_key=key,
        )

        from sqlmodel import Session, select

        from daemon.repositories.job_queue.models import JobItem
        from daemon.repositories.message_queue.models import MessageQueue
        from daemon.repositories.task.models import Task, TaskStatus

        with Session(dedup_engine) as session:
            job_rows = session.exec(
                select(JobItem).where(JobItem.idempotency_key == key)
            ).all()
            task_rows = session.exec(select(Task)).all()
            mq_rows = session.exec(select(MessageQueue)).all()

        # No phantom JobItem: only JobItem-A exists for the key.
        assert len(job_rows) == 1
        assert job_rows[0].job_id == job_a_id

        # No orphaned PENDING Task survives compensation. Any task row
        # that is NOT the surviving Task-A must be terminal (CANCELLED).
        surviving = [
            t for t in task_rows
            if t.status == TaskStatus.PENDING.value
        ]
        assert len(surviving) == 1, (
            f"exactly ONE PENDING Task must survive the crash-window "
            f"dedup; got {len(surviving)}: "
            f"{[(t.id, t.work_id, t.status) for t in task_rows]}"
        )
        assert surviving[0].work_id == job_a_id, (
            "the surviving PENDING Task must be Task-A (work_id == "
            f"JobItem-A.job_id == {job_a_id}); work_id="
            f"{surviving[0].work_id}"
        )

        # Any non-surviving Task row (if cancel_task flipped a phantom)
        # must be terminal CANCELLED — no phantom is PENDING or RUNNING.
        terminal = [
            t for t in task_rows
            if t.id != surviving[0].id
        ]
        for phantom in terminal:
            assert phantom.status == TaskStatus.CANCELLED.value, (
                f"phantom Task-{phantom.id} must be CANCELLED, got "
                f"{phantom.status}"
            )

        # No orphaned MessageQueue row: only MQ-A survives.
        assert len(mq_rows) == 1, (
            f"exactly ONE MessageQueue row must survive the crash-window "
            f"dedup; got {len(mq_rows)}"
        )
        assert mq_rows[0].message_id == mq_a_id

        # The SECOND call returns the REAL JobItem-A id.
        assert result2.job_id == job_a_id, (
            "dedup hit must return the REAL JobItem id, not the phantom"
        )

    @pytest.mark.asyncio
    async def test_dedup_compensation_cancels_running_task(self, dedup_engine):
        """Crash-window variant (already-claimed): the phantom Task-B is
        claimed (flipped RUNNING) between the prelude commit and the
        dedup-detect — the exact production race window the
        compensation's own comment names ("worker-pool claim between
        prelude and enqueue"). The fixture forces RUNNING as the
        prior-status to model that production race; the assertion then
        pins the OUTCOME (Task-B lands CANCELLED, row preserved) rather
        than the specific cancel_task branch selection.

        Mechanism (reviewer option b — real flip, no rename): the
        substrate ``enqueue`` call is wrapped by a passive observer —
        the REAL substrate still runs (the partial-UNIQUE-index dedup
        hit is real). After the real enqueue returns (prelude long
        since committed), the wrapper flips the ONE PENDING Task whose
        ``work_id != JobItem-A.job_id`` to RUNNING via a direct session
        write, before the dedup-detect reads the status. In this
        fixture that wrapper IS the worker-pool stand-in (no real
        worker pool exists to win the claim race), and the phantom
        singleton is asserted BEFORE the flip so the test stays honest.
        """
        (
            manager,
            svc,
            job_repo,
            task_repo,
            mq_repo,
            _queue_repo,
        ) = self._build_stack(dedup_engine)

        key = f"scheduler:dedup-running:{self.RUN_AT_ISO}"

        # First call — completes normally (JobItem-A + Task-A + MQ-A).
        result1 = await svc.enqueue_message_job(
            instance_id=self.INSTANCE_ID,
            message="scheduled message",
            source="scheduler",
            idempotency_key=key,
        )
        job_a_id = result1.job_id

        from sqlmodel import Session, col, select

        from daemon.repositories.task.models import Task

        # Worker-pool stand-in: wrap the REAL substrate enqueue (no
        # stubbing — the dedup hit below is the real partial UNIQUE
        # index collision). The flip lands inside the production race
        # window: after the substrate claim returns, BEFORE the
        # dedup-detect reads Task-B's status.
        jq_service = manager._job_queue_service
        real_enqueue = jq_service.enqueue
        flip_observations: dict = {}

        async def claim_race_flip(*args, **kwargs):
            result = await real_enqueue(*args, **kwargs)
            with Session(dedup_engine) as session:
                # The phantom set: PENDING tasks NOT linked to
                # JobItem-A. In this fixture it must be exactly
                # {Task-B} — assert the singleton BEFORE flipping so
                # the test cannot silently flip an unexpected row.
                phantoms = session.exec(
                    select(Task).where(
                        col(Task.status) == "pending",
                        col(Task.work_id) != job_a_id,
                    )
                ).all()
                assert len(phantoms) == 1, (
                    "expected exactly ONE phantom PENDING Task (Task-B) "
                    f"before the claim-race flip, got {len(phantoms)}: "
                    f"{[(t.id, t.work_id, t.status) for t in phantoms]}"
                )
                task_b = phantoms[0]
                flip_observations["task_b_id"] = task_b.id
                flip_observations["task_b_work_id"] = task_b.work_id
                # The claim race fires: PENDING → RUNNING — the same
                # direct status write the worker pool's
                # claim_pending_task commits.
                task_b.status = "running"
                session.add(task_b)
                session.commit()
                # Prove the flip landed BEFORE the compensation runs —
                # Task-B is now RUNNING so the compensation observes
                # the production race window's prior-status (the test
                # only pins the OUTCOME, not the specific cancel
                # branch).
                session.refresh(task_b)
                assert task_b.status == "running", (
                    "Task-B must be RUNNING before the dedup-detect "
                    f"reads it; got {task_b.status}"
                )
            return result

        jq_service.enqueue = claim_race_flip

        # Second call — the prelude writes Task-B + MQ-B, the REAL
        # substrate hits the partial UNIQUE index and returns JobItem-A,
        # the wrapper flips Task-B RUNNING, then the dedup-detect fires
        # and the compensation cancels Task-B.
        result2 = await svc.enqueue_message_job(
            instance_id=self.INSTANCE_ID,
            message="scheduled message",
            source="scheduler",
            idempotency_key=key,
        )

        from daemon.repositories.job_queue.models import JobItem
        from daemon.repositories.message_queue.models import MessageQueue
        from daemon.repositories.task.models import TaskStatus

        task_b_id = flip_observations["task_b_id"]
        task_b_work_id = flip_observations["task_b_work_id"]

        with Session(dedup_engine) as session:
            job_rows = session.exec(select(JobItem)).all()
            task_rows = session.exec(select(Task)).all()
            mq_rows = session.exec(select(MessageQueue)).all()

        # Only ONE JobItem (JobItem-A) — no phantom JobItem survives.
        assert len(job_rows) == 1, (
            f"expected 1 JobItem after dedup, got {len(job_rows)}"
        )
        assert job_rows[0].job_id == job_a_id

        # RUNNING-branch contract: the already-claimed phantom Task-B
        # must land CANCELLED — NOT deleted. cancel_task preserves
        # the row (its hot path on a RUNNING prior-status selects the
        # row-preserving UPDATE, not DELETE); the assertion below
        # pins the OUTCOME (CANCELLED status, row preserved), not
        # the specific branch path.
        task_b_row = next(t for t in task_rows if t.id == task_b_id)
        assert task_b_row.status == TaskStatus.CANCELLED.value, (
            "the RUNNING phantom Task-B must land CANCELLED — row "
            "preserved, not deleted; got "
            f"{task_b_row.status}"
        )
        assert task_b_row.work_id == task_b_work_id

        # Exactly 2 Task rows: Task-A + the cancelled Task-B.
        assert len(task_rows) == 2, (
            "expected exactly 2 Task rows (Task-A + cancelled Task-B), "
            f"got {len(task_rows)}: "
            f"{[(t.id, t.work_id, t.status) for t in task_rows]}"
        )

        # Task-A untouched: still PENDING, still linked to JobItem-A
        # (linkage contract work_id == job_id on the surviving task).
        task_a_row = next(t for t in task_rows if t.work_id == job_a_id)
        assert task_a_row.status == TaskStatus.PENDING.value, (
            "Task-A must be untouched by the compensation; got "
            f"{task_a_row.status}"
        )
        assert task_a_row.work_id == job_rows[0].job_id, (
            "Task.work_id must equal JobItem.job_id (linkage contract)"
        )

        # MQ-B deleted by the compensation: only MQ-A survives.
        assert len(mq_rows) == 1, (
            f"expected 1 MessageQueue after dedup (MQ-B deleted), got "
            f"{len(mq_rows)}"
        )
        assert mq_rows[0].message_id == result1.message_id

        # Second call returns JobItem-A's id.
        assert result2.job_id == job_a_id

    @pytest.mark.asyncio
    async def test_no_idempotency_key_two_calls_create_two_jobs(self, dedup_engine):
        """No-key guard: two ``enqueue_message_job`` calls WITHOUT an
        idempotency key are fully independent — exactly 2 JobItems, 2
        Tasks (both PENDING, distinct work_ids each linked to its own
        JobItem), and 2 MessageQueue rows — and NO compensation fires
        (zero cancelled tasks, both MQ rows intact).

        Pins the dedup-hit gate's no-key branch: the gate requires
        ``idempotency_key is not None``. A future refactor that drops
        that guard would route no-key second calls into the
        phantom-compensation path — cancelling the second Task and
        deleting the second MQ row — and trip this test.
        """
        (
            _manager,
            svc,
            job_repo,
            task_repo,
            mq_repo,
            _queue_repo,
        ) = self._build_stack(dedup_engine)

        result1 = await svc.enqueue_message_job(
            instance_id=self.INSTANCE_ID,
            message="scheduled message",
            source="scheduler",
            idempotency_key=None,
        )
        result2 = await svc.enqueue_message_job(
            instance_id=self.INSTANCE_ID,
            message="scheduled message",
            source="scheduler",
            idempotency_key=None,
        )

        from sqlmodel import Session, select

        from daemon.repositories.job_queue.models import JobItem
        from daemon.repositories.message_queue.models import MessageQueue
        from daemon.repositories.task.models import Task, TaskStatus

        with Session(dedup_engine) as session:
            job_rows = session.exec(select(JobItem)).all()
            task_rows = session.exec(select(Task)).all()
            mq_rows = session.exec(select(MessageQueue)).all()

        # Exactly 2 independent JobItems — no dedup collapse.
        assert len(job_rows) == 2, (
            f"expected 2 JobItems without an idempotency key, got "
            f"{len(job_rows)}"
        )
        job_ids = {j.job_id for j in job_rows}
        assert result1.job_id != result2.job_id, (
            "two no-key calls must mint distinct work_ids"
        )
        assert job_ids == {result1.job_id, result2.job_id}

        # Exactly 2 Tasks, both still PENDING — NO compensation fired.
        assert len(task_rows) == 2, (
            f"expected 2 Task rows without an idempotency key, got "
            f"{len(task_rows)}: "
            f"{[(t.id, t.work_id, t.status) for t in task_rows]}"
        )
        for t in task_rows:
            assert t.status == TaskStatus.PENDING.value, (
                "no compensation may fire without an idempotency key; "
                f"Task-{t.id} is {t.status}"
            )

        # Distinct work_ids, each linked to its own JobItem (linkage
        # contract on BOTH rows).
        task_work_ids = {t.work_id for t in task_rows}
        assert len(task_work_ids) == 2, "work_ids must be distinct"
        assert task_work_ids == job_ids, (
            "each Task.work_id must equal its JobItem.job_id (linkage "
            "contract on both rows)"
        )

        # Exactly 2 MessageQueue rows — the second MQ row was NOT
        # deleted by any compensation.
        assert len(mq_rows) == 2, (
            f"expected 2 MessageQueue rows without an idempotency key, "
            f"got {len(mq_rows)}"
        )
        assert {m.message_id for m in mq_rows} == {
            result1.message_id,
            result2.message_id,
        }
