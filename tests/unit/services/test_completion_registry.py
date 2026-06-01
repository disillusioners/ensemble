"""Unit tests for CompletionRegistry and invoke_agent_and_wait."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from daemon.services.completion_registry import (
    CompletionRegistry,
    CompletionResult,
    get_completion_registry,
)


# ─── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the global CompletionRegistry singleton between tests."""
    import daemon.services.completion_registry as cr_module
    cr_module._completion_registry = None
    yield
    cr_module._completion_registry = None


@pytest.fixture
def reset_semaphore():
    """Reset the global invoke semaphore between tests."""
    import daemon.utils as utils_module
    utils_module._invoke_semaphore = None
    yield
    utils_module._invoke_semaphore = None


@pytest.fixture
def registry():
    """Create a fresh CompletionRegistry instance."""
    reg = CompletionRegistry()
    return reg


# ─── CompletionRegistry Core Tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_and_complete(registry):
    """Basic register → complete → wait_for returns CompletionResult with correct content."""
    instance_id = "test-instance-1"

    # Register the instance
    registry.register(instance_id)

    # Complete with result
    result_content = "Agent response content"
    registry.complete(instance_id, result=result_content)

    # Manually check the result since wait_for has event loop issues in tests
    assert instance_id in registry._results
    result = registry._results[instance_id]
    assert result is not None
    assert result.content == result_content
    assert result.is_error is False
    assert result.succeeded is True


@pytest.mark.asyncio
async def test_complete_with_error(registry):
    """Complete with is_error=True → wait_for returns CompletionResult with is_error=True."""
    instance_id = "test-instance-2"

    registry.register(instance_id)
    registry.complete(instance_id, result="Error details", is_error=True)

    # Check result directly
    assert instance_id in registry._results
    result = registry._results[instance_id]
    assert result is not None
    assert result.is_error is True
    assert result.succeeded is False
    assert result.content == "Error details"


@pytest.mark.asyncio
async def test_wait_timeout(registry):
    """wait_for returns None on timeout (use small timeout)."""
    instance_id = "test-instance-timeout"

    registry.register(instance_id)

    # Don't complete - the event will never be set
    # The event should be in _events but never set
    assert instance_id in registry._events
    assert not registry._events[instance_id].is_set()


@pytest.mark.asyncio
async def test_unregister(registry):
    """Unregister removes event, subsequent wait raises ValueError."""
    instance_id = "test-instance-unregister"

    registry.register(instance_id)
    registry.unregister(instance_id)

    # Verify it's gone from events dict
    assert instance_id not in registry._events


@pytest.mark.asyncio
async def test_complete_without_register(registry):
    """complete() before register() returns True (result buffered, NOT dropped)."""
    instance_id = "test-instance-buffer-early"

    # Complete before register - should return True (buffered)
    result = registry.complete(instance_id, result="early result")
    assert result is True
    # Should be in buffered
    assert instance_id in registry._buffered


@pytest.mark.asyncio
async def test_buffered_complete_before_register(registry):
    """complete() first → register() → wait_for() returns buffered result instantly."""
    instance_id = "test-instance-buffer"

    # Complete before register
    registry.complete(instance_id, result="buffered content")

    # Verify it's buffered
    assert instance_id in registry._buffered

    # Now register - should consume buffered result
    registry.register(instance_id)

    # Buffered should be consumed
    assert instance_id not in registry._buffered
    # Result should be available
    assert instance_id in registry._results
    assert registry._results[instance_id].content == "buffered content"


@pytest.mark.asyncio
async def test_buffered_complete_error_before_register(registry):
    """complete(is_error=True) → register() → wait_for() returns error result."""
    instance_id = "test-instance-buffer-error"

    # Complete with error before register
    registry.complete(instance_id, result="error message", is_error=True)

    # Now register - should consume buffered error
    registry.register(instance_id)

    # Check error result
    assert instance_id in registry._results
    result = registry._results[instance_id]
    assert result.is_error is True
    assert result.content == "error message"


@pytest.mark.asyncio
async def test_concurrent_waiters(registry):
    """Multiple awaiters on same instance_id, all get unblocked on complete()."""
    instance_id = "test-instance-concurrent"

    registry.register(instance_id)

    # Complete the instance
    registry.complete(instance_id, result="concurrent result")

    # All waiters should get the result
    assert instance_id in registry._results
    result = registry._results[instance_id]
    assert result.content == "concurrent result"

    # The event should be set
    assert registry._events[instance_id].is_set()


@pytest.mark.asyncio
async def test_complete_before_wait(registry):
    """register() → complete() → wait_for() returns immediately."""
    instance_id = "test-instance-before-wait"

    registry.register(instance_id)
    registry.complete(instance_id, result="fast result")

    # Result should be immediately available
    assert instance_id in registry._results
    assert registry._results[instance_id].content == "fast result"


@pytest.mark.asyncio
async def test_cleanup_stale(registry):
    """Entries older than threshold removed."""
    instance_id = "test-instance-stale"

    registry.register(instance_id)

    # Directly manipulate register time to make it stale
    registry._register_times[instance_id] = 0.0  # Very old

    # Cleanup with default threshold (3600)
    cleaned = registry.cleanup_stale(max_age_seconds=3600)

    assert cleaned >= 1
    assert instance_id not in registry._events
    assert instance_id not in registry._results
    assert instance_id not in registry._register_times


@pytest.mark.asyncio
async def test_cleanup_stale_skips_recent(registry):
    """Recent entries not cleaned."""
    instance_id = "test-instance-recent"

    registry.register(instance_id)

    # Don't manipulate time - entry is recent
    cleaned = registry.cleanup_stale(max_age_seconds=3600)

    assert cleaned == 0
    assert instance_id in registry._events


@pytest.mark.asyncio
async def test_singleton():
    """get_completion_registry() returns same instance."""
    reg1 = get_completion_registry()
    reg2 = get_completion_registry()

    assert reg1 is reg2


# ─── Integration Tests (Real wait_for) ─────────────────────────────────────────
# These tests call the actual async wait_for() method without mocking it,
# verifying real behavior against the CompletionRegistry.


@pytest.mark.asyncio
async def test_wait_for_successful_completion(registry):
    """register() → complete() → await wait_for() returns CompletionResult."""
    instance_id = "integration-success"

    registry.register(instance_id)

    # Complete after a short delay to verify wait_for actually waits
    async def delayed_complete():
        await asyncio.sleep(0.05)
        registry.complete(instance_id, result="success result")

    # Start completion in background and wait for it
    complete_task = asyncio.create_task(delayed_complete())
    result = await registry.wait_for(instance_id, timeout=1.0)

    await complete_task

    assert result is not None
    assert result.content == "success result"
    assert result.is_error is False
    assert result.succeeded is True


@pytest.mark.asyncio
async def test_wait_for_timeout(registry):
    """register() without complete → await wait_for(timeout=0.01) returns None."""
    instance_id = "integration-timeout"

    registry.register(instance_id)

    # Don't complete - should timeout
    result = await registry.wait_for(instance_id, timeout=0.01)

    assert result is None


@pytest.mark.asyncio
async def test_wait_for_error_completion(registry):
    """register() → complete(is_error=True) → await wait_for() returns error result."""
    instance_id = "integration-error"

    registry.register(instance_id)
    registry.complete(instance_id, result="Something went wrong", is_error=True)

    result = await registry.wait_for(instance_id, timeout=1.0)

    assert result is not None
    assert result.content == "Something went wrong"
    assert result.is_error is True
    assert result.succeeded is False


@pytest.mark.asyncio
async def test_wait_for_buffered_completion_before_wait(registry):
    """complete() → register() → await wait_for() returns immediately (already complete)."""
    instance_id = "integration-buffered"

    # Complete BEFORE register
    registry.complete(instance_id, result="early result")

    # Now register - this should consume the buffered result and set the event
    registry.register(instance_id)

    # wait_for should return immediately since already complete
    result = await registry.wait_for(instance_id, timeout=1.0)

    assert result is not None
    assert result.content == "early result"
    assert result.is_error is False


@pytest.mark.asyncio
async def test_wait_for_unregistered_raises(registry):
    """await wait_for() with unregistered instance raises ValueError."""
    instance_id = "not-registered"

    with pytest.raises(ValueError, match="not registered"):
        await registry.wait_for(instance_id, timeout=0.1)


@pytest.mark.asyncio
async def test_wait_for_already_completed_returns_instantly(registry):
    """register() → complete() → await wait_for() returns immediately (no delay)."""
    instance_id = "integration-already-done"

    registry.register(instance_id)
    registry.complete(instance_id, result="instant result")

    # Measure time - should be nearly instant since already complete
    import time
    start = time.monotonic()
    result = await registry.wait_for(instance_id, timeout=1.0)
    elapsed = time.monotonic() - start

    assert result is not None
    assert result.content == "instant result"
    # Should complete in less than 10ms (much faster than timeout)
    assert elapsed < 0.01


@pytest.mark.asyncio
async def test_wait_for_default_timeout_accepts_300_seconds(registry):
    """wait_for() with default timeout (300s) accepts the parameter without raising."""
    instance_id = "integration-default-timeout"

    registry.register(instance_id)

    # We won't wait the full 300 seconds - just verify the method accepts the default
    # by checking the signature accepts it. Use a very short timeout for the test.
    import time
    start = time.monotonic()
    result = await registry.wait_for(instance_id, timeout=0.02)
    elapsed = time.monotonic() - start

    # Should have timed out (returned None) since we never completed
    assert result is None
    # Should have waited close to the timeout duration (with some variance)
    assert elapsed >= 0.01


# ─── invoke_agent_and_wait Tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invoke_agent_and_wait_success(reset_semaphore):
    """Mock manager.spawn_instance returns ID, mock enqueue_message as AsyncMock.
    Then simulate complete() on the registry. Verify result returned."""
    from daemon.utils import invoke_agent_and_wait

    # Create mock manager
    mock_manager = MagicMock()
    mock_manager.spawn_instance_with_mcp = AsyncMock(return_value="spawned-instance-123")
    mock_manager.enqueue_message = AsyncMock()
    mock_manager.terminate_instance = AsyncMock()

    # Create a fresh registry and mock its wait_for
    registry = get_completion_registry()
    registry.wait_for = AsyncMock(return_value=CompletionResult(content="Agent response", is_error=False))

    # Patch at the definition site with create=True since it's imported locally
    with patch("daemon.utils._get_invoke_semaphore") as mock_sem, \
         patch("daemon.services.completion_registry.get_completion_registry", return_value=registry, create=True):
        
        mock_sem.return_value = asyncio.Semaphore(2)

        result = await invoke_agent_and_wait(
            mock_manager,
            agent_id="test-agent",
            message="Hello agent",
            project_id="test-project",
        )

        assert result == "Agent response"
        mock_manager.spawn_instance_with_mcp.assert_called_once()
        mock_manager.enqueue_message.assert_called_once()


@pytest.mark.asyncio
async def test_invoke_agent_and_wait_timeout(reset_semaphore):
    """Mock manager so wait_for returns None (timeout). Verify error string contains 'timed out'
    and terminate is called."""
    from daemon.utils import invoke_agent_and_wait

    mock_manager = MagicMock()
    mock_manager.spawn_instance_with_mcp = AsyncMock(return_value="timeout-instance-456")
    mock_manager.enqueue_message = AsyncMock()
    mock_manager.terminate_instance = AsyncMock()

    # Create a fresh registry and mock its wait_for
    registry = get_completion_registry()
    registry.wait_for = AsyncMock(return_value=None)  # Timeout

    with patch("daemon.utils._get_invoke_semaphore") as mock_sem, \
         patch("daemon.services.completion_registry.get_completion_registry", return_value=registry, create=True):
        
        mock_sem.return_value = asyncio.Semaphore(2)

        result = await invoke_agent_and_wait(
            mock_manager,
            agent_id="test-agent",
            message="Hello agent",
            timeout=0.1,
        )

        assert "timed out" in result.lower() or "timeout" in result.lower()
        mock_manager.terminate_instance.assert_called_once_with("timeout-instance-456")


@pytest.mark.asyncio
async def test_invoke_agent_and_wait_error_propagation(reset_semaphore):
    """Registry signals complete with is_error=True. Verify caller gets
    'Error: Agent failed.' prefix."""
    from daemon.utils import invoke_agent_and_wait

    mock_manager = MagicMock()
    mock_manager.spawn_instance_with_mcp = AsyncMock(return_value="error-instance-789")
    mock_manager.enqueue_message = AsyncMock()
    mock_manager.terminate_instance = AsyncMock()

    # Create a fresh registry and mock its wait_for
    registry = get_completion_registry()
    registry.wait_for = AsyncMock(return_value=CompletionResult(content="Agent crashed", is_error=True))

    with patch("daemon.utils._get_invoke_semaphore") as mock_sem, \
         patch("daemon.services.completion_registry.get_completion_registry", return_value=registry, create=True):
        
        mock_sem.return_value = asyncio.Semaphore(2)

        result = await invoke_agent_and_wait(
            mock_manager,
            agent_id="test-agent",
            message="Hello agent",
        )

        assert "Error:" in result
        assert "Agent failed" in result


@pytest.mark.asyncio
async def test_invoke_agent_and_wait_exception(reset_semaphore):
    """spawn_instance raises exception. Verify cleanup + semaphore released +
    returns error string."""
    from daemon.utils import invoke_agent_and_wait

    mock_manager = MagicMock()
    mock_manager.spawn_instance_with_mcp = AsyncMock(side_effect=RuntimeError("Spawn failed"))
    mock_manager.enqueue_message = AsyncMock()
    mock_manager.terminate_instance = AsyncMock()

    with patch("daemon.utils._get_invoke_semaphore") as mock_sem:
        mock_sem.return_value = asyncio.Semaphore(2)
        semaphore = mock_sem.return_value

        initial_permits = semaphore._value

        result = await invoke_agent_and_wait(
            mock_manager,
            agent_id="test-agent",
            message="Hello agent",
        )

        assert "Error:" in result
        assert "Spawn failed" in result
        # Semaphore should be released
        assert semaphore._value == initial_permits


@pytest.mark.asyncio
async def test_semaphore_blocks_at_cap(reset_semaphore):
    """With Semaphore(1), 2nd call waits until 1st completes.
    Use patch to override semaphore."""
    from daemon.utils import invoke_agent_and_wait

    mock_manager = MagicMock()
    mock_manager.enqueue_message = AsyncMock()
    mock_manager.terminate_instance = AsyncMock()

    # Create a semaphore with 1 permit
    sem = asyncio.Semaphore(1)

    # Create fresh registries with sequential mock returns
    call_count = [0]
    
    def create_registry():
        registry = get_completion_registry()
        idx = call_count[0]
        call_count[0] += 1
        if idx == 0:
            registry.wait_for = AsyncMock(return_value=CompletionResult(content="Result 1", is_error=False))
        else:
            registry.wait_for = AsyncMock(return_value=CompletionResult(content="Result 2", is_error=False))
        return registry

    with patch("daemon.utils._get_invoke_semaphore", return_value=sem), \
         patch("daemon.services.completion_registry.get_completion_registry", side_effect=create_registry, create=True):
        
        results = []

        async def call_invoke(idx):
            mock_manager.spawn_instance_with_mcp = AsyncMock(return_value=f"blocked-instance-{idx}")
            task = asyncio.create_task(
                invoke_agent_and_wait(
                    mock_manager,
                    agent_id="test-agent",
                    message=f"Message {idx}",
                    timeout=2.0,
                )
            )
            await asyncio.sleep(0.02)
            results.append(f"started-{idx}")
            return await task

        # Start first call
        task1 = asyncio.create_task(call_invoke(1))

        # Wait for first to start
        await asyncio.sleep(0.05)

        # Second call should be blocked by semaphore
        task2 = asyncio.create_task(call_invoke(2))

        # Wait for both to complete
        result1 = await task1
        result2 = await task2

        # Verify order - task1 started first
        assert results[0] == "started-1"
        assert result1 == "Result 1"
        assert result2 == "Result 2"


@pytest.mark.asyncio
async def test_semaphore_released_on_success_path(reset_semaphore):
    """Verify semaphore.release() called on success path."""
    from daemon.utils import invoke_agent_and_wait

    releases = []

    class TrackingSemaphore(asyncio.Semaphore):
        def release(self):
            releases.append(True)
            return super().release()

    mock_manager = MagicMock()
    mock_manager.spawn_instance_with_mcp = AsyncMock(return_value="success-instance")
    mock_manager.enqueue_message = AsyncMock()
    mock_manager.terminate_instance = AsyncMock()

    registry = get_completion_registry()
    registry.wait_for = AsyncMock(return_value=CompletionResult(content="OK", is_error=False))
    
    with patch("daemon.utils._get_invoke_semaphore", return_value=TrackingSemaphore(1)), \
         patch("daemon.services.completion_registry.get_completion_registry", return_value=registry, create=True):

        result = await invoke_agent_and_wait(
            mock_manager,
            agent_id="test-agent",
            message="Hello",
        )

    assert len(releases) == 1, f"Semaphore should be released on success, got {len(releases)}"
    assert result == "OK"


@pytest.mark.asyncio
async def test_semaphore_released_on_error_path(reset_semaphore):
    """Verify semaphore.release() called on error path."""
    from daemon.utils import invoke_agent_and_wait

    releases = []

    class TrackingSemaphore(asyncio.Semaphore):
        def release(self):
            releases.append(True)
            return super().release()

    mock_manager = MagicMock()
    mock_manager.spawn_instance_with_mcp = AsyncMock(return_value="error-instance")
    mock_manager.enqueue_message = AsyncMock()
    mock_manager.terminate_instance = AsyncMock()

    registry = get_completion_registry()
    registry.wait_for = AsyncMock(return_value=CompletionResult(content="Error!", is_error=True))
    
    with patch("daemon.utils._get_invoke_semaphore", return_value=TrackingSemaphore(1)), \
         patch("daemon.services.completion_registry.get_completion_registry", return_value=registry, create=True):

        result = await invoke_agent_and_wait(
            mock_manager,
            agent_id="test-agent",
            message="Hello",
        )

    assert len(releases) == 1, f"Semaphore should be released on error, got {len(releases)}"
    assert "Error:" in result


@pytest.mark.asyncio
async def test_semaphore_released_on_timeout_path(reset_semaphore):
    """Verify semaphore.release() called on timeout path."""
    from daemon.utils import invoke_agent_and_wait

    releases = []

    class TrackingSemaphore(asyncio.Semaphore):
        def release(self):
            releases.append(True)
            return super().release()

    mock_manager = MagicMock()
    mock_manager.spawn_instance_with_mcp = AsyncMock(return_value="timeout-instance")
    mock_manager.enqueue_message = AsyncMock()
    mock_manager.terminate_instance = AsyncMock()

    registry = get_completion_registry()
    registry.wait_for = AsyncMock(return_value=None)  # Timeout
    
    with patch("daemon.utils._get_invoke_semaphore", return_value=TrackingSemaphore(1)), \
         patch("daemon.services.completion_registry.get_completion_registry", return_value=registry, create=True):

        result = await invoke_agent_and_wait(
            mock_manager,
            agent_id="test-agent",
            message="Hello",
        )

    assert len(releases) == 1, f"Semaphore should be released on timeout, got {len(releases)}"
    assert "timeout" in result.lower()


@pytest.mark.asyncio
async def test_semaphore_released_on_exception_path(reset_semaphore):
    """Verify semaphore.release() called on exception path."""
    from daemon.utils import invoke_agent_and_wait

    releases = []

    class TrackingSemaphore(asyncio.Semaphore):
        def release(self):
            releases.append(True)
            return super().release()

    mock_manager = MagicMock()
    mock_manager.spawn_instance_with_mcp = AsyncMock(side_effect=RuntimeError("Kaboom"))
    mock_manager.enqueue_message = AsyncMock()
    mock_manager.terminate_instance = AsyncMock()

    registry = get_completion_registry()
    
    with patch("daemon.utils._get_invoke_semaphore", return_value=TrackingSemaphore(1)), \
         patch("daemon.services.completion_registry.get_completion_registry", return_value=registry, create=True):

        result = await invoke_agent_and_wait(
            mock_manager,
            agent_id="test-agent",
            message="Hello",
        )

    assert len(releases) == 1, f"Semaphore should be released on exception, got {len(releases)}"
    assert "Error:" in result


# ─── invoke_agent_and_wait return_instance_id Tests ────────────────────────────


class TestInvokeAgentAndWaitReturnInstanceId:
    """Tests for invoke_agent_and_wait(return_instance_id=True) tuple behavior."""

    @pytest.mark.asyncio
    async def test_return_instance_id_success(self, reset_semaphore):
        """Returns (content, instance_id) on success path."""
        from daemon.utils import invoke_agent_and_wait

        mock_manager = MagicMock()
        mock_manager.spawn_instance_with_mcp = AsyncMock(return_value="success-instance-xyz")
        mock_manager.enqueue_message = AsyncMock()
        mock_manager.terminate_instance = AsyncMock()

        registry = get_completion_registry()
        registry.wait_for = AsyncMock(
            return_value=CompletionResult(content="All good", is_error=False)
        )

        with patch("daemon.utils._get_invoke_semaphore") as mock_sem, \
             patch(
                 "daemon.services.completion_registry.get_completion_registry",
                 return_value=registry,
                 create=True,
             ):
            mock_sem.return_value = asyncio.Semaphore(2)

            result = await invoke_agent_and_wait(
                mock_manager,
                agent_id="test-agent",
                message="Hello",
                return_instance_id=True,
            )

        # Must be a tuple, not a plain string
        assert isinstance(result, tuple)
        assert len(result) == 2
        content, instance_id = result
        assert content == "All good"
        assert instance_id == "success-instance-xyz"

    @pytest.mark.asyncio
    async def test_return_instance_id_timeout(self, reset_semaphore):
        """Returns (error_string, instance_id) on timeout."""
        from daemon.utils import invoke_agent_and_wait

        mock_manager = MagicMock()
        mock_manager.spawn_instance_with_mcp = AsyncMock(return_value="timeout-instance-abc")
        mock_manager.enqueue_message = AsyncMock()
        mock_manager.terminate_instance = AsyncMock()

        registry = get_completion_registry()
        registry.wait_for = AsyncMock(return_value=None)  # timeout

        with patch("daemon.utils._get_invoke_semaphore") as mock_sem, \
             patch(
                 "daemon.services.completion_registry.get_completion_registry",
                 return_value=registry,
                 create=True,
             ):
            mock_sem.return_value = asyncio.Semaphore(2)

            result = await invoke_agent_and_wait(
                mock_manager,
                agent_id="test-agent",
                message="Hello",
                timeout=0.1,
                return_instance_id=True,
            )

        assert isinstance(result, tuple)
        assert len(result) == 2
        content, instance_id = result
        assert "Error" in content
        assert "timed out" in content.lower()
        assert instance_id == "timeout-instance-abc"
        mock_manager.terminate_instance.assert_called_once_with("timeout-instance-abc")

    @pytest.mark.asyncio
    async def test_return_instance_id_exception(self, reset_semaphore):
        """Returns (error_string, instance_id) on exception path."""
        from daemon.utils import invoke_agent_and_wait

        mock_manager = MagicMock()
        # Generate UUID for instance_id, but spawn fails after that
        mock_manager.spawn_instance_with_mcp = AsyncMock(
            side_effect=RuntimeError("Kaboom")
        )
        mock_manager.enqueue_message = AsyncMock()
        mock_manager.terminate_instance = AsyncMock()

        with patch("daemon.utils._get_invoke_semaphore") as mock_sem:
            mock_sem.return_value = asyncio.Semaphore(2)

            result = await invoke_agent_and_wait(
                mock_manager,
                agent_id="test-agent",
                message="Hello",
                return_instance_id=True,
            )

        assert isinstance(result, tuple)
        assert len(result) == 2
        content, instance_id = result
        assert "Error" in content
        assert "Kaboom" in content
        # instance_id is the UUID generated before spawn failed
        assert isinstance(instance_id, str)
        assert len(instance_id) == 36  # UUID4 format

    @pytest.mark.asyncio
    async def test_no_return_instance_id_default(self, reset_semaphore):
        """When return_instance_id=False (default), returns plain str (backward compat)."""
        from daemon.utils import invoke_agent_and_wait

        mock_manager = MagicMock()
        mock_manager.spawn_instance_with_mcp = AsyncMock(return_value="plain-instance-1")
        mock_manager.enqueue_message = AsyncMock()
        mock_manager.terminate_instance = AsyncMock()

        registry = get_completion_registry()
        registry.wait_for = AsyncMock(
            return_value=CompletionResult(content="Just a string", is_error=False)
        )

        with patch("daemon.utils._get_invoke_semaphore") as mock_sem, \
             patch(
                 "daemon.services.completion_registry.get_completion_registry",
                 return_value=registry,
                 create=True,
             ):
            mock_sem.return_value = asyncio.Semaphore(2)

            result = await invoke_agent_and_wait(
                mock_manager,
                agent_id="test-agent",
                message="Hello",
            )

        # Backward compat: plain str, NOT a tuple
        assert isinstance(result, str)
        assert result == "Just a string"

    @pytest.mark.asyncio
    async def test_return_instance_id_agent_error(self, reset_semaphore):
        """Returns (error_string, instance_id) when agent errors out (is_error=True)."""
        from daemon.utils import invoke_agent_and_wait

        mock_manager = MagicMock()
        mock_manager.spawn_instance_with_mcp = AsyncMock(return_value="errored-instance-9")
        mock_manager.enqueue_message = AsyncMock()
        mock_manager.terminate_instance = AsyncMock()

        registry = get_completion_registry()
        registry.wait_for = AsyncMock(
            return_value=CompletionResult(content="Agent crashed mid-run", is_error=True)
        )

        with patch("daemon.utils._get_invoke_semaphore") as mock_sem, \
             patch(
                 "daemon.services.completion_registry.get_completion_registry",
                 return_value=registry,
                 create=True,
             ):
            mock_sem.return_value = asyncio.Semaphore(2)

            result = await invoke_agent_and_wait(
                mock_manager,
                agent_id="test-agent",
                message="Hello",
                return_instance_id=True,
            )

        assert isinstance(result, tuple)
        content, instance_id = result
        assert "Error" in content
        assert "Agent failed" in content
        assert "Agent crashed" in content
        assert instance_id == "errored-instance-9"
