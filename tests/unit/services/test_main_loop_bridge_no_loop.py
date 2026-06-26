"""Tests for ``MainLoopBridge.run_async_no_wait`` no-loop branch.

Regression coverage for the 2026-06-26 review fix: ``run_async_no_wait``
now closes the coroutine locally when no event loop is wired (instead of
silently dropping it and emitting ``RuntimeWarning: coroutine '...' was
never awaited`` on GC). Returns ``False`` on the no-loop branch and
``True`` on the happy path so callers / tests can assert the dispatch
outcome.

The bridge is exercised from sync worker threads; the no-loop branch is
the common case in tests and during shutdown.
"""

from __future__ import annotations

import asyncio

import pytest

from daemon.services.main_loop_bridge import MainLoopBridge


@pytest.fixture(autouse=True)
def _reset_bridge():
    """Reset bridge singleton before and after each test for isolation.

    ``MainLoopBridge`` is a process-wide singleton; without this fixture
    a previous test that called ``set_loop(...)`` would leak its loop
    into the next test and skew the no-loop assertion.
    """
    MainLoopBridge.reset()
    yield
    MainLoopBridge.reset()


@pytest.mark.asyncio
async def test_run_async_no_wait_no_loop_closes_coroutine():
    """No-loop branch: helper closes the coro and returns False.

    Verifies the 2026-06-26 fix that lifts ``coro.close()`` into the
    bridge so all callers benefit from a clean no-op rather than a
    ``RuntimeWarning`` on GC.
    """
    async def _sample():
        return 42

    coro = _sample()
    assert coro.cr_frame is not None, "coro must have a frame pre-close"

    result = MainLoopBridge.run_async_no_wait(coro)

    assert result is False, "no-loop branch must return False"
    assert coro.cr_frame is None, (
        "coro should be closed (cr_frame set to None) — without this "
        "GC would emit a RuntimeWarning"
    )


@pytest.mark.asyncio
async def test_run_async_no_wait_with_loop_returns_true():
    """Happy path: helper schedules the coro and returns True.

    Uses the running event loop (the one pytest-asyncio provides) to
    confirm the new return value semantics on the dispatch path, not
    just the no-loop branch.
    """
    MainLoopBridge.set_loop(asyncio.get_running_loop())

    async def _sample():
        return 42

    result = MainLoopBridge.run_async_no_wait(_sample())
    assert result is True, "happy path must return True"

    # Let the scheduled coro run so its frame is freed (no GC warning)
    await asyncio.sleep(0)


def test_get_loop_is_public():
    """``get_loop`` is the public replacement for the private ``_loop`` attr.

    Callers should not reach into ``MainLoopBridge._loop`` directly;
    this test pins the public API.
    """
    loop = asyncio.new_event_loop()
    try:
        MainLoopBridge.set_loop(loop)
        assert MainLoopBridge.get_loop() is loop
    finally:
        MainLoopBridge.reset()
        loop.close()
