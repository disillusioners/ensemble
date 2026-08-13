"""Resilience primitives for the MCP tool layer.

This module implements the generic half of the **hybrid** resilience
architecture (Option C from the design doc):

- **Generic primitives** (this file): ``RetryPolicy``, ``ResultCache``,
  ``AuthFailureClassifier``, ``ResilienceConfig``, ``ResilienceManager``.
  Reusable across any MCP server that wants to opt in.
- **Per-server tuning** (e.g. ``PlaneServerDefinition.resilience_config``):
  the actual values for retries / cache TTL / circuit-breaker
  thresholds / fallback messages.

The wiring into the actual ``session.call_tool`` happens in
``daemon/mcp/tool_adapter._lazy_coroutine``, which is the single
funnel for every MCP tool call. Other servers (context7, webfetch)
are unaffected because their ``BuiltinServerDefinition.resilience_config``
returns ``None`` (base-class default) — ``_lazy_coroutine`` short-circuits
to the no-resilience path when no config is registered.

The on-demand probe lives in the ``CircuitBreaker.can_execute`` transition:
when the circuit is OPEN and ``recovery_timeout`` has elapsed, the next
``can_execute()`` call moves it to HALF_OPEN and returns ``True`` — the
following tool call IS the probe. There is intentionally no background
``health_monitor`` task.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from daemon.mcp.errors import (
    McpAuthError,
    McpError,
    McpToolError,
    McpTransientError,
)
from daemon.sources.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RetryPolicy — exponential backoff + jitter for transient failures
# ---------------------------------------------------------------------------


@dataclass
class RetryPolicy:
    """Exponential backoff retry policy for MCP tool calls.

    Only retries exceptions listed in ``retryable_exceptions`` — auth and
    tool errors propagate immediately (retrying them just wastes time
    and burns the circuit-breaker failure budget).

    Delays follow ``base_delay * 2^(attempt-1)`` (capped at
    ``max_delay``) plus a random jitter of 0–50% of the computed delay
    to avoid thundering herd when many agents retry the same flaky
    server simultaneously.

    Example:
        max_attempts=3, base_delay=1.0, max_delay=8.0, jitter=True
        → delays of ~1s, ~2s, ~4s (with 0–50% jitter on each).
    """

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 8.0
    jitter: bool = True
    retryable_exceptions: tuple = (McpTransientError, asyncio.TimeoutError)
    # CR-6: writes (create_*, update_*, delete_*, ...) carry
    # non-idempotent side effects — retrying on a transient error can
    # create duplicate issues, double-assign, etc. The default
    # ``False`` preserves the "fail loudly on write flakiness"
    # contract; callers that KNOW their writes are idempotent (e.g. a
    # ``set_status`` that uses server-side equality) can flip it to
    # ``True`` explicitly.
    retry_writes: bool = False

    async def execute(
        self,
        func,
        *args,
        is_write: bool = False,
        **kwargs,
    ):
        """Execute ``func`` with retry. Raises the last exception on failure.

        CR-6: when ``is_write=True`` and ``retry_writes=False`` (the
        default), the callable is executed exactly once with no retry
        on transient failure. The single-attempt path is essential for
        non-idempotent writes — a transient blip on a ``create_issue``
        retry would create a duplicate issue.

        Args:
            func: Async callable to execute.
            *args, **kwargs: Forwarded to ``func``.
            is_write: ``True`` when the wrapped call has write
                side-effects that should not be retried. When
                ``True`` and ``retry_writes`` is ``False``, executes
                ``func`` exactly once and re-raises any error.

        Returns:
            The result of the first successful ``func`` call.

        Raises:
            The last exception encountered — a ``McpTransientError`` /
            ``asyncio.TimeoutError`` after all attempts are exhausted,
            or an immediate ``McpAuthError`` / ``McpToolError`` /
            ``McpError`` subclass that isn't retryable.
        """
        # CR-6: short-circuit on writes. Execute once, raise on any
        # error — no retry, no jitter, no delay. The caller is
        # expected to surface the failure to the operator (or
        # degrade via the fallback in the outer handler).
        if is_write and not self.retry_writes:
            return await func(*args, **kwargs)

        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await func(*args, **kwargs)
            except self.retryable_exceptions as e:
                last_exc = e
                if attempt >= self.max_attempts:
                    # Out of attempts — surface the last transient error
                    # so the caller can record the circuit-breaker
                    # failure and (optionally) return a fallback.
                    raise
                delay = self._compute_delay(attempt)
                logger.debug(
                    f"RetryPolicy: attempt {attempt}/{self.max_attempts} "
                    f"failed with {type(e).__name__}, "
                    f"retrying in {delay:.2f}s"
                )
                await asyncio.sleep(delay)
            except McpError:
                # Non-retryable McpError subclass (auth, tool error, etc.)
                # — propagate immediately, do NOT consume a retry slot.
                raise
            # Anything outside the McpError hierarchy is treated as
            # transient by the callers that wrap raw exceptions via
        # ``AuthFailureClassifier``; this method only deals with
        # already-classified errors, so it doesn't try to classify
        # arbitrary exceptions here.
        # If the loop exits without returning or raising (shouldn't
        # happen — ``max_attempts >= 1`` always runs at least one
        # attempt and either returns or raises), re-raise the last
        # exception as a defensive fallback.
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("RetryPolicy.execute exited without result")

    def _compute_delay(self, attempt: int) -> float:
        """Compute the exponential-backoff delay for ``attempt``.

        Tidier-1: extracted from ``execute`` so the per-attempt delay
        math lives in exactly one place. Same formula as before —
        ``min(base_delay * 2^(attempt-1), max_delay)`` plus an
        optional 0..50% random jitter. Used by callers that need to
        align their own retry timing (e.g.
        ``_do_call_with_retry``) so the two paths can't drift on
        delay math.
        """
        delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        if self.jitter:
            delay += random.uniform(0, delay * 0.5)
        return delay


# ---------------------------------------------------------------------------
# ResultCache — TTL-based async-safe cache for read tools
# ---------------------------------------------------------------------------


class ResultCache:
    """TTL-based async-safe result cache for MCP tool results.

    Keyed by ``(server_name, tool_name, canonical_kwargs_hash)``. The
    kwargs hash is computed from a canonical JSON dump (sorted keys) so
    ``{"a": 1, "b": 2}`` and ``{"b": 2, "a": 1}`` produce the same key.

    LRU eviction when ``max_entries`` is exceeded: the oldest-inserted
    entry is dropped first. Eviction runs in O(1) via ``OrderedDict``.

    All public methods are coroutine-safe (``asyncio.Lock`` guards the
    dict mutation surface). The cache is in-process only — restarting
    the daemon starts cold, which is acceptable for read tools whose
    results re-populate on first call.
    """

    def __init__(self, default_ttl: float = 60.0, max_entries: int = 1000):
        """Initialize the cache.

        Args:
            default_ttl: Default TTL in seconds for entries that don't
                pass an explicit ``ttl`` to ``set()``.
            max_entries: Maximum number of entries before LRU eviction
                kicks in. ``0`` disables eviction (unbounded).
        """
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        # OrderedDict for O(1) LRU. Key: cache key string;
        # value: (value, expiry, server_generation_at_set_time).
        self._entries: OrderedDict[str, tuple[Any, float, int]] = OrderedDict()
        # CR-5: per-server generation counter. ``invalidate_server``
        # bumps the generation for ``server_name``; entries stamped
        # with an older generation are treated as stale on read —
        # closes the read-during-invalidation TOCTOU race where a
        # reader whose ``set`` runs after the invalidation would
        # otherwise leave stale data in the cache.
        self._generations: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def get(
        self, server_name: str, tool_name: str, kwargs: dict
    ) -> tuple[Any, bool]:
        """Look up a cached result.

        Returns ``(cached_value, True)`` on hit, ``(None, False)`` on miss
        or expiry. Expired entries are removed lazily on read.

        CR-5: also returns a miss when the entry's stamped generation
        does not match the current generation for ``server_name`` —
        a reader that started before ``invalidate_server`` finished
        and wrote its value after the invalidation is treated as stale
        and dropped on the next read.

        Args:
            server_name: The MCP server name (e.g. ``"plane"``).
            tool_name: The adapted tool name (e.g.
                ``"plane_list_issues"``).
            kwargs: Tool call kwargs to include in the cache key.

        Returns:
            ``(value, True)`` on hit, ``(None, False)`` on miss.
        """
        key = self._make_key(server_name, tool_name, kwargs)
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None, False
            value, expiry, gen = entry
            current_gen = self._generations.get(server_name, 0)
            if gen != current_gen:
                # Stale entry — a write invalidation happened after
                # this entry was set. Drop it so the next read
                # re-fetches from the server.
                self._entries.pop(key, None)
                return None, False
            if time.monotonic() >= expiry:
                # Lazy expiry — drop the entry on read so we don't keep
                # dead weight in the dict.
                self._entries.pop(key, None)
                return None, False
            # LRU touch: move-to-end on read so the eviction policy
            # reflects actual usage, not just insertion order.
            self._entries.move_to_end(key)
            return value, True

    async def set(
        self,
        server_name: str,
        tool_name: str,
        kwargs: dict,
        value: Any,
        ttl: float | None = None,
    ) -> None:
        """Store a result.

        Args:
            server_name: The MCP server name.
            tool_name: The adapted tool name.
            kwargs: Tool call kwargs (used in cache key).
            value: The value to cache.
            ttl: TTL override in seconds. ``None`` uses
                ``default_ttl``.
        """
        key = self._make_key(server_name, tool_name, kwargs)
        effective_ttl = ttl if ttl is not None else self._default_ttl
        expiry = time.monotonic() + effective_ttl
        async with self._lock:
            # CR-5: stamp the entry with the current generation for
            # this server. ``set`` is always called outside an
            # ``invalidate_server`` call, so the stamp is consistent.
            # The only way the stored generation can be stale is if a
            # concurrent reader's ``set`` completes after our
            # ``invalidate_server`` — and that's exactly what the
            # generation check in ``get`` is designed to catch.
            current_gen = self._generations.get(server_name, 0)
            self._entries[key] = (value, expiry, current_gen)
            self._entries.move_to_end(key)
            # Evict oldest entries until under cap. We only evict one
            # per ``set``; the common case is that callers don't push
            # 1000+ distinct keys per second, so amortized cost is fine.
            while self._max_entries and len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    async def invalidate_server(self, server_name: str) -> None:
        """Invalidate ALL cache entries for ``server_name``.

        Used after a write tool succeeds — the server's data shape may
        have changed, so the entire per-server cache must go. Simple
        and safe; we don't try to be clever about which specific
        resources are affected.

        CR-5: instead of (or in addition to) dropping entries, bump
        the generation counter for ``server_name``. ``get`` rejects
        entries whose stamped generation no longer matches the
        server's current generation. This closes the TOCTOU race
        where a reader's ``set`` (started before the invalidation)
        could otherwise land in the cache after the invalidation
        completed, leaving stale data behind.

        Ordering note: ``_increments`` is performed BEFORE the entry
        purge so that any reader holding the lock during the bump
        (and writing its value next) sees the new generation on its
        stamp and is rejected by the next ``get``.
        """
        prefix = f"{server_name}:"
        async with self._lock:
            # Bump generation first — any concurrent ``set`` that
            # grabs the lock after this will stamp the new generation,
            # but the next ``get`` will see the bump and reject
            # either way (because the entry's prior stamp is now
            # stale).
            self._generations[server_name] = (
                self._generations.get(server_name, 0) + 1
            )
            # Then drop the existing entries. ``get`` double-checks
            # the generation but pre-clearing keeps the dict tidy and
            # avoids the lazy-expiry path on every read.
            keys_to_drop = [k for k in self._entries if k.startswith(prefix)]
            for k in keys_to_drop:
                self._entries.pop(k, None)

    @staticmethod
    def _make_key(server_name: str, tool_name: str, kwargs: dict) -> str:
        """Build a canonical cache key.

        Uses ``json.dumps(..., sort_keys=True)`` for a deterministic
        kwargs representation, then SHA-256 hashes it (hex digest) so
        the key is bounded length regardless of input size.

        Args:
            server_name: The MCP server name.
            tool_name: The adapted tool name.
            kwargs: Tool call kwargs.

        Returns:
            ``"{server_name}:{tool_name}:{hash}"``
        """
        canonical = json.dumps(kwargs, sort_keys=True, default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"{server_name}:{tool_name}:{digest}"


# ---------------------------------------------------------------------------
# AuthFailureClassifier — classify raw exceptions into McpError hierarchy
# ---------------------------------------------------------------------------


# Substrings (lowercase) that map to McpAuthError. Order matters only
# for tests / readability — these are OR'd, not priority-ordered.
_AUTH_PATTERNS: tuple[str, ...] = ("401", "403", "unauthorized", "forbidden")

# Substrings (lowercase) that map to McpTransientError. Both HTTP 5xx
# and connection-level errors.
_TRANSIENT_PATTERNS: tuple[str, ...] = (
    "503",
    "502",
    "500",
    "504",
    "connection reset",
    "connection refused",
    "timeout",
    "timed out",
)


def classify_exception(exc: Exception) -> McpError:
    """Classify a raw exception into the ``McpError`` hierarchy.

    String-matches the exception message (case-insensitive) for known
    HTTP status codes and connection-level error patterns. Used by
    ``RetryPolicy.execute`` and the inline call in ``_lazy_coroutine``
    so non-MCPError exceptions from the underlying transport get a
    retryability verdict.

    The classification is conservative — anything we don't recognize
    becomes ``McpToolError`` (non-retryable, surfaced to the agent).
    Better to surface a novel failure than to retry-spam an unknown
    error class.

    Args:
        exc: The raw exception raised by ``session.call_tool`` or the
            underlying transport.

    Returns:
        An ``McpError`` subclass (``McpAuthError`` /
        ``McpTransientError`` / ``McpToolError``).
    """
    # If it's already in our hierarchy, pass it through unchanged so
    # callers that raise McpAuthError directly get the right type.
    if isinstance(exc, McpError):
        return exc

    message = str(exc).lower()
    for pattern in _AUTH_PATTERNS:
        if pattern in message:
            return McpAuthError(str(exc))
    for pattern in _TRANSIENT_PATTERNS:
        if pattern in message:
            return McpTransientError(str(exc))
    return McpToolError(str(exc))


# ---------------------------------------------------------------------------
# ResilienceConfig — per-server tuning
# ---------------------------------------------------------------------------


@dataclass
class ResilienceConfig:
    """Per-server resilience configuration. None fields = feature disabled.

    All fields are optional so a definition can opt into one piece
    (e.g. just circuit breaking) without enabling everything. The
    ``None``-as-disabled convention keeps ``_lazy_coroutine`` simple:
    a None field means "skip this step entirely".

    Example (Plane):
        ResilienceConfig(
            retry_policy=RetryPolicy(max_attempts=3),
            cache_ttl=300.0,
            circuit_failure_threshold=5,
            fallback_message='{"status":"unavailable"}',
            read_tool_patterns=("list_", "get_", "search_"),
        )
    """

    retry_policy: RetryPolicy | None = None
    cache_ttl: float | None = None  # None = no caching
    cache_max_entries: int = 1000
    circuit_failure_threshold: int = 5
    circuit_recovery_timeout: float = 60.0
    probe_timeout: float = 5.0  # HALF_OPEN probe — synchronous inline
    fallback_message: str | None = None
    # 5-min staleness threshold — avoids hitting external API on every
    # tool call. Used by ``ResilienceManager.is_stale`` for callers
    # that want to bypass the resilience path when fresh data isn't
    # required.
    stale_threshold: float = 300.0
    read_tool_patterns: tuple[str, ...] = ()
    write_tool_patterns: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# ResilienceManager — per-server state container
# ---------------------------------------------------------------------------


class ResilienceManager:
    """Owns per-server resilience state.

    Created once (typically on ``McpService.__init__``) and shared
    across all instances + tools. Provides the lookups
    ``_lazy_coroutine`` makes on every tool call:

        config = manager.get_config(server_name)
        cb = manager.get_circuit_breaker(server_name)
        cache = manager.get_cache(server_name)

    A server only appears in the internal dicts after ``register()``
    is called (with its ``ResilienceConfig``). Servers that never opt
    in remain absent — ``get_config`` / ``get_circuit_breaker`` /
    ``get_cache`` all return ``None`` for absent servers and
    ``_lazy_coroutine`` falls back to the no-resilience path.

    ``CircuitBreaker`` is reused from ``daemon.sources.circuit_breaker``
    (NOT reimplemented here) so the rest of the daemon benefits from
    the same well-tested primitive.
    """

    def __init__(self) -> None:
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._caches: dict[str, ResultCache] = {}
        # server_name → monotonic timestamp of the last successful call.
        # Used by ``is_stale`` for callers that want to bypass the
        # cache when last-known-good data is too old.
        self._last_success: dict[str, float] = {}
        self._configs: dict[str, ResilienceConfig] = {}

    def register(self, server_name: str, config: ResilienceConfig) -> None:
        """Register ``server_name`` for resilience.

        Creates a circuit breaker (with the config's failure threshold
        + recovery timeout) and a result cache (if ``cache_ttl`` is
        set).

        T-6: state preservation — re-registering with the same config
        (``same threshold + recovery_timeout + cache_ttl + cache_max_entries``)
        preserves the existing ``CircuitBreaker`` (with its
        failure_count + state) and ``ResultCache`` (with its
        populated entries). The previous implementation always
        re-instantiated the CB, resetting failure_count to 0 — a
        regression for any caller that re-registered after a brief
        in-memory state change (e.g. env-var reload tests).

        When the relevant config fields change, a fresh CB / cache
        IS allocated. This matches the operator intent: a new
        threshold means a new policy, so the old state is dropped.
        The cache replacement keeps the same behavior as before
        (cache is rebuilt on TTL change).

        Args:
            server_name: The MCP server name (matches
                ``BuiltinServerDefinition.name``).
            config: The resilience tuning for this server.
        """
        # Detect "same config" → preserve state. The threshold +
        # recovery_timeout are the only fields that affect the CB;
        # cache_ttl + cache_max_entries are the only fields that
        # affect the cache.
        prev_cb = self._circuit_breakers.get(server_name)
        prev_cache = self._caches.get(server_name)
        same_cb = (
            prev_cb is not None
            and prev_cb.failure_threshold == config.circuit_failure_threshold
            and prev_cb.recovery_timeout == config.circuit_recovery_timeout
        )
        same_cache_ttl = (
            (prev_cache is not None and config.cache_ttl is not None)
            and prev_cache._default_ttl == config.cache_ttl
            and prev_cache._max_entries == config.cache_max_entries
        )

        self._configs[server_name] = config

        if same_cb:
            # Keep the existing CB — failure_count, state, and
            # probe-in-flight flag are all preserved.
            pass
        else:
            self._circuit_breakers[server_name] = CircuitBreaker(
                failure_threshold=config.circuit_failure_threshold,
                recovery_timeout=config.circuit_recovery_timeout,
            )

        # Only allocate a cache when caching is actually enabled. Saves
        # memory for servers that opt into retry + circuit breaking but
        # not caching.
        if config.cache_ttl is not None:
            if same_cache_ttl:
                # Keep the existing cache — entries and generation
                # counter are preserved.
                pass
            else:
                self._caches[server_name] = ResultCache(
                    default_ttl=config.cache_ttl,
                    max_entries=config.cache_max_entries,
                )
        else:
            # Make sure no stale cache from a prior registration lingers.
            self._caches.pop(server_name, None)

    def get_circuit_breaker(self, server_name: str) -> CircuitBreaker | None:
        """Return the circuit breaker for ``server_name``, or ``None``."""
        return self._circuit_breakers.get(server_name)

    def get_cache(self, server_name: str) -> ResultCache | None:
        """Return the result cache for ``server_name``, or ``None``."""
        return self._caches.get(server_name)

    def get_config(self, server_name: str) -> ResilienceConfig | None:
        """Return the resilience config for ``server_name``, or ``None``."""
        return self._configs.get(server_name)

    def record_success(self, server_name: str) -> None:
        """Stamp ``last_success`` for ``server_name`` with monotonic now.

        Called by ``_lazy_coroutine`` after a successful tool call so
        downstream callers can decide whether to bypass the cache based
        on the staleness threshold.
        """
        self._last_success[server_name] = time.monotonic()

    def is_stale(self, server_name: str, threshold: float) -> bool:
        """Return ``True`` if no successful call in the last ``threshold`` seconds.

        A server is "fresh" after the first successful call within
        ``threshold`` seconds, and "stale" otherwise (including
        before any success has been recorded). Callers can use this to
        decide between cached-fallback and direct-call paths.

        Args:
            server_name: The MCP server name.
            threshold: Seconds. ``last_success`` older than this →
                stale.

        Returns:
            ``True`` if stale (or never recorded), ``False`` if fresh.
        """
        last = self._last_success.get(server_name)
        if last is None:
            return True
        return (time.monotonic() - last) >= threshold

    def reset_circuit(self, server_name: str) -> None:
        """Force-reset the circuit breaker (emergency recovery hook).

        Bypasses the normal state machine — use when an operator has
        manually verified the upstream service is back and wants to
        resume traffic immediately rather than waiting for the next
        HALF_OPEN probe.
        """
        cb = self._circuit_breakers.get(server_name)
        if cb is not None:
            cb.reset()

    def has_config(self, server_name: str) -> bool:
        """Return ``True`` iff a ``ResilienceConfig`` is registered."""
        return server_name in self._configs


# ---------------------------------------------------------------------------
# Helper for _lazy_coroutine — read/write classification
# ---------------------------------------------------------------------------


def is_read_tool(tool_name: str, config: ResilienceConfig) -> bool:
    """Classify a tool as read or write based on configured prefixes.

    The check strips the configured prefix from ``tool_name`` before
    pattern-matching so both ``plane_list_issues`` (with prefix
    ``"plane"``) and ``list_issues`` (without) classify correctly.
    The strip is naive — ``tool_name.split("_", 1)`` only strips the
    FIRST ``_``-prefixed segment, which matches the tool name
    convention used by ``create_lazy_mcp_tools`` (``{prefix}_{tool}``
    or ``mcp_{server}_{tool}``).

    Falls back to ``tool_name`` unchanged when no prefix can be
    stripped (e.g. ``list_issues`` already starts with the read
    pattern itself). For Plane's read pattern ``("list_", "get_",
    "search_")`` this returns ``True`` for both
    ``plane_list_issues`` and ``list_issues`` because the strip is a
    no-op for the latter and the prefix-match succeeds for the former.

    Args:
        tool_name: The ADAPTED tool name (e.g. ``"plane_list_issues"``).
        config: The server's resilience config with
            ``read_tool_patterns`` set.

    Returns:
        ``True`` if the tool matches any read pattern (or no write
        pattern is configured), ``False`` if it matches a write pattern.
    """
    if not config.read_tool_patterns and not config.write_tool_patterns:
        # No patterns configured — treat everything as non-cacheable.
        # Caller can decide what to do with the result; this is the
        # safe default.
        return False

    # Strip the leading "prefix_" if present. ``split("_", 1)`` yields
    # at most 2 parts; we take the second when there's an underscore,
    # otherwise the whole name.
    stripped = tool_name.split("_", 1)[1] if "_" in tool_name else tool_name

    # Write wins over read when both patterns match — a tool named
    # ``get_create_data`` shouldn't be cached just because it starts
    # with ``get_``.
    if config.write_tool_patterns:
        if any(stripped.startswith(p) for p in config.write_tool_patterns):
            return False
    if config.read_tool_patterns:
        return any(stripped.startswith(p) for p in config.read_tool_patterns)
    # Only write patterns configured and the tool didn't match any →
    # conservative default: treat as read (cacheable). In practice
    # every server that uses caching configures both.
    return True


__all__ = [
    "RetryPolicy",
    "ResultCache",
    "AuthFailureClassifier",  # name kept for back-compat (it's a function)
    "classify_exception",
    "ResilienceConfig",
    "ResilienceManager",
    "is_read_tool",
]


# Back-compat alias — the spec names the function
# ``AuthFailureClassifier`` even though it's a free function, not a
# class. Exporting under both names keeps callers happy without
# renaming.
AuthFailureClassifier = classify_exception
