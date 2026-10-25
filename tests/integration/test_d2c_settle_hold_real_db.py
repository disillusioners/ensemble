"""D2(c) real-DB settle-hold — the flagged coverage gap, CLOSED.

The end-of-train verification (2026-10-24, gap list item 1) flagged:
``fetch_pending_for_targets`` (``dependency_bus/repository.py``) had
ZERO real-DB coverage anywhere in the repo (all 5 union tests mock the
repo), and no test seeded a real PENDING watcher and asserted the
leader does NOT settle through the C row.

This file closes BOTH halves against a REAL file-backed SQLite database
(the repo recipe: file-backed engine, NullPool, WAL/busy_timeout/
foreign_keys PRAGMAs, ``create_all`` — ``test_phase1_ledger_pins.py``):

1. **Real-SQL coverage of ``fetch_pending_for_targets``** — PENDING vs
   FIRED vs CANCELLED vs out-of-tree vs empty-IN-list, no mocks.

2. **End-to-end settle-hold through the C row** — seed a real PENDING
   watcher on a tree member, render it through the REAL D2(c) provider
   (``_commissioned_rows_for_tree`` → the real repository), and assert
   the settle-hold mechanics:
     * the rendered C row carries ``commissioned_pending`` — a status
       DELIBERATELY NOT ``completed`` (the in-flight marker);
     * ``_completed_child_ids`` (the delivery-evidence suppressor) does
       NOT admit the child — the C row cannot be swallowed as
       "delivered", so the judge's SOURCE C section still reports live
       work and the leader cannot verdict the mission complete through
       it;
   and the RELEASE leg: fire the watcher (real ``transition_state``)
   → the provider renders nothing → the hold is lifted.

The subordination clause itself (judge prompt text) is out of scope —
this file proves the DATA side: a real pending watcher is visible to
the exact query the C-union consumes, and is NOT suppressible.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.dependency_bus.models import (
    DependencyWatcher,
    DependencyWatcherState,
)
from daemon.repositories.dependency_bus.repository import (
    DependencyWatcherRepository,
)
from daemon.repositories.instance.models import Instance
from daemon.services.attestation_resolver_activation import (
    C_TREE_COMMISSIONED_STATUS,
    _completed_child_ids,
    _commissioned_rows_for_tree,
)

_LEADER = "aaaa1111-0000-4000-8000-0000000leadr"[:36]
_TREE_MEMBER = "bbbb2222-0000-4000-8000-0000000child"[:36]
_OUT_OF_TREE = "cccc3333-0000-4000-8000-0000000other"[:36]


def _watcher(
    watch_id: str,
    source_task_id: int,
    target: str,
    state: str,
    child_id: str,
) -> DependencyWatcher:
    return DependencyWatcher(
        watch_id=watch_id,
        source_task_id=str(source_task_id),
        target_instance_id=target,
        follow_up_payload={
            "target_instance_id": target,
            "message": "x",
            "source": f"internal_agent:{target}",
            "metadata": {"kind": "child_complete", "child_id": child_id},
        },
        watcher_metadata={"kind": "child_complete"},
        state=state,
    )


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'd2c-settle-hold.sqlite'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    with Session(eng) as session:
        for iid in (_LEADER, _TREE_MEMBER, _OUT_OF_TREE):
            session.add(
                Instance(
                    instance_id=iid,
                    agent_id="worker",
                    agent_dir=f"agents/worker/{iid[:8]}",
                    status="running",
                    project_id=None,
                )
            )
        session.commit()
    yield eng
    eng.dispose()


@pytest.fixture
def watcher_repo(engine) -> DependencyWatcherRepository:
    return DependencyWatcherRepository(engine)


def _seed_tree(engine, watcher_repo: DependencyWatcherRepository) -> None:
    """The D2 shape: leader-tree work still in flight + distractors."""
    with Session(engine) as session:
        session.add_all(
            [
                # THE settle-hold row: PENDING watcher targeting the
                # tree member (send_message commission shape).
                _watcher(
                    "w-pending-tree",
                    101,
                    _TREE_MEMBER,
                    DependencyWatcherState.PENDING.value,
                    _TREE_MEMBER,
                ),
                # Distractor: same child, already FIRED (work done).
                _watcher(
                    "w-fired",
                    102,
                    _TREE_MEMBER,
                    DependencyWatcherState.FIRED.value,
                    _TREE_MEMBER,
                ),
                # Distractor: CANCELLED (the parent was stopped).
                _watcher(
                    "w-cancelled",
                    103,
                    _LEADER,
                    DependencyWatcherState.CANCELLED.value,
                    _LEADER,
                ),
                # Distractor: PENDING but OUT of the leader tree.
                _watcher(
                    "w-pending-out-of-tree",
                    104,
                    _OUT_OF_TREE,
                    DependencyWatcherState.PENDING.value,
                    _OUT_OF_TREE,
                ),
            ]
        )
        session.commit()


class TestFetchPendingForTargetsRealSQL:
    async def test_returns_exactly_the_pending_in_tree_rows(
        self, engine, watcher_repo
    ):
        _seed_tree(engine, watcher_repo)
        rows = watcher_repo.fetch_pending_for_targets([_LEADER, _TREE_MEMBER])
        ids = {r.watch_id for r in rows}
        # the PENDING in-tree row IS returned…
        assert "w-pending-tree" in ids
        # …and NONE of the distractors are
        assert "w-fired" not in ids
        assert "w-cancelled" not in ids
        assert "w-pending-out-of-tree" not in ids
        assert len(rows) == 1

    async def test_empty_target_list_short_circuits(self, watcher_repo):
        # the empty-IN-list contract: [] → [] with NO query
        assert watcher_repo.fetch_pending_for_targets([]) == []

    async def test_no_pending_rows_returns_empty(self, engine, watcher_repo):
        _seed_tree(engine, watcher_repo)
        # a target set with no PENDING rows at all
        rows = watcher_repo.fetch_pending_for_targets(["no-such-target"])
        assert rows == []


class TestSettleHoldThroughTheCRow:
    async def test_pending_watcher_holds_the_settle(
        self, engine, watcher_repo
    ):
        """End-to-end: seed a real PENDING watcher → the leader does NOT
        settle through the C row."""
        _seed_tree(engine, watcher_repo)
        manager_stub = SimpleNamespace(_watcher_repo=watcher_repo)
        tree_ids = [_LEADER, _TREE_MEMBER]

        # The REAL D2(c) provider (real repo, real SQL — zero mocks).
        c_rows = _commissioned_rows_for_tree(manager_stub, tree_ids)

        # The C row EXISTS — the tree looks NON-quiet to the gate.
        assert len(c_rows) == 1
        assert c_rows[0]["instance_id"] == _TREE_MEMBER
        # The status is the in-flight marker — DELIBERATELY NOT
        # "completed", so the delivered-child suppressor cannot admit it.
        assert c_rows[0]["status"] == C_TREE_COMMISSIONED_STATUS
        assert c_rows[0]["status"] != "completed"

        # The suppression half of the hold: the delivered-child set does
        # NOT contain the child — the C row survives as live work.
        completed = _completed_child_ids(c_rows)
        assert _TREE_MEMBER not in completed

    async def test_fired_watcher_releases_the_hold(
        self, engine, watcher_repo
    ):
        """The release leg: once the work completes (watcher FIRED — a
        real guarded transition), the C-union renders NOTHING and the
        settle-hold is lifted."""
        _seed_tree(engine, watcher_repo)
        # Real guarded transition PENDING → FIRED (exactly-once).
        fired = watcher_repo.transition_state(
            "w-pending-tree", DependencyWatcherState.FIRED.value
        )
        assert fired is True
        # a second fire attempt is a no-op (rowcount 0)
        assert (
            watcher_repo.transition_state(
                "w-pending-tree", DependencyWatcherState.FIRED.value
            )
            is False
        )

        manager_stub = SimpleNamespace(_watcher_repo=watcher_repo)
        c_rows = _commissioned_rows_for_tree(
            manager_stub, [_LEADER, _TREE_MEMBER]
        )
        assert c_rows == []
        # and the delivered-child suppressor sees nothing to hold on
        assert _completed_child_ids(c_rows) == frozenset()

    async def test_missing_repo_fails_open_to_no_rows(self):
        """The provider's fail-open posture: no repository → no rows →
        the gate degrades to legacy (never a crash)."""
        manager_stub = SimpleNamespace(_watcher_repo=None)
        assert _commissioned_rows_for_tree(manager_stub, [_LEADER]) == []

    async def test_empty_tree_renders_no_rows(self, engine, watcher_repo):
        manager_stub = SimpleNamespace(_watcher_repo=watcher_repo)
        assert _commissioned_rows_for_tree(manager_stub, []) == []
