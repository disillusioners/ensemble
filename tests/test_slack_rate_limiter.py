"""Tests for Slack tiered rate limiter."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from daemon.sources.adapters.slack.rate_limiter import (
    SlackTier,
    SlackTieredRateLimiter,
    TIER_CONFIGS,
    METHOD_TIER_MAP,
    TierConfig,
    TokenBucket,
)


class TestTierConfigs:
    """Test tier configuration values."""

    def test_tier_configs_have_correct_rate_limits(self):
        """Verify TIER_1=1/min, TIER_2=5/min, TIER_3=50/min, TIER_4=100/min."""
        assert TIER_CONFIGS[SlackTier.TIER_1].requests_per_minute == 1
        assert TIER_CONFIGS[SlackTier.TIER_1].burst_size == 1

        assert TIER_CONFIGS[SlackTier.TIER_2].requests_per_minute == 5
        assert TIER_CONFIGS[SlackTier.TIER_2].burst_size == 5

        assert TIER_CONFIGS[SlackTier.TIER_3].requests_per_minute == 50
        assert TIER_CONFIGS[SlackTier.TIER_3].burst_size == 50

        assert TIER_CONFIGS[SlackTier.TIER_4].requests_per_minute == 100
        assert TIER_CONFIGS[SlackTier.TIER_4].burst_size == 100


class TestMethodToTierMapping:
    """Test method-to-tier mapping."""

    def test_method_to_tier_mapping_chat_post_message(self):
        """chat.postMessage should be TIER_2."""
        assert METHOD_TIER_MAP["chat.postMessage"] == SlackTier.TIER_2

    def test_method_to_tier_mapping_chat_update(self):
        """chat.update should be TIER_2."""
        assert METHOD_TIER_MAP["chat.update"] == SlackTier.TIER_2

    def test_method_to_tier_mapping_conversations_info(self):
        """conversations.info should be TIER_3."""
        assert METHOD_TIER_MAP["conversations.info"] == SlackTier.TIER_3

    def test_method_to_tier_mapping_auth_test(self):
        """auth.test should be TIER_4."""
        assert METHOD_TIER_MAP["auth.test"] == SlackTier.TIER_4

    def test_method_to_tier_mapping_admin_users_list(self):
        """admin.users.list should be TIER_1."""
        assert METHOD_TIER_MAP["admin.users.list"] == SlackTier.TIER_1

    def test_method_to_tier_mapping_unknown_defaults_to_tier_3(self):
        """Unknown methods default to TIER_3."""
        unknown_methods = [
            "unknown.method",
            "slack.method.that.does.not.exist",
            "custom.action",
        ]
        for method in unknown_methods:
            assert method not in METHOD_TIER_MAP, f"{method} should not be in map"
            # Test through the limiter's _get_tier method
            limiter = SlackTieredRateLimiter()
            assert limiter._get_tier(method) == SlackTier.TIER_3


class TestSlackTieredRateLimiter:
    """Test SlackTieredRateLimiter class."""

    @pytest.fixture
    def limiter(self):
        """Create a fresh rate limiter for each test."""
        return SlackTieredRateLimiter()

    def test_get_tier_for_known_method(self, limiter):
        """Test getting tier for known methods."""
        assert limiter._get_tier("chat.postMessage") == SlackTier.TIER_2
        assert limiter._get_tier("auth.test") == SlackTier.TIER_4
        assert limiter._get_tier("conversations.list") == SlackTier.TIER_3

    def test_get_tier_for_unknown_method(self, limiter):
        """Unknown methods should return TIER_3."""
        assert limiter._get_tier("unknown.method") == SlackTier.TIER_3

    @pytest.mark.asyncio
    async def test_acquire_success(self, limiter):
        """Test successful token acquisition."""
        result = await limiter.acquire("auth.test")
        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_returns_false_when_empty(self, limiter):
        """Test acquire returns False when no tokens available."""
        # Fill up the TIER_1 bucket
        tier1_bucket = limiter._buckets[SlackTier.TIER_1]
        async with tier1_bucket._lock:
            tier1_bucket._tokens = 0

        result = await limiter.acquire("admin.users.list")
        assert result is False


class TestAcquireAndExecute:
    """Test acquire_and_execute functionality."""

    @pytest.fixture
    def limiter(self):
        """Create a fresh rate limiter for each test."""
        return SlackTieredRateLimiter()

    @pytest.mark.asyncio
    async def test_acquire_and_execute_success(self, limiter):
        """Test successful execution after rate limit acquisition."""
        mock_fn = AsyncMock(return_value={"status": "ok", "data": "result"})

        success, result = await limiter.acquire_and_execute(
            "auth.test",
            mock_fn,
            max_wait=5.0
        )

        assert success is True
        assert result == {"status": "ok", "data": "result"}
        mock_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_acquire_and_execute_with_chat_post_message(self, limiter):
        """Test acquire_and_execute with chat.postMessage (TIER_2)."""
        mock_fn = AsyncMock(return_value={"ts": "1234567890"})

        success, result = await limiter.acquire_and_execute(
            "chat.postMessage",
            mock_fn,
            max_wait=5.0
        )

        assert success is True
        assert result == {"ts": "1234567890"}
        mock_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_acquire_and_execute_function_exception(self, limiter):
        """Test acquire_and_execute handles function exceptions."""
        async def failing_fn():
            raise ValueError("API error")

        success, result = await limiter.acquire_and_execute(
            "auth.test",
            failing_fn,
            max_wait=5.0
        )

        assert success is False
        assert result is None

    @pytest.mark.asyncio
    async def test_acquire_and_execute_timeout(self, limiter):
        """Test failure when rate limit can't be acquired."""
        # Create a limiter and exhaust TIER_1 bucket
        limiter = SlackTieredRateLimiter()
        tier1_bucket = limiter._buckets[SlackTier.TIER_1]

        # Drain all tokens and prevent refill by holding the lock
        async with tier1_bucket._lock:
            tier1_bucket._tokens = 0
            tier1_bucket._last_refill = float('inf')  # Prevent time-based refill

        # Mock the bucket's acquire to always return False
        with patch.object(tier1_bucket, 'acquire', return_value=False):
            mock_fn = AsyncMock(return_value="should not execute")

            success, result = await limiter.acquire_and_execute(
                "admin.users.list",  # TIER_1 method
                mock_fn,
                max_wait=0.1  # Very short timeout
            )

            assert success is False
            assert result is None
            mock_fn.assert_not_called()


class TestTierStatus:
    """Test status reporting methods."""

    @pytest.fixture
    def limiter(self):
        """Create a fresh rate limiter for each test."""
        return SlackTieredRateLimiter()

    def test_get_tier_status(self, limiter):
        """Verify status reporting for a single tier."""
        status = limiter.get_tier_status(SlackTier.TIER_2)

        assert status["tier"] == 2
        assert status["tier_name"] == "TIER_2"
        assert "config" in status
        assert status["config"]["requests_per_minute"] == 5
        assert status["config"]["burst_size"] == 5

    def test_get_tier_status_invalid_tier(self, limiter):
        """Test status for invalid tier returns error."""
        # Test that passing a tier not in buckets returns error
        # We can't create an invalid SlackTier since it's an IntEnum
        # Instead test with a tier value that exists but has no bucket
        # by mocking the buckets dict
        original_buckets = limiter._buckets.copy()
        limiter._buckets = {}  # Empty buckets

        status = limiter.get_tier_status(SlackTier.TIER_2)

        assert "error" in status
        limiter._buckets = original_buckets  # Restore

    def test_get_all_status(self, limiter):
        """Verify all tiers reported."""
        status = limiter.get_all_status()

        assert "TIER_1" in status
        assert "TIER_2" in status
        assert "TIER_3" in status
        assert "TIER_4" in status

        # Verify each tier has correct rate
        assert status["TIER_1"]["config"]["requests_per_minute"] == 1
        assert status["TIER_2"]["config"]["requests_per_minute"] == 5
        assert status["TIER_3"]["config"]["requests_per_minute"] == 50
        assert status["TIER_4"]["config"]["requests_per_minute"] == 100


class TestTokenBucket:
    """Test TokenBucket implementation."""

    def test_token_bucket_initialization(self):
        """Test token bucket initializes with full tokens."""
        config = TierConfig(requests_per_minute=10, burst_size=5)
        bucket = TokenBucket(config)

        assert bucket._tokens == 5.0
        assert bucket._rate == config

    @pytest.mark.asyncio
    async def test_token_bucket_acquire(self):
        """Test token bucket acquire decrements tokens."""
        # Use very slow refill rate (1/min) so no refill happens during test
        config = TierConfig(requests_per_minute=1, burst_size=2)
        bucket = TokenBucket(config)

        # First acquire should succeed
        result = await bucket.acquire()
        assert result is True
        assert bucket._tokens < 2.0  # Tokens decreased

        # Second acquire should also succeed
        result = await bucket.acquire()
        assert result is True
        # Third acquire should fail (no tokens left, and refill is too slow)
        result = await bucket.acquire()
        assert result is False

    @pytest.mark.asyncio
    async def test_token_bucket_wait_and_acquire_success(self):
        """Test wait_and_acquire succeeds when tokens become available."""
        config = TierConfig(requests_per_minute=600, burst_size=1)  # Fast refill
        bucket = TokenBucket(config)

        # Drain tokens
        await bucket.acquire()
        assert bucket._tokens == 0.0

        # Wait should eventually succeed (token should refill quickly)
        result = await bucket.wait_and_acquire(max_wait=2.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_token_bucket_wait_and_acquire_timeout(self):
        """Test wait_and_acquire returns False on timeout."""
        config = TierConfig(requests_per_minute=0.001, burst_size=1)  # Very slow
        bucket = TokenBucket(config)

        # Drain tokens
        await bucket.acquire()

        # Wait should timeout
        result = await bucket.wait_and_acquire(max_wait=0.1)
        assert result is False
