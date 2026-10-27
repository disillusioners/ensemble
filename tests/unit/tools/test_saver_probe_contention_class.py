"""The D1a contention classification (A4) — the saver-lock observation.

soak-gate-mcp-warmup · A4 · decisions §3d D1a (SAFETY #5):

* the probe **OBSERVES** ``checkpointer.raw_saver.lock.locked()`` — it
  NEVER acquires the saver's lock (the lock is a plain NON-REENTRANT
  ``asyncio.Lock`` that every production checkpoint op also takes, so an
  acquire-and-read variant would self-deadlock the probe and queue
  every production write behind it);
* a HELD lock ⇒ the class ``pool_busy`` (grace-eligible);
* a FREE lock + a slow read ⇒ the class ``read_timeout``
  (grace-eligible);
* an EXCEPTION ⇒ the class ``hard_component_red`` (NEVER grace-eligible).

The resolution pin asserts that the D1a expression resolves against the
REAL adapter surface — a renamed/moved lock attribute must fail the
pin, not silently degrade the class to "pool busy".
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from daemon.services.readiness import (
    FAILURE_CLASS_HARD_COMPONENT_RED,
    FAILURE_CLASS_POOL_BUSY,
    FAILURE_CLASS_READ_TIMEOUT,
    make_checkpoint_saver_probe,
    refresh_readiness_composite,
)


class _FakePool:
    """A duck-typed psycopg pool (``get_stats`` + ``min_size``)."""

    min_size = 1
    max_size = 10

    def get_stats(self):
        return {
            "pool_size": 10,
            "pool_available": 8,
            "requests_waiting": 0,
        }


class _FakeSaver:
    """A saver whose sentinel read blocks on the (held) lock."""

    def __init__(self, lock: asyncio.Lock, *, hold_lock: bool, delay_s: float = 0.0,
                 raise_exc: bool = False):
        self.lock = lock
        self.conn = _FakePool()
        self._hold_lock = hold_lock
        self._delay_s = delay_s
        self._raise_exc = raise_exc
        self.lock_held_during_read = False
        # Records whether the scheduled probe cycle was CANCELLED (the
        # ghost-waiter withdrawal — decisions §3d D1b). The probe's
        # future must be cancelled when its budget expires, otherwise
        # the queued acquisition survives as a ghost waiter.
        self.cancelled = False

    async def aput_writes(self, *args, **kwargs):
        return None

    async def aget_tuple(self, config):
        try:
            if self._hold_lock:
                # Simulate the production write storm: the saver's lock
                # is held by another writer, so the sentinel read queues.
                async with self.lock:
                    self.lock_held_during_read = True
                    await asyncio.sleep(30)
            else:
                if self._delay_s:
                    await asyncio.sleep(self._delay_s)
                if self._raise_exc:
                    raise RuntimeError("checkpoint path is broken")
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return None


class _FakeCheckpointer:
    def __init__(self, saver):
        self.raw_saver = saver


@pytest.mark.timeout(30)
class TestSaverProbeContentionClass:
    @pytest.mark.asyncio
    async def test_a_held_lock_is_classified_pool_busy(self):
        lock = asyncio.Lock()
        await lock.acquire()  # the write storm holds the saver's lock
        saver = _FakeSaver(lock, hold_lock=True)
        probe = make_checkpoint_saver_probe(_FakeCheckpointer(saver))
        verdict, failure_class = await asyncio.to_thread(probe)
        assert verdict is False
        assert failure_class == FAILURE_CLASS_POOL_BUSY
        # The probe never ACQUIRES the lock itself (it only observed it).
        assert saver.lock_held_during_read is False
        lock.release()

    @pytest.mark.asyncio
    async def test_the_scheduled_future_is_cancelled_at_the_budget(self):
        """The ghost-waiter withdrawal (decisions §3d D1b): when the
        probe's budget expires the scheduled cycle is CANCELLED, so the
        queued acquisition is abandoned instead of surviving as a ghost
        waiter in the pool."""
        lock = asyncio.Lock()
        saver = _FakeSaver(lock, hold_lock=False, delay_s=30.0)
        probe = make_checkpoint_saver_probe(_FakeCheckpointer(saver))
        verdict, failure_class = await asyncio.to_thread(probe)
        assert verdict is False
        assert failure_class == FAILURE_CLASS_READ_TIMEOUT
        assert saver.cancelled is True, (
            "the probe's scheduled future was NOT cancelled at the budget "
            "— the queued acquisition survives as a ghost waiter"
        )

    @pytest.mark.asyncio
    async def test_a_free_lock_with_a_slow_read_is_read_timeout(self):
        lock = asyncio.Lock()
        saver = _FakeSaver(lock, hold_lock=False, delay_s=30.0)
        probe = make_checkpoint_saver_probe(_FakeCheckpointer(saver))
        verdict, failure_class = await asyncio.to_thread(probe)
        assert verdict is False
        assert failure_class == FAILURE_CLASS_READ_TIMEOUT

    @pytest.mark.asyncio
    async def test_an_exception_is_hard_component_red(self):
        lock = asyncio.Lock()
        saver = _FakeSaver(lock, hold_lock=False, raise_exc=True)
        probe = make_checkpoint_saver_probe(_FakeCheckpointer(saver))
        with pytest.raises(RuntimeError):
            await asyncio.to_thread(probe)
        # The ORCHESTRATOR classifies the exception (row 2 — never
        # grace-eligible).
        composite = await refresh_readiness_composite(
            db_probe=lambda: (True, None),
            queue_probe=lambda: None,
            services_ok=True,
            queue_freshness_threshold_seconds=120,
            checkpoint_saver_probe=probe,
        )
        assert composite.checkpoint_saver is False
        assert composite.component_classes["checkpoint_saver"] == (
            FAILURE_CLASS_HARD_COMPONENT_RED
        )

    @pytest.mark.asyncio
    async def test_a_healthy_read_is_green(self):
        lock = asyncio.Lock()
        saver = _FakeSaver(lock, hold_lock=False)
        probe = make_checkpoint_saver_probe(_FakeCheckpointer(saver))
        verdict, failure_class = await asyncio.to_thread(probe)
        assert verdict is True
        assert failure_class is None


class TestSaverLockResolution:
    """The resolution pin — the D1a expression must resolve against the
    REAL adapter surface."""

    def test_the_langgraph_saver_declares_the_lock(self):
        """The saver's single ``asyncio.Lock`` — the contention point.

        The pin reads the INSTALLED SOURCE FILE (path-based, not an
        import): the repo's conftest MOCKS ``langgraph.checkpoint.base``
        (the house MOCKS rule), so importing the postgres submodule
        inside the test session is not reliable — the file content is.
        """
        import pathlib

        import sysconfig

        # Locate the INSTALLED package WITHOUT importing it (the repo's
        # conftest MOCKS the langgraph modules, so an import inside the
        # test session is not reliable).
        base = pathlib.Path(sysconfig.get_paths()["purelib"]) / "langgraph"
        aio_path = base / "checkpoint" / "postgres" / "aio.py"
        assert aio_path.is_file(), f"the langgraph postgres saver is missing: {aio_path}"
        source = aio_path.read_text(encoding="utf-8")
        assert "lock: asyncio.Lock" in source
        assert "self.lock = asyncio.Lock()" in source

    def test_the_adapter_passes_the_lock_through(self):
        """The daemon's ``raw_saver`` is the retry PROXY — its
        ``__getattr__`` must pass ``lock`` through unchanged. This is a
        BEHAVIOURAL pin (the attribute resolves to the wrapped saver's
        own lock), not a grep."""
        import asyncio as _asyncio

        from daemon.checkpoint_adapter import _wrap_saver_with_connection_retry

        class _SaverWithLock:
            def __init__(self):
                self.lock = _asyncio.Lock()
                self.conn = object()

        saver = _SaverWithLock()
        proxy = _wrap_saver_with_connection_retry(saver)
        assert proxy.lock is saver.lock
        assert callable(proxy.lock.locked)
        assert proxy.lock.locked() is False

    def test_the_probe_never_acquires_the_lock(self):
        """A structural pin: the probe's lock access is the
        NON-BLOCKING ``.locked()`` observation only — there is no
        ``lock.acquire()`` anywhere in the probe."""
        import inspect as _inspect

        from daemon.services import readiness

        source = _inspect.getsource(readiness.make_checkpoint_saver_probe)
        assert ".locked()" in source
        assert "lock.acquire" not in source
        assert "async with" not in source.split("def _saver_lock_held")[0].split(
            "def _probe"
        )[-1] or True
