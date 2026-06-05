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
        """
        task = self._receive_task
        self._receive_task = None
        if task is None or task.done():
            return
        try:
            await self._exit_stack.aclose()
        except Exception as e:
            logger.debug(f"Error closing exit stack during stop: {e}")
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
