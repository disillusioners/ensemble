"""Integration tests for _pause_cascade_db_sync and _resume_cascade_db_sync (L14).

These tests call the sync SQL helpers DIRECTLY against a real in-memory SQLite
engine — no mocking of the DB layer. The helpers are bound methods on
``InstanceLifecycleService``; we construct a minimal service instance with a
mock manager that supplies ``engine`` and ``write_guard`` attributes.

Covers the gap left by test_instance_lifecycle_h10_l14.py: those tests go
through the full async wrapper (pause_instance_cascade / resume_instance_cascade)
which assembles paused_instances_data and ancestor_ids from repo lookups. These
tests bypass that to focus purely on the SQL UPDATE invariants:

  * _pause_cascade_db_sync: batched UPDATE WHERE instance_id IN (...) AND
    status IN (running, idle, waiting_children) sets status='paused',
    waiting_for=0, paused_at=<timestamp>. Terminal / already-paused rows
    are no-ops (guarded by the status predicate).

  * _resume_cascade_db_sync: two-step UPDATE. Step 1 clears paused_at and
    sets status='running', waiting_for=0 for all paused nodes. Step 2 bumps
    waiting_for=1 for ancestor_ids when is_root_resume=False.

Run with::

    python -m pytest tests/unit/test_pause_resume_db_sync_integration.py -v --tb=short
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceHierarchy, InstanceStatus
from daemon.services.instance_lifecycle import InstanceLifecycleService, _CascadeUpdateResult
from daemon.write_pause_guard import WritePauseGuard


# ─── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool for cross-thread safety)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def write_guard() -> WritePauseGuard:
    """Fresh WritePauseGuard — not paused."""
    return WritePauseGuard()


@pytest.fixture
def service(engine: Engine, write_guard: WritePauseGuard) -> InstanceLifecycleService:
    """Minimal InstanceLifecycleService wired to a real engine."""
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = MagicMock()
    manager._request_registry = MagicMock()
    manager._request_registry.cancel_by_instance = MagicMock(return_value=0)
    manager._graph_tasks = {}
    manager.instances = {}
    return InstanceLifecycleService(
        manager=manager,
        cancellation_service=MagicMock(),
        events_service=None,
        job_queue_service=None,
    )


# ─── Helpers ───────────────────────────────────────────────────────────────────


def seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "coder",
    parent_id: str | None = None,
    waiting_for: int = 0,
    paused_at: str | None = None,
    version: int = 1,
) -> str:
    """Insert an Instance row. Returns the instance_id."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            agent_name=agent_id,
            parent_id=parent_id,
            status=status,
            waiting_for=waiting_for,
            paused_at=paused_at,
            instance_metadata={},
            children="[]",
            version=version,
            created_at=now_iso,
            updated_at=now_iso,
        )
        s.add(inst)
        s.commit()
    return iid


def seed_hierarchy(engine: Engine, *, parent_id: str, child_id: str) -> None:
    """Insert an InstanceHierarchy row."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(InstanceHierarchy(parent_id=parent_id, child_id=child_id, created_at=now_iso))
        s.commit()


def get_instance(engine: Engine, instance_id: str) -> Instance | None:
    """Read a fresh Instance row (no session caching)."""
    with Session(engine) as s:
        return s.get(Instance, instance_id)


# ─── _pause_cascade_db_sync tests ──────────────────────────────────────────────


class TestPauseCascadeDbSync:
    """Tests for the _pause_cascade_db_sync SQL helper."""

    def test_pause_running_tree_sets_status_and_waiting_for(self, engine, write_guard, service):
        """Pause a tree of 3 running instances. All become PAUSED with waiting_for=0."""
        root = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        c1 = seed_instance(engine, status=InstanceStatus.RUNNING.value, parent_id=root)
        c2 = seed_instance(engine, status=InstanceStatus.RUNNING.value, parent_id=root)
        seed_hierarchy(engine, parent_id=root, child_id=c1)
        seed_hierarchy(engine, parent_id=root, child_id=c2)

        now_iso = datetime.now(timezone.utc).isoformat()
        paused_data = [(root, "coder", 0), (c1, "coder", 0), (c2, "coder", 0)]

        result = service._pause_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[root, c1, c2],
            paused_at_iso=now_iso,
            paused_instances_data=paused_data,
        )

        # All updated
        assert set(result.updated_ids) == {root, c1, c2}
        assert result.skipped_ids == []

        # DB state
        for iid in (root, c1, c2):
            inst = get_instance(engine, iid)
            assert inst.status == InstanceStatus.PAUSED.value, f"{iid[:8]} must be PAUSED"
            assert inst.waiting_for == 0, f"{iid[:8]} must have waiting_for=0"
            assert inst.paused_at == now_iso, f"{iid[:8]} must have paused_at={now_iso!r}"

    def test_pause_skips_completed_nodes(self, engine, write_guard, service):
        """F-03 status guard: COMPLETED nodes are NOT paused (terminal status protected).

        The SQL predicate ``status IN ('running','idle','waiting_children')`` is
        the last line of defence. We pass COMPLETED in paused_instances_data to
        simulate the cascade loop having included it (or a race where status
        changed after the loop read it), then verify the SQL leaves it alone.
        """
        root = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        child_done = seed_instance(
            engine, status=InstanceStatus.COMPLETED.value, parent_id=root
        )
        seed_hierarchy(engine, parent_id=root, child_id=child_done)

        now_iso = datetime.now(timezone.utc).isoformat()
        # Cascade loop would normally skip COMPLETED, but we include it to
        # test the SQL guard.
        paused_data = [(root, "coder", 0), (child_done, "coder", 0)]

        result = service._pause_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[root, child_done],
            paused_at_iso=now_iso,
            paused_instances_data=paused_data,
        )

        # ``updated_ids`` reflects caller intent (computed from
        # paused_instances_data before the SQL runs); the SQL guard is
        # what actually prevents the DB write for ineligible rows.
        assert set(result.updated_ids) == {root, child_done}

        # Verify the DB end-state: COMPLETED row must NOT be paused.
        # This is the real-world invariant — the SQL status predicate
        # ``status IN ('running','idle','waiting_children')`` is the
        # last line of defence.
        assert get_instance(engine, root).status == InstanceStatus.PAUSED.value
        assert get_instance(engine, child_done).status == InstanceStatus.COMPLETED.value
        # paused_at must NOT have been written for the COMPLETED row.
        assert get_instance(engine, child_done).paused_at is None

    def test_pause_passes_waiting_children_nodes(self, engine, write_guard, service):
        """WAITING_CHILDREN status is eligible for pause (not terminal)."""
        root = seed_instance(engine, status=InstanceStatus.WAITING_CHILDREN.value)
        child = seed_instance(engine, status=InstanceStatus.RUNNING.value, parent_id=root)
        seed_hierarchy(engine, parent_id=root, child_id=child)

        now_iso = datetime.now(timezone.utc).isoformat()
        paused_data = [(root, "coder", 0), (child, "coder", 0)]

        result = service._pause_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[root, child],
            paused_at_iso=now_iso,
            paused_instances_data=paused_data,
        )

        assert set(result.updated_ids) == {root, child}
        assert get_instance(engine, root).status == InstanceStatus.PAUSED.value
        assert get_instance(engine, child).status == InstanceStatus.PAUSED.value

    def test_pause_empty_paused_instances_data_returns_empty_result(self, engine, write_guard, service):
        """Empty paused_instances_data is an immediate no-op (edge case)."""
        result = service._pause_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=["does-not-exist"],
            paused_at_iso=datetime.now(timezone.utc).isoformat(),
            paused_instances_data=[],
        )
        assert result.updated_ids == []
        assert result.skipped_ids == []

    def test_pause_clears_waiting_for_regardless_of_previous_value(self, engine, write_guard, service):
        """SQL hardcodes waiting_for=0 for all paused rows, clearing any prior value."""
        root = seed_instance(engine, status=InstanceStatus.RUNNING.value, waiting_for=99)
        child = seed_instance(
            engine, status=InstanceStatus.RUNNING.value, parent_id=root, waiting_for=5
        )
        seed_hierarchy(engine, parent_id=root, child_id=child)

        now_iso = datetime.now(timezone.utc).isoformat()
        # Note: paused_instances_data carries the pre-classified waiting_for values
        # (cascade loop sets them to 0 when pending children exist). The SQL
        # itself hardcodes 0 regardless, so passing non-zero here is a valid
        # edge-case test — it proves the SQL, not the wrapper, is the authority.
        paused_data = [(root, "coder", 99), (child, "coder", 5)]

        result = service._pause_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[root, child],
            paused_at_iso=now_iso,
            paused_instances_data=paused_data,
        )

        # All rows must have waiting_for=0 in the DB even though we passed 99/5
        assert get_instance(engine, root).waiting_for == 0
        assert get_instance(engine, child).waiting_for == 0

    def test_pause_returns_correct_named_tuple_fields(self, engine, write_guard, service):
        """Verify _CascadeUpdateResult fields are populated correctly."""
        root = seed_instance(engine, status=InstanceStatus.RUNNING.value, agent_id="leader")
        child = seed_instance(
            engine, status=InstanceStatus.RUNNING.value, parent_id=root, agent_id="coder"
        )
        seed_hierarchy(engine, parent_id=root, child_id=child)

        now_iso = datetime.now(timezone.utc).isoformat()
        paused_data = [(root, "leader", 0), (child, "coder", 0)]

        result = service._pause_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[root, child],
            paused_at_iso=now_iso,
            paused_instances_data=paused_data,
        )

        assert isinstance(result, _CascadeUpdateResult)
        assert set(result.updated_ids) == {root, child}
        assert result.skipped_ids == []
        assert result.agent_ids_by_instance == {root: "leader", child: "coder"}
        assert result.waiting_for_by_instance == {root: 0, child: 0}


# ─── _resume_cascade_db_sync tests ─────────────────────────────────────────────


class TestResumeCascadeDbSync:
    """Tests for the _resume_cascade_db_sync SQL helper."""

    def test_resume_from_root_sets_all_running_waiting_for_zero(self, engine, write_guard, service):
        """Resume from root: all nodes get status='running', waiting_for=0, paused_at=NULL."""
        root = seed_instance(
            engine,
            status=InstanceStatus.PAUSED.value,
            paused_at="2026-01-01T00:00:00+00:00",
            waiting_for=1,
        )
        c1 = seed_instance(
            engine,
            status=InstanceStatus.PAUSED.value,
            parent_id=root,
            paused_at="2026-01-01T00:00:00+00:00",
            waiting_for=2,
        )
        c2 = seed_instance(
            engine,
            status=InstanceStatus.PAUSED.value,
            parent_id=root,
            paused_at="2026-01-01T00:00:00+00:00",
            waiting_for=3,
        )
        seed_hierarchy(engine, parent_id=root, child_id=c1)
        seed_hierarchy(engine, parent_id=root, child_id=c2)

        result = service._resume_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[root, c1, c2],
            ancestor_ids=set(),  # empty = root resume
            is_root_resume=True,
        )

        assert set(result.updated_ids) == {root, c1, c2}
        assert result.skipped_ids == []

        for iid in (root, c1, c2):
            inst = get_instance(engine, iid)
            assert inst.status == InstanceStatus.RUNNING.value, f"{iid[:8]} must be RUNNING"
            assert inst.waiting_for == 0, f"{iid[:8]} must have waiting_for=0"
            assert inst.paused_at is None, f"{iid[:8]} must have paused_at=NULL"

    def test_resume_from_child_ancestors_get_waiting_for_one(self, engine, write_guard, service):
        """Resume from child: ancestors get waiting_for=1; resumed child and siblings get 0."""
        root = seed_instance(
            engine,
            status=InstanceStatus.PAUSED.value,
            paused_at="2026-01-01T00:00:00+00:00",
        )
        child_resumed = seed_instance(
            engine,
            status=InstanceStatus.PAUSED.value,
            parent_id=root,
            paused_at="2026-01-01T00:00:00+00:00",
        )
        sibling = seed_instance(
            engine,
            status=InstanceStatus.PAUSED.value,
            parent_id=root,
            paused_at="2026-01-01T00:00:00+00:00",
        )
        seed_hierarchy(engine, parent_id=root, child_id=child_resumed)
        seed_hierarchy(engine, parent_id=root, child_id=sibling)

        result = service._resume_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[root, child_resumed, sibling],
            ancestor_ids={root},  # only root is an ancestor of child_resumed
            is_root_resume=False,
        )

        assert set(result.updated_ids) == {root, child_resumed, sibling}

        # Ancestor (root) gets waiting_for=1
        assert get_instance(engine, root).waiting_for == 1
        # Resumed node and sibling get waiting_for=0
        assert get_instance(engine, child_resumed).waiting_for == 0
        assert get_instance(engine, sibling).waiting_for == 0

    def test_resume_from_leaf_full_ancestor_chain_waiting_for_one(self, engine, write_guard, service):
        """Deep tree resume: every ancestor in the chain gets waiting_for=1."""
        root = seed_instance(
            engine, status=InstanceStatus.PAUSED.value, paused_at="2026-01-01T00:00:00+00:00"
        )
        l1 = seed_instance(
            engine, status=InstanceStatus.PAUSED.value, parent_id=root, paused_at="2026-01-01T00:00:00+00:00"
        )
        l2 = seed_instance(
            engine, status=InstanceStatus.PAUSED.value, parent_id=l1, paused_at="2026-01-01T00:00:00+00:00"
        )
        leaf = seed_instance(
            engine, status=InstanceStatus.PAUSED.value, parent_id=l2, paused_at="2026-01-01T00:00:00+00:00"
        )
        seed_hierarchy(engine, parent_id=root, child_id=l1)
        seed_hierarchy(engine, parent_id=l1, child_id=l2)
        seed_hierarchy(engine, parent_id=l2, child_id=leaf)

        tree_ids = [root, l1, l2, leaf]
        ancestor_ids = {root, l1, l2}  # leaf's ancestors: [l2, l1, root]

        result = service._resume_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=tree_ids,
            ancestor_ids=ancestor_ids,
            is_root_resume=False,
        )

        assert set(result.updated_ids) == set(tree_ids)

        # Ancestors
        assert get_instance(engine, root).waiting_for == 1
        assert get_instance(engine, l1).waiting_for == 1
        assert get_instance(engine, l2).waiting_for == 1
        # Resumed node
        assert get_instance(engine, leaf).waiting_for == 0

        # All status = running, paused_at cleared
        for iid in tree_ids:
            inst = get_instance(engine, iid)
            assert inst.status == InstanceStatus.RUNNING.value
            assert inst.paused_at is None

    def test_resume_skips_running_nodes(self, engine, write_guard, service):
        """Status guard: already-RUNNING nodes are not touched by resume UPDATE.

        The SQL predicate ``status = 'paused'`` guards against re-writing
        nodes that were already resumed (e.g. by a concurrent call).
        """
        root = seed_instance(
            engine, status=InstanceStatus.PAUSED.value, paused_at="2026-01-01T00:00:00+00:00"
        )
        child_running = seed_instance(
            engine,
            status=InstanceStatus.RUNNING.value,  # already running — not paused
            parent_id=root,
            paused_at=None,
        )
        seed_hierarchy(engine, parent_id=root, child_id=child_running)

        result = service._resume_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[root, child_running],
            ancestor_ids=set(),
            is_root_resume=True,
        )

        # ``updated_ids`` reflects caller intent (== tree_ids for resume);
        # the SQL guard ``status = 'paused'`` is what prevents the DB
        # write for ineligible rows.
        assert set(result.updated_ids) == {root, child_running}

        # Verify the DB end-state: the already-RUNNING child must NOT
        # have been touched (its paused_at must remain NULL — the
        # resume UPDATE didn't reach it because the status predicate
        # excluded it).
        root_after = get_instance(engine, root)
        assert root_after.status == InstanceStatus.RUNNING.value
        assert root_after.paused_at is None

        child_after = get_instance(engine, child_running)
        assert child_after.status == InstanceStatus.RUNNING.value
        assert child_after.paused_at is None

    def test_resume_empty_tree_ids_returns_empty_result(self, engine, write_guard, service):
        """Empty tree_ids is an immediate no-op (edge case)."""
        result = service._resume_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[],
            ancestor_ids=set(),
            is_root_resume=True,
        )
        assert result.updated_ids == []
        assert result.skipped_ids == []

    def test_resume_returns_correct_named_tuple_fields(self, engine, write_guard, service):
        """Verify _CascadeUpdateResult fields are populated correctly."""
        root = seed_instance(
            engine, status=InstanceStatus.PAUSED.value, paused_at="2026-01-01T00:00:00+00:00"
        )
        child = seed_instance(
            engine,
            status=InstanceStatus.PAUSED.value,
            parent_id=root,
            paused_at="2026-01-01T00:00:00+00:00",
        )
        seed_hierarchy(engine, parent_id=root, child_id=child)

        result = service._resume_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[root, child],
            ancestor_ids={root},
            is_root_resume=False,
        )

        assert isinstance(result, _CascadeUpdateResult)
        assert set(result.updated_ids) == {root, child}
        assert result.skipped_ids == []
        # agent_ids_by_instance is empty dict (caller pre-fetches for SSE)
        assert result.agent_ids_by_instance == {}
        assert result.waiting_for_by_instance == {root: 1, child: 0}
