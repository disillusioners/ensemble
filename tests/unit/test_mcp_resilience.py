"""Unit tests for the MCP resilience primitives.

Covers the generic half of the Phase 4 resilience layer:

- ``RetryPolicy`` — backoff, jitter, retryability classification.
- ``ResultCache`` — TTL expiry, LRU eviction, server-scoped invalidation.
- ``classify_exception`` — pattern-based mapping of raw exceptions
  to the ``McpError`` hierarchy.
- ``ResilienceManager`` — registration, lookups, staleness tracking.
- ``is_read_tool`` — read/write classification with prefix stripping.
- ``CircuitBreaker`` (via ``ResilienceManager``) — state transitions
  through CLOSED → OPEN → HALF_OPEN → CLOSED.

We unmock ``daemon.mcp.tool_adapter`` first (the root conftest
replaces it with a stub that lacks the real ``_build_lazy_coroutine``).
The resilience module itself is NOT mocked by the conftest, so it
imports cleanly.
"""
import asyncio
import sys
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Unmock daemon.mcp.tool_adapter — the conftest mock doesn't have
# ``_build_lazy_coroutine`` (it's a MagicMock that returns MagicMock).
# ---------------------------------------------------------------------------
_mock_tool_adapter = sys.modules.pop("daemon.mcp.tool_adapter", None)

from daemon.mcp.errors import (  # noqa: E402
    McpAuthError,
    McpError,
    McpToolError,
    McpTransientError,
    McpUnavailableError,
)
from daemon.mcp.resilience import (  # noqa: E402
    AuthFailureClassifier,  # alias for classify_exception
    ResilienceConfig,
    ResilienceManager,
    ResultCache,
    RetryPolicy,
    classify_exception,
    is_read_tool,
)
from daemon.mcp.tool_adapter import (  # noqa: E402
    McpSessionProvider,
    _build_lazy_coroutine,
)
from daemon.sources.circuit_breaker import (  # noqa: E402
    CircuitBreaker,
    CircuitState,
)

if _mock_tool_adapter is not None:
    sys.modules["daemon.mcp.tool_adapter"] = _mock_tool_adapter


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    """Tests for the ``RetryPolicy`` exponential-backoff primitive."""

    @pytest.mark.asyncio
    async def test_succeeds_after_transient_failures(self):
        """Fail 2x, succeed on 3rd attempt → returns result."""
        attempts = {"n": 0}

        async def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise McpTransientError(f"transient {attempts['n']}")
            return "ok"

        policy = RetryPolicy(max_attempts=3, base_delay=0.0, jitter=False)
        result = await policy.execute(flaky)
        assert result == "ok"
        assert attempts["n"] == 3

    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts(self):
        """Always-failing transient → raises after max_attempts."""
        attempts = {"n": 0}

        async def always_fail():
            attempts["n"] += 1
            raise McpTransientError(f"fail {attempts['n']}")

        policy = RetryPolicy(max_attempts=3, base_delay=0.0, jitter=False)
        with pytest.raises(McpTransientError):
            await policy.execute(always_fail)
        assert attempts["n"] == 3

    @pytest.mark.asyncio
    async def test_does_not_retry_auth_errors(self):
        """McpAuthError raised once → propagates immediately (no retry)."""
        attempts = {"n": 0}

        async def auth_fail():
            attempts["n"] += 1
            raise McpAuthError("bad key")

        policy = RetryPolicy(max_attempts=3, base_delay=0.0, jitter=False)
        with pytest.raises(McpAuthError):
            await policy.execute(auth_fail)
        # Should NOT have retried — auth errors are non-retryable.
        assert attempts["n"] == 1

    @pytest.mark.asyncio
    async def test_does_not_retry_tool_errors(self):
        """McpToolError raised once → propagates immediately (no retry)."""
        attempts = {"n": 0}

        async def tool_fail():
            attempts["n"] += 1
            raise McpToolError("bad input")

        policy = RetryPolicy(max_attempts=3, base_delay=0.0, jitter=False)
        with pytest.raises(McpToolError):
            await policy.execute(tool_fail)
        assert attempts["n"] == 1

    @pytest.mark.asyncio
    async def test_exponential_backoff_delays(self, monkeypatch):
        """Verify the delay sequence is base_delay * 2^(attempt-1).

        With max_attempts=3, base_delay=1.0, jitter=False, max_delay=8.0:
        - attempt 1 fails → sleep 1.0s before attempt 2
        - attempt 2 fails → sleep 2.0s before attempt 3
        """
        sleeps: list[float] = []

        async def fake_sleep(s: float) -> None:
            sleeps.append(s)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        attempts = {"n": 0}

        async def always_fail():
            attempts["n"] += 1
            raise McpTransientError(f"fail {attempts['n']}")

        policy = RetryPolicy(
            max_attempts=3, base_delay=1.0, max_delay=8.0, jitter=False
        )
        with pytest.raises(McpTransientError):
            await policy.execute(always_fail)
        # Two sleeps (between attempts 1→2 and 2→3), no sleep after 3rd.
        assert len(sleeps) == 2
        assert sleeps[0] == pytest.approx(1.0)
        assert sleeps[1] == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_max_delay_cap(self, monkeypatch):
        """Delay is capped at ``max_delay`` even when exponent overflows."""
        sleeps: list[float] = []

        async def fake_sleep(s: float) -> None:
            sleeps.append(s)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        async def always_fail():
            raise McpTransientError("x")

        policy = RetryPolicy(
            max_attempts=4, base_delay=1.0, max_delay=2.0, jitter=False
        )
        with pytest.raises(McpTransientError):
            await policy.execute(always_fail)
        # Attempt 1→2: 1.0, 2→3: 2.0 (capped), 3→4: 2.0 (capped).
        assert sleeps == [1.0, 2.0, 2.0]

    @pytest.mark.asyncio
    async def test_jitter_adds_random_component(self, monkeypatch):
        """With jitter=True, the sleep is base * (0.5 .. 1.5)."""
        sleeps: list[float] = []

        async def fake_sleep(s: float) -> None:
            sleeps.append(s)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        async def always_fail():
            raise McpTransientError("x")

        policy = RetryPolicy(
            max_attempts=2, base_delay=2.0, max_delay=8.0, jitter=True
        )
        with pytest.raises(McpTransientError):
            await policy.execute(always_fail)
        # One sleep between attempt 1 and 2. Should be 2.0 * (0.5..1.5).
        assert len(sleeps) == 1
        assert 1.0 <= sleeps[0] <= 3.0

    @pytest.mark.asyncio
    async def test_passes_args_and_kwargs(self):
        """``execute`` forwards positional + keyword args to func."""

        async def echo(*args, **kwargs):
            return (args, kwargs)

        policy = RetryPolicy(max_attempts=1, base_delay=0.0, jitter=False)
        result = await policy.execute(echo, "a", "b", k1="v1", k2="v2")
        assert result == (("a", "b"), {"k1": "v1", "k2": "v2"})

    @pytest.mark.asyncio
    async def test_timeout_is_retryable(self, monkeypatch):
        """``asyncio.TimeoutError`` is in the default retryable set."""
        attempts = {"n": 0}

        async def timeouts():
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise asyncio.TimeoutError("slow")
            return "recovered"

        # Default policy: max_attempts=3, base_delay=0.0
        policy = RetryPolicy(base_delay=0.0, jitter=False)
        result = await policy.execute(timeouts)
        assert result == "recovered"
        assert attempts["n"] == 2


# ---------------------------------------------------------------------------
# classify_exception
# ---------------------------------------------------------------------------


class TestAuthFailureClassifier:
    """Tests for the raw-exception classifier."""

    def test_auth_classifier_detects_401(self):
        """'401' substring → McpAuthError."""
        e = classify_exception(Exception("HTTP 401 Unauthorized"))
        assert isinstance(e, McpAuthError)

    def test_auth_classifier_detects_403(self):
        """'403' substring → McpAuthError."""
        e = classify_exception(Exception("403 Forbidden"))
        assert isinstance(e, McpAuthError)

    def test_auth_classifier_detects_unauthorized_keyword(self):
        """'unauthorized' substring → McpAuthError."""
        e = classify_exception(Exception("connection unauthorized"))
        assert isinstance(e, McpAuthError)

    def test_auth_classifier_detects_forbidden_keyword(self):
        """'forbidden' substring → McpAuthError."""
        e = classify_exception(Exception("Access forbidden"))
        assert isinstance(e, McpAuthError)

    def test_auth_classifier_case_insensitive(self):
        """Pattern matching is case-insensitive."""
        e = classify_exception(Exception("UNAUTHORIZED"))
        assert isinstance(e, McpAuthError)

    def test_5xx_is_transient(self):
        """500/502/503/504 → McpTransientError."""
        for code in ("500", "502", "503", "504"):
            e = classify_exception(Exception(f"HTTP {code}"))
            assert isinstance(e, McpTransientError), (
                f"{code} should be transient, got {type(e).__name__}"
            )

    def test_timeout_message_is_transient(self):
        """'timeout' substring → McpTransientError."""
        e = classify_exception(Exception("Request timeout"))
        assert isinstance(e, McpTransientError)

    def test_timed_out_message_is_transient(self):
        """'timed out' substring → McpTransientError."""
        e = classify_exception(Exception("Connection timed out after 30s"))
        assert isinstance(e, McpTransientError)

    def test_connection_reset_is_transient(self):
        """'connection reset' substring → McpTransientError."""
        e = classify_exception(Exception("connection reset by peer"))
        assert isinstance(e, McpTransientError)

    def test_connection_refused_is_transient(self):
        """'connection refused' substring → McpTransientError."""
        e = classify_exception(Exception("connection refused"))
        assert isinstance(e, McpTransientError)

    def test_unknown_is_tool_error(self):
        """Unrecognized message → McpToolError (non-retryable)."""
        e = classify_exception(Exception("something weird happened"))
        assert isinstance(e, McpToolError)

    def test_already_classified_passthrough(self):
        """Existing McpError instances pass through unchanged."""
        original = McpAuthError("already classified")
        result = classify_exception(original)
        assert result is original, (
            "Existing McpError should pass through, not be re-classified"
        )

    def test_legacy_alias_name(self):
        """``AuthFailureClassifier`` is an alias for ``classify_exception``."""
        assert AuthFailureClassifier is classify_exception


# ---------------------------------------------------------------------------
# ResultCache
# ---------------------------------------------------------------------------


class TestResultCache:
    """Tests for the in-memory TTL + LRU cache."""

    @pytest.mark.asyncio
    async def test_hit_within_ttl(self):
        """Set then immediate get → hit."""
        cache = ResultCache(default_ttl=60.0, max_entries=100)
        await cache.set("plane", "plane_list", {"k": 1}, "value-1")
        value, hit = await cache.get("plane", "plane_list", {"k": 1})
        assert hit is True
        assert value == "value-1"

    @pytest.mark.asyncio
    async def test_miss_after_ttl_expiry(self, monkeypatch):
        """Sleep past TTL → next get is a miss.

        We don't actually sleep — we advance the monotonic clock via
        ``time.monotonic`` patch.
        """
        cache = ResultCache(default_ttl=60.0, max_entries=100)
        await cache.set("plane", "plane_list", {"k": 1}, "value-1")

        # Advance monotonic clock by 61 seconds.
        base = time.monotonic()
        monkeypatch.setattr(
            time, "monotonic", lambda: base + 61
        )

        value, hit = await cache.get("plane", "plane_list", {"k": 1})
        assert hit is False
        assert value is None

    @pytest.mark.asyncio
    async def test_miss_for_unknown_key(self):
        """Get on never-set key → miss."""
        cache = ResultCache(default_ttl=60.0, max_entries=100)
        value, hit = await cache.get("plane", "plane_list", {"k": 1})
        assert hit is False
        assert value is None

    @pytest.mark.asyncio
    async def test_kwargs_key_normalization(self):
        """Same kwargs in different key order → same key (canonical hash)."""
        cache = ResultCache(default_ttl=60.0, max_entries=100)
        await cache.set("plane", "plane_list", {"a": 1, "b": 2}, "v")
        value, hit = await cache.get("plane", "plane_list", {"b": 2, "a": 1})
        assert hit is True
        assert value == "v"

    @pytest.mark.asyncio
    async def test_different_kwargs_different_keys(self):
        """Different kwargs → separate cache entries."""
        cache = ResultCache(default_ttl=60.0, max_entries=100)
        await cache.set("plane", "plane_list", {"k": 1}, "v1")
        await cache.set("plane", "plane_list", {"k": 2}, "v2")
        v1, h1 = await cache.get("plane", "plane_list", {"k": 1})
        v2, h2 = await cache.get("plane", "plane_list", {"k": 2})
        assert h1 is True and v1 == "v1"
        assert h2 is True and v2 == "v2"

    @pytest.mark.asyncio
    async def test_ttl_override_per_set(self, monkeypatch):
        """``ttl`` kwarg overrides ``default_ttl``."""
        cache = ResultCache(default_ttl=60.0, max_entries=100)
        await cache.set(
            "plane", "plane_list", {"k": 1}, "v", ttl=2.0
        )

        base = time.monotonic()
        # At t+1s → still fresh.
        monkeypatch.setattr(time, "monotonic", lambda: base + 1)
        _, hit_fresh = await cache.get("plane", "plane_list", {"k": 1})
        assert hit_fresh is True
        # At t+3s → expired.
        monkeypatch.setattr(time, "monotonic", lambda: base + 3)
        _, hit_expired = await cache.get("plane", "plane_list", {"k": 1})
        assert hit_expired is False

    @pytest.mark.asyncio
    async def test_invalidate_by_server(self):
        """``invalidate_server`` drops ALL entries for that server."""
        cache = ResultCache(default_ttl=60.0, max_entries=100)
        # Plane entries.
        await cache.set("plane", "plane_list", {"k": 1}, "v1")
        await cache.set("plane", "plane_list", {"k": 2}, "v2")
        await cache.set("plane", "plane_get", {"id": "x"}, "v3")
        # Non-Plane entry — must survive.
        await cache.set("ctx7", "ctx7_get", {"q": "y"}, "v4")

        await cache.invalidate_server("plane")

        _, h1 = await cache.get("plane", "plane_list", {"k": 1})
        _, h2 = await cache.get("plane", "plane_list", {"k": 2})
        _, h3 = await cache.get("plane", "plane_get", {"id": "x"})
        _, h4 = await cache.get("ctx7", "ctx7_get", {"q": "y"})
        assert h1 is False
        assert h2 is False
        assert h3 is False
        assert h4 is True

    @pytest.mark.asyncio
    async def test_lru_eviction(self):
        """Cache evicts oldest entry when ``max_entries`` exceeded."""
        cache = ResultCache(default_ttl=60.0, max_entries=3)
        await cache.set("srv", "tool", {"k": 1}, "v1")
        await cache.set("srv", "tool", {"k": 2}, "v2")
        await cache.set("srv", "tool", {"k": 3}, "v3")
        # 4th set → evicts k=1 (oldest).
        await cache.set("srv", "tool", {"k": 4}, "v4")

        _, h1 = await cache.get("srv", "tool", {"k": 1})
        _, h2 = await cache.get("srv", "tool", {"k": 2})
        _, h3 = await cache.get("srv", "tool", {"k": 3})
        _, h4 = await cache.get("srv", "tool", {"k": 4})
        assert h1 is False, "oldest entry should be evicted"
        assert h2 is True
        assert h3 is True
        assert h4 is True


# ---------------------------------------------------------------------------
# CircuitBreaker (via ResilienceManager)
# ---------------------------------------------------------------------------


class TestCircuitBreakerFlow:
    """Circuit-breaker state transitions through the manager API."""

    @pytest.mark.asyncio
    async def test_opens_after_failure_threshold(self):
        """N failures (>= threshold) → state == OPEN."""
        config = ResilienceConfig(circuit_failure_threshold=5)
        mgr = ResilienceManager()
        mgr.register("plane", config)
        cb = mgr.get_circuit_breaker("plane")
        assert cb is not None
        assert cb.get_state() == "closed"

        for _ in range(5):
            await cb.record_failure()
        assert cb.get_state() == "open"
        # Next can_execute in OPEN state with no elapsed timeout → False.
        assert await cb.can_execute() is False

    @pytest.mark.asyncio
    async def test_does_not_open_below_threshold(self):
        """Threshold-1 failures → still CLOSED."""
        config = ResilienceConfig(circuit_failure_threshold=5)
        mgr = ResilienceManager()
        mgr.register("plane", config)
        cb = mgr.get_circuit_breaker("plane")

        for _ in range(4):
            await cb.record_failure()
        assert cb.get_state() == "closed"

    @pytest.mark.asyncio
    async def test_half_open_after_recovery_timeout(self, monkeypatch):
        """OPEN + elapsed recovery_timeout → HALF_OPEN on next can_execute."""
        config = ResilienceConfig(
            circuit_failure_threshold=5,
            circuit_recovery_timeout=60.0,
        )
        mgr = ResilienceManager()
        mgr.register("plane", config)
        cb = mgr.get_circuit_breaker("plane")

        for _ in range(5):
            await cb.record_failure()
        assert cb.get_state() == "open"

        # Advance monotonic clock past recovery_timeout.
        base = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: base + 61)
        # can_execute moves the breaker to HALF_OPEN and returns True.
        assert await cb.can_execute() is True
        assert cb.get_state() == "half_open"

    @pytest.mark.asyncio
    async def test_closes_on_success_after_half_open(self):
        """HALF_OPEN + record_success → CLOSED (recovery confirmed)."""
        config = ResilienceConfig(
            circuit_failure_threshold=5,
            circuit_recovery_timeout=0.0,  # immediate recovery
        )
        mgr = ResilienceManager()
        mgr.register("plane", config)
        cb = mgr.get_circuit_breaker("plane")

        for _ in range(5):
            await cb.record_failure()
        # recovery_timeout=0.0 → next can_execute immediately moves to HALF_OPEN.
        await cb.can_execute()
        assert cb.get_state() == "half_open"

        await cb.record_success()
        assert cb.get_state() == "closed"
        assert cb.failure_count == 0


# ---------------------------------------------------------------------------
# ResilienceManager
# ---------------------------------------------------------------------------


class TestResilienceManager:
    """Manager-level state lookups and lifecycle."""

    def test_register_creates_circuit_breaker(self):
        """register() creates a CircuitBreaker with config thresholds."""
        mgr = ResilienceManager()
        config = ResilienceConfig(
            circuit_failure_threshold=3,
            circuit_recovery_timeout=42.0,
        )
        mgr.register("plane", config)
        cb = mgr.get_circuit_breaker("plane")
        assert isinstance(cb, CircuitBreaker)
        assert cb.failure_threshold == 3
        assert cb.recovery_timeout == 42.0

    def test_register_creates_cache_when_ttl_set(self):
        """register() creates a cache when ``cache_ttl`` is not None."""
        mgr = ResilienceManager()
        config = ResilienceConfig(cache_ttl=60.0, cache_max_entries=50)
        mgr.register("plane", config)
        cache = mgr.get_cache("plane")
        assert isinstance(cache, ResultCache)
        assert cache._default_ttl == 60.0
        assert cache._max_entries == 50

    def test_register_no_cache_when_ttl_none(self):
        """register() leaves cache as ``None`` when ``cache_ttl=None``."""
        mgr = ResilienceManager()
        config = ResilienceConfig(cache_ttl=None)
        mgr.register("plane", config)
        assert mgr.get_cache("plane") is None

    def test_get_config_returns_registered_config(self):
        """get_config returns the registered config object."""
        mgr = ResilienceManager()
        config = ResilienceConfig(circuit_failure_threshold=99)
        mgr.register("plane", config)
        assert mgr.get_config("plane") is config

    def test_unknown_server_returns_none_for_all_lookups(self):
        """Servers never registered return ``None`` from all lookups."""
        mgr = ResilienceManager()
        assert mgr.get_config("never-seen") is None
        assert mgr.get_circuit_breaker("never-seen") is None
        assert mgr.get_cache("never-seen") is None

    def test_has_config(self):
        """has_config reports registered vs absent servers."""
        mgr = ResilienceManager()
        mgr.register("plane", ResilienceConfig())
        assert mgr.has_config("plane") is True
        assert mgr.has_config("context7") is False

    def test_record_success_and_is_stale(self, monkeypatch):
        """record_success updates the timestamp; is_stale reflects it."""
        mgr = ResilienceManager()
        mgr.register("plane", ResilienceConfig())

        # No success recorded → stale.
        assert mgr.is_stale("plane", threshold=10.0) is True

        # Record a success at t=0, threshold 60s.
        base = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: base)
        mgr.record_success("plane")
        assert mgr.is_stale("plane", threshold=60.0) is False

        # Advance 61s → stale again.
        monkeypatch.setattr(time, "monotonic", lambda: base + 61)
        assert mgr.is_stale("plane", threshold=60.0) is True

    def test_reset_circuit(self):
        """reset_circuit forces the breaker back to CLOSED."""
        mgr = ResilienceManager()
        mgr.register(
            "plane",
            ResilienceConfig(circuit_failure_threshold=1),
        )
        cb = mgr.get_circuit_breaker("plane")
        # Trip the breaker.
        asyncio.run(cb.record_failure())
        assert cb.get_state() == "open"

        mgr.reset_circuit("plane")
        assert cb.get_state() == "closed"
        assert cb.failure_count == 0

    def test_reset_circuit_unknown_server_is_noop(self):
        """reset_circuit on an unregistered server is a no-op (no error)."""
        mgr = ResilienceManager()
        # Should not raise.
        mgr.reset_circuit("never-registered")

    def test_reregister_replaces_state(self):
        """Re-registering overwrites the previous config + state."""
        mgr = ResilienceManager()
        mgr.register(
            "plane",
            ResilienceConfig(circuit_failure_threshold=5),
        )
        cb = mgr.get_circuit_breaker("plane")
        assert cb.failure_threshold == 5

        mgr.register(
            "plane",
            ResilienceConfig(circuit_failure_threshold=99),
        )
        cb_new = mgr.get_circuit_breaker("plane")
        assert cb_new.failure_threshold == 99


# ---------------------------------------------------------------------------
# is_read_tool
# ---------------------------------------------------------------------------


class TestIsReadTool:
    """Read/write classification with prefix stripping."""

    def test_read_with_plane_prefix(self):
        """``plane_list_issues`` → read (list_ pattern)."""
        cfg = ResilienceConfig(
            read_tool_patterns=("list_", "get_", "search_"),
            write_tool_patterns=("create_", "update_", "delete_"),
        )
        assert is_read_tool("plane_list_issues", cfg) is True

    def test_read_with_single_segment_prefix(self):
        """Adapted name with one prefix segment strips cleanly.

        Documents the actual contract: ``is_read_tool`` strips the
        leading ``_``-segment via ``split("_", 1)``. So a single
        ``_`` in the adapted name drops the prefix; a multi-segment
        prefix (``mcp_server_tool``) keeps ``server_tool`` which may
        not match the pattern — call sites should know their prefix
        shape.

        Callers must pass the ADAPTED name (e.g.
        ``plane_list_issues``); bare names (``list_issues``) are not
        a supported input shape.
        """
        cfg = ResilienceConfig(
            read_tool_patterns=("list_", "get_"),
            write_tool_patterns=("create_",),
        )
        # ``xy_list_issues`` → split ``xy_`` → ``list_issues`` →
        # matches ``list_`` → read.
        assert is_read_tool("xy_list_issues", cfg) is True

    def test_write_tool_with_prefix(self):
        """``plane_create_issue`` → write."""
        cfg = ResilienceConfig(
            read_tool_patterns=("list_", "get_", "search_"),
            write_tool_patterns=("create_", "update_", "delete_"),
        )
        assert is_read_tool("plane_create_issue", cfg) is False

    def test_no_patterns_configured(self):
        """No patterns configured → conservative False (not cacheable)."""
        cfg = ResilienceConfig()
        assert is_read_tool("plane_list_issues", cfg) is False

    def test_write_pattern_matches_at_stripped_start(self):
        """When stripped name starts with a write pattern, write wins.

        Documents the actual semantics: ``startswith`` is checked on
        the stripped name. ``plane_create_data`` → strip ``plane_`` →
        ``create_data`` → matches ``create_`` → write. There's no
        substring-search fallback for ``get_create_data``-style names
        (a hypothetical tool whose name begins with a read prefix but
        contains a write prefix later).
        """
        cfg = ResilienceConfig(
            read_tool_patterns=("get_",),
            write_tool_patterns=("create_",),
        )
        assert is_read_tool("plane_create_data", cfg) is False

    def test_search_pattern(self):
        """``search_*`` is read."""
        cfg = ResilienceConfig(
            read_tool_patterns=("list_", "get_", "search_"),
            write_tool_patterns=(),
        )
        assert is_read_tool("plane_search_issues", cfg) is True

    def test_only_write_patterns_no_match(self):
        """Only-write-patterns configured, no match → defaults to read.

        Conservative default: if we can't classify as write, treat as
        read so a downstream caching layer can still cache it. Better
        to over-cache than to silently drop legitimate read tools.
        """
        cfg = ResilienceConfig(
            read_tool_patterns=(),
            write_tool_patterns=("create_",),
        )
        # After stripping → "list_issues" — doesn't match any write.
        assert is_read_tool("plane_list_issues", cfg) is True


# ---------------------------------------------------------------------------
# End-to-end: resilience wired into the lazy coroutine
# ---------------------------------------------------------------------------


class TestLazyCoroutineResilienceIntegration:
    """End-to-end tests through ``_build_lazy_coroutine``.

    These exercise the full flow: cache → circuit → retry → fallback.
    The session is mocked so we don't need a real Plane server; the
    point is to prove ``_lazy_coroutine`` consults the
    ``ResilienceManager`` in the right order and produces the right
    output shape.
    """

    def _build(
        self,
        session,
        manager: "ResilienceManager | None" = None,
        adapted_name: str | None = None,
        timeout: float = 30.0,
    ):
        """Build a lazy coroutine with mocked session + optional manager."""
        from daemon.mcp.tool_adapter import McpSessionProvider

        provider = MagicMock()
        provider.get_session = AsyncMock(return_value=session)

        coro = _build_lazy_coroutine(
            server_name="plane",
            original_tool_name="list_issues",
            adapted_tool_name=adapted_name or "plane_list_issues",
            session_provider=provider,
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
            timeout_seconds=timeout,
            resilience_manager=manager,
        )
        return coro

    def _make_ok_session(self, text: str = "ok") -> MagicMock:
        """Build a session whose ``call_tool`` returns a successful result.

        The shape mimics an MCP ``CallToolResult`` (with ``content``,
        ``isError``, ``structuredContent``) so the production
        ``_convert_call_tool_result`` from ``langchain_mcp_adapters``
        can parse it. We also patch
        ``daemon.mcp.tool_adapter._convert_call_tool_result`` to a
        deterministic shim so tests assert on a stable shape (the
        production converter returns a content block with a UUID,
        which would make equality assertions fragile).
        """
        from types import SimpleNamespace
        session = MagicMock()
        result = SimpleNamespace(
            content=[SimpleNamespace(text=text)],
            isError=False,
            structuredContent=None,
        )
        session.call_tool = AsyncMock(return_value=result)
        return session

    @pytest.fixture(autouse=True)
    def _patch_convert(self, monkeypatch):
        """Patch ``_convert_call_tool_result`` for stable test assertions.

        The production converter (from ``langchain_mcp_adapters``)
        adds UUIDs to content blocks, which makes equality checks
        fragile. We replace it with a deterministic shim that
        mirrors the fallback implementation in
        ``daemon/mcp/tool_adapter.py`` (which handles ``SimpleNamespace``
        via duck typing on the ``text`` attribute).
        """
        def shim(result):
            if getattr(result, "isError", False):
                msg = "\n".join(
                    item.text for item in result.content
                    if hasattr(item, "text")
                ) or "MCP tool returned an error"
                from langchain_core.tools import ToolException
                raise ToolException(msg)
            return (
                [{"type": "text", "text": item.text}
                 for item in result.content
                 if hasattr(item, "text")],
                None,
            )

        monkeypatch.setattr(
            "daemon.mcp.tool_adapter._convert_call_tool_result",
            shim,
        )

    @pytest.mark.asyncio
    async def test_legacy_path_when_no_manager(self):
        """No manager → legacy path; single attempt; result returned as-is."""
        session = self._make_ok_session("legacy result")
        coro = self._build(session)

        result = await coro(project_id="p1")
        # Legacy path: result is the (content, artifact) tuple from
        # ``_convert_call_tool_result``.
        assert session.call_tool.call_count == 1
        content, artifact = result
        assert content[0]["text"] == "legacy result"
        assert artifact is None

    @pytest.mark.asyncio
    async def test_legacy_path_when_no_config(self):
        """Manager present but no config for this server → legacy path."""
        session = self._make_ok_session("ok")
        manager = ResilienceManager()  # empty — nothing registered
        coro = self._build(session, manager=manager)

        await coro(project_id="p1")
        # Should still call once — no resilience.
        assert session.call_tool.call_count == 1

    @pytest.mark.asyncio
    async def test_resilience_path_caches_read_result(self):
        """Read tool result is cached and returned on second call without re-fetch."""
        session = self._make_ok_session("data")
        manager = ResilienceManager()
        manager.register(
            "plane",
            ResilienceConfig(
                cache_ttl=60.0,
                read_tool_patterns=("list_",),
                write_tool_patterns=("create_",),
            ),
        )
        coro = self._build(session, manager=manager)

        # First call → hits the session.
        result1 = await coro(project_id="p1")
        assert session.call_tool.call_count == 1

        # Second call → cache hit; no additional session.call_tool.
        result2 = await coro(project_id="p1")
        assert session.call_tool.call_count == 1
        # Same result returned.
        assert result1 == result2

    @pytest.mark.asyncio
    async def test_resilience_path_different_kwargs_not_cached(self):
        """Different kwargs → different cache entries; second call hits session."""
        session = self._make_ok_session("data")
        manager = ResilienceManager()
        manager.register(
            "plane",
            ResilienceConfig(
                cache_ttl=60.0,
                read_tool_patterns=("list_",),
                write_tool_patterns=(),
            ),
        )
        coro = self._build(session, manager=manager)

        await coro(project_id="p1")
        await coro(project_id="p2")  # different kwargs → miss
        assert session.call_tool.call_count == 2

    @pytest.mark.asyncio
    async def test_resilience_path_retry_then_succeed(self):
        """Transient failures are retried; eventual success returns."""
        from daemon.mcp.errors import McpTransientError
        session = MagicMock()
        call_count = {"n": 0}

        async def call_tool(name, kwargs):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise McpTransientError("flaky")
            from types import SimpleNamespace
            return SimpleNamespace(
                content=[SimpleNamespace(text="recovered")],
                isError=False,
            )
        session.call_tool = call_tool

        manager = ResilienceManager()
        manager.register(
            "plane",
            ResilienceConfig(
                retry_policy=RetryPolicy(
                    max_attempts=3, base_delay=0.0, jitter=False
                ),
                read_tool_patterns=("list_",),
                write_tool_patterns=(),
            ),
        )
        coro = self._build(session, manager=manager)

        result = await coro(project_id="p1")
        content, _ = result
        assert content[0]["text"] == "recovered"
        assert call_count["n"] == 2  # 1 fail + 1 success

    @pytest.mark.asyncio
    async def test_resilience_path_auth_error_does_not_retry(self):
        """McpAuthError raised once → propagates immediately, no retry."""
        from daemon.mcp.errors import McpAuthError
        from langchain_core.tools import ToolException
        session = MagicMock()
        call_count = {"n": 0}

        async def call_tool(name, kwargs):
            call_count["n"] += 1
            raise McpAuthError("bad key")
        session.call_tool = call_tool

        manager = ResilienceManager()
        manager.register(
            "plane",
            ResilienceConfig(
                retry_policy=RetryPolicy(max_attempts=3, base_delay=0.0),
                read_tool_patterns=("list_",),
                write_tool_patterns=(),
            ),
        )
        coro = self._build(session, manager=manager)

        with pytest.raises(ToolException) as exc:
            await coro(project_id="p1")
        assert "authentication" in str(exc.value).lower()
        # Auth errors MUST NOT retry.
        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_resilience_path_write_invalidates_cache(self):
        """A write tool success invalidates the entire server cache."""
        # First do a read to populate the cache.
        read_session = self._make_ok_session("old data")
        read_mgr = ResilienceManager()
        read_mgr.register(
            "plane",
            ResilienceConfig(
                cache_ttl=60.0,
                read_tool_patterns=("list_", "get_"),
                write_tool_patterns=("create_",),
            ),
        )
        read_coro = self._build(
            read_session, manager=read_mgr, adapted_name="plane_list_issues"
        )
        await read_coro(project_id="p1")
        assert read_mgr.get_cache("plane")._entries  # non-empty

        # Now do a write with a different session/manager shape (same cache key though).
        write_session = self._make_ok_session("ok")
        write_mgr = ResilienceManager()
        write_mgr.register(
            "plane",
            ResilienceConfig(
                cache_ttl=60.0,
                read_tool_patterns=("list_", "get_"),
                write_tool_patterns=("create_",),
            ),
        )
        # Use a NEW manager but the same manager object so cache state
        # is shared — re-register doesn't reset the cache.
        write_mgr._caches = read_mgr._caches
        write_coro = self._build(
            write_session,
            manager=write_mgr,
            adapted_name="plane_create_issue",
        )
        await write_coro(project_id="p2")

        # Cache should be empty after the write.
        assert not write_mgr.get_cache("plane")._entries

    @pytest.mark.asyncio
    async def test_resilience_path_circuit_open_returns_fallback(self):
        """Circuit OPEN + recovery not elapsed → fallback JSON returned."""
        session = self._make_ok_session()
        manager = ResilienceManager()
        fallback_json = '{"status":"unavailable","source":"plane"}'
        manager.register(
            "plane",
            ResilienceConfig(
                circuit_failure_threshold=1,
                circuit_recovery_timeout=999.0,  # long — stays OPEN
                read_tool_patterns=("list_",),
                write_tool_patterns=(),
                fallback_message=fallback_json,
            ),
        )
        # Trip the circuit.
        cb = manager.get_circuit_breaker("plane")
        await cb.record_failure()
        assert cb.get_state() == "open"

        coro = self._build(session, manager=manager)
        result = await coro(project_id="p1")
        # Should return the fallback content, NOT call the session.
        assert session.call_tool.call_count == 0
        content, _ = result
        assert content[0]["text"] == fallback_json

    @pytest.mark.asyncio
    async def test_resilience_path_no_fallback_raises(self):
        """No fallback configured → ToolException on circuit OPEN."""
        from langchain_core.tools import ToolException
        session = self._make_ok_session()
        manager = ResilienceManager()
        manager.register(
            "plane",
            ResilienceConfig(
                circuit_failure_threshold=1,
                circuit_recovery_timeout=999.0,
                read_tool_patterns=("list_",),
                write_tool_patterns=(),
                fallback_message=None,  # no fallback
            ),
        )
        cb = manager.get_circuit_breaker("plane")
        await cb.record_failure()

        coro = self._build(session, manager=manager)
        with pytest.raises(ToolException) as exc:
            await coro(project_id="p1")
        assert "unavailable" in str(exc.value).lower()

    @pytest.mark.asyncio
    async def test_resilience_path_record_success_updates_manager(self):
        """A successful call updates ``_last_success`` via the manager."""
        session = self._make_ok_session()
        manager = ResilienceManager()
        manager.register(
            "plane",
            ResilienceConfig(
                read_tool_patterns=("list_",),
                write_tool_patterns=(),
            ),
        )
        coro = self._build(session, manager=manager)

        assert manager.is_stale("plane", threshold=10.0) is True
        await coro(project_id="p1")
        assert manager.is_stale("plane", threshold=10.0) is False

    @pytest.mark.asyncio
    async def test_runtime_kwarg_stripped_in_resilience_path(self):
        """LangGraph's runtime kwarg is stripped in BOTH legacy and resilience paths."""
        session = self._make_ok_session()
        manager = ResilienceManager()
        manager.register(
            "plane",
            ResilienceConfig(
                read_tool_patterns=("list_",),
                write_tool_patterns=(),
            ),
        )
        coro = self._build(session, manager=manager)

        await coro(project_id="p1", runtime="should-be-stripped")
        # Verify session.call_tool was called WITHOUT runtime.
        forwarded_kwargs = session.call_tool.call_args.args[1]
        assert "runtime" not in forwarded_kwargs
