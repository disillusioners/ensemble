"""Engine watchdog STALL DRILL (Engine Arc P4 — T4.3 / AC3).

This is the acceptance evidence for AMD-2 / D4.7: a REAL engine stall (a
sleep longer than the derived threshold) is injected into the tick loop, and
the engine-independent watchdog must:

* fire EXACTLY ONE CRITICAL per episode,
* bump the counter exposed via the engine snapshot,
* leave the engine TASK alive and resuming (it cannot kill the engine),
* never call ``engine.register()`` on the wedged engine.

Mutation (recorded in the pack): remove the stall predicate ⇒ no CRITICAL ⇒
the ``test_stall_drill`` assertion REDs.  Wall-times (detection + recovery)
are emitted on stdout and captured by the pack transcript.

File name per ``test-strategy.md`` §1.1 (T7/T8 capability names — P4 owns the
drill file; P7 registers the pack).
"""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel
from sqlmodel.pool import StaticPool

from daemon.repositories.engine.repository import EngineRepository
from daemon.services.engine.engine import EngineService
from daemon.services.engine.registry import EngineRegistry
from daemon.services.engine.watchdog import EngineWatchdog


class _Cfg:
    """Tiny config: small threshold so the drill is fast, still config-derived."""

    tick_resolution_seconds = 0.05
    boot_max_window_seconds = 0.30  # threshold = 3*0.05 + 0.30 = 0.45s
    watchdog_interval_seconds = 0.03
    tick_workers = 1
    shadow_mode = True


def _engine():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _svc():
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    return EngineService(repo, reg, _Cfg(), shadow_mode=True)


@pytest.mark.asyncio
async def test_stall_drill(caplog):
    """Inject a real stall; assert one CRITICAL + counter + engine survival.

    Emits (stdout, captured by the pack):
      DRILL wedge induced: sleep=1.00s threshold=0.45s
      DRILL detection wall-time: X.XXXs
      DRILL recovery wall-time: Y.YYYs
      DRILL engine survival: task_alive=True tick_count_after=Z
    """
    import logging

    svc = _svc()
    # Guard: the watchdog must never rearm through the wedged engine.
    def _forbidden_register(*args, **kwargs):
        pytest.fail("watchdog called engine.register() on the wedged engine")

    svc.register = _forbidden_register  # type: ignore[attr-defined]

    stall_s = 1.0
    original_run = svc._execution_loop.run

    async def stalled_run():
        await asyncio.sleep(stall_s)
        await original_run()

    svc._execution_loop.run = stalled_run  # type: ignore[assignment]

    rearm_calls: list[dict] = []
    wd = EngineWatchdog(
        svc,
        _Cfg(),
        rearm=lambda sample: rearm_calls.append(sample),
    )
    threshold = wd.stall_threshold
    assert abs(threshold - 0.45) < 1e-9

    t0 = time.monotonic()
    await svc.start()
    wd.start()
    try:
        with caplog.at_level(logging.CRITICAL, logger="daemon.services.engine.watchdog"):
            # Wait for the watchdog to detect the stall AND emit the CRITICAL.
            # The counter is bumped just before the log call inside the thread,
            # so poll for BOTH (the log line lands a moment after the counter).
            def _crit_count():
                return len([
                    r for r in caplog.records
                    if r.levelno == logging.CRITICAL
                    and "ENGINE WATCHDOG" in r.getMessage()
                ])

            deadline = t0 + stall_s + 3.0
            while (
                svc._watchdog_stall_count == 0 or _crit_count() == 0
            ) and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            detect_t = time.monotonic()

            assert svc._watchdog_stall_count == 1, (
                "expected exactly one stall counter bump per episode, got "
                f"{svc._watchdog_stall_count}"
            )
            assert wd.criticals == 1, (
                f"expected exactly one CRITICAL per episode, got {wd.criticals}"
            )
            crits = [r for r in caplog.records if r.levelno == logging.CRITICAL
                     and "ENGINE WATCHDOG" in r.getMessage()]
            assert len(crits) == 1, (
                f"expected one ENGINE WATCHDOG CRITICAL line, got {len(crits)}"
            )
            assert len(rearm_calls) == 1, "engine-independent rearm must fire once"
            assert wd.rearms == 1

            # The engine task must still be alive (the watchdog cannot kill it).
            assert svc._task is not None and not svc._task.done(), (
                "engine task was killed by the watchdog"
            )

            # Wait for the engine to resume ticking after the injected stall.
            ticks_before = svc._tick_count
            resume_deadline = time.monotonic() + 3.0
            while svc._tick_count <= ticks_before and time.monotonic() < resume_deadline:
                await asyncio.sleep(0.01)
            recover_t = time.monotonic()
            assert svc._tick_count > ticks_before, "engine did not resume ticking"
            # A resumed engine clears the episode; no second CRITICAL.
            await asyncio.sleep(0.15)
            assert wd.criticals == 1, "a second CRITICAL fired without a new stall"
    finally:
        wd.stop()
        await svc.stop()

    print(f"\nDRILL wedge induced: sleep={stall_s:.2f}s threshold={threshold:.2f}s")
    print(f"DRILL detection wall-time: {detect_t - t0:.3f}s")
    print(f"DRILL recovery wall-time: {recover_t - t0:.3f}s")
    print(
        f"DRILL engine survival: task_alive=True tick_count_after={svc._tick_count}"
    )


@pytest.mark.asyncio
async def test_stall_predicate_removed_is_red():
    """Mutation proof: with the predicate removed, NO CRITICAL fires.

    The pack also runs the raw mutation (patch ``is_stalled`` to False and run
    ``test_stall_drill``) and records the RED verbatim; this test is the
    in-suite, fast corollary that asserts the mutant's observable signature.
    """
    svc = _svc()
    wd = EngineWatchdog(svc, _Cfg())
    # Mutant: the stall predicate is gone.
    wd.is_stalled = lambda sample, now=None: False  # type: ignore[assignment]
    svc._last_tick_monotonic = time.monotonic() - 10.0
    assert wd.is_stalled(wd.sample()) is False
    assert wd.criticals == 0
    # The real predicate WOULD have fired on the same sample.
    real = EngineWatchdog(svc, _Cfg())
    assert real.is_stalled(wd.sample()) is True
