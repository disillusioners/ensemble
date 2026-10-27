"""Task-scoped wrappers for ``mcp`` client context managers.

``mcp.stdio_client``, ``mcp.client.sse.sse_client`` and
``mcp.client.streamable_http.streamablehttp_client`` are all
``@asynccontextmanager`` factories backed by an anyio task group. Anyio
binds each cancel scope to the asyncio task that called ``__aenter__``,
so ``__aexit__`` must be invoked from the *same* task. Closing the
context manager from a different task raises::

    RuntimeError: Attempted to exit cancel scope in a different task
        than it was entered in

The agents-ensemble daemon stores these stream context managers in the
connection manager / warm-up pool and closes them later from a
different task (instance termination, pool health check, replenish,
test-helper disconnect, etc.), which triggers the error above and
surfaces as an unretrieved task exception during async-generator
finalisation.

The wrappers in this module own the inner context manager in a
dedicated background task so the inner ``__aenter__`` and ``__aexit__``
are always called in the same task. The outer ``__aenter__`` /
``__aexit__`` can be invoked from any task — they signal the background
task to start / stop the inner context manager.

Two classes are exposed:

* :class:`TaskScopedContextManager` — generic wrapper around any
  ``@asynccontextmanager`` factory. Used directly for SSE and
  Streamable HTTP transports.
* :class:`TaskScopedStdioClient` — thin convenience wrapper that
  constructs a ``mcp.stdio_client(server_params)`` inside the
  background task. Retained for back-compat with the original fix in
  commit ``c025480``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from concurrent.futures import ThreadPoolExecutor
import os
import threading
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Callable, Optional

import mcp
from mcp import StdioServerParameters

logger = logging.getLogger(__name__)

# ── W-3 (P2 review) — claim-once ledger for MCP child stamps ────────────────
#
# Concurrent same-command MCP sessions scan the SAME /proc children:
# without a claim ledger each session appended BOTH pids to its own
# ``_registered`` list, and the FIRST ``on_close`` marked the
# still-live sibling exited (false history row + the child vanished
# from live attribution). The ledger is keyed ``(pid, start_time)``
# (the PID-recycle-safe pair): the first session to claim a child
# owns its stamp + exit-mark; later sessions skip it entirely.
# Claims are released at ``_mark_children_exit`` so a genuinely new
# child reusing the key later can be claimed again.
_claimed_children: set[tuple[int, int]] = set()
_claimed_children_lock = threading.Lock()

# Type alias for a factory that returns an async context manager.
# We accept any callable to also support lambdas that close over
# transport-specific kwargs (URL, headers, etc.).
AsyncContextManagerFactory = Callable[[], AbstractAsyncContextManager[Any]]


# ── D5 — MCP child stamp (resource-cost-control Phase 2) ────────────────────
#
# The REAL spawn seam for an MCP stdio server is the factory below
# (``TaskScopedStdioClient._run`` calls ``self._factory()`` →
# ``mcp.stdio_client(server_params)`` → ``anyio.open_process(...,
# start_new_session=True)``). The library gives us no PID handle, so
# the stamp is a BEST-EFFORT /proc scan performed RIGHT AFTER the
# streams become ready (the moment the child is guaranteed alive):
#
#   * ppid == our pid (anyio's ``start_new_session=True`` changes
#     the SESSION/pgid, NOT the parent — so the child's ppid is us);
#   * ``/proc/<pid>/cmdline`` argv[0] basename == basename(command)
#     (the configured ``config.command``, e.g. ``npx`` / ``uvx``).
#
# ``instance_id`` is NULLABLE (D11): the warm-up pool spawns
# servers process-wide with NO agent in context, so those rows
# carry ``instance_id=None``. The connection-manager path passes
# the owning instance explicitly.
#
# The proc root is module-level so tests can inject a synthetic
# tree (the 2026-10-12 fence discipline). Production default is
# the real ``/proc``.
_PROC_ROOT: Path = Path("/proc")

# Bounded handshake wait (normative in requirements.md §4.6, P6).
# 30.0 s — strictly inside the 60 s warmup outer budget so the outer
# budget stays authoritative (30 + 2 + 30 + 6 = 68 s <= 60 s is the
# documented split; 55 s is REJECTED).
HANDSHAKE_WAIT_TIMEOUT_S: float = 30.0

# Dedicated executor for the /proc child-stamp scan (architecture §5.1).
# NOT the shared default executor: asyncio.to_thread / run_in_executor(None, ...)
# share the default pool, which carries the daemon's LLM calls and compaction
# (graph.py:1952-1957, compaction.py:4647); because the scan is awaited before
# _ready.set(), a queued scan would stall the handshake behind a 120s LLM call.
# Queue depth: bounded by spawn count, which is fully bounded only by phase-3's
# C1 quarantine (S1). Process-lifetime lazy threads (house precedent); no
# shutdown path is wired here (manager.py is out of the daemon-file scope fence).
_PROC_SCAN_EXECUTOR = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="mcp-procscan"
)


def _basename_command(command: str) -> str:
    """Return the basename of an executable path (portable).

    Defensive: non-string input (e.g. a mock in a test harness)
    yields ``""`` rather than raising — the caller treats an empty
    basename as "no match".
    """
    if not isinstance(command, str) or not command:
        return ""
    return os.path.basename(command)


def _read_cmdline(proc_root: Path, pid: int) -> list[str]:
    """Best-effort ``/proc/<pid>/cmdline`` → argv list.

    The kernel stores argv NUL-separated. Returns ``[]`` on any
    read failure (process gone, permission).
    """
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return []
    if not raw:
        return []
    return [part.decode(errors="replace") for part in raw.split(b"\x00") if part]


def _read_ppid_and_starttime(proc_root: Path, pid: int) -> tuple[Optional[int], Optional[int]]:
    """Return ``(ppid, starttime_jiffies)`` from ``/proc/<pid>/stat``.

    Field 4 = ppid, field 22 = starttime. ``(comm)`` may contain
    spaces/parens — split on the LAST ``)``. Returns ``(None, None)``
    on any failure.
    """
    try:
        raw = (proc_root / str(pid) / "stat").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None, None
    last_paren = raw.rfind(")")
    if last_paren == -1:
        return None, None
    _, _, tail = raw.rpartition(")")
    parts = tail.strip().split()
    # field 4 = parts[1], field 22 = parts[19]
    try:
        return int(parts[1]), int(parts[19])
    except (ValueError, IndexError):
        return None, None


def scan_mcp_child_pids(
    *,
    command: str,
    args: Optional[list[str]] = None,
    proc_root: Optional[Path] = None,
    parent_pid: Optional[int] = None,
) -> list[tuple[int, int]]:
    """Best-effort scan for spawned MCP child pids.

    Returns ``[(pid, starttime_jiffies)]`` for every process whose
    ppid == ``parent_pid`` (default ``os.getpid()``) AND whose
    ``cmdline`` argv[0] basename matches ``command`` AND whose
    ``argv[1:]`` starts with ``args``.

    W-3: ``args`` must be NON-EMPTY. A server configured with
    ``args=[]``/``None`` would otherwise wildcard-match ANY
    same-basename child of the daemon (every ``uvx`` invocation,
    bash-spawned or not) — a basename-only match is too
    wildcard-prone to attribute safely, so an args-less server
    matches NOTHING (skip rather than wildcard). Never raises —
    a missing /proc (macOS/Windows) yields ``[]``.
    """
    root = proc_root if proc_root is not None else _PROC_ROOT
    want_ppid = parent_pid if parent_pid is not None else os.getpid()
    want_comm = _basename_command(command)
    if not want_comm:
        return []
    if not args:
        # W-3 — no disambiguating argv → no match (never wildcard).
        return []
    matches: list[tuple[int, int]] = []
    try:
        entries = list(root.iterdir())
    except (FileNotFoundError, PermissionError, OSError):
        return []
    for entry in entries:
        name = entry.name
        if not name.isdigit():
            continue
        try:
            pid = int(name)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        ppid, starttime = _read_ppid_and_starttime(root, pid)
        if ppid != want_ppid or starttime is None:
            continue
        argv = _read_cmdline(root, pid)
        if not argv:
            continue
        if _basename_command(argv[0]) != want_comm:
            continue
        if argv[1:1 + len(args)] != list(args):
            continue
        matches.append((pid, starttime))
    return matches



def _is_caller_cancelled() -> bool:
    """Return True if the current task is being cancelled.

    ``asyncio.current_task().cancelling()`` is available in Python 3.11+.
    Older versions fall back to a weaker check based on the task's
    ``_must_cancel`` flag (private API, best-effort).
    """
    task = asyncio.current_task()
    if task is None:
        return False
    try:
        return task.cancelling() > 0
    except AttributeError:  # pragma: no cover
        return bool(getattr(task, "_must_cancel", False))


class TaskScopedContextManager:
    """Async context manager that owns another ACM in a dedicated background task.

    The inner context manager's ``__aenter__`` and ``__aexit__`` are
    always called in the same asyncio task (the background task owned
    by this wrapper), avoiding "Attempted to exit cancel scope in a
    different task" errors when the consumer closes the wrapper from a
    different task — which happens routinely in this daemon for pooled
    MCP connections and test-helper sessions.

    The wrapper itself is a normal async context manager and can be
    used with ``async with``::

        async with TaskScopedContextManager(factory) as streams:
            ...

    Or manually::

        cm = TaskScopedContextManager(factory)
        streams = await cm.__aenter__()
        try:
            ...
        finally:
            await cm.__aexit__(None, None, None)

    Args:
        factory: A zero-argument callable returning an async context
            manager. The factory is invoked *inside* the background
            task, so any transport-specific resources are bound to
            that task. Using a factory (rather than a pre-built CM)
            is what makes the wrapper transport-agnostic and matches
            the ``@asynccontextmanager`` pattern used by
            ``mcp.stdio_client``, ``mcp.client.sse.sse_client`` and
            ``mcp.client.streamable_http.streamablehttp_client``.
        name: Optional label used in the background task's name
            (``asyncio.Task.name``) for easier debugging.
    """

    def __init__(
        self,
        factory: AsyncContextManagerFactory,
        name: str = "mcp-client",
        *,
        on_ready: Optional[Callable[[], Optional[Awaitable[Any]]]] = None,
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        """Store the factory; defer work to ``__aenter__``.

        ``on_ready`` / ``on_close`` are optional best-effort hooks
        (D5 — resource-cost-control Phase 2). ``on_ready`` fires in
        the background task RIGHT AFTER the inner streams are ready
        (the moment the spawned child is guaranteed alive).
        ``on_close`` fires AFTER the inner ``__aexit__`` has run.
        Both are exception-swallowed (logged at WARNING) so a hook
        failure can never break the transport lifecycle.
        """
        self._factory = factory
        self._name = name
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Event = asyncio.Event()
        self._close: asyncio.Event = asyncio.Event()
        self._streams: Any = None
        self._error: BaseException | None = None
        self._on_ready = on_ready
        self._on_close = on_close

    async def __aenter__(self) -> Any:
        """Spawn the background task and wait for the streams to be ready.

        Returns:
            Whatever the inner context manager's ``__aenter__``
            returned (typically a 2- or 3-tuple of streams for
            ``mcp`` transports, but the wrapper is transport-agnostic
            and returns it as-is).

        Raises:
            Any failure to obtain streams — factory error, inner
            ``__aenter__`` error, or the caller's own
            ``CancelledError`` if the caller is cancelled while
            waiting for the background task.
        """
        self._task = asyncio.create_task(self._run(), name=self._name)
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=HANDSHAKE_WAIT_TIMEOUT_S)
        except BaseException:
            if self._task is not None and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except BaseException:
                    pass
            raise
        if self._error is not None:
            assert self._task is not None
            try:
                await self._task
            except BaseException:
                pass
            raise self._error
        return self._streams

    @property
    def background_task(self) -> asyncio.Task | None:
        """The task that owns the inner context manager's lifecycle.

        Exposed so a bounded-teardown caller (e.g. the MCP warmup pool's
        idle reap) can hard-cancel a teardown that has blown past its
        grace window. ``None`` before ``__aenter__`` and after
        ``__aexit__`` has drained the task.
        """
        return self._task

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool | None:
        """Signal the background task to close the inner context manager.

        Awaits the background task so the caller knows the transport
        has been torn down by the time this returns. Safe to call from
        any task (including one different from the task that called
        ``__aenter__``), but it must be called *after* ``__aenter__``
        has returned — calling it concurrently with a still-pending
        ``__aenter__`` will return already-closed streams to the
        ``__aenter__`` caller.

        Returns:
            ``False``. The caller's own exception (if any) is not
            suppressed. Exceptions from the inner cleanup are logged
            at ``DEBUG`` and swallowed — the caller can't act on them
            and the connection is going away anyway.
        """
        self._close.set()
        task = self._task
        self._task = None
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                if _is_caller_cancelled():
                    raise
            except Exception:
                pass
        return False

    async def _run(self) -> None:
        """Background task that owns the inner context manager lifecycle.

        ``CancelledError`` is NEVER caught here. The background task
        may be cancelled by ``__aenter__``'s cleanup path (when the
        caller of ``__aenter__`` is cancelled) or by external code
        awaiting the task (e.g. ``__aexit__``'s ``await task`` when
        the caller of ``__aexit__`` is cancelled). Catching
        ``CancelledError`` would silently swallow the cancellation
        and cause the awaiter to see a normal completion instead of
        ``CancelledError`` — breaking cancellation propagation
        through the wrapper.
        """
        try:
            streams_cm = self._factory()
        except BaseException as e:
            self._error = e
            self._ready.set()
            return
        try:
            try:
                self._streams = await streams_cm.__aenter__()
            except asyncio.CancelledError:
                # G1 guarantee: a cancellation that arrives while the
                # inner transport is still entering must NOT skip the
                # inner __aexit__ (which installs the SDK's process-group
                # shutdown: stdin close -> killpg SIGTERM -> SIGKILL).
                # Historically the re-raise at this point happened BEFORE
                # the finally: block below could install __aexit__, so the
                # spawned child survived. Run __aexit__ explicitly here.
                try:
                    await streams_cm.__aexit__(None, None, None)
                except BaseException:
                    pass
                raise
            except BaseException as e:
                self._error = e
                self._ready.set()
                return
            # D5 — best-effort on_ready hook (MCP child stamp). Runs
            # in the background task, right after the child is
            # guaranteed alive AND BEFORE ``_ready.set()`` so the
            # stamp is guaranteed complete by the time the
            # ``__aenter__`` waiter resumes (deterministic ordering
            # — no dependence on the event-loop's wake schedule).
            # Exceptions are swallowed: a hook failure must never
            # break the transport.
            if self._on_ready is not None:
                try:
                    result = self._on_ready()
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:
                    logger.warning(
                        "TaskScopedContextManager on_ready hook failed: "
                        "%s: %s", type(exc).__name__, exc,
                    )
            self._ready.set()
            try:
                await self._close.wait()
            finally:
                try:
                    await streams_cm.__aexit__(None, None, None)
                except asyncio.CancelledError:
                    raise
                except BaseException as e:
                    logger.debug("Inner context manager __aexit__ raised: %s", e)
                # D5 — best-effort on_close hook (MCP child exit-mark).
                # Runs AFTER the inner __aexit__ has torn down the
                # transport (the child is gone or going). Exceptions
                # swallowed as above.
                if self._on_close is not None:
                    try:
                        self._on_close()
                    except Exception as exc:
                        logger.warning(
                            "TaskScopedContextManager on_close hook failed: "
                            "%s: %s", type(exc).__name__, exc,
                        )
        except BaseException as e:
            if not self._ready.is_set():
                self._error = e
                self._ready.set()
            raise


class TaskScopedStdioClient(TaskScopedContextManager):
    """Task-scoped wrapper around ``mcp.stdio_client(server_params)``.

    Thin convenience subclass that constructs the stdio CM lazily
    inside the background task. Retained for back-compat with the
    original fix (commit ``c025480``) so existing call sites and
    tests that patch ``daemon.mcp.stdio_wrapper.mcp.stdio_client``
    keep working unchanged.

    D5 — resource-cost-control Phase 2: this is also the MCP child
    STAMP seam. After the streams are ready (the child is alive) we
    best-effort scan /proc for the spawned pid and register it in
    :mod:`daemon.services.resource_monitor.pid_registry` with
    ``kind='mcp_child'``; on close we mark the same pids exited.
    ``instance_id`` is NULLABLE (D11) — the warm-up pool spawns
    servers process-wide with no agent in context, so those rows
    carry ``instance_id=None``.
    """

    def __init__(
        self,
        server_params: StdioServerParameters,
        *,
        instance_id: Optional[str] = None,
        server_name: Optional[str] = None,
    ) -> None:
        """Store the server parameters; the factory is built on demand.

        Args:
            server_params: The stdio server parameters (command /
                args / env) the inner ``mcp.stdio_client`` uses.
            instance_id: Owning instance (None for pool warmups —
                D11 NULLABLE).
            server_name: Registry server name (goes into ``detail``
                for forensics; None when the caller does not know).
        """
        self._server_params = server_params
        self._instance_id = instance_id
        self._server_name = server_name
        # PIDs registered this session, paired with their
        # starttime jiffies so the exit-mark can pass the same
        # (pid, start_time) key the register used (PID-recycle
        # defense at mark_exit).
        self._registered: list[tuple[int, int]] = []
        super().__init__(
            factory=lambda: mcp.stdio_client(server_params),
            name="mcp-stdio-client",
            on_ready=self._schedule_stamp,
            on_close=self._mark_children_exit,
        )

    @property
    def registered_child_pids(self) -> list[tuple[int, int]]:
        """The ``[(pid, start_time)]`` pairs registered this session."""
        return list(self._registered)

    def _stamp_children(self) -> None:
        """Best-effort /proc scan → register every spawned child.

        Never raises (the ``on_ready`` hook swallows anyway). A
        missing /proc or no match is a silent no-op.
        """
        try:
            from daemon.services.resource_monitor.pid_registry import (
                KIND_MCP_CHILD,
                get_pid_registry,
            )
        except Exception:  # pragma: no cover — import is cheap + stable
            return
        matches = scan_mcp_child_pids(
            command=self._server_params.command,
            args=list(self._server_params.args or []),
        )
        if not matches:
            return
        registry = get_pid_registry()
        for pid, starttime in matches:
            # W-3 claim-once: check-and-claim ATOMICALLY under the
            # ledger lock — two concurrent same-command sessions
            # cannot both win the same (pid, start_time), so only
            # the claiming session appends to ``_registered`` and
            # only its ``on_close`` exit-marks the child.
            with _claimed_children_lock:
                if (pid, starttime) in _claimed_children:
                    continue
                _claimed_children.add((pid, starttime))
            try:
                registry.register(
                    pid=pid,
                    start_time=starttime,
                    kind=KIND_MCP_CHILD,
                    instance_id=self._instance_id,
                    command=_basename_command(self._server_params.command),
                    detail=(
                        f"server={self._server_name}"
                        if self._server_name
                        else "server=<pool>"
                    ),
                    parent_pid=os.getpid(),
                )
                self._registered.append((pid, starttime))
            except Exception as exc:
                # Release the failed claim so a later session can
                # stamp the child (the registry row never landed).
                with _claimed_children_lock:
                    _claimed_children.discard((pid, starttime))
                logger.warning(
                    "mcp_child stamp: register(pid=%d) failed: %s: %s",
                    pid, type(exc).__name__, exc,
                )

    def _schedule_stamp(self):
        # W-3: perform the /proc scan OFF-loop on the DEDICATED executor
        # and return the awaitable so the transport awaits it BEFORE
        # _ready.set() — work off-loop, await on-loop. Registration is
        # therefore complete before __aenter__ returns (required by
        # tests/test_resource_pid_registry.py:505-510). A fire-and-forget
        # shape is UNSAFE (a deferred scan can find a dead child and
        # return [] with no reap handle).
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        return loop.run_in_executor(_PROC_SCAN_EXECUTOR, self._stamp_children)

    def _mark_children_exit(self) -> None:
        """Mark every child registered this session as exited.

        Best-effort; the exit kind is ``EXIT_KIND_EXIT`` (the
        session teardown closed the child cleanly; we cannot read
        its real exit code — the mcp transport does not expose it).
        """
        if not self._registered:
            return
        try:
            from daemon.services.resource_monitor.pid_registry import (
                EXIT_KIND_EXIT,
                get_pid_registry,
            )
        except Exception:  # pragma: no cover
            return
        registry = get_pid_registry()
        for pid, starttime in self._registered:
            try:
                registry.mark_exit(
                    pid=pid,
                    start_time=starttime,
                    exit_code=None,
                    exit_kind=EXIT_KIND_EXIT,
                )
            except Exception as exc:
                logger.warning(
                    "mcp_child stamp: mark_exit(pid=%d) failed: %s: %s",
                    pid, type(exc).__name__, exc,
                )
        # W-3 — release the claims BEFORE clearing ``_registered``
        # (the keys being released ARE the session's registered
        # pairs) so a child spawned later under the same
        # (pid, start_time) key can be claimed again.
        with _claimed_children_lock:
            for key in self._registered:
                _claimed_children.discard(key)
        self._registered.clear()
