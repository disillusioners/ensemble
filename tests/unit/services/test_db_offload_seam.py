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
        return 1

    async def run():
        t = threading.current_thread()
        orig = t.name
        t.name = "db-offload-0"
        try:
            with pytest.raises(db_offload.DbOffloadReentrancyError):
                await db_offload.offload(f)
        finally:
            t.name = orig
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
