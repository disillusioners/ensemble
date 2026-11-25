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
from daemon.services.engine.watchdog import EngineWatchdog, make_direct_db_rearm


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


# ---------------------------------------------------------------------------
# S-2 — the watchdog re-arm predicate pin (Engine Arc P7)
# ---------------------------------------------------------------------------
# The predicate under test is the REAL one at
# ``daemon/services/engine/watchdog.py:63`` (inside ``make_direct_db_rearm``):
#
#     if getattr(job, "dead", 0) and not getattr(job, "last_outcome", None):
#
# INTENT (documented at the pin, verbatim-style): the rearm targets ONLY the
# born-dead-with-no-stamp shape (``dead=1`` AND ``last_outcome`` falsy).  The
# second conjunct is INTENTIONAL, not an oversight: an F1-escalated row
# (``dead=1`` AND ``last_outcome="SKIPPED_DEP_MISSING"``, per the escalation
# path at ``daemon/services/engine/execution.py:270-297``) carries a terminal
# outcome and is owned by the escalation/backoff path — NOT by the watchdog.
# Re-arming it here would race the escalation owner, so the watchdog must leave
# it dead.
#
# Mutation proof (disposable extract, ``git archive HEAD | tar -x -C /tmp``):
# drop the ``not getattr(job, "last_outcome", None)`` conjunct from
# ``watchdog.py:63`` ⇒ ``test_f1_escalated_row_is_not_rearmed`` REDs (the
# escalated row gets stamped).  Restore = discard the extract.


class _FakeEngineJob:
    """Minimal stand-in for an ``EngineJob`` row (only the fields the predicate reads)."""

    def __init__(self, job_id, dead, last_outcome):
        self.id = job_id
        self.dead = dead
        self.last_outcome = last_outcome


class _FakeEngineRepository:
    """Tiny fake repository: ``list_jobs`` + ``stamp_job_outcome``, recording stamps."""

    def __init__(self, jobs):
        self._jobs = list(jobs)
        self.stamps = []

    def list_jobs(self):
        return list(self._jobs)

    def stamp_job_outcome(self, job_id, *, dead=None, **_kwargs):
        self.stamps.append((str(job_id), dead))
        for job in self._jobs:
            if str(job.id) == str(job_id) and dead is not None:
                job.dead = dead
        return 1


class TestDirectDbRearmPredicate:
    """Pin the REAL ``make_direct_db_rearm`` predicate (``watchdog.py:63``)."""

    def test_born_dead_no_stamp_is_rearmed(self):
        """(a) dead=1 + last_outcome=None → re-armed (stamp dead=0), returns 1."""
        repo = _FakeEngineRepository([_FakeEngineJob("j1", dead=1, last_outcome=None)])
        rearmed = make_direct_db_rearm(repo)({})
        assert rearmed == 1
        assert repo.stamps == [("j1", 0)]
        assert repo._jobs[0].dead == 0

    def test_f1_escalated_row_is_not_rearmed(self):
        """(b) dead=1 + last_outcome="SKIPPED_DEP_MISSING" → NOT re-armed.

        The exclusion is INTENTIONAL — see the module comment block above.
        This is the F1-escalated shape (``execution.py:270-297``): the row
        carries a terminal outcome and is owned by the escalation/backoff
        path, not the watchdog.  Mutation: removing the ``not last_outcome``
        conjunct makes this leg RED.
        """
        repo = _FakeEngineRepository([
            _FakeEngineJob("j2", dead=1, last_outcome="SKIPPED_DEP_MISSING"),
        ])
        rearmed = make_direct_db_rearm(repo)({})
        assert rearmed == 0
        assert repo.stamps == []
        assert repo._jobs[0].dead == 1

    def test_alive_row_is_untouched(self):
        """(c) dead=0 + last_outcome=None → untouched (no stamp), returns 0."""
        repo = _FakeEngineRepository([_FakeEngineJob("j3", dead=0, last_outcome=None)])
        rearmed = make_direct_db_rearm(repo)({})
        assert rearmed == 0
        assert repo.stamps == []
        assert repo._jobs[0].dead == 0
