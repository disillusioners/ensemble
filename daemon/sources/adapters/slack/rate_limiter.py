"""Tiered rate limiter for Slack API with per-method rate limits."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum

logger = logging.getLogger(__name__)


class SlackTier(IntEnum):
    """Slack API rate limit tiers.

    Slack has 4 tiers based on method impact:
    - Tier 1: 1 request/min (admin, expensive methods)
    - Tier 2: 5 requests/min (write operations)
    - Tier 3: 50 requests/min (most read operations)
    - Tier 4: 100+ requests/min (simple reads)
    """
    TIER_1 = 1  # 1 req/min
    TIER_2 = 2  # 5 req/min
    TIER_3 = 3  # 50 req/min
    TIER_4 = 4  # 100+ req/min


# Method to tier mapping for Slack API
METHOD_TIER_MAP: dict[str, SlackTier] = {
    # Tier 1: 1 req/min (admin methods, expensive operations)
    "admin.analytics.getFile": SlackTier.TIER_1,
    "admin.conversations.setTeams": SlackTier.TIER_1,
    "admin.teams.settings.info": SlackTier.TIER_1,
    "admin.users.list": SlackTier.TIER_1,

    # Tier 2: 5 req/min (write operations, more expensive reads)
    "chat.postMessage": SlackTier.TIER_2,
    "chat.update": SlackTier.TIER_2,
    "chat.delete": SlackTier.TIER_2,
    "chat.scheduleMessage": SlackTier.TIER_2,
    "chat.deleteScheduledMessage": SlackTier.TIER_2,
    "conversations.open": SlackTier.TIER_2,
    "conversations.create": SlackTier.TIER_2,
    "conversations.invite": SlackTier.TIER_2,
    "conversations.kick": SlackTier.TIER_2,
    "conversations.leave": SlackTier.TIER_2,
    "conversations.rename": SlackTier.TIER_2,
    "conversations.setPurpose": SlackTier.TIER_2,
    "conversations.setTopic": SlackTier.TIER_2,
    "conversations.unarchive": SlackTier.TIER_2,
    "users.lookupByEmail": SlackTier.TIER_2,

    # Tier 3: 50 req/min (standard read operations)
    "conversations.info": SlackTier.TIER_3,
    "conversations.list": SlackTier.TIER_3,
    "conversations.members": SlackTier.TIER_3,
    "conversations.history": SlackTier.TIER_3,
    "conversations.replies": SlackTier.TIER_3,
    "users.info": SlackTier.TIER_3,
    "users.list": SlackTier.TIER_3,
    "files.info": SlackTier.TIER_3,
    "files.sharedPublicURL": SlackTier.TIER_3,

    # Tier 4: 100+ req/min (simple reads, lightweight operations)
    "auth.test": SlackTier.TIER_4,
    "bots.info": SlackTier.TIER_4,
    "team.info": SlackTier.TIER_4,
    "files.list": SlackTier.TIER_4,
    "reactions.list": SlackTier.TIER_4,
    "bookmarks.list": SlackTier.TIER_4,
}


@dataclass
class TierConfig:
    """Configuration for a rate limit tier."""
    requests_per_minute: float
    burst_size: int


# Tier configurations
TIER_CONFIGS: dict[SlackTier, TierConfig] = {
    SlackTier.TIER_1: TierConfig(requests_per_minute=1, burst_size=1),
    SlackTier.TIER_2: TierConfig(requests_per_minute=5, burst_size=5),
    SlackTier.TIER_3: TierConfig(requests_per_minute=50, burst_size=50),
    SlackTier.TIER_4: TierConfig(requests_per_minute=100, burst_size=100),
}


class TokenBucket:
    """Token bucket for rate limiting."""

    def __init__(self, rate: TierConfig):
        self._rate = rate
        self._tokens = float(rate.burst_size)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """Try to acquire a token. Returns True if allowed."""
        async with self._lock:
            return self._try_acquire_unlocked()

    def _try_acquire_unlocked(self) -> bool:
        """Non-blocking acquire (must hold lock)."""
        now = time.monotonic()
        elapsed = now - self._last_refill

        # Refill tokens based on elapsed time
        self._tokens = min(
            self._rate.burst_size,
            self._tokens + elapsed * self._rate.requests_per_minute / 60.0
        )
        self._last_refill = now

        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False

    async def wait_and_acquire(self, max_wait: float = 5.0) -> bool:
        """Wait up to max_wait for a token.

        Args:
            max_wait: Maximum seconds to wait.

        Returns:
            True if token acquired, False if timeout.
        """
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            if await self.acquire():
                return True
            await asyncio.sleep(0.1)
        return False


class SlackTieredRateLimiter:
    """Tiered rate limiter for Slack API methods.

    Implements per-tier token buckets following Slack's rate limit tiers.
    Each tier has its own bucket with different refill rates.
    """

    def __init__(self) -> None:
        """Initialize tiered rate limiter."""
        self._buckets: dict[SlackTier, TokenBucket] = {
            tier: TokenBucket(config)
            for tier, config in TIER_CONFIGS.items()
        }
        self._lock = asyncio.Lock()

    def _get_tier(self, method: str) -> SlackTier:
        """Get the tier for a Slack API method.

        Args:
            method: The Slack API method name (e.g., "chat.postMessage").

        Returns:
            The tier for this method (defaults to TIER_3).
        """
        return METHOD_TIER_MAP.get(method, SlackTier.TIER_3)

    def _get_bucket(self, method: str) -> TokenBucket:
        """Get the token bucket for a method.

        Args:
            method: The Slack API method name.

        Returns:
            The token bucket for this method's tier.
        """
        tier = self._get_tier(method)
        return self._buckets[tier]

    async def acquire(self, method: str) -> bool:
        """Try to acquire rate limit token for a method.

        Args:
            method: The Slack API method name.

        Returns:
            True if allowed, False otherwise.
        """
        bucket = self._get_bucket(method)
        return await bucket.acquire()

    async def wait_and_acquire(self, method: str, max_wait: float = 30.0) -> bool:
        """Wait up to max_wait for a rate limit token.

        Args:
            method: The Slack API method name.
            max_wait: Maximum seconds to wait.

        Returns:
            True if token acquired, False if timeout.
        """
        bucket = self._get_bucket(method)
        tier = self._get_tier(method)

        # Calculate appropriate sleep interval based on tier
        if tier == SlackTier.TIER_1:
            sleep_interval = 1.0  # 1 req/min = wait up to 60s
            max_wait = min(max_wait, 60.0)
        elif tier == SlackTier.TIER_2:
            sleep_interval = 0.5  # 5 req/min
            max_wait = min(max_wait, 15.0)
        else:
            sleep_interval = 0.1

        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            if await bucket.acquire():
                return True
            await asyncio.sleep(sleep_interval)
        return False

    async def acquire_and_execute(
        self,
        method: str,
        fn,
        max_wait: float = 30.0,
    ) -> tuple[bool, any]:
        """Acquire rate limit token and execute a function.

        This is the main method for rate-limited API calls.

        Args:
            method: The Slack API method name.
            fn: Async function to execute after acquiring token.
            max_wait: Maximum seconds to wait for rate limit.

        Returns:
            Tuple of (success, result) where success is False if
            rate limit couldn't be acquired.
        """
        # Auto-increase max_wait for Tier 1 methods (1 req/min)
        tier = self._get_tier(method)
        if tier == SlackTier.TIER_1:
            max_wait = max(max_wait, 65.0)  # Ensure at least 65s for Tier 1

        # Wait for rate limit
        if not await self.wait_and_acquire(method, max_wait=max_wait):
            logger.warning(f"Rate limit timeout for method={method}")
            return False, None

        # Execute the function
        try:
            result = await fn()
            return True, result
        except Exception as e:
            logger.error(f"Error executing {method}: {e}")
            return False, None

    def get_tier_status(self, tier: SlackTier) -> dict:
        """Get status information for a tier.

        Args:
            tier: The tier to get status for.

        Returns:
            Dict with tier status information.
        """
        bucket = self._buckets.get(tier)
        if bucket is None:
            return {"error": f"Unknown tier: {tier}"}

        return {
            "tier": int(tier),
            "tier_name": tier.name,
            "config": {
                "requests_per_minute": bucket._rate.requests_per_minute,
                "burst_size": bucket._rate.burst_size,
            },
        }

    def get_all_status(self) -> dict:
        """Get status for all tiers.

        Returns:
            Dict with status for all tiers.
        """
        return {tier.name: self.get_tier_status(tier) for tier in SlackTier}
