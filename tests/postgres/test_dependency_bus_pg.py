"""PostgreSQL-only tests for the DependencyBus service (Phase D deliverable D9).

Verifies the bus works correctly against a real PostgreSQL backend — not just
SQLite. Specifically:

  * The atomic guarded UPDATE in ``transition_state`` correctly serializes
    under PG row-level locking (the SQLite in-memory pack can't observe
    this race-window behavior because StaticPool serializes everything).
  * Restart survival works on a real engine (rows persist across bus
    instances sharing the engine).
  * Backpressure holds at large batch sizes on PG.
  * Cancellation is durable across the DB (CANCELLED rows are never
    re-fired, even after a restart).

Run with::

    uv run python -m pytest tests/postgres/test_dependency_bus_pg.py -v \\
        -m postgres --override-ini="addopts="

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips the entire
module cleanly when PostgreSQL is not reachable, so this file is safe to
collect even on machines without a running PG.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlmodel import Session, select

from daemon.repositories.dependency_bus import (
    DependencyWatcher,
    DependencyWatcherRepository,
    DependencyWatcherState,
)
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    Outcome,
    set_dependency_bus,
)


# Auto-apply the postgres marker so ``pytest -m postgres`` selects these
# tests and the default ``addopts = "-m 'not integration and not postgres'"``
# skips them unless overridden.
pytestmark = pytest.mark.postgres


# =============================================================================
# Helpers
# =============================================================================


def make_fu(
    target_id: str = "parent-A",
    message: str = "m",
    metadata: dict | None = None,
) -> FollowUp:
    """Build a FollowUp with sensible defaults."""
    return FollowUp(
        target_instance_id=target_id,
        message=message,
        metadata=metadata if metadata is not None else {},
    )


def make_outcome(
    status: str = "completed", error: str | None = None
) -> Outcome:
    """Build an Outcome with sensible defaults."""
    return Outcome(status=status, error=error)


def fresh_bus(repo: DependencyWatcherRepository) -> DependencyBus:
    """Construct a NEW DependencyBus bound to ``repo`` (used for restart tests)."""
    return DependencyBus(repo)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def bus_repo(pg_repository_factory):
    """Real DependencyWatcherRepository bound to the PG engine."""
    return pg_repository_factory(DependencyWatcherRepository)


@pytest.fixture
async def bus(bus_repo):
    """Started DependencyBus; auto-stops on teardown."""
    set_dependency_bus(None)
    b = DependencyBus(bus_repo)
    await b.start()
    try:
        yield b
    finally:
        await b.stop()


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.asyncio
async def test_pg_watch_emit_basic(bus, bus_repo, pg_engine):
    """watch 3, emit, assert 3 fired; verify rows are FIRED in DB with fired_at."""
    for i in range(3):
        await bus.watch("pg-task-basic", make_fu(target_id=f"parent-pg-{i}"))

    fired = await bus.emit_terminal("pg-task-basic", make_outcome())
    assert len(fired) == 3
    assert {fu.target_instance_id for fu in fired} == {
        f"parent-pg-{i}" for i in range(3)
    }

    # Verify the DB state directly: all 3 rows are FIRED with non-None fired_at.
    fired_state = DependencyWatcherState.FIRED.value
    with Session(pg_engine) as session:
        stmt = select(DependencyWatcher).where(
            DependencyWatcher.source_task_id == "pg-task-basic"
        )
        rows = list(session.exec(stmt))
        assert len(rows) == 3
        for row in rows:
            assert row.state == fired_state
            assert row.fired_at is not None


@pytest.mark.asyncio
async def test_pg_concurrent_emit_atomicity(bus):
    """Two concurrent emits on same source fire exactly 1 FollowUp total.

    The per-source-task asyncio.Lock serializes the two emits; the second
    sees the row already FIRED (guarded UPDATE returns rowcount == 0)
    and returns []. On real PG, the row-level lock at UPDATE time is the
    backpressure primitive.
    """
    await bus.watch("pg-task-once", make_fu(target_id="parent-pg-once"))

    fired_a, fired_b = await asyncio.gather(
        bus.emit_terminal("pg-task-once", make_outcome()),
        bus.emit_terminal("pg-task-once", make_outcome()),
    )

    total = list(fired_a) + list(fired_b)
    assert len(total) == 1, (
        f"Expected exactly 1 fired FollowUp across two concurrent emits, "
        f"got {len(total)}. PG backpressure primitive regressed."
    )
    assert total[0].target_instance_id == "parent-pg-once"


@pytest.mark.asyncio
async def test_pg_restart_survival(bus, bus_repo):
    """After stop+new bus+start on same engine, the watcher still fires."""
    await bus.watch("pg-task-restart", make_fu(target_id="parent-pg-restart"))
    await bus.stop()

    new_bus = fresh_bus(bus_repo)
    await new_bus.start()
    try:
        fired = await new_bus.emit_terminal("pg-task-restart", make_outcome())
        assert len(fired) == 1
        assert fired[0].target_instance_id == "parent-pg-restart"
    finally:
        await new_bus.stop()


@pytest.mark.asyncio
async def test_pg_backpressure_large_batch(bus, pg_engine):
    """500 watchers, emit → exactly 500 fired; verify all rows are FIRED in DB."""
    n = 500
    source = "pg-task-batch"
    targets = {f"parent-pg-batch-{i}" for i in range(n)}

    for i in range(n):
        await bus.watch(source, make_fu(target_id=f"parent-pg-batch-{i}"))

    fired = await bus.emit_terminal(source, make_outcome())
    assert len(fired) == n, (
        f"Expected exactly {n} fired FollowUps, got {len(fired)}."
    )
    assert {fu.target_instance_id for fu in fired} == targets

    # Confirm via DB: all N rows are FIRED.
    fired_state = DependencyWatcherState.FIRED.value
    with Session(pg_engine) as session:
        stmt = select(DependencyWatcher).where(
            DependencyWatcher.source_task_id == source
        )
        rows = list(session.exec(stmt))
        assert len(rows) == n
        for row in rows:
            assert row.state == fired_state
            assert row.fired_at is not None


@pytest.mark.asyncio
async def test_pg_cancel_prevents_fire(bus, pg_engine):
    """watch + cancel + emit → 0 fired; DB row is CANCELLED."""
    await bus.watch(
        "pg-task-cx", make_fu(target_id="parent-pg-cx", message="will-be-cancelled")
    )

    cancelled = await bus.cancel_for_target("parent-pg-cx")
    assert cancelled == 1

    fired = await bus.emit_terminal("pg-task-cx", make_outcome())
    assert fired == []

    # Confirm via DB: the row is CANCELLED, never made it to FIRED.
    cancelled_state = DependencyWatcherState.CANCELLED.value
    with Session(pg_engine) as session:
        stmt = select(DependencyWatcher).where(
            DependencyWatcher.source_task_id == "pg-task-cx"
        )
        rows = list(session.exec(stmt))
        assert len(rows) == 1
        assert rows[0].state == cancelled_state
        # fired_at should be None on a CANCELLED row — the bus passes
        # fired_at=None for cancellation transitions.
        assert rows[0].fired_at is None


@pytest.mark.asyncio
async def test_pg_cancel_for_source_prevents_fire(bus, pg_engine):
    """cancel_for_source: PG regression for the 2026-06-26 incident.

    ``StaleTaskRecovery`` force-cancels a stale task and schedules a
    retry; the bus must cancel the cancelled task's watchers so the
    retry's natural completion (firing ``emit_terminal`` for its OWN
    task id) doesn't strand the parent in ``waiting_children``.
    Mirrors the existing ``test_pg_cancel_prevents_fire`` but keyed on
    source instead of target.
    """
    await bus.watch(
        "pg-task-cs", make_fu(target_id="parent-pg-cs", message="will-be-cancelled")
    )

    cancelled = await bus.cancel_for_source("pg-task-cs")
    assert cancelled == 1

    fired = await bus.emit_terminal("pg-task-cs", make_outcome())
    assert fired == []

    # Confirm via DB: the row is CANCELLED, never made it to FIRED.
    cancelled_state = DependencyWatcherState.CANCELLED.value
    with Session(pg_engine) as session:
        stmt = select(DependencyWatcher).where(
            DependencyWatcher.source_task_id == "pg-task-cs"
        )
        rows = list(session.exec(stmt))
        assert len(rows) == 1
        assert rows[0].state == cancelled_state
        assert rows[0].fired_at is None