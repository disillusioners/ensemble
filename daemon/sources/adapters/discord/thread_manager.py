"""Thread lifecycle manager for Discord source adapter.

Tracks Discord thread instances per guild with TTL-based expiry, LRU
eviction at capacity, and archive-state tracking. Mirrors
``daemon/sources/adapters/slack/thread_manager.py`` but adapted to
Discord's data model (per-guild OrderedDict keyed on thread snowflake).

Key differences from the Slack ``ThreadManager``:

* Slack keys on ``workspace_id``; Discord keys on ``guild_id`` (the
  Discord equivalent of a Slack workspace).
* Discord threads auto-archive after a server-configured idle window.
  We surface ``is_archived`` and ``archive_timestamp`` so the adapter
  can route outbound sends to the parent channel when the thread is
  archived.
* Guild dict access is lock-protected per guild (not globally) so
  concurrent activity across guilds does not serialize.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from daemon.manager import InstanceManager


@dataclass
class ThreadInstance:
    """Represents a Discord thread instance mapping.

    Attributes:
        thread_id: Discord snowflake ID of the thread.
        channel_id: Discord snowflake ID of the parent channel.
        guild_id: Discord snowflake ID of the guild (server).
        instance_id: Optional ensemble instance ID handling this thread.
        created_at: ``time.monotonic()`` timestamp of first registration.
        last_accessed: ``time.monotonic()`` timestamp of most recent
            touch — used to compute TTL expiry.
        is_archived: Whether Discord marked the thread as archived.
            Archived threads should route outbound sends to the parent
            channel rather than the thread itself.
        archive_timestamp: ``time.monotonic()`` of when ``is_archived``
            was set, or None if never archived.
    """

    thread_id: str
    channel_id: str
    guild_id: str
    instance_id: str | None = None
    created_at: float = field(default_factory=time.monotonic)
    last_accessed: float = field(default_factory=time.monotonic)
    is_archived: bool = False
    archive_timestamp: float | None = None


class DiscordThreadManager:
    """Track Discord thread instances per guild.

    Storage layout::

        guild_id -> OrderedDict[thread_id, ThreadInstance]

    * TTL expiry: ``last_accessed`` older than ``ttl_seconds`` evicts the
      thread and terminates its ensemble instance.
    * LRU eviction: at capacity, the least-recently-used thread per
      guild is evicted.
    * Archive handling: ``mark_archived()`` flips the flag and stamps
      ``archive_timestamp``. The adapter checks this flag during outbound
      send to decide whether to route to the thread or its parent
      channel.
    * Concurrency: each guild has its own ``asyncio.Lock`` so reads and
      writes on one guild never serialize reads and writes on another.
      A top-level ``_guilds_guard`` protects the dict that maps
      ``guild_id -> asyncio.Lock`` so it can be safely extended.

    Attributes:
        DEFAULT_TTL_SECONDS: 24 hours, matching Slack's default.
        DEFAULT_MAX_THREADS_PER_GUILD: 50 threads per guild.
    """

    DEFAULT_TTL_SECONDS: float = 24 * 60 * 60
    DEFAULT_MAX_THREADS_PER_GUILD: int = 50

    def __init__(
        self,
        manager: "InstanceManager",
        ttl_seconds: float | None = None,
        max_threads_per_guild: int | None = None,
    ) -> None:
        self._manager = manager
        self._ttl_seconds = ttl_seconds or self.DEFAULT_TTL_SECONDS
        self._max_threads = (
            max_threads_per_guild or self.DEFAULT_MAX_THREADS_PER_GUILD
        )

        # Per-guild thread storage. Outer dict is guarded by
        # ``_guilds_guard``; each per-guild OrderedDict is guarded by
        # the lock in ``_guild_locks``.
        self._threads: dict[str, OrderedDict[str, ThreadInstance]] = {}
        self._guild_locks: dict[str, asyncio.Lock] = {}
        self._guilds_guard = asyncio.Lock()

        # Reverse map: instance_id -> (guild_id, thread_id) for cleanup.
        self._instance_to_thread: dict[str, tuple[str, str]] = {}

        # Shutdown idempotency.
        self._shutdown_called = False

    # ---------- internal helpers ----------

    async def _get_guild_lock(self, guild_id: str) -> asyncio.Lock:
        """Return (and lazily create) the per-guild asyncio.Lock."""
        async with self._guilds_guard:
            lock = self._guild_locks.get(guild_id)
            if lock is None:
                lock = asyncio.Lock()
                self._guild_locks[guild_id] = lock
            return lock

    async def _get_guild_threads(
        self, guild_id: str
    ) -> OrderedDict[str, ThreadInstance]:
        """Return (and lazily create) the per-guild thread OrderedDict.

        FIX 8: Wrap the lazy creation in ``_guilds_guard`` so two coroutines
        that race on the same new ``guild_id`` do not clobber each other's
        ``OrderedDict`` assignment.
        """
        async with self._guilds_guard:
            if guild_id not in self._threads:
                self._threads[guild_id] = OrderedDict()
            return self._threads[guild_id]

    # ---------- public API ----------

    async def register_thread(
        self,
        guild_id: str,
        channel_id: str,
        thread_id: str,
        instance_id: str | None = None,
    ) -> ThreadInstance:
        """Register or update a thread.

        If the thread already exists, updates ``last_accessed``,
        ``instance_id``, and ``channel_id`` (the latter in case the bot
        observed the thread from a different channel reference).
        Returns the (possibly new) ``ThreadInstance``.

        Performs TTL and LRU eviction at capacity within the guild.

        FIX 7: ``register_thread`` is the first signal of a new lifecycle
        after a ``shutdown()`` — reset the shutdown latch so we don't
        silently no-op on a stop→start cycle.
        """
        # FIX 7: re-arm the shutdown latch so this manager can be reused
        # after ``shutdown()`` has been called. Registering a thread is
        # the unambiguous "we're live again" signal.
        self._shutdown_called = False

        lock = await self._get_guild_lock(guild_id)
        async with lock:
            guild_threads = await self._get_guild_threads(guild_id)
            now = time.monotonic()

            existing = guild_threads.get(thread_id)
            if existing is not None:
                if existing.instance_id and existing.instance_id != instance_id:
                    self._instance_to_thread.pop(existing.instance_id, None)
                existing.last_accessed = now
                if instance_id is not None:
                    existing.instance_id = instance_id
                existing.channel_id = channel_id
                if instance_id is not None:
                    self._instance_to_thread[instance_id] = (guild_id, thread_id)
                guild_threads.move_to_end(thread_id)
                return existing

            # New thread — evict expired then LRU first.
            await self._evict_expired_unlocked(guild_id, guild_threads)

            while len(guild_threads) >= self._max_threads:
                await self._evict_oldest_unlocked(guild_id, guild_threads)

            thread = ThreadInstance(
                thread_id=thread_id,
                channel_id=channel_id,
                guild_id=guild_id,
                instance_id=instance_id,
                created_at=now,
                last_accessed=now,
            )
            guild_threads[thread_id] = thread
            if instance_id is not None:
                self._instance_to_thread[instance_id] = (guild_id, thread_id)

            logger.info(
                f"Registered Discord thread: guild={guild_id}, "
                f"channel={channel_id}, thread={thread_id}, "
                f"instance={instance_id}"
            )
            return thread

    async def get_thread(
        self, guild_id: str, thread_id: str
    ) -> ThreadInstance | None:
        """Return a thread instance or None if missing/expired.

        Touches ``last_accessed`` (LRU-friendly) on hit. Does NOT raise
        if the thread is archived — callers can inspect ``is_archived``
        on the returned object.
        """
        lock = await self._get_guild_lock(guild_id)
        async with lock:
            guild_threads = self._threads.get(guild_id)
            if not guild_threads:
                return None
            thread = guild_threads.get(thread_id)
            if thread is None:
                return None
            now = time.monotonic()
            if now - thread.last_accessed > self._ttl_seconds:
                logger.debug(
                    f"Discord thread expired on lookup: "
                    f"guild={guild_id}, thread={thread_id}"
                )
                guild_threads.pop(thread_id, None)
                if thread.instance_id:
                    self._instance_to_thread.pop(thread.instance_id, None)
                return None
            thread.last_accessed = now
            guild_threads.move_to_end(thread_id)
            return thread

    async def mark_archived(
        self, guild_id: str, thread_id: str, archived: bool = True
    ) -> None:
        """Set the archive flag on a tracked thread.

        Idempotent: marking an already-archived thread again is a no-op
        (preserves the original ``archive_timestamp``).
        """
        lock = await self._get_guild_lock(guild_id)
        async with lock:
            guild_threads = self._threads.get(guild_id)
            if not guild_threads:
                return
            thread = guild_threads.get(thread_id)
            if thread is None:
                return
            if thread.is_archived == archived:
                return
            thread.is_archived = archived
            thread.archive_timestamp = time.monotonic() if archived else None
            logger.debug(
                f"Discord thread archive state changed: "
                f"guild={guild_id}, thread={thread_id}, archived={archived}"
            )

    async def evict_expired(self) -> list[ThreadInstance]:
        """Run a TTL eviction pass across all guilds."""
        all_evicted: list[ThreadInstance] = []
        # Snapshot guild ids under the guilds guard so we don't race
        # with the lazy creation of new per-guild dicts.
        async with self._guilds_guard:
            guild_ids = list(self._threads.keys())
        for guild_id in guild_ids:
            lock = await self._get_guild_lock(guild_id)
            async with lock:
                guild_threads = self._threads.get(guild_id)
                if guild_threads is None:
                    continue
                evicted = await self._evict_expired_unlocked(guild_id, guild_threads)
                all_evicted.extend(evicted)
        return all_evicted

    async def get_stats(self) -> dict[str, Any]:
        """Return manager stats for observability/tests."""
        async with self._guilds_guard:
            guild_ids = list(self._threads.keys())
        per_guild: dict[str, int] = {}
        archived = 0
        for guild_id in guild_ids:
            lock = await self._get_guild_lock(guild_id)
            async with lock:
                guild_threads = self._threads.get(guild_id, {})
                per_guild[guild_id] = len(guild_threads)
                archived += sum(
                    1 for t in guild_threads.values() if t.is_archived
                )
        return {
            "total_threads": sum(per_guild.values()),
            "guilds": len(per_guild),
            "threads_per_guild": per_guild,
            "archived_threads": archived,
            "ttl_seconds": self._ttl_seconds,
            "max_threads_per_guild": self._max_threads,
        }

    async def shutdown(self) -> None:
        """Terminate every tracked instance and clear state.

        Idempotent — subsequent calls are no-ops. Per-instance
        termination failures are logged and skipped so a single bad
        instance does not abort the shutdown sweep.
        """
        if self._shutdown_called:
            return
        self._shutdown_called = True

        async with self._guilds_guard:
            guild_ids = list(self._threads.keys())
        for guild_id in guild_ids:
            lock = await self._get_guild_lock(guild_id)
            async with lock:
                guild_threads = self._threads.get(guild_id)
                if not guild_threads:
                    continue
                # Iterate a copy because _terminate_thread_unlocked mutates
                # the dict.
                for thread_id in list(guild_threads.keys()):
                    thread = guild_threads.get(thread_id)
                    if thread is None:
                        continue
                    await self._terminate_thread_unlocked(
                        guild_id, thread_id, thread, guild_threads
                    )

        # Clear remaining maps so a fresh start begins from empty state.
        async with self._guilds_guard:
            self._threads.clear()
            self._guild_locks.clear()
            self._instance_to_thread.clear()
        logger.info("DiscordThreadManager shutdown complete")

    # ---------- internal eviction primitives (caller holds lock) ----------

    async def _evict_expired_unlocked(
        self,
        guild_id: str,
        guild_threads: OrderedDict[str, ThreadInstance],
    ) -> list[ThreadInstance]:
        """Evict TTL-expired threads; caller holds the per-guild lock."""
        now = time.monotonic()
        evicted: list[ThreadInstance] = []
        expired_ids = [
            tid
            for tid, thread in guild_threads.items()
            if now - thread.last_accessed > self._ttl_seconds
        ]
        for tid in expired_ids:
            thread = guild_threads.pop(tid, None)
            if thread is None:
                continue
            if thread.instance_id:
                self._instance_to_thread.pop(thread.instance_id, None)
                await self._safe_terminate(thread.instance_id, reason="ttl_expiry")
            evicted.append(thread)
            logger.info(
                f"Evicted expired Discord thread: guild={guild_id}, thread={tid}"
            )
        return evicted

    async def _evict_oldest_unlocked(
        self,
        guild_id: str,
        guild_threads: OrderedDict[str, ThreadInstance],
    ) -> ThreadInstance | None:
        """Evict the least-recently-used thread; caller holds lock."""
        if not guild_threads:
            return None
        tid, thread = guild_threads.popitem(last=False)
        if thread.instance_id:
            self._instance_to_thread.pop(thread.instance_id, None)
            await self._safe_terminate(thread.instance_id, reason="lru_eviction")
        logger.info(
            f"Evicted oldest Discord thread (LRU): guild={guild_id}, thread={tid}"
        )
        return thread

    async def _terminate_thread_unlocked(
        self,
        guild_id: str,
        thread_id: str,
        thread: ThreadInstance,
        guild_threads: OrderedDict[str, ThreadInstance],
    ) -> None:
        """Terminate the instance backing a thread; caller holds lock."""
        guild_threads.pop(thread_id, None)
        if thread.instance_id:
            self._instance_to_thread.pop(thread.instance_id, None)
            await self._safe_terminate(thread.instance_id, reason="shutdown")

    async def _safe_terminate(self, instance_id: str, *, reason: str) -> None:
        """Terminate an instance, swallowing and logging errors."""
        try:
            await self._manager.terminate_instance(instance_id)
            logger.info(
                f"Terminated Discord thread instance: "
                f"instance={instance_id}, reason={reason}"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"Failed to terminate Discord thread instance "
                f"{instance_id} ({reason}): {e}"
            )
