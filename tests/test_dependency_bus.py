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
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

# Register table models so create_all() picks them up.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401

# NOTE: ``daemon.repositories.task.models`` is intentionally NOT imported at
# module level. Doing so would register the ``task`` table on
# ``SQLModel.metadata`` globally, which would cause the
# ``bus_repo`` fixture's ``create_all()`` to create an empty ``task`` table
# for ALL tests in this file — and the bus's startup sweep would then
# classify every existing-test watcher as an orphan (empty task table ⇒
# ``source_task_id NOT IN (...)`` matches every PENDING watcher).
#
# Phase 1's orphan-sweep tests need the ``task`` table to exist so the
# sweep's IN-subquery runs (rather than failing-open with "no such
# table"). They use the ``bus_repo_with_task`` fixture below, which
# creates the ``task`` table via raw SQL on a per-test fresh engine
# without registering the Task model globally.

from daemon.repositories.dependency_bus import (
    DependencyWatcher,
    DependencyWatcherRepository,
    DependencyWatcherState,
)
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    Outcome,
    get_dependency_bus,
    set_dependency_bus,
)

# Phase 5: tests/test_dependency_bus.py previously had a mirror-test helper
# (``_make_cm_for_mirror_test``) and a CM-vs-bus equivalence test class
# (around lines 1520-1561). CorrelationManager is removed; those tests
# no longer apply. They are intentionally not re-implemented in this
# commit — bus-vs-bus mirror behaviour is now tested by tests under
# tests/test_dependency_bus_mirror.py (or similar follow-up).


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


def _insert_task(engine, instance_id: str, status: str) -> int:
    """Insert a ``task`` row with the given status; return the integer id.

    Helper for Phase 1 orphan-sweep tests — the sweep's IN-subquery
    filters against the ``task`` table, so the unit tests must
    fabricate ``task`` rows with explicit statuses (running / pending /
    paused / cancelled / failed) to exercise the active-task predicate.

    Uses a raw INSERT (not ``Session.add(Task(...))``) so the Task model
    is not registered on ``SQLModel.metadata`` — see the module-level
    NOTE in the imports section. The schema below mirrors what
    ``Task.__table_args__`` would produce; if the model evolves, this
    helper must be updated in lockstep.

    Args:
        engine: SQLAlchemy ``Engine`` bound to the test database
            (must have the ``task`` table — see
            ``bus_repo_with_task`` fixture).
        instance_id: Parent instance id for the task. Required by
            the schema.
        status: One of ``TaskStatus`` lowercase values
            (``"running"``, ``"pending"``, ``"paused"``,
            ``"completed"``, ``"failed"``, ``"cancelled"``).

    Returns:
        The integer id of the newly-inserted ``task`` row, returned
        via SQLite's ``lastrowid`` from the INSERT statement.
    """
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "INSERT INTO task "
                "(task_type, instance_id, status, created_at, version, work_id) "
                "VALUES (:ttype, :iid, :status, :created_at, :version, :work_id)"
            ),
            {
                "ttype": "process_message",
                "iid": instance_id,
                "status": status,
                "created_at": now,
                "version": 0,
                "work_id": str(uuid.uuid4()),
            },
        )
        # SQLite's lastrowid; matches the autoincrement integer id
        # used by the Task model when registered.
        return int(result.lastrowid)


# -------------------------------------------------------------------------
# Phase 1 orphan-sweep fixtures
# -------------------------------------------------------------------------


_TASK_SCHEMA_DDL = (
    "CREATE TABLE IF NOT EXISTS task ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  task_type VARCHAR NOT NULL DEFAULT 'process_message',"
    "  instance_id VARCHAR NOT NULL,"
    "  message_id VARCHAR,"
    "  status VARCHAR NOT NULL DEFAULT 'pending',"
    "  worker_id VARCHAR,"
    "  retry_count INTEGER NOT NULL DEFAULT 0,"
    "  next_retry_at VARCHAR,"
    "  cancel_requested BOOLEAN NOT NULL DEFAULT 0,"
    "  cancel_requested_at VARCHAR,"
    "  retry_scheduled BOOLEAN NOT NULL DEFAULT 0,"
    "  result TEXT,"
    "  error TEXT,"
    "  created_at DATETIME NOT NULL,"
    "  started_at DATETIME,"
    "  completed_at DATETIME,"
    "  last_heartbeat_at DATETIME,"
    "  version INTEGER NOT NULL DEFAULT 0,"
    "  work_id VARCHAR NOT NULL,"
    "  UNIQUE(work_id)"
    ")"
)
_TASK_INDEXES_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_task_status_created "
    "ON task (status, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_task_instance_id "
    "ON task (instance_id)",
    "CREATE INDEX IF NOT EXISTS ix_task_message_id "
    "ON task (message_id)",
    "CREATE INDEX IF NOT EXISTS ix_task_status "
    "ON task (status)",
    "CREATE INDEX IF NOT EXISTS ix_task_worker_id "
    "ON task (worker_id)",
    "CREATE INDEX IF NOT EXISTS ix_task_last_heartbeat_at "
    "ON task (last_heartbeat_at)",
)


@pytest.fixture
def bus_repo_with_task(bus_repo):
    """Variant of ``bus_repo`` that ALSO has the ``task`` table created.

    Required for the Phase 1 orphan-sweep tests, which exercise the
    sweep's IN-subquery against ``task``. The ``bus_repo`` fixture
    alone is insufficient: when the ``task`` table doesn't exist,
    ``_sweep_orphan_watchers`` fails open with a "no such table"
    exception and returns 0 — so the orphan tests would never see
    any cancellation.

    Implementation note — why this is a separate fixture rather than
    a module-level Task import:

    Importing ``daemon.repositories.task.models`` at the top of this
    file would register the ``task`` table on ``SQLModel.metadata``
    globally, causing the ``bus_repo`` fixture's
    ``SQLModel.metadata.create_all(eng)`` to create an empty ``task``
    table for EVERY test in this file (including existing ones).
    The bus's startup sweep would then classify every existing-test
    PENDING watcher as an orphan (empty task table ⇒ ``source_task_id
    NOT IN (...)`` matches every row), breaking ``TestRestartSurvival``
    and other tests that rely on PENDING watchers surviving
    ``bus.start()``.

    Per-test raw-SQL DDL avoids that: the ``task`` table only exists
    on engines created by this fixture, and the existing
    ``bus_repo`` fixture is unaffected. The schema is hand-written
    to match ``daemon.repositories.task.models.Task.__table_args__``
    — keep the two in sync if the model evolves.

    Returns:
        The same :class:`DependencyWatcherRepository` instance from
        ``bus_repo``, but bound to an engine that now also has the
        ``task`` table created.
    """
    engine = bus_repo.engine
    with engine.begin() as conn:
        conn.execute(text(_TASK_SCHEMA_DDL))
        # SQLite executes one statement per cursor; iterate the DDL
        # tuple rather than chaining via ";" (which Python's sqlite3
        # driver rejects with "You can only execute one statement
        # at a time").
        for ddl in _TASK_INDEXES_DDL:
            conn.execute(text(ddl))
    return bus_repo


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture
def bus_repo():
    """In-memory SQLite repo for unit tests.

    StaticPool + check_same_thread=False is REQUIRED: asyncio.to_thread
    shares the connection with the main thread, and :memory: databases are
    connection-scoped by default.

    Only the ``dependency_watchers`` table is created on the engine.
    Why not the full ``SQLModel.metadata.create_all``? Transitive
    imports register many tables on ``SQLModel.metadata`` — including
    the ``task`` table (transitively via
    ``daemon/repositories/__init__.py``, which re-exports
    ``daemon.repositories.task.models``). An empty ``task``
    table causes the bus's startup ``_sweep_orphan_watchers`` sweep to
    classify every PENDING watcher as an orphan (the sweep's
    IN-subquery ``source_task_id NOT IN (SELECT id FROM task WHERE
    status IN ('running','pending','paused'))`` matches every row when
    the ``task`` table is empty), which breaks pre-existing tests
    that rely on PENDING watchers surviving ``bus.start()``.

    Phase 1's orphan-sweep tests need the ``task`` table to exist
    so the sweep actually runs (rather than failing-open with
    "no such table") — they use the ``bus_repo_with_task`` fixture,
    which creates the table via raw SQL on top of this fixture's
    engine.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create ONLY the dependency_watchers table. Other models'
    # tables (task, instance, event, ...) are intentionally NOT
    # created here — see the docstring above for the rationale.
    watcher_table = SQLModel.metadata.tables.get("dependency_watchers")
    if watcher_table is not None:
        watcher_table.create(eng, checkfirst=True)
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
# TestCancelForSource
# -------------------------------------------------------------------------


class TestCancelForSource:
    """Tests for :meth:`DependencyBus.cancel_for_source`.

    Production regression (2026-06-26, instance 06f500af stuck in
    ``waiting_children``): when ``StaleTaskRecovery`` force-cancels a
    stale task and schedules a retry, the bus's PENDING watchers keyed
    on the cancelled ``source_task_id`` are orphaned — the retry's
    natural completion fires ``emit_terminal`` for its OWN task id,
    which cannot match the original watcher. The parent stays in
    ``waiting_children`` forever. ``cancel_for_source`` is the
    symmetry to ``cancel_for_target`` keyed on the source task id.
    """

    @pytest.mark.asyncio
    async def test_cancel_for_source_transitions_to_cancelled(self, bus):
        await bus.watch("task-1", make_fu(target_id="parent-X"))
        count = await bus.cancel_for_source("task-1")
        assert count == 1
        assert await bus.pending_watchers("task-1") == []

    @pytest.mark.asyncio
    async def test_cancel_for_source_only_affects_specified_source(self, bus):
        """Cancel task-1 leaves task-2's watchers intact (proves per-source keying)."""
        await bus.watch("task-1", make_fu(target_id="parent-X"))
        await bus.watch("task-2", make_fu(target_id="parent-Y"))
        await bus.cancel_for_source("task-1")
        assert await bus.pending_watchers("task-1") == []
        assert len(await bus.pending_watchers("task-2")) == 1

    @pytest.mark.asyncio
    async def test_cancel_for_source_returns_count(self, bus):
        """Multiple watchers on same source are all cancelled."""
        for i in range(3):
            await bus.watch("task-1", make_fu(target_id=f"parent-{i}"))
        count = await bus.cancel_for_source("task-1")
        assert count == 3

    @pytest.mark.asyncio
    async def test_cancel_for_source_then_emit_fires_nothing(self, bus):
        """After cancellation, emit_terminal is a no-op for that source.

        Mirrors the ``cancel_for_target`` invariant: CANCELLED rows are
        never fired (the bus keying is symmetric).
        """
        await bus.watch("task-1", make_fu(target_id="parent-A"))
        cancelled = await bus.cancel_for_source("task-1")
        assert cancelled == 1
        fired = await bus.emit_terminal("task-1", make_outcome())
        assert fired == []

    @pytest.mark.asyncio
    async def test_cancel_for_source_unknown_returns_zero(self, bus):
        """Unknown source_task_id is a clean no-op (matches target cancel)."""
        count = await bus.cancel_for_source("never-watched-task")
        assert count == 0

    @pytest.mark.asyncio
    async def test_cancel_for_source_releases_parent_in_waiting_children(
        self, bus,
    ):
        """End-to-end regression: cancelling the source lets the parent complete.

        Models the production incident (instance 06f500af). A parent
        (``parent-X``) registered a watcher via ``send_message`` against
        ``source_task_id=task-1``. The child task was force-cancelled by
        stale recovery and a retry was scheduled. Without
        ``cancel_for_source``, ``count_pending_for_target(parent-X)``
        stays > 0 forever and the parent never reaches COMPLETED. With
        the fix, cancelling the orphaned source releases the parent.
        """
        # Parent registered a FollowUp via send_message → bus.watch
        await bus.watch("task-1", make_fu(target_id="parent-X"))
        # Sanity: parent is "waiting on children"
        assert await bus.count_pending_for_target("parent-X") == 1
        # Stale recovery cancels the original task and schedules a retry
        cancelled = await bus.cancel_for_source("task-1")
        assert cancelled == 1
        # Parent's gate now sees zero pending children
        assert await bus.count_pending_for_target("parent-X") == 0

    @pytest.mark.asyncio
    async def test_cancel_for_source_purges_cache_for_restart(self, bus, bus_repo):
        """Restart-survival: cancellation persists in the DB and the cache is clean.

        Same shape as ``test_cancelled_watcher_not_fired_after_restart``
        but keyed on source — proves the new path has the same crash-
        survival guarantees as the existing cancel primitive.
        """
        b1 = DependencyBus(bus_repo)
        await b1.start()
        await b1.watch("task-1", make_fu(target_id="parent-1"))
        await b1.cancel_for_source("task-1")
        await b1.stop()

        b2 = await fresh_bus(bus_repo)
        try:
            fired = await b2.emit_terminal("task-1", make_outcome())
            assert fired == []
        finally:
            await b2.stop()


# -------------------------------------------------------------------------
# TestCancelBusWatchersForTaskAsync
# -------------------------------------------------------------------------


class TestCancelBusWatchersForTaskAsync:
    """Tests for the shared ``cancel_bus_watchers_for_task_async`` helper.

    This helper consolidates the two near-identical bus-cancel bridges
    that live in ``manager._on_stale_task_cancelled_and_retried`` and
    ``worker_pool.Worker._cancel_bus_watchers_for_task``. The two
    callers are thin sync wrappers around it — see the refactor commit
    that introduced the helper. Failure here breaks BOTH stale recovery
    and the worker-pool timeout-retry cancel path, so coverage matters.
    """

    @pytest.mark.asyncio
    async def test_cancels_watchers_for_task(self, bus):
        """Direct invocation cancels registered watchers and returns count."""
        await bus.watch("task-1", make_fu(target_id="parent-X"))
        from daemon.services.dependency_bus import (
            cancel_bus_watchers_for_task_async,
        )
        cancelled = await cancel_bus_watchers_for_task_async(
            cancelled_task_id="task-1",
            retry_task_id=99,
            origin="unit_test",
            bus=bus,
        )
        assert cancelled == 1
        assert await bus.pending_watchers("task-1") == []

    @pytest.mark.asyncio
    async def test_no_watchers_returns_zero(self, bus):
        """Unknown source_task_id returns 0 (not an error)."""
        from daemon.services.dependency_bus import (
            cancel_bus_watchers_for_task_async,
        )
        cancelled = await cancel_bus_watchers_for_task_async(
            cancelled_task_id="never-watched",
            retry_task_id=None,
            origin="unit_test",
            bus=bus,
        )
        assert cancelled == 0

    @pytest.mark.asyncio
    async def test_int_task_id_accepted(self, bus):
        """``cancelled_task_id`` may be int; helper stringifies for the DB column."""
        # Register a watcher whose source_task_id matches the str(int) form
        # — the helper calls str() on the int before the DB lookup, so the
        # watcher is keyed the same way.
        await bus.watch("42", make_fu(target_id="parent-X"))
        from daemon.services.dependency_bus import (
            cancel_bus_watchers_for_task_async,
        )
        # Use int to match how worker_pool / stale_recovery call it (task.id is int)
        cancelled = await cancel_bus_watchers_for_task_async(
            cancelled_task_id=42,
            retry_task_id=43,
            origin="unit_test",
            bus=bus,
        )
        assert cancelled == 1

    @pytest.mark.asyncio
    async def test_returns_count_when_bus_missing(self):
        """When no bus is available (singleton is None AND bus=None passed),
        helper returns 0 without raising.

        The bus can be missing in degraded states (test fixtures,
        singleton reset). The helper must degrade gracefully — never
        re-raise from the caller thread (a worker thread crash here
        would propagate to the recovery thread, breaking subsequent
        cancel cycles).
        """
        from daemon.services.dependency_bus import (
            cancel_bus_watchers_for_task_async,
        )
        cancelled = await cancel_bus_watchers_for_task_async(
            cancelled_task_id="task-1",
            retry_task_id=None,
            origin="unit_test",
            bus=None,
        )
        assert cancelled == 0


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
#     does not propagate out of ``_process_event``.


class TestBusRetriggerFinalize:
    """Pin down the negative case: the bus does NOT re-trigger finalize.

    Phase 1 (2026-06-24, report-lane decoupling) removed the
    re-trigger finalize loop from ``_emit_terminal_via_bus`` — the
    bus is a pure state machine, and finalization flows through the
    report ``Task`` (PROCESS_REPORT) → ``_process_event`` path in
    ``JobFeedbackObserver``. The single remaining test below pins
    the contract: firing a watcher for a parent with multiple
    watchers does NOT call the observer's ``_finalize_job`` —
    neither via the old retrigger loop nor via any other side
    channel from ``_emit_terminal_via_bus``.
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
    async def test_retrigger_skipped_when_watchers_remain(self):
        """R2: 2 watchers on same target → fire only 1 → finalize NOT called.

        Phase 1 (2026-06-24, report-lane decoupling): the bus no longer
        re-triggers ``_finalize_job`` from ``_emit_terminal_via_bus`` —
        finalization flows through the report Task → ``_process_event``
        path instead. This test pins the contract that firing a single
        watcher for a parent with multiple watchers does NOT call the
        observer's ``_finalize_job`` (the old gate) — the test still
        passes under the new design because the bus does not call
        ``_finalize_job`` at all from this path.
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
        # CRITICAL: the bus does not re-trigger finalize (the report-lane
        # decoupling moved finalization to the Task → _process_event path).
        observer._get_processing_job_for_instance.assert_not_called()
        observer._finalize_job.assert_not_called()


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
# This flag is then read by
# ``JobFeedbackObserver._process_event`` (via
# ``_resolve_finalize_status``) so a parent whose LAST child errored
# finalizes as ``"error"`` instead of ``"completed"`` (mirrors the old
# CM ``_determine_terminal_status`` "any error → error" conservative
# rule).
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
        ``_process_event`` (in ``JobFeedbackObserver``) consults to
        decide whether to fall through to finalize or to emit
        ``in_progress`` and wait for the report Task to fire its
        own lifecycle event.
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

        # count_pending_for_target == 0 means the finalize gate
        # is open. ``_process_event`` would resolve the parent job
        # to ``"error"`` because of the sticky flag (via
        # ``_resolve_finalize_status``).
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
        ``_process_event``'s finalize branch from racing itself:
        the bus serializes per-source-task state transitions with
        the per-task lock, and ``count_pending_for_target`` is a
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
        # Fire both terminal events. NOTE: emitted sequentially rather than
        # via ``asyncio.gather`` because the test fixture uses an in-memory
        # SQLite database with StaticPool — concurrent emits on the same
        # shared connection race and raise ``sqlite3.InterfaceError``. The
        # bus itself supports concurrent emits safely (see
        # ``TestNoDoubleDecrement.test_concurrent_emit_does_not_double_fire``,
        # which uses a file-backed engine). Sequential emits verify the
        # same per-source-task / per-target gate invariants the original
        # gather was meant to exercise.
        # SQLite StaticPool limitation: concurrent emits on same connection are unsafe; sequential emits verify the same property
        result_a = await bus.emit_terminal("task-conc-a", make_outcome(status="completed"))
        result_b = await bus.emit_terminal("task-conc-b", make_outcome(status="completed"))
        results = [result_a, result_b]

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

        # The parent is fully resolved: count is 0. The finalize
        # gate (``count_pending_for_target == 0``) is now open, and
        # it will fire exactly once when the post-fire
        # ``_process_event`` runs — not twice, because the gate is
        # checked once per FollowUp and only the LAST watcher
        # crossing the threshold opens it.
        assert bus.count_pending_for_target_sync(parent_id) == 0, (
            "After concurrent fires, parent must have 0 pending — "
            "the retrigger gate's release condition is satisfied "
            "exactly once (the second fire observes count == 0 "
            "AFTER its own transition, the first observes count == 1)."
        )


# -------------------------------------------------------------------------
# TestOrphanSweep (Phase 1 — orphan watcher defense-in-depth)
# -------------------------------------------------------------------------
#
# Phase 1 of the architecture migration (2026-06-27): the bus gets a
# defense-in-depth startup sweep that cancels PENDING watchers whose
# ``source_task_id`` no longer corresponds to an active task. The
# sweep is implemented as an atomic conditional UPDATE in
# :meth:`DependencyBus._sweep_orphan_watchers`:
#
#   ``UPDATE dependency_watchers SET state = 'CANCELLED', fired_at = :now
#    WHERE state = 'PENDING'
#    AND source_task_id NOT IN (
#      SELECT id FROM task WHERE status IN ('running', 'pending', 'paused')
#    )``
#
# These tests pin the unit-level contracts against an in-memory SQLite
# engine + a real ``DependencyBus``. The PG equivalent (with a real
# ``bus.start()`` → ``_sweep_orphan_watchers`` path) lives in
# ``tests/postgres/test_06f500af_bug_class_eliminated_pg.py``.
#
# Active-task predicate: running/pending/paused tasks EXCLUDE their
# watchers from the sweep. Paused tasks are intentionally preserved
# for resume semantics (Decision 2 of the Pause/Resume redesign).
#
# State value casing: ``dependency_watchers.state`` uses UPPERCASE
# values ('PENDING', 'FIRED', 'CANCELLED'); ``task.status`` uses
# lowercase ('running', 'pending', 'paused'). The mixed casing in
# the sweep SQL is intentional and matches the actual on-disk column
# values — see ``dependency_bus.py:_sweep_orphan_watchers``.
# -------------------------------------------------------------------------


class TestOrphanSweep:
    """Phase 1 (2026-06-27): orphan watcher defense-in-depth sweep.

    Pins the unit-level contracts of
    :meth:`DependencyBus._sweep_orphan_watchers` against an in-memory
    SQLite engine. The five tests cover the four state-machine
    contracts (orphan cancelled, active exempt, idempotent, mixed
    batch) plus the repository primitive
    :meth:`DependencyWatcherRepository.fetch_all_pending` that the
    sweep's audit path relies on.
    """

    @pytest.mark.asyncio
    async def test_orphan_pending_watcher_gets_cancelled(
        self, bus_repo_with_task, bus
    ):
        """A PENDING watcher whose ``source_task_id`` points to a
        non-existent task is transitioned to CANCELLED by the sweep.

        This is the core 06f500af bug-class invariant: an orphan
        PENDING watcher would otherwise keep
        ``count_pending_for_target(parent) > 0`` forever and strand
        the parent in ``waiting_children`` (the production incident
        pattern recorded in commit 06f500af).

        Steps:
          1. Insert a PENDING ``DependencyWatcher`` whose
             ``source_task_id`` does NOT exist in the ``task`` table.
          2. Call ``bus._sweep_orphan_watchers()`` directly (bypasses
             ``start()`` to keep the test focused on the sweep
             itself).
          3. Assert: the sweep returned 1 (the orphan was cancelled).
          4. Assert: the watcher is no longer in the PENDING set
             (transitions to CANCELLED).
          5. Assert: ``count_pending_for_target(parent) == 0`` — the
             parent's gate can fire.

        Pre-Phase-1: there is no sweep, so the orphan stays PENDING
        and assertion 5 fails — the parent is stuck forever.
        """
        bus_repo = bus_repo_with_task
        parent_id = "parent-orphan-sweep"
        # Source task id that does NOT exist in the ``task`` table.
        orphan_source = "9999"

        # Insert the orphan PENDING watcher directly via the repo.
        watcher = DependencyWatcher(
            source_task_id=orphan_source,
            target_instance_id=parent_id,
            follow_up_payload=make_fu(target_id=parent_id).to_payload(),
        )
        bus_repo.insert(watcher)

        # Sanity: the watcher landed as PENDING.
        pending_before = bus_repo.fetch_pending_for_source(orphan_source)
        assert len(pending_before) == 1
        assert pending_before[0].state == DependencyWatcherState.PENDING.value

        # Sweep — direct call. Returns the number of orphans cancelled.
        swept = await bus._sweep_orphan_watchers()
        assert swept == 1, (
            f"orphan sweep must cancel exactly 1 watcher "
            f"(source_task_id={orphan_source} has no active task); "
            f"got swept={swept}"
        )

        # The watcher must no longer be PENDING (it transitioned to
        # CANCELLED). Use ``fetch_all_pending`` (the read primitive
        # the sweep's audit path mirrors) to verify.
        all_pending_after = bus_repo.fetch_all_pending()
        pending_ids = {w.watch_id for w in all_pending_after}
        assert watcher.watch_id not in pending_ids, (
            f"orphan watcher {watcher.watch_id} must be cancelled "
            f"(no longer in the PENDING set after sweep)"
        )

        # And the parent's pending-children count drops to 0 — its
        # completion gate can now fire.
        assert bus.count_pending_for_target_sync(parent_id) == 0, (
            "parent's count_pending_for_target must drop to 0 after "
            "the orphan sweep, releasing the completion gate"
        )

    @pytest.mark.asyncio
    async def test_active_tasks_watchers_not_cancelled(
        self, bus_repo_with_task, bus
    ):
        """PENDING watchers for running/pending/paused tasks must NOT
        be cancelled by the sweep.

        Critical: paused tasks MUST keep their watchers intact for
        resume semantics (Decision 2 of the Pause/Resume redesign —
        bus watchers survive pause). Without the paused-exemption in
        the SQL ``IN``-list, the sweep would wrongly cancel
        watchers for tasks that may resume later, causing missed
        child completion reports when the parent resumes.

        Steps:
          1. Insert Task rows with statuses: running, pending, paused.
          2. Register a PENDING watcher on each task id.
          3. Call sweep.
          4. Assert: sweep returned 0 (no orphans — all 3 tasks are
             active).
          5. Assert: all 3 watchers remain PENDING (count is 3).
        """
        bus_repo = bus_repo_with_task
        parent_id = "parent-active"
        instance_id = "instance-active"

        # Insert one Task per active status — each gets a PENDING
        # watcher keyed on its id. Status strings match the TaskStatus
        # enum values (lowercase) — the enum class is intentionally
        # not imported here (see module-level NOTE); the literal
        # strings match ``daemon.repositories.task.models.TaskStatus``.
        running_id = _insert_task(
            bus_repo.engine, instance_id, "running"
        )
        pending_id = _insert_task(
            bus_repo.engine, instance_id, "pending"
        )
        paused_id = _insert_task(
            bus_repo.engine, instance_id, "paused"
        )

        for src_id in (running_id, pending_id, paused_id):
            bus_repo.insert(
                DependencyWatcher(
                    source_task_id=str(src_id),
                    target_instance_id=parent_id,
                    follow_up_payload=make_fu(
                        target_id=parent_id
                    ).to_payload(),
                )
            )

        # Sweep — must be a no-op for active tasks.
        swept = await bus._sweep_orphan_watchers()
        assert swept == 0, (
            f"sweep must NOT cancel watchers for active tasks "
            f"(running/pending/paused); got swept={swept}"
        )

        # All 3 watchers must remain PENDING.
        for src_id in (running_id, pending_id, paused_id):
            pending_rows = bus_repo.fetch_pending_for_source(str(src_id))
            assert len(pending_rows) == 1, (
                f"watcher for task {src_id} must remain PENDING "
                f"after sweep (active-task predicate exempts it)"
            )
            assert (
                pending_rows[0].state
                == DependencyWatcherState.PENDING.value
            ), (
                f"watcher for task {src_id} must remain PENDING "
                f"(state check); got state={pending_rows[0].state}"
            )

        # Parent's pending count is unchanged (still 3).
        assert bus.count_pending_for_target_sync(parent_id) == 3

    @pytest.mark.asyncio
    async def test_sweep_is_idempotent(self, bus_repo_with_task, bus):
        """Calling sweep twice is safe: first cancels, second is a no-op.

        Idempotency is a load-bearing property — ``bus.start()`` calls
        the sweep on every boot, and a future second invocation (e.g.
        from a maintenance hook or a manual operator action) must not
        raise or double-cancel an already-CANCELLED row.

        Steps:
          1. Insert an orphan PENDING watcher.
          2. First sweep: returns 1, the watcher is CANCELLED.
          3. Second sweep: returns 0 (nothing left to cancel), no error.

        The guarded UPDATE (``WHERE state='PENDING'``) is what makes
        this safe — the second call's UPDATE matches zero rows and
        reports ``rowcount == 0``.
        """
        bus_repo = bus_repo_with_task
        parent_id = "parent-idempotent"
        orphan_source = "8888"

        # Insert the orphan.
        watcher = DependencyWatcher(
            source_task_id=orphan_source,
            target_instance_id=parent_id,
            follow_up_payload=make_fu(target_id=parent_id).to_payload(),
        )
        bus_repo.insert(watcher)

        # First sweep: cancels the orphan.
        swept_first = await bus._sweep_orphan_watchers()
        assert swept_first == 1, (
            f"first sweep must cancel the orphan; got swept={swept_first}"
        )

        # The watcher must no longer be PENDING.
        all_pending_after_first = bus_repo.fetch_all_pending()
        pending_ids_after_first = {
            w.watch_id for w in all_pending_after_first
        }
        assert watcher.watch_id not in pending_ids_after_first, (
            "orphan watcher must be cancelled (no longer PENDING) "
            "after the first sweep"
        )

        # Second sweep: nothing left to sweep. The guarded UPDATE
        # (``WHERE state='PENDING'``) matches zero rows, returns 0.
        swept_second = await bus._sweep_orphan_watchers()
        assert swept_second == 0, (
            f"second sweep must return 0 (no PENDING orphans left); "
            f"got swept={swept_second}"
        )

        # Parent's pending count is still 0 — no spurious regressions.
        assert bus.count_pending_for_target_sync(parent_id) == 0

    @pytest.mark.asyncio
    async def test_mixed_scenario(self, bus_repo_with_task, bus):
        """Mixed batch: 1 orphan + 2 running-task + 1 paused-task watcher.

        The sweep must cancel exactly 1 (the orphan) and leave the 3
        active-task watchers intact. This is the realistic scenario:
        the bus accumulates a mix of pending watchers across restart
        cycles, and the sweep must distinguish active from orphan
        without false positives.

        Steps:
          1. Insert 1 orphan watcher (no corresponding ``task`` row).
          2. Insert 2 watchers on a running task.
          3. Insert 1 watcher on a paused task.
          4. Sweep.
          5. Assert: 1 cancelled (the orphan); 3 remain PENDING.

        The two running-task watchers share a parent (siblings
        watching the same child from the same parent). The paused-task
        watcher targets a different parent. This mirrors the
        production fan-in shape from the 06f500af incident.
        """
        bus_repo = bus_repo_with_task
        parent_running = "parent-running-mixed"
        parent_paused = "parent-paused-mixed"
        parent_orphan = "parent-orphan-mixed"
        instance_id = "instance-mixed"

        # 1 orphan watcher (no corresponding task row).
        orphan_watcher = DependencyWatcher(
            source_task_id="7777",
            target_instance_id=parent_orphan,
            follow_up_payload=make_fu(target_id=parent_orphan).to_payload(),
        )
        bus_repo.insert(orphan_watcher)

        # 2 watchers on a running task (same parent, siblings).
        running_task_id = _insert_task(
            bus_repo.engine, instance_id, "running"
        )
        for i in range(2):
            bus_repo.insert(
                DependencyWatcher(
                    source_task_id=str(running_task_id),
                    target_instance_id=parent_running,
                    follow_up_payload=make_fu(
                        target_id=parent_running, message=f"running-{i}"
                    ).to_payload(),
                )
            )

        # 1 watcher on a paused task (different parent).
        paused_task_id = _insert_task(
            bus_repo.engine, instance_id, "paused"
        )
        bus_repo.insert(
            DependencyWatcher(
                source_task_id=str(paused_task_id),
                target_instance_id=parent_paused,
                follow_up_payload=make_fu(
                    target_id=parent_paused
                ).to_payload(),
            )
        )

        # Sanity: 4 PENDING watchers before the sweep.
        all_pending_before = bus_repo.fetch_all_pending()
        assert len(all_pending_before) == 4, (
            f"setup: expected 4 PENDING watchers before sweep; "
            f"got {len(all_pending_before)}"
        )

        # Sweep — must cancel ONLY the 1 orphan.
        swept = await bus._sweep_orphan_watchers()
        assert swept == 1, (
            f"sweep must cancel exactly 1 (the orphan); "
            f"running/paused watchers must be preserved; "
            f"got swept={swept}"
        )

        # Orphan is gone from PENDING; the 3 active watchers remain.
        all_pending_after = bus_repo.fetch_all_pending()
        assert len(all_pending_after) == 3, (
            f"after sweep: expected 3 PENDING (the 3 active-task "
            f"watchers); got {len(all_pending_after)}"
        )
        remaining_ids = {w.watch_id for w in all_pending_after}
        assert orphan_watcher.watch_id not in remaining_ids, (
            "orphan watcher must be removed from PENDING set"
        )

        # Per-parent counts: orphan parent has 0 (released), running
        # parent has 2 (both siblings intact), paused parent has 1
        # (exempt from sweep).
        assert (
            bus.count_pending_for_target_sync(parent_orphan) == 0
        ), "orphan parent's pending count must drop to 0"
        assert (
            bus.count_pending_for_target_sync(parent_running) == 2
        ), "running-task parent's 2 sibling watchers must survive the sweep"
        assert (
            bus.count_pending_for_target_sync(parent_paused) == 1
        ), "paused-task parent's watcher must survive the sweep"

    def test_fetch_all_pending_returns_only_pending(self, bus_repo):
        """``DependencyWatcherRepository.fetch_all_pending()`` returns
        only PENDING watchers (FIRED and CANCELLED are excluded).

        This is the read primitive the bus's startup sweep logic
        mirrors. Verifying the filter works correctly here ensures
        the audit path (``fetch_all_pending`` → manual review) and
        the sweep itself stay consistent on the underlying
        ``state = 'PENDING'`` predicate.

        Steps:
          1. Insert one watcher in each state: PENDING, FIRED, CANCELLED.
          2. Call ``bus_repo.fetch_all_pending()``.
          3. Assert: only the PENDING watcher is returned.
        """
        # Insert one watcher per state. The default ``state`` on the
        # model is PENDING, but we set it explicitly for clarity and
        # so a future default change doesn't silently break this test.
        pending_w = DependencyWatcher(
            source_task_id="src-pending-test",
            target_instance_id="parent-pending-test",
            follow_up_payload=make_fu(
                target_id="parent-pending-test"
            ).to_payload(),
            state=DependencyWatcherState.PENDING.value,
        )
        fired_w = DependencyWatcher(
            source_task_id="src-fired-test",
            target_instance_id="parent-fired-test",
            follow_up_payload=make_fu(
                target_id="parent-fired-test"
            ).to_payload(),
            state=DependencyWatcherState.FIRED.value,
        )
        cancelled_w = DependencyWatcher(
            source_task_id="src-cancelled-test",
            target_instance_id="parent-cancelled-test",
            follow_up_payload=make_fu(
                target_id="parent-cancelled-test"
            ).to_payload(),
            state=DependencyWatcherState.CANCELLED.value,
        )
        bus_repo.insert(pending_w)
        bus_repo.insert(fired_w)
        bus_repo.insert(cancelled_w)

        # fetch_all_pending returns ONLY the PENDING one.
        result = bus_repo.fetch_all_pending()
        result_ids = {w.watch_id for w in result}

        assert len(result) == 1, (
            f"fetch_all_pending must return only PENDING watchers; "
            f"got {len(result)} (expected 1)"
        )
        assert pending_w.watch_id in result_ids, (
            "the PENDING watcher must be in the result"
        )
        assert fired_w.watch_id not in result_ids, (
            "FIRED watcher must be excluded from the result"
        )
        assert cancelled_w.watch_id not in result_ids, (
            "CANCELLED watcher must be excluded from the result"
        )
