"""T5 — run_long_tool_nudge_loop tests.

Mirrors the watchdog loop contract: disabled → immediate return with
zero ticks; enabled → ticks with patched sleep; CancelledError from
run_once propagates (shutdown contract); random Exception is
swallowed + ERROR-logged and the loop continues.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import daemon.services.long_tool_nudge as lt
from daemon.services.long_tool_nudge import run_long_tool_nudge_loop

_STATS = {
    "instances_scanned": 0,
    "stamps_inspected": 0,
    "fired": 0,
    "skipped_disabled": 0,
    "errors": 0,
    "stale_stamps_force_cleared": 0,
    "orphan_episodes_discarded": 0,
}


def _scanner_mock(enabled: bool = True) -> AsyncMock:
    scanner = AsyncMock()
    scanner.enabled = enabled
    scanner.run_once = AsyncMock(return_value=dict(_STATS))
    return scanner


@pytest.mark.asyncio
async def test_disabled_loop_returns_immediately(monkeypatch):
    scanner = _scanner_mock(enabled=False)
    sleeps = 0

    async def _fail_sleep(_):
        nonlocal sleeps
        sleeps += 1
        raise AssertionError("loop must not sleep when disabled")

    monkeypatch.setattr(lt.asyncio, "sleep", _fail_sleep)
    result = await run_long_tool_nudge_loop(scanner, interval_seconds=60)
    assert result is None
    assert scanner.run_once.await_count == 0


@pytest.mark.asyncio
async def test_enabled_loop_ticks_with_patched_sleep(monkeypatch):
    scanner = _scanner_mock(enabled=True)
    ticks = {"n": 0}

    async def fake_sleep(_):
        ticks["n"] += 1
        if ticks["n"] >= 3:
            # Simulate shutdown between ticks — the loop returns cleanly.
            raise asyncio.CancelledError()

    monkeypatch.setattr(lt.asyncio, "sleep", fake_sleep)
    result = await run_long_tool_nudge_loop(scanner, interval_seconds=60)
    assert result is None  # clean return (sleep swallowed the cancel)
    assert scanner.run_once.await_count == 3


@pytest.mark.asyncio
async def test_cancelled_error_from_run_once_propagates(monkeypatch):
    scanner = _scanner_mock(enabled=True)
    scanner.run_once = AsyncMock(side_effect=asyncio.CancelledError())

    async def _fail_sleep(_):
        raise AssertionError("loop must not reach sleep when run_once cancels")

    monkeypatch.setattr(lt.asyncio, "sleep", _fail_sleep)
    with pytest.raises(asyncio.CancelledError):
        await run_long_tool_nudge_loop(scanner, interval_seconds=60)


@pytest.mark.asyncio
async def test_random_exception_swallowed_and_loop_continues(
    monkeypatch, caplog
):
    scanner = _scanner_mock(enabled=True)
    scanner.run_once = AsyncMock(
        side_effect=[RuntimeError("db blip"), dict(_STATS), dict(_STATS)]
    )
    ticks = {"n": 0}

    async def fake_sleep(_):
        ticks["n"] += 1
        if ticks["n"] >= 3:
            raise asyncio.CancelledError()

    monkeypatch.setattr(lt.asyncio, "sleep", fake_sleep)
    with caplog.at_level("ERROR", logger="daemon.services.long_tool_nudge"):
        result = await run_long_tool_nudge_loop(scanner, interval_seconds=60)
    assert result is None
    assert scanner.run_once.await_count == 3
    assert any("cycle failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_tick_stats_logged_on_fired(monkeypatch, caplog):
    scanner = _scanner_mock(enabled=True)
    stats = dict(_STATS)
    stats["fired"] = 1
    scanner.run_once = AsyncMock(return_value=stats)
    calls = {"n": 0}

    async def fake_sleep(_):
        calls["n"] += 1
        if calls["n"] >= 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(lt.asyncio, "sleep", fake_sleep)
    with caplog.at_level("INFO", logger="daemon.services.long_tool_nudge"):
        await run_long_tool_nudge_loop(scanner, interval_seconds=60)
    assert any("tick stats" in r.message for r in caplog.records)
