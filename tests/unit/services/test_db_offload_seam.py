"""Unit pins for the ``daemon.services.db_offload`` seam (wi-3f5bea95 P2).

Covers value/exception propagation, contextvar survival, thread naming,
gate accounting under cancellation, reentrancy containment, drain
semantics, FIFO/barging, and the LIMITS_-knob wiring. In-process only —
no DB, no network.
"""
import asyncio
import threading
import time

import pytest

from daemon.services import db_offload


def test_returns_callee_value():
    db_offload.configure(max_workers=2, drain_bound_s=5)

    def f(x, y=0):
        return x + y + 1

    async def run():
        r = await db_offload.offload(f, 5, y=3)
        assert r == 9
        await db_offload.shutdown()

    asyncio.run(run())


def test_propagates_exception_type():
    db_offload.configure(max_workers=2, drain_bound_s=5)

    def boom():
        raise ValueError("x")

    async def run():
        with pytest.raises(ValueError):
            await db_offload.offload(boom)
        await db_offload.shutdown()

    asyncio.run(run())


def test_kwargs_reach_callee():
    db_offload.configure(max_workers=2, drain_bound_s=5)

    def kwonly(*, a, b):
        return a - b

    async def run():
        r = await db_offload.offload(kwonly, a=10, b=4)
        assert r == 6
        await db_offload.shutdown()

    asyncio.run(run())


def test_contextvar_survives():
    import contextvars

    cv = contextvars.ContextVar("t", default=None)
    db_offload.configure(max_workers=2, drain_bound_s=5)

    def worker():
        return cv.get()

    async def run():
        token = cv.set("hello")
        try:
            r = await db_offload.offload(worker)
            assert r == "hello"
        finally:
            cv.reset(token)
        await db_offload.shutdown()

    asyncio.run(run())


def test_thread_name_prefix():
    db_offload.configure(max_workers=2, drain_bound_s=5)
    seen = {}

    def worker():
        seen["name"] = threading.current_thread().name
        return True

    async def run():
        await db_offload.offload(worker)
        assert seen["name"].startswith("db-offload")
        await db_offload.shutdown()

    asyncio.run(run())


def test_saturation_defers():
    db_offload.configure(max_workers=2, drain_bound_s=5)

    def slow():
        time.sleep(0.2)
        return "done"

    async def run():
        results = await asyncio.gather(*[db_offload.offload(slow) for _ in range(5)])
        assert all(r == "done" for r in results)
        await db_offload.shutdown()

    asyncio.run(run())


def test_reentrancy_raises():
    db_offload.configure(max_workers=2, drain_bound_s=5)

    def f():
        # nested seam call via asyncio.run inside the worker (D-52 vector)
        async def inner():
            return await db_offload.offload(lambda: 1)
        return asyncio.run(inner())

    async def run():
        with pytest.raises(db_offload.DbOffloadReentrancyError):
            await db_offload.offload(f)
        await db_offload.shutdown()

    asyncio.run(run())


def test_baseexception_contained():
    db_offload.configure(max_workers=2, drain_bound_s=5)

    def boom():
        raise KeyboardInterrupt()

    async def run():
        with pytest.raises(db_offload.DbOffloadError):
            await db_offload.offload(boom)
        await db_offload.shutdown()

    asyncio.run(run())


def test_cancelled_caller_propagates():
    db_offload.configure(max_workers=1, drain_bound_s=5)

    def slow():
        time.sleep(0.5)
        return "done"

    async def run():
        task = asyncio.create_task(db_offload.offload(slow))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.6)
        gate = db_offload.get_gate()
        assert gate.permits == 1 and gate.inflight == 0 and gate.waiters == 0
        await db_offload.shutdown()

    asyncio.run(run())


def test_constant_names():
    from daemon.constants import DB_OFFLOAD_MAX_WORKERS_DEFAULT, DB_OFFLOAD_DRAIN_BOUND_S

    assert DB_OFFLOAD_MAX_WORKERS_DEFAULT == 6
    assert DB_OFFLOAD_DRAIN_BOUND_S == 10


def test_ix_no_reserve_under_cancellation_storm():
    async def run():
        db_offload.configure(max_workers=2, drain_bound_s=5)
        gate = db_offload.get_gate()
        # occupy both workers
        await gate.acquire()
        await gate.acquire()
        assert gate.permits == 0 and gate.inflight == 2
        # park one waiter
        waiter_task = asyncio.create_task(gate.acquire())
        await asyncio.sleep(0.1)
        assert gate.waiters == 1, gate.waiters
        # cancel the parked waiter (storm of one)
        waiter_task.cancel()
        try:
            await waiter_task
        except asyncio.CancelledError:
            pass
        # NO-RESERVE: cancelled waiter held no permit
        assert gate.permits == 0
        assert gate.inflight == 2
        assert len(gate.waiter_deque) == 0
        # release both
        gate.do_release()
        gate.do_release()
        assert gate.permits == 2 and gate.inflight == 0
        await db_offload.shutdown()

    asyncio.run(run())


def test_x_during_flight_not_released():
    db_offload.configure(max_workers=1, drain_bound_s=5)

    def slow():
        time.sleep(0.5)
        return "done"

    async def run():
        task = asyncio.create_task(db_offload.offload(slow))
        await asyncio.sleep(0.1)
        # cancel while in flight
        task.cancel()
        # during-flight: thread still running -> inflight still 1, permit not released
        await asyncio.sleep(0)
        gate = db_offload.get_gate()
        # permit must not have been released yet (thread still running)
        assert gate.inflight == 1, gate.inflight
        assert gate.permits == 0, gate.permits
        await asyncio.sleep(0.6)
        gate = db_offload.get_gate()
        assert gate.permits == 1 and gate.inflight == 0
        await db_offload.shutdown()

    asyncio.run(run())


def test_xii_subprocess_knob():
    """W-2: drive the real LIMITS_ -> LimitsConfig -> configure chain."""
    import os, subprocess, sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    assert (repo_root / "daemon" / "config.py").is_file(), repo_root

    code = (
        "import os, sys, asyncio\n"
        "sys.path.insert(0, os.environ['REPO_ROOT'])\n"
        "os.environ.setdefault('ENSEMBLE_SELF_ENV', 'dev')\n"
        "from daemon.config import LimitsConfig\n"
        "cfg = LimitsConfig()  # reads LIMITS_DB_OFFLOAD_MAX_WORKERS from env\n"
        "from daemon.services import db_offload\n"
        "async def main():\n"
        "    db_offload.configure(max_workers=cfg.db_offload_max_workers, drain_bound_s=cfg.db_offload_drain_bound_s)\n"
        "    print('WORKERS', db_offload.get_gate().workers)\n"
        "    await db_offload.shutdown()\n"
        "asyncio.run(main())\n"
    )
    env = dict(os.environ)
    env["LIMITS_DB_OFFLOAD_MAX_WORKERS"] = "3"
    env["REPO_ROOT"] = str(repo_root)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env,
        cwd=str(repo_root),
    )
    assert "WORKERS 3" in proc.stdout, proc.stdout + proc.stderr


def test_xiii_drain_waiter_failure():
    db_offload.configure(max_workers=1, drain_bound_s=5)

    def slow():
        time.sleep(0.5)
        return "done"

    async def run():
        # occupy the single worker
        holder = asyncio.create_task(db_offload.offload(slow))
        await asyncio.sleep(0.1)
        # park a waiter
        waiter = asyncio.create_task(db_offload.offload(slow))
        await asyncio.sleep(0.1)
        gate = db_offload.get_gate()
        assert gate.waiters == 1
        # drain while waiter parked
        await db_offload.shutdown()
        # waiter should receive defined error, not hang
        try:
            await waiter
        except db_offload.DbOffloadClosedError:
            pass
        except Exception as e:
            # acceptable: any defined error
            assert "closed" in str(e).lower() or "drain" in str(e).lower()
        await holder

    asyncio.run(run())


def test_barging_repro_fifo():
    """Barging repro (C2): parked waiter admitted, never rejected, FIFO holds."""
    async def run():
        db_offload.configure(max_workers=1, drain_bound_s=5)
        gate = db_offload.get_gate()
        # occupy the single worker
        await gate.acquire()
        # park two waiters (FIFO order)
        w1 = asyncio.create_task(gate.acquire())
        w2 = asyncio.create_task(gate.acquire())
        await asyncio.sleep(0.1)
        assert gate.waiters == 2
        # create the fresh acquirer BEFORE do_release so the pre-fix path REDs
        fresh = asyncio.create_task(gate.acquire())
        # release one permit (transfer) — should wake w1, NOT admit a fresh acquirer
        gate.do_release()
        await asyncio.sleep(0)
        # w1 must be admitted (never rejected)
        await w1
        # fresh + w2 must be parked (FIFO: w2 before fresh)
        assert gate.waiters == 2, gate.waiters
        # admit w2 then fresh in order
        gate.do_release()
        await w2
        gate.do_release()
        await fresh
        gate.do_release()
        assert gate.permits == 1 and gate.inflight == 0 and gate.waiters == 0
        await db_offload.shutdown()

    asyncio.run(run())


def test_cancel_after_wake_returns_token():
    """CRITICAL-1: cancel-after-wake must return the token, not drop it."""
    async def run():
        db_offload.configure(max_workers=1, drain_bound_s=5)
        gate = db_offload.get_gate()
        # occupy the single worker
        await gate.acquire()
        # park a waiter
        waiter_task = asyncio.create_task(gate.acquire())
        await asyncio.sleep(0.05)
        assert gate.waiters == 1
        # release (transfer) — wakes waiter_task's future
        gate.do_release()
        # cancel in the micro-gap before resumption
        waiter_task.cancel()
        try:
            await waiter_task
        except asyncio.CancelledError:
            pass
        # token must be returned: either transferred or freed
        # a subsequent acquire must complete (no hang, no wedged state)
        async def probe():
            await gate.acquire()
            gate.do_release()
            return "ok"
        res = await asyncio.wait_for(probe(), timeout=2.0)
        assert res == "ok"
        assert gate.permits + gate.inflight + getattr(gate, 'in_transit', 0) == gate.workers
        await db_offload.shutdown()

    asyncio.run(run())


def test_bounded_capacity_sweep():
    """CRITICAL-1: ~260 trials, zero permanent capacity loss."""
    async def run():
        W = 2
        db_offload.configure(max_workers=W, drain_bound_s=5)
        gate = db_offload.get_gate()
        losses = 0
        for _ in range(260):
            # occupy all workers
            for _ in range(W):
                await gate.acquire()
            # park a waiter
            async def waiter_coro():
                await gate.acquire()
                gate.do_release()
                return "done"
            wt = asyncio.create_task(waiter_coro())
            await asyncio.sleep(0)
            # release one (transfer) then cancel the woken waiter
            gate.do_release()
            wt.cancel()
            try:
                await wt
            except (asyncio.CancelledError, Exception):
                pass
            # release remaining holders
            for _ in range(W):
                gate.do_release()
            # invariant check
            if gate.permits + gate.inflight + gate.in_transit != W:
                losses += 1
            # probe: a fresh acquire must complete
            await asyncio.wait_for(gate.acquire(), timeout=1.0)
            gate.do_release()
        assert losses == 0, f"permanent losses: {losses}"
        await db_offload.shutdown()

    asyncio.run(run())


def test_cancel_propagates_callee_not_called():
    """DISCRIMINATOR (cycle-3 CRITICAL): a cancelled parked waiter must (a)
    propagate CancelledError to the caller, (b) NOT have its callee submitted,
    and (c) leave _inflight == real running work."""
    running = {"n": 0}

    def counting_callee():
        running["n"] += 1
        time.sleep(0.1)
        running["n"] -= 1
        return "done"

    async def run():
        db_offload.configure(max_workers=1, drain_bound_s=5)
        gate = db_offload.get_gate()
        # occupy the single slot directly (no offload; keeps inflight==1
        # without a holder whose done-callback would race the cancel)
        await gate.acquire()
        # park a waiter via the PUBLIC offload() API
        cancelled_task = asyncio.create_task(db_offload.offload(counting_callee))
        await asyncio.sleep(0.05)
        assert gate.waiters == 1
        # release (transfer) then cancel in the SAME tick: the post-wake
        # branch is the only path that can swallow.
        gate.do_release()
        cancelled_task.cancel()
        try:
            await cancelled_task
            raise AssertionError("CancelledError did NOT propagate (swallowed)")
        except asyncio.CancelledError:
            pass

        # (b) the cancelled task's callee must NOT have been submitted
        assert running["n"] == 0, (
            f"callee ran {running['n']}x for a cancelled task (swallowed)"
        )

        # (c) submit a probe; _inflight must equal real running work
        probe = asyncio.create_task(db_offload.offload(counting_callee))
        await asyncio.sleep(0.02)
        assert gate.inflight == running["n"], (
            f"_inflight={gate.inflight} but real running={running['n']}"
        )
        await probe
        gate.do_release()
        assert gate.permits + gate.inflight + getattr(gate, 'in_transit', 0) == gate.workers
        await db_offload.shutdown()

    asyncio.run(run())
