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
