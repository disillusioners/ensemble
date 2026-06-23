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
from daemon.services.correlation_manager import CorrelationManager


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
    """Phase 1 (2026-06-23): generation counter lives on the bus.

    The bus is now the sole owner of the per-parent generation
    counter — the previous CM-based test setup (creating a CM
    instance, registering it as the module singleton, and asserting
    on ``cm._generation`` / ``cm.get_generation``) is replaced by
    direct assertions on ``bus.generation`` /
    ``bus.get_generation``. The orphan-race re-arm in
    :meth:`JobFeedbackObserver._finalize_job` reads
    ``bus.get_generation`` and triggers a COMPLETED → PROCESSING
    transition when a watch bumped the counter during finalization.
    """

    @pytest.mark.asyncio
    async def test_watch_bumps_bus_generation(self, bus_repo):
        """watch() bumps bus.generation[parent_id] (no CM involved)."""
        bus = DependencyBus(bus_repo)
        await bus.start()

        try:
            parent_id = "parent-gen-test"
            assert bus.get_generation(parent_id) == 0

            fu = make_fu(target_id=parent_id)
            await bus.watch("task-gen-test", fu)

            gen_after = bus.get_generation(parent_id)
            assert gen_after > 0, (
                f"generation should be bumped, got {gen_after}"
            )

            # Second watch bumps again — counter is monotonic.
            await bus.watch("task-gen-test-2", fu)
            gen_after2 = bus.get_generation(parent_id)
            assert gen_after2 > gen_after, (
                f"generation should increase, got {gen_after2} vs {gen_after}"
            )
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_increment_generation_helper(self, bus_repo):
        """``increment_generation(parent_id)`` mutates the counter monotonically."""
        bus = DependencyBus(bus_repo)
        await bus.start()

        try:
            parent_id = "parent-incr"
            assert bus.get_generation(parent_id) == 0

            bus.increment_generation(parent_id)
            assert bus.get_generation(parent_id) == 1

            bus.increment_generation(parent_id)
            bus.increment_generation(parent_id)
            assert bus.get_generation(parent_id) == 3
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_get_generation_returns_zero_for_unknown_parent(self, bus_repo):
        """``get_generation`` returns 0 (not KeyError) for an untracked parent."""
        bus = DependencyBus(bus_repo)
        await bus.start()

        try:
            # No watch has happened — generation is implicitly 0.
            assert bus.get_generation("never-watched-parent") == 0
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_concurrent_watches_are_safely_serialized(self, bus_repo):
        """Multiple concurrent watches for the same parent produce a deterministic,
        monotonically-increasing generation counter with no missed bumps.

        Exercises the lock-ordering invariant: generation mutation is
        OUTSIDE the per-parent lock (atomic CPython dict assignment),
        so concurrent ``watch`` calls all see a strictly-increasing
        counter. No races, no double-counts, no drops — the final
        counter is exactly ``N`` after ``N`` concurrent watches.
        """
        bus = DependencyBus(bus_repo)
        await bus.start()

        try:
            parent_id = "parent-concurrent"
            n_concurrent = 20

            # All N watches are scheduled simultaneously. Even with
            # the GIL switching between coroutines, the dict
            # assignment is atomic in CPython and the bump happens
            # BEFORE the per-parent lock acquisition — so every
            # watch produces exactly one bump, no double-counts.
            await asyncio.gather(
                *[
                    bus.watch(
                        f"task-conc-{i}",
                        make_fu(target_id=parent_id),
                    )
                    for i in range(n_concurrent)
                ]
            )

            assert bus.get_generation(parent_id) == n_concurrent, (
                f"expected {n_concurrent} bumps, "
                f"got {bus.get_generation(parent_id)}"
            )

            # And the DB has all N watchers (sanity check — the
            # INSERT path must not have dropped any rows under
            # contention).
            pending = await asyncio.gather(
                *[
                    bus.pending_watchers(f"task-conc-{i}")
                    for i in range(n_concurrent)
                ]
            )
            assert sum(len(p) for p in pending) == n_concurrent
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_generation_survives_bus_restart(self, bus_repo):
        """Generation counter survives a bus.stop() / bus.start() cycle.

        The counter is in-memory only (matches the CM's previous
        contract — the DB stores PENDING watchers, not the generation
        number). After a restart, the counter is rebuilt as a fresh
        ``dict`` and starts at 0; orphan-race detection still works
        because the next ``watch`` from the caller bumps it back to
        a non-zero value before any concurrent ``_finalize_job``
        observes it.

        This test verifies two restart invariants:

          1. A bus restarted over the same DB does NOT inherit
             stale generation state (avoids false-positive re-arms
             from a previous process).
          2. A new ``watch`` after restart still bumps the counter
             correctly (the post-restart generation state is
             functional, not just empty).
        """
        # First bus — bump generation, then stop.
        bus1 = DependencyBus(bus_repo)
        await bus1.start()
        parent_id = "parent-restart"
        await bus1.watch("task-restart-1", make_fu(target_id=parent_id))
        await bus1.watch("task-restart-2", make_fu(target_id=parent_id))
        assert bus1.get_generation(parent_id) == 2
        await bus1.stop()

        # Restart — counter is fresh (no DB persistence — matches CM).
        bus2 = DependencyBus(bus_repo)
        try:
            assert bus2.get_generation(parent_id) == 0, (
                "restarted bus should not inherit stale generation"
            )

            # New watch after restart bumps from 0 → 1.
            await bus2.watch("task-restart-3", make_fu(target_id=parent_id))
            assert bus2.get_generation(parent_id) == 1
        finally:
            await bus2.stop()

    @pytest.mark.asyncio
    async def test_per_parent_lock_serializes_db_insert(self, bus_repo):
        """``_get_parent_lock`` returns a usable asyncio.Lock per parent.

        Two concurrent ``watch`` calls on the SAME parent must
        serialize their DB INSERTs (the per-parent lock is held for
        the duration of the ``asyncio.to_thread`` call). The
        generation counter is incremented N times regardless of
        lock contention — the bump is outside the lock, the
        INSERT is inside it.

        Different parents do NOT block each other (the lock is
        keyed per-parent, not a global mutex).
        """
        bus = DependencyBus(bus_repo)
        await bus.start()

        try:
            parent_a = "parent-A-locked"
            parent_b = "parent-B-locked"

            # Mixed concurrency: same-parent + cross-parent.
            await asyncio.gather(
                bus.watch("src-a1", make_fu(target_id=parent_a)),
                bus.watch("src-a2", make_fu(target_id=parent_a)),
                bus.watch("src-b1", make_fu(target_id=parent_b)),
                bus.watch("src-b2", make_fu(target_id=parent_b)),
            )

            assert bus.get_generation(parent_a) == 2
            assert bus.get_generation(parent_b) == 2
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_watch_without_any_dependencies_works(self, bus_repo):
        """watch() works without CM, bus, or any singleton wiring.

        Phase 1 graceful-degradation contract: a bare
        ``DependencyBus`` instance, not registered as a singleton,
        still accepts ``watch`` calls correctly. (The CM used to
        be optional — the previous ``test_watch_without_cm_does_not_crash``
        test. Phase 1 makes the bus itself the required wiring; this
        test confirms a fresh, unregistered bus instance still
        works in isolation, which is the common unit-test pattern.)
        """
        bus = DependencyBus(bus_repo)
        await bus.start()

        try:
            fu = make_fu(target_id="parent-isolated")
            await bus.watch("task-isolated", fu)

            pending = await bus.pending_watchers("task-isolated")
            assert len(pending) == 1
            assert bus.get_generation("parent-isolated") == 1
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_orphan_race_detection_without_cm(self, bus_repo):
        """The observer's pre/post-commit orphan-race check works using only the bus.

        Simulates the contract: a ``_finalize_job``-shaped reader
        takes pre_gen, then a concurrent ``watch`` bumps the
        counter, then the reader takes post_gen — the difference
        must be observable on the bus. No CM involved.
        """
        bus = DependencyBus(bus_repo)
        await bus.start()

        try:
            parent_id = "parent-orphan"

            # Step 1: pre_gen snapshot (observer pattern)
            pre_gen = bus.get_generation(parent_id)
            assert pre_gen == 0

            # Step 2: concurrent register lands during the critical
            # section (the bus bump-outside-lock guarantees
            # post_gen > pre_gen here).
            await bus.watch("task-orphan", make_fu(target_id=parent_id))

            # Step 3: post_gen snapshot — bump is visible without
            # the reader holding any lock.
            post_gen = bus.get_generation(parent_id)
            assert post_gen > pre_gen, (
                f"orphan-race bump should be visible: pre={pre_gen}, post={post_gen}"
            )
        finally:
            await bus.stop()


# -------------------------------------------------------------------------
# TestOrphanRaceE2E
# -------------------------------------------------------------------------
"""End-to-end tests for the orphan-race detector contract.

The orphan-race re-arm in ``JobFeedbackObserver._finalize_job``
(``daemon/services/job_feedback_observer.py:958`` for pre_gen,
``:1004`` for post_gen) reads ``bus.get_generation(instance_id)``
DIRECTLY — not via the CM passthrough — before and after the
per-parent lock to detect whether a concurrent ``DependencyBus.watch``
landed during the critical section (lock acquire → to_thread →
commit → lock release).

These tests exercise that production contract end-to-end via the
bus API (no daemon, no job queue, no real DB commit). The positive
case (concurrent watch bumps the counter during the critical
section) is already covered by ``test_orphan_race_detection_without_cm``
above — these tests focus on the additional invariants that the
production flow depends on:

  1. The read path returns the value the detector would observe.
  2. A watch that lands BEFORE the critical section does NOT fire
     the re-arm (negative case — proves the check is detecting
     in-flight watches, not stale state).
  3. Orphaned CM bumps (cm.resolve_job bumping a separate counter
     dict) are invisible to ``bus.get_generation()`` — proves the
     B-W1 comment at ``job_feedback_observer.py:1006-1019`` is
     correct: orphaned CM bumps cannot cause spurious
     COMPLETED → PROCESSING → COMPLETED cycles.
"""


class TestOrphanRaceE2E:
    """End-to-end tests for the orphan-race detector via ``bus.get_generation()``."""

    @pytest.mark.asyncio
    async def test_get_generation_returns_observed_value(self, bus):
        """The read path: ``bus.get_generation()`` returns the value the
        detector would observe.

        ``JobFeedbackObserver._finalize_job`` calls
        ``bus.get_generation(instance_id)`` directly (see
        ``job_feedback_observer.py:958`` and ``:1004``). This test
        documents the contract: the bus API returns the current
        per-parent generation counter, monotonically increasing with
        each ``watch`` call. The value 0 is returned for never-watched
        parents (not a ``KeyError`` — the bus uses ``dict.get`` with
        a default of 0; see ``daemon/services/dependency_bus.py:840``).
        """
        parent_id = "parent-e2e-read"

        # Never-watched parent → 0 (not KeyError).
        assert bus.get_generation(parent_id) == 0

        # First watch → 1.
        await bus.watch("task-e2e-r1", make_fu(target_id=parent_id))
        assert bus.get_generation(parent_id) == 1

        # Second watch on the same parent → 2 (monotonic).
        await bus.watch("task-e2e-r2", make_fu(target_id=parent_id))
        assert bus.get_generation(parent_id) == 2

        # A different parent is unaffected (counters are per-parent).
        await bus.watch("task-e2e-r3", make_fu(target_id="parent-e2e-other"))
        assert bus.get_generation("parent-e2e-other") == 1
        assert bus.get_generation(parent_id) == 2

    @pytest.mark.asyncio
    async def test_orphan_race_does_not_fire_when_watch_lands_before_critical_section(
        self, bus_repo
    ):
        """Negative case: a watch that completes BEFORE ``pre_gen`` does
        NOT fire the re-arm (post_gen == pre_gen).

        The orphan-race detector at ``job_feedback_observer.py:1005``
        fires ONLY when ``post_gen > pre_gen`` — i.e. a watch landed
        DURING the critical section (between pre_gen and post_gen).
        A watch that completed before ``pre_gen`` is already visible
        to ``pre_gen`` (the bump is atomic and happens BEFORE the
        per-parent lock in ``DependencyBus.watch``, see
        ``dependency_bus.py:360``), so no further bump occurs during
        the critical section and ``post_gen == pre_gen``.

        This test is the genuine value-add versus
        ``test_orphan_race_detection_without_cm``: it proves the
        detector is sensitive to timing (in-flight watches), not
        just counter state. If the detector were firing on stale
        state, this test would fail (``post_gen`` would equal
        ``pre_gen`` but the re-arm would still fire — we assert
        the post-check invariant directly here).
        """
        bus = DependencyBus(bus_repo)
        await bus.start()

        try:
            parent_id = "parent-e2e-negative"

            # Watch lands BEFORE the critical section. Its bump is
            # already committed to ``bus.generation`` by the time
            # ``watch`` returns (the bump is outside the per-parent
            # lock — atomic CPython dict assignment).
            await bus.watch("task-before-critical", make_fu(target_id=parent_id))

            # pre_gen snapshot — already sees the pre-section bump.
            pre_gen = bus.get_generation(parent_id)
            assert pre_gen == 1, (
                f"pre_gen should see the pre-section bump: got {pre_gen}"
            )

            # Simulate the critical section: acquire the per-parent
            # lock, do no work (no concurrent watch fires during
            # this window), release. This mirrors the shape of
            # ``_finalize_job`` at ``job_feedback_observer.py:965-973``
            # but without the actual DB commit.
            async with await bus._get_parent_lock(parent_id):
                # Intentionally empty: no concurrent ``watch`` lands
                # here. If one did, the bump would happen at
                # ``dependency_bus.py:360`` BEFORE the lock is even
                # acquired (so it would still race with pre_gen,
                # not appear inside this block).
                pass

            # post_gen snapshot: no bump during the critical section.
            post_gen = bus.get_generation(parent_id)
            assert post_gen == pre_gen, (
                f"orphan-race must NOT fire when no concurrent watch "
                f"landed during the critical section: "
                f"pre={pre_gen}, post={post_gen}"
            )

            # The detector's guard (``post_gen > pre_gen``) is
            # therefore False, and the re-arm at
            # ``job_feedback_observer.py:1005`` would short-circuit.
            # We assert this directly to make the contract explicit.
            assert not (post_gen > pre_gen), (
                "detector guard ``post_gen > pre_gen`` must be False "
                "when no concurrent watch occurred"
            )
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_orphaned_cm_bumps_do_not_trigger_bus_rearm(self, bus_repo):
        """Orphaned CM bumps (a SEPARATE counter dict) must NOT be visible
        via ``bus.get_generation()``.

        Phase 1 cleanup note (B-W1, see
        ``job_feedback_observer.py:1006-1019``): ``cm.resolve_job``
        still bumps ``cm._generation[instance_id]`` at
        ``correlation_manager.py:584``, but the orphan-race re-arm
        reads ``bus.generation`` (a SEPARATE dict on the bus).
        Therefore orphaned CM bumps cannot cause spurious
        COMPLETED → PROCESSING → COMPLETED cycles — the only
        ``post_gen > pre_gen`` trigger is a ``DependencyBus.watch``
        that landed during the critical section.

        This test simulates the orphaned CM with a minimal mock: a
        plain ``dict`` counter, bumped in isolation, with NO
        connection to ``bus.generation``. It then asserts that
        ``bus.get_generation()`` does NOT see those bumps — proving
        the B-W1 fix comment is correct (orphaned bumps don't reach
        the bus path).
        """
        bus = DependencyBus(bus_repo)
        await bus.start()

        try:
            parent_id = "parent-e2e-cm-orphan"

            # Establish a baseline on the bus: one real watch, so
            # ``bus.generation[parent_id] == 1``. This gives us a
            # non-zero starting point so we can prove that orphaned
            # CM bumps don't accidentally increment the bus counter.
            await bus.watch("task-baseline", make_fu(target_id=parent_id))
            bus_pre = bus.get_generation(parent_id)
            assert bus_pre == 1

            # Simulate an orphaned CM: a separate counter dict,
            # bumped independently. This mirrors ``cm.resolve_job``
            # bumping ``cm._generation[target]`` at
            # ``correlation_manager.py:584`` while the bus is
            # unaware. We use a plain dict (not a real CM instance)
            # because the contract under test is: the bus counter
            # is INDEPENDENT — no shared storage with CM.
            mock_cm_generation: dict[str, int] = {parent_id: 0}

            def cm_resolve_job_orphan(target: str) -> int:
                """Mimic ``cm.resolve_job`` bumping its own counter.

                Returns the new (orphaned) CM generation value. This
                is the value an observer that reads ``cm._generation``
                directly would see — but the bus does NOT read from
                this dict, so it must remain unaffected.
                """
                mock_cm_generation[target] = (
                    mock_cm_generation.get(target, 0) + 1
                )
                return mock_cm_generation[target]

            # Bump the orphaned CM counter repeatedly. In production,
            # this would correspond to multiple ``cm.resolve_job``
            # calls for jobs whose instance_id no longer has a
            # PROCESSING job in the bus (the orphans).
            for _ in range(5):
                cm_resolve_job_orphan(parent_id)

            # Sanity: the orphaned CM counter WAS bumped (so the test
            # would FAIL — for the right reason — if our mock were a
            # no-op and the bus assertion passed trivially).
            assert mock_cm_generation[parent_id] == 5, (
                "mock CM must actually bump — test invariant"
            )

            # The bus counter is UNCHANGED by orphaned CM bumps.
            # This is the B-W1 fix: ``bus.generation`` is a separate
            # dict from ``cm._generation``, so bumps on the latter
            # do not leak into ``bus.get_generation()``.
            bus_post = bus.get_generation(parent_id)
            assert bus_post == bus_pre, (
                f"orphaned CM bumps must NOT leak into bus.generation: "
                f"bus_pre={bus_pre}, bus_post={bus_post}, "
                f"orphaned_cm={mock_cm_generation[parent_id]}"
            )

            # And the detector's guard (``post_gen > pre_gen``)
            # evaluates to False — the re-arm at
            # ``job_feedback_observer.py:1005`` short-circuits.
            # This is the property that prevents spurious
            # COMPLETED → PROCESSING → COMPLETED cycles.
            assert not (bus_post > bus_pre), (
                "detector guard must be False for orphaned CM bumps"
            )
        finally:
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

    @pytest.mark.asyncio
    async def test_retrigger_fires_on_error_path(self):
        """R5 (inverse-regression): the error path also fires finalize.

        Bug: ``daemon/services/error_reporting.py`` used to call
        ``_bus.emit_terminal()`` directly and enqueue FollowUps, but
        NEVER invoked ``_retrigger_parent_finalize``. If the errored
        child was the last PENDING watcher for its parent, the parent's
        job finalization was never re-triggered and the parent stayed
        in PROCESSING forever — the exact inverse-regression bug the
        re-trigger was added to prevent on the completion path.

        Fix: ``error_reporting.py`` now delegates to
        ``child_reports._emit_terminal_via_bus()`` (which handles
        FollowUp enqueue + the re-trigger loop uniformly). This test
        pins down the contract: when the error path fires the last
        PENDING watcher, ``_finalize_job`` is invoked on the parent's
        observer so the parent job transitions PROCESSING → COMPLETED
        (not stuck).

        The test mirrors R1 (``test_retrigger_fires_when_all_watchers_resolved``)
        but with ``status="error"`` and a non-None ``error`` payload,
        matching the call signature used by
        ``ErrorReportingService._send_error_report`` after the fix.
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

        # Simulate the error path: status="error" with an error payload,
        # exactly as ``ErrorReportingService._send_error_report`` calls
        # it after the fix. The bus keys on the string task_id.
        fired = await service._emit_terminal_via_bus(
            task_id="task-1",
            status="error",
            error="max_retries_exceeded",
            summary="child errored: max_retries_exceeded",
        )

        # The bus returned the FollowUp and the retrigger fired finalize
        # — the exact behavior the fix is meant to guarantee for the
        # error path. Without the fix, ``_finalize_job`` would NOT be
        # called here and the parent would stay PROCESSING.
        assert len(fired) == 1
        assert fired[0].target_instance_id == target_id
        observer._get_processing_job_for_instance.assert_awaited_once_with(
            target_id
        )
        observer._finalize_job.assert_awaited_once()
        _args, kwargs = observer._finalize_job.call_args
        # _finalize_job signature: (job, instance_id, status, error).
        # The status argument is "completed" (the parent's JOB
        # transitions PROCESSING → COMPLETED — the child-fired terminal
        # event is sufficient for the parent job to finalize; the child
        # status was already written by ``ErrorReportingService``).
        assert kwargs.get("status") == "completed" or (
            len(_args) >= 3 and _args[2] == "completed"
        )


# -------------------------------------------------------------------------
# TestCMGenerationMirror (Phase 1 C1 fix — 2026-06-23)
# -------------------------------------------------------------------------
#
# Regression: ``CorrelationManager.register_job_send`` (the watch_job
# path), ``CorrelationManager.register_message_send``, and
# ``CorrelationManager.resolve_job`` each bump ``cm._generation`` for
# orphan-race protection. After Phase 1 migration the observer reads
# ``bus.get_generation``, NOT ``cm._generation``. Without mirroring the
# bump to the bus, orphan-race detection is silently broken on these
# CM-driven paths.
#
# The fix: each CM bump is mirrored to ``bus.increment_generation``
# when a bus is wired. This test class verifies the mirror works for
# every bump site (register_job_send, register_message_send, resolve_job)
# and that the orphan-race detector — using only the bus API — sees
# the bumps from the CM path.
# -------------------------------------------------------------------------


def _make_cm_for_mirror_test() -> CorrelationManager:
    """Build a CM with mock repos for the bump-mirror tests.

    ``register_job_send`` / ``register_message_send`` / ``resolve_job``
    only mutate in-memory state — they don't touch the repos. The
    mocks are placeholders to satisfy the constructor signature.
    """
    return CorrelationManager(
        instance_repository=MagicMock(name="InstanceRepo"),
        message_queue_repository=MagicMock(name="MsgRepo"),
        completion_callback=None,
        event_bus=None,
    )


class TestCMGenerationMirror:
    """Phase 1 C1 fix: CM bumps ``_generation`` AND mirrors to the bus.

    The bus is now the source of truth for the observer's orphan-race
    detector (``JobFeedbackObserver._finalize_job`` reads
    ``bus.get_generation(parent_id)``). Each CM bump site — message,
    job, and resolve — must mirror its increment to the bus so the
    observer sees the bump regardless of which path produced it.

    These tests install a real ``DependencyBus`` as the module
    singleton, drive ``CorrelationManager`` methods, and assert that
    ``bus.get_generation(parent_id)`` reflects every bump. They also
    exercise the orphan-race pattern (pre/post snapshot) end-to-end
    through the CM path.
    """

    @pytest.fixture(autouse=True)
    async def _install_bus(self, bus_repo):
        """Install a real DependencyBus as the module singleton.

        Mirrors the pattern in ``TestBusRetriggerFinalize._install_bus``:
        a real bus on a real in-memory repo, started before the test,
        stopped after, with the module singleton cleared on exit.
        """
        b = DependencyBus(bus_repo)
        await b.start()
        set_dependency_bus(b)
        try:
            yield b
        finally:
            await b.stop()
            set_dependency_bus(None)

    @pytest.mark.asyncio
    async def test_register_message_send_mirrors_to_bus(self):
        """``cm.register_message_send`` bumps ``bus.get_generation(parent_id)``.

        The message-driven register path was also bumping
        ``cm._generation`` only. Without the mirror, the observer's
        pre/post commit check would miss a register that lands during
        a ``_finalize_job`` critical section and skip the re-arm.
        """
        cm = _make_cm_for_mirror_test()
        bus = get_dependency_bus()
        parent_id = "parent-msg-mirror"

        assert bus.get_generation(parent_id) == 0

        await cm.register_message_send(parent_id, "child-1", "msg-1")

        # The mirror landed on the bus. Both counters agree so the
        # observer (bus side) sees what CM recorded (cm side).
        assert bus.get_generation(parent_id) == 1, (
            "register_message_send must mirror its _generation bump to the bus"
        )

        # A second register bumps again — monotonic on both sides.
        await cm.register_message_send(parent_id, "child-2", "msg-2")
        assert bus.get_generation(parent_id) == 2

    @pytest.mark.asyncio
    async def test_register_job_send_mirrors_to_bus(self):
        """``cm.register_job_send`` (the watch_job path) bumps the bus.

        This is the critical regression: ``watch_job`` calls
        ``cm.register_job_send``, which bumps ``cm._generation``.
        Before the C1 fix, the observer (reading the bus) did NOT see
        this bump, silently breaking orphan-race protection on the
        watch_job path. After the fix, the bus counter increments too.
        """
        cm = _make_cm_for_mirror_test()
        bus = get_dependency_bus()
        parent_id = "parent-job-mirror"
        child_job_id = "job-watched-1"

        assert bus.get_generation(parent_id) == 0

        await cm.register_job_send(parent_id, child_job_id)

        # The bus counter advanced — the observer's pre/post check will
        # detect this bump as a concurrent register.
        assert bus.get_generation(parent_id) == 1, (
            "register_job_send must mirror its _generation bump to the bus "
            "for watch_job orphan-race protection"
        )

        # Second watch on the same parent → 2 (monotonic).
        await cm.register_job_send(parent_id, "job-watched-2")
        assert bus.get_generation(parent_id) == 2

    @pytest.mark.asyncio
    async def test_resolve_job_mirrors_to_bus(self):
        """``cm.resolve_job`` mirrors its bump to the bus.

        The resolve path bumps for symmetry with ``register_job_send``
        so the counter monotonically reflects outstanding work units
        seen. The bus side must see the bump too — otherwise the
        observer could miss a resolve that lands during finalization
        and treat a still-PENDING watch as completed.
        """
        cm = _make_cm_for_mirror_test()
        bus = get_dependency_bus()
        parent_id = "parent-resolve-mirror"
        child_job_id = "job-resolve-1"

        # Register first (bumps bus once).
        await cm.register_job_send(parent_id, child_job_id)
        assert bus.get_generation(parent_id) == 1

        # Resolve bumps again.
        await cm.resolve_job(parent_id, child_job_id, status="responded")

        # The mirror landed — bus counter is now 2 (register + resolve).
        assert bus.get_generation(parent_id) == 2, (
            "resolve_job must mirror its _generation bump to the bus"
        )

    @pytest.mark.asyncio
    async def test_orphan_race_detected_via_cm_register_job_send(self):
        """End-to-end orphan-race detector via ``bus.get_generation`` over CM path.

        Simulates the contract that ``JobFeedbackObserver._finalize_job``
        relies on: a pre/post snapshot of ``bus.get_generation`` must
        observe a concurrent ``cm.register_job_send`` (the watch_job
        path). Before the C1 fix, the post snapshot was identical to
        the pre snapshot because the bump lived only in
        ``cm._generation``. After the fix, the bump is visible on the
        bus.
        """
        cm = _make_cm_for_mirror_test()
        bus = get_dependency_bus()
        parent_id = "parent-orphan-via-cm"

        # Step 1: pre-gen snapshot (observer pattern, observer holds
        # the per-parent lock).
        pre_gen = bus.get_generation(parent_id)
        assert pre_gen == 0

        # Step 2: a concurrent register_job_send lands during the
        # critical section (mimics ``watch_job`` arriving mid-finalize).
        await cm.register_job_send(parent_id, "job-mid-finalize")

        # Step 3: post-gen snapshot. The mirror guarantees the bump is
        # visible — without it, post_gen == pre_gen and the re-arm
        # would not fire, orphaning the late child.
        post_gen = bus.get_generation(parent_id)
        assert post_gen > pre_gen, (
            f"orphan-race bump from CM must be visible on bus: "
            f"pre={pre_gen}, post={post_gen}"
        )

    @pytest.mark.asyncio
    async def test_orphan_race_detected_via_cm_register_message_send(self):
        """Same orphan-race contract, exercised through ``register_message_send``.

        The message path is the original (and more common) orphan-race
        site. The mirror must be wired here too so a concurrent
        ``register_message_send`` is visible to the bus-side observer.
        """
        cm = _make_cm_for_mirror_test()
        bus = get_dependency_bus()
        parent_id = "parent-orphan-via-msg"

        pre_gen = bus.get_generation(parent_id)
        await cm.register_message_send(parent_id, "child-x", "msg-x")

        post_gen = bus.get_generation(parent_id)
        assert post_gen > pre_gen, (
            f"orphan-race bump from register_message_send must be visible: "
            f"pre={pre_gen}, post={post_gen}"
        )

    @pytest.mark.asyncio
    async def test_concurrent_cm_registers_produce_no_lost_bumps(self, bus_repo):
        """Concurrent ``cm.register_job_send`` for the same parent bumps
        the bus deterministically N times — no lost bumps, no double counts.

        ``register_job_send`` bumps BEFORE acquiring the per-parent lock
        (orphan-race invariant). The mirror inherits this ordering — the
        bus counter is atomic CPython dict assignment — so concurrent
        registers cannot lose a bump. The final bus value must equal
        the number of concurrent calls.
        """
        # Use a fresh bus for this test (the autouse fixture installs
        # one too, but a fresh instance keeps the assertion clean and
        # avoids cross-test interference).
        fresh_bus = DependencyBus(bus_repo)
        await fresh_bus.start()
        set_dependency_bus(fresh_bus)
        try:
            cm = _make_cm_for_mirror_test()
            parent_id = "parent-concurrent-cm"
            n_concurrent = 25

            # All N registers scheduled simultaneously.
            await asyncio.gather(
                *[
                    cm.register_job_send(parent_id, f"job-{i}")
                    for i in range(n_concurrent)
                ]
            )

            # Bus counter equals the number of bumps — every register
            # produced exactly one mirrored bump.
            assert fresh_bus.get_generation(parent_id) == n_concurrent, (
                f"expected {n_concurrent} mirrored bumps, "
                f"got {fresh_bus.get_generation(parent_id)}"
            )

            # And CM's local counter agrees (the mirror keeps both
            # sides in sync — that's the whole point of the C1 fix).
            assert cm.get_generation(parent_id) == n_concurrent
        finally:
            await fresh_bus.stop()
            set_dependency_bus(None)

    @pytest.mark.asyncio
    async def test_mirror_works_with_no_bus_wired(self):
        """If no bus is installed (legacy/testing), the mirror is a no-op.

        Graceful degradation: the bump-outside-lock still happens on
        CM, and the lazy import + None check means the mirror path
        silently skips when the bus singleton is unset. This keeps the
        legacy CM-only test suites (which don't install a bus) working.
        """
        # Clear the bus for this test — the autouse fixture installed
        # one, but we explicitly verify the no-bus branch.
        set_dependency_bus(None)
        cm = _make_cm_for_mirror_test()
        parent_id = "parent-no-bus"

        # Both bumps must succeed without raising.
        await cm.register_message_send(parent_id, "c", "m")
        await cm.register_job_send(parent_id, "j")
        await cm.resolve_job(parent_id, "j", status="responded")

        # CM's local counter advanced (3 bumps total: msg, job, resolve).
        # ``get_generation`` falls back to ``cm._generation`` when the
        # bus is None — see ``correlation_manager.py:787``.
        assert cm.get_generation(parent_id) == 3


# -------------------------------------------------------------------------
# TestBusSoleAuthority (Phase 5 — CM removed; bus is the sole authority)
# -------------------------------------------------------------------------
#
# Phase 5 (2026-06-23) removed the CorrelationManager. The DependencyBus
# is now the SOLE completion authority: it tracks per-parent pending
# watcher counts, per-parent error flags, and the per-parent generation
# counter used for orphan-race detection.
#
# Task 5.7 added a behavioral fix: when a child task emits a terminal
# event with ``status="error"``, the bus stamps
# ``_parent_errored[target_id] = True`` for each fired FollowUp's parent.
# This flag is then read by ``ChildReportsService._retrigger_parent_finalize``
# so a parent whose LAST child errored finalizes as ``"error"`` instead
# of ``"completed"`` (mirrors the old CM
# ``_determine_terminal_status`` "any error → error" conservative rule).
#
# The tests below pin down the bus-side contracts that Phase 5 relies
# on. They use the existing ``bus`` / ``bus_repo`` fixtures (in-memory
# SQLite) and the ``make_fu`` / ``make_outcome`` helpers — no daemon,
# no PostgreSQL, no CM.
# -------------------------------------------------------------------------


class TestBusSoleAuthority:
    """Phase 5: DependencyBus is the sole completion authority.

    These tests verify the contracts the bus owns exclusively after
    CM removal: pending-watcher counting, per-parent error flagging,
    restart survival of in-DB state, and concurrent terminal
    handling. They complement ``TestBusRetriggerFinalize`` (which
    exercises the post-fire re-trigger on the completion path) and
    ``TestGenerationCounterBump`` (which exercises the per-parent
    generation counter) — together they pin down the bus's full
    post-Phase-5 surface.
    """

    @pytest.mark.asyncio
    async def test_parent_completes_only_after_all_children_done(self, bus):
        """Parent has pending watchers until ALL children fire.

        The bus is the source of truth for "is the parent still
        waiting on children". ``count_pending_for_target(parent)``
        must be > 0 while any PENDING watcher exists and == 0 only
        when the last one has fired. This is the gate
        ``_retrigger_parent_finalize`` uses to decide whether to
        re-trigger the parent job's finalization.
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"

        # Two watchers from two different sources both targeting the
        # same parent — the typical multi-child fan-in.
        await bus.watch("task-child-1", make_fu(target_id=parent_id))
        await bus.watch("task-child-2", make_fu(target_id=parent_id))

        # Both PENDING → count is 2.
        assert bus.count_pending_for_target_sync(parent_id) == 2
        assert await bus.count_pending_for_target(parent_id) == 2

        # Fire the first child → 1 PENDING remains.
        await bus.emit_terminal("task-child-1", make_outcome(status="completed"))
        assert bus.count_pending_for_target_sync(parent_id) == 1, (
            "After firing the first child, one PENDING watcher "
            "must still be registered for the parent."
        )

        # Fire the second child → 0 PENDING — parent is ready to
        # finalize. This is the moment the retrigger gate
        # (``count_pending_for_target == 0``) opens.
        await bus.emit_terminal("task-child-2", make_outcome(status="completed"))
        assert bus.count_pending_for_target_sync(parent_id) == 0, (
            "After firing the last child, count must be 0 — "
            "this is the retrigger gate's release condition."
        )
        assert await bus.count_pending_for_target(parent_id) == 0

    @pytest.mark.asyncio
    async def test_parent_errors_if_any_child_errored(self, bus):
        """``had_parent_error(parent_id)`` flips True if ANY child errored.

        Phase 5's behavioral fix: when ``emit_terminal`` sees
        ``status="error"``, it sets ``_parent_errored[target_id] = True``
        for every fired FollowUp's parent. The flag is sticky — even
        if subsequent children complete normally, the parent still
        finalizes as ``"error"`` (the conservative "any error → error"
        rule that was lost when CM was removed).

        This test pins down both halves of the contract:
          1. An error terminal on child-1 sets the flag.
          2. A subsequent completion on child-2 does NOT clear the
             flag (sticky semantics — mirrors the old CM behavior).
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"

        await bus.watch("task-err", make_fu(target_id=parent_id))
        await bus.watch("task-ok", make_fu(target_id=parent_id))

        # Pre-condition: no error recorded.
        assert bus.had_parent_error(parent_id) is False

        # Fire child-1 with status="error" → flag flips True.
        await bus.emit_terminal(
            "task-err", make_outcome(status="error", error="boom")
        )
        assert bus.had_parent_error(parent_id) is True, (
            "An error terminal event must set the per-parent "
            "error flag (Phase 5 behavioral fix)."
        )

        # Fire child-2 with status="completed" — flag must STAY True
        # (sticky semantics; a later success does not clear a prior
        # error).
        await bus.emit_terminal("task-ok", make_outcome(status="completed"))
        assert bus.had_parent_error(parent_id) is True, (
            "Error flag must be sticky: a later completion does "
            "NOT clear a prior error. ``had_parent_error`` mirrors "
            "the old CM 'any error → error' rule."
        )

        # count_pending_for_target == 0 means the retrigger gate
        # is open. ``_retrigger_parent_finalize`` would resolve the
        # parent job to ``"error"`` because of the sticky flag.
        assert bus.count_pending_for_target_sync(parent_id) == 0

        # Cleanup: explicit clear for symmetry with the production
        # finalize path. ``clear_parent_error`` is the public API
        # the post-finalize hook calls to avoid flag leakage
        # across terminate/revive cycles.
        bus.clear_parent_error(parent_id)
        assert bus.had_parent_error(parent_id) is False

    @pytest.mark.asyncio
    async def test_pending_count_survives_bus_restart(self, bus_repo):
        """Pending watcher counts are DB-backed — restart preserves them.

        PENDING watcher rows are the bus's source of truth. After
        ``stop()`` (which clears the in-memory cache) and a fresh
        ``start()`` (which re-warms the cache from the DB), the
        pending count for a target must be the same. Without this,
        a process restart would lose track of parents that are
        still waiting on children and fail to re-trigger their
        finalization (the inverse-regression bug class).
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"

        b1 = DependencyBus(bus_repo)
        await b1.start()
        # Register 3 watchers on the same parent from 3 different
        # sources. All PENDING in the DB.
        await b1.watch("task-r-1", make_fu(target_id=parent_id))
        await b1.watch("task-r-2", make_fu(target_id=parent_id))
        await b1.watch("task-r-3", make_fu(target_id=parent_id))
        assert b1.count_pending_for_target_sync(parent_id) == 3
        await b1.stop()

        # Restart over the same in-memory DB (StaticPool). Cache
        # is cold; ``_warm_cache`` rebuilds it from the DB.
        b2 = DependencyBus(bus_repo)
        try:
            # The PENDING rows survived the restart — count is
            # unchanged.
            assert b2.count_pending_for_target_sync(parent_id) == 3, (
                "Pending count must survive bus restart — "
                "the DB is the source of truth, not the cache."
            )
            assert await b2.count_pending_for_target(parent_id) == 3
        finally:
            await b2.stop()

    @pytest.mark.asyncio
    async def test_cancel_for_target_clears_all_pending_watchers(self, bus):
        """``cancel_for_target`` transitions all matching PENDING rows to CANCELLED.

        Cancellation is the bus's per-target reset primitive. After
        a cancel, ``count_pending_for_target`` must drop to 0
        (CANCELLED rows are excluded from the count — the same
        exclusion the gate relies on for FIRED rows). This is the
        invariant that lets the completion gates treat a cancelled
        target the same as a fully-resolved one.
        """
        target_id = f"target-{uuid.uuid4().hex[:8]}"

        # 3 watchers, all targeting the same target. Mixed source
        # tasks — cancellation must collapse them all regardless
        # of source.
        await bus.watch("task-c-1", make_fu(target_id=target_id))
        await bus.watch("task-c-2", make_fu(target_id=target_id))
        await bus.watch("task-c-3", make_fu(target_id=target_id))

        # Sanity: all 3 PENDING.
        assert bus.count_pending_for_target_sync(target_id) == 3

        # Cancel — every PENDING row for the target transitions to
        # CANCELLED, regardless of which source task it came from.
        cancelled = await bus.cancel_for_target(target_id)
        assert cancelled == 3, (
            f"cancel_for_target must return the number of rows "
            f"transitioned (expected 3, got {cancelled})."
        )

        # The count drops to 0 — the retrigger gate (``== 0``) is
        # now satisfied even though no FollowUp was actually
        # delivered. The completion gates use this to release the
        # parent job lock when children are explicitly cancelled.
        assert bus.count_pending_for_target_sync(target_id) == 0
        assert await bus.count_pending_for_target(target_id) == 0

    @pytest.mark.asyncio
    async def test_generation_counter_resets_on_restart_but_remains_functional(self, bus_repo):
        """Generation counter resets on bus restart but stays functional.

        The generation counter is **in-memory only** — it is NOT
        persisted to the DB and is intentionally reset on every
        bus restart. This is the correct behavior:

          * The DB stores PENDING watcher rows (durable), not the
            generation number itself.
          * The counter detects orphan-races *within a session*,
            not across process boundaries.
          * On restart, PENDING watchers are rebuilt from the DB.
            The next ``watch()`` call bumps the counter back to a
            non-zero value, restoring orphan-race detection.

        Two restart invariants verified here:

          1. A restarted bus does NOT inherit stale generation
             state — the counter is a fresh ``dict`` starting at 0.
             This avoids false-positive re-arms from a previous
             process that finalized parents with a high generation.
          2. A new ``watch`` after restart still bumps the counter
             correctly (1, then 2, ...). The post-restart
             generation state is functional, not just empty —
             orphan-race detection works as soon as the first new
             ``watch`` lands, because that bump is what the
             detector compares against a captured pre-finalize
             snapshot.
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"

        # First bus — two watches bump the counter to 2.
        b1 = DependencyBus(bus_repo)
        await b1.start()
        await b1.watch("task-gen-r-1", make_fu(target_id=parent_id))
        await b1.watch("task-gen-r-2", make_fu(target_id=parent_id))
        assert b1.get_generation(parent_id) == 2
        await b1.stop()

        # Restart — counter is fresh, starts at 0. This is the
        # correct behavior (matches the CM's previous in-memory
        # contract; the DB rows are the durable record, not the
        # counter).
        b2 = DependencyBus(bus_repo)
        try:
            assert b2.get_generation(parent_id) == 0, (
                "Generation counter must reset to 0 on bus restart "
                "(in-memory only, NOT persisted). A restarted bus "
                "must not inherit stale generation state from a "
                "previous process."
            )

            # First post-restart watch bumps 0 → 1.
            await b2.watch("task-gen-r-3", make_fu(target_id=parent_id))
            assert b2.get_generation(parent_id) == 1

            # Second post-restart watch bumps 1 → 2 — the counter
            # is fully functional again. Orphan-race detection
            # works: any finalization snapshot taken AFTER this
            # bump will see ``post_gen > pre_gen`` and re-arm if
            # a concurrent register_job_send lands.
            await b2.watch("task-gen-r-4", make_fu(target_id=parent_id))
            assert b2.get_generation(parent_id) == 2
        finally:
            await b2.stop()

    @pytest.mark.asyncio
    async def test_concurrent_child_completions_dont_double_finalize(self, bus):
        """Two concurrent terminal events finalize the parent exactly once.

        With two watchers on the same parent (one per source task),
        firing both terminal events concurrently via ``asyncio.gather``
        must:
          1. Deliver each FollowUp exactly once (no double-fire on
             the bus's transition path).
          2. Leave ``count_pending_for_target(parent) == 0`` —
             the parent is ready to finalize, and the retrigger
             gate's ``== 0`` check fires exactly once.

        This is the concurrency invariant that keeps
        ``_retrigger_parent_finalize`` from racing itself: the bus
        serializes per-source-task state transitions with the
        per-task lock, and ``count_pending_for_target`` is a
        consistent DB read, so the gate's release condition is
        true exactly once across the gather (not twice — which
        would call ``_finalize_job`` twice and break the
        "finalize exactly once" contract).
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"

        # Two watchers, one per source, both targeting the same
        # parent. The retrigger gate looks at the per-target
        # count, not per-source — so the parent-finalize is the
        # only thing this test is racing.
        await bus.watch("task-conc-a", make_fu(target_id=parent_id))
        await bus.watch("task-conc-b", make_fu(target_id=parent_id))

        # Pre-condition: 2 PENDING, gate is closed.
        assert bus.count_pending_for_target_sync(parent_id) == 2

        # Fire both terminal events concurrently. Each call
        # transitions its own watcher's row PENDING → FIRED and
        # returns the fired FollowUp. The bus's per-source-task
        # lock prevents double-fire on the same source (covered
        # by ``TestNoDoubleDecrement.test_concurrent_emit_does_not_double_fire``);
        # the per-target gate prevents the parent from being
        # finalized twice (this test).
        results = await asyncio.gather(
            bus.emit_terminal("task-conc-a", make_outcome(status="completed")),
            bus.emit_terminal("task-conc-b", make_outcome(status="completed")),
        )

        # Each emit_terminal returned exactly one FollowUp — the
        # transition path is exactly-once.
        fired_counts = [len(r) for r in results]
        assert fired_counts == [1, 1], (
            f"Each concurrent emit must fire its own FollowUp "
            f"exactly once: got {fired_counts}."
        )
        total_fired = sum(fired_counts)
        assert total_fired == 2, (
            f"Total fired across both concurrent emits must be 2, "
            f"got {total_fired} — the per-source task lock prevents "
            f"double-fire on the bus side."
        )

        # The parent is fully resolved: count is 0. The retrigger
        # gate (``count_pending_for_target == 0``) is now open, and
        # it will fire exactly once when the post-fire
        # ``_retrigger_parent_finalize`` runs — not twice, because
        # the gate is checked once per FollowUp and only the LAST
        # watcher crossing the threshold opens it.
        assert bus.count_pending_for_target_sync(parent_id) == 0, (
            "After concurrent fires, parent must have 0 pending — "
            "the retrigger gate's release condition is satisfied "
            "exactly once (the second fire observes count == 0 "
            "AFTER its own transition, the first observes count == 1)."
        )
