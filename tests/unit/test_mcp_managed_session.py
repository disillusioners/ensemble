"""Unit tests for ManagedClientSession's start/stop lifecycle.

Focus: cross-task start/stop safety (the regression that motivated the
managed_session.py rewrite). The receive loop runs in a plain
``asyncio.Task`` so it can be cancelled from any task without anyio's
"cancel scope in a different task" error.

Why we read the source file and exec the methods instead of
subclassing ``ManagedClientSession``
--------------------------------------------------------------

The conftest (``tests/conftest.py``) replaces ``mcp.ClientSession`` with
a ``MagicMock()`` instance to keep the MCP SDK off the test path. As a
result, the daemon's ``ManagedClientSession`` inherits from that mock,
and its ``__new__`` / attribute resolution don't behave like a normal
class — ``inspect.getsource`` on the class methods returns the
``MagicMock``'s auto-generated source, not the daemon's real code.

To exercise the *real* ``start()`` and ``stop()`` code from
``daemon/mcp/managed_session.py`` without inheriting from the mock,
this test reads the file directly, extracts the method bodies, and
``exec``'s them in a clean namespace as free coroutine functions. The
methods are then bound to a ``SimpleNamespace`` that provides only the
attributes they read (``_exit_stack``, ``_receive_task``, ``_receive_loop``).
This runs the same shipped code, not a copy.
"""

from __future__ import annotations

import asyncio
import logging
import textwrap
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace

import pytest


# Path to the real managed_session.py module
_MANAGED_SESSION_PATH = (
    Path(__file__).parent.parent.parent / "daemon" / "mcp" / "managed_session.py"
)


def _extract_method(source: str, name: str) -> str:
    """Extract a method's source by name from a class block."""
    lines = source.splitlines()
    in_method = False
    method_lines: list[str] = []
    base_indent: int | None = None
    for line in lines:
        stripped = line.lstrip()
        if not in_method:
            if stripped.startswith(f"async def {name}(") or stripped.startswith(f"def {name}("):
                in_method = True
                base_indent = len(line) - len(stripped)
                method_lines.append(line)
        else:
            if stripped == "" or (len(line) - len(stripped)) > base_indent:
                method_lines.append(line)
            else:
                break
    if not method_lines:
        raise ValueError(f"Method {name} not found")
    return "\n".join(method_lines)


def _load_real_method(name: str):
    """Load a real method from managed_session.py and return it as a free function."""
    source = _MANAGED_SESSION_PATH.read_text()
    method_src = _extract_method(source, name)
    # Dedent so it's a top-level function
    method_src = textwrap.dedent(method_src)
    globals_dict = {
        "asyncio": asyncio,
        "logger": logging.getLogger("daemon.mcp.managed_session"),
    }
    local_ns: dict = {}
    exec(method_src, globals_dict, local_ns)
    return local_ns[name]


# Extract the real start() and stop() from the file at import time.
# If the daemon file changes shape and the extraction breaks, the test
# errors loudly here rather than silently testing stale code.
_real_start = _load_real_method("start")
_real_stop = _load_real_method("stop")


def _make_test_session() -> SimpleNamespace:
    """Build a SimpleNamespace with the attributes start()/stop() need.

    Provides:
    - ``_exit_stack``: a real ``AsyncExitStack`` (so ``stop()`` can ``aclose()``)
    - ``_receive_task``: ``None`` (set by ``start()``)
    - ``_receive_loop``: a fake long-running coroutine
    - ``start``/``stop``: the real methods bound to this namespace
    """
    session = SimpleNamespace()
    session._receive_task = None
    session._exit_stack = AsyncExitStack()

    async def _fake_receive_loop():
        await asyncio.sleep(3600)

    session._receive_loop = _fake_receive_loop
    session.start = _real_start.__get__(session, type(session))
    session.stop = _real_stop.__get__(session, type(session))
    return session


class TestManagedClientSessionStartStop:
    """Tests for the rewritten start()/stop() lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_asyncio_task(self):
        """``start()`` must create a plain ``asyncio.Task``.

        The whole point of the rewrite: the old code used an anyio task
        group which is bound to the task that called ``__aenter__``.
        Switching to ``asyncio.Task`` is what makes ``stop()`` safe to
        call from a different task.
        """
        session = _make_test_session()

        await session.start()

        assert isinstance(session._receive_task, asyncio.Task)
        assert not session._receive_task.done()

        await session.stop()

    @pytest.mark.asyncio
    async def test_start_and_stop_in_same_task(self):
        """Baseline: start and stop in the same task works."""
        session = _make_test_session()

        await session.start()
        assert session._receive_task is not None
        assert not session._receive_task.done()

        await session.stop()
        assert session._receive_task is None

    @pytest.mark.asyncio
    async def test_start_and_stop_in_different_tasks(self):
        """Regression test: start() and stop() in different tasks must not raise.

        Before the rewrite, this would have raised
        ``RuntimeError: Attempted to exit cancel scope in a different task``
        because the old ``start()`` created an anyio task group. The
        rewrite uses ``asyncio.Task`` which is cross-task safe.
        """
        session = _make_test_session()

        started = asyncio.Event()
        stopped = asyncio.Event()

        async def starter():
            await session.start()
            started.set()
            await stopped.wait()
            await session.stop()

        async def stopper():
            await started.wait()
            # Give the starter task time to fully complete ``start()``
            await asyncio.sleep(0.05)
            await session.stop()
            stopped.set()

        # If the rewrite is broken (still using anyio), this raises.
        # With asyncio.Task, both complete cleanly.
        await asyncio.gather(starter(), stopper())

        assert session._receive_task is None

    @pytest.mark.asyncio
    async def test_double_stop_is_safe(self):
        """Calling stop() twice should not raise."""
        session = _make_test_session()

        await session.start()
        await session.stop()
        # Second call is a no-op
        await session.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_noop(self):
        """stop() without start() should be a no-op, not an error."""
        session = _make_test_session()

        await session.stop()  # Must not raise
        assert session._receive_task is None

    @pytest.mark.asyncio
    async def test_start_is_idempotent_while_running(self):
        """start() called twice while a receive loop is running should be a no-op."""
        session = _make_test_session()

        await session.start()
        first_task = session._receive_task

        await session.start()  # Should not replace the running task
        assert session._receive_task is first_task

        await session.stop()

    @pytest.mark.asyncio
    async def test_caller_cancellation_during_stop_propagates(self):
        """If the *caller* of stop() is cancelled, the cancellation should propagate.

        The pre-rewrite ``stop()`` swallowed ``CancelledError`` from
        ``await task`` even when the *caller's* task was being cancelled.
        The fix re-raises if the current task is being cancelled.
        """
        session = _make_test_session()
        await session.start()

        async def caller():
            # Cancel the caller mid-stop
            task = asyncio.current_task()
            task.cancel()
            await session.stop()

        with pytest.raises(asyncio.CancelledError):
            await caller()
