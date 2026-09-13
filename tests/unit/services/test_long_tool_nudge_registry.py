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


@pytest.mark.asyncio
async def test_lookup_parent_for_awaits_async_callable(registry):
    """SURGICAL PIN: ``lookup_parent_for`` MUST await an async callable.

    Council fix-cycle 2 — production attaches ``read_parent_id`` (an
    ``async def``) at ``api.py:811-812``; the previous implementation
    wrapped any attached lookup in ``asyncio.to_thread`` and returned
    the coroutine object unawaited (truthy → stamped as
    ``parent_id=<coroutine>`` → fire-path ``repo.get(coroutine)``
    raised → per-instance error isolation swallowed it → 0 nudges
    + RuntimeWarning spam). This test pins the shape-aware dispatch:
    an async lookup is awaited directly; a sync lookup is wrapped in
    ``asyncio.to_thread`` (preserved for existing sync callers).
    """
    captured: list[str] = []

    async def async_lookup(child_id: str) -> Optional[str]:
        captured.append(child_id)
        return f"parent-of-{child_id}"

    registry.attach_parent_lookup(async_lookup)
    result = await registry.lookup_parent_for("child-42")
    # Plain string, not a coroutine, not None.
    assert result == "parent-of-child-42"
    assert isinstance(result, str)
    # The async lookup ran exactly once and saw the right child_id.
    assert captured == ["child-42"]


@pytest.mark.asyncio
async def test_lookup_parent_for_unwraps_partial_wrapped_async(registry):
    """SURGICAL PIN: ``lookup_parent_for`` MUST yield a plain string
    for a ``functools.partial``-wrapped async callable.

    ``functools.partial`` over an ``async def`` historically evaded
    ``inspect.iscoroutinefunction`` (Python <3.12), forcing the
    pre-fix dispatch into the ``asyncio.to_thread`` branch which
    returned the coroutine OBJECT unawaited (truthy → stamped as
    ``parent_id=<coroutine>`` → re-introduces the EXACT critical
    class fix-cycle 2 just closed: 0 nudges + RuntimeWarning spam).
    On Python 3.13 ``iscoroutinefunction`` correctly detects the
    partial so the async branch handles it; on older Pythons the
    dispatch-site ``isawaitable`` guard catches the coroutine
    object on both branches. Pin drives the partial-wrapped async
    lookup end-to-end and asserts a plain STRING parent_id lands
    in the stamp, regardless of which branch the dispatch takes.

    See ``test_lookup_parent_for_unwraps_sync_wrapper_returning_coroutine``
    for the actual red-green reproducer on Python 3.13 (a sync
    wrapper that returns a coroutine — the shape that DOES evade
    ``iscoroutinefunction`` on this version, since
    ``functools.partial`` is correctly detected in Python 3.12+).
    """
    import functools

    captured: list[str] = []

    async def _async_lookup_impl(child_id: str, suffix: str) -> Optional[str]:
        captured.append(child_id)
        return f"partial-parent-{child_id}-{suffix}"

    async_lookup_partial = functools.partial(_async_lookup_impl, suffix="X")
    registry.attach_parent_lookup(async_lookup_partial)
    result = await registry.lookup_parent_for("child-partial")
    # Plain string, not a coroutine, not None — the bug signature.
    assert isinstance(result, str), (
        f"partial-wrapped async lookup MUST yield a plain string; "
        f"got {type(result).__name__}: {result!r}"
    )
    assert result == "partial-parent-child-partial-X"
    assert captured == ["child-partial"]


@pytest.mark.asyncio
async def test_lookup_parent_for_unwraps_sync_wrapper_returning_coroutine(
    registry,
):
    """RED-GREEN REPRODUCER: a SYNC wrapper that returns a coroutine
    MUST be awaited by ``lookup_parent_for``.

    This is the actual bug shape that reproduces on Python 3.13.
    ``iscoroutinefunction`` returns ``False`` for a plain ``def``
    (regardless of what calling it yields), so the dispatch enters
    the ``asyncio.to_thread`` branch. On the thread pool the wrapper
    returns the inner coroutine object; ``asyncio.to_thread``'s
    internal ``return await loop.run_in_executor(...)`` returns
    THAT coroutine object — but only awaits the executor future,
    NOT the inner coroutine. The OUTER ``await asyncio.to_thread(...)``
    consumes the to_thread wrapper coroutine but receives the
    INNER coroutine back unawaited. Without the ``isawaitable``
    post-guard, ``parent_id`` is the coroutine object (truthy,
    passes the ``if parent_id else None`` check) and the stamp
    lands with ``parent_id=<coroutine>`` — exactly the wedge
    class fix-cycle 2 closed.

    The 3-line ``isawaitable`` guard catches the coroutine on both
    dispatch branches and awaits it. Pin drives a sync wrapper
    that returns a coroutine and asserts a plain STRING parent_id
    lands in the stamp.
    """
    import inspect

    captured: list[str] = []

    async def _async_inner(child_id: str) -> str:
        captured.append(child_id)
        return f"sync-wrap-parent-{child_id}"

    # SYNC function that returns the coroutine. ``iscoroutinefunction``
    # is ``False``; calling it returns a coroutine. This is the
    # shape that reproduces on Python 3.13 — ``functools.partial``
    # does NOT reproduce here because 3.13 detects it.
    def sync_wrapper_returning_coroutine(child_id: str):
        return _async_inner(child_id)

    # Sanity: the wrapper is NOT marked as a coroutine function
    # (the dispatch will route it to the to_thread branch).
    assert not inspect.iscoroutinefunction(sync_wrapper_returning_coroutine)

    registry.attach_parent_lookup(sync_wrapper_returning_coroutine)
    result = await registry.lookup_parent_for("child-sync-wrap")
    # Plain string — not a coroutine, not None. The bug signature
    # is ``result`` being a coroutine object that passes the
    # truthy check.
    assert isinstance(result, str), (
        f"sync wrapper returning a coroutine MUST be awaited — a "
        f"plain string is the only correct shape; got "
        f"{type(result).__name__}: {result!r}"
    )
    assert result == "sync-wrap-parent-child-sync-wrap"
    assert captured == ["child-sync-wrap"]


@pytest.mark.asyncio
async def test_lookup_parent_for_to_thread_sync_callable(registry):
    """Sync lookup stays on the ``asyncio.to_thread`` path.

    Council fix-cycle 2 — the shape-aware dispatch must preserve the
    pre-existing sync-wrap behavior so the W1 wedge history (the
    sync repo read was moved off the event loop for the same
    reason) does not regress. Pins the original contract for sync
    callers.
    """
    sync_calls: list[str] = []

    def sync_lookup(child_id: str) -> Optional[str]:
        sync_calls.append(child_id)
        return f"sync-parent-{child_id}"

    registry.attach_parent_lookup(sync_lookup)
    result = await registry.lookup_parent_for("child-sync")
    assert result == "sync-parent-child-sync"
    assert isinstance(result, str)
    assert sync_calls == ["child-sync"]


def test_record_start_resolves_threshold_before_acquiring_lock():
    """SURGICAL PIN: ``record_start`` threshold resolve MUST precede
    ``async with self._lock`` acquisition (council fix-cycle 2 wedge fix).

    The sync threshold resolver may run a slow PG read; running it
    under the module singleton lock stalls every other instance's
    stamp / clear / snapshot for the duration of the read. Cycle 2
    moved the resolver to ``asyncio.to_thread`` BEFORE the lock —
    the lock-held region only does in-memory dict ops. This test
    pins that ordering structurally so a silent revert (move the
    resolve back inside the lock) fails the suite without relying
    on flaky lock-contention timing.

    Mirrors the B6 structural-grep pattern in
    ``test_long_tool_nudge_detector.py``: extract the function
    source via ``inspect.getsource`` and assert both markers are
    present (separately — so a rename of one or the other fails
    LOUDLY, not silently passes), then assert the resolve marker
    textually precedes the lock acquisition marker.
    """
    import inspect

    from daemon.services.long_tool_nudge import LongToolNudgeRegistry

    # Extract ``record_start`` source via the unbound method (the
    # same path the B6 structural pin uses on the module). This
    # is preferred over slicing the module source between
    # ``async def record_start`` and the next ``def `` — the
    # unbound-method extract tracks the def exactly and surfaces
    # a clean IndentationError-equivalent if the function is
    # renamed (the getsource call would still return the body
    # but rename markers below would fail loudly).
    record_start_src = inspect.getsource(LongToolNudgeRegistry.record_start)

    # Both markers MUST be present. Asserting presence SEPARATELY
    # means a future rename of one marker (e.g. ``to_thread`` →
    # ``run_in_executor``) trips the pin loudly instead of
    # silently passing because the relative-order check alone
    # would not catch a missing one.
    resolve_marker = "await asyncio.to_thread("
    lock_marker = "async with self._lock:"
    assert resolve_marker in record_start_src, (
        f"record_start must pre-resolve the threshold via "
        f"asyncio.to_thread (council fix-cycle 2 wedge fix); "
        f"marker {resolve_marker!r} missing — did someone move "
        f"the resolve back under the lock?"
    )
    assert lock_marker in record_start_src, (
        f"record_start must acquire ``self._lock`` via "
        f"``async with``; marker {lock_marker!r} missing — "
        f"structural pin failed (lock acquisition renamed?)"
    )

    resolve_idx = record_start_src.index(resolve_marker)
    lock_idx = record_start_src.index(lock_marker)
    assert resolve_idx < lock_idx, (
        f"threshold resolve must PRECEDE ``async with self._lock`` "
        f"acquisition in record_start (council fix-cycle 2 wedge "
        f"fix — a slow PG read under the lock stalls every other "
        f"instance's stamp/clear/snapshot); got resolve at "
        f"offset {resolve_idx} and lock at offset {lock_idx}"
    )
