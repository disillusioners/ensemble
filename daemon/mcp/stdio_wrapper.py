"""Task-scoped wrapper for ``mcp.stdio_client``.

``mcp.stdio_client`` is an ``@asynccontextmanager`` backed by an anyio task
group. Anyio's task group binds its cancel scope to the asyncio task that
called ``__aenter__``, so ``__aexit__`` must be invoked from the same task.
Calling it from a different task raises::

    RuntimeError: Attempted to exit cancel scope in a different task
        than it was entered in

The agents-ensemble daemon stores ``stdio_client`` instances in the
connection manager and warm-up pool and closes them later from a different
task (instance termination, pool health check, replenish, etc.), which
triggers the error above and surfaces as an unretrieved task exception
during async-generator finalisation.

This wrapper owns the inner ``stdio_client`` in a dedicated background task
so the inner ``__aenter__`` and ``__aexit__`` are always called in the same
task. The outer ``__aenter__`` / ``__aexit__`` can be invoked from any task
— they signal the background task to start / stop the inner context manager.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import mcp
from mcp import StdioServerParameters

logger = logging.getLogger(__name__)


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


class TaskScopedStdioClient:
    """Async context manager that wraps ``mcp.stdio_client`` in a dedicated task.

    The inner ``stdio_client``'s ``__aenter__`` and ``__aexit__`` are always
    called in the same asyncio task (the background task owned by this
    wrapper), avoiding "Attempted to exit cancel scope in a different task"
    errors when the consumer closes the wrapper from a different task.

    The wrapper itself is a normal async context manager and can be used
    with ``async with``::

        async with TaskScopedStdioClient(server_params) as (read, write):
            session = ManagedClientSession(read, write)
            await session.start()
            ...

    Or manually::

        cm = TaskScopedStdioClient(server_params)
        read, write = await cm.__aenter__()
        try:
            ...
        finally:
            await cm.__aexit__(None, None, None)
    """

    def __init__(self, server_params: StdioServerParameters) -> None:
        """Store parameters; defer work to ``__aenter__``."""
        self._server_params = server_params
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Event = asyncio.Event()
        self._close: asyncio.Event = asyncio.Event()
        self._read_stream: Any = None
        self._write_stream: Any = None
        self._error: BaseException | None = None

    async def __aenter__(self) -> tuple[Any, Any]:
        """Spawn the background task and wait for the streams to be ready.

        Returns:
            ``(read_stream, write_stream)`` from the inner ``stdio_client``.

        Raises:
            Any failure to obtain streams — factory error (e.g. ``FileNotFoundError``),
            inner ``__aenter__`` error, or the caller's own ``CancelledError`` if
            the caller is cancelled while waiting for the background task.
        """
        self._task = asyncio.create_task(self._run(), name="mcp-stdio-client")
        try:
            await self._ready.wait()
        except BaseException:
            if self._task is not None and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except BaseException:
                    pass
            # Re-raise so the caller's ``CancelledError`` (or any other
            # BaseException) propagates. We only swallow cleanup errors
            # *inside* the inner ``await task`` above; the original
            # exception is preserved.
            raise
        if self._error is not None:
            assert self._task is not None
            try:
                await self._task
            except BaseException:
                pass
            raise self._error
        return self._read_stream, self._write_stream

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool | None:
        """Signal the background task to close the inner ``stdio_client``.

        Awaits the background task so the caller knows the subprocess has
        been torn down by the time this returns. Safe to call from any task
        (including one different from the one that called ``__aenter__``),
        but it must be called *after* ``__aenter__`` has returned — calling
        it concurrently with a still-pending ``__aenter__`` will return
        already-closed streams to the ``__aenter__`` caller.

        Returns:
            ``False``. The caller's own exception (if any) is not suppressed.
            Exceptions from the inner cleanup are logged at ``DEBUG`` and
            swallowed — the caller can't act on them and the connection is
            going away anyway.
        """
        self._close.set()
        task = self._task
        self._task = None
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                # If the *caller* of __aexit__ is being cancelled, propagate
                # that so cancellation isn't silently swallowed. Otherwise
                # the cancellation is from the inner background task (we
                # cancelled it via ``task.cancel()`` if it was still
                # running) and is expected.
                if _is_caller_cancelled():
                    raise
            except Exception:
                # Inner stdio_client may raise during cleanup (e.g.
                # BrokenPipeError when the process already exited). Swallow
                # because the caller can't act on it and we've already
                # reported the error during connect if there was one.
                pass
        return False

    async def _run(self) -> None:
        """Background task that owns the inner ``stdio_client`` lifecycle.

        ``CancelledError`` is NEVER caught here. The background task may
        be cancelled by ``__aenter__``'s cleanup path (when the caller of
        ``__aenter__`` is cancelled) or by external code awaiting the task
        (e.g. ``__aexit__``'s ``await task`` when the caller of
        ``__aexit__`` is cancelled). Catching ``CancelledError`` would
        silently swallow the cancellation and cause the awaiter to see a
        normal completion instead of ``CancelledError`` — breaking
        cancellation propagation through the wrapper.
        """
        try:
            streams_cm = mcp.stdio_client(self._server_params)
        except BaseException as e:
            # The factory itself can raise (e.g. ``FileNotFoundError`` if the
            # subprocess command doesn't exist). Surface it to a waiter on
            # ``__aenter__`` so the caller sees the original error.
            self._error = e
            self._ready.set()
            return
        try:
            try:
                self._read_stream, self._write_stream = await streams_cm.__aenter__()
            except asyncio.CancelledError:
                # Let cancellation propagate so awaiters see it.
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
                    logger.debug("Inner stdio_client __aexit__ raised: %s", e)
        except BaseException as e:
            # If _close.wait() itself is cancelled or the finally block
            # raised, surface it to a waiter on __aenter__ so the caller
            # sees a meaningful error.
            if not self._ready.is_set():
                self._error = e
                self._ready.set()
            # Re-raise so that any code awaiting this background task
            # (specifically ``__aexit__``'s ``await task``) sees the error
            # and can decide what to do — ``__aexit__`` swallows it.
            raise
