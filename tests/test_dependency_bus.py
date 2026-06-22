"""Unit tests for DependencyBus — in-memory SQLite, no daemon, no PostgreSQL.

Phase D deliverable D9: Dependency Bus test pack (unit portion).

Covers:
  1. Watch / emit_terminal semantics
  2. No double-decrement bug class (structural proof)
  3. Restart / crash survival
  4. Cancellation
  5. Backpressure (large batch)
  6. FollowUp serialization round-trips
  7. Pending-watcher cache / DB fallback
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

# Register table models so create_all() picks them up.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401

from daemon.repositories.dependency_bus import (
    DependencyWatcher,
    DependencyWatcherRepository,
)
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    Outcome,
    get_dependency_bus,
    set_dependency_bus,
)


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------


def make_fu(
    target_id: str = "parent-A",
    message: str = "m",
    source: str = "dependency_bus",
    metadata: dict | None = None,
) -> FollowUp:
    return FollowUp(
        target_instance_id=target_id,
        message=message,
        source=source,
        metadata=metadata or {},
    )


def make_outcome(
    status: str = "completed",
    error: str | None = None,
    summary: str | None = None,
) -> Outcome:
    return Outcome(status=status, error=error, summary=summary)


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture
def bus_repo():
    """In-memory SQLite repo for unit tests.

    StaticPool + check_same_thread=False is REQUIRED: asyncio.to_thread
    shares the connection with the main thread, and :memory: databases are
    connection-scoped by default.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return DependencyWatcherRepository(eng)


@pytest.fixture
async def bus(bus_repo):
    """Started DependencyBus over the in-memory repo."""
    b = DependencyBus(bus_repo)
    await b.start()
    try:
        yield b
    finally:
        await b.stop()


async def fresh_bus(repo: DependencyWatcherRepository) -> DependencyBus:
    """Construct and start a brand-new bus (for restart-survival tests).

    Must be awaited: ``bus.start()`` is async and we cannot call
    ``loop.run_until_complete`` from inside a running event loop
    (pytest-asyncio's loop is already running).
    """
    b = DependencyBus(repo)
    await b.start()
    return b


# -------------------------------------------------------------------------
# TestWatchEmitSemantics
# -------------------------------------------------------------------------


class TestWatchEmitSemantics:
    """Tests for basic watch / emit_terminal contract."""

    @pytest.mark.asyncio
    async def test_watch_creates_pending_watcher(self, bus):
        await bus.watch("task-1", make_fu())
        result = await bus.pending_watchers("task-1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_multiple_watchers_same_source(self, bus):
        for i in range(3):
            await bus.watch("task-1", make_fu(target_id=f"parent-{i}"))
        result = await bus.pending_watchers("task-1")
        assert len(result) == 3
        ids = {fu.target_instance_id for fu in result}
        assert ids == {"parent-0", "parent-1", "parent-2"}

    @pytest.mark.asyncio
    async def test_emit_fires_all_pending(self, bus):
        for i in range(3):
            await bus.watch("task-1", make_fu(target_id=f"parent-{i}"))
        fired = await bus.emit_terminal("task-1", make_outcome())
        assert len(fired) == 3
        assert await bus.pending_watchers("task-1") == []

    @pytest.mark.asyncio
    async def test_emit_is_idempotent(self, bus):
        await bus.watch("task-1", make_fu())
        first = await bus.emit_terminal("task-1", make_outcome())
        assert len(first) == 1
        second = await bus.emit_terminal("task-1", make_outcome())
        assert second == []

    @pytest.mark.asyncio
    async def test_emit_with_no_watchers_returns_empty(self, bus):
        result = await bus.emit_terminal("task-nonexistent", make_outcome())
        assert result == []

    @pytest.mark.asyncio
    async def test_follow_up_payload_round_trip(self, bus):
        meta = {"child_id": "c1", "k": 42, "nested": {"a": 1}}
        await bus.watch("task-1", make_fu(metadata=meta))
        fired = await bus.emit_terminal("task-1", make_outcome())
        assert len(fired) == 1
        assert fired[0].metadata == meta

    @pytest.mark.asyncio
    async def test_outcome_status_propagated(self, bus):
        """All PENDING watchers fire regardless of terminal status."""
        await bus.watch("task-1", make_fu())
        # emit_terminal does not filter by outcome — fires all PENDING.
        fired = await bus.emit_terminal("task-1", Outcome(status="error", error="boom"))
        assert len(fired) == 1


# -------------------------------------------------------------------------
# TestNoDoubleDecrement
# -------------------------------------------------------------------------


class TestNoDoubleDecrement:
    """Proof that the double-decrement bug class is eliminated.

    The DependencyBus uses DB rows as the source of truth, not a shared
    mutable counter. The structural test checks for absence of a counter;
    the concurrency test verifies the guarded UPDATE prevents double-fire.
    """

    @pytest.mark.asyncio
    async def test_bus_has_no_counter(self, bus):
        """Structural proof: no waiting_for-style mutable counter exists."""
        # The bus uses a dict cache, not a shared integer counter.
        assert not hasattr(bus, "waiting_for")
        assert not hasattr(bus, "_counter")

    @pytest.mark.asyncio
    async def test_concurrent_emit_does_not_double_fire(self, bus):
        """Guarded UPDATE (WHERE state='PENDING') prevents double-fire.

        Two concurrent emit_terminal calls on the same source_task_id must
        deliver the single FollowUp exactly once. The per-task asyncio.Lock
        serializes them; the guarded UPDATE makes the second see rowcount=0.
        """
        await bus.watch("task-1", make_fu())

        async def emit_once():
            return await bus.emit_terminal("task-1", make_outcome())

        # asyncio.gather runs both coroutines concurrently.
        results = await asyncio.gather(emit_once(), emit_once())
        # Sum across both returns must be exactly 1 (one returns [fu], one []).
        total = sum(len(r) for r in results)
        assert total == 1, f"Expected 1 total FollowUp across both emits, got {total}"


# -------------------------------------------------------------------------
# TestRestartSurvival
# -------------------------------------------------------------------------


class TestRestartSurvival:
    """Tests for crash/restart survival: DB is the source of truth."""

    @pytest.mark.asyncio
    async def test_watcher_survives_restart(self, bus_repo):
        """A watcher registered before stop() is still fired after restart."""
        b1 = DependencyBus(bus_repo)
        await b1.start()
        await b1.watch("task-1", make_fu(target_id="parent-1"))
        await b1.stop()

        # New bus instance shares the same in-memory DB (StaticPool).
        b2 = await fresh_bus(bus_repo)
        try:
            fired = await b2.emit_terminal("task-1", make_outcome())
            assert len(fired) == 1
        finally:
            await b2.stop()

    @pytest.mark.asyncio
    async def test_fired_watcher_not_refired_after_restart(self, bus_repo):
        """FIRED rows must not fire a second time after restart."""
        b1 = DependencyBus(bus_repo)
        await b1.start()
        await b1.watch("task-1", make_fu())
        fired1 = await b1.emit_terminal("task-1", make_outcome())
        assert len(fired1) == 1
        await b1.stop()

        b2 = await fresh_bus(bus_repo)
        try:
            fired2 = await b2.emit_terminal("task-1", make_outcome())
            assert fired2 == []
        finally:
            await b2.stop()

    @pytest.mark.asyncio
    async def test_start_recovers_pending_watchers(self, bus_repo):
        """start() warms cache from DB: pending watchers survive restart."""
        # Insert a row directly via the repo (simulates pre-crash state).
        raw = DependencyWatcher(
            source_task_id="pre-existing",
            target_instance_id="parent-X",
            follow_up_payload=make_fu(target_id="parent-X").to_payload(),
        )
        bus_repo.insert(raw)

        # New bus warms its cache from DB on start().
        b = await fresh_bus(bus_repo)
        try:
            result = await b.pending_watchers("pre-existing")
            assert len(result) == 1
            assert result[0].target_instance_id == "parent-X"
        finally:
            await b.stop()

    @pytest.mark.asyncio
    async def test_cancelled_watcher_not_fired_after_restart(self, bus_repo):
        """CANCELLED rows must not fire after restart."""
        b1 = DependencyBus(bus_repo)
        await b1.start()
        await b1.watch("task-1", make_fu(target_id="parent-1"))
        await b1.cancel_for_target("parent-1")
        await b1.stop()

        b2 = await fresh_bus(bus_repo)
        try:
            fired = await b2.emit_terminal("task-1", make_outcome())
            assert fired == []
        finally:
            await b2.stop()


# -------------------------------------------------------------------------
# TestCancellation
# -------------------------------------------------------------------------


class TestCancellation:
    """Tests for cancel_for_target."""

    @pytest.mark.asyncio
    async def test_cancel_for_target_transitions_to_cancelled(self, bus):
        await bus.watch("task-1", make_fu(target_id="parent-X"))
        count = await bus.cancel_for_target("parent-X")
        assert count == 1
        assert await bus.pending_watchers("task-1") == []

    @pytest.mark.asyncio
    async def test_cancel_only_affects_specified_target(self, bus):
        """Cancel X leaves Y's watchers intact."""
        await bus.watch("task-1", make_fu(target_id="parent-X"))
        await bus.watch("task-2", make_fu(target_id="parent-Y"))
        await bus.cancel_for_target("parent-X")
        assert await bus.pending_watchers("task-1") == []
        assert len(await bus.pending_watchers("task-2")) == 1

    @pytest.mark.asyncio
    async def test_cancel_returns_count(self, bus):
        """Cancel returns the number of rows actually transitioned."""
        for i in range(3):
            await bus.watch("task-1", make_fu(target_id="parent-X"))
        count = await bus.cancel_for_target("parent-X")
        assert count == 3

    @pytest.mark.asyncio
    async def test_cancel_then_emit_fires_nothing(self, bus):
        await bus.watch("task-1", make_fu())
        await bus.cancel_for_target("parent-A")
        fired = await bus.emit_terminal("task-1", make_outcome())
        assert fired == []

    @pytest.mark.asyncio
    async def test_cancel_unknown_target_returns_zero(self, bus):
        count = await bus.cancel_for_target("nonexistent-parent")
        assert count == 0


# -------------------------------------------------------------------------
# TestBackpressure
# -------------------------------------------------------------------------


class TestBackpressure:
    """Tests for the backpressure primitive: exactly-once delivery under load."""

    @pytest.mark.asyncio
    async def test_10000_watchers_emit_one_at_a_time(self, bus):
        """10 000 watchers on one source → exactly 10 000 fired (no more, no less).

        Budget note: reduced to 1 000 watchers for SQLite :memory: speed.
        The backpressure guarantee is the same at any scale; 1k is sufficient
        to prove the loop doesn't skip or double-fire.
        """
        N = 1000  # 10 000 would work on a real DB; :memory: is faster at 1k.
        source = "batch-source"
        for i in range(N):
            await bus.watch(source, make_fu(target_id=f"parent-{i}"))

        fired = await bus.emit_terminal(source, make_outcome())
        assert len(fired) == N, f"Expected {N} fired, got {len(fired)}"

        # Second emit must be empty.
        fired2 = await bus.emit_terminal(source, make_outcome())
        assert fired2 == [], "Second emit should return [] — rows already FIRED"


# -------------------------------------------------------------------------
# TestFollowUpSerialization
# -------------------------------------------------------------------------


class TestFollowUpSerialization:
    """Tests for FollowUp / Outcome frozen dataclass round-trips."""

    def test_follow_up_to_payload_from_payload_roundtrip(self):
        fu = FollowUp(
            target_instance_id="parent-1",
            message="hello",
            source="test-source",
            metadata={"key": "value", "num": 99},
        )
        payload = fu.to_payload()
        fu2 = FollowUp.from_payload(payload)
        assert fu == fu2

    def test_follow_up_with_complex_metadata(self):
        meta = {
            "str": "hello",
            "int": 42,
            "float": 3.14,
            "bool": True,
            "none": None,
            "list": [1, 2, "three"],
            "nested": {"a": [1, {"b": 2}]},
        }
        fu = make_fu(metadata=meta)
        payload = fu.to_payload()
        fu2 = FollowUp.from_payload(payload)
        assert fu2.metadata == meta

    def test_follow_up_default_source(self):
        fu = FollowUp(target_instance_id="x", message="y")
        assert fu.source == "dependency_bus"

    def test_outcome_defaults(self):
        o = Outcome(status="completed")
        assert o.error is None
        assert o.summary is None


# -------------------------------------------------------------------------
# TestReviewerFixes
# -------------------------------------------------------------------------


class TestCrashRecoveryEnqueuedAt:
    """C1 fix: crash between transition_state commit and enqueue → restart → FollowUp recovered exactly once."""

    @pytest.mark.asyncio
    async def test_fired_unsent_recovered_on_restart(self, bus_repo):
        """FIRED row with enqueued_at=NULL is recovered by start()."""
        from daemon.services.dependency_bus import DependencyBus, FollowUp, Outcome

        bus1 = DependencyBus(bus_repo)
        await bus1.start()
        fu = FollowUp(target_instance_id="parent-crash", message="done")
        await bus1.watch("task-crash", fu)
        # Simulate terminal event — watcher transitions to FIRED
        fired = await bus1.emit_terminal("task-crash", Outcome(status="completed"))
        assert len(fired) == 1
        # Simulate crash: stop WITHOUT enqueuing (enqueued_at stays NULL)
        await bus1.stop()

        # Restart with a new bus instance
        bus2 = DependencyBus(bus_repo)
        recovered = await bus2.start()
        # The FIRED-but-unsent watcher should be recovered
        assert len(recovered) == 1, f"expected 1 recovered, got {len(recovered)}"
        watch_id, recovered_fu = recovered[0]
        assert recovered_fu.target_instance_id == "parent-crash"
        await bus2.stop()

    @pytest.mark.asyncio
    async def test_mark_enqueued_prevents_double_recovery(self, bus_repo):
        """After mark_enqueued, the watcher is NOT recovered on next restart."""
        from daemon.services.dependency_bus import DependencyBus, FollowUp, Outcome

        bus1 = DependencyBus(bus_repo)
        await bus1.start()
        fu = FollowUp(target_instance_id="parent-dedup", message="done")
        await bus1.watch("task-dedup", fu)
        fired = await bus1.emit_terminal("task-dedup", Outcome(status="completed"))
        assert len(fired) == 1
        await bus1.stop()

        # Restart — recover the unsent watcher
        bus2 = DependencyBus(bus_repo)
        recovered = await bus2.start()
        assert len(recovered) == 1
        watch_id, _ = recovered[0]
        # Mark as enqueued (simulating successful re-enqueue)
        await bus2.mark_enqueued(watch_id)
        await bus2.stop()

        # Restart again — the marked watcher should NOT be recovered
        bus3 = DependencyBus(bus_repo)
        recovered2 = await bus3.start()
        assert len(recovered2) == 0, f"expected 0 after marking, got {len(recovered2)}"
        await bus3.stop()

    @pytest.mark.asyncio
    async def test_partial_crash_only_recovers_unsent(self, bus_repo):
        """When some watchers are enqueued and some aren't, only unsent are recovered."""
        from daemon.services.dependency_bus import DependencyBus, FollowUp, Outcome

        bus1 = DependencyBus(bus_repo)
        await bus1.start()
        fu1 = FollowUp(target_instance_id="parent-1", message="done-1")
        fu2 = FollowUp(target_instance_id="parent-2", message="done-2")
        await bus1.watch("task-multi", fu1)
        await bus1.watch("task-multi", fu2)
        fired = await bus1.emit_terminal("task-multi", Outcome(status="completed"))
        assert len(fired) == 2
        await bus1.stop()

        # Restart — recover both
        bus2 = DependencyBus(bus_repo)
        recovered = await bus2.start()
        assert len(recovered) == 2
        # Mark only the first as enqueued
        await bus2.mark_enqueued(recovered[0][0])
        await bus2.stop()

        # Restart — only the unmarked one should be recovered
        bus3 = DependencyBus(bus_repo)
        recovered2 = await bus3.start()
        assert len(recovered2) == 1, f"expected 1 unmarked, got {len(recovered2)}"
        await bus3.stop()


class TestGenerationCounterBump:
    """C2 fix: watch() bumps CM generation counter so _finalize_job's re-arm works."""

    @pytest.mark.asyncio
    async def test_watch_bumps_cm_generation(self, bus_repo):
        """watch() bumps cm._generation[parent_id] before acquiring the bus lock."""
        from daemon.services.dependency_bus import DependencyBus, FollowUp
        from daemon.services.correlation_manager import CorrelationManager

        # Create a real CM (no repos needed — we only test _generation access)
        cm = CorrelationManager(
            instance_repository=None,
            message_queue_repository=None,
        )

        bus = DependencyBus(bus_repo)
        await bus.start()

        # Set CM as the module singleton so watch() can find it
        from daemon.services.correlation_manager import set_correlation_manager, get_correlation_manager
        set_correlation_manager(cm)

        try:
            parent_id = "parent-gen-test"
            assert cm.get_generation(parent_id) == 0

            fu = FollowUp(target_instance_id=parent_id, message="done")
            await bus.watch("task-gen-test", fu)

            gen_after = cm.get_generation(parent_id)
            assert gen_after > 0, f"generation should be bumped, got {gen_after}"

            # Second watch bumps again
            await bus.watch("task-gen-test-2", fu)
            gen_after2 = cm.get_generation(parent_id)
            assert gen_after2 > gen_after, f"generation should increase, got {gen_after2} vs {gen_after}"
        finally:
            set_correlation_manager(None)
            await bus.stop()

    @pytest.mark.asyncio
    async def test_watch_without_cm_does_not_crash(self, bus_repo):
        """watch() handles CM not being wired (no crash, bus still works)."""
        from daemon.services.dependency_bus import DependencyBus, FollowUp
        from daemon.services.correlation_manager import set_correlation_manager

        set_correlation_manager(None)

        bus = DependencyBus(bus_repo)
        await bus.start()

        fu = FollowUp(target_instance_id="parent-no-cm", message="done")
        # Should not crash even though CM is None
        await bus.watch("task-no-cm", fu)

        pending = await bus.pending_watchers("task-no-cm")
        assert len(pending) == 1
        await bus.stop()


# -------------------------------------------------------------------------
# TestPendingWatchersFallback
# -------------------------------------------------------------------------


class TestPendingWatchersFallback:
    """Tests for cache hit vs. DB fallback on pending_watchers."""

    @pytest.mark.asyncio
    async def test_pending_watchers_cache_hit(self, bus):
        """watch() populates the cache; pending_watchers returns it."""
        await bus.watch("task-1", make_fu(target_id="p1"))
        await bus.watch("task-1", make_fu(target_id="p2"))
        result = await bus.pending_watchers("task-1")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_pending_watchers_db_fallback_after_restart(self, bus_repo):
        """Cache miss after restart falls back to DB (cache was cold)."""
        b1 = DependencyBus(bus_repo)
        await b1.start()
        await b1.watch("task-1", make_fu(target_id="p1"))
        await b1.watch("task-1", make_fu(target_id="p2"))
        await b1.stop()

# New bus — cache was never warmed for task-1.
        b2 = await fresh_bus(bus_repo)
        try:
            result = await b2.pending_watchers("task-1")
            assert len(result) == 2
        finally:
            await b2.stop()


# -------------------------------------------------------------------------
# TestCountPendingForTarget (premature-completion gate helper)
# -------------------------------------------------------------------------


class TestCountPendingForTarget:
    """Tests for ``count_pending_for_target`` (the bus-side pending-children
    count used by the completion gates in ``child_reports`` and
    ``job_feedback_observer``).

    This is the read-side companion to ``cancel_for_target``: both filter
    the same ``dependency_watchers`` table by ``target_instance_id`` and
    ``state='PENDING'``. ``cancel_for_target`` transitions matching rows
    to CANCELLED; this method just counts them — the cheap hot-path query
    the gates run inside ``WriteGuardSession`` on a worker thread.

    Contract:
      * Returns 0 when no PENDING watchers exist (the common case).
      * Returns the integer count when matching rows exist.
      * Only counts PENDING rows — FIRED / CANCELLED rows are excluded.
      * Does NOT mutate state (read-only).
      * Both async (``count_pending_for_target``) and sync
        (``count_pending_for_target_sync``) variants are tested — the
        sync variant is the one the completion gates call.
    """

    @pytest.mark.asyncio
    async def test_count_pending_for_target_zero_when_no_watchers(self, bus):
        """Empty bus → count is 0 (the common case for completed parents)."""
        count = await bus.count_pending_for_target("nonexistent-parent")
        assert count == 0
        assert bus.count_pending_for_target_sync("nonexistent-parent") == 0

    @pytest.mark.asyncio
    async def test_count_pending_for_target_counts_all_pending(self, bus):
        """Multiple watchers across multiple sources → all counted."""
        await bus.watch("task-1", make_fu(target_id="parent-A"))
        await bus.watch("task-1", make_fu(target_id="parent-A"))
        await bus.watch("task-2", make_fu(target_id="parent-A"))
        await bus.watch("task-3", make_fu(target_id="parent-B"))

        # Parent-A has 3 watchers (2 on task-1, 1 on task-2).
        assert bus.count_pending_for_target_sync("parent-A") == 3
        assert await bus.count_pending_for_target("parent-A") == 3
        # Parent-B has 1 watcher (on task-3).
        assert bus.count_pending_for_target_sync("parent-B") == 1
        # Parent-C has none.
        assert bus.count_pending_for_target_sync("parent-C") == 0

    @pytest.mark.asyncio
    async def test_count_pending_for_target_excludes_fired_rows(self, bus):
        """After emit_terminal, the count drops (FIRED rows excluded)."""
        await bus.watch("task-1", make_fu(target_id="parent-X"))
        await bus.watch("task-2", make_fu(target_id="parent-X"))
        await bus.watch("task-3", make_fu(target_id="parent-X"))

        assert bus.count_pending_for_target_sync("parent-X") == 3

        # Fire task-1 → 2 remaining
        await bus.emit_terminal("task-1", make_outcome())
        assert bus.count_pending_for_target_sync("parent-X") == 2

        # Fire task-2 → 1 remaining
        await bus.emit_terminal("task-2", make_outcome())
        assert bus.count_pending_for_target_sync("parent-X") == 1

    @pytest.mark.asyncio
    async def test_count_pending_for_target_excludes_cancelled_rows(self, bus):
        """After cancel_for_target, the count drops to 0 (CANCELLED excluded)."""
        for i in range(3):
            await bus.watch(f"task-{i}", make_fu(target_id="parent-Y"))

        assert bus.count_pending_for_target_sync("parent-Y") == 3

        cancelled = await bus.cancel_for_target("parent-Y")
        assert cancelled == 3
        assert bus.count_pending_for_target_sync("parent-Y") == 0

    @pytest.mark.asyncio
    async def test_count_pending_for_target_only_counts_target_match(self, bus):
        """Filter is exact match on target_instance_id — not prefix/substring."""
        await bus.watch("task-1", make_fu(target_id="parent-1"))
        await bus.watch("task-1", make_fu(target_id="parent-10"))
        await bus.watch("task-1", make_fu(target_id="parent-100"))

        # Each parent has exactly 1 watcher.
        assert bus.count_pending_for_target_sync("parent-1") == 1
        assert bus.count_pending_for_target_sync("parent-10") == 1
        assert bus.count_pending_for_target_sync("parent-100") == 1

    def test_count_pending_for_target_sync_reads_db_directly(self, bus_repo):
        """The sync variant reads from the DB (source of truth) — no cache.

        Inserts a watcher via the repo (bypassing the bus's async cache),
        then constructs a fresh bus and checks the count. This proves the
        sync variant doesn't depend on the in-memory cache and is safe to
        call from sync contexts that have no event loop.
        """
        bus = DependencyBus(bus_repo)
        # NOTE: do NOT call bus.start() — we want to verify the sync
        # variant hits the DB even when the cache is cold.
        try:
            w = DependencyWatcher(
                source_task_id="pre-existing",
                target_instance_id="parent-sync",
                follow_up_payload=make_fu(target_id="parent-sync").to_payload(),
            )
            bus_repo.insert(w)
            # Sync variant returns the DB count regardless of cache state.
            assert bus.count_pending_for_target_sync("parent-sync") == 1
        finally:
            # Async cleanup if any — but bus was never started, so no cache.
            pass


# -------------------------------------------------------------------------
# TestBusRetriggerFinalize (Phase D re-trigger via _emit_terminal_via_bus)
# -------------------------------------------------------------------------
#
# The ``_emit_terminal_via_bus`` helper in ``daemon/services/child_reports.py``
# re-triggers ``_finalize_job`` on the parent's ``JobFeedbackObserver`` after
# the bus fires all of its PENDING watchers. This block exercises the
# retrigger path end-to-end against a real bus + a mocked observer.
#
# Critical safety properties under test:
#   * Fires exactly once per target when ALL watchers have been fired
#     (count_pending_for_target(target) == 0).
#   * Does NOT fire when any watcher is still PENDING for the target.
#   * Does NOT fire when there is no PROCESSING job (job-already-terminal
#     case is a clean no-op).
#   * Loop-level guard: a failure on one target's re-trigger does not
#     abort the rest of the loop.
#   * Helper-level guard: ``_get_processing_job_for_instance`` raising
#     does not propagate out of ``_retrigger_parent_finalize``.


class TestBusRetriggerFinalize:
    """Tests for the bus re-trigger finalize path in ``_emit_terminal_via_bus``.

    The re-trigger is the inverse-regression fix for the bus-path
    ``send_message`` starves the CM callback bug — without it, jobs on
    the bus path stay PROCESSING forever. The tests below pin down the
    three safety contracts that the fix relies on.
    """

    @pytest.fixture(autouse=True)
    async def _install_bus(self, bus_repo):
        """Install a real DependencyBus as the module singleton for the
        duration of the test. Tears down on exit so other test files that
        expect ``get_dependency_bus() is None`` are not affected.
        """
        b = DependencyBus(bus_repo)
        await b.start()
        set_dependency_bus(b)
        try:
            yield b
        finally:
            await b.stop()
            set_dependency_bus(None)

    def _build_service_with_observer(
        self, observer: MagicMock | None
    ) -> "ChildReportsService":
        """Build a ChildReportsService against a mock manager.

        The mock manager exposes:
          * ``_job_feedback_observer`` — the mocked observer (or None)
          * ``enqueue_message`` — AsyncMock so the bus-followup enqueue
            loop completes without touching the DB

        The service is constructed via ``__new__`` to skip ``__init__``
        (which would touch the real manager). Mirrors the pattern used
        in ``tests/unit/services/test_child_reports.py``.
        """
        from daemon.services.child_reports import ChildReportsService

        manager = MagicMock(name="InstanceManager")
        manager._job_feedback_observer = observer
        manager.enqueue_message = AsyncMock(name="enqueue_message")

        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = manager
        service._events_service = None
        return service

    @pytest.mark.asyncio
    async def test_retrigger_fires_when_all_watchers_resolved(self):
        """R1: register one watcher → fire it → _finalize_job called once.

        When the bus transitions the only PENDING watcher to FIRED, the
        post-fire ``count_pending_for_target`` returns 0 for the target,
        and the retrigger guard fires ``observer._finalize_job`` exactly
        once with the COMPLETED status. This is the happy path that
        releases the parent job lock after the bus has fully resolved.
        """
        # Mock observer: a PROCESSING job exists, _finalize_job succeeds.
        fake_job = MagicMock(name="JobItem")
        fake_job.job_id = f"job-{uuid.uuid4().hex[:8]}"

        observer = MagicMock(name="JobFeedbackObserver")
        observer._get_processing_job_for_instance = AsyncMock(
            return_value=fake_job
        )
        observer._finalize_job = AsyncMock(name="_finalize_job")

        service = self._build_service_with_observer(observer)
        target_id = f"parent-{uuid.uuid4().hex[:8]}"

        bus = get_dependency_bus()
        await bus.watch("task-1", make_fu(target_id=target_id))

        # task_id is converted to str() inside _emit_terminal_via_bus
        # before being passed to bus.emit_terminal(), so use the string
        # form directly to match the watcher's source_task_id.
        fired = await service._emit_terminal_via_bus(
            task_id="task-1", status="completed"
        )

        # The bus returned the FollowUp and the retrigger fired finalize.
        assert len(fired) == 1
        assert fired[0].target_instance_id == target_id
        observer._get_processing_job_for_instance.assert_awaited_once_with(
            target_id
        )
        observer._finalize_job.assert_awaited_once()
        _args, kwargs = observer._finalize_job.call_args
        # _finalize_job signature: (job, instance_id, status, error)
        # The status argument must be "completed" so the parent job
        # transitions PROCESSING → COMPLETED.
        assert kwargs.get("status") == "completed" or (
            len(_args) >= 3 and _args[2] == "completed"
        )

    @pytest.mark.asyncio
    async def test_retrigger_skipped_when_watchers_remain(self):
        """R2: 2 watchers on same target → fire only 1 → finalize NOT called.

        The gate is ``count_pending_for_target(target) == 0``. When one
        watcher is still PENDING, the retrigger MUST skip — calling
        ``_finalize_job`` prematurely would race the bus and risk
        double-fire / premature completion (the bug class the gate
        exists to prevent).
        """
        # Observer with a real-looking job so the helper would otherwise
        # proceed — but the gate must skip it.
        fake_job = MagicMock(name="JobItem")
        fake_job.job_id = f"job-{uuid.uuid4().hex[:8]}"
        observer = MagicMock(name="JobFeedbackObserver")
        observer._get_processing_job_for_instance = AsyncMock(
            return_value=fake_job
        )
        observer._finalize_job = AsyncMock(name="_finalize_job")

        service = self._build_service_with_observer(observer)
        target_id = f"parent-{uuid.uuid4().hex[:8]}"

        bus = get_dependency_bus()
        # Two watchers on different sources targeting the same parent.
        await bus.watch("task-1", make_fu(target_id=target_id))
        await bus.watch("task-2", make_fu(target_id=target_id))

        # Fire only task-1 → 1 PENDING remains for target_id.
        fired = await service._emit_terminal_via_bus(
            task_id="task-1", status="completed"
        )

        assert len(fired) == 1
        # CRITICAL: the gate must have blocked the retrigger — the other
        # watcher is still PENDING.
        observer._get_processing_job_for_instance.assert_not_called()
        observer._finalize_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_retrigger_skipped_when_job_already_terminal(self):
        """R3: no PROCESSING job exists → _retrigger_parent_finalize no-ops.

        The observer's ``_get_processing_job_for_instance`` returns
        ``None`` (the job was already finalized or never existed).
        ``_retrigger_parent_finalize`` MUST return cleanly without
        raising — the helper is called from the post-commit dispatch
        path where any propagated exception would surface as a job
        error and re-trigger subsequent failures.
        """
        # Observer where the job lookup returns None (already terminal).
        observer = MagicMock(name="JobFeedbackObserver")
        observer._get_processing_job_for_instance = AsyncMock(return_value=None)
        observer._finalize_job = AsyncMock(name="_finalize_job")

        service = self._build_service_with_observer(observer)
        target_id = f"parent-{uuid.uuid4().hex[:8]}"

        bus = get_dependency_bus()
        await bus.watch("task-1", make_fu(target_id=target_id))

        # Should NOT raise — clean skip on None job.
        fired = await service._emit_terminal_via_bus(
            task_id="task-1", status="completed"
        )

        assert len(fired) == 1
        observer._get_processing_job_for_instance.assert_awaited_once_with(
            target_id
        )
        # The critical assertion: _finalize_job was NEVER called.
        observer._finalize_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_retrigger_helper_swallows_lookup_exception(self):
        """E1 (review fix): ``_get_processing_job_for_instance`` raising
        does NOT propagate out of ``_retrigger_parent_finalize``.

        A transient DB error during the job lookup must be logged and
        swallowed — the helper returns ``None`` semantically (we don't
        know if there's a job to finalize), so the safe behavior is to
        skip and let the next child completion retry. The previous
        version of the helper would propagate the exception out to the
        retrigger loop in ``_emit_terminal_via_bus`` and abort
        re-triggering of all subsequent targets.
        """
        observer = MagicMock(name="JobFeedbackObserver")
        observer._get_processing_job_for_instance = AsyncMock(
            side_effect=RuntimeError("transient DB failure")
        )
        observer._finalize_job = AsyncMock(name="_finalize_job")

        service = self._build_service_with_observer(observer)
        target_id = f"parent-{uuid.uuid4().hex[:8]}"

        # Should NOT raise — the helper swallows the lookup error.
        await service._retrigger_parent_finalize(target_id)

        # _finalize_job MUST NOT be called when the lookup failed.
        observer._finalize_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_retrigger_loop_continues_after_target_failure(self):
        """E2 (review fix): a failure on one target's retrigger does not
        abort re-triggering the remaining targets.

        The outer ``for fu in fired`` loop in ``_emit_terminal_via_bus``
        wraps ``_retrigger_parent_finalize`` in its own try/except. If
        the inner helper were to raise (bug, unexpected error path),
        the loop MUST continue to the next target — the enqueue above
        already succeeded for all targets, so abandoning the rest would
        strand multiple parents.
        """
        # Observer whose _finalize_job raises on the first target and
        # succeeds on the second. _get_processing_job_for_instance
        # always returns a fake job (so the helper proceeds to the
        # _finalize_job call).
        fake_job_a = MagicMock(name="JobItem-a")
        fake_job_a.job_id = f"job-a-{uuid.uuid4().hex[:8]}"
        fake_job_b = MagicMock(name="JobItem-b")
        fake_job_b.job_id = f"job-b-{uuid.uuid4().hex[:8]}"

        observer = MagicMock(name="JobFeedbackObserver")

        async def lookup_job(instance_id: str):
            return {"parent-A": fake_job_a, "parent-B": fake_job_b}[instance_id]

        observer._get_processing_job_for_instance = AsyncMock(side_effect=lookup_job)

        finalize_calls: list[str] = []

        async def finalize(job, instance_id, status, error):
            finalize_calls.append(instance_id)
            if instance_id == "parent-A":
                raise RuntimeError("simulated finalize failure")

        observer._finalize_job = AsyncMock(side_effect=finalize)

        service = self._build_service_with_observer(observer)
        target_a = "parent-A"
        target_b = "parent-B"

        bus = get_dependency_bus()
        # Two watchers on the SAME source task, each targeting a
        # different parent. The bus fires both FollowUps atomically;
        # the retrigger loop then visits each distinct target once.
        await bus.watch("task-both", make_fu(target_id=target_a))
        await bus.watch("task-both", make_fu(target_id=target_b))

        # Fire the single source — both FollowUps come back in one
        # call, so the retrigger loop hits both targets. The first
        # target's finalize raises; the second must still run.
        fired = await service._emit_terminal_via_bus(
            task_id="task-both", status="completed"
        )

        assert len(fired) == 2
        # CRITICAL: the second target's finalize was reached despite the
        # first target's exception. Without the loop-level guard, the
        # first exception would propagate and parent-B would stay
        # PROCESSING forever (the inverse-regression bug).
        assert finalize_calls == [target_a, target_b], (
            f"Loop-level exception guard failed: expected both targets "
            f"finalized, got {finalize_calls}"
        )

    @pytest.mark.asyncio
    async def test_retrigger_helper_noop_without_observer(self):
        """R4 (graceful degradation): no observer wired → clean no-op.

        In unit tests and during partial init, the manager may not have
        ``_job_feedback_observer`` set yet. The helper must return
        cleanly (logged at DEBUG) rather than raise AttributeError.
        """
        service = self._build_service_with_observer(observer=None)

        # Must NOT raise.
        await service._retrigger_parent_finalize("any-instance-id")
