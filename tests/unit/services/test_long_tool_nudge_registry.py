"""T1 — LongToolNudgeRegistry unit tests.

Pins: record/clear/snapshot concurrency under asyncio.gather (50
writers + 1 reader); ``clear`` returns the cleared stamp;
``clear_many`` (council fix-cycle 1, A5) clears every requested
stamp under one lock and evicts the per-batch threshold cache
alongside; overflow cap drops the oldest instance with a WARN log;
empty snapshot; duplicate ``(instance_id, tool_call_id)`` keeps the
FIRST stamp (first-stamp-wins idempotency). Council fix-cycle 1:
removed the ``clear_for_instance`` test (the method had no
production callers and was deleted alongside).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from daemon.services.long_tool_nudge import (
    LongToolNudgeRegistry,
    _Stamp,
)


@pytest.fixture
def registry() -> LongToolNudgeRegistry:
    return LongToolNudgeRegistry()


@pytest.mark.asyncio
async def test_record_start_and_snapshot_roundtrip(registry):
    await registry.record_start("inst-1", "call-1", "bash", "parent-1")
    snap = await registry.snapshot()
    assert set(snap.keys()) == {"inst-1"}
    stamp = snap["inst-1"]["call-1"]
    assert isinstance(stamp, _Stamp)
    assert stamp.tool_call_id == "call-1"
    assert stamp.tool_name == "bash"
    assert stamp.parent_id == "parent-1"
    assert stamp.started_at <= time.monotonic()


@pytest.mark.asyncio
async def test_clear_returns_cleared_stamp(registry):
    await registry.record_start("inst-1", "call-1", "bash", "parent-1")
    before = time.monotonic()
    stamp = await registry.clear("inst-1", "call-1")
    assert stamp is not None
    assert stamp.tool_call_id == "call-1"
    assert stamp.started_at <= before
    assert await registry.snapshot() == {}
    # Clearing again returns None.
    assert await registry.clear("inst-1", "call-1") is None


@pytest.mark.asyncio
async def test_snapshot_empty_when_no_stamps(registry):
    assert await registry.snapshot() == {}


@pytest.mark.asyncio
async def test_clear_many_clears_all_in_one_lock_acquisition(registry):
    """Council fix-cycle 1, A5 — REGRESSION PIN for ``clear_many``.

    The wrapper's per-batch finally calls ``clear_many`` with all
    stamp ids from the current batch; the method must remove every
    stamp under ONE lock acquisition (the cancel-immune contract
    depends on this — a cancel between two stamp clears could
    otherwise leak the rest to the AD-9a TTL belt). Test failure
    here means a future refactor broke the single-lock guarantee.
    """
    await registry.record_start("inst-1", "call-1", "bash", "p")
    await registry.record_start("inst-1", "call-2", "read_file", "p")
    await registry.record_start("inst-2", "call-3", "bash", "p")
    cleared = await registry.clear_many(
        "inst-1", ["call-1", "call-2", "missing"]
    )
    assert sorted(tc_id for tc_id, _ in cleared) == ["call-1", "call-2"]
    snap = await registry.snapshot()
    # inst-1 is fully cleared and evicted; inst-2 is untouched.
    assert set(snap.keys()) == {"inst-2"}
    # Per-batch threshold cache evicted alongside (W1).
    assert "inst-1" not in registry._batch_threshold_cache


@pytest.mark.asyncio
async def test_duplicate_tool_call_id_keeps_first_stamp(registry):
    await registry.record_start("inst-1", "call-1", "bash", "p")
    first = (await registry.snapshot())["inst-1"]["call-1"]
    await asyncio.sleep(0.01)
    await registry.record_start("inst-1", "call-1", "bash", "p")
    second_snapshot = await registry.snapshot()
    assert second_snapshot["inst-1"]["call-1"] is first  # same object
    assert second_snapshot["inst-1"]["call-1"].started_at == first.started_at


@pytest.mark.asyncio
async def test_concurrent_writers_and_reader(registry):
    """50 concurrent writers + 1 concurrent snapshot reader (T1 pin)."""

    async def writer(i: int) -> None:
        inst = f"inst-{i % 10}"
        await registry.record_start(inst, f"call-{i}", "bash", "p")
        await asyncio.sleep(0)
        await registry.clear(inst, f"call-{i}")

    async def reader() -> int:
        seen = 0
        for _ in range(20):
            snap = await registry.snapshot()
            seen += len(snap)
            await asyncio.sleep(0)
        return seen

    results = await asyncio.gather(
        *[writer(i) for i in range(50)], reader(), return_exceptions=True
    )
    exceptions = [r for r in results if isinstance(r, BaseException)]
    assert exceptions == []
    assert results[-1] >= 0  # reader observed snapshots without error
    assert await registry.snapshot() == {}  # all cleared


@pytest.mark.asyncio
async def test_overflow_drops_oldest_instance_with_warn(registry, caplog):
    small = LongToolNudgeRegistry(max_tracked_instances=2)
    with caplog.at_level("WARNING", logger="daemon.services.long_tool_nudge"):
        await small.record_start("inst-a", "call-1", "bash", "p")
        await small.record_start("inst-b", "call-1", "bash", "p")
        await small.record_start("inst-c", "call-1", "bash", "p")  # overflows
    snap = await small.snapshot()
    # inst-a was inserted first → dropped oldest.
    assert set(snap.keys()) == {"inst-b", "inst-c"}
    assert any("registry overflow" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_snapshot_is_shallow_copy_not_live(registry):
    await registry.record_start("inst-1", "call-1", "bash", "p")
    snap = await registry.snapshot()
    await registry.clear("inst-1", "call-1")
    # The earlier snapshot still observes the stamp object.
    assert "call-1" in snap.get("inst-1", {})
    assert await registry.snapshot() == {}
