"""Completion registry service for synchronous async wait on instance completions."""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

_STALE_THRESHOLD_SECONDS = 3600

logger = logging.getLogger(__name__)


@dataclass
class CompletionResult:
    """Result of an instance completion.

    Attributes:
        content: The result content from the completion.
        is_error: Whether this result represents an error condition.
    """

    content: Any
    is_error: bool = False

    @property
    def succeeded(self) -> bool:
        """Return True if the completion succeeded (not an error)."""
        return not self.is_error


class CompletionRegistry:
    """Thread-safe registry for managing instance completion events.

    Provides per-instance asyncio.Event for synchronous wait patterns,
    with support for cross-thread notification and stale entry cleanup.

    Thread Safety:
        All dict operations are protected by threading.Lock.
        Event setting uses call_soon_threadsafe for cross-thread safety.
    """

    def __init__(self) -> None:
        """Initialize the completion registry."""
        self._events: dict[str, asyncio.Event] = {}
        self._results: dict[str, CompletionResult] = {}
        self._buffered: dict[str, CompletionResult] = {}
        self._register_times: dict[str, float] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store event loop reference for cross-thread event notification.

        Args:
            loop: The asyncio event loop to use for thread-safe event setting.
        """
        self._loop = loop

    def register(self, instance_id: str) -> None:
        """Register a new instance for completion tracking.

        Creates an asyncio.Event for the instance. If a buffered completion
        exists, it is consumed immediately and the event is set so that
        wait_for() returns instantly.

        Args:
            instance_id: Unique identifier for the instance.
        """
        with self._lock:
            if instance_id in self._events:
                logger.warning("Instance %s already registered", instance_id[:8])
                return

            event = asyncio.Event()

            # Check for buffered completion - consume immediately
            if instance_id in self._buffered:
                self._results[instance_id] = self._buffered.pop(instance_id)
                event.set()
                logger.debug(
                    "Consumed buffered completion for %s, event set immediately",
                    instance_id[:8],
                )

            self._events[instance_id] = event
            self._register_times[instance_id] = time.monotonic()
            logger.debug("Registered instance %s", instance_id[:8])

    def complete(
        self, instance_id: str, result: Any = None, is_error: bool = False
    ) -> bool:
        """Signal that an instance has completed.

        Thread-safe completion signaling. If the event exists, stores the result
        and sets the event (outside lock). If no event exists yet, buffers the
        result for later consumption by register().

        Args:
            instance_id: Unique identifier for the completed instance.
            result: The completion result content.
            is_error: Whether this completion represents an error.

        Returns:
            True if event was set or result was buffered.
            False only for duplicate completion (event already set).
        """
        completion = CompletionResult(content=result, is_error=is_error)

        with self._lock:
            if instance_id in self._events:
                event = self._events[instance_id]

                # Check for duplicate completion
                if instance_id in self._results:
                    logger.warning(
                        "Duplicate completion for %s, ignoring", instance_id[:8]
                    )
                    return False

                # Store result and schedule event.set() outside lock
                self._results[instance_id] = completion
                loop = self._loop
                logger.debug(
                    "Completing instance %s (error=%s), scheduling event.set()",
                    instance_id[:8],
                    is_error,
                )

                # Set event outside lock - use threadsafe if loop running
                if loop is not None and loop.is_running():
                    loop.call_soon_threadsafe(event.set)
                else:
                    event.set()

                return True

            # No event yet - buffer the result
            self._buffered[instance_id] = completion
            logger.debug("Buffered completion for %s (no event yet)", instance_id[:8])
            return True

    async def wait_for(self, instance_id: str, timeout: float = 300.0) -> CompletionResult | None:
        """Wait for an instance to complete with a timeout.

        Args:
            instance_id: Unique identifier for the instance.
            timeout: Maximum seconds to wait (default 300).

        Returns:
            CompletionResult if completed within timeout.
            None if timeout occurred.

        Raises:
            ValueError: If instance is not registered.
        """
        with self._lock:
            if instance_id not in self._events:
                raise ValueError(f"Instance {instance_id[:8]} not registered")
            event = self._events[instance_id]
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for instance %s", instance_id[:8])
            return None
        with self._lock:
            result = self._results.get(instance_id)
            logger.debug(
                "Instance %s completed (error=%s)",
                instance_id[:8],
                result.is_error if result else "N/A",
            )
            return result

    def unregister(self, instance_id: str) -> None:
        """Remove all registry entries for an instance.

        Cleans up events, results, buffered completions, and register times.

        Args:
            instance_id: Unique identifier for the instance.
        """
        with self._lock:
            self._events.pop(instance_id, None)
            self._results.pop(instance_id, None)
            self._buffered.pop(instance_id, None)
            self._register_times.pop(instance_id, None)

        logger.debug("Unregistered instance %s", instance_id[:8])

    def cleanup_stale(self, max_age_seconds: int = _STALE_THRESHOLD_SECONDS) -> int:
        """Remove entries older than the specified age threshold.

        Safety valve: clears _buffered if it grows too large (>100 entries).

        Args:
            max_age_seconds: Maximum age in seconds before entry is considered stale.

        Returns:
            Number of entries cleaned up.
        """
        current_time = time.monotonic()
        cleaned = 0

        with self._lock:
            # Safety valve for buffered entries
            if len(self._buffered) > 100:
                logger.warning(
                    "Clearing %d buffered entries (exceeds threshold)",
                    len(self._buffered),
                )
                self._buffered.clear()
                cleaned += 1

            # Remove stale register times
            stale_ids = [
                instance_id
                for instance_id, register_time in self._register_times.items()
                if current_time - register_time > max_age_seconds
            ]

            for instance_id in stale_ids:
                self._events.pop(instance_id, None)
                self._results.pop(instance_id, None)
                self._buffered.pop(instance_id, None)
                self._register_times.pop(instance_id, None)
                cleaned += 1

        if cleaned > 0:
            logger.warning("Cleaned up %d stale entries", cleaned)

        return cleaned


# Module-level singleton
_completion_registry: CompletionRegistry | None = None


def get_completion_registry() -> CompletionRegistry:
    """Get the global CompletionRegistry singleton instance.

    Returns:
        The shared CompletionRegistry instance.
    """
    global _completion_registry
    if _completion_registry is None:
        _completion_registry = CompletionRegistry()
    return _completion_registry
