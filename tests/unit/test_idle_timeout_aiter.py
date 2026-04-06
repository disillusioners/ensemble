"""Tests for InstanceManager._idle_timeout_aiter()."""

import asyncio
import pytest
from unittest.mock import AsyncMock

from daemon.manager import InstanceManager
from daemon.llm_error_classifier import StreamIdleTimeoutError


class SlowAsyncIterator:
    """Async iterator that yields items with configurable delays."""

    def __init__(self, items: list, delays: list[float]):
        """Create iterator.
        
        Args:
            items: Items to yield.
            delays: Delay (seconds) BEFORE yielding each item. Must have same length as items.
        """
        self.items = list(items)
        self.delays = list(delays)
        self.index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index >= len(self.items):
            raise StopAsyncIteration()
        
        delay = self.delays[self.index]
        if delay > 0:
            await asyncio.sleep(delay)
        
        item = self.items[self.index]
        self.index += 1
        return item


class ImmediateStopIterator:
    """Async iterator that immediately raises StopAsyncIteration."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration()


@pytest.mark.asyncio
async def test_idle_timeout_fires():
    """Mock iterator that sleeps beyond timeout → verify StreamIdleTimeoutError is raised."""
    timeout = 0.1
    # Single item with delay that exceeds timeout
    slow_aiter = SlowAsyncIterator(items=["item1"], delays=[timeout * 3])

    with pytest.raises(StreamIdleTimeoutError) as exc_info:
        result = []
        async for item in InstanceManager._idle_timeout_aiter(slow_aiter, timeout):
            result.append(item)

    assert exc_info.value.timeout_seconds == timeout
    assert "0.1" in str(exc_info.value) or "0.10" in str(exc_info.value)


@pytest.mark.asyncio
async def test_normal_passthrough():
    """Mock iterator that yields items within timeout → verify all items pass through."""
    timeout = 1.0
    items = ["a", "b", "c"]
    # All items arrive quickly (well within timeout)
    slow_aiter = SlowAsyncIterator(items=items, delays=[0, 0, 0])

    result = []
    async for item in InstanceManager._idle_timeout_aiter(slow_aiter, timeout):
        result.append(item)

    assert result == items


@pytest.mark.asyncio
async def test_disabled_timeout_zero():
    """Verify with timeout=0 the wrapper is a no-op and all items pass through."""
    timeout = 0
    items = ["x", "y", "z"]
    # Even with slow delays, timeout=0 means no timeout
    slow_aiter = SlowAsyncIterator(items=items, delays=[0.05, 0.05, 0.05])

    result = []
    async for item in InstanceManager._idle_timeout_aiter(slow_aiter, timeout):
        result.append(item)

    assert result == items


@pytest.mark.asyncio
async def test_disabled_timeout_negative():
    """Verify with negative timeout the wrapper is a no-op."""
    timeout = -1
    items = ["first", "second"]
    slow_aiter = SlowAsyncIterator(items=items, delays=[0.05, 0.05])

    result = []
    async for item in InstanceManager._idle_timeout_aiter(slow_aiter, timeout):
        result.append(item)

    assert result == items


@pytest.mark.asyncio
async def test_empty_iterator():
    """Async iterator that immediately raises StopAsyncIteration → verify clean exit, no error."""
    timeout = 10.0
    empty_aiter = ImmediateStopIterator()

    result = []
    async for item in InstanceManager._idle_timeout_aiter(empty_aiter, timeout):
        result.append(item)

    assert result == []


@pytest.mark.asyncio
async def test_slow_then_fast():
    """Iterator takes a while for first item (within timeout) then yields remaining quickly."""
    timeout = 0.5
    items = ["first", "second", "third"]
    # First item within timeout, rest are fast
    delays = [0.3, 0.0, 0.0]
    slow_aiter = SlowAsyncIterator(items=items, delays=delays)

    result = []
    async for item in InstanceManager._idle_timeout_aiter(slow_aiter, timeout):
        result.append(item)

    assert result == items


@pytest.mark.asyncio
async def test_single_item_within_timeout():
    """Single item arriving within timeout should be returned."""
    timeout = 1.0
    single_aiter = SlowAsyncIterator(items=["only"], delays=[0.1])

    result = []
    async for item in InstanceManager._idle_timeout_aiter(single_aiter, timeout):
        result.append(item)

    assert result == ["only"]


@pytest.mark.asyncio
async def test_timeout_between_items():
    """Timeout fires between items if no new item arrives in time."""
    timeout = 0.1
    # First item fast, second item too slow
    items = ["first", "second"]
    delays = [0.0, timeout * 5]
    slow_aiter = SlowAsyncIterator(items=items, delays=delays)

    # First item should come through
    result = []
    with pytest.raises(StreamIdleTimeoutError) as exc_info:
        async for item in InstanceManager._idle_timeout_aiter(slow_aiter, timeout):
            result.append(item)

    # First item arrives before timeout
    assert result == ["first"]
    # Timeout fires waiting for second item
    assert exc_info.value.timeout_seconds == timeout


@pytest.mark.asyncio
async def test_unittest_mock_asyncmock():
    """Verify AsyncMock-based iterator works (standard test pattern)."""
    timeout = 1.0
    mock_aiter = AsyncMock()
    mock_aiter.__anext__ = AsyncMock(side_effect=["a", "b", StopAsyncIteration()])

    result = []
    async for item in InstanceManager._idle_timeout_aiter(mock_aiter, timeout):
        result.append(item)

    assert result == ["a", "b"]


@pytest.mark.asyncio
async def test_unittest_mock_timeout_error():
    """Verify slow __anext__ raises TimeoutError correctly."""
    timeout = 0.1

    class SlowIterator:
        """Iterator that sleeps longer than timeout."""
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(timeout * 5)
            return "slow"

    slow_aiter = SlowIterator()

    with pytest.raises(StreamIdleTimeoutError) as exc_info:
        result = []
        async for item in InstanceManager._idle_timeout_aiter(slow_aiter, timeout):
            result.append(item)

    assert exc_info.value.timeout_seconds == timeout


@pytest.mark.asyncio
async def test_tool_executing_callback_extends_timeout():
    """Verify tool_executing_callback uses request_timeout during tool execution.
    
    This tests the fix for the issue where idle timeout fires during tool execution
    (when no events flow from graph.astream). Tool execution time should be excluded
    from idle timeout by using request_timeout instead.
    """
    timeout = 0.1  # Short timeout for normal operation
    request_timeout = 1.0  # Longer timeout for tool execution
    
    # Track if callback returns True (tools executing)
    tools_executing = True
    
    async def tool_executing_callback():
        return tools_executing
    
    # Item takes longer than normal timeout but shorter than request_timeout
    slow_aiter = SlowAsyncIterator(items=["item"], delays=[request_timeout * 0.5])
    
    result = []
    async for item in InstanceManager._idle_timeout_aiter(
        slow_aiter, timeout, request_timeout=request_timeout, tool_executing_callback=tool_executing_callback
    ):
        result.append(item)
    
    # Item should come through because callback returned True (uses request_timeout)
    assert result == ["item"]


@pytest.mark.asyncio
async def test_tool_executing_callback_returns_false_uses_normal_timeout():
    """Verify normal timeout applies when callback returns False (tools not executing)."""
    timeout = 0.1
    request_timeout = 10.0  # Should not be used
    
    async def tool_executing_callback():
        return False  # Tools not executing
    
    # Item takes longer than normal timeout
    slow_aiter = SlowAsyncIterator(items=["item"], delays=[timeout * 5])
    
    with pytest.raises(StreamIdleTimeoutError):
        result = []
        async for item in InstanceManager._idle_timeout_aiter(
            slow_aiter, timeout, request_timeout=request_timeout, tool_executing_callback=tool_executing_callback
        ):
            result.append(item)


@pytest.mark.asyncio
async def test_tool_executing_callback_dynamic_state():
    """Verify timeout behavior changes based on callback returning different values."""
    timeout = 0.1
    request_timeout = 1.0
    
    # Simulate: tools start executing after first item
    call_count = 0
    
    async def tool_executing_callback():
        nonlocal call_count
        call_count += 1
        # First call returns False (normal timeout), then True (request_timeout)
        return call_count > 1
    
    # First item fast, second item slower than normal timeout but OK with request_timeout
    slow_aiter = SlowAsyncIterator(items=["first", "second"], delays=[0.0, timeout * 5])
    
    result = []
    async for item in InstanceManager._idle_timeout_aiter(
        slow_aiter, timeout, request_timeout=request_timeout, tool_executing_callback=tool_executing_callback
    ):
        result.append(item)
    
    # Both items should come through - second one uses request_timeout
    assert result == ["first", "second"]
