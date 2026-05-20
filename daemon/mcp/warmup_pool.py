"""MCP pre-warmed connection pool for built-in STDIO servers.

Eliminates cold-start delay by keeping ready-to-use subprocess connections available.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass
from typing import Any

import mcp
from langchain_core.tools import BaseTool
from mcp import ClientSession, StdioServerParameters
from langchain_mcp_adapters.tools import load_mcp_tools

from daemon.mcp.config import McpStdioConfig

logger = logging.getLogger(__name__)

# Module-level singleton
_mcp_warmup_pool: McpWarmupPool | None = None


@dataclass
class PooledConnection:
    """A pre-warmed MCP connection ready for immediate use."""

    session: ClientSession
    stream_cm: Any
    tools: list[BaseTool]
    server_name: str
    created_at: float


class McpWarmupPool:
    """
    Pre-warmed connection pool for built-in STDIO MCP servers.

    Eliminates the 5-15s cold-start delay by keeping ready-to-use subprocess
    connections available. Manages pool lifecycle, health checks, and automatic
    replenishment.
    """

    DEFAULT_POOL_SIZE = 1

    def __init__(self) -> None:
        """Initialize the warmup pool with empty state."""
        self._pools: dict[str, asyncio.Queue[PooledConnection]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._configs: dict[str, McpStdioConfig] = {}
        self._pool_sizes: dict[str, int] = {}
        self._tool_discovery_cache: dict[str, list[BaseTool]] = {}
        self._running: bool = False
        self._health_task: asyncio.Task | None = None
        self._replenish_tasks: set[asyncio.Task] = set()
        self._replenish_semaphore: asyncio.Semaphore = asyncio.Semaphore(2)

    def register_server(self, server_name: str, config: McpStdioConfig, pool_size: int = DEFAULT_POOL_SIZE) -> None:
        """
        Register a built-in STDIO server for pooling.

        Args:
            server_name: Unique name for this server
            config: STDIO configuration for the server
            pool_size: Number of connections to maintain (default: 1)
        """
        if server_name in self._pools:
            logger.warning(f"Server '{server_name}' already registered, skipping")
            return

        self._pools[server_name] = asyncio.Queue()
        self._locks[server_name] = asyncio.Lock()
        self._configs[server_name] = config
        self._pool_sizes[server_name] = pool_size
        logger.debug(f"Registered server '{server_name}' with pool_size={pool_size}")

    async def warmup(self, pool_size: dict[str, int] | None = None) -> None:
        """
        Warm up pools for all registered servers.

        Spawns N subprocess connections in parallel, completes handshake,
        discovers tools, and stores as PooledConnection.

        Non-fatal: logs errors but does not raise.

        Args:
            pool_size: Optional per-server pool size override
        """
        if not self._configs:
            logger.warning("No servers registered for warmup")
            return

        if self._running:
            logger.warning("Warmup already in progress or completed, skipping")
            return

        self._running = True

        async def warmup_server(server_name: str) -> None:
            size = pool_size.get(server_name, self.DEFAULT_POOL_SIZE) if pool_size else self._pool_sizes.get(server_name, 1)
            try:
                await self._warmup_server(server_name, size)
                logger.info(f"Warmed up pool for '{server_name}' ({size} connections)")
            except Exception as e:
                logger.error(f"Failed to warm up pool for '{server_name}': {e}", exc_info=True)

        await asyncio.gather(
            *[warmup_server(name) for name in self._configs],
            return_exceptions=True,
        )
        logger.info(f"MCP warmup complete: {len(self._configs)} server(s) ready")

    async def _warmup_server(self, server_name: str, size: int) -> None:
        """
        Warm up a single server's pool.

        Args:
            server_name: Name of the server
            size: Number of connections to create
        """
        pool = self._pools[server_name]
        tasks = [self._create_pooled_connection(server_name) for _ in range(size)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, BaseException):
                logger.error(
                    f"Failed to create pooled connection for '{server_name}': "
                    f"{type(result).__name__}: {result}",
                    exc_info=result,
                )
            else:
                await pool.put(result)

    async def _create_pooled_connection(self, server_name: str) -> PooledConnection:
        """
        Create a single pooled connection.

        Creates STDIO subprocess, completes handshake, discovers tools.

        CRITICAL: Cleans up subprocess on any failure to prevent orphans.

        Args:
            server_name: Name of the server to connect to

        Returns:
            PooledConnection ready for immediate use

        Raises:
            Exception: On connection failure (after cleanup)
        """
        config = self._configs[server_name]
        server_params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=config.env,
        )
        streams_cm = mcp.stdio_client(server_params)
        session: ClientSession | None = None

        try:
            read_stream, write_stream = await streams_cm.__aenter__()
            session = ClientSession(read_stream, write_stream)
            async with asyncio.timeout(30):
                await session.initialize()

                # Use cached tools if available, otherwise discover
                if server_name in self._tool_discovery_cache:
                    tools = self._tool_discovery_cache[server_name]
                else:
                    tools = await load_mcp_tools(session)
                    self._tool_discovery_cache[server_name] = tools

            return PooledConnection(
                session=session,
                stream_cm=streams_cm,
                tools=tools,
                server_name=server_name,
                created_at=time.monotonic(),
            )
        except BaseException:
            # Clean up to prevent orphaned subprocess
            try:
                if session is not None:
                    await session.close()
            except Exception:
                pass
            try:
                await streams_cm.__aexit__(*sys.exc_info())
            except Exception:
                pass
            raise

    async def acquire(self, server_name: str) -> PooledConnection | None:
        """
        Acquire a pooled connection (non-blocking).

        Args:
            server_name: Name of the server

        Returns:
            PooledConnection if available, None otherwise
        """
        if not self._running:
            return None

        pool = self._pools.get(server_name)
        if pool is None:
            return None

        try:
            conn = pool.get_nowait()
        except asyncio.QueueEmpty:
            return None

        self._start_tracked_replenish(server_name)
        return conn

    def _start_tracked_replenish(self, server_name: str) -> None:
        """
        Start a background replenish task (fire-and-forget).

        Args:
            server_name: Name of the server to replenish
        """
        if not self._running:
            return

        task = asyncio.create_task(self._replenish(server_name))
        self._replenish_tasks.add(task)
        task.add_done_callback(self._replenish_tasks.discard)

    async def _replenish(self, server_name: str) -> None:
        """
        Background task to replenish pool to target size.

        Uses semaphore to cap concurrent replenishment across all servers,
        and per-server lock to prevent race conditions.

        Args:
            server_name: Name of the server to replenish
        """
        if not self._running:
            return

        pool = self._pools.get(server_name)
        if pool is None or pool.qsize() >= self._pool_sizes.get(server_name, self.DEFAULT_POOL_SIZE):
            return

        async with self._replenish_semaphore:
            if not self._running:
                return

            async with self._locks[server_name]:
                if pool.qsize() >= self._pool_sizes.get(server_name, self.DEFAULT_POOL_SIZE):
                    return

                try:
                    conn = await self._create_pooled_connection(server_name)
                    await pool.put(conn)
                    logger.debug(f"Replenished pool for {server_name}")
                except Exception as e:
                    logger.warning(f"Failed to replenish pool for {server_name}: {e}", exc_info=True)

    async def health_check(self) -> None:
        """
        Perform health check on all pooled connections.

        Checks each connection via ping, evicts dead ones, replenishes to target size.
        """
        for server_name, pool in self._pools.items():
            lock = self._locks[server_name]
            async with lock:
                # Snapshot all connections
                snapshot: list[PooledConnection] = []
                while True:
                    try:
                        snapshot.append(pool.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                # Check health and separate healthy from dead
                healthy: list[PooledConnection] = []
                for conn in snapshot:
                    try:
                        await asyncio.wait_for(conn.session.send_ping(), timeout=5.0)
                        healthy.append(conn)
                    except Exception:
                        logger.warning(
                            f"Health check failed for pooled {server_name}, discarding"
                        )
                        await self._close_connection(conn)

                # Put healthy connections back
                for conn in healthy:
                    await pool.put(conn)

                # Replenish if below target
                if len(healthy) < self._pool_sizes.get(server_name, self.DEFAULT_POOL_SIZE):
                    self._start_tracked_replenish(server_name)

    async def _health_check_loop(self, interval: float) -> None:
        """
        Background loop for periodic health checks.

        Args:
            interval: Seconds between health checks
        """
        while self._running:
            try:
                await asyncio.sleep(interval)
                if self._running:
                    await self.health_check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check loop error: {e}", exc_info=True)

    def start_health_check(self, interval: float = 60) -> None:
        """
        Start the background health check loop.

        Args:
            interval: Seconds between health checks (default: 60)
        """
        if self._health_task is not None and not self._health_task.done():
            logger.warning("Health check already running")
            return

        self._health_task = asyncio.create_task(self._health_check_loop(interval))
        logger.info(f"Started MCP health check loop (interval={interval}s)")

    async def drain(self) -> None:
        """Drain the pool: stop accepting, cancel tasks, close connections."""
        self._running = False
        self._tool_discovery_cache.clear()

        # Cancel replenish tasks
        for task in self._replenish_tasks:
            task.cancel()

        if self._replenish_tasks:
            await asyncio.gather(*self._replenish_tasks, return_exceptions=True)
        self._replenish_tasks.clear()

        # Cancel health check task
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

        # Close all pooled connections
        close_tasks: list[asyncio.Task[None]] = []
        for server_name, pool in self._pools.items():
            while True:
                try:
                    conn = pool.get_nowait()
                    close_tasks.append(asyncio.create_task(self._close_connection(conn)))
                except asyncio.QueueEmpty:
                    break

        if close_tasks:
            await asyncio.wait_for(
                asyncio.gather(*close_tasks, return_exceptions=True),
                timeout=10.0,
            )

        # Final sweep: catch any connections added by late replenish tasks
        for server_name, pool in self._pools.items():
            while True:
                try:
                    conn = pool.get_nowait()
                    await self._close_connection(conn)
                except asyncio.QueueEmpty:
                    break

        logger.info("MCP warm-up pool drained")

    async def _close_connection(self, conn: PooledConnection) -> None:
        """
        Close a PooledConnection cleanly.

        Args:
            conn: Connection to close
        """
        try:
            await conn.session.close()
        except Exception:
            pass
        try:
            await conn.stream_cm.__aexit__(None, None, None)
        except Exception:
            pass

    def get_status(self) -> dict[str, dict[str, Any]]:
        """
        Get pool status for observability.

        Returns:
            Dict mapping server_name to pool stats
        """
        return {
            server_name: {
                "available": pool.qsize(),
                "pool_size": self._pool_sizes.get(server_name, 1),
                "healthy": pool.qsize() > 0,
            }
            for server_name, pool in self._pools.items()
        }


def get_mcp_warmup_pool() -> McpWarmupPool:
    """
    Get the module-level singleton McpWarmupPool instance.

    Returns:
        The singleton McpWarmupPool
    """
    global _mcp_warmup_pool
    if _mcp_warmup_pool is None:
        _mcp_warmup_pool = McpWarmupPool()
    return _mcp_warmup_pool
