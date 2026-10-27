"""Bounded-wait + off-loop on_ready + G1 teardown pins for the stdio wrapper.

Part B (B1 / B3) of soak-gate-mcp-warmup.

PINS
----
(a) the ``on_ready`` hook performs the /proc scan OFF-loop on the
    DEDICATED executor and returns the awaitable (the W-3 shape);
(b) the transport's handshake wait is BOUNDED (``HANDSHAKE_WAIT_TIMEOUT_S``,
    the arch-pinned 30.0 s — the 55 s value is REJECTED) and is strictly
    inside the 60 s warmup outer budget;
(c) the G1 teardown guarantee: a cancellation delivered while the inner
    transport is still entering STILL runs the inner ``__aexit__`` — the
    SDK's process-group shutdown. The inner CM is a small stand-in whose
    ``__aexit__`` sets a flag, so the code path is directly observable
    (the REAL child liveness is proven by the integration pin
    ``tests/integration/test_mcp_child_liveness.py``, which censuses
    /proc against the REAL pinned SDK).
"""

from __future__ import annotations

import asyncio
import inspect
import types
from unittest.mock import patch

import pytest

import daemon.mcp.stdio_wrapper as sw


def _params() -> types.SimpleNamespace:
    return types.SimpleNamespace(command="uvx", args=["mcp-server-x"])


# ── (a) the off-loop scan runs on the DEDICATED executor ────────────────


def test_on_ready_uses_dedicated_executor():
    cm = sw.TaskScopedStdioClient(_params(), instance_id="i", server_name="s")
    # The hook returns an awaitable (W-3 shape) — it does NOT run the
    # /proc scan inline on the event-loop thread.
    res = cm._schedule_stamp()
    assert inspect.isawaitable(res)
    # ...and it targets the DEDICATED executor, not the shared default
    # pool (which carries the daemon's LLM calls and compaction).
    assert sw._PROC_SCAN_EXECUTOR._thread_name_prefix == "mcp-procscan"
    assert sw._PROC_SCAN_EXECUTOR._max_workers == 2
    if hasattr(res, "close"):
        res.close()


def test_secret_executor_is_dedicated():
    from daemon.mcp import warmup_pool

    assert warmup_pool._SECRET_RESOLVE_EXECUTOR._thread_name_prefix == "mcp-secret"
    assert warmup_pool._SECRET_RESOLVE_EXECUTOR._max_workers == 2


# ── (b) the handshake wait is bounded ───────────────────────────────────


def test_handshake_wait_bounded(monkeypatch):
    monkeypatch.setattr(sw, "HANDSHAKE_WAIT_TIMEOUT_S", 0.1)

    async def _hang(self):
        # Never set _ready; simulate a hung handshake.
        await asyncio.sleep(10)

    async def _run():
        cm = sw.TaskScopedStdioClient(_params(), instance_id="i", server_name="s")
        cm._run = _hang.__get__(cm, type(cm))
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await cm.__aenter__()

    asyncio.run(_run())


def test_handshake_bound_less_than_sixty():
    assert sw.HANDSHAKE_WAIT_TIMEOUT_S < 60.0
    assert sw.HANDSHAKE_WAIT_TIMEOUT_S == 30.0


# ── (c) the G1 teardown guarantee ──────────────────────────────────────


class _RecordingCM:
    """A stand-in inner CM whose ``__aexit__`` records that it ran.

    ``__aenter__`` raises ``CancelledError`` immediately — the G1 route:
    the cancellation lands while the inner transport is still entering.
    """

    def __init__(self) -> None:
        self.exited = False
        self.enter_attempted = False

    async def __aenter__(self):
        self.enter_attempted = True
        raise asyncio.CancelledError()

    async def __aexit__(self, *exc):
        self.exited = True
        return False


def test_g1_cancellation_runs_aexit():
    """A cancelled inner enter MUST still run the inner ``__aexit__``."""
    holder: dict = {}

    def fake_client(server_params):
        cm = _RecordingCM()
        holder["cm"] = cm
        return cm

    async def _run():
        with patch.object(sw.mcp, "stdio_client", fake_client):
            wrapper = sw.TaskScopedStdioClient(
                _params(), instance_id="i", server_name="s"
            )
            with pytest.raises(BaseException):
                await wrapper.__aenter__()
        return holder["cm"]

    inner = asyncio.run(_run())
    assert inner.enter_attempted, "the inner __aenter__ was never reached"
    assert inner.exited is True, (
        "G1 BROKEN: the inner __aexit__ did not run after the cancellation — "
        "the SDK's process-group shutdown would be skipped and the spawned "
        "child would be orphaned"
    )


def test_request_close_is_synchronous():
    """``request_close`` must not await — a cancelled task cannot await."""
    cm = sw.TaskScopedStdioClient(_params(), instance_id="i", server_name="s")
    # Calling it from a synchronous context must not raise.
    cm.request_close()
    assert cm._close.is_set()
