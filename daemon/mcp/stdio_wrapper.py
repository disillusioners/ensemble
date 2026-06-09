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
import logging
from contextlib import AbstractAsyncContextManager
from typing import Any, Callable

import mcp
from mcp import StdioServerParameters

logger = logging.getLogger(__name__)

# Type alias for a factory that returns an async context manager.
# We accept any callable to also support lambdas that close over
# transport-specific kwargs (URL, headers, etc.).
AsyncContextManagerFactory = Callable[[], AbstractAsyncContextManager[Any]]


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
    ) -> None:
        """Store the factory; defer work to ``__aenter__``."""
        self._factory = factory
        self._name = name
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Event = asyncio.Event()
        self._close: asyncio.Event = asyncio.Event()
        self._streams: Any = None
        self._error: BaseException | None = None

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
            await self._ready.wait()
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
                raise
            except BaseException as e:
                self._error = e
                self._ready.set()
                return
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
    """

    def __init__(self, server_params: StdioServerParameters) -> None:
        """Store the server parameters; the factory is built on demand."""
        self._server_params = server_params
        super().__init__(
            factory=lambda: mcp.stdio_client(server_params),
            name="mcp-stdio-client",
        )
