"""Phase 3 tests: Per-parent Lock serialization (Fix C4) — concurrent resolves.

Fix C4 serializes all ``register_message_send``, ``resolve_response``,
and ``check_parent_completion`` calls for the same parent via a per-parent
``asyncio.Lock``. This is required because the 3 cascade sites are called
from 4 concurrent contexts:

  1. ``task_processor.py:389``     — WorkerPool thread
  2. ``message_job_handler.py:317``— JobQueue asyncio task
  3. ``manager.py:2743``          — resume background asyncio task
  4. ``worker_pool.py:400``       — WorkerPool thread via MainLoopBridge

These are NOT within a single EventBus consumer loop. Without the Lock,
two concurrent resolves could both see ``pending_count > 0`` (stale
read) and neither fire the completion callback → parent stuck.

The per-parent Lock is fine-grained: different parents process in
parallel via different Lock objects, so there's no global contention.

Test coverage (mapped to plan §Verification Strategy item 3):
  6. Spawn N concurrent ``resolve_response`` calls for the SAME parent
     (after registering N). Verify the callback fires exactly once,
     after the last resolve, and exactly one resolve returns True.
  7. Spawn concurrent resolves for DIFFERENT parents. Verify each
     parent's callback fires exactly once, and the two parents
     process independently (different Lock objects → no contention).

See ``.agents/shared/planning/correlation-manager/phase3-cascade-unification.md``
for the full plan.

Run with:

    pytest tests/test_cascade_concurrency.py -v
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.skip(reason="Phase 5: CorrelationManager removed; tests CM concurrent resolve behavior")

# CM-era imports removed in Phase 5 (CorrelationManager → DependencyBus).
# Tests in this module are skipped via ``pytestmark`` above.


# =============================================================================
# Shared mock helpers
# =============================================================================


def make_instance_repo(
    *,
    instance_by_id: dict[str, Any] | None = None,
) -> MagicMock:
    """Mock SQLModelInstanceRepository."""
    repo = MagicMock(name="InstanceRepo")
    by_id = instance_by_id or {}
    repo.get = MagicMock(side_effect=lambda iid: by_id.get(iid))
    repo.get_all_with_waiting_for = MagicMock(return_value=[])
    repo.get_children = MagicMock(return_value=[])
    return repo


def make_msg_repo() -> MagicMock:
    """Mock SQLModelMessageQueueRepository."""
    repo = MagicMock(name="MsgRepo")
    repo.get_pending_for_instances = MagicMock(return_value=[])
    repo.list = MagicMock(return_value=[])
    return repo


def make_cm(
    *,
    callback: Any = None,
    instance_repo: MagicMock | None = None,
    msg_repo: MagicMock | None = None,
) -> CorrelationManager:
    """Build a CM with sensible defaults."""
    return CorrelationManager(
        instance_repository=instance_repo or make_instance_repo(),
        message_queue_repository=msg_repo or make_msg_repo(),
        completion_callback=callback,
    )


# =============================================================================
# Test 6 — N concurrent resolves for the same parent (Fix C4)
# =============================================================================


class TestConcurrentResolvesSameParent:
    """Spawn N concurrent ``resolve_response`` calls for the SAME parent.

    The per-parent ``asyncio.Lock`` (Fix C4) serializes them so that
    exactly one returns True (the resolving last entry) and the callback
    fires exactly once. Without the Lock, two concurrent resolves could
    both pass the ``is_complete`` check and both fire the callback.
    """

    @pytest.mark.asyncio
    async def test_three_concurrent_resolves_same_parent(self):
        """Register 3 sends, then fire 3 concurrent resolves via
        ``asyncio.gather``. Verify:
          - Exactly one resolve returns True.
          - The callback fires exactly once.
          - The callback fires with terminal_status='completed'.
          - The set is empty after the gather.
        """
        callback = AsyncMock()
        cm = make_cm(callback=callback)
        parent = "parent-1"
        keys = [(f"child-{i}", str(uuid.uuid4())) for i in range(3)]

        for child, msg in keys:
            await cm.register_message_send(parent, child, msg)
        assert cm.get_pending_count(parent) == 3
        callback.assert_not_called()

        # Fire all 3 resolves concurrently.
        results = await asyncio.gather(
            *[cm.resolve_response(parent, c, m) for c, m in keys]
        )

        # Exactly one True.
        true_count = sum(1 for r in results if r is True)
        assert true_count == 1, (
            f"Fix C4 violation: expected exactly 1 True result, got "
            f"{true_count}. Concurrent resolves bypassed the per-parent Lock."
        )

        # Callback fired exactly once, with the right args.
        assert callback.call_count == 1
        callback.assert_called_once_with(parent, "completed")

        # Set is empty after the gather.
        assert cm.get_pending_count(parent) == 0

    @pytest.mark.asyncio
    async def test_five_concurrent_resolves_same_parent(self):
        """5 concurrent resolves for the same parent — stress version.

        Same invariants as the 3-resolve test, with more concurrency.
        """
        callback = AsyncMock()
        cm = make_cm(callback=callback)
        parent = "parent-1"
        n = 5
        keys = [(f"child-{i}", str(uuid.uuid4())) for i in range(n)]

        for child, msg in keys:
            await cm.register_message_send(parent, child, msg)

        results = await asyncio.gather(
            *[cm.resolve_response(parent, c, m) for c, m in keys]
        )

        # Exactly one True.
        true_count = sum(1 for r in results if r is True)
        assert true_count == 1, f"Expected 1 True, got {true_count}"

        # Callback fired exactly once.
        assert callback.call_count == 1
        callback.assert_called_once_with(parent, "completed")

    @pytest.mark.asyncio
    async def test_concurrent_resolves_lock_serializes_order(self):
        """Verify the per-parent Lock actually serializes the resolves
        (they don't run truly in parallel within the critical section).

        We do this by tracking entry and exit of the callback. The
        callback's execution must NOT overlap with another resolve's
        critical section — because the callback runs AFTER the lock
        is released (W1 fix), this test verifies that the lock is
        released before the callback runs, by checking that a subsequent
        register on the same parent can acquire the lock.
        """
        callback_invocations: list[float] = []
        callback_started = asyncio.Event()
        callback_finished = asyncio.Event()

        async def slow_callback(parent_id: str, terminal_status: str) -> None:
            # Mark the callback as started and remember the time.
            callback_invocations.append(_now())
            callback_started.set()
            # Hold the callback "in flight" so we can verify the lock
            # is not held during this window.
            await asyncio.sleep(0.05)
            callback_finished.set()

        def _now() -> float:
            import time
            return time.monotonic()

        cm = make_cm(callback=slow_callback)
        parent = "parent-1"

        await cm.register_message_send(parent, "child-1", "msg-1")

        # Fire the completing resolve — it will release the lock and
        # THEN invoke the slow callback. We then immediately try to
        # register a new msg; this register must NOT block (proving
        # the lock was released before the callback).
        async def register_after_callback() -> None:
            await callback_started.wait()
            # The callback is now in flight. If the lock were still held,
            # this register would block. We use a short timeout to detect
            # the violation.
            try:
                await asyncio.wait_for(
                    cm.register_message_send(parent, "child-2", "msg-2"),
                    timeout=0.5,
                )
            except asyncio.TimeoutError:
                pytest.fail(
                    "Fix C4 / W1 violation: register hung while callback "
                    "was in flight — the per-parent lock was held across "
                    "the callback"
                )

        # Concurrently: completing resolve + register attempt.
        await asyncio.gather(
            cm.resolve_response(parent, "child-1", "msg-1"),
            register_after_callback(),
        )

        # The callback ran exactly once.
        assert len(callback_invocations) == 1
        # The register that raced with the callback completed before
        # the callback returned (otherwise register_after_callback would
        # have waited for callback_finished). This proves the lock was
        # released before the callback was invoked.
        assert cm.get_pending_count(parent) == 1

    @pytest.mark.asyncio
    async def test_concurrent_resolves_with_mixed_statuses(self):
        """Concurrent resolves with mixed statuses (responded + error) for
        the same parent. The terminal_status is "error" (conservative)
        and the callback fires exactly once.
        """
        callback = AsyncMock()
        cm = make_cm(callback=callback)
        parent = "parent-1"
        keys = [(f"child-{i}", str(uuid.uuid4())) for i in range(3)]

        for c, m in keys:
            await cm.register_message_send(parent, c, m)

        # One of the resolves is an error.
        results = await asyncio.gather(
            cm.resolve_response(parent, keys[0][0], keys[0][1], status=STATUS_RESPONDED),
            cm.resolve_response(parent, keys[1][0], keys[1][1], status="error"),
            cm.resolve_response(parent, keys[2][0], keys[2][1], status=STATUS_RESPONDED),
        )

        true_count = sum(1 for r in results if r is True)
        assert true_count == 1
        assert callback.call_count == 1
        # Conservative: any error → "error".
        callback.assert_called_once_with(parent, "error")

    @pytest.mark.asyncio
    async def test_concurrent_resolves_no_double_callback(self):
        """Stress: 20 concurrent resolves for the same parent.

        Without the per-parent Lock, multiple concurrent resolves could
        all see ``is_complete`` and fire the callback multiple times.
        With the Lock, the callback fires exactly once even under
        high concurrency.
        """
        callback = AsyncMock()
        cm = make_cm(callback=callback)
        parent = "parent-1"
        n = 20
        keys = [(f"child-{i}", str(uuid.uuid4())) for i in range(n)]

        for c, m in keys:
            await cm.register_message_send(parent, c, m)

        results = await asyncio.gather(
            *[cm.resolve_response(parent, c, m) for c, m in keys]
        )

        # Exactly one True (the resolving last entry).
        true_count = sum(1 for r in results if r is True)
        assert true_count == 1, (
            f"Fix C4 violation under stress: expected 1 True, got {true_count}"
        )
        # Callback fired exactly once.
        assert callback.call_count == 1
        callback.assert_called_once_with(parent, "completed")


# =============================================================================
# Test 7 — Concurrent resolves for DIFFERENT parents (different Locks)
# =============================================================================


class TestConcurrentResolvesDifferentParents:
    """Concurrent resolves for DIFFERENT parents use DIFFERENT Locks.

    This is the fine-grained property of Fix C4: per-parent Locks allow
    different parents to process in parallel without contending on a
    single global lock. Different parents → different Lock objects →
    no contention, no cross-talk.
    """

    @pytest.mark.asyncio
    async def test_two_parents_resolve_in_parallel(self):
        """Two parents, each with 2 pending sends, resolved concurrently.

        Each parent's callback fires exactly once. The two parents'
        state is independent (P1's resolves don't affect P2's count).
        """
        callback = AsyncMock()
        cm = make_cm(callback=callback)

        p1_keys = [("p1c1", str(uuid.uuid4())), ("p1c2", str(uuid.uuid4()))]
        p2_keys = [("p2c1", str(uuid.uuid4())), ("p2c2", str(uuid.uuid4()))]
        for c, m in p1_keys:
            await cm.register_message_send("parent-1", c, m)
        for c, m in p2_keys:
            await cm.register_message_send("parent-2", c, m)

        # Concurrently resolve all 4 (2 for each parent).
        all_resolves = (
            [cm.resolve_response("parent-1", c, m) for c, m in p1_keys]
            + [cm.resolve_response("parent-2", c, m) for c, m in p2_keys]
        )
        results = await asyncio.gather(*all_resolves)

        # Exactly 2 True (one per parent).
        true_count = sum(1 for r in results if r is True)
        assert true_count == 2, f"Expected 2 True (one per parent), got {true_count}"

        # Each parent's callback fired exactly once.
        assert callback.call_count == 2
        p1_calls = [c for c in callback.call_args_list if c.args[0] == "parent-1"]
        p2_calls = [c for c in callback.call_args_list if c.args[0] == "parent-2"]
        assert len(p1_calls) == 1
        assert len(p2_calls) == 1
        # `call_args_list` contains Call objects — compare .args to the tuple.
        assert p1_calls[0].args == ("parent-1", "completed")
        assert p2_calls[0].args == ("parent-2", "completed")

    @pytest.mark.asyncio
    async def test_different_parents_use_different_locks(self):
        """The per-parent Lock is created lazily and is unique per parent.

        Fix C4 relies on different parents having different Lock objects
        so that resolves on different parents don't serialize through a
        single global lock. This test verifies that invariant directly
        by inspecting the CM's ``_locks`` dict after a few operations.
        """
        cm = make_cm(callback=AsyncMock())

        # Touch each parent once to trigger lazy Lock creation.
        await cm.register_message_send("parent-A", "c1", "m1")
        await cm.register_message_send("parent-B", "c1", "m1")
        await cm.register_message_send("parent-C", "c1", "m1")

        # _locks must have one entry per parent.
        assert set(cm._locks.keys()) == {"parent-A", "parent-B", "parent-C"}
        # And the Locks must be distinct objects.
        assert cm._locks["parent-A"] is not cm._locks["parent-B"]
        assert cm._locks["parent-A"] is not cm._locks["parent-C"]
        assert cm._locks["parent-B"] is not cm._locks["parent-C"]

    @pytest.mark.asyncio
    async def test_many_parents_resolve_concurrently_without_contention(self):
        """10 parents, 3 pending each, resolved concurrently.

        With per-parent Locks, the 10 parents process independently.
        Each parent fires its callback exactly once, and no cross-talk
        occurs. With a single global Lock, the total wall-clock time
        would be ~10x longer (all resolves serialized); with per-parent
        Locks, they overlap freely.
        """
        callback = AsyncMock()
        cm = make_cm(callback=callback)

        n_parents = 10
        pending_per_parent = 3
        keys_by_parent: dict[str, list[tuple[str, str]]] = {}
        for p_idx in range(n_parents):
            parent = f"parent-{p_idx}"
            keys_by_parent[parent] = []
            for c_idx in range(pending_per_parent):
                child = f"c{p_idx}-{c_idx}"
                msg = str(uuid.uuid4())
                keys_by_parent[parent].append((child, msg))
                await cm.register_message_send(parent, child, msg)

        # Concurrently resolve ALL pending entries.
        all_resolves = [
            cm.resolve_response(parent, c, m)
            for parent, keys in keys_by_parent.items()
            for c, m in keys
        ]
        results = await asyncio.gather(*all_resolves)

        # Exactly one True per parent.
        true_count = sum(1 for r in results if r is True)
        assert true_count == n_parents, (
            f"Expected {n_parents} True results (one per parent), got {true_count}"
        )

        # Each parent fired its callback exactly once.
        assert callback.call_count == n_parents
        for p_idx in range(n_parents):
            parent = f"parent-{p_idx}"
            matching = [
                c for c in callback.call_args_list if c.args[0] == parent
            ]
            assert len(matching) == 1, (
                f"Parent {parent} fired callback {len(matching)} times, expected 1"
            )
            # `call_args_list` contains Call objects — compare .args to the tuple.
            assert matching[0].args == (parent, "completed")

        # All parents cleaned up from _pending.
        assert len(cm._pending) == 0

    @pytest.mark.asyncio
    async def test_resolves_for_one_parent_do_not_block_another(self):
        """A resolve for parent-A must not block a concurrent resolve for
        parent-B (different Locks → no contention).

        We verify by holding parent-A's lock from outside the CM and
        timing how long a parent-B resolve takes. If parent-B were
        blocked on parent-A's lock, it would time out.
        """
        import time

        cm = make_cm(callback=AsyncMock())
        await cm.register_message_send("parent-A", "c", "m")
        await cm.register_message_send("parent-B", "c", "m")

        # Acquire parent-A's lock from outside the CM to hold it.
        lock_a = cm._get_lock("parent-A")
        await lock_a.acquire()
        try:
            start = time.monotonic()
            # A resolve for parent-B must complete quickly because
            # it uses parent-B's lock, not parent-A's.
            result = await asyncio.wait_for(
                cm.resolve_response("parent-B", "c", "m"),
                timeout=1.0,
            )
            elapsed = time.monotonic() - start
            assert result is True
            # parent-B's resolve should have been near-instant.
            assert elapsed < 0.5, (
                f"parent-B's resolve took {elapsed:.3f}s while parent-A's "
                f"lock was held — different parents are serialized, "
                f"violating the per-parent Lock invariant"
            )
        finally:
            lock_a.release()
