"""Idle-reap tests for the MCP warmup pool (commission item O4 Part B).

Covers: idle connection reaped past the timeout; an in-use (acquired)
connection never reaped; ``idle_reap_seconds=0`` disables reaping; the
grace-window escalation hard-cancels a wedged teardown; and the config
knob's default / env-override / validation semantics.

No real MCP server is ever spawned — all sessions/streams are fakes and
replenishment is stubbed. No subprocess, network, filesystem, or
HOME-derived / live-shaped path is touched anywhere in this module.
"""

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from daemon.config import McpPoolConfig
from daemon.mcp import warmup_pool as warmup_pool_module
from daemon.mcp.config import McpStdioConfig
from daemon.mcp.warmup_pool import McpWarmupPool, PooledConnection


def _make_config() -> McpStdioConfig:
    return McpStdioConfig(transport="stdio", command="npx", args=["-y", "fake-server"])


def _make_pooled_connection(
    server_name: str = "context7",
    idle_seconds: float = 0.0,
) -> PooledConnection:
    """Pooled connection whose ``created_at`` lies ``idle_seconds`` in the past.

    ``created_at`` doubles as the enqueue time for pool-owned connections
    (a pooled connection never re-enters the queue after acquisition), so
    back-dating it models an idle-queued child.
    """
    return PooledConnection(
        session=AsyncMock(),
        stream_cm=MagicMock(),
        tools=[MagicMock()],
        server_name=server_name,
        created_at=time.monotonic() - idle_seconds,
    )


@pytest.fixture
def pool() -> McpWarmupPool:
    p = McpWarmupPool()  # default idle_reap_seconds=1800
    p.register_server("context7", _make_config(), pool_size=1)
    p._running = True
    return p


@pytest.mark.asyncio
async def test_idle_connection_reaped_beyond_timeout(pool):
    """A connection idle past the timeout is reaped; a fresh one is not."""
    stale = _make_pooled_connection(idle_seconds=10_000.0)
    fresh = _make_pooled_connection(idle_seconds=1.0)
    await pool._pools["context7"].put(stale)
    await pool._pools["context7"].put(fresh)

    with patch.object(pool, "_start_tracked_replenish"):
        await pool.health_check()

    # Stale: torn down via the standard close path, never pinged.
    stale.session.stop.assert_awaited_once()
    stale.session.send_ping.assert_not_awaited()
    # Fresh: pinged and returned to the pool.
    fresh.session.stop.assert_not_awaited()
    fresh.session.send_ping.assert_awaited_once()
    assert pool._pools["context7"].qsize() == 1


@pytest.mark.asyncio
async def test_reap_logs_server_name_and_idle_duration(pool, caplog):
    """Each reap is logged with the server name and the idle duration."""
    stale = _make_pooled_connection(server_name="context7", idle_seconds=10_000.0)
    await pool._pools["context7"].put(stale)

    with patch.object(pool, "_start_tracked_replenish"), caplog.at_level(
        logging.INFO, logger="daemon.mcp.warmup_pool"
    ):
        await pool.health_check()

    assert "Reaping idle pooled MCP connection" in caplog.text
    assert "server='context7'" in caplog.text
    assert "idle=" in caplog.text


@pytest.mark.asyncio
async def test_in_use_connection_is_never_reaped(pool):
    """An acquired (in-use) connection is absent from the queue and must
    never be touched by a reap pass, while a queued idle peer is reaped."""
    in_use = _make_pooled_connection(idle_seconds=10_000.0)
    await pool._pools["context7"].put(in_use)

    # Acquire it: ownership leaves the pool queue (the connection is in use).
    with patch.object(pool, "_start_tracked_replenish"):
        acquired = await pool.acquire("context7")
    assert acquired is in_use
    assert pool._pools["context7"].qsize() == 0

    # A queued idle peer, reaped in the same pass, proves the pass ran.
    queued_idle = _make_pooled_connection(idle_seconds=10_000.0)
    await pool._pools["context7"].put(queued_idle)

    with patch.object(pool, "_start_tracked_replenish"):
        await pool.health_check()

    in_use.session.stop.assert_not_awaited()
    in_use.session.send_ping.assert_not_awaited()
    queued_idle.session.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_reaping_disabled_when_zero():
    """``idle_reap_seconds=0`` disables reaping entirely."""
    p = McpWarmupPool(idle_reap_seconds=0)
    p.register_server("context7", _make_config(), pool_size=1)
    p._running = True

    ancient = _make_pooled_connection(idle_seconds=10**9)
    await p._pools["context7"].put(ancient)

    with patch.object(p, "_start_tracked_replenish"):
        await p.health_check()

    ancient.session.stop.assert_not_awaited()
    ancient.session.send_ping.assert_awaited_once()
    assert p._pools["context7"].qsize() == 1


@pytest.mark.asyncio
async def test_reap_grace_expiry_escalates_to_hard_cancel(pool, monkeypatch, caplog):
    """A teardown that wedges past the grace window is hard-cancelled."""
    monkeypatch.setattr(warmup_pool_module, "REAP_CLOSE_GRACE_SECONDS", 0.05)

    async def _hang(*args, **kwargs):
        await asyncio.Event().wait()  # never returns

    conn = _make_pooled_connection(idle_seconds=10_000.0)
    conn.session.stop = AsyncMock(side_effect=_hang)
    bg_task = asyncio.create_task(asyncio.Event().wait(), name="fake-stdio-bg")
    conn.stream_cm.background_task = bg_task

    await pool._pools["context7"].put(conn)

    with patch.object(pool, "_start_tracked_replenish"), caplog.at_level(
        logging.WARNING, logger="daemon.mcp.warmup_pool"
    ):
        await pool.health_check()

    assert bg_task.cancelled()
    assert "escalating to hard teardown cancel" in caplog.text


def test_pool_default_matches_config_default():
    """The pool ctor default and the config default agree (30 min)."""
    assert McpWarmupPool()._idle_reap_seconds == 1800
    assert McpPoolConfig().idle_reap_seconds == 1800


def test_config_idle_reap_env_override_and_validation(monkeypatch):
    """The knob follows the module's existing ``MCP_POOL_`` env convention;
    ``0`` is accepted (disabled) and negatives are rejected."""
    monkeypatch.setenv("MCP_POOL_IDLE_REAP_SECONDS", "90")
    assert McpPoolConfig().idle_reap_seconds == 90

    monkeypatch.setenv("MCP_POOL_IDLE_REAP_SECONDS", "0")
    assert McpPoolConfig().idle_reap_seconds == 0

    with pytest.raises(ValidationError):
        McpPoolConfig(idle_reap_seconds=-1)
