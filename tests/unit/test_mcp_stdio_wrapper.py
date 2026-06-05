"""Unit tests for TaskScopedStdioClient."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.mcp.stdio_wrapper import TaskScopedStdioClient


def _make_server_params() -> MagicMock:
    """Create a mock StdioServerParameters."""
    return MagicMock()


def _make_mock_inner_cm(read_stream=None, write_stream=None) -> MagicMock:
    """Create a mock context manager that mimics mcp.stdio_client."""
    read_stream = read_stream if read_stream is not None else MagicMock()
    write_stream = write_stream if write_stream is not None else MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=(read_stream, write_stream))
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


class TestTaskScopedStdioClient:
    """Tests for TaskScopedStdioClient."""

    @pytest.mark.asyncio
    async def test_aenter_returns_streams_from_inner(self):
        """__aenter__ should return the streams from the inner context manager."""
        read_stream = MagicMock(name="read")
        write_stream = MagicMock(name="write")
        inner_cm = _make_mock_inner_cm(read_stream, write_stream)

        with patch("daemon.mcp.stdio_wrapper.mcp.stdio_client", return_value=inner_cm) as mock_factory:
            wrapper = TaskScopedStdioClient(_make_server_params())
            read, write = await wrapper.__aenter__()

        assert read is read_stream
        assert write is write_stream
        mock_factory.assert_called_once()
        inner_cm.__aenter__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aexit_calls_inner_aexit(self):
        """__aexit__ should call the inner context manager's __aexit__."""
        inner_cm = _make_mock_inner_cm()

        with patch("daemon.mcp.stdio_wrapper.mcp.stdio_client", return_value=inner_cm):
            wrapper = TaskScopedStdioClient(_make_server_params())
            await wrapper.__aenter__()
            await wrapper.__aexit__(None, None, None)

        inner_cm.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aexit_swallows_inner_aexit_exception(self):
        """__aexit__ should not propagate exceptions from the inner __aexit__."""
        inner_cm = _make_mock_inner_cm()
        inner_cm.__aexit__ = AsyncMock(side_effect=BrokenPipeError("subprocess gone"))

        with patch("daemon.mcp.stdio_wrapper.mcp.stdio_client", return_value=inner_cm):
            wrapper = TaskScopedStdioClient(_make_server_params())
            await wrapper.__aenter__()
            # Should not raise even though inner __aexit__ does
            await wrapper.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_works_with_async_with(self):
        """Wrapper should work as a normal async context manager."""
        read_stream = MagicMock(name="read")
        write_stream = MagicMock(name="write")
        inner_cm = _make_mock_inner_cm(read_stream, write_stream)

        with patch("daemon.mcp.stdio_wrapper.mcp.stdio_client", return_value=inner_cm):
            async with TaskScopedStdioClient(_make_server_params()) as streams:
                read, write = streams
                assert read is read_stream
                assert write is write_stream

        inner_cm.__aenter__.assert_awaited_once()
        inner_cm.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aenter_raises_when_inner_aenter_fails(self):
        """__aenter__ should propagate exceptions from the inner __aenter__."""
        inner_cm = _make_mock_inner_cm()
        inner_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("spawn failed"))

        with patch("daemon.mcp.stdio_wrapper.mcp.stdio_client", return_value=inner_cm):
            wrapper = TaskScopedStdioClient(_make_server_params())
            with pytest.raises(RuntimeError, match="spawn failed"):
                await wrapper.__aenter__()

    @pytest.mark.asyncio
    async def test_aenter_raises_when_factory_raises_synchronously(self):
        """__aenter__ should propagate exceptions from the mcp.stdio_client factory.

        The factory can raise synchronously (e.g. ``FileNotFoundError`` when
        the subprocess command is missing). The wrapper must surface this
        to the caller instead of hanging on ``_ready.wait()``.
        """
        with patch(
            "daemon.mcp.stdio_wrapper.mcp.stdio_client",
            side_effect=FileNotFoundError("npx not found"),
        ):
            wrapper = TaskScopedStdioClient(_make_server_params())
            with pytest.raises(FileNotFoundError, match="npx not found"):
                await wrapper.__aenter__()

    @pytest.mark.asyncio
    async def test_aenter_runs_inner_in_different_task(self):
        """__aenter__ should run the inner __aenter__ in the wrapper's task, not the caller's.

        This is the property that prevents the "Attempted to exit cancel scope
        in a different task" error: both inner __aenter__ and __aexit__ run in
        the same task (the background task).
        """
        inner_aenter_task = None
        inner_aexit_task = None
        call_event = asyncio.Event()

        class TrackingCM:
            def __init__(self):
                self._aenter_called = False

            async def __aenter__(self):
                nonlocal inner_aenter_task
                inner_aenter_task = asyncio.current_task()
                self._aenter_called = True
                return (MagicMock(name="read"), MagicMock(name="write"))

            async def __aexit__(self, *exc_info):
                nonlocal inner_aexit_task
                inner_aexit_task = asyncio.current_task()
                return False

        tracking_cm = TrackingCM()

        with patch("daemon.mcp.stdio_wrapper.mcp.stdio_client", return_value=tracking_cm):
            wrapper = TaskScopedStdioClient(_make_server_params())
            await wrapper.__aenter__()

        # Caller task is different from the wrapper's background task
        caller_task = asyncio.current_task()
        assert inner_aenter_task is not caller_task
        assert inner_aenter_task is not None

        # Now exit from a different task than __aenter__ was called from
        await asyncio.create_task(_exit_in_task(wrapper, call_event))
        await call_event.wait()

        # The inner __aenter__ and __aexit__ should have run in the SAME task
        assert inner_aexit_task is inner_aenter_task, (
            "Inner __aenter__ and __aexit__ must run in the same task to avoid "
            "anyio 'Attempted to exit cancel scope in a different task' errors"
        )

    @pytest.mark.asyncio
    async def test_aexit_from_different_task_does_not_raise(self):
        """__aexit__ should work safely when called from a task other than __aenter__.

        This is the core regression test for the production bug: in the old
        code, closing a stored streams_cm from a different task raised
        RuntimeError. With the wrapper, it must succeed.
        """
        inner_cm = _make_mock_inner_cm()

        with patch("daemon.mcp.stdio_wrapper.mcp.stdio_client", return_value=inner_cm):
            wrapper = TaskScopedStdioClient(_make_server_params())
            # __aenter__ in this task
            await wrapper.__aenter__()

            # __aexit__ in a different spawned task
            async def closer():
                await wrapper.__aexit__(None, None, None)

            closer_task = asyncio.create_task(closer())
            await closer_task  # Must not raise

        inner_cm.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_double_aexit_is_safe(self):
        """Calling __aexit__ twice should not raise."""
        inner_cm = _make_mock_inner_cm()

        with patch("daemon.mcp.stdio_wrapper.mcp.stdio_client", return_value=inner_cm):
            wrapper = TaskScopedStdioClient(_make_server_params())
            await wrapper.__aenter__()
            await wrapper.__aexit__(None, None, None)
            # Second call should be a no-op
            await wrapper.__aexit__(None, None, None)


async def _exit_in_task(wrapper: TaskScopedStdioClient, event: asyncio.Event) -> None:
    """Helper: call __aexit__ in a separate task and signal when done."""
    await wrapper.__aexit__(None, None, None)
    event.set()
