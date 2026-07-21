"""PostgreSQL tests for the Wanderer multi-completion-report bugfix.

This module tests three fixes in ``daemon/services/child_reports.py``:

  Fix 1 — Active-children guard in ``regular_child_completed`` branch.
      A non-root instance (e.g., Wanderer, parent_id=leader) that still
      has non-terminal children must NOT emit a ``completion_report`` to
      its parent on every graph turn. The guard queries
      ``instances.parent_id`` (NOT the bus counter, which under-counts
      because ``spawn_instance`` does not register watchers for every
      spawn) and returns the new outcome ``child_still_running_defer``.

  Fix 2 — Status-level idempotency at the top of
      ``_process_child_completion_db_sync``. Re-entry on an already
      terminal instance (COMPLETED or ERROR) must short-circuit with
      ``idempotency_skip`` — no duplicate status write, no duplicate
      report, no duplicate INSTANCE_COMPLETED event.

  Fix 3 — ``pending_for_parent`` off-by-1. The snapshot is taken inside
      the WriteGuardSession transaction BEFORE the post-commit bus
      terminal hook fires (which decrements the parent's pending count
      by exactly 1). The corrected count is ``max(0, count - 1)``.

Run with::

    pytest tests/postgres/test_wanderer_completion_reporting_pg.py \\
        -v -m postgres --override-ini="addopts="

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips the
entire module cleanly when PostgreSQL is unreachable, so this file is
safe to collect even on machines without a running PG.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlmodel import Session

# Import model classes so SQLModel.metadata sees them when
# ``create_all()`` runs on the PG engine (matches the pattern in
# tests/postgres/conftest.py).
from daemon.repositories.dependency_bus import (
    DependencyWatcher,
    DependencyWatcherRepository,
    DependencyWatcherState,
)
from daemon.repositories.event.models import Event, EventKind  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (  # noqa: F401
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.task.models import Task  # noqa: F401
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus
from daemon.write_pause_guard import WritePauseGuard


# Auto-apply the postgres marker so ``pytest -m postgres`` selects these
# tests and the default ``-m 'not integration and not postgres'`` addopts
# skips them unless overridden.
pytestmark = pytest.mark.postgres


# =============================================================================
# Service / row helpers (mirror the helpers in tests/unit/services/test_child_reports.py)
# =============================================================================


def _build_child_reports_service(engine: Engine) -> ChildReportsService:
    """Build a real ``ChildReportsService`` with a mock manager that
    exposes only the attributes ``_process_child_completion_db_sync``
    needs. Uses ``__new__`` to skip ``__init__`` and bind attributes
    manually — mirrors the helper in ``tests/unit/services/test_child_reports.py``.
    """

    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._checkpointer = None
    manager._live_hub = None  # SSE no-op (guarded on truthiness)
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()
    manager._task_repo = None  # disables task lookup in bus hook path
    manager._worker_pool = None  # disables notify_work()

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None  # lifecycle event publish is guarded
    return service


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    parent_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "wanderer",
) -> Instance:
    """Insert an Instance row with the given parent_id and status."""
    inst = Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=f"/tmp/{agent_id}",
        parent_id=parent_id,
        status=status,
        version=1,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        instance_metadata={},
    )
    with Session(engine) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


def _seed_dependency_watcher(
    engine: Engine,
    *,
    target_instance_id: str,
    source_task_id: str,
    state: str = DependencyWatcherState.PENDING.value,
) -> None:
    """Insert a DependencyWatcher row targeting the given parent."""
    watcher = DependencyWatcher(
        source_task_id=source_task_id,
        target_instance_id=target_instance_id,
        follow_up_payload={"kind": "follow_up"},
        watcher_metadata={"child_id": source_task_id},
        state=state,
    )
    with Session(engine) as session:
        session.add(watcher)
        session.commit()


def _read_instance_status(engine: Engine, instance_id: str) -> str | None:
    """Read current instance status from the DB."""
    with Session(engine) as session:
        inst = session.get(Instance, instance_id)
        return inst.status if inst else None


def _count_completion_reports(
    engine: Engine, *, parent_id: str, child_id: str
) -> int:
    """Count COMPLETION_REPORT messages enqueued for ``parent_id`` from
    ``child_id`` (any status — READY, PROCESSING, COMPLETED).
    """
    with Session(engine) as session:
        stmt = (
            select(func.count())
            .select_from(MessageQueue)
            .where(MessageQueue.instance_id == parent_id)
            .where(
                MessageQueue.source.like(f"internal_report:{child_id}:%")
            )
        )
        # ``session.scalar(stmt)`` returns the int directly (vs ``exec`` which
        # returns a Row). The SQLModel idiom for scalar count queries.
        return int(session.scalar(stmt) or 0)


def _count_events(
    engine: Engine, *, instance_id: str, kind: str
) -> int:
    """Count Event rows of the given kind for the given instance."""
    with Session(engine) as session:
        stmt = (
            select(func.count())
            .select_from(Event)
            .where(Event.instance_id == instance_id)
            .where(Event.kind == kind)
        )
        return int(session.scalar(stmt) or 0)


def _read_parent_event_pending(
    engine: Engine, *, parent_id: str, child_id: str
) -> int | None:
    """Read ``pending_for_parent`` from the most recent CHILD_COMPLETED
    event for ``parent_id`` referencing ``child_id``.
    """
    with Session(engine) as session:
        stmt = (
            select(Event)
            .where(Event.instance_id == parent_id)
            .where(Event.kind == EventKind.CHILD_COMPLETED.value)
            .order_by(Event.created_at.desc())
        )
        # ``session.exec(stmt)`` returns Row objects; ``session.scalars(stmt)``
        # returns the Event instances directly (this is the SQLModel idiom).
        for ev in session.scalars(stmt).all():
            try:
                data = json.loads(ev.data or "{}")
                if data.get("child_instance_id") == child_id:
                    return int(data.get("pending_for_parent", 0))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return None


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def bus_repo(pg_repository_factory):
    """Real DependencyWatcherRepository bound to the PG engine."""
    return pg_repository_factory(DependencyWatcherRepository)


@pytest.fixture(autouse=True)
def bus(bus_repo):
    """Real DependencyBus singleton; auto-clears on teardown.

    The bus must be wired because ``_process_child_completion_db_sync``
    raises the A8/A9 hard error when the bus singleton is None (bus is
    the sole completion authority post-Phase 5).

    We do NOT need to start the bus — only ``count_pending_for_target_sync``
    is used by the code under test (no ``watch()`` / ``emit_terminal()``
    calls needed). Starting the bus requires an event loop, which is
    fiddly in a sync pytest fixture; the static singleton is enough.
    """
    from daemon.services.dependency_bus import DependencyBus as _Bus

    b = _Bus(bus_repo)
    set_dependency_bus(b)
    try:
        yield b
    finally:
        set_dependency_bus(None)


# =============================================================================
# Fix 1 — Active-children guard in regular_child_completed branch
# =============================================================================


class TestFix1ActiveChildrenGuard:
    """Fix 1: non-root instance with non-terminal children must defer.

    The Wanderer bug: when a non-root parent (parent_id=leader) has its
    own children (coders) still running, the regular_child_completed
    branch was unconditionally emitting a completion_report to leader on
    every graph turn. The fix adds a guard: if the just-completed child
    still has non-terminal children itself, return the new outcome
    ``child_still_running_defer`` with no commit, no report, no events.
    """

    def test_non_root_instance_with_active_children_defers(
        self, pg_engine: Engine
    ):
        """A non-root instance with one RUNNING child must defer.

        Setup:
          - leader (root, no parent)
          - wanderer (parent_id=leader, RUNNING)
          - coder1 (parent_id=wanderer, RUNNING — still active)

        Expectation:
          - outcome == "child_still_running_defer"
          - wanderer's status is UNCHANGED (no COMPLETED write)
          - NO completion_report message was enqueued to leader
          - NO INSTANCE_COMPLETED event for wanderer
        """
        service = _build_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine,
            instance_id="wanderer",
            parent_id="leader",
            status=InstanceStatus.RUNNING.value,
            agent_id="wanderer",
        )
        _seed_instance(
            pg_engine,
            instance_id="coder1",
            parent_id="wanderer",
            status=InstanceStatus.RUNNING.value,
            agent_id="coder",
        )

        result = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-wanderer-1",
            last_content="wanderer final response",
        )

        assert result.outcome == "child_still_running_defer", (
            f"Expected 'child_still_running_defer' (non-root instance has "
            f"active children), got '{result.outcome}'"
        )
        assert result.instance_id == "wanderer"
        assert result.parent_id == "leader"

        # Status must be UNCHANGED — the defer path does not commit.
        assert _read_instance_status(pg_engine, "wanderer") == (
            InstanceStatus.RUNNING.value
        ), "Defer path must not write status"

        # No completion_report was enqueued to leader.
        assert _count_completion_reports(
            pg_engine, parent_id="leader", child_id="wanderer"
        ) == 0, "Defer path must NOT emit completion_report to parent"

        # No INSTANCE_COMPLETED event was emitted for wanderer.
        assert _count_events(
            pg_engine,
            instance_id="wanderer",
            kind=EventKind.INSTANCE_COMPLETED.value,
        ) == 0, "Defer path must NOT emit INSTANCE_COMPLETED event"

    def test_non_root_instance_with_multiple_active_children_defers(
        self, pg_engine: Engine
    ):
        """Even when one child has completed, an active sibling keeps
        the parent in defer.

        Setup:
          - wanderer (parent_id=leader)
          - coder1 (parent_id=wanderer, COMPLETED) — already finished
          - coder2 (parent_id=wanderer, RUNNING) — still active

        Expectation: outcome == "child_still_running_defer"
        (the COMPLETED child does NOT cancel the defer)
        """
        service = _build_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine, instance_id="wanderer", parent_id="leader",
        )
        _seed_instance(
            pg_engine, instance_id="coder1", parent_id="wanderer",
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_instance(
            pg_engine, instance_id="coder2", parent_id="wanderer",
            status=InstanceStatus.RUNNING.value,
        )

        result = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-wanderer-2",
            last_content="wanderer response 2",
        )

        assert result.outcome == "child_still_running_defer"

    def test_non_root_instance_with_all_children_done_emits_report(
        self, pg_engine: Engine
    ):
        """When ALL children are terminal, the guard passes and the
        report is emitted normally.

        Setup:
          - wanderer (parent_id=leader)
          - coder1 (parent_id=wanderer, COMPLETED)
          - coder2 (parent_id=wanderer, ERROR) — terminal, treated as
            done

        Expectation: outcome == "regular_child_completed" (no defer)
        """
        service = _build_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine, instance_id="wanderer", parent_id="leader",
        )
        _seed_instance(
            pg_engine, instance_id="coder1", parent_id="wanderer",
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_instance(
            pg_engine, instance_id="coder2", parent_id="wanderer",
            status=InstanceStatus.ERROR.value,
        )

        # Seed a bus watcher for the just-completed child so the bus
        # gate (line ~1549 in regular_child_completed cascade) passes.
        _seed_dependency_watcher(
            pg_engine,
            target_instance_id="wanderer",
            source_task_id="task-wanderer-3",
        )

        result = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-wanderer-3",
            last_content="wanderer final response",
        )

        # All children are terminal → guard passes → regular_child_completed.
        assert result.outcome == "regular_child_completed", (
            f"Expected 'regular_child_completed' when all children are "
            f"terminal, got '{result.outcome}'"
        )

        # Status transitioned to COMPLETED.
        assert _read_instance_status(pg_engine, "wanderer") == (
            InstanceStatus.COMPLETED.value
        )

        # A completion_report was enqueued to leader.
        assert _count_completion_reports(
            pg_engine, parent_id="leader", child_id="wanderer"
        ) == 1, "regular_child_completed must emit one completion_report"

    def test_self_excluded_from_active_children_count(
        self, pg_engine: Engine
    ):
        """The just-completed instance must be excluded from its own
        active-children count.

        Setup:
          - wanderer (parent_id=leader, RUNNING) — the just-completed
            instance
          - coder1 (parent_id=wanderer, RUNNING) — still active

        If the guard did NOT exclude self, the count would be 2 (self +
        coder1) instead of 1 (coder1 only). Either way the defer fires,
        but the count must reflect siblings, not self.

        We assert via the deferred outcome — if the guard mis-counted
        self, the deferred outcome would still fire but with an
        over-counted log message (harder to assert). The correctness of
        the self-exclusion is what makes the "all children done" case
        pass (see ``test_non_root_instance_with_all_children_done_emits_report``)
        — re-running this on a wanderer-only tree would defer if self
        were counted.
        """
        service = _build_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        # Parent instance with NO children.
        _seed_instance(
            pg_engine, instance_id="solo-wanderer", parent_id="leader",
        )

        _seed_dependency_watcher(
            pg_engine,
            target_instance_id="solo-wanderer",
            source_task_id="task-solo-1",
        )

        result = service._process_child_completion_db_sync(
            instance_id="solo-wanderer",
            completed_message_id="msg-solo-1",
            last_content="solo response",
        )

        # No children at all → guard passes → regular_child_completed.
        assert result.outcome == "regular_child_completed", (
            "Self-exclusion: an instance with no children must NOT be "
            "counted as its own active child"
        )


# =============================================================================
# Fix 1 — Q1 review: TERMINATED / FAILED siblings must NOT block parent
# =============================================================================


class TestFix1TerminalStatusesCoverAllFour:
    """Q1 review: the active-children guard's ``status NOT IN (...)``
    list must include ALL FOUR canonical terminal statuses
    (``TERMINAL_STATUSES = {COMPLETED, ERROR, TERMINATED, FAILED}``).

    Pre-fix: only ``COMPLETED, ERROR`` were excluded. A single TERMINATED
    or FAILED child would be counted as "active" and the parent's
    completion_report would be deferred forever (a hard wedge).

    Post-fix: all four terminal statuses are excluded. The guard passes
    when the parent's only remaining children are in any terminal state.
    """

    def test_terminated_sibling_does_not_block_parent_completion(
        self, pg_engine: Engine
    ):
        """A TERMINATED sibling is terminal — parent must NOT defer.

        Mirrors ``test_non_root_instance_with_all_children_done_emits_report``
        but with a TERMINATED sibling instead of a COMPLETED one. The
        completion_report must be emitted normally.
        """
        service = _build_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine, instance_id="wanderer", parent_id="leader",
        )
        # Only child is TERMINATED — terminal, but pre-fix this would
        # still count as "active" and wedge the parent.
        _seed_instance(
            pg_engine,
            instance_id="coder-terminated",
            parent_id="wanderer",
            status=InstanceStatus.TERMINATED.value,
        )
        # Seed a bus watcher so the bus gate passes in
        # regular_child_completed (mirrors the COMPLETED/ERROR sibling
        # test pattern).
        _seed_dependency_watcher(
            pg_engine,
            target_instance_id="wanderer",
            source_task_id="task-terminated-1",
        )

        result = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-terminated-1",
            last_content="wanderer final response",
        )

        # TERMINATED sibling is terminal → guard passes → regular_child_completed.
        assert result.outcome == "regular_child_completed", (
            f"TERMINATED sibling is terminal; expected 'regular_child_completed', "
            f"got '{result.outcome}' (pre-fix this would be 'child_still_running_defer')"
        )

        # A completion_report WAS emitted to leader.
        assert _count_completion_reports(
            pg_engine, parent_id="leader", child_id="wanderer"
        ) == 1, (
            "TERMINATED sibling must NOT block completion_report emission"
        )

        # wanderer's status transitioned to COMPLETED.
        assert _read_instance_status(pg_engine, "wanderer") == (
            InstanceStatus.COMPLETED.value
        )

    def test_failed_sibling_does_not_block_parent_completion(
        self, pg_engine: Engine
    ):
        """A FAILED sibling is terminal — parent must NOT defer.

        Symmetric to the TERMINATED case. FAILED is the fourth canonical
        terminal status (alongside COMPLETED, ERROR, TERMINATED) and
        must be excluded from the active-children count.
        """
        service = _build_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine, instance_id="wanderer", parent_id="leader",
        )
        _seed_instance(
            pg_engine,
            instance_id="coder-failed",
            parent_id="wanderer",
            status=InstanceStatus.FAILED.value,
        )
        _seed_dependency_watcher(
            pg_engine,
            target_instance_id="wanderer",
            source_task_id="task-failed-1",
        )

        result = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-failed-1",
            last_content="wanderer final response",
        )

        assert result.outcome == "regular_child_completed", (
            f"FAILED sibling is terminal; expected 'regular_child_completed', "
            f"got '{result.outcome}' (pre-fix this would be 'child_still_running_defer')"
        )

        assert _count_completion_reports(
            pg_engine, parent_id="leader", child_id="wanderer"
        ) == 1, (
            "FAILED sibling must NOT block completion_report emission"
        )

        assert _read_instance_status(pg_engine, "wanderer") == (
            InstanceStatus.COMPLETED.value
        )


# =============================================================================
# Fix 1 — Q5 regression test: per-graph-turn Wanderer pattern
# =============================================================================


class TestFix1PerGraphTurnWandererRegression:
    """Q5 regression test for the actual reported Wanderer bug.

    The reported bug: Wanderer emits a ``completion_report`` to leader on
    EVERY graph turn while its spawned coders are still running, because
    the ``regular_child_completed`` branch unconditionally writes a fresh
    report + PROCESS_REPORT task + emits INSTANCE_COMPLETED on every
    invocation of ``_process_child_completion_db_sync``.

    With Fix 1's active-children guard in place, every such invocation
    while a child is still RUNNING must return
    ``child_still_running_defer`` and write NOTHING — no status
    transition, no completion_report, no event.

    This is the canonical 3-turn Wanderer pattern:
      - Turn 1: wanderer's own user message arrives (wanderer has just
        spawned its coders, all RUNNING).
      - Turn 2: coder1's completion_report arrives at wanderer, wanderer
        re-runs the child-completion pipeline.
      - Turn 3: coder2's completion_report arrives at wanderer, wanderer
        re-runs again.

    Pre-fix: each turn emits a duplicate completion_report to leader.
    Post-fix: every turn defers, zero reports emitted.
    """

    def test_three_graph_turns_emit_zero_reports(
        self, pg_engine: Engine
    ):
        """Three sequential calls with DIFFERENT completed_message_ids,
        simulating three graph turns while two coders are RUNNING.

        Setup:
          - leader (root, no parent)
          - wanderer (parent_id=leader, RUNNING)
          - coder1 (parent_id=wanderer, RUNNING)
          - coder2 (parent_id=wanderer, RUNNING)

        Assertions:
          - All three calls return "child_still_running_defer"
          - Zero completion_reports are enqueued to leader
          - wanderer's status is UNCHANGED (still RUNNING)
        """
        service = _build_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine, instance_id="wanderer", parent_id="leader",
        )
        _seed_instance(
            pg_engine,
            instance_id="coder1",
            parent_id="wanderer",
            status=InstanceStatus.RUNNING.value,
            agent_id="coder",
        )
        _seed_instance(
            pg_engine,
            instance_id="coder2",
            parent_id="wanderer",
            status=InstanceStatus.RUNNING.value,
            agent_id="coder",
        )

        # Turn 1: wanderer processes its own message first (both coders
        # still running).
        r1 = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-wanderer-turn-1",
            last_content="wanderer turn 1",
        )
        assert r1.outcome == "child_still_running_defer", (
            f"Turn 1: both coders RUNNING → defer; got '{r1.outcome}'"
        )

        # Turn 2: a coder has reported back; wanderer re-runs the
        # pipeline. coder2 is still RUNNING → defer still fires.
        r2 = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-coder1-report",
            last_content="wanderer turn 2",
        )
        assert r2.outcome == "child_still_running_defer", (
            f"Turn 2: coder2 still RUNNING → defer; got '{r2.outcome}'"
        )

        # Turn 3: another coder reports; wanderer re-runs again. If the
        # bug were present, this would have already produced 2 duplicate
        # completion_reports by now (one per turn). With the fix, all
        # three defer while a single child remains.
        r3 = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-coder2-report",
            last_content="wanderer turn 3",
        )
        assert r3.outcome == "child_still_running_defer", (
            f"Turn 3: defer still fires while wanderer has RUNNING children; "
            f"got '{r3.outcome}'"
        )

        # Critical regression assertion: ZERO completion_reports were
        # emitted to leader across all three turns. This is the core
        # invariant of the Wanderer bug fix.
        assert _count_completion_reports(
            pg_engine, parent_id="leader", child_id="wanderer"
        ) == 0, (
            "Per-graph-turn pattern: 3 defer calls must NOT emit any "
            "completion_report to leader (regression for Wanderer bug)"
        )

        # wanderer's status must be UNCHANGED — defer path commits nothing.
        assert _read_instance_status(pg_engine, "wanderer") == (
            InstanceStatus.RUNNING.value
        ), "Defer path must not write wanderer's status"

        # No INSTANCE_COMPLETED event for wanderer.
        assert _count_events(
            pg_engine,
            instance_id="wanderer",
            kind=EventKind.INSTANCE_COMPLETED.value,
        ) == 0, "Defer path must NOT emit INSTANCE_COMPLETED event"


# =============================================================================
# Fix 2 — Status-level idempotency short-circuit
# =============================================================================


class TestFix2Idempotency:
    """Fix 2: re-entry on an already-terminal instance must short-circuit.

    Without this guard, re-running ``_process_child_completion_db_sync``
    on a COMPLETED or ERROR instance re-writes status, re-emits a
    completion_report, re-emits an INSTANCE_COMPLETED event, and
    re-triggers the bus terminal hook — duplicating every observable
    side effect on each re-entry.
    """

    def test_completed_instance_short_circuits(self, pg_engine: Engine):
        """A COMPLETED instance returns ``idempotency_skip`` and writes
        nothing.
        """
        service = _build_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine,
            instance_id="wanderer",
            parent_id="leader",
            status=InstanceStatus.COMPLETED.value,  # already terminal
        )

        result = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-already-done",
            last_content="re-entry attempt",
        )

        assert result.outcome == "idempotency_skip", (
            f"Expected 'idempotency_skip' for COMPLETED instance, "
            f"got '{result.outcome}'"
        )

        # Status must be unchanged (still COMPLETED, no extra version bump).
        with Session(pg_engine) as session:
            inst = session.get(Instance, "wanderer")
            assert inst.status == InstanceStatus.COMPLETED.value

        # No completion_report enqueued.
        assert _count_completion_reports(
            pg_engine, parent_id="leader", child_id="wanderer"
        ) == 0

        # No INSTANCE_COMPLETED event.
        assert _count_events(
            pg_engine,
            instance_id="wanderer",
            kind=EventKind.INSTANCE_COMPLETED.value,
        ) == 0

    def test_error_instance_short_circuits(self, pg_engine: Engine):
        """An ERROR instance also short-circuits — ERROR is terminal."""
        service = _build_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine,
            instance_id="wanderer",
            parent_id="leader",
            status=InstanceStatus.ERROR.value,
        )

        result = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-after-error",
            last_content="re-entry after error",
        )

        assert result.outcome == "idempotency_skip"

    def test_double_call_does_not_double_write(
        self, pg_engine: Engine
    ):
        """Calling the function TWICE on the same instance (first call
        completes it, second call must skip) results in exactly ONE
        completion_report and ONE INSTANCE_COMPLETED event.
        """
        service = _build_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine, instance_id="wanderer", parent_id="leader",
        )
        _seed_dependency_watcher(
            pg_engine,
            target_instance_id="wanderer",
            source_task_id="task-idem-1",
        )

        # First call: completes the instance.
        r1 = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-idem-1",
            last_content="first response",
        )
        assert r1.outcome == "regular_child_completed"

        # Second call: must short-circuit with idempotency_skip.
        r2 = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-idem-2",  # different message id
            last_content="re-entry attempt",
        )
        assert r2.outcome == "idempotency_skip", (
            f"Second call must idempotency_skip, got '{r2.outcome}'"
        )

        # Exactly ONE completion_report from wanderer to leader.
        assert _count_completion_reports(
            pg_engine, parent_id="leader", child_id="wanderer"
        ) == 1, "Double-call must NOT duplicate completion_report"

        # Exactly ONE INSTANCE_COMPLETED event for wanderer.
        assert _count_events(
            pg_engine,
            instance_id="wanderer",
            kind=EventKind.INSTANCE_COMPLETED.value,
        ) == 1, "Double-call must NOT duplicate INSTANCE_COMPLETED event"

    def test_running_instance_proceeds_normally(
        self, pg_engine: Engine
    ):
        """Control: a RUNNING instance is NOT short-circuited by Fix 2.
        The idempotency guard is for terminal states only.
        """
        service = _build_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine,
            instance_id="wanderer",
            parent_id="leader",
            status=InstanceStatus.RUNNING.value,
        )
        _seed_dependency_watcher(
            pg_engine,
            target_instance_id="wanderer",
            source_task_id="task-running-1",
        )

        result = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-running-1",
            last_content="response",
        )

        assert result.outcome == "regular_child_completed", (
            "RUNNING instance must NOT be short-circuited by Fix 2"
        )


# =============================================================================
# Fix 3 — pending_for_parent off-by-1 correction
# =============================================================================


class TestFix3PendingForParentOffBy1:
    """Fix 3: ``pending_for_parent`` in the CHILD_COMPLETED event must
    reflect the post-fire bus count, not the pre-commit snapshot.

    The snapshot is taken inside the WriteGuardSession transaction
    BEFORE the post-commit bus terminal hook fires (which decrements the
    parent's pending count by exactly 1). The corrected count is
    ``max(0, count - 1)``.
    """

    def test_single_child_emits_zero_pending(
        self, pg_engine: Engine
    ):
        """When the just-completed child's parent has only one
        PENDING correlation (the just-completed child), the event's
        ``pending_for_parent`` must be 0 (post-fire count = 1 - 1 = 0).

        Without the fix: count=1, snapshot=1, post-fire count=0.
        With the fix: max(0, 1 - 1) = 0.

        Note: ``pending_for_parent`` queries the parent's
        ``dependency_watchers`` count — i.e. watchers targeting
        ``instance.parent_id`` (leader). We seed 1 PENDING watcher for
        leader, simulating one pending correlation that will fire as
        part of this completion.
        """
        service = _build_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine, instance_id="wanderer", parent_id="leader",
        )
        # Seed 1 PENDING watcher targeting the parent (leader).
        _seed_dependency_watcher(
            pg_engine,
            target_instance_id="leader",
            source_task_id="task-only-1",
            state=DependencyWatcherState.PENDING.value,
        )

        result = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-only-1",
            last_content="final response",
        )
        assert result.outcome == "regular_child_completed"

        # The CHILD_COMPLETED event is published on `instance.parent_id`
        # (the parent of the just-completed child). For wanderer with
        # parent_id="leader", the event is on "leader" and references
        # child_instance_id="wanderer". See child_reports.py ~line 1748.
        pending = _read_parent_event_pending(
            pg_engine, parent_id="leader", child_id="wanderer"
        )
        assert pending == 0, (
            f"Single-child completion must record pending_for_parent=0 "
            f"(post-fire count = 1 - 1), got {pending}"
        )

    def test_multiple_pending_children_emits_count_minus_one(
        self, pg_engine: Engine
    ):
        """When the parent has N pending correlations and one fires,
        ``pending_for_parent`` must be N-1.
        """
        service = _build_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine, instance_id="wanderer", parent_id="leader",
        )
        # Seed 3 PENDING watchers for the parent (leader).
        for i in range(3):
            _seed_dependency_watcher(
                pg_engine,
                target_instance_id="leader",
                source_task_id=f"task-multi-{i}",
                state=DependencyWatcherState.PENDING.value,
            )

        result = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-multi",
            last_content="response",
        )
        assert result.outcome == "regular_child_completed"

        pending = _read_parent_event_pending(
            pg_engine, parent_id="leader", child_id="wanderer"
        )
        # count=3, post-fire=2 → max(0, 3 - 1) = 2.
        assert pending == 2, (
            f"3 pending - 1 just-fired = 2 post-fire; "
            f"got pending_for_parent={pending}"
        )

    def test_no_watchers_emits_zero_not_negative(
        self, pg_engine: Engine
    ):
        """When the parent has NO watchers at all, ``pending_for_parent``
        must be 0 (defensive clamp — never negative).
        """
        service = _build_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine, instance_id="wanderer", parent_id="leader",
        )
        # No watchers seeded.

        result = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-no-watchers",
            last_content="response",
        )
        assert result.outcome == "regular_child_completed"

        pending = _read_parent_event_pending(
            pg_engine, parent_id="leader", child_id="wanderer"
        )
        # max(0, 0 - 1) = 0 — defensive clamp.
        assert pending == 0, (
            f"No watchers: max(0, 0 - 1) = 0; got {pending}"
        )


# =============================================================================
# Corrective emit — multi-turn child regression (leader stuck in
# waiting_children after Wanderer emits its completion_report from a
# task id ≠ the task that registered the parent's watcher).
# =============================================================================


class TestCorrectiveEmitMultiTurnChild:
    """Regression test for the leader-stuck-in-``waiting_children``
    bug introduced by the Wanderer per-graph-turn fix (commit 8616ff45).

    The Wanderer fix added an ``active-children guard`` that returns
    the new outcome ``child_still_running_defer`` on the child's first
    graph turn (when its spawned coders are still running). The
    dispatch handler for that outcome never calls the bus's terminal
    emit, so the parent's watcher (keyed on the child's FIRST
    ``process_message`` task id) stays PENDING. When the child finally
    reaches its terminal graph turn on a LATER ``PROCESS_REPORT``
    task id, the task-keyed ``emit_terminal`` cannot match the watcher
    → the parent wedges in ``waiting_children`` forever.

    The fix adds a corrective emit hook
    (:meth:`ChildReportsService._emit_terminal_for_child_instance_via_bus`)
    that matches the watcher on the (parent, child) instance pair via
    ``follow_up_payload.metadata.child_id`` (stamped by ``send_message``
    when the watcher was registered) instead of the task id. Exactly-
    once is preserved by the bus's guarded
    ``transition_state`` UPDATE — the corrective emit is a no-op when
    the task-keyed emit already fired the watcher.
    """

    def _seed_watcher_production_shape(
        self,
        engine: Engine,
        *,
        target_instance_id: str,
        source_task_id: str,
        child_instance_id: str,
        state: str = DependencyWatcherState.PENDING.value,
    ) -> str:
        """Seed a DependencyWatcher row with the production FollowUp
        payload shape ``send_message`` writes (so the (parent, child)
        pair matcher in ``fetch_pending_for_target_and_child`` finds
        it via ``follow_up_payload.metadata.child_id``).
        """
        watch_id = f"watch-{source_task_id}-{child_instance_id[:8]}"
        # Mirror FollowUp.to_payload() shape so the bus's in-memory
        # filter (metadata.child_id == child_instance_id) matches.
        payload = {
            "target_instance_id": target_instance_id,
            "message": (
                f"[dependency_bus] child {child_instance_id} "
                f"completed for message msg-{source_task_id}"
            ),
            "source": f"internal_agent:{target_instance_id}",
            "metadata": {
                "kind": "child_complete",
                "child_id": child_instance_id,
                "parent_id": target_instance_id,
                "message_id": f"msg-{source_task_id}",
            },
        }
        watcher = DependencyWatcher(
            watch_id=watch_id,
            source_task_id=source_task_id,
            target_instance_id=target_instance_id,
            follow_up_payload=payload,
            watcher_metadata={},
            state=state,
        )
        with Session(engine) as session:
            session.add(watcher)
            session.commit()
        return watch_id

    def _build_async_child_reports_service(self, engine: Engine):
        """Build a ChildReportsService for async dispatch tests.

        Mirrors ``_build_child_reports_service`` but leaves
        ``_task_repo`` as a MagicMock with ``get_by_message`` returning
        ``None`` — that disables the task-keyed emit (returns []) and
        forces the corrective emit path to do all the work, exactly as
        in the production multi-turn scenario where the child's
        terminal turn is on a task id that did NOT register a watcher.
        """
        from unittest.mock import MagicMock

        manager = MagicMock(name="InstanceManager")
        manager.engine = engine
        manager.write_guard = WritePauseGuard()
        manager._checkpointer = None
        manager._live_hub = None
        manager._queue_repository = MagicMock()
        manager._instance_repository = MagicMock()
        manager._worker_pool = None  # disables notify_work()

        # ``_task_repo.get_by_message`` returns None — the task-keyed
        # emit in ``_dispatch_post_commit_side_effects`` will then be
        # called with task_id=None and short-circuit, leaving the
        # corrective (parent, child) emit as the sole path to fire
        # the watcher. This mirrors the production multi-turn case.
        task_repo = MagicMock()
        task_repo.get_by_message = MagicMock(return_value=None)
        manager._task_repo = task_repo

        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = manager
        service._events_service = None
        return service

    @pytest.mark.asyncio
    async def test_corrective_emit_fires_watcher_when_task_keyed_emit_misses(
        self, pg_engine: Engine
    ):
        """The corrective emit must fire the parent's PENDING watcher
        when the task-keyed emit cannot match (multi-turn child).

        Scenario mirroring the production bug:
          - Leader's watcher for wanderer is registered on
            ``source_task_id="task-init-T0"`` (the FIRST
            ``process_message`` task, when leader sent ``send_message``
            to wanderer).
          - Wanderer's terminal graph turn runs on a LATER
            ``PROCESS_REPORT`` task "task-final-TN" (the task that
            emits wanderer's completion_report back to leader).
          - The task-keyed ``_emit_terminal_via_bus(task_id=TN)``
            cannot match the watcher (keyed on T0), so without the
            corrective emit the watcher stays PENDING and leader
            wedges in ``waiting_children``.

        With the fix, the corrective emit fires the watcher by
        matching on the (parent, child) pair; the parent's pending
        count drops to 0.
        """
        service = self._build_async_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine,
            instance_id="wanderer",
            parent_id="leader",
            status=InstanceStatus.RUNNING.value,
        )

        # Watcher keyed on the FIRST task (T0) — what production
        # ``send_message`` creates on the parent's first ``send_message``
        # to wanderer. child_id (in metadata) is wanderer's instance id.
        self._seed_watcher_production_shape(
            pg_engine,
            target_instance_id="leader",
            source_task_id="task-init-T0",
            child_instance_id="wanderer",
        )
        # Pre-condition: leader has 1 PENDING watcher.
        assert _count_pending_watchers(
            pg_engine, target_instance_id="leader"
        ) == 1

        # Wanderer reaches its terminal graph turn. The bus hook in
        # ``_dispatch_post_commit_side_effects`` runs AFTER the
        # sync DB helper commits wanderer's COMPLETED status and the
        # completion_report to leader.
        result = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-wanderer-final-TN",
            last_content="wanderer completion_report to leader",
        )
        assert result.outcome == "regular_child_completed", (
            f"Pre-condition: regular_child_completed outcome; "
            f"got '{result.outcome}'"
        )
        assert result.parent_id == "leader"

        # The sync helper does NOT fire the watcher — the bus emit hook
        # runs post-commit in ``_dispatch_post_commit_side_effects``.
        # Pre-fix this assertion would pass; post-fix this is where the
        # watcher would be expected to still be PENDING if the bus hook
        # had not run yet.
        assert _count_pending_watchers(
            pg_engine, target_instance_id="leader"
        ) == 1, (
            "Sync DB helper must NOT fire the watcher — the bus hook "
            "fires post-commit in _dispatch_post_commit_side_effects"
        )

        # ── The corrective emit ───────────────────────────────────────
        # The task repo mock returns None for ``get_by_message`` so the
        # task-keyed emit short-circuits. Only the corrective
        # (parent, child) emit can fire the watcher. Pre-fix this call
        # did not exist; the watcher would stay PENDING forever.
        await service._dispatch_post_commit_side_effects(
            result,
            last_content="wanderer completion_report to leader",
            completed_message_id="msg-wanderer-final-TN",
        )

        # ── Post-fix assertions ─────────────────────────────────────
        # The corrective emit MUST have transitioned the watcher to
        # FIRED — pending count drops to 0, leader is no longer wedged.
        assert _count_pending_watchers(
            pg_engine, target_instance_id="leader"
        ) == 0, (
            "Corrective emit must fire the (parent, child)-matched "
            "watcher when the task-keyed emit cannot match — "
            "pre-fix the watcher stayed PENDING and the parent wedged "
            "in waiting_children"
        )

        # The watcher row itself is FIRED (state transitioned, not
        # deleted) and stamped with ``fired_at`` + ``enqueued_at``.
        with Session(pg_engine) as session:
            stmt = select(DependencyWatcher).where(
                DependencyWatcher.target_instance_id == "leader"
            )
            row = session.scalars(stmt).first()
            assert row is not None, "Watcher row must persist post-fire"
            assert row.state == DependencyWatcherState.FIRED.value, (
                f"Watcher state must be FIRED post-corrective emit; "
                f"got {row.state}"
            )
            assert row.fired_at is not None, (
                "fired_at must be stamped by transition_state"
            )
            assert row.enqueued_at is not None, (
                "enqueued_at dedup marker must be stamped so a future "
                "restart's _recover_fired_unsent does not re-deliver "
                "this row"
            )

    @pytest.mark.asyncio
    async def test_corrective_emit_is_noop_when_task_keyed_emit_already_fired(
        self, pg_engine: Engine
    ):
        """When the task-keyed emit already fired the watcher (single-
        turn child case), the corrective emit must be a no-op.

        Exactly-once is enforced by ``transition_state``'s guarded
        ``WHERE state = 'PENDING'`` Core UPDATE: a row already FIRED
        returns ``rowcount == 0`` and the corrective emit appends
        nothing to its fired list.

        Scenario:
          - Watcher registered with source_task_id="task-final-100"
            (the same task that reaches the terminal graph turn —
            single-turn child case).
          - Task repo mock returns a Task with id=100 for
            ``get_by_message``, so the task-keyed emit fires the
            watcher via ``source_task_id=100``.
          - The corrective emit then runs and finds no PENDING rows
            (already FIRED) — returns [].
        """
        from unittest.mock import MagicMock

        service = self._build_async_child_reports_service(pg_engine)
        # Override the task repo mock to return a Task-like object
        # whose ``id`` matches the watcher's source_task_id, so the
        # task-keyed emit will fire the watcher.
        task_like = MagicMock()
        task_like.id = "task-final-100"
        service._manager._task_repo.get_by_message = MagicMock(
            return_value=task_like
        )

        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine,
            instance_id="wanderer",
            parent_id="leader",
        )
        # Watcher registered with the task id that will be the
        # terminal turn — single-turn case where the task-keyed emit
        # fires the watcher.
        self._seed_watcher_production_shape(
            pg_engine,
            target_instance_id="leader",
            source_task_id="task-final-100",
            child_instance_id="wanderer",
        )

        result = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-wanderer-fin",
            last_content="wanderer completion report",
        )
        assert result.outcome == "regular_child_completed"

        # Pre-dispatch: still PENDING — bus emit runs post-commit.
        assert _count_pending_watchers(
            pg_engine, target_instance_id="leader"
        ) == 1

        # Dispatch runs BOTH emits (task-keyed first, corrective second).
        await service._dispatch_post_commit_side_effects(
            result,
            last_content="wanderer completion report",
            completed_message_id="msg-wanderer-fin",
        )

        # Exactly-once: watcher FIRED once, not double-fired.
        assert _count_pending_watchers(
            pg_engine, target_instance_id="leader"
        ) == 0
        with Session(pg_engine) as session:
            stmt = select(DependencyWatcher).where(
                DependencyWatcher.target_instance_id == "leader"
            )
            row = session.scalars(stmt).first()
            assert row is not None
            assert row.state == DependencyWatcherState.FIRED.value

    @pytest.mark.asyncio
    async def test_corrective_emit_no_watcher_is_safe_noop(
        self, pg_engine: Engine
    ):
        """When no PENDING watcher exists for the (parent, child)
        pair, the corrective emit must be a safe no-op (returns [],
        raises nothing).
        """
        service = self._build_async_child_reports_service(pg_engine)
        _seed_instance(pg_engine, instance_id="leader", parent_id=None)
        _seed_instance(
            pg_engine,
            instance_id="wanderer",
            parent_id="leader",
        )
        # No watcher seeded.

        result = service._process_child_completion_db_sync(
            instance_id="wanderer",
            completed_message_id="msg-no-watch",
            last_content="response",
        )
        assert result.outcome == "regular_child_completed"

        # Dispatch should NOT raise even with no watchers.
        await service._dispatch_post_commit_side_effects(
            result,
            last_content="response",
            completed_message_id="msg-no-watch",
        )

        # Still no watchers — no orphan row was created.
        assert _count_pending_watchers(
            pg_engine, target_instance_id="leader"
        ) == 0


def _count_pending_watchers(
    engine: Engine, *, target_instance_id: str
) -> int:
    """Count PENDING DependencyWatcher rows targeting the given parent."""
    with Session(engine) as session:
        stmt = (
            select(func.count())
            .select_from(DependencyWatcher)
            .where(
                DependencyWatcher.target_instance_id == target_instance_id
            )
            .where(
                DependencyWatcher.state
                == DependencyWatcherState.PENDING.value
            )
        )
        return int(session.scalar(stmt) or 0)
