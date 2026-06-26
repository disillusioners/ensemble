"""Thread manager for Slack channel threads with TTL and LRU eviction."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from daemon.manager import InstanceManager


@dataclass
class ThreadInstance:
    """Represents a thread instance mapping."""
    thread_ts: str
    channel_id: str
    workspace_id: str
    instance_id: str | None
    created_at: float
    last_accessed: float


class ThreadManager:
    """Manages Slack thread instances with TTL and LRU eviction.

    Slack threads can exist in channels. This manager:
    - Maps thread_ts to ThreadInstance
    - Enforces TTL (24 hours by default)
    - Limits threads per workspace (50 by default)
    - Uses LRU eviction when at capacity
    - Terminates instances on eviction
    """

    DEFAULT_TTL_SECONDS: float = 24 * 60 * 60  # 24 hours
    DEFAULT_MAX_THREADS_PER_WORKSPACE: int = 50

    def __init__(
        self,
        manager: "InstanceManager",
        ttl_seconds: float | None = None,
        max_threads_per_workspace: int | None = None,
    ):
        """Initialize thread manager.

        Args:
            manager: InstanceManager for terminating instances.
            ttl_seconds: Thread TTL in seconds (default: 24 hours).
            max_threads_per_workspace: Max threads per workspace (default: 50).
        """
        self._manager = manager

        # Configuration
        self._ttl_seconds = ttl_seconds or self.DEFAULT_TTL_SECONDS
        self._max_threads = max_threads_per_workspace or self.DEFAULT_MAX_THREADS_PER_WORKSPACE

        # Thread storage: workspace_id -> OrderedDict[thread_ts, ThreadInstance]
        self._threads: dict[str, OrderedDict[str, ThreadInstance]] = {}
        self._threads_guard = asyncio.Lock()

        # Track instance to thread mapping for cleanup
        self._instance_to_thread: dict[str, tuple[str, str]] = {}  # instance_id -> (workspace_id, thread_ts)

    def _get_workspace_threads(self, workspace_id: str) -> OrderedDict[str, ThreadInstance]:
        """Get or create thread storage for a workspace.

        Args:
            workspace_id: The workspace ID.

        Returns:
            OrderedDict of threads for this workspace.
        """
        if workspace_id not in self._threads:
            self._threads[workspace_id] = OrderedDict()
        return self._threads[workspace_id]

    async def _register_thread_unlocked(
        self,
        workspace_id: str,
        channel_id: str,
        thread_ts: str,
        instance_id: str,
    ) -> ThreadInstance:
        """Register a thread instance (must hold lock).

        Args:
            workspace_id: The Slack workspace ID.
            channel_id: The channel ID.
            thread_ts: The thread timestamp.
            instance_id: The agent instance ID.

        Returns:
            The created ThreadInstance.
        """
        workspace_threads = self._get_workspace_threads(workspace_id)
        now = time.monotonic()

        # Check if thread already exists
        if thread_ts in workspace_threads:
            thread = workspace_threads[thread_ts]
            
            # Clean up old instance mapping if instance changed
            if thread.instance_id and thread.instance_id != instance_id:
                self._instance_to_thread.pop(thread.instance_id, None)
            
            thread.last_accessed = now
            thread.instance_id = instance_id
            
            # Add new reverse mapping
            self._instance_to_thread[instance_id] = (workspace_id, thread_ts)
            
            # Move to end (most recently used)
            workspace_threads.move_to_end(thread_ts)
            return thread

        # Evict expired threads first
        await self._evict_expired_unlocked(workspace_id)

        # Evict oldest if at capacity
        while len(workspace_threads) >= self._max_threads:
            await self._evict_oldest_unlocked(workspace_id)

        # Create new thread instance
        thread = ThreadInstance(
            thread_ts=thread_ts,
            channel_id=channel_id,
            workspace_id=workspace_id,
            instance_id=instance_id,
            created_at=now,
            last_accessed=now,
        )

        workspace_threads[thread_ts] = thread
        self._instance_to_thread[instance_id] = (workspace_id, thread_ts)

        logger.info(
            f"Registered thread: workspace={workspace_id}, "
            f"channel={channel_id}, thread_ts={thread_ts}, "
            f"instance={instance_id}"
        )

        return thread

    async def _get_thread_unlocked(
        self,
        workspace_id: str,
        thread_ts: str,
    ) -> ThreadInstance | None:
        """Get a thread instance (must hold lock).

        Args:
            workspace_id: The Slack workspace ID.
            thread_ts: The thread timestamp.

        Returns:
            ThreadInstance if found and not expired, None otherwise.
        """
        workspace_threads = self._get_workspace_threads(workspace_id)

        if thread_ts not in workspace_threads:
            return None

        thread = workspace_threads[thread_ts]
        now = time.monotonic()

        # Check if expired
        if now - thread.last_accessed > self._ttl_seconds:
            logger.debug(f"Thread expired: workspace={workspace_id}, thread_ts={thread_ts}")
            del workspace_threads[thread_ts]
            if thread.instance_id:
                self._instance_to_thread.pop(thread.instance_id, None)
            return None

        # Update last accessed and move to end (LRU)
        thread.last_accessed = now
        workspace_threads.move_to_end(thread_ts)

        return thread

    async def get_or_create_instance(
        self,
        workspace_id: str,
        channel_id: str,
        thread_ts: str,
        agent_id: str,
    ) -> str:
        """Get existing instance for thread or create new one.

        Args:
            workspace_id: The Slack workspace ID.
            channel_id: The channel ID.
            thread_ts: The thread timestamp.
            agent_id: The agent ID for new instances.

        Returns:
            The instance ID.
        """
        async with self._threads_guard:
            thread = await self._get_thread_unlocked(workspace_id, thread_ts)
            if thread and thread.instance_id:
                thread.last_accessed = time.monotonic()
                self._threads[workspace_id].move_to_end(thread_ts)
                return thread.instance_id
            
            instance_id, _validated_model_override = await self._manager.spawn_instance(agent_id=agent_id)
            await self._register_thread_unlocked(workspace_id, channel_id, thread_ts, instance_id)
            return instance_id

    async def _evict_expired_unlocked(self, workspace_id: str) -> list[ThreadInstance]:
        """Evict all expired threads for a workspace (must hold lock).

        Args:
            workspace_id: The workspace ID.

        Returns:
            List of evicted ThreadInstances.
        """
        workspace_threads = self._get_workspace_threads(workspace_id)
        now = time.monotonic()
        evicted = []

        # Find expired threads
        expired_ts = [
            ts for ts, thread in workspace_threads.items()
            if now - thread.last_accessed > self._ttl_seconds
        ]

        for ts in expired_ts:
            thread = workspace_threads.pop(ts)
            if thread.instance_id:
                self._instance_to_thread.pop(thread.instance_id, None)
                try:
                    await self._manager.terminate_instance(thread.instance_id)
                except Exception as e:
                    logger.warning(f"Could not terminate expired instance {thread.instance_id}: {e}")
            evicted.append(thread)
            logger.info(f"Evicted expired thread: workspace={workspace_id}, thread_ts={ts}")

        return evicted

    async def _evict_oldest_unlocked(self, workspace_id: str) -> ThreadInstance | None:
        """Evict the oldest (least recently used) thread (must hold lock).

        Args:
            workspace_id: The workspace ID.

        Returns:
            The evicted ThreadInstance, or None if no threads.
        """
        workspace_threads = self._get_workspace_threads(workspace_id)

        if not workspace_threads:
            return None

        # Pop oldest (first item)
        ts, thread = workspace_threads.popitem(last=False)

        if thread.instance_id:
            self._instance_to_thread.pop(thread.instance_id, None)

            # Terminate the instance
            try:
                await self._manager.terminate_instance(thread.instance_id)
                logger.info(
                    f"Terminated evicted instance: instance={thread.instance_id}, "
                    f"reason=LRU_eviction"
                )
            except Exception as e:
                logger.warning(f"Failed to terminate evicted instance: {e}")

        logger.info(
            f"Evicted oldest thread (LRU): workspace={workspace_id}, "
            f"thread_ts={ts}, instance={thread.instance_id}"
        )

        return thread

    async def evict_expired(self) -> list[ThreadInstance]:
        """Evict all expired threads across all workspaces.

        Returns:
            List of all evicted ThreadInstances.
        """
        async with self._threads_guard:
            all_evicted = []
            for workspace_id in list(self._threads.keys()):
                evicted = await self._evict_expired_unlocked(workspace_id)
                all_evicted.extend(evicted)
            return all_evicted

    async def get_stats(self) -> dict:
        """Get thread manager statistics.

        Returns:
            Dict with statistics.
        """
        async with self._threads_guard:
            total_threads = sum(len(threads) for threads in self._threads.values())
            return {
                "total_threads": total_threads,
                "workspaces": len(self._threads),
                "threads_per_workspace": {
                    ws: len(threads) for ws, threads in self._threads.items()
                },
                "ttl_seconds": self._ttl_seconds,
                "max_threads_per_workspace": self._max_threads,
            }

    async def cleanup_instance(self, instance_id: str) -> None:
        """Clean up thread mapping for an instance.

        Args:
            instance_id: The instance ID to clean up.
        """
        async with self._threads_guard:
            key = self._instance_to_thread.pop(instance_id, None)
            if key:
                workspace_id, thread_ts = key
                workspace_threads = self._get_workspace_threads(workspace_id)
                workspace_threads.pop(thread_ts, None)
                logger.debug(f"Cleaned up thread for instance: {instance_id}")

    async def shutdown(self) -> None:
        """Terminate all tracked instances on adapter shutdown."""
        async with self._threads_guard:
            for workspace_id in list(self._threads.keys()):
                workspace_threads = self._threads[workspace_id]
                for ts, thread in list(workspace_threads.items()):
                    if thread.instance_id:
                        try:
                            await self._manager.terminate_instance(thread.instance_id)
                        except Exception as e:
                            logger.warning(f"Could not terminate instance {thread.instance_id}: {e}")
                workspace_threads.clear()
            self._instance_to_thread.clear()
