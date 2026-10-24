"""D2(b) + D2(c) — the nudge completion watcher + the tree-scoped C-union.

Completion-subsystem Phase 1, defect D2 (premature settle). Three parts:

* **(b) WATCHER-MINT** — an ``instance_nudge`` used to write an audit
  row and NOTHING ELSE. The commissioned work was invisible to the
  system's single visibility chokepoint (``dependency_watchers``), so
  the root gate (L3) and the attestation gate (R2) could not see the
  mission had out-of-tree work in flight — the leader settled while
  the nudged worker was still running (the demonstrated fd302262
  shape: the mission settled 5m06s early).
* **(b) ORPHAN-SWEEP PIN** — a watcher minted on a made-up task id is
  CANCELLED by the bus's orphan sweep (grace 30s + sweep interval
  90s ⇒ ≤120s), so the mint MUST key on a REAL task id. This file
  pins BOTH halves: a fake id is cancelled, a real id survives.
* **(c) TREE-SCOPED C-UNION** — the judge reads the same structural
  tree as the gate, so an out-of-tree commission is invisible to it.
  The provider now renders the PENDING watchers of the leader's tree
  as "commissioned work in flight" rows (a READ-SIDE JOIN over the
  existing table — no new state).

FAIL-OPEN POSTURE
  * the mint is fail-open (a failed mint degrades to today's
    behaviour — no watcher — and never fails the nudge);
  * the C-union provider fails open to EMPTY rows (the C seam already
    treats a provider raise as a whole-eval fail-open).

MUTATION NOTE: the behavioural pins below are proven to go RED when the
corresponding production change is reverted — see the commit message.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
# The orphan sweep's IN-subquery runs against the ``task`` table — the
# model must be registered so ``create_all`` builds it (the sweep
# fails OPEN when the table is missing, which would silently disable
# the very cancellation this file pins).
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.dependency_bus import (
    DependencyWatcher,
    DependencyWatcherRepository,
    DependencyWatcherState,
)
from daemon.services.dependency_bus import DependencyBus, FollowUp
from daemon.services.attestation_resolver_activation import (
    C_TREE_COMMISSIONED_STATUS,
    make_tree_rows_provider,
)
from daemon.tools.instance_ops import _register_nudge_completion_watcher


# ── Part 1 — the mint helper (unit) ─────────────────────────────────────────


class _FakeTask:
    def __init__(self, task_id: int) -> None:
        self.id = task_id


def _manager_with_task_repo(task, watcher_repo=None, engine=None):
    manager = MagicMock()
    manager._task_repo = MagicMock()
    manager._task_repo.get_by_message = MagicMock(return_value=task)
    manager._task_repo.find_running_by_instance = MagicMock(return_value=task)
    if watcher_repo is not None:
        manager._watcher_repo = watcher_repo
    return manager


class TestMintHelper:
    async def test_the_enqueue_lane_keys_on_the_real_task_id(self):
        """The enqueue lane resolves the message id → the REAL Task row
        (the enqueue minted a PENDING Task synchronously)."""
        bus = MagicMock()
        bus.watch = AsyncMock()
        manager = _manager_with_task_repo(_FakeTask(4242))
        from daemon.services import dependency_bus as bus_mod

        original = bus_mod.get_dependency_bus
        bus_mod.get_dependency_bus = lambda: bus
        try:
            ok = await _register_nudge_completion_watcher(
                manager,
                nudger_instance_id="nudger-1",
                worker_instance_id="worker-1",
                message_id="msg-1",
                lane="enqueue",
            )
        finally:
            bus_mod.get_dependency_bus = original

        assert ok is True
        manager._task_repo.get_by_message.assert_called_once_with("msg-1")
        assert bus.watch.await_count == 1
        kwargs = bus.watch.await_args.kwargs
        assert kwargs["source_task_id"] == "4242"
        follow_up = kwargs["follow_up"]
        assert isinstance(follow_up, FollowUp)
        assert follow_up.target_instance_id == "nudger-1"
        assert follow_up.source == "internal_agent:nudger-1"
        assert follow_up.metadata["kind"] == "nudge_complete"
        assert follow_up.metadata["child_id"] == "worker-1"
        assert follow_up.metadata["parent_id"] == "nudger-1"
        assert follow_up.metadata["message_id"] == "msg-1"

    async def test_the_injection_lane_keys_on_the_running_task(self):
        """The injection lane has no MessageQueue row — it keys on the
        worker's live RUNNING task."""
        bus = MagicMock()
        bus.watch = AsyncMock()
        manager = _manager_with_task_repo(_FakeTask(77))
        from daemon.services import dependency_bus as bus_mod

        original = bus_mod.get_dependency_bus
        bus_mod.get_dependency_bus = lambda: bus
        try:
            ok = await _register_nudge_completion_watcher(
                manager,
                nudger_instance_id="nudger-1",
                worker_instance_id="worker-1",
                message_id=None,
                lane="injection",
            )
        finally:
            bus_mod.get_dependency_bus = original

        assert ok is True
        manager._task_repo.find_running_by_instance.assert_called_once_with(
            "worker-1"
        )
        assert bus.watch.await_args.kwargs["source_task_id"] == "77"

    async def test_the_mint_is_fail_open(self):
        """A bus failure degrades to today's behaviour (no watcher) —
        it NEVER raises into the nudge."""
        bus = MagicMock()
        bus.watch = AsyncMock(side_effect=RuntimeError("bus down"))
        manager = _manager_with_task_repo(_FakeTask(1))
        from daemon.services import dependency_bus as bus_mod

        original = bus_mod.get_dependency_bus
        bus_mod.get_dependency_bus = lambda: bus
        try:
            ok = await _register_nudge_completion_watcher(
                manager,
                nudger_instance_id="nudger-1",
                worker_instance_id="worker-1",
                message_id="msg-1",
            )
        finally:
            bus_mod.get_dependency_bus = original

        assert ok is False

    async def test_a_missing_bus_is_fail_open(self):
        manager = _manager_with_task_repo(_FakeTask(1))
        from daemon.services import dependency_bus as bus_mod

        original = bus_mod.get_dependency_bus
        bus_mod.get_dependency_bus = lambda: None
        try:
            ok = await _register_nudge_completion_watcher(
                manager,
                nudger_instance_id="nudger-1",
                worker_instance_id="worker-1",
                message_id="msg-1",
            )
        finally:
            bus_mod.get_dependency_bus = original
        assert ok is False

    async def test_no_nudger_means_no_mint(self):
        """Nobody to wake → no watcher."""
        manager = _manager_with_task_repo(_FakeTask(1))
        ok = await _register_nudge_completion_watcher(
            manager,
            nudger_instance_id=None,
            worker_instance_id="worker-1",
            message_id="msg-1",
        )
        assert ok is False

    async def test_a_missing_task_means_no_mint(self):
        """No real task id → no watcher (a fake id would be cancelled
        by the orphan sweep)."""
        manager = _manager_with_task_repo(None)
        ok = await _register_nudge_completion_watcher(
            manager,
            nudger_instance_id="nudger-1",
            worker_instance_id="worker-1",
            message_id="msg-1",
        )
        assert ok is False

    async def test_a_missing_task_repo_is_fail_open(self):
        manager = MagicMock()
        manager._task_repo = None
        ok = await _register_nudge_completion_watcher(
            manager,
            nudger_instance_id="nudger-1",
            worker_instance_id="worker-1",
            message_id="msg-1",
        )
        assert ok is False


# ── Part 2 — the orphan-sweep pin (real bus + real DB) ──────────────────────

_TASK_TABLE_PROBE = "SELECT name FROM sqlite_master WHERE type='table' AND name='task'"


@pytest.fixture
def bus_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    with eng.begin() as conn:
        found = conn.execute(text(_TASK_TABLE_PROBE)).fetchone()
    assert found is not None, (
        "the task table must exist for the orphan sweep's IN-subquery"
    )
    yield eng
    eng.dispose()


def _insert_task(engine, instance_id: str, status: str = "pending") -> int:
    """Insert a REAL Task row and return its id.

    Uses the ORM model (the table is created by
    ``SQLModel.metadata.create_all``) so the row carries every
    NOT-NULL column the orphan sweep's IN-subquery needs.
    """
    from sqlmodel import Session

    from daemon.repositories.task.models import Task, TaskType

    with Session(engine) as session:
        task = Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=instance_id,
            status=status,
            created_at=datetime.now(timezone.utc),
            work_id=str(uuid.uuid4()),
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return int(task.id)


class TestOrphanSweepPin:
    """THE PIN the design names: "fake task ids cancelled ≤120s".

    A watcher minted on a made-up source task id is CANCELLED by the
    bus's orphan sweep (30s grace + 90s sweep interval ⇒ ≤120s in
    production). A watcher minted on a REAL task id survives. The mint
    MUST therefore resolve a real Task row — which is exactly what
    ``_register_nudge_completion_watcher`` does (message id → Task
    row, or the worker's live RUNNING task).
    """

    @pytest.mark.asyncio
    async def test_a_watcher_on_a_fake_task_id_is_cancelled(self, bus_engine):
        repo = DependencyWatcherRepository(bus_engine)
        bus = DependencyBus(repo)
        await bus.start()
        try:
            repo.insert(
                DependencyWatcher(
                    source_task_id="9999",  # NOT a real task id
                    target_instance_id="nudger-1",
                    follow_up_payload=FollowUp(
                        target_instance_id="nudger-1",
                        message="m",
                        source="internal_agent:nudger-1",
                        metadata={
                            "kind": "nudge_complete",
                            "child_id": "worker-1",
                            "parent_id": "nudger-1",
                            "message_id": "msg-1",
                        },
                    ).to_payload(),
                )
            )
            assert len(repo.fetch_pending_for_target("nudger-1")) == 1

            swept = await bus._sweep_orphan_watchers(min_watcher_age_seconds=0)
            assert swept == 1
            assert repo.fetch_pending_for_target("nudger-1") == []
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_a_watcher_on_a_real_task_id_survives_the_sweep(
        self, bus_engine
    ):
        repo = DependencyWatcherRepository(bus_engine)
        bus = DependencyBus(repo)
        await bus.start()
        try:
            real_task_id = _insert_task(bus_engine, "worker-1", "pending")
            repo.insert(
                DependencyWatcher(
                    source_task_id=str(real_task_id),
                    target_instance_id="nudger-1",
                    follow_up_payload=FollowUp(
                        target_instance_id="nudger-1",
                        message="m",
                        source="internal_agent:nudger-1",
                        metadata={
                            "kind": "nudge_complete",
                            "child_id": "worker-1",
                            "parent_id": "nudger-1",
                            "message_id": "msg-1",
                        },
                    ).to_payload(),
                )
            )
            swept = await bus._sweep_orphan_watchers(min_watcher_age_seconds=0)
            assert swept == 0
            assert len(repo.fetch_pending_for_target("nudger-1")) == 1
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_the_minted_watcher_is_not_orphaned(self, bus_engine):
        """The END-TO-END shape: the mint helper, against a REAL bus
        and a REAL Task row, produces a watcher that the orphan sweep
        does NOT cancel."""
        repo = DependencyWatcherRepository(bus_engine)
        bus = DependencyBus(repo)
        await bus.start()
        from daemon.services import dependency_bus as bus_mod

        original = bus_mod.get_dependency_bus
        bus_mod.get_dependency_bus = lambda: bus
        try:
            real_task_id = _insert_task(bus_engine, "worker-1", "pending")
            manager = MagicMock()
            manager._task_repo = MagicMock()
            manager._task_repo.get_by_message = MagicMock(
                return_value=_FakeTask(real_task_id)
            )
            ok = await _register_nudge_completion_watcher(
                manager,
                nudger_instance_id="nudger-1",
                worker_instance_id="worker-1",
                message_id="msg-1",
                lane="enqueue",
            )
            assert ok is True
            assert len(repo.fetch_pending_for_target("nudger-1")) == 1
            swept = await bus._sweep_orphan_watchers(min_watcher_age_seconds=0)
            assert swept == 0
            assert len(repo.fetch_pending_for_target("nudger-1")) == 1
        finally:
            bus_mod.get_dependency_bus = original
            await bus.stop()


# ── Part 4 — the tool-level wiring ──────────────────────────────────────────


class _NudgeManager:
    """The surface ``instance_nudge`` touches."""

    def __init__(self, task=None, enqueue_result=None):
        self._instance_repository = MagicMock()
        self._task_repo = MagicMock()
        self._task_repo.get_by_message = MagicMock(return_value=task)
        self._task_repo.find_running_by_instance = MagicMock(
            return_value=task
        )
        self._graph_tasks: dict = {}
        self._user_origin_windows: dict = {}
        self._inherited_provenance_windows: dict = {}
        self._user_origin_last_stamp: dict = {}
        self._question_manager = None
        self.engine = None  # no audit repo → no audit rows
        self.enqueued: list[tuple[str, str, str]] = []
        self.injected: list[tuple[str, str, str]] = []
        self._enqueue_result = enqueue_result

    async def get_instance(self, iid):
        return iid

    def find_near_instance(self, iid, max_distance=3):
        return []

    async def enqueue_message(self, instance_id, message, source="api", **kw):
        self.enqueued.append((instance_id, message, source))
        return self._enqueue_result

    def set_injection(self, instance_id, content, source=None, **kw):
        self.injected.append((instance_id, content, source))


class _EnqueueResult:
    def __init__(self, message_id: str) -> None:
        self.message_id = message_id


def _nudge_tool(manager):
    import daemon.tools.instance_ops as io

    tools = io.create_instance_ops_tools(
        manager, current_instance_id="nudger-1", agent_id="maintenancer"
    )
    return next(t for t in tools if t.name == "instance_nudge")


class TestNudgeToolWiring:
    async def test_the_nudge_mints_the_completion_watcher(self, monkeypatch):
        """The tool-level wiring pin: a real-act nudge calls the mint
        helper with the enqueued message id."""
        import daemon.tools.instance_ops as io

        calls: list[dict] = []

        async def _fake_mint(manager, **kwargs):
            calls.append(kwargs)
            return True

        monkeypatch.setattr(io, "_register_nudge_completion_watcher", _fake_mint)
        manager = _NudgeManager(
            task=_FakeTask(11),
            enqueue_result=_EnqueueResult("msg-42"),
        )
        out = await _nudge_tool(manager).ainvoke(
            {"id": "worker-1", "message": "please finish", "reason": "r",
             "dry_run": False}
        )
        assert '"delivered": true' in out.lower()
        assert len(calls) == 1
        assert calls[0]["nudger_instance_id"] == "nudger-1"
        assert calls[0]["worker_instance_id"] == "worker-1"
        assert calls[0]["message_id"] == "msg-42"
        assert calls[0]["lane"] == "enqueue"

    async def test_a_failed_mint_does_not_fail_the_nudge(self, monkeypatch):
        """FAIL-OPEN at the tool boundary: the nudge still succeeds."""
        import daemon.tools.instance_ops as io

        async def _fake_mint(manager, **kwargs):
            raise RuntimeError("mint exploded")

        monkeypatch.setattr(io, "_register_nudge_completion_watcher", _fake_mint)
        manager = _NudgeManager(
            task=_FakeTask(11),
            enqueue_result=_EnqueueResult("msg-42"),
        )
        out = await _nudge_tool(manager).ainvoke(
            {"id": "worker-1", "message": "please finish", "reason": "r",
             "dry_run": False}
        )
        assert "ERROR" not in out
        assert '"delivered": true' in out.lower()

    async def test_the_dry_run_never_mints(self, monkeypatch):
        """A preview writes no watcher."""
        import daemon.tools.instance_ops as io

        calls: list[dict] = []

        async def _fake_mint(manager, **kwargs):
            calls.append(kwargs)
            return True

        monkeypatch.setattr(io, "_register_nudge_completion_watcher", _fake_mint)
        manager = _NudgeManager(task=_FakeTask(11))
        out = await _nudge_tool(manager).ainvoke(
            {"id": "worker-1", "message": "please finish", "reason": "r"}
        )
        assert "NO mutation happened" in out
        assert calls == []


# ── Part 3 — the tree-scoped C-union ────────────────────────────────────────


def _watcher_row(target: str, child: str, state: str = "PENDING"):
    return DependencyWatcher(
        source_task_id="123",
        target_instance_id=target,
        follow_up_payload=FollowUp(
            target_instance_id=target,
            message="m",
            source=f"internal_agent:{target}",
            metadata={
                "kind": "nudge_complete",
                "child_id": child,
                "parent_id": target,
                "message_id": "msg-1",
            },
        ).to_payload(),
        state=state,
    )


class TestTreeScopedCUnion:
    def test_a_pending_watcher_in_the_tree_is_rendered(self):
        root = "root-id"
        child = "child-id"
        watcher_repo = MagicMock()
        watcher_repo.fetch_pending_for_targets.return_value = [
            _watcher_row(child, child)
        ]
        manager = MagicMock()
        manager.get_tree_ids_permanent.return_value = [root, child]
        manager._instance_repository = MagicMock()
        manager._instance_repository.get.return_value = None
        manager._watcher_repo = watcher_repo

        rows = make_tree_rows_provider(manager, root)()
        commissioned = [
            r for r in rows if r["status"] == C_TREE_COMMISSIONED_STATUS
        ]
        assert len(commissioned) == 1
        assert commissioned[0]["instance_id"] == child
        # The read is scoped to the tree.
        watcher_repo.fetch_pending_for_targets.assert_called_once_with(
            [root, child]
        )

    def test_a_watcher_outside_the_tree_is_not_rendered(self):
        """The union is TREE-SCOPED — a watcher targeting an instance
        outside the leader's tree is not this leader's business."""
        root = "root-id"
        watcher_repo = MagicMock()
        watcher_repo.fetch_pending_for_targets.return_value = []
        manager = MagicMock()
        manager.get_tree_ids_permanent.return_value = [root]
        manager._instance_repository = MagicMock()
        manager._instance_repository.get.return_value = None
        manager._watcher_repo = watcher_repo

        rows = make_tree_rows_provider(manager, root)()
        assert [
            r for r in rows if r["status"] == C_TREE_COMMISSIONED_STATUS
        ] == []

    def test_the_commissioned_rows_lead_the_bundle(self):
        """The bundle renders only the first BUNDLE_C_TREE_ROWS_MAX
        rows — the DECISIVE commissioned evidence must not be clipped
        out by a wide tree."""
        root = "root-id"
        children = [f"child-{i}" for i in range(30)]
        watcher_repo = MagicMock()
        watcher_repo.fetch_pending_for_targets.return_value = [
            _watcher_row(children[0], children[0])
        ]
        manager = MagicMock()
        manager.get_tree_ids_permanent.return_value = [root, *children]
        repo = MagicMock()
        repo.get.side_effect = lambda iid: MagicMock(
            instance_id=iid, status="running", agent_id="worker"
        )
        manager._instance_repository = repo
        manager._watcher_repo = watcher_repo

        rows = make_tree_rows_provider(manager, root)()
        assert rows[0]["status"] == C_TREE_COMMISSIONED_STATUS

    def test_the_provider_fails_open_to_empty_rows(self):
        """A watcher-read failure degrades to EMPTY rows — never to a
        wrong verdict."""
        root = "root-id"
        watcher_repo = MagicMock()
        watcher_repo.fetch_pending_for_targets.side_effect = RuntimeError(
            "boom"
        )
        manager = MagicMock()
        manager.get_tree_ids_permanent.return_value = [root]
        manager._instance_repository = MagicMock()
        manager._instance_repository.get.return_value = None
        manager._watcher_repo = watcher_repo

        rows = make_tree_rows_provider(manager, root)()
        assert rows == []

    def test_a_missing_watcher_repo_is_fail_open(self):
        root = "root-id"
        manager = MagicMock(spec=["get_tree_ids_permanent"])
        manager.get_tree_ids_permanent.return_value = [root]
        assert make_tree_rows_provider(manager, root)() == []

    def test_the_commissioned_status_is_not_completed(self):
        """The cross-resolution helper treats a ``completed`` status as
        "this child's report was delivered" — a commissioned row means
        the OPPOSITE, so the vocabularies must not collide."""
        assert C_TREE_COMMISSIONED_STATUS != "completed"
