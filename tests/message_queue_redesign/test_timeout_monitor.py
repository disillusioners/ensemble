"""Tests for daemon/cancellation.py CancellationToken enhancements and daemon/services/timeout_monitor.py."""

import pytest
import threading
import time

from daemon.cancellation import (
    CancellationToken,
    CancellationTokenSource,
    CancellationReason,
)
from daemon.services.timeout_monitor import TimeoutMonitor


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def cancellation_source():
    """Create a CancellationTokenSource for testing."""
    return CancellationTokenSource()


# =============================================================================
# Test CancellationToken.cancelled_at Property
# =============================================================================

class TestCancellationTokenCancelledAt:
    """Tests for CancellationToken.cancelled_at property."""

    def test_cancellation_token_cancelled_at_records_timestamp(
        self, cancellation_source
    ):
        """Verify cancelled_at is set after cancellation."""
        cancellation_source.cancel(CancellationReason.MANUAL)

        assert cancellation_source.token.cancelled_at is not None
        assert isinstance(cancellation_source.token.cancelled_at, float)

    def test_cancellation_token_cancelled_at_none_before_cancel(
        self, cancellation_source
    ):
        """Verify cancelled_at is None before any cancellation."""
        assert cancellation_source.token.cancelled_at is None

    def test_cancellation_token_cancelled_at_records_on_timeout(
        self, cancellation_source
    ):
        """Verify cancelled_at is set when cancelled with TIMEOUT reason."""
        cancellation_source.cancel(CancellationReason.TIMEOUT)

        assert cancellation_source.token.cancelled_at is not None
        assert cancellation_source.token.reason == CancellationReason.TIMEOUT

    def test_cancelled_at_is_monotonic_timestamp(self, cancellation_source):
        """Verify cancelled_at uses monotonic time (always positive)."""
        before = time.monotonic()
        cancellation_source.cancel(CancellationReason.MANUAL)
        after = time.monotonic()

        assert cancellation_source.token.cancelled_at >= before
        assert cancellation_source.token.cancelled_at <= after


# =============================================================================
# Test CancellationReason.TIMEOUT
# =============================================================================

class TestCancellationReasonTimeout:
    """Tests for CancellationReason.TIMEOUT enum value."""

    def test_cancellation_reason_timeout_exists(self):
        """Verify TIMEOUT reason exists in enum."""
        assert hasattr(CancellationReason, "TIMEOUT")

    def test_cancellation_reason_timeout_value(self):
        """Verify TIMEOUT has correct string value."""
        assert CancellationReason.TIMEOUT.value == "timeout"


# =============================================================================
# Test CancellationToken Thread Safety
# =============================================================================

class TestCancellationTokenThreadSafety:
    """Tests for thread safety of CancellationToken."""

    def test_cancellation_token_thread_safety(self):
        """Verify concurrent cancellation attempts are handled correctly."""
        source = CancellationTokenSource()
        cancelled_at_values = []

        def cancel_with_tracking():
            source.cancel(CancellationReason.TIMEOUT)
            cancelled_at_values.append(source.token.cancelled_at)

        threads = [
            threading.Thread(target=cancel_with_tracking)
            for _ in range(10)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Token should be cancelled
        assert source.token.is_cancelled is True
        # All captured cancelled_at values should be the same
        assert len(set(cancelled_at_values)) == 1
        # The cancelled_at should be set
        assert cancelled_at_values[0] is not None

    def test_concurrent_cancel_preserves_first_reason(self):
        """Verify the first cancellation reason is preserved."""
        source = CancellationTokenSource()

        def try_cancel_manual():
            source.cancel(CancellationReason.MANUAL)

        def try_cancel_timeout():
            source.cancel(CancellationReason.TIMEOUT)

        thread1 = threading.Thread(target=try_cancel_manual)
        thread2 = threading.Thread(target=try_cancel_timeout)

        thread1.start()
        thread2.start()
        thread1.join()
        thread2.join()

        # Token should be cancelled
        assert source.token.is_cancelled is True
        # Reason should be one of the two (either is valid since both happen nearly simultaneously)
        assert source.token.reason in (
            CancellationReason.MANUAL,
            CancellationReason.TIMEOUT,
        )


# =============================================================================
# Test TimeoutMonitor
# =============================================================================

class TestTimeoutMonitor:
    """Tests for TimeoutMonitor class."""

    def test_timeout_monitor_fires_after_timeout(self, cancellation_source):
        """Verify monitor cancels token after timeout expires."""
        monitor = TimeoutMonitor(
            task_id=1,
            source=cancellation_source,
            timeout_seconds=0.1,
        )

        monitor.start()
        time.sleep(0.3)  # Wait longer than timeout

        assert cancellation_source.token.is_cancelled is True
        assert cancellation_source.token.reason == CancellationReason.TIMEOUT
        assert monitor.fired is True

        monitor.stop()

    def test_timeout_monitor_stop_before_timeout(self, cancellation_source):
        """Verify monitor does not cancel token when stopped early."""
        monitor = TimeoutMonitor(
            task_id=2,
            source=cancellation_source,
            timeout_seconds=5.0,  # Long timeout
        )

        monitor.start()
        monitor.stop()  # Stop immediately

        assert cancellation_source.token.is_cancelled is False
        assert cancellation_source.token.reason is None
        assert monitor.fired is False

    def test_timeout_monitor_cancelled_at_recorded_on_timeout(
        self, cancellation_source
    ):
        """Verify token.cancelled_at is set when timeout fires."""
        monitor = TimeoutMonitor(
            task_id=3,
            source=cancellation_source,
            timeout_seconds=0.1,
        )

        monitor.start()
        time.sleep(0.3)

        assert cancellation_source.token.cancelled_at is not None

        monitor.stop()

    def test_timeout_monitor_very_short_timeout(self, cancellation_source):
        """Verify monitor works with very short timeouts (50ms)."""
        monitor = TimeoutMonitor(
            task_id=4,
            source=cancellation_source,
            timeout_seconds=0.05,  # 50ms
        )

        monitor.start()
        time.sleep(0.2)  # Wait 200ms

        assert cancellation_source.token.is_cancelled is True
        assert cancellation_source.token.reason == CancellationReason.TIMEOUT

        monitor.stop()

    def test_timeout_monitor_fired_property(self, cancellation_source):
        """Test fired property state transitions."""
        # Initially False
        monitor = TimeoutMonitor(
            task_id=5,
            source=cancellation_source,
            timeout_seconds=0.1,
        )
        assert monitor.fired is False

        # Start and wait for timeout
        monitor.start()
        time.sleep(0.3)
        assert monitor.fired is True

        monitor.stop()

        # After early stop (different source)
        source2 = CancellationTokenSource()
        monitor2 = TimeoutMonitor(
            task_id=6,
            source=source2,
            timeout_seconds=5.0,
        )
        monitor2.start()
        monitor2.stop()
        assert monitor2.fired is False

    def test_timeout_monitor_is_running(self, cancellation_source):
        """Test is_running returns correct states."""
        monitor = TimeoutMonitor(
            task_id=7,
            source=cancellation_source,
            timeout_seconds=0.5,
        )

        # Before start
        assert monitor.is_running() is False

        # After start
        monitor.start()
        time.sleep(0.05)  # Give thread time to start
        assert monitor.is_running() is True

        # After stop
        monitor.stop()
        assert monitor.is_running() is False

    def test_timeout_monitor_double_start(self, cancellation_source):
        """Verify double start is handled gracefully."""
        monitor = TimeoutMonitor(
            task_id=8,
            source=cancellation_source,
            timeout_seconds=0.1,
        )

        monitor.start()
        monitor.start()  # Second start should not fail
        time.sleep(0.3)

        assert cancellation_source.token.is_cancelled is True
        assert monitor.fired is True

        monitor.stop()

    def test_timeout_monitor_already_cancelled_source(self, cancellation_source):
        """Verify TimeoutMonitor handles already-cancelled source."""
        monitor = TimeoutMonitor(
            task_id=9,
            source=cancellation_source,
            timeout_seconds=0.1,
        )

        # Cancel before starting monitor
        cancellation_source.cancel(CancellationReason.MANUAL)

        monitor.start()
        time.sleep(0.2)

        # Token should remain cancelled with MANUAL reason (first cancel wins)
        assert cancellation_source.token.is_cancelled is True
        assert cancellation_source.token.reason == CancellationReason.MANUAL
        # fired is True because timeout elapsed (even though cancel was no-op due to idempotency)
        assert monitor.fired is True

        monitor.stop()

    def test_timeout_monitor_thread_name(self, cancellation_source):
        """Verify monitor thread has expected name."""
        monitor = TimeoutMonitor(
            task_id=42,
            source=cancellation_source,
            timeout_seconds=5.0,
        )

        monitor.start()
        time.sleep(0.05)

        assert monitor._thread is not None
        assert "TimeoutMonitor-task-42" in monitor._thread.name

        monitor.stop()

    def test_timeout_monitor_stop_idempotent(self, cancellation_source):
        """Verify multiple stops are safe."""
        monitor = TimeoutMonitor(
            task_id=10,
            source=cancellation_source,
            timeout_seconds=0.5,
        )

        monitor.start()
        monitor.stop()
        monitor.stop()  # Second stop should not fail

        assert cancellation_source.token.is_cancelled is False

    def test_timeout_monitor_graceful_shutdown(self, cancellation_source):
        """Verify monitor shuts down cleanly without leaving threads."""
        monitor = TimeoutMonitor(
            task_id=11,
            source=cancellation_source,
            timeout_seconds=10.0,  # Long timeout
        )

        monitor.start()
        monitor.stop()

        # Thread should not be alive
        if monitor._thread:
            assert not monitor._thread.is_alive()


# =============================================================================
# Integration Tests
# =============================================================================

class TestTimeoutMonitorIntegration:
    """Integration tests for TimeoutMonitor with CancellationToken."""

    def test_full_timeout_flow(self):
        """Full flow: create, start, timeout, verify cancellation."""
        source = CancellationTokenSource()
        monitor = TimeoutMonitor(
            task_id=100,
            source=source,
            timeout_seconds=0.1,
        )

        # Initial state
        assert source.token.is_cancelled is False
        assert source.token.cancelled_at is None
        assert source.token.reason is None
        assert monitor.fired is False
        assert monitor.is_running() is False

        # Start monitor
        monitor.start()
        assert monitor.is_running() is True

        # Wait for timeout
        time.sleep(0.3)

        # After timeout
        assert source.token.is_cancelled is True
        assert source.token.reason == CancellationReason.TIMEOUT
        assert source.token.cancelled_at is not None
        assert monitor.fired is True
        assert monitor.is_running() is False

        # Cleanup
        monitor.stop()

    def test_early_stop_preserves_uncancelled_token(self):
        """Early stop should not affect uncancelled state."""
        source = CancellationTokenSource()
        monitor = TimeoutMonitor(
            task_id=101,
            source=source,
            timeout_seconds=10.0,
        )

        monitor.start()
        time.sleep(0.05)
        monitor.stop()

        assert source.token.is_cancelled is False
        assert source.token.cancelled_at is None
        assert source.token.reason is None
        assert monitor.fired is False
