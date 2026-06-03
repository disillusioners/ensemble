"""Unit tests for ``daemon.write_pause_guard.WritePauseGuard`` and ``WriteGuardSession``.

Phase 3 of the SQLite->PostgreSQL migration requires briefly blocking all
in-flight database writes while the data migrator copies rows. The
``WritePauseGuard`` is the primitive that drains and gates writers; this
test module exercises its core contract:

* Basic enter/exit bracketing.
* ``pause_writes()`` blocks until active writes drain (real threads).
* ``resume_writes()`` releases the gate.
* ``is_write_paused`` property reflects state.
* ``RuntimeError`` raised when entering a write while paused.
* Drain event correctly signals when the last write exits.
* Cooperative behaviour works from both sync and async contexts.

The tests run without a database; we exercise the gate state machine in
isolation and use ``MagicMock`` for the underlying ``Session`` because the
guard's contract is purely about lifecycle, not about SQL.
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest

from daemon.write_pause_guard import WriteGuardSession, WritePauseGuard


# ──────────────────────────────────────────────────────────────────────────────
# Basic state machine
# ──────────────────────────────────────────────────────────────────────────────


class TestWritePauseGuardBasicState:
    """A fresh guard is in the "writes allowed, none in flight" state."""

    def test_initial_state(self):
        """A brand-new guard has no active writes and is not paused."""
        guard = WritePauseGuard()

        assert guard.is_write_paused is False
        assert guard.active_writes == 0

    def test_write_enter_increments_counter(self):
        """``write_enter()`` increments ``active_writes``."""
        guard = WritePauseGuard()

        guard.write_enter()
        assert guard.active_writes == 1

        guard.write_enter()
        assert guard.active_writes == 2

    def test_write_exit_decrements_counter(self):
        """``write_exit()`` decrements ``active_writes``."""
        guard = WritePauseGuard()
        guard.write_enter()
        guard.write_enter()
        assert guard.active_writes == 2

        guard.write_exit()
        assert guard.active_writes == 1

        guard.write_exit()
        assert guard.active_writes == 0

    def test_write_enter_write_exit_brackets(self):
        """A paired enter/exit round-trips the counter to 0."""
        guard = WritePauseGuard()
        for _ in range(10):
            guard.write_enter()
            guard.write_exit()

        assert guard.active_writes == 0
        assert guard.is_write_paused is False

    def test_write_exit_without_enter_is_safe(self):
        """Extra ``write_exit()`` calls log a warning but don't corrupt state."""
        guard = WritePauseGuard()
        # Should not raise
        guard.write_exit()
        assert guard.active_writes == 0

        # Should still be safe to use after.
        guard.write_enter()
        guard.write_exit()
        assert guard.active_writes == 0

    def test_is_write_paused_reflects_state(self):
        """``is_write_paused`` flips with pause/resume."""
        guard = WritePauseGuard()

        assert guard.is_write_paused is False
        guard.pause_writes()
        assert guard.is_write_paused is True
        guard.resume_writes()
        assert guard.is_write_paused is False

    def test_active_writes_property_reads_under_lock(self):
        """``active_writes`` is safe to read concurrently with writers."""
        guard = WritePauseGuard()
        guard.write_enter()
        guard.write_enter()

        observed: list[int] = []
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                observed.append(guard.active_writes)

        t = threading.Thread(target=reader)
        t.start()
        time.sleep(0.01)  # let the reader run briefly
        stop.set()
        t.join(timeout=1.0)

        # Every observed value must be 1 or 2 (in-flight writes). Anything
        # else would mean the property read a half-mutated counter.
        assert all(v in (1, 2) for v in observed), observed


# ──────────────────────────────────────────────────────────────────────────────
# Pause/resume semantics
# ──────────────────────────────────────────────────────────────────────────────


class TestWritePauseGuardPauseResume:
    """Pause blocks new writes and waits for in-flight writes to drain."""

    def test_pause_writes_with_no_active_writes_returns_immediately(self):
        """Pause is a no-op fast path when nothing is in flight."""
        guard = WritePauseGuard()

        start = time.monotonic()
        guard.pause_writes()
        elapsed = time.monotonic() - start

        assert guard.is_write_paused is True
        # No active writes, drain event already set, so pause is near-instant.
        assert elapsed < 0.5

    def test_resume_writes_clears_pause_flag(self):
        """``resume_writes()`` sets ``is_write_paused`` back to False."""
        guard = WritePauseGuard()
        guard.pause_writes()
        assert guard.is_write_paused is True

        guard.resume_writes()
        assert guard.is_write_paused is False

    def test_pause_writes_blocks_until_active_writes_drain(self):
        """``pause_writes()`` waits for in-flight writers to call ``write_exit``."""
        guard = WritePauseGuard()

        # Simulate a write in progress.
        guard.write_enter()
        assert guard.active_writes == 1

        pause_done = threading.Event()
        resume_after_delay = 0.2

        def pauser() -> None:
            guard.pause_writes()
            pause_done.set()

        def releaser() -> None:
            # Release the in-flight write shortly after the pauser blocks.
            time.sleep(resume_after_delay)
            guard.write_exit()

        pauser_thread = threading.Thread(target=pauser)
        releaser_thread = threading.Thread(target=releaser)

        pauser_thread.start()
        releaser_thread.start()

        # Pause should not finish until releaser calls write_exit().
        assert not pause_done.wait(timeout=resume_after_delay / 2)
        # After the releaser has finished, pause must have completed.
        releaser_thread.join(timeout=1.0)
        pauser_thread.join(timeout=1.0)

        assert pause_done.is_set(), "pause_writes did not return after writes drained"
        assert guard.is_write_paused is True

    def test_pause_writes_serialised_against_concurrent_resume(self):
        """A concurrent pause/resume pair never races the paused flag."""
        guard = WritePauseGuard()
        # Pre-arm an in-flight write so pause will actually wait.
        guard.write_enter()

        observed_states: list[tuple[bool, bool]] = []
        stop = threading.Event()

        def pauser() -> None:
            while not stop.is_set():
                guard.pause_writes()
                observed_states.append(("paused", guard.is_write_paused))
                guard.resume_writes()
                observed_states.append(("resumed", guard.is_write_paused))

        t = threading.Thread(target=pauser)
        t.start()
        time.sleep(0.05)
        stop.set()
        # Release the in-flight write so the pauser can drain and exit.
        guard.write_exit()
        t.join(timeout=2.0)

        # Every "paused" snapshot must be True, every "resumed" must be False.
        for label, paused in observed_states:
            if label == "paused":
                assert paused is True
            else:
                assert paused is False

    def test_pause_writes_is_idempotent(self):
        """Calling ``pause_writes()`` multiple times is safe."""
        guard = WritePauseGuard()

        guard.pause_writes()
        guard.pause_writes()  # should not block; event already set
        assert guard.is_write_paused is True


# ──────────────────────────────────────────────────────────────────────────────
# Blocking new writes while paused
# ──────────────────────────────────────────────────────────────────────────────


class TestWritePauseGuardBlocksNewWrites:
    """``write_enter()`` must raise when the guard is paused."""

    def test_write_enter_raises_when_paused(self):
        """Entering a write while paused raises ``RuntimeError``."""
        guard = WritePauseGuard()
        guard.pause_writes()

        with pytest.raises(RuntimeError, match="Writes are paused"):
            guard.write_enter()

    def test_write_enter_after_resume_succeeds(self):
        """After ``resume_writes()``, new writes are accepted again."""
        guard = WritePauseGuard()
        guard.pause_writes()
        guard.resume_writes()

        # Should not raise.
        guard.write_enter()
        assert guard.active_writes == 1

    def test_runtime_error_does_not_increment_counter(self):
        """A failed ``write_enter()`` must not inflate the active-writes counter."""
        guard = WritePauseGuard()
        guard.pause_writes()

        for _ in range(5):
            with pytest.raises(RuntimeError):
                guard.write_enter()

        # Counter must be untouched — otherwise pause could never drain.
        assert guard.active_writes == 0

        # And resuming now must work without leaving stranded counters.
        guard.resume_writes()
        assert guard.is_write_paused is False


# ──────────────────────────────────────────────────────────────────────────────
# Drain event
# ──────────────────────────────────────────────────────────────────────────────


class TestWritePauseGuardDrainEvent:
    """The drain event must be set whenever ``_active_writes == 0``."""

    def test_drain_event_set_initially(self):
        """A new guard has no writers, so the drain event is set."""
        guard = WritePauseGuard()
        assert guard._drain_event.is_set() is True

    def test_drain_event_cleared_on_enter(self):
        """``write_enter()`` clears the drain event."""
        guard = WritePauseGuard()
        guard.write_enter()
        assert guard._drain_event.is_set() is False

    def test_drain_event_set_on_last_exit(self):
        """``write_exit()`` sets the drain event when counter hits zero."""
        guard = WritePauseGuard()
        guard.write_enter()
        guard.write_enter()
        assert guard._drain_event.is_set() is False

        guard.write_exit()
        # Still 1 active, drain pending.
        assert guard._drain_event.is_set() is False

        guard.write_exit()
        # Last writer exited; drain event set.
        assert guard._drain_event.is_set() is True

    def test_pause_writes_waits_on_drain_event(self):
        """A pauser blocked on a writer must wake immediately after the writer exits."""
        guard = WritePauseGuard()
        guard.write_enter()

        pause_result: dict = {"completed": False, "elapsed": None}

        def pauser() -> None:
            start = time.monotonic()
            guard.pause_writes()
            pause_result["elapsed"] = time.monotonic() - start
            pause_result["completed"] = True

        t = threading.Thread(target=pauser)
        t.start()

        # Let pauser actually block.
        time.sleep(0.05)
        assert not pause_result["completed"]

        # Release; pauser should drain very quickly.
        release_at = time.monotonic()
        guard.write_exit()
        t.join(timeout=1.0)

        assert pause_result["completed"] is True
        # The pauser's remaining time after release should be tiny.
        assert pause_result["elapsed"] is not None
        assert pause_result["elapsed"] < 0.5


# ──────────────────────────────────────────────────────────────────────────────
# Sync / async interoperability
# ──────────────────────────────────────────────────────────────────────────────


class TestWritePauseGuardSyncAsyncInterop:
    """The guard must work when called from event-loop threads too."""

    @pytest.mark.asyncio
    async def test_pause_from_async_loop_thread(self):
        """Calling ``pause_writes()`` from an event loop does not raise."""
        guard = WritePauseGuard()

        # `await asyncio.to_thread` mirrors how the migration worker
        # invokes the sync guard from inside an async context.
        await asyncio.to_thread(guard.pause_writes)
        assert guard.is_write_paused is True

        await asyncio.to_thread(guard.resume_writes)
        assert guard.is_write_paused is False

    @pytest.mark.asyncio
    async def test_mixed_sync_and_async_writers(self):
        """Writers from sync and async contexts share the same counter."""
        guard = WritePauseGuard()
        async_writer_entered = threading.Event()
        async_writer_release = threading.Event()

        async def async_writer() -> None:
            await asyncio.to_thread(guard.write_enter)
            async_writer_entered.set()
            await asyncio.get_event_loop().run_in_executor(
                None, async_writer_release.wait
            )
            await asyncio.to_thread(guard.write_exit)

        # Two sync writers + one async writer.
        guard.write_enter()
        guard.write_enter()

        task = asyncio.create_task(async_writer())
        # Wait until the async writer has actually entered.
        await asyncio.to_thread(async_writer_entered.wait, timeout=1.0)
        assert guard.active_writes == 3

        # Release the two sync writers from a worker thread.
        def release_sync_writers() -> None:
            guard.write_exit()
            guard.write_exit()

        threading.Thread(target=release_sync_writers, daemon=True).start()

        # Now release the async writer.
        async_writer_release.set()
        await task
        assert guard.active_writes == 0

        # Pause should drain immediately now (no writers in flight).
        await asyncio.to_thread(guard.pause_writes)
        assert guard.is_write_paused is True


# ──────────────────────────────────────────────────────────────────────────────
# WriteGuardSession context manager
# ──────────────────────────────────────────────────────────────────────────────


class TestWriteGuardSession:
    """``WriteGuardSession`` registers with the guard on enter, releases on exit."""

    def test_enter_registers_write_exit_releases(self):
        """A with-block increments then decrements the counter."""
        guard = WritePauseGuard()
        session = MagicMock()

        with WriteGuardSession(session, guard) as proxy:
            assert guard.active_writes == 1
            # The wrapper should proxy attribute access to the session.
            proxy.add("foo")

        # After the block, write_exit was called and the session was closed.
        assert guard.active_writes == 0
        session.close.assert_called_once()
        session.add.assert_called_once_with("foo")

    def test_enter_raises_when_paused(self):
        """Entering the context manager while paused raises ``RuntimeError``."""
        guard = WritePauseGuard()
        session = MagicMock()
        guard.pause_writes()

        with pytest.raises(RuntimeError, match="Writes are paused"):
            with WriteGuardSession(session, guard):
                pytest.fail("__enter__ should have raised")

        # Counter untouched.
        assert guard.active_writes == 0

    def test_exit_calls_close_even_on_exception(self):
        """The session is closed and the guard released even if the body raises."""
        guard = WritePauseGuard()
        session = MagicMock()

        with pytest.raises(ValueError):
            with WriteGuardSession(session, guard):
                raise ValueError("boom")

        assert guard.active_writes == 0
        session.close.assert_called_once()

    def test_close_is_idempotent(self):
        """Calling ``close()`` twice does not call ``write_exit`` twice."""
        guard = WritePauseGuard()
        session = MagicMock()

        proxy = WriteGuardSession(session, guard)
        proxy.__enter__()
        assert guard.active_writes == 1

        proxy.close()
        assert guard.active_writes == 0
        # Calling again is a no-op.
        proxy.close()
        assert guard.active_writes == 0

        # The underlying session is still only closed once.
        assert session.close.call_count == 1

    def test_attribute_access_delegates_to_session(self):
        """Non-reserved attributes proxy to the wrapped session."""
        session = MagicMock()
        session.execute = MagicMock(return_value="ok")
        guard = WritePauseGuard()

        proxy = WriteGuardSession(session, guard)
        # Reading attributes goes through __getattr__ to the session.
        result = proxy.execute("SELECT 1")
        assert result == "ok"
        session.execute.assert_called_once_with("SELECT 1")

    def test_reserved_attrs_not_proxied(self):
        """``__getattr__`` rejects the wrapper's own private attributes."""
        session = MagicMock()
        guard = WritePauseGuard()
        proxy = WriteGuardSession(session, guard)

        # Normal attribute lookup still works for the wrapper's own
        # bookkeeping attributes.
        assert proxy._session is session
        assert proxy._guard is guard
        assert proxy._closed is False

        # But ``__getattr__`` itself refuses to delegate the reserved
        # names — it raises AttributeError rather than returning the
        # wrapper's instance attribute. This prevents accidental
        # recursion if a caller ever invokes ``__getattr__`` directly.
        with pytest.raises(AttributeError):
            proxy.__getattr__("_session")
        with pytest.raises(AttributeError):
            proxy.__getattr__("_guard")
        with pytest.raises(AttributeError):
            proxy.__getattr__("__enter__")
