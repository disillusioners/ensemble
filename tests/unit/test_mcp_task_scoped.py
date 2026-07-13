"""Unit tests for the generic ``TaskScopedContextManager``.

These tests cover the wrapper used for the SSE and Streamable-HTTP
MCP transports (``mcp.client.sse.sse_client``,
``mcp.client.streamable_http.streamablehttp_client``) — both of
which are anyio-task-group backed and previously raised
``RuntimeError: Attempted to exit cancel scope in a different task``
when their stored context manager was closed from a different
asyncio task (pool health-check, instance termination, replenish, …).

Back-compat coverage for ``TaskScopedStdioClient`` lives in
``test_mcp_stdio_wrapper.py``; that class is now a thin subclass of
``TaskScopedContextManager``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.mcp.stdio_wrapper import TaskScopedContextManager


class _TrackingACM:
    """Inner ACM that records which task runs __aenter__ / __aexit__.

    The wrapper's contract is that ``factory()`` returns an ACM
    (matching ``mcp.stdio_client(server_params)`` etc.). The ACM
    itself is a regular object with ``__aenter__``/``__aexit__``
    methods, not a coroutine.
    """

    def __init__(self, name: str, recorder: dict[str, Any]) -> None:
        self._name = name
        self._recorder = recorder

    async def __aenter__(self):
        self._recorder["aenter_task"] = asyncio.current_task()
        return (MagicMock(name=f"{self._name}-read"), MagicMock(name=f"{self._name}-write"))

    async def __aexit__(self, exc_type, exc, tb):
        self._recorder["aexit_task"] = asyncio.current_task()
        return False


class _SlowAexitACM:
    """Inner ACM whose __aexit__ blocks until ``release`` is set.

    ``__aenter__`` does NOT block — it just records the task and
    returns. This mirrors the ``BlockingExitCM`` helper used in the
    original STDIO tests: a slow __aenter__ + slow __aexit__ would
    cause the background task to be cancelled before the inner CM
    even sets up, defeating the purpose of the regression test.
    """

    def __init__(self, recorder: dict[str, Any], release: asyncio.Event) -> None:
        self._recorder = recorder
        self._release = release

    async def __aenter__(self):
        self._recorder["aenter_task"] = asyncio.current_task()
        return (MagicMock(name="read"), MagicMock(name="write"))

    async def __aexit__(self, exc_type, exc, tb):
        self._recorder["aexit_task"] = asyncio.current_task()
        await self._release.wait()
        return False


class _SlowAenterACM:
    """Inner ACM whose __aenter__ blocks on ``release_enter`` until released.

    Used by the caller-cancellation-during-__aenter__ test: the test
    starts the wrapper, then cancels the *caller* of ``__aenter__``
    while the background task is blocked inside the inner __aenter__.
    The wrapper must propagate the caller's ``CancelledError``.
    """

    def __init__(self, release_enter: asyncio.Event, release_exit: asyncio.Event) -> None:
        self._release_enter = release_enter
        self._release_exit = release_exit

    async def __aenter__(self):
        await self._release_enter.wait()
        return (MagicMock(name="read"), MagicMock(name="write"))

    async def __aexit__(self, exc_type, exc, tb):
        await self._release_exit.wait()
        return False


class _FailingFactory:
    """ACM whose ``__aenter__`` raises synchronously, like a missing binary."""

    def __aenter__(self):
        raise FileNotFoundError("npx not found")

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestTaskScopedContextManager:
    """Behavioural tests for the generic ACM wrapper."""

    @pytest.mark.asyncio
    async def test_aenter_returns_inner_streams(self):
        """__aenter__ should return whatever the inner __aenter__ yielded."""
        recorder: dict[str, Any] = {}

        def factory():
            return _TrackingACM("sse", recorder)

        wrapper = TaskScopedContextManager(factory)
        streams = await wrapper.__aenter__()
        try:
            assert len(streams) == 2
            assert streams[0]._mock_name == "sse-read"
            assert streams[1]._mock_name == "sse-write"
        finally:
            await wrapper.__aexit__(None, None, None)

        assert recorder["aenter_task"] is recorder["aexit_task"]

    @pytest.mark.asyncio
    async def test_factory_raising_synchronously_propagates(self):
        """A sync exception in the factory must surface to the caller, not hang."""
        wrapper = TaskScopedContextManager(lambda: _FailingFactory())
        with pytest.raises(FileNotFoundError, match="npx not found"):
            await wrapper.__aenter__()
        await wrapper.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_inner_aenter_exception_propagates(self):
        """An exception from the inner __aenter__ must surface to the caller."""

        class BoomACM:
            async def __aenter__(self):
                raise RuntimeError("spawn failed")

            async def __aexit__(self, *exc):
                return False

        wrapper = TaskScopedContextManager(lambda: BoomACM())
        with pytest.raises(RuntimeError, match="spawn failed"):
            await wrapper.__aenter__()
        await wrapper.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_inner_aexit_exception_is_swallowed(self):
        """__aexit__ must not propagate exceptions from the inner __aexit__."""
        inner_aexit = AsyncMock(side_effect=BrokenPipeError("subprocess gone"))

        class Cm:
            async def __aenter__(self):
                return (MagicMock(), MagicMock())

            async def __aexit__(self, *exc):
                await inner_aexit()
                return False

        wrapper = TaskScopedContextManager(lambda: Cm())
        await wrapper.__aenter__()
        await wrapper.__aexit__(None, None, None)
        inner_aexit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_works_with_async_with(self):
        """Wrapper must work as a plain ``async with`` context manager."""
        recorder: dict[str, Any] = {}

        def factory():
            return _TrackingACM("http", recorder)

        async with TaskScopedContextManager(factory) as streams:
            assert len(streams) == 2

        assert recorder["aenter_task"] is recorder["aexit_task"]

    @pytest.mark.asyncio
    async def test_aenter_and_aexit_run_in_same_task(self):
        """The whole point: inner __aenter__ and __aexit__ run in the same task."""
        recorder: dict[str, Any] = {}

        def factory():
            return _TrackingACM("streamable-http", recorder)

        wrapper = TaskScopedContextManager(factory)
        await wrapper.__aenter__()  # caller task A

        async def close_in_other_task():
            await wrapper.__aexit__(None, None, None)

        await asyncio.create_task(close_in_other_task())  # caller task B

        assert recorder["aenter_task"] is not None
        assert recorder["aexit_task"] is recorder["aenter_task"], (
            "Inner __aenter__ and __aexit__ must run in the same task to "
            "avoid anyio 'Attempted to exit cancel scope in a different task' "
            "errors when closing pooled streamable-http / SSE connections"
        )

    @pytest.mark.asyncio
    async def test_double_aexit_is_safe(self):
        """Calling __aexit__ twice must not raise and must not double-close."""
        recorder: dict[str, Any] = {}

        def factory():
            return _TrackingACM("sse", recorder)

        wrapper = TaskScopedContextManager(factory)
        await wrapper.__aenter__()
        await wrapper.__aexit__(None, None, None)
        await wrapper.__aexit__(None, None, None)  # no-op
        assert recorder["aexit_task"] is not None

    @pytest.mark.asyncio
    async def test_concurrent_aexit_is_idempotent(self):
        """Concurrent __aexit__ calls must not double-close the inner CM."""
        aexit_calls = 0

        class Cm:
            async def __aenter__(self):
                return (MagicMock(), MagicMock())

            async def __aexit__(self, *exc):
                nonlocal aexit_calls
                aexit_calls += 1
                return False

        wrapper = TaskScopedContextManager(lambda: Cm())
        await wrapper.__aenter__()

        async def closer():
            await wrapper.__aexit__(None, None, None)

        await asyncio.gather(closer(), closer())
        assert aexit_calls == 1

    @pytest.mark.asyncio
    async def test_caller_cancellation_during_aenter_propagates(self):
        """Caller-cancelled ``__aenter__`` propagates ``CancelledError``."""
        release_enter = asyncio.Event()
        release_exit = asyncio.Event()
        wrapper = TaskScopedContextManager(
            lambda: _SlowAenterACM(release_enter, release_exit)
        )
        try:
            caller_task = asyncio.create_task(wrapper.__aenter__())
            await asyncio.sleep(0.05)
            caller_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller_task
        finally:
            release_enter.set()
            release_exit.set()
            task = wrapper._task
            if task is not None and not task.done():
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except Exception:
                    task.cancel()
                    try:
                        await task
                    except Exception:
                        pass

    @pytest.mark.asyncio
    async def test_caller_cancellation_during_aexit_propagates(self):
        """Caller-cancelled ``__aexit__`` propagates ``CancelledError``."""
        release = asyncio.Event()
        wrapper = TaskScopedContextManager(lambda: _SlowAexitACM({}, release))
        await wrapper.__aenter__()
        try:
            exit_task = asyncio.create_task(wrapper.__aexit__(None, None, None))
            await asyncio.sleep(0.05)
            exit_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await exit_task
        finally:
            release.set()
            task = wrapper._task
            if task is not None and not task.done():
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except Exception:
                    task.cancel()
                    try:
                        await task
                    except Exception:
                        pass

    @pytest.mark.asyncio
    async def test_regression_anyio_cross_task_cancel_scope(self):
        """Regression: simulates the real anyio-task-group bug signature.

        The old (pre-fix) code called ``streamablehttp_client`` /
        ``sse_client`` and stored the resulting context manager in a
        pool. Closing it from a different task raised
        ``RuntimeError: Attempted to exit cancel scope in a different
        task`` and surfaced as an unretrieved task exception in the
        logs (the symptom in the reported bug report).

        This test reproduces the *task-relationship* contract that
        causes the error — different tasks for enter and exit — and
        asserts the wrapper handles it without raising. A test that
        used a real ``streamablehttp_client`` would require a live
        server; the synthetic ACM above exercises the same task-group
        binding contract that anyio checks.
        """
        enter_task_ref: dict[str, Any] = {}
        exit_task_ref: dict[str, Any] = {}

        class AnyioStyleCM:
            async def __aenter__(self):
                enter_task_ref["task"] = asyncio.current_task()
                return (MagicMock(), MagicMock())

            async def __aexit__(self, *exc):
                exit_task_ref["task"] = asyncio.current_task()
                return False

        wrapper = TaskScopedContextManager(lambda: AnyioStyleCM())
        await wrapper.__aenter__()

        async def pool_close():
            await wrapper.__aexit__(None, None, None)

        await asyncio.create_task(pool_close())

        assert enter_task_ref["task"] is exit_task_ref["task"]
