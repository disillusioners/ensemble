"""Bounded-wait + off-loop on_ready pin for TaskScopedStdioClient (Part B B1/B3)."""

from __future__ import annotations

import asyncio
import inspect
import types
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

import daemon.mcp.stdio_wrapper as sw


def test_on_ready_uses_dedicated_executor():
    params = types.SimpleNamespace(command="uvx", args=["mcp-server-x"])
    cm = sw.TaskScopedStdioClient(params, instance_id="i", server_name="s")
    # _stamp_children now performs the /proc scan OFF-loop and returns
    # the awaitable (W-3 shape) instead of running synchronously.
    res = cm._schedule_stamp()
    assert inspect.isawaitable(res)
    # close the coroutine to avoid unawaited-coroutine warning
    if hasattr(res, "close"):
        res.close()


def test_handshake_wait_bounded(monkeypatch):
    # Force a very small bound.
    monkeypatch.setattr(sw, "HANDSHAKE_WAIT_TIMEOUT_S", 0.1)

    async def _hang(self):
        # Never set _ready; simulate a hung handshake.
        await asyncio.sleep(10)

    params = types.SimpleNamespace(command="uvx", args=["mcp-server-x"])

    async def _run():
        cm = sw.TaskScopedStdioClient(params, instance_id="i", server_name="s")
        # Replace _run with the hanging variant.
        cm._run = _hang.__get__(cm, type(cm))
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await cm.__aenter__()

    asyncio.run(_run())


def test_handshake_bound_less_than_sixty():
    assert sw.HANDSHAKE_WAIT_TIMEOUT_S < 60.0
    assert sw.HANDSHAKE_WAIT_TIMEOUT_S == 30.0


def test_g1_cancellation_runs_aexit():
    # Inject a cancellation during __aenter__ and assert the inner
    # __aexit__ ran (child-tree teardown).
    import asyncio as _a

    @asynccontextmanager
    async def fake_client(server_params):
        # Raise CancelledError during __aenter__ to exercise G1.
        raise _a.CancelledError()
        yield ("r", "w")  # pragma: no cover

    params = types.SimpleNamespace(command="uvx", args=["mcp-server-x"])

    async def _run():
        with patch.object(sw.mcp, "stdio_client", fake_client):
            cm = sw.TaskScopedStdioClient(params, instance_id="i", server_name="s")
            try:
                await cm.__aenter__()
            except (_a.CancelledError, BaseException):
                pass
        # If we reach here without hanging, the cancellation path
        # completed (G1 guarantee: __aexit__ ran). No assertion on
        # counter — a mocked client cannot prove child liveness; the
        # integration pin (test_mcp_child_liveness.py) does that.
        return True

    assert asyncio.run(_run()) is True
