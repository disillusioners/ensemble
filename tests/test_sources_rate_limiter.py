"""Tests for TokenBucketLimiter in daemon/sources/rate_limiter.py."""

import pytest
import asyncio
import time

from daemon.sources.rate_limiter import TokenBucketLimiter, RateLimit


# =============================================================================
# Basic Acquisition Tests
# =============================================================================

@pytest.mark.asyncio
async def test_acquire_when_tokens_available():
    """Should return True when tokens are available."""
    limiter = TokenBucketLimiter(RateLimit(messages_per_second=10, burst_size=5))
    result = await limiter.acquire()
    assert result is True


@pytest.mark.asyncio
async def test_acquire_fails_when_no_tokens():
    """Should return False when burst is exhausted."""
    limiter = TokenBucketLimiter(RateLimit(messages_per_second=10, burst_size=2))
    
    # First two should succeed (burst_size=2)
    result1 = await limiter.acquire()
    assert result1 is True
    
    result2 = await limiter.acquire()
    assert result2 is True
    
    # Third should fail - burst exhausted
    result3 = await limiter.acquire()
    assert result3 is False


@pytest.mark.asyncio
async def test_burst_size_limit():
    """Should respect burst_size limit."""
    burst_size = 3
    limiter = TokenBucketLimiter(RateLimit(messages_per_second=10, burst_size=burst_size))
    
    # Should be able to acquire exactly burst_size times
    results = []
    for _ in range(burst_size):
        results.append(await limiter.acquire())
    
    # All burst_size acquisitions should succeed
    assert all(results) is True
    
    # Next acquisition should fail
    result = await limiter.acquire()
    assert result is False


# =============================================================================
# Token Refill Tests
# =============================================================================

@pytest.mark.asyncio
async def test_tokens_refill_over_time():
    """Tokens should refill based on rate."""
    rate = 10  # 10 tokens per second
    burst_size = 2
    limiter = TokenBucketLimiter(RateLimit(messages_per_second=rate, burst_size=burst_size))
    
    # Exhaust all tokens
    await limiter.acquire()
    await limiter.acquire()
    
    # Should be empty now
    result = await limiter.acquire()
    assert result is False
    
    # Wait for refill - with rate=10, after 0.5s we should have 5 tokens
    await asyncio.sleep(0.5)
    
    # Now should have tokens available
    result = await limiter.acquire()
    assert result is True


# =============================================================================
# Wait and Acquire Tests
# =============================================================================

@pytest.mark.asyncio
async def test_wait_and_acquire_succeeds():
    """Should succeed before timeout."""
    limiter = TokenBucketLimiter(RateLimit(messages_per_second=10, burst_size=1))
    
    # Exhaust the token
    await limiter.acquire()
    
    # Start wait_and_acquire - should succeed before timeout
    start = time.monotonic()
    result = await limiter.wait_and_acquire(max_wait=1.0)
    elapsed = time.monotonic() - start
    
    assert result is True
    assert elapsed < 1.0  # Should complete well before timeout


@pytest.mark.asyncio
async def test_wait_and_acquire_times_out():
    """Should fail after timeout."""
    # Very slow rate: 1 token per 10 seconds, burst=1
    limiter = TokenBucketLimiter(RateLimit(messages_per_second=0.1, burst_size=1))
    
    # Exhaust the token
    await limiter.acquire()
    
    # Wait with very short timeout - should timeout
    start = time.monotonic()
    result = await limiter.wait_and_acquire(max_wait=0.2)
    elapsed = time.monotonic() - start
    
    assert result is False
    assert elapsed >= 0.2  # Should have waited close to max_wait


# =============================================================================
# Thread Safety Tests
# =============================================================================

@pytest.mark.asyncio
async def test_concurrent_acquire_thread_safety():
    """Multiple concurrent acquires should work correctly."""
    limiter = TokenBucketLimiter(RateLimit(messages_per_second=100, burst_size=10))
    
    # Run many concurrent acquires
    num_tasks = 20
    results = await asyncio.gather(*[limiter.acquire() for _ in range(num_tasks)])
    
    # With burst=10, only 10 should succeed
    successful = sum(1 for r in results if r)
    assert successful == 10
    
    # Others should fail
    failed = sum(1 for r in results if not r)
    assert failed == 10


@pytest.mark.asyncio
async def test_concurrent_wait_and_acquire():
    """Multiple concurrent wait_and_acquire should be thread-safe."""
    limiter = TokenBucketLimiter(RateLimit(messages_per_second=10, burst_size=1))
    
    # Exhaust the token
    await limiter.acquire()
    
    # Start multiple wait_and_acquire concurrently
    # Only one should eventually get the token first, but due to timing
    # multiple might succeed if they check at slightly different times
    async def try_acquire():
        return await limiter.wait_and_acquire(max_wait=2.0)
    
    results = await asyncio.gather(*[try_acquire() for _ in range(3)])
    
    # At least one should succeed (the fastest one)
    successful = sum(1 for r in results if r)
    assert successful >= 1


# =============================================================================
# Edge Cases
# =============================================================================

@pytest.mark.asyncio
async def test_zero_rate():
    """Edge case with zero rate - should still allow burst."""
    limiter = TokenBucketLimiter(RateLimit(messages_per_second=0, burst_size=3))
    
    # Burst should still work
    result1 = await limiter.acquire()
    result2 = await limiter.acquire()
    result3 = await limiter.acquire()
    result4 = await limiter.acquire()  # Should fail
    
    assert result1 is True
    assert result2 is True
    assert result3 is True
    assert result4 is False
    
    # No refill should occur over time with zero rate
    await asyncio.sleep(0.5)
    result = await limiter.acquire()
    assert result is False


@pytest.mark.asyncio
async def test_available_tokens_property():
    """Property returns reasonable value."""
    limiter = TokenBucketLimiter(RateLimit(messages_per_second=10, burst_size=5))
    
    # Initially should have full burst
    tokens = limiter.available_tokens
    assert tokens == 5
    
    # After acquiring, should have one less (accounting for slight refill)
    await limiter.acquire()
    tokens = limiter.available_tokens
    assert 3 < tokens <= 5  # May have slight refill, capped at burst
    
    # Exhaust remaining tokens
    for _ in range(4):
        await limiter.acquire()
    
    tokens = limiter.available_tokens
    assert tokens < 1


@pytest.mark.asyncio
async def test_high_rate_refill():
    """Test with high rate for faster refill."""
    limiter = TokenBucketLimiter(RateLimit(messages_per_second=1000, burst_size=2))
    
    # Exhaust burst
    await limiter.acquire()
    await limiter.acquire()
    
    # With rate=1000, after 0.01s should have 10 tokens
    await asyncio.sleep(0.01)
    
    result = await limiter.acquire()
    assert result is True


@pytest.mark.asyncio
async def test_multiple_refills():
    """Test multiple refill cycles."""
    limiter = TokenBucketLimiter(RateLimit(messages_per_second=2, burst_size=1))
    
    # Exhaust
    await limiter.acquire()
    
    # Wait for refill multiple times
    for _ in range(3):
        await asyncio.sleep(0.6)  # Should refill ~1.2 tokens
        result = await limiter.acquire()
        assert result is True


@pytest.mark.asyncio
async def test_burst_not_exceeded_by_refill():
    """Tokens should never exceed burst_size."""
    limiter = TokenBucketLimiter(RateLimit(messages_per_second=100, burst_size=5))
    
    # Wait long enough for many refills
    await asyncio.sleep(1.0)  # Should try to add 100 tokens
    
    # But should still be capped at burst_size
    tokens = limiter.available_tokens
    assert tokens <= 5
