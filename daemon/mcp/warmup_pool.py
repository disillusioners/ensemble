"""MCP pre-warmed connection pool for built-in STDIO servers.

Eliminates cold-start delay by keeping ready-to-use subprocess connections available.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
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
# P3 review F3: route the stored env through the spawn-time marker
# resolver so pooled connections see plaintext (never ``__KMS_REF__…__``)
# in the subprocess env. resolve_env is idempotent for non-marker values.
from daemon.services.kms_resolver import resolve_env
# agent-secrets P2 (design §1c) — LITERAL composition:
# ``resolve_secret_markers(resolve_env(config.env))``. KMS-Lite runs
# FIRST (the pre-existing seam), agent-secrets markers resolve on the
# KMS-resolved dict (marker shapes are disjoint — the composition is
# order-stable). Spawn-point resolution ONLY: ``build_pooled_stdio_config``
# is DO-NOT-TOUCH (R4 — resolving there would persist plaintext into
# DB config; see p2-architecture-recommendation.md §1c). Surface
# ``"mcp_spawn"`` (envelope + audit context — the two forms are
# byte-identical for this seam).
# Gap-2: service discovery consolidated into the scrub module's
# public ``get_process_secret_service``.
from daemon.services.secret_persistence_scrub import (
    get_process_secret_service as _get_process_secret_service,
)
from daemon.services.secret_resolver import resolve_secret_markers
logger = logging.getLogger(__name__)

# ── idle reap (O4 Part B) ────────────────────────────────────────────────────
# Bound on the WHOLE teardown of a reaped idle connection. The STDIO transport
# teardown performed inside the ``mcp`` library closes the child's stdin, waits
# briefly for exit, and then escalates SIGTERM -> SIGKILL over the child's
# process group (``mcp.os.posix.utilities.terminate_posix_process_tree``). This
# window is the outer guard: a teardown that wedges past it must not stall the
# pool's maintenance pass, so we hard-cancel the owning task (see
# ``McpWarmupPool._reap_idle_connection``).
REAP_CLOSE_GRACE_SECONDS: float = 10.0
# How long to wait for a hard-cancelled teardown task to actually unwind before
# giving up on it (the residual is logged at WARNING).
REAP_CANCEL_JOIN_GRACE_SECONDS: float = 2.0

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


# Total warmup budget (outer timeout). The plan's only warmup budget is
# the 60s outer timeout (phase2-plan.md step 3). The contract's
# Warmup budgets (architecture-recommendation.md:263, :549 constants table):
#   WARMUP_CREATE_BUDGET_S = 60.0  — per-spawn budget (the outer timeout
#                                    around a single _create_pooled_connection).
#   WARMUP_TOTAL_BUDGET_S   = 90.0  — belt-and-braces around the per-server
#                                    gather in warmup() so warmup() RETURNS
#                                    even when the initial gather would hang
#                                    (the hang-wedge mechanism).
WARMUP_CREATE_BUDGET_S: float = 60.0
WARMUP_TOTAL_BUDGET_S: float = 90.0

# Dedicated executor for secret/KMS marker resolution (architecture §5.1).
# NOT the shared default executor (which carries LLM calls + compaction).
# Queue depth: bounded by spawn count, fully bounded only by phase-3's C1
# quarantine (S1). Process-lifetime lazy threads (house precedent); no
# shutdown path wired here (manager.py is out of the daemon-file scope fence).
_SECRET_RESOLVE_EXECUTOR = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="mcp-secret"
)

class McpWarmupPool:
    """
    Pre-warmed connection pool for built-in STDIO MCP servers.

    Eliminates the 5-15s cold-start delay by keeping ready-to-use subprocess
    connections available. Manages pool lifecycle, health checks, and automatic
    replenishment.
    """

    DEFAULT_POOL_SIZE = 1

    def __init__(
        self,
        tool_call_timeout: int = 120,
        idle_reap_seconds: int = 1800,
        health_check_timeout: int = 5,
    ) -> None:
        """Initialize the warmup pool with empty state.

        Args:
            tool_call_timeout: Default timeout (seconds) applied to MCP tool calls
                when adapting discovered tools. Defaults to 120 for backward
                compatibility.
            idle_reap_seconds: Reap pooled connections that have been idle
                (sitting unused in the pool queue) for at least this many
                seconds. Default 1800 (30 minutes). ``0`` disables idle
                reaping. Mirrors ``McpPoolConfig.idle_reap_seconds``.
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
        self._idle_reap_seconds: int = idle_reap_seconds
        self._health_check_timeout: int = health_check_timeout
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

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *[warmup_server(name) for name in self._configs],
                    return_exceptions=True,
                ),
                timeout=WARMUP_TOTAL_BUDGET_S,
            )
        except asyncio.TimeoutError:
            # ONLY the total budget's own timeout reaches here:
            # ``asyncio.wait_for`` converts its timeout to TimeoutError.
            # A CancelledError here is an OUTER cancellation (the daemon
            # shutting down) and MUST propagate: swallowing it would let
            # this task complete successfully, so the manager's
            # ``await self._warmup_task`` would not raise and
            # ``_warmup_and_report`` would CONTINUE past the shutdown
            # cancel — calling ``start_health_check`` (a new background
            # task during shutdown) and ``eager_warm_schemas()``, which
            # can SPAWN FRESH MCP SUBPROCESSES while the daemon is going
            # down and block the shutdown on that discovery.
            logger.warning(
                "MCP warmup total budget (%.1fs) exceeded; proceeding so "
                "starters can run (hang-wedge belt-and-braces).",
                WARMUP_TOTAL_BUDGET_S,
            )
        except asyncio.CancelledError:
            # An OUTER cancellation (daemon shutdown) — propagate it.
            raise
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
        # agent-secrets P2 (design §1c) — LITERAL composition:
        # ``resolve_secret_markers(resolve_env(config.env))``. Stored
        # config NEVER holds resolved values (R1); the resolution
        # happens at the SPAWN POINT, mirroring KMS-Lite. A failing
        # marker (missing id, malformed, DB down) raises the typed
        # envelope BEFORE the subprocess spawns — the server does NOT
        # start with literal markers or partial env (fail-closed).
        # Off-loop: secret/KMS marker resolution is synchronous and
        # can block on Fernet/KMS I/O — run it on a worker thread so it
        # never occupies the event loop (B1). Fail-closed semantics are
        # preserved: a raising marker propagates and aborts the spawn
        # BEFORE the subprocess starts.
        def _resolve_env_sync() -> dict[str, str] | None:
            return resolve_secret_markers(
                resolve_env(config.env),
                secret_service=_get_process_secret_service(),
                actor="<secret_resolver>",
                surface="mcp_spawn",
            )

        resolved_env = await asyncio.get_running_loop().run_in_executor(
            _SECRET_RESOLVE_EXECUTOR, _resolve_env_sync
        )
        server_params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=resolved_env,
        )
        streams_cm = TaskScopedStdioClient(server_params)
        read_stream = write_stream = None
        session: ManagedClientSession | None = None

        try:
            # Single outer timeout wrapping everything from startup to tool discovery
            # (the handshake __aenter__ is now INSIDE the budget — previously
            # it sat outside and a hung handshake could wedge forever).
            async with asyncio.timeout(WARMUP_CREATE_BUDGET_S):
                read_stream, write_stream = await streams_cm.__aenter__()

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
            # Clean up to prevent orphaned subprocess.
            #
            # B3: the close request comes FIRST and is SYNCHRONOUS. Under
            # a cancellation, the FIRST await in this block raises
            # CancelledError immediately, so any cleanup that is not
            # requested before it would never run — the transport would
            # never learn it must close and the spawned child would
            # survive. ``request_close()`` sets the close event with no
            # await, so it works from a cancelled task.
            try:
                streams_cm.request_close()
            except Exception:
                pass
            if session is not None:
                try:
                    await session.stop()
                except BaseException:
                    pass
            try:
                await streams_cm.__aexit__(*sys.exc_info())
            except BaseException:
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

    def update_server_env(self, server_name: str, new_env: dict[str, str]) -> bool:
        """Refresh the stored env for a pooled server WITHOUT touching healthy connections.

        Fix for MCP warmup-pool config starvation (2026-10-02, defect
        pinned against ``definition.build_config({})`` in
        ``InstanceManager._init_warmup_pool``). The pool registered
        each server's config from the BUILTIN definition's defaults at
        boot; ``mcp_set_env`` and ``kms_attach`` writes land ONLY in
        the DB row. A subsequent ``acquire()`` + ``_replenish`` would
        re-spawn a subprocess from the stale ``_configs[server].env``,
        so non-secret / KMS marker env never reached the pool's stdio
        subprocesses (live symptom: a server's tool calls failed with
        "config not configured" while the DB row held the values).

        The fix has two halves (see ``_init_warmup_pool`` in
        ``daemon/manager.py`` for the other half — the boot-time row
        overlay):

          * This method keeps the stored config fresh. The next
            ``_create_pooled_connection`` reads the updated env, so
            ``acquire()`` + replenish cycles serve fresh env to new
            subprocesses.
          * Existing HEALTHY pooled connections keep the env they were
            spawned with (subprocess env is captured at fork time, the
            daemon cannot retroactively rewrite it). They drain
            naturally through the normal acquire/release cycle and are
            replaced by connections spawned with the new env. We
            deliberately do NOT kill healthy connections — that would
            negate the whole point of the warmup pool. Unrelated
            pooled servers are untouched.

        KMS markers (LANE-1 ``__KMS_REF__<handle>__`` and LANE-2
        ``__KMS_ENV__<var>__``) flow through unchanged — the resolver
        at ``daemon/services/kms_resolver.resolve_env`` still runs at
        spawn time on the refreshed env.

        Args:
            server_name: The pooled server whose env changed.
            new_env: The new env dict to store. Merged over any prior
                env so a partial write (e.g. mcp_set_env only setting
                a few of the schema-declared env keys) lands atomically
                alongside the pre-existing keys (incl. KMS markers).

        Returns:
            True iff the server was registered and its stored env was
            refreshed. False if the server is not in this pool (no-op,
            matching the bootstrap "skip if not registered" pattern).
        """
        if server_name not in self._configs:
            return False

        # Defensive copy — callers (mcp_set_env, kms_attach) may have
        # kept a reference and reuse it; we don't want a later mutation
        # to silently corrupt the pool's view.
        stored = self._configs[server_name]
        merged_env: dict[str, str] = dict(stored.env or {})
        if new_env:
            merged_env.update(new_env)

        # Replace the stored entry. Command/args/timeout do not change
        # at runtime (env is the only mutable axis for stdio servers),
        # so a shallow copy of the existing McpStdioConfig with the
        # updated env is the correct semantics. We re-construct the
        # model rather than mutating in place so the change is atomic
        # and trivially observable to concurrent _create_pooled_connection
        # readers (the next replenishment sees the new env).
        self._configs[server_name] = McpStdioConfig(
            transport=stored.transport,
            command=stored.command,
            args=list(stored.args),
            env=merged_env,
            timeout=stored.timeout,
        )
        return True

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
                    logger.debug(f"Failed to replenish pool for {server_name}: {e}", exc_info=True)

    async def health_check(self) -> None:
        """
        Perform health check on all pooled connections.

        Checks each connection via ping, evicts dead ones, replenishes to target size.
        Also reaps connections that have been idle longer than
        ``idle_reap_seconds`` (``0`` disables idle reaping) — see
        :meth:`_reap_idle_connection`. Both eviction paths ride this single
        existing maintenance pass; there is no separate loop or thread.
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
                    # Idle reap (O4 Part B). Only connections resting in the
                    # QUEUE reach this loop: a connection that is in use has
                    # been ``acquire()``-d out of the queue (ownership
                    # transferred to the caller), so it is structurally absent
                    # here and can never be reaped mid-use. Should such a
                    # connection ever return to the pool it is re-considered on
                    # a later pass. The snapshot-and-drain under the per-server
                    # lock is what makes this guard airtight.
                    if self._idle_reap_seconds > 0:
                        idle_seconds = time.monotonic() - conn.created_at
                        if idle_seconds >= self._idle_reap_seconds:
                            await self._reap_idle_connection(
                                server_name, conn, idle_seconds
                            )
                            continue
                    try:
                        await asyncio.wait_for(conn.session.send_ping(), timeout=self._health_check_timeout)
                        healthy.append(conn)
                    except Exception as e:
                        logger.warning(
                            f"Health check failed for pooled {server_name}: {e}", exc_info=True
                        )
                        await self._close_connection(conn)

                # Put healthy connections back
                for conn in healthy:
                    await pool.put(conn)

                # Replenish if below target. Reaped-as-idle slots refill here
                # on the same cadence as health-evicted ones — the pool keeps
                # its warm-start contract; only a connection's LIFETIME is
                # bounded by ``idle_reap_seconds``.
                if len(healthy) < self._pool_sizes.get(server_name, self.DEFAULT_POOL_SIZE):
                    self._start_tracked_replenish(server_name)

    async def _reap_idle_connection(
        self,
        server_name: str,
        conn: PooledConnection,
        idle_seconds: float,
    ) -> None:
        """Reap one idle pooled connection with a bounded, escalating teardown.

        Called by :meth:`health_check` for a connection that has been idle at
        least ``idle_reap_seconds``. Uses the standard close path
        (``session.stop()`` + stream-CM ``__aexit__``), which for the STDIO
        transport closes the child's stdin and — inside the ``mcp`` library —
        waits briefly before escalating SIGTERM -> SIGKILL over the child's
        process group. That IS the graceful-terminate -> SIGKILL-after-a-grace
        contract; this method adds the outer bound (``REAP_CLOSE_GRACE_SECONDS``)
        so a wedged teardown cannot stall the pool's maintenance pass, plus a
        hard-cancel escalation on expiry. Every reap/reap-failure is logged
        with the server name and the idle duration.

        Args:
            server_name: Server the connection belongs to.
            conn: The idle pooled connection to reap.
            idle_seconds: How long ``conn`` has been idle (for the log/audit).
        """
        logger.info(
            f"Reaping idle pooled MCP connection: server='{server_name}' "
            f"idle={idle_seconds:.0f}s (idle_reap_seconds={self._idle_reap_seconds})"
        )
        try:
            await asyncio.wait_for(
                self._close_connection(conn), timeout=REAP_CLOSE_GRACE_SECONDS
            )
            return
        except asyncio.TimeoutError:
            # ONLY the grace window's own timeout reaches here. An outer
            # cancellation (the health-check task being cancelled) MUST
            # propagate — swallowing it would make the cancelled task
            # complete successfully and hide the cancel from its awaiter.
            logger.warning(
                f"Idle reap teardown for server='{server_name}' exceeded the "
                f"{REAP_CLOSE_GRACE_SECONDS:.0f}s grace window "
                f"(idle={idle_seconds:.0f}s); escalating to hard teardown cancel"
            )
        except asyncio.CancelledError:
            # An OUTER cancellation — propagate it.
            raise
        except Exception as e:
            logger.warning(
                f"Idle reap teardown error for server='{server_name}' "
                f"(idle={idle_seconds:.0f}s): {type(e).__name__}: {e}"
            )
            return

        # Escalation: the graceful window expired. Hard-cancel the task that
        # owns the inner context manager so the wedged ``__aexit__`` cannot
        # keep a live task (and the child it holds) around, and so the
        # maintenance pass always makes progress. Non-``asyncio.Task`` stubs
        # (test fakes) are left alone.
        task = getattr(conn.stream_cm, "background_task", None)
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()
            try:
                await asyncio.wait(
                    {task}, timeout=REAP_CANCEL_JOIN_GRACE_SECONDS
                )
            except Exception:
                pass
            if not task.done():
                logger.warning(
                    f"Idle reap teardown for server='{server_name}' still "
                    f"pending after hard cancel "
                    f"(idle={idle_seconds:.0f}s) — child may linger until "
                    f"process exit"
                )

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


def build_pooled_stdio_config(
    definition: "BuiltinServerDefinition",
    row_config: dict | None,
) -> McpStdioConfig:
    """Build a pooled ``McpStdioConfig`` from a builtin definition + live row.

    Fix for MCP warmup-pool config starvation (2026-10-02, paired with
    :meth:`McpWarmupPool.update_server_env`): the pre-fix pool was
    registered with ``definition.build_config({})`` only, ignoring the
    DB row's ``config.env``. After any ``mcp_set_env`` / ``kms_attach``
    write the row carried the live env but the pool did not. This
    helper is the boot-time half of the fix: it overlays the row's
    ``config.env`` over the definition defaults so the very first
    ``_create_pooled_connection`` already spawns with the live env.

    **PRECEDENCE (PINNED, do not change without an explicit ticket):**
    row ``config.env`` wins over ``definition.build_config({})`` env on
    every key. Rationale:

    * The DB row is the SOURCE OF TRUTH for runtime env. The
      bootstrap-time ``config`` written by
      ``InstanceManager._bootstrap_builtin_servers`` IS
      ``build_config({})``, but every subsequent ``mcp_set_env`` /
      ``kms_attach`` write merges into the row. A stale definition
      would silently drop those keys.
    * The definition defaults are the FALLBACK for the case where
      the row has no env (e.g. mid-bootstrap before the row is
      written, or a user-wiped row). Falling back to the definition
      matches pre-fix behavior on the "empty / missing" path so
      unrelated servers (e.g. ``plane``) keep working when their row
      has no env entries of its own.
    * On key conflict, the row wins — by the same argument, the row
      is the live state. Operators who want to reset a key must do it
      via the explicit writers (``mcp_set_env`` / ``kms_attach``);
      silently re-introducing a definition default on daemon restart
      would break the ``mcp_set_env`` / ``kms_attach`` self-provisioning
      chain (markers in the row would be replaced by definition
      defaults that have no value).

    Command / args / timeout come from the definition (unchanged at
    runtime — env is the only mutable axis for stdio servers). The
    row's ``config`` may carry user-set ``args`` too in principle,
    but the bootstrap path always regenerates the row's
    ``transport / command / args`` from the definition; the row's
    contributions to pooled state are the env (and any future runtime
    additions). This helper therefore overlays ONLY env, keeping
    command / args / timeout on the definition's source-of-truth
    output.

    Args:
        definition: A built-in server definition.
            Used for ``build_config({})`` and the schema-derived env
            defaults.
        row_config: The live ``mcp_servers.config`` dict from the DB
            (may be ``None`` or empty if the row has never been
            written). When present, ``row_config["env"]`` is overlaid
            on the definition defaults.

    Returns:
        A ``McpStdioConfig`` ready to hand to
        :meth:`McpWarmupPool.register_server`. The transport
        check (``stdio``) is the caller's responsibility — the pool
        only registers stdio servers; non-stdio is filtered out
        upstream in ``_init_warmup_pool``.
    """
    config_dict = definition.build_config({})
    if config_dict.get("transport") != "stdio":
        # Callers should not pass non-stdio definitions here, but be
        # explicit: any non-stdio path is forwarded to the McpStdioConfig
        # constructor (which will fail with a clean pydantic ValidationError
        # on a transport mismatch — better than silent string coercion).
        pass

    row_env: dict[str, str] = {}
    if row_config:
        candidate = row_config.get("env")
        if isinstance(candidate, dict):
            # Filter to string→string — pydantic will ValidationError
            # otherwise. KMS markers are str values and pass through.
            row_env = {k: v for k, v in candidate.items() if isinstance(k, str) and isinstance(v, str)}

    if row_env:
        merged_env: dict[str, str] = {**(config_dict.get("env") or {})}
        merged_env.update(row_env)
        config_dict["env"] = merged_env

    return McpStdioConfig(**config_dict)
