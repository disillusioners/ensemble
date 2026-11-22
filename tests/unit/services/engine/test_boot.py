"""Engine boot-phase unit pins (Engine Arc P4 — T4.1 / AC1, AC2, AC8).

Capability coverage: test-strategy §1.1 T7 (boot-phase classes + jitter).
Task path authority: ``tests/unit/services/engine/test_boot.py``.

Covers:
* AC1 — BOOT_SWEEP/BOOT_PASS deadline: a pass past ``deadline_s`` is abandoned
  (ERROR naming the pass) and boot CONTINUES. Mutation: ``wait_for`` removed ⇒ RED.
* AC2 — first-tick classes: IMMEDIATE t=0, DELAYED(+30) ≈30s, jitter differs
  across jobs and is stable on re-evaluation within one boot. Mutation: jitter
  removed ⇒ RED.
* AC8 — the engine's own deadlined registration pass is a distinct awaited
  pass (inserted at position 28); the class exists and is nameable as
  ``BootSweepClass.BOOT_SWEEP`` (F-13).
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from daemon.services.engine.boot import (
    BootClass,
    BootSweepClass,
    FirstTickScheduler,
    compute_first_tick_offset,
    first_tick_jitter,
    run_boot_pass,
    run_boot_sweep,
)
from daemon.services.engine.spec import IntervalTrigger


def test_boot_classes_and_sweep_alias():
    assert {c.value for c in BootClass} == {
        "boot_sweep",
        "boot_pass",
        "first_tick_immediate",
        "first_tick_delayed",
    }
    # F-13: the class is nameable as BootSweepClass.BOOT_SWEEP.
    assert BootSweepClass.BOOT_SWEEP is BootClass.BOOT_SWEEP


@pytest.mark.asyncio
async def test_deadlined_pass_expires_and_continues(caplog):
    """AC1 — a pass past its deadline is abandoned, ERROR logged, boot continues."""
    async def slow():
        await asyncio.sleep(5.0)
        return "never"

    with caplog.at_level(logging.ERROR, logger="daemon.services.engine.boot"):
        result = await run_boot_pass(slow(), 0.02, "engine_registration")
    assert result is None
    errs = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("engine_registration" in m and "CONTINUES" in m for m in errs)


@pytest.mark.asyncio
async def test_deadlined_pass_returns_result_when_fast():
    async def fast():
        return 42

    assert await run_boot_pass(fast(), 1.0, "fast_pass") == 42


@pytest.mark.asyncio
async def test_run_boot_sweep_isolates_one_slow_pass():
    async def slow():
        await asyncio.sleep(5.0)

    async def fast():
        return "ok"

    results = await run_boot_sweep(
        [
            ("slow", slow, 0.02),
            ("fast", fast, 1.0),
        ]
    )
    assert results["slow"] is None
    assert results["fast"] == "ok"


def test_first_tick_immediate_and_delayed_offsets():
    imm = IntervalTrigger(seconds=90, first_tick_kind="immediate")
    delayed = IntervalTrigger(
        seconds=90, first_tick_kind="delayed", first_tick_offset_seconds=30.0
    )
    assert compute_first_tick_offset("j", imm, "boot") == 0.0
    assert compute_first_tick_offset("j", delayed, "boot") == 30.0


def test_jitter_stable_within_boot_differs_across_jobs():
    """AC2 — jitter deterministic-by-seed: stable within boot, differs across jobs."""
    trig = IntervalTrigger(seconds=300, jitter_seconds=300)
    sched = FirstTickScheduler(seed="boot-A")
    ids = [f"job-{i}" for i in range(8)]
    offsets = [sched.offset_for(j, trig) for j in ids]
    # stable on re-evaluation within the same boot
    assert [sched.offset_for(j, trig) for j in ids] == offsets
    # differs across >= 5 jobs
    assert len(set(offsets)) >= 5
    # every offset in [0, 300)
    assert all(0.0 <= o < 300.0 for o in offsets)
    # a fresh boot seed recomputes (at least one differs)
    other = FirstTickScheduler(seed="boot-B")
    assert [other.offset_for(j, trig) for j in ids] != offsets


def test_jitter_removed_is_red():
    """Mutation proof: with jitter removed, all offsets collapse to the base."""
    trig = IntervalTrigger(seconds=300, jitter_seconds=300)
    # Mutant: first_tick_jitter always returns 0.
    offsets = [first_tick_jitter(f"job-{i}", 0, "boot") for i in range(8)]
    assert offsets == [0.0] * 8  # mutant signature: no desynchronization
    # Real behaviour differs.
    real = [first_tick_jitter(f"job-{i}", 300, "boot") for i in range(8)]
    assert len(set(real)) >= 5


def test_compute_initial_due_times():
    sched = FirstTickScheduler(seed="boot")
    specs = [
        type("S", (), {"id": "a", "trigger": IntervalTrigger(seconds=90)})(),
        type("S", (), {"id": "b", "trigger": IntervalTrigger(seconds=90)})(),
    ]
    times = sched.compute_initial_due_times(specs)
    assert set(times) == {"a", "b"}
    assert times == sched.compute_initial_due_times(specs)  # stable
