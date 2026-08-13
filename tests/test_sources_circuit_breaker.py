"""Tests for CircuitBreaker implementation."""

import pytest
import asyncio
import time

from daemon.sources.circuit_breaker import CircuitBreaker, CircuitState


@pytest.mark.asyncio
async def test_initial_state_is_closed():
    """Fresh circuit breaker should be CLOSED."""
    cb = CircuitBreaker()
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_can_execute_when_closed():
    """Should allow execution when closed."""
    cb = CircuitBreaker()
    assert await cb.can_execute() is True


@pytest.mark.asyncio
async def test_failure_count_starts_at_zero():
    """Initial failure count should be 0."""
    cb = CircuitBreaker()
    assert cb.failure_count == 0


@pytest.mark.asyncio
async def test_transitions_to_open_after_threshold():
    """After N failures, should be OPEN."""
    cb = CircuitBreaker(failure_threshold=3)
    
    # Record 3 failures
    await cb.record_failure()
    await cb.record_failure()
    await cb.record_failure()
    
    # Should now be OPEN
    assert cb.state == CircuitState.OPEN
    assert await cb.can_execute() is False


@pytest.mark.asyncio
async def test_cannot_execute_when_open():
    """Should deny execution when OPEN."""
    cb = CircuitBreaker(failure_threshold=2)
    
    # Record failures to open the circuit
    await cb.record_failure()
    await cb.record_failure()
    
    assert cb.state == CircuitState.OPEN
    assert await cb.can_execute() is False


@pytest.mark.asyncio
async def test_transitions_to_half_open_after_recovery_timeout():
    """After timeout, should be HALF_OPEN."""
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
    
    # Record failure to open the circuit
    await cb.record_failure()
    assert cb.state == CircuitState.OPEN
    
    # Wait for recovery timeout
    await asyncio.sleep(0.02)
    
    # Should transition to HALF_OPEN on next can_execute
    await cb.can_execute()
    assert cb.state == CircuitState.HALF_OPEN


@pytest.mark.asyncio
async def test_half_open_allows_execution():
    """HALF_OPEN allows one probe call at a time (CR-4 contract).

    The first caller after recovery_timeout transitions OPEN→HALF_OPEN and
    claims the probe slot — that caller is allowed to execute. Concurrent
    callers in HALF_OPEN see the probe in flight and are blocked, preventing
    the thundering-herd that would re-trip the failing server.
    """
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)

    # Open the circuit
    await cb.record_failure()
    assert cb.state == CircuitState.OPEN

    # Wait for timeout
    await asyncio.sleep(0.02)

    # First call after timeout: OPEN→HALF_OPEN, probe claimed, allowed
    assert await cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN

    # Second concurrent call: probe still in flight → blocked (CR-4)
    assert await cb.can_execute() is False


@pytest.mark.asyncio
async def test_half_open_to_closed_on_success():
    """Success in HALF_OPEN closes circuit."""
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
    
    # Open the circuit
    await cb.record_failure()
    
    # Wait for timeout and transition to HALF_OPEN
    await asyncio.sleep(0.02)
    await cb.can_execute()
    assert cb.state == CircuitState.HALF_OPEN
    
    # Record success
    await cb.record_success()
    
    # Should be CLOSED
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_half_open_to_open_on_failure():
    """Failure in HALF_OPEN opens circuit again."""
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
    
    # Open the circuit
    await cb.record_failure()
    
    # Wait for timeout and transition to HALF_OPEN
    await asyncio.sleep(0.02)
    await cb.can_execute()
    assert cb.state == CircuitState.HALF_OPEN
    
    # Record failure in HALF_OPEN
    await cb.record_failure()
    
    # Should be OPEN again
    assert cb.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_record_success_resets_failure_count():
    """Success resets count to 0."""
    cb = CircuitBreaker(failure_threshold=5)
    
    # Record some failures
    await cb.record_failure()
    await cb.record_failure()
    await cb.record_failure()
    assert cb.failure_count == 3
    
    # Record success
    await cb.record_success()
    
    # Count should be reset
    assert cb.failure_count == 0


@pytest.mark.asyncio
async def test_record_failure_increments_count():
    """Failure increments count."""
    cb = CircuitBreaker(failure_threshold=5)
    
    assert cb.failure_count == 0
    
    await cb.record_failure()
    assert cb.failure_count == 1
    
    await cb.record_failure()
    assert cb.failure_count == 2


@pytest.mark.asyncio
async def test_concurrent_can_execute_thread_safety():
    """Multiple concurrent calls don't race."""
    cb = CircuitBreaker(failure_threshold=10)
    
    # Run many concurrent can_execute calls
    tasks = [cb.can_execute() for _ in range(100)]
    results = await asyncio.gather(*tasks)
    
    # All should return True (no failures recorded yet)
    assert all(result is True for result in results)


@pytest.mark.asyncio
async def test_recovery_timeout_not_elapsed():
    """Should stay OPEN if timeout not reached."""
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=10.0)
    
    # Open the circuit
    await cb.record_failure()
    assert cb.state == CircuitState.OPEN
    
    # Wait only a short time (less than recovery timeout)
    await asyncio.sleep(0.01)
    
    # Should still be OPEN (can't execute)
    assert cb.state == CircuitState.OPEN
    assert await cb.can_execute() is False


@pytest.mark.asyncio
async def test_get_state_returns_string():
    """State should be readable as string."""
    cb = CircuitBreaker()
    
    assert cb.get_state() == "closed"
    
    cb.state = CircuitState.OPEN
    assert cb.get_state() == "open"
    
    cb.state = CircuitState.HALF_OPEN
    assert cb.get_state() == "half_open"
