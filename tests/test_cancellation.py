"""Tests for daemon/cancellation.py and daemon/request_registry.py."""

import pytest
import asyncio
import threading
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock
import concurrent.futures

from daemon.cancellation import (
    CancellationToken,
    CancellationTokenSource,
    CancellationReason,
    OperationCancelledError,
)
from daemon.request_registry import (
    ActiveRequestRegistry,
    ActiveRequest,
)


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def cancellation_source():
    """Create a CancellationTokenSource for testing."""
    return CancellationTokenSource()


@pytest.fixture
def request_registry():
    """Create an ActiveRequestRegistry for testing."""
    return ActiveRequestRegistry()


@pytest.fixture
def mock_asyncio_task():
    """Create a mock asyncio.Task for testing."""
    task = MagicMock(spec=asyncio.Task)
    task.done.return_value = False
    loop = MagicMock()
    loop.is_running.return_value = True
    task.get_loop.return_value = loop
    return task


# =============================================================================
# Test CancellationReason
# =============================================================================

class TestCancellationReason:
    """Tests for CancellationReason enum."""

    def test_reason_values(self):
        """Verify enum values exist."""
        assert CancellationReason.TIMEOUT.value == "timeout"
        assert CancellationReason.WATCHDOG_RETRY.value == "watchdog_retry"
        assert CancellationReason.MANUAL.value == "manual"
        assert CancellationReason.SHUTDOWN.value == "shutdown"
        assert CancellationReason.SESSION_TERMINATED.value == "session_terminated"

    def test_all_reasons_defined(self):
        """Ensure no missing reasons."""
        reasons = list(CancellationReason)
        assert len(reasons) == 6

    def test_user_stopped_reason(self):
        """Verify USER_STOPPED enum exists and has correct value."""
        assert CancellationReason.USER_STOPPED.value == "user_stopped"


class TestRequestRegistryCancelByInstance:
    """Tests for request_registry.cancel_by_instance reason forwarding."""
    
    def test_cancel_by_instance_passes_reason(self, request_registry):
        """Verify cancel_by_instance passes the reason to individual cancels."""
        # Register two requests for the same instance
        request_registry.register("msg-1", "instance-1")
        request_registry.register("msg-2", "instance-1")
        
        # Cancel by instance with USER_STOPPED reason
        cancelled_count = request_registry.cancel_by_instance(
            "instance-1", 
            CancellationReason.USER_STOPPED
        )
        
        # Should have cancelled both
        assert cancelled_count == 2
        
        # Verify the tokens have the USER_STOPPED reason
        req1 = request_registry.get_request("msg-1")
        req2 = request_registry.get_request("msg-2")
        assert req1.cancellation_source.token.reason == CancellationReason.USER_STOPPED
        assert req2.cancellation_source.token.reason == CancellationReason.USER_STOPPED


class TestManagerCancelInstanceRequests:
    """Tests for manager.cancel_instance_requests."""
    
    def test_cancel_instance_requests_returns_count(self):
        """Verify cancel_instance_requests returns the count of cancelled."""
        from unittest.mock import Mock
        from daemon.cancellation import CancellationReason
        from daemon.manager import InstanceManager
        
        # Create manager with basic attributes
        manager = InstanceManager.__new__(InstanceManager)
        
        # Mock the cancellation service
        mock_cancellation_service = Mock()
        mock_cancellation_service.cancel_instance_requests.return_value = 2
        manager._cancellation_service = mock_cancellation_service
        
        result = manager.cancel_instance_requests("instance-1", CancellationReason.USER_STOPPED)
        
        assert result == 2
        mock_cancellation_service.cancel_instance_requests.assert_called_once_with(
            "instance-1", CancellationReason.USER_STOPPED
        )


# =============================================================================
# Test OperationCancelledError
# =============================================================================

class TestOperationCancelledError:
    """Tests for OperationCancelledError exception."""

    def test_error_with_reason(self):
        """Basic error construction."""
        error = OperationCancelledError(CancellationReason.TIMEOUT)
        assert error.reason == CancellationReason.TIMEOUT
        assert "timeout" in error.message

    def test_error_default_message(self):
        """Auto-generated message."""
        error = OperationCancelledError(CancellationReason.WATCHDOG_RETRY)
        assert error.message == "Operation cancelled: watchdog_retry"

    def test_error_custom_message(self):
        """Custom message override."""
        error = OperationCancelledError(
            CancellationReason.MANUAL, 
            "Custom error message"
        )
        assert error.message == "Custom error message"
        assert error.reason == CancellationReason.MANUAL

    def test_error_inheritance(self):
        """Is an Exception."""
        error = OperationCancelledError(CancellationReason.SHUTDOWN)
        assert isinstance(error, Exception)


# =============================================================================
# Test CancellationToken
# =============================================================================

class TestCancellationToken:
    """Tests for CancellationToken class."""

    def test_initial_state_not_cancelled(self):
        """Fresh token is not cancelled."""
        token = CancellationToken()
        assert token.is_cancelled is False

    def test_check_raises_when_cancelled(self):
        """check() raises error when cancelled."""
        token = CancellationToken()
        token._cancelled.set()
        token._reason = CancellationReason.MANUAL

        with pytest.raises(OperationCancelledError) as exc_info:
            token.check()

        assert exc_info.value.reason == CancellationReason.MANUAL

    def test_check_no_raise_when_not_cancelled(self):
        """check() passes when not cancelled."""
        token = CancellationToken()
        # Should not raise
        token.check()

    def test_async_check_same_as_sync(self):
        """async_check() behaves like check()."""
        token = CancellationToken()
        token._cancelled.set()
        token._reason = CancellationReason.TIMEOUT

        async def run_test():
            with pytest.raises(OperationCancelledError) as exc_info:
                await token.async_check()
            assert exc_info.value.reason == CancellationReason.TIMEOUT

        asyncio.run(run_test())

    def test_reason_none_initially(self):
        """No reason before cancellation."""
        token = CancellationToken()
        assert token.reason is None

    def test_wait_for_cancellation_returns_true(self):
        """Wait returns when cancelled."""
        token = CancellationToken()
        source = CancellationTokenSource()
        source._token = token

        def cancel_after_delay():
            time.sleep(0.05)
            source.cancel(CancellationReason.MANUAL)

        thread = threading.Thread(target=cancel_after_delay)
        thread.start()

        result = token.wait_for_cancellation(timeout=1.0)
        thread.join()

        assert result is True

    def test_wait_for_cancellation_timeout(self):
        """Wait times out correctly."""
        token = CancellationToken()
        result = token.wait_for_cancellation(timeout=0.1)
        assert result is False

    def test_thread_safety_is_cancelled(self):
        """Concurrent reads are safe."""
        token = CancellationToken()
        source = CancellationTokenSource()
        source._token = token
        results = []

        def read_is_cancelled():
            for _ in range(100):
                results.append(token.is_cancelled)
                time.sleep(0.001)

        threads = [threading.Thread(target=read_is_cancelled) for _ in range(5)]
        for t in threads:
            t.start()

        time.sleep(0.02)
        source.cancel(CancellationReason.MANUAL)

        for t in threads:
            t.join()

        # All reads should complete without error
        assert len(results) > 0


# =============================================================================
# Test CancellationTokenSource
# =============================================================================

class TestCancellationTokenSource:
    """Tests for CancellationTokenSource class."""

    def test_token_property_returns_same_instance(self, cancellation_source):
        """Same token always returned."""
        token1 = cancellation_source.token
        token2 = cancellation_source.token
        assert token1 is token2

    def test_cancel_sets_token_cancelled(self, cancellation_source):
        """Cancel propagates to token."""
        cancellation_source.cancel(CancellationReason.MANUAL)
        assert cancellation_source.token.is_cancelled is True

    def test_cancel_sets_reason(self, cancellation_source):
        """Reason is stored."""
        cancellation_source.cancel(CancellationReason.WATCHDOG_RETRY)
        assert cancellation_source.token.reason == CancellationReason.WATCHDOG_RETRY

    def test_cancel_idempotent(self, cancellation_source):
        """Double cancel is safe."""
        cancellation_source.cancel(CancellationReason.MANUAL)
        cancellation_source.cancel(CancellationReason.TIMEOUT)

        # First reason should be preserved
        assert cancellation_source.token.reason == CancellationReason.MANUAL

    def test_is_cancelled_method(self, cancellation_source):
        """Source tracks own state."""
        assert cancellation_source.is_cancelled() is False
        cancellation_source.cancel(CancellationReason.MANUAL)
        assert cancellation_source.is_cancelled() is True

    def test_callback_invoked_on_cancel(self, cancellation_source):
        """Callbacks fire."""
        callback = MagicMock()
        cancellation_source.register_callback(callback)

        cancellation_source.cancel(CancellationReason.MANUAL)

        callback.assert_called_once()

    def test_multiple_callbacks(self, cancellation_source):
        """All callbacks fire."""
        callbacks = [MagicMock() for _ in range(3)]
        for cb in callbacks:
            cancellation_source.register_callback(cb)

        cancellation_source.cancel(CancellationReason.MANUAL)

        for cb in callbacks:
            cb.assert_called_once()

    def test_callback_exception_swallowed(self, cancellation_source):
        """Callback errors don't propagate."""
        failing_callback = MagicMock(side_effect=ValueError("Test error"))
        success_callback = MagicMock()

        cancellation_source.register_callback(failing_callback)
        cancellation_source.register_callback(success_callback)

        # Should not raise
        cancellation_source.cancel(CancellationReason.MANUAL)

        # Second callback should still be called
        success_callback.assert_called_once()

    def test_callback_registered_after_cancel(self, cancellation_source):
        """Late registration not invoked."""
        cancellation_source.cancel(CancellationReason.MANUAL)

        callback = MagicMock()
        cancellation_source.register_callback(callback)

        # Callback should not be called since cancel already happened
        callback.assert_not_called()

    def test_thread_safety_cancel(self):
        """Concurrent cancel is safe."""
        source = CancellationTokenSource()
        reasons_collected = []

        def cancel_with_reason(reason):
            source.cancel(reason)
            reasons_collected.append(source.token.reason)

        threads = [
            threading.Thread(target=cancel_with_reason, args=(CancellationReason.MANUAL,)),
            threading.Thread(target=cancel_with_reason, args=(CancellationReason.TIMEOUT,)),
            threading.Thread(target=cancel_with_reason, args=(CancellationReason.WATCHDOG_RETRY,)),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Only one cancel should "win"
        assert source.token.is_cancelled is True
        # All collected reasons should be the same (the first one that won)
        assert len(set(id(r) for r in reasons_collected)) == 1


# =============================================================================
# Test ActiveRequestRegistry
# =============================================================================

class TestActiveRequestRegistry:
    """Tests for ActiveRequestRegistry class."""

    def test_register_returns_cancellation_source(self, request_registry):
        """Registration works."""
        source = request_registry.register("msg-1", "instance-1")
        assert isinstance(source, CancellationTokenSource)

    def test_register_creates_active_request(self, request_registry):
        """Request tracked internally."""
        request_registry.register("msg-1", "instance-1")
        request = request_registry.get_request("msg-1")
        assert request is not None
        assert request.message_id == "msg-1"
        assert request.instance_id == "instance-1"

    def test_unregister_removes_request(self, request_registry):
        """Cleanup works."""
        request_registry.register("msg-1", "instance-1")
        request_registry.unregister("msg-1")
        assert request_registry.get_request("msg-1") is None

    def test_unregister_nonexistent_no_error(self, request_registry):
        """Safe to unregister unknown."""
        # Should not raise
        request_registry.unregister("nonexistent-msg")

    def test_cancel_returns_true_when_found(self, request_registry):
        """Cancel works."""
        request_registry.register("msg-1", "instance-1")
        result = request_registry.cancel("msg-1", CancellationReason.WATCHDOG_RETRY)
        assert result is True

    def test_cancel_returns_false_when_not_found(self, request_registry):
        """Cancel unknown fails gracefully."""
        result = request_registry.cancel("nonexistent", CancellationReason.MANUAL)
        assert result is False

    def test_cancel_signals_token(self, request_registry):
        """Token is cancelled."""
        source = request_registry.register("msg-1", "instance-1")
        request_registry.cancel("msg-1", CancellationReason.WATCHDOG_RETRY)
        assert source.token.is_cancelled is True
        assert source.token.reason == CancellationReason.WATCHDOG_RETRY

    def test_cancel_cancels_asyncio_task(self, request_registry, mock_asyncio_task):
        """Task.cancel() called via loop."""
        request_registry.register("msg-1", "instance-1", task=mock_asyncio_task)
        request_registry.cancel("msg-1", CancellationReason.WATCHDOG_RETRY)

        mock_asyncio_task.get_loop.return_value.call_soon_threadsafe.assert_called_once()

    def test_cancel_task_already_done(self, request_registry):
        """Handles done task gracefully."""
        done_task = MagicMock(spec=asyncio.Task)
        done_task.done.return_value = True

        request_registry.register("msg-1", "instance-1", task=done_task)
        result = request_registry.cancel("msg-1", CancellationReason.WATCHDOG_RETRY)

        assert result is True
        # Token should still be cancelled
        assert request_registry.get_request("msg-1").cancellation_source.token.is_cancelled

    def test_get_active_for_instance(self, request_registry):
        """Instance filtering works."""
        request_registry.register("msg-1", "instance-1")
        request_registry.register("msg-2", "instance-1")
        request_registry.register("msg-3", "instance-2")

        active = request_registry.get_active_for_instance("instance-1")
        assert set(active) == {"msg-1", "msg-2"}

    def test_get_active_for_empty_instance(self, request_registry):
        """Empty instance returns empty list."""
        active = request_registry.get_active_for_instance("nonexistent")
        assert active == []

    def test_by_instance_index_updated(self, request_registry):
        """Instance index maintained."""
        request_registry.register("msg-1", "instance-1")
        request_registry.register("msg-2", "instance-2")

        assert set(request_registry.get_active_for_instance("instance-1")) == {"msg-1"}
        assert set(request_registry.get_active_for_instance("instance-2")) == {"msg-2"}

        request_registry.unregister("msg-1")

        assert request_registry.get_active_for_instance("instance-1") == []
        assert set(request_registry.get_active_for_instance("instance-2")) == {"msg-2"}

    def test_register_with_task(self, request_registry, mock_asyncio_task):
        """Task is stored."""
        request_registry.register("msg-1", "instance-1", task=mock_asyncio_task)
        request = request_registry.get_request("msg-1")
        assert request.task is mock_asyncio_task

    def test_thread_id_recorded(self, request_registry):
        """Thread ID captured."""
        request_registry.register("msg-1", "instance-1")
        request = request_registry.get_request("msg-1")
        assert request.thread_id == threading.current_thread().ident

    def test_concurrent_register(self, request_registry):
        """Thread-safe registration."""
        registered_ids = []

        def register_msg(msg_id):
            source = request_registry.register(msg_id, "instance-1")
            registered_ids.append(msg_id)

        threads = [
            threading.Thread(target=register_msg, args=(f"msg-{i}",))
            for i in range(50)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All messages should be registered
        assert len(registered_ids) == 50
        for msg_id in registered_ids:
            assert request_registry.get_request(msg_id) is not None

    def test_concurrent_cancel(self, request_registry):
        """Thread-safe cancellation."""
        request_registry.register("msg-1", "instance-1")
        results = []

        def cancel_msg():
            result = request_registry.cancel("msg-1", CancellationReason.MANUAL)
            results.append(result)

        threads = [threading.Thread(target=cancel_msg) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # First cancel returns True, subsequent return True (already cancelled)
        assert all(r is True for r in results)


# =============================================================================
# Test ActiveRequest Dataclass
# =============================================================================

class TestActiveRequest:
    """Tests for ActiveRequest dataclass."""

    def test_active_request_creation(self):
        """Basic creation."""
        source = CancellationTokenSource()
        request = ActiveRequest(
            message_id="msg-1",
            instance_id="instance-1",
            cancellation_source=source,
            started_at=datetime.now(timezone.utc),
        )
        assert request.message_id == "msg-1"
        assert request.instance_id == "instance-1"
        assert request.cancellation_source is source
        assert request.task is None
        assert request.thread_id is None

    def test_active_request_with_task(self):
        """Creation with task."""
        source = CancellationTokenSource()
        task = MagicMock(spec=asyncio.Task)
        request = ActiveRequest(
            message_id="msg-1",
            instance_id="instance-1",
            cancellation_source=source,
            started_at=datetime.now(timezone.utc),
            task=task,
            thread_id=12345,
        )
        assert request.task is task
        assert request.thread_id == 12345


# =============================================================================
# Integration Tests
# =============================================================================

class TestCancellationIntegration:
    """Integration tests for cancellation flow."""

    def test_full_cancel_flow(self):
        """Full cancellation from source to token check."""
        source = CancellationTokenSource()
        token = source.token

        assert token.is_cancelled is False

        source.cancel(CancellationReason.WATCHDOG_RETRY)

        assert token.is_cancelled is True
        assert token.reason == CancellationReason.WATCHDOG_RETRY

        with pytest.raises(OperationCancelledError):
            token.check()

    def test_registry_cancel_flow(self, request_registry):
        """Registry cancellation flow."""
        # Register
        source = request_registry.register("msg-1", "instance-1")

        # Verify registered
        assert request_registry.get_request("msg-1") is not None
        assert source.token.is_cancelled is False

        # Cancel
        result = request_registry.cancel("msg-1", CancellationReason.TIMEOUT)
        assert result is True
        assert source.token.is_cancelled is True

        # Unregister
        request_registry.unregister("msg-1")
        assert request_registry.get_request("msg-1") is None

    def test_callback_with_registry_cancel(self, request_registry):
        """Callback fires when registry cancels."""
        source = request_registry.register("msg-1", "instance-1")
        callback = MagicMock()
        source.register_callback(callback)

        request_registry.cancel("msg-1", CancellationReason.WATCHDOG_RETRY)

        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_context_cancellation(self, request_registry):
        """Async context cancellation works."""
        async def long_running(msg_id):
            source = request_registry.register(msg_id, "instance-1")
            try:
                await asyncio.sleep(10)
                return "completed"
            except asyncio.CancelledError:
                return "cancelled"
            finally:
                request_registry.unregister(msg_id)

        task = asyncio.create_task(long_running("msg-1"))

        # Let task start
        await asyncio.sleep(0.01)

        # Cancel via registry
        request_registry.cancel("msg-1", CancellationReason.MANUAL)

        # The asyncio task cancellation should propagate
        # Note: This tests the flow but asyncio task cancellation is separate from token cancellation
        request = request_registry.get_request("msg-1")
        if request:
            assert request.cancellation_source.token.is_cancelled

        task.cancel()
        result = await task
        assert result == "cancelled"
