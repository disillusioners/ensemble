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

from langchain_core.tools import BaseTool
from mcp import StdioServerParameters
from langchain_mcp_adapters.tools import load_mcp_tools

from daemon.mcp.config import McpStdioConfig
from daemon.mcp.managed_session import ManagedClientSession
from daemon.mcp.models import McpToolSchema
from daemon.mcp.stdio_wrapper import TaskScopedStdioClient
from daemon.mcp.tool_adapter import _slugify, adapt_mcp_tools

logger = logging.getLogger(__name__)

# Module-level singleton
_mcp_warmup_pool: McpWarmupPool | None = None


@dataclass
class PooledConnection:
    """A pre-warmed MCP connection ready for immediate use."""

    session: ManagedClientSession
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

    def __init__(self, tool_call_timeout: int = 120) -> None:
        """Initialize the warmup pool with empty state.

        Args:
            tool_call_timeout: Default timeout (seconds) applied to MCP tool calls
                when adapting discovered tools. Defaults to 120 for backward
                compatibility.
        """
        self._pools: dict[str, asyncio.Queue[PooledConnection]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._configs: dict[str, McpStdioConfig] = {}
        self._pool_sizes: dict[str, int] = {}
        self._tool_discovery_cache: dict[str, list[BaseTool]] = {}
        self._running: bool = False
        self._health_task: asyncio.Task | None = None
        self._replenish_tasks: set[asyncio.Task] = set()
        self._replenish_semaphore: asyncio.Semaphore = asyncio.Semaphore(2)
        self._tool_call_timeout: int = tool_call_timeout
        # Per-server timeout overrides. Servers without an entry fall back to
        # ``self._tool_call_timeout``. A value of ``0`` is meaningful here —
        # it disables the per-call timeout wrap entirely, so we must use
        # ``is not None`` checks (not truthiness) when populating / looking up.
        self._tool_call_timeouts: dict[str, int] = {}

    def register_server(
        self,
        server_name: str,
        config: McpStdioConfig,
        pool_size: int = DEFAULT_POOL_SIZE,
        tool_call_timeout: int | None = None,
    ) -> None:
        """
        Register a built-in STDIO server for pooling.

        Args:
            server_name: Unique name for this server
            config: STDIO configuration for the server
            pool_size: Number of connections to maintain (default: 1)
            tool_call_timeout: Optional per-server tool call timeout override
                (seconds). When ``None`` (the default), tools for this server
                inherit the pool-wide ``self._tool_call_timeout``. When an
                integer is provided — including ``0``, which disables the
                per-call timeout wrap entirely — it overrides the pool default
                for this server only.
        """
        if server_name in self._pools:
            logger.warning(
                f"Server '{server_name}' already registered, skipping "
                f"(ignored timeout override: pool already using its original tool_call_timeout)"
            )
            return

        self._pools[server_name] = asyncio.Queue()
        self._locks[server_name] = asyncio.Lock()
        self._configs[server_name] = config
        self._pool_sizes[server_name] = pool_size
        if tool_call_timeout is not None:
            # ``is not None`` (not truthiness) — ``0`` must be stored because it
            # is a valid override that disables the timeout wrap.
            self._tool_call_timeouts[server_name] = tool_call_timeout
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
                success_count = await self._warmup_server(server_name, size)
                if success_count == size:
                    logger.info(f"Warmed up pool for '{server_name}' ({success_count}/{size} connections)")
                elif success_count > 0:
                    logger.warning(f"Partially warmed up pool for '{server_name}' ({success_count}/{size} connections)")
                else:
                    logger.error(f"Failed to warm up pool for '{server_name}' (0/{size} connections created)")
            except Exception as e:
                logger.error(f"Failed to warm up pool for '{server_name}': {e}", exc_info=True)

        await asyncio.gather(
            *[warmup_server(name) for name in self._configs],
            return_exceptions=True,
        )
        logger.info(f"MCP warmup complete: {len(self._configs)} server(s) ready")

    async def _warmup_server(self, server_name: str, size: int) -> int:
        """
        Warm up a single server's pool.

        Args:
            server_name: Name of the server
            size: Number of connections to create

        Returns:
            Number of successfully created connections
        """
        pool = self._pools[server_name]
        tasks = [self._create_pooled_connection(server_name) for _ in range(size)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success_count = 0
        for result in results:
            if isinstance(result, (Exception, asyncio.CancelledError)):
                logger.error(
                    f"Failed to create pooled connection for '{server_name}': "
                    f"{type(result).__name__}: {result}",
                    exc_info=(type(result), result, result.__traceback__),
                )
            elif isinstance(result, BaseException):
                # Re-raise KeyboardInterrupt, SystemExit, etc.
                raise result
            else:
                await pool.put(result)
                success_count += 1

        return success_count

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
        streams_cm = TaskScopedStdioClient(server_params)
        read_stream = write_stream = None
        session: ManagedClientSession | None = None

        try:
            read_stream, write_stream = await streams_cm.__aenter__()

            # Single outer timeout wrapping everything from startup to tool discovery
            async with asyncio.timeout(60):
                # Give subprocess time to start up (npx/uvx need time for package resolution)
                await asyncio.sleep(2.0)

                # Use ManagedClientSession to keep receive loop running after initialization
                session = ManagedClientSession(read_stream, write_stream)
                await session.start()

                # Retry initialize with per-attempt timeout
                max_retries = 3
                last_error: Exception | None = None
                for attempt in range(1, max_retries + 1):
                    try:
                        async with asyncio.timeout(10):
                            await session.initialize()
                            break  # Success
                    except asyncio.CancelledError:
                        raise  # Propagate cancellation immediately
                    except (asyncio.TimeoutError, Exception) as e:
                        last_error = e
                        if attempt < max_retries:
                            wait_time = attempt * 2  # 2s, 4s backoff
                            logger.warning(
                                f"Initialize attempt {attempt}/{max_retries} failed for '{server_name}': "
                                f"{type(e).__name__}: {e}. Retrying in {wait_time}s..."
                            )
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(
                                f"All {max_retries} initialize attempts failed for '{server_name}'"
                            )
                            raise last_error

                # Tool discovery (inside the 60s outer timeout)
                if server_name in self._tool_discovery_cache:
                    tools = self._tool_discovery_cache[server_name]
                else:
                    tools = await load_mcp_tools(session)
                    # Resolve effective timeout: per-server override (set via
                    # register_server) wins over the pool-wide default. ``0`` is
                    # a valid override (disables the wrap) so we use ``dict.get``
                    # rather than truthy fallbacks.
                    timeout = self._tool_call_timeouts.get(
                        server_name, self._tool_call_timeout
                    )
                    tools = adapt_mcp_tools(server_name, tools, tool_call_timeout=timeout)
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
            if session is not None:
                try:
                    await session.stop()
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

    def is_pooled_server(self, server_name: str) -> bool:
        """Return True if this server is registered with the warmup pool.

        Public accessor for the pool's known-server set. Use this rather
        than poking at ``self._configs`` from outside the class.

        Args:
            server_name: Name of the server to check.

        Returns:
            True iff ``server_name`` was passed to ``register_server()``.
        """
        return server_name in self._configs

    def get_cached_tool_schemas(
        self, server_name: str
    ) -> list[McpToolSchema] | None:
        """Extract tool schemas from the discovery cache for a pooled server.

        Called by ``McpService.get_schemas_for_server`` so the schema cache
        can be populated without opening a new connection. Schemas are
        returned with the original (un-prefixed) MCP tool names — the
        ``mcp_{slug}_`` prefix that ``adapt_mcp_tools`` adds is stripped
        here.

        Args:
            server_name: Name of the pooled server.

        Returns:
            List of ``McpToolSchema`` instances, or ``None`` if the server
            has no cached tools (e.g. warmup hasn't completed yet).
        """
        if server_name not in self._tool_discovery_cache:
            return None

        cached_tools = self._tool_discovery_cache[server_name]
        prefix = f"mcp_{_slugify(server_name)}_"

        schemas: list[McpToolSchema] = []
        for tool in cached_tools:
            original_name = tool.name
            if original_name.startswith(prefix):
                original_name = original_name[len(prefix):]
            schemas.append(
                McpToolSchema(
                    name=original_name,
                    description=tool.description or "",
                    input_schema=self._extract_input_schema(tool.args_schema),
                    server_name=server_name,
                )
            )
        return schemas

    @staticmethod
    def _extract_input_schema(args_schema: Any) -> dict[str, Any]:
        """Normalize ``tool.args_schema`` into a JSON Schema dict.

        ``langchain_core.tools.BaseTool.args_schema`` can be one of:
        - A Pydantic ``BaseModel`` class — exposes ``.schema()``.
        - A plain ``dict`` — already a JSON Schema (some MCP servers
          advertise schemas this way through ``langchain_mcp_adapters``).
        - ``None`` — no schema advertised.

        The previous implementation called ``.schema()`` unconditionally,
        which raised ``AttributeError: 'dict' object has no attribute
        'schema'`` whenever a server returned a dict-shaped schema, making
        the entire server's tools invisible to agents.

        Args:
            args_schema: The ``args_schema`` attribute of an adapted
                ``BaseTool`` instance.

        Returns:
            A JSON Schema dict. Falls back to ``{}`` for ``None`` or
            unexpected types so a single bad tool never breaks the rest
            of the discovery batch.
        """
        if args_schema is None:
            return {}
        if isinstance(args_schema, dict):
            return args_schema
        # Pydantic v1 / v2 BaseModel classes expose ``.schema()`` /
        # ``.model_json_schema()``; prefer the v1 method for backward
        # compatibility with the existing test fixtures.
        schema_method = getattr(args_schema, "schema", None)
        if callable(schema_method):
            try:
                return schema_method()
            except Exception:
                return {}
        return {}

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
                    except Exception as e:
                        logger.warning(
                            f"Health check failed for pooled {server_name}: {e}", exc_info=True
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

    async def release_connection(self, conn: PooledConnection) -> None:
        """Public API: release a ``PooledConnection`` back to the pool's lifecycle.

        Delegates to ``_close_connection`` so subprocess teardown + stream
        CM aexit live in exactly one place. Callers that acquired a
        connection outside the normal ``acquire`` / transfer flow
        (e.g. ``_McpSessionProviderImpl`` after a failed
        ``transfer_session``) MUST go through this method instead of
        touching ``_close_connection`` directly — that's the
        encapsulation boundary the pool exposes.

        Args:
            conn: The ``PooledConnection`` to release.
        """
        await self._close_connection(conn)

    async def _close_connection(self, conn: PooledConnection) -> None:
        """
        Close a PooledConnection cleanly.

        Args:
            conn: Connection to close
        """
        try:
            await conn.session.stop()
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
