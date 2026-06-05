"""Managed MCP client session with proper lifecycle handling."""

from __future__ import annotations

import asyncio
import logging

from mcp import ClientSession

logger = logging.getLogger(__name__)


class ManagedClientSession(ClientSession):
    """
    Extended ClientSession with cross-task-safe lifecycle management.

    The base ``ClientSession`` uses an anyio task group for its receive
    loop, which binds the cancel scope to the task that called
    ``__aenter__``. When ``__aexit__`` runs in a different task, anyio
    raises ``RuntimeError: Attempted to exit cancel scope in a different
    task than it was entered in``.

    This subclass exposes explicit ``start()`` / ``stop()`` methods and
    runs the receive loop in a regular ``asyncio.Task`` instead of an
    anyio task group. ``asyncio.Task`` can be cancelled from any task,
    so ``start()`` and ``stop()`` may run in different tasks safely.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._receive_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the session's receive loop. Must be called before use."""
        if self._receive_task is not None and not self._receive_task.done():
            return
        self._receive_task = asyncio.create_task(
            self._receive_loop(), name="mcp-managed-session-rx"
        )

    async def stop(self) -> None:
        """Stop the session's receive loop and clean up resources.

        Safe to call from any asyncio task. The receive task is cancelled
        and awaited here so the caller knows cleanup has finished.

        Note: ``_exit_stack`` is initialised by ``BaseSession.__init__``
        (not ``__aenter__``), so calling ``aclose()`` here is always safe
        even though the parent ``__aenter__`` is never invoked.
        """
        task = self._receive_task
        self._receive_task = None
        if task is None or task.done():
            return
        try:
            # Closing the exit stack before cancelling the receive task mirrors
            # the order in ``BaseSession.__aexit__``. The receive task holds
            # the streams that are registered with the exit stack; closing the
            # stack first gives those resources a chance to unwind cleanly
            # before we cancel the task's loop.
            await self._exit_stack.aclose()
        except Exception as e:
            logger.debug(f"Error closing exit stack during stop: {e}")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # If the *current* task is being cancelled, propagate that rather
            # than silently swallowing it.  ``asyncio.current_task().cancelling()``
            # is available in Python 3.11+; use the simpler form on older
            # versions.
            try:
                cancelling = asyncio.current_task().cancelling()
            except AttributeError:  # pragma: no cover
                cancelling = self._receive_task is not None and self._receive_task.done()
            if cancelling:
                raise
        except Exception:
            pass
