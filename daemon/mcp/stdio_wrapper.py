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
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

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
# 30.0 s — the handshake bound is INSIDE the 60 s per-spawn warmup
# budget. The documented worst-case split is 30 (handshake) + 2
# (npx grace) + 30 (3 initialize retries) + 6 (backoff) = 68 s, i.e.
# 68 s > 60 s, so the OUTER (per-spawn) budget wins and cancels the
# 3rd initialize retry — the inner bound is a backstop for the
# handshake itself, not a widening of the outer budget. The 55 s
# value is REJECTED (it pushes the split further past the outer
# budget).
HANDSHAKE_WAIT_TIMEOUT_S: float = 30.0

#: Grace window the close-watchdog gives the background task to run the
#: inner transport's own ``__aexit__`` (the SDK's process-group shutdown)
#: before the task is hard-cancelled and orphan children are reaped.
CLOSE_GRACE_S: float = 10.0

#: Bound on the inner transport's teardown when it runs on a CANCELLED
#: path. The SDK's own shutdown sequence (stdin close -> wait 2 s ->
#: SIGTERM -> SIGKILL) can take a few seconds, but an inner
#: ``__aexit__`` that never returns must NOT wedge the cancelling
#: caller — so the teardown is bounded and the deterministic child reap
#: (``_reap_orphans``) is the backstop.
INNER_EXIT_GRACE_S: float = 5.0

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
        self._watchdog: asyncio.Task | None = None

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
            if self._error is not None:
                assert self._task is not None
                try:
                    await self._task
                except BaseException:
                    pass
                raise self._error
            return self._streams
        except BaseException:
            # G1 (full note in ``_run``): the SDK's process-group shutdown
            # lives in the async generator's ``finally`` AFTER the
            # ``yield`` — cancelling the task that awaits ``__aenter__``
            # leaves that generator SUSPENDED and the spawned child
            # ORPHANED.
            #
            # This block covers EVERY failure path of ``__aenter__`` —
            # including a cancellation delivered in the window AFTER
            # ``_ready.wait()`` returned but BEFORE the streams were
            # handed to the caller (which used to skip the cleanup
            # entirely and leave the transport open forever).
            #
            # The two cases are handled differently:
            #
            # * the inner transport is ALREADY ENTERED (``_ready`` set):
            #   signal close and let the background task run the SDK's own
            #   ``__aexit__`` — that is what actually reaps the child.
            # * the inner transport is STILL ENTERING: the SDK generator
            #   is suspended and its teardown can never run, so we cancel
            #   the background task and reap the spawned child EXPLICITLY
            #   (the STDIO subclass's /proc census + process-group kill).
            if self._ready.is_set():
                self._signal_close()
            else:
                task = self._task
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await task
                    except BaseException as exc:
                        logger.debug(
                            "TaskScopedContextManager background task did not "
                            "unwind cleanly: %s: %s",
                            type(exc).__name__, exc,
                        )
                self._reap_orphans()
            raise

    def request_close(self) -> None:
        """Ask the transport to close — SYNCHRONOUS, never awaits.

        This is the cancellation-safe close request. Under a
        cancellation, ``await cm.__aexit__(...)`` never even enters the
        coroutine (the cancellation is delivered AT the await point), so
        the transport would never learn that it must close — and the
        spawned child would survive. This method therefore sets the
        close event (and arms the watchdog) with NO await, so it works
        from a cancelled task.

        Safe to call from any task, any number of times.
        """
        self._signal_close()

    def _signal_close(self) -> None:
        """Ask the background task to close the inner transport.

        The background task owns the inner context manager, so the ONLY
        way to run the SDK's process-group shutdown is to let that task
        reach the inner ``__aexit__``. This method therefore sets the
        close event and arms a bounded watchdog; it never cancels the
        background task (a cancelled inner ``__aenter__`` orphans the
        spawned child — see the G1 note in ``_run``).
        """
        self._close.set()
        task = self._task
        if task is None or task.done() or self._watchdog is not None:
            return

        async def _run_close_watchdog() -> None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(task), timeout=CLOSE_GRACE_S
                )
            except asyncio.TimeoutError:
                # The inner transport wedged: hard-cancel the task and
                # reap any orphan child it spawned.
                task.cancel()
                try:
                    await task
                except BaseException as exc:
                    logger.debug(
                        "TaskScopedContextManager close-watchdog: the inner "
                        "task did not unwind cleanly: %s: %s",
                        type(exc).__name__, exc,
                    )
                try:
                    # SYNCHRONOUS by design (the method is a plain
                    # function on purpose — it runs from ``finally``
                    # blocks, where an await is not possible). The
                    # ``await`` here was a one-token bug: it raised a
                    # TypeError on EVERY close-watchdog activation and
                    # the spurious WARNING below fired every time.
                    self._reap_orphans()
                except Exception as exc:  # pragma: no cover — best effort
                    logger.warning(
                        "TaskScopedContextManager orphan reap failed: %s: %s",
                        type(exc).__name__, exc,
                    )
            except BaseException as exc:
                logger.debug(
                    "TaskScopedContextManager close-watchdog exited with: "
                    "%s: %s",
                    type(exc).__name__, exc,
                )

        self._watchdog = asyncio.create_task(
            _run_close_watchdog(), name=f"{self._name}-close-watchdog"
        )

    def _reap_orphans(self) -> None:
        """Reap child processes orphaned by a cancelled inner transport.

        Transport-agnostic default: nothing to reap. The STDIO subclass
        overrides this with a /proc census of the spawned children.

        SYNCHRONOUS on purpose: it is called from ``finally`` blocks
        that may run UNDER a cancellation, and a sync call cannot be
        interrupted by one.
        """
        return None

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
        # NOTE-3: signal close (not a bare ``_close.set()``) so the
        # CLOSE_GRACE_S watchdog is armed — the normal path's
        # ``await task`` below is unbounded, and without the watchdog a
        # wedged inner ``__aexit__`` would leave the reap backstop
        # unreached.
        self._signal_close()
        task = self._task
        self._task = None
        try:
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    if _is_caller_cancelled():
                        raise
                except Exception:
                    pass
        finally:
            # B3 backstop (synchronous, so a cancellation cannot skip it):
            # the SDK's own teardown may not have completed — or may have
            # been interrupted — so census /proc and kill whatever this
            # session still owns.
            try:
                self._reap_orphans()
            except Exception as exc:  # pragma: no cover — best effort
                logger.debug("mcp_child reap failed: %s: %s", type(exc).__name__, exc)
        return False

    async def _run(self) -> None:
        """Background task that owns the inner context manager lifecycle.

        The cancellation is PROPAGATED, never swallowed: the background
        task may be cancelled by ``__aenter__``'s cleanup path (when
        the caller of ``__aenter__`` is cancelled) or by external code
        awaiting the task (e.g. ``__aexit__``'s ``await task`` when
        the caller of ``__aexit__`` is cancelled). Swallowing
        ``CancelledError`` would make the awaiter see a normal
        completion instead of ``CancelledError`` — breaking
        cancellation propagation through the wrapper.

        The teardown (``__aexit__``) and the child reap DO run on the
        cancelled path — they are installed in ``finally`` blocks and
        are bounded (``INNER_EXIT_GRACE_S``) / synchronous, so a
        cancellation cannot skip them and cannot wedge the canceller.
        """
        try:
            streams_cm = self._factory()
        except BaseException as e:
            self._error = e
            self._ready.set()
            return
        # G1 (verified against the PINNED mcp==1.27.1): the inner
        # transport's teardown — the SDK's process-group shutdown (stdin
        # close -> wait -> SIGTERM -> SIGKILL) — lives in the async
        # generator's ``finally`` AFTER the ``yield``. So the teardown
        # only runs if the inner ``__aexit__`` is actually reached.
        #
        # The teardown is therefore installed in a ``finally`` that
        # covers the WHOLE post-``__aenter__`` region (the on_ready hook
        # included). A cancellation anywhere in that region — including
        # during the off-loop /proc stamp — would otherwise re-raise
        # BEFORE the teardown was installed and the spawned child would
        # be ORPHANED (measured against the pinned SDK: the child stays
        # in /proc after the cancel).
        try:
            try:
                self._streams = await streams_cm.__aenter__()
            except asyncio.CancelledError:
                # G1 (see the full note just above): a cancelled enter
                # never reaches the SDK's ``finally`` teardown, so the
                # spawned child would be ORPHANED without the explicit
                # exit below.
                #
                # Run the teardown explicitly here. It is best-effort: an
                # @asynccontextmanager whose __aenter__ was interrupted
                # can refuse __aexit__ ("generator didn't yield") — that
                # must not mask the cancellation, and the explicit
                # child reap (below) is the deterministic backstop.
                try:
                    await asyncio.wait_for(
                        streams_cm.__aexit__(None, None, None),
                        timeout=INNER_EXIT_GRACE_S,
                    )
                except BaseException as exc:
                    logger.debug(
                        "Inner context manager __aexit__ raised: %s: %s",
                        type(exc).__name__, exc,
                    )
                raise
            except BaseException as e:
                # B3: the inner transport failed to enter. Run its
                # __aexit__ defensively so a partially-spawned child is
                # torn down (the SDK's own generator may or may not have
                # unwound). Guarded: an @asynccontextmanager whose
                # __aenter__ raised can refuse __aexit__ ("generator
                # didn't yield") — that must not mask the real error.
                try:
                    await streams_cm.__aexit__(None, None, None)
                except BaseException as exc:
                    logger.debug(
                        "Inner context manager __aexit__ raised: %s: %s",
                        type(exc).__name__, exc,
                    )
                self._error = e
                self._ready.set()
                return
            try:
                # D5 — best-effort on_ready hook (MCP child stamp). Runs
                # in the background task, right after the child is
                # guaranteed alive AND BEFORE ``_ready.set()`` so the
                # stamp is guaranteed complete by the time the
                # ``__aenter__`` waiter resumes (deterministic ordering
                # — no dependence on the event-loop's wake schedule).
                # Exceptions are swallowed: a hook failure must never
                # break the transport.
                #
                # It sits INSIDE this try so the teardown below is
                # installed before the hook runs — a cancellation during
                # the (off-loop) stamp must still reach the SDK's
                # process-group shutdown.
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
                await self._close.wait()
            finally:
                # NOTE-4: the cancellation is captured and re-raised
                # AFTER the teardown and the reap — a bare ``raise`` in
                # an ``except CancelledError`` arm would skip everything
                # that follows it.
                cancel_exc: BaseException | None = None
                try:
                    await streams_cm.__aexit__(None, None, None)
                except asyncio.CancelledError as exc:
                    cancel_exc = exc
                except BaseException as e:
                    logger.debug("Inner context manager __aexit__ raised: %s", e)
                # B3 backstop: the SDK's own teardown is not reliable on
                # every path (its killpg lives after the yield), so the
                # session's children are censused and reaped explicitly.
                # It is SYNCHRONOUS, so a cancellation cannot skip it.
                try:
                    self._reap_orphans()
                except Exception as exc:  # pragma: no cover — best effort
                    logger.debug("mcp_child reap failed: %s: %s", type(exc).__name__, exc)
                # The cancellation (if any) propagates only AFTER the
                # teardown and the reap have run.
                if cancel_exc is not None:
                    raise cancel_exc
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
        # PIDs of matching children observed BEFORE the transport was
        # entered — the baseline the orphan reap diffs against, so a
        # reap never touches a child that predates this session.
        self._preexisting_children: set[int] = set()
        super().__init__(
            factory=self._make_factory(server_params),
            name="mcp-stdio-client",
            on_ready=self._schedule_stamp,
            on_close=self._mark_children_exit,
        )

    def _make_factory(self, server_params):
        """Factory that snapshots the pre-existing children, then spawns.

        The snapshot runs immediately before the inner transport is
        created so the orphan reap has a precise baseline.
        """

        def _factory():
            self._snapshot_preexisting()
            return mcp.stdio_client(server_params)

        return _factory

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
        # NOTE-9: ``get_running_loop`` only — the old
        # ``new_event_loop()`` fallback could hand the executor a
        # FOREIGN loop (a latent cross-loop trap) and was unreachable
        # from the real call path anyway (the hook always runs inside a
        # running loop).
        loop = asyncio.get_running_loop()
        return loop.run_in_executor(_PROC_SCAN_EXECUTOR, self._stamp_children)

    def _matching_children(self) -> list[tuple[int, int]]:
        """The children of THIS process that match this server EXACTLY.

        ONE matcher, used by BOTH the baseline snapshot and the reap
        candidate set — so the baseline is always a superset of the reap
        set and the two can never diverge.

        The predicate is the exact-argv one (``scan_mcp_child_pids``):
        a direct child of this process whose argv[0] basename equals the
        server command AND whose argv[1:] starts with the server args.
        A concurrent sibling that uses the SAME command basename with
        DIFFERENT args (the common case — two npx-based MCP servers)
        therefore does NOT match, so it can never be reaped by a failing
        session.

        A server configured with ``args=[]`` matches NOTHING here — the
        same deliberate W-3 rule the child STAMP uses (a basename-only
        match would wildcard-match any same-basename child of the
        daemon). Such a server is therefore never a reap candidate
        either: ambiguity is resolved in favour of NOT killing.
        """
        try:
            return scan_mcp_child_pids(
                command=self._server_params.command,
                args=list(self._server_params.args or []),
            )
        except Exception:  # pragma: no cover — the scan never raises
            return []

    def _snapshot_preexisting(self) -> None:
        """Record the matching children that existed BEFORE this session.

        Called right before the inner transport is entered. The orphan
        reap diffs against this baseline so it can only ever kill a
        child this session spawned. The baseline uses the SAME matcher
        as the reap candidate set (``_matching_children``).
        """
        self._preexisting_children = {
            pid for pid, _ in self._matching_children()
        }

    def _session_children(self) -> list[int]:
        """The pids of children THIS session spawned (and still owns).

        A child belongs to this session when it

        * matches this server EXACTLY (same matcher as the baseline),
        * was NOT present in the baseline snapshot taken before the
          transport was created (so a pre-existing sibling is never
          touched), and
        * no other live session has claimed it (the claim-once ledger).

        A claim made by THIS session is in ``self._registered`` and is
        exactly the child we may reap.
        """
        found: list[int] = []
        for pid, starttime in self._matching_children():
            if pid in self._preexisting_children:
                continue
            if (
                (pid, starttime) in _claimed_children
                and (pid, starttime) not in self._registered
            ):
                # Claimed by ANOTHER live session — never touch it.
                continue
            found.append(pid)
        return found

    def _reap_orphans(self) -> None:
        """Kill the children THIS session spawned but could not close.

        The SDK's process-group shutdown lives in its async generator's
        ``finally`` AFTER the ``yield``; when the transport is
        interrupted before that point the generator is left suspended
        and the child survives (verified against mcp==1.27.1). This
        reap is the deterministic backstop: it censuses /proc for the
        children this session owns and kills their process groups.
        """
        import os as _os
        import signal as _signal

        for pid in self._session_children():
            try:
                _os.killpg(_os.getpgid(pid), _signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    _os.kill(pid, _signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    continue
            logger.warning(
                "mcp_child reap: killed orphaned child pid=%d", pid,
            )

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
