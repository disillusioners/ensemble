"""Managed MCP client session with proper lifecycle handling."""

from __future__ import annotations

import anyio
from mcp import ClientSession


class ManagedClientSession(ClientSession):
    """
    Extended ClientSession that properly manages its task group lifecycle.

    The base ClientSession requires being used as an async context manager to start
    its receive loop. This subclass allows manual lifecycle management so sessions
    can be returned to callers while keeping the receive loop running.
    """

    async def start(self) -> None:
        """Start the session's receive loop. Must be called before use."""
        if hasattr(self, "_task_group") and self._task_group is not None:
            return  # Already started
        self._task_group = anyio.create_task_group()
        await self._task_group.__aenter__()
        self._task_group.start_soon(self._receive_loop)

    async def stop(self) -> None:
        """Stop the session's receive loop and clean up resources."""
        if not hasattr(self, "_task_group") or self._task_group is None:
            return
        try:
            await self._exit_stack.aclose()
        except Exception:
            pass
        self._task_group.cancel_scope.cancel()
        try:
            await self._task_group.__aexit__(None, None, None)
        except Exception:
            pass
        self._task_group = None
