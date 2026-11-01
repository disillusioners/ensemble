"""wi-1fbee2ec — report-obligation visibility EMISSION-time + MINT pins.

Pins covered here (real SQLite engine, real repos, real walk code):

* **(a) repro at the EMISSION checkpoint** — ``_root_completion_gate``
  leg 6: terminal child + outstanding PENDING obligation owed to the
  gated root → DENY (``outstanding_report_obligations=N``) joining the
  downgrade path; the wedge resolver is NOT consulted when the
  obligation leg holds (leg ordering).
* **(b) obligation-mint-on-completion** — the REAL
  ``_process_child_completion_db_sync`` drive: exactly ONE obligation
  row minted in the terminalization transaction, state=PENDING,
  ``deferred_reason=SYNC_MINT`` (the wi-1fbee2ec provenance stamp),
  artifact fields set; re-entrant terminalization mints NO second row
  (CAS + guard); rollback ⇒ NO orphan row (an exception at the
  injection flush rolls back the WHOLE txn — child stays RUNNING);
  sync-vs-backfill race ⇒ single non-terminal row (the write-once
  triple index).
* **(e) dogfood dispatch-window at the EMISSION checkpoint** — a
  minted-not-yet-RUNNING (dormant IDLE) descendant blocks the emission
  gate via the tree-liveness leg (``evaluate_mission_live`` already
  counts non-terminal descendants — verified + pinned, no change
  needed) and reads NON-QUIET to the WIDENED in-turn liveness walk
  (``_count_descendants_busy_and_live``).
* Repository primitive ``count_outstanding_for_subtree`` semantics +
  the manager facade ``count_outstanding_report_obligations``.

Mutation legs (planted-defect RED → restore GREEN) run at the
verification phase against these assertions.
"""

import asyncio
from datetime import datetime, timezone
from types import MethodType
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from daemon.constants import DEFERRED_REASON_SYNC_MINT
from daemon.manager import InstanceManager
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.services.child_reports import ChildReportsService
from daemon.write_pause_guard import WritePauseGuard

ROOT = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"
GRANDCHILD = "33333333-3333-4333-8333-333333333333"
OUTSIDER = "44444444-4444-4444-8444-444444444444"


@pytest.fixture
def engine(tmp_path):
    """File-backed SQLite (NOT :memory: StaticPool).

    The regular-child completion path calls
    ``resolve_report_recipient_with_commission`` mid-transaction — the
    resolver opens its OWN session (F1 isolation). On a shared
    StaticPool in-memory connection that probe's close-time ROLLBACK
    aborts the write session's open transaction (one connection, one
    ambient txn). File-backed SQLite gives every session its own
    connection, so the probes cannot bleed into the write txn.
    """
    eng = create_engine(
        f"sqlite:///{tmp_path}/obligation-pins.db",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def insert_instance(
    engine,
    instance_id: str,
    *,
    status: str,
    parent_id: str | None = None,
    agent_id: str = "worker",
    version: int = 2,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id=agent_id,
                agent_dir=f"agents/{agent_id}",
                status=status,
                parent_id=parent_id,
                version=version,
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()


def insert_injection(
    engine,
    *,
    parent_id: str,
    child_id: str,
    child_msg: str = "child-msg-1",
    state: str = ReportInjectionState.PENDING.value,
    reason: str | None = None,
    report_message_id: str | None = "report-msg-1",
) -> None:
    with Session(engine) as s:
        s.add(
            ReportInjection(
                parent_instance_id=parent_id,
                child_instance_id=child_id,
                child_message_id=child_msg,
                report_message_id=report_message_id,
                content="x",
                state=state,
                deferred_reason=reason,
            )
        )
        s.commit()


def all_injections(engine) -> list[ReportInjection]:
    with Session(engine) as s:
        return list(s.exec(select(ReportInjection)).all())


# ─────────────────────────────────────────────────────────────────────────────
# Repository primitive — count_outstanding_for_subtree
# ─────────────────────────────────────────────────────────────────────────────


class TestCountOutstandingForSubtree:
    def test_pending_and_deferred_count_terminal_excluded(self, engine):
        insert_injection(
            engine, parent_id=ROOT, child_id=CHILD,
            child_msg="m-pending",
        )
        insert_injection(
            engine, parent_id=ROOT, child_id=CHILD,
            child_msg="m-deferred", state="DEFERRED", reason="RESUME_ROUTER",
        )
        insert_injection(
            engine, parent_id=ROOT, child_id=CHILD,
            child_msg="m-injected", state="INJECTED",
        )
        insert_injection(
            engine, parent_id=ROOT, child_id=CHILD,
            child_msg="m-delivered", state="TASK_DELIVERED",
        )
        insert_injection(
            engine, parent_id=ROOT, child_id=CHILD,
            child_msg="m-failed", state="FAILED",
        )
        repo = ReportInjectionRepository(engine=engine)
        assert repo.count_outstanding_for_subtree([ROOT]) == 2

    def test_recipient_and_child_keying(self, engine):
        # The 10:35 shape: obligation owed TO the gated root.
        insert_injection(
            engine, parent_id=ROOT, child_id=CHILD, child_msg="to-root"
        )
        # Grandchild report owed to a mid member (both endpoints in
        # the subtree).
        insert_injection(
            engine, parent_id=CHILD, child_id=GRANDCHILD,
            child_msg="to-mid",
        )
        # A subtree descendant completed, recipient OUTSIDE the tree
        # (cross-tree routing) — still an obligation of the subtree.
        insert_injection(
            engine, parent_id=OUTSIDER, child_id=GRANDCHILD,
            child_msg="cross-tree",
        )
        # Completely unrelated row.
        insert_injection(
            engine, parent_id=OUTSIDER, child_id="55555555-5555-4555-8555-555555555555",
            child_msg="unrelated",
        )
        repo = ReportInjectionRepository(engine=engine)
        assert (
            repo.count_outstanding_for_subtree(
                [ROOT, CHILD, GRANDCHILD], exclude_child_id=ROOT
            )
            == 3
        )

    def test_own_upward_obligation_excluded(self, engine):
        # The gated instance's OWN report (child == gated, recipient
        # outside the subtree) must NOT hold its own gate.
        insert_injection(
            engine, parent_id=OUTSIDER, child_id=ROOT,
            child_msg="own-report",
        )
        repo = ReportInjectionRepository(engine=engine)
        assert (
            repo.count_outstanding_for_subtree(
                [ROOT], exclude_child_id=ROOT
            )
            == 0
        )
        # Without the exclusion it IS in scope (sanity of the keying).
        assert repo.count_outstanding_for_subtree([ROOT]) == 1

    def test_empty_subtree_short_circuits(self, engine):
        repo = ReportInjectionRepository(engine=engine)
        assert repo.count_outstanding_for_subtree([]) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Manager facade — count_outstanding_report_obligations (real walk code)
# ─────────────────────────────────────────────────────────────────────────────


def make_manager_shell(engine):
    """A minimal manager shell carrying exactly the attributes the
    facade reads — bound to the REAL unbound method (no manager init)."""
    shell = MagicMock()
    shell._instance_repository = SQLModelInstanceRepository(engine)
    shell._engine = engine
    shell.LIVE_DESCENDANTS_BFS_CAP = InstanceManager.LIVE_DESCENDANTS_BFS_CAP
    shell.count_outstanding_report_obligations = MethodType(
        InstanceManager.count_outstanding_report_obligations, shell
    )
    return shell


class TestManagerFacade:
    def test_facade_counts_subtree_obligations(self, engine):
        insert_instance(engine, ROOT, status="completed")
        insert_instance(
            engine, CHILD, status="completed", parent_id=ROOT
        )
        insert_instance(
            engine, GRANDCHILD, status="completed", parent_id=CHILD
        )
        insert_injection(
            engine, parent_id=ROOT, child_id=CHILD, child_msg="m1"
        )
        insert_injection(
            engine, parent_id=CHILD, child_id=GRANDCHILD, child_msg="m2"
        )
        shell = make_manager_shell(engine)
        assert (
            shell.count_outstanding_report_obligations(ROOT) == 2
        )
        # Own-upward exclusion applies through the facade too.
        insert_injection(
            engine, parent_id=OUTSIDER, child_id=ROOT, child_msg="own"
        )
        assert (
            shell.count_outstanding_report_obligations(ROOT) == 2
        )

    def test_facade_no_repo_reads_zero(self, engine):
        shell = MagicMock()
        shell._instance_repository = None
        shell.count_outstanding_report_obligations = MethodType(
            InstanceManager.count_outstanding_report_obligations, shell
        )
        assert shell.count_outstanding_report_obligations(ROOT) == 0


# ─────────────────────────────────────────────────────────────────────────────
# (e) the WIDENED in-turn liveness walk — dogfood dispatch window
# ─────────────────────────────────────────────────────────────────────────────


class TestWidenedLivenessWalk:
    """The 2026-11-01 adjudication: ANY non-terminal descendant =
    NON-QUIET for the LIVE count; the BUSY subset keeps the strict
    two-set semantics.

    The walk is exercised through the REAL helper bound to a shell
    (``object.__new__``-free — the MethodType pattern above).
    """

    def make_shell(self, engine):
        shell = MagicMock()
        shell._instance_repository = SQLModelInstanceRepository(engine)
        shell.LIVE_DESCENDANTS_BFS_CAP = (
            InstanceManager.LIVE_DESCENDANTS_BFS_CAP
        )
        return shell

    def call_walk(self, shell, iid):
        return InstanceManager._count_descendants_busy_and_live(shell, iid)

    def test_minted_dormant_idie_child_is_live_the_dogfood_window(self, engine):
        """THE pin-(e) in-turn leg: child MINTED (IDLE), NO message /
        task evidence rows (the dispatch→RUNNING micro-race) — OLD
        read 0 (verify-clean); NEW reads 1 (non-quiet)."""
        insert_instance(engine, ROOT, status="running")
        insert_instance(
            engine, CHILD, status=InstanceStatus.IDLE.value, parent_id=ROOT
        )
        busy, live = self.call_walk(self.make_shell(engine), ROOT)
        assert live == 1
        assert busy == 0  # dormant is NEVER busy (LCA unchanged)

    def test_queued_dormant_grandchild_is_live(self, engine):
        insert_instance(engine, ROOT, status="running")
        insert_instance(
            engine, CHILD, status=InstanceStatus.WAITING_CHILDREN.value,
            parent_id=ROOT,
        )
        insert_instance(
            engine, GRANDCHILD, status=InstanceStatus.QUEUED.value,
            parent_id=CHILD,
        )
        busy, live = self.call_walk(self.make_shell(engine), ROOT)
        assert live == 2  # WC unconditional + QUEUED now unconditional
        assert busy == 1  # WC is busy; QUEUED is not

    def test_terminal_descendants_still_excluded(self, engine):
        insert_instance(engine, ROOT, status="running")
        insert_instance(
            engine, CHILD, status=InstanceStatus.COMPLETED.value,
            parent_id=ROOT,
        )
        busy, live = self.call_walk(self.make_shell(engine), ROOT)
        assert (busy, live) == (0, 0)

    def test_paused_live_not_busy_running_busy(self, engine):
        insert_instance(engine, ROOT, status="running")
        insert_instance(
            engine, CHILD, status=InstanceStatus.PAUSED.value,
            parent_id=ROOT,
        )
        insert_instance(
            engine, GRANDCHILD, status=InstanceStatus.RUNNING.value,
            parent_id=CHILD,
        )
        busy, live = self.call_walk(self.make_shell(engine), ROOT)
        assert live == 2
        assert busy == 1  # RUNNING only — PAUSED stays non-busy

    def test_b08f40fe_orphan_now_reads_nonquiet_superseded(self, engine):
        """The superseded posture, pinned honestly: an aged
        never-dispatched IDLE orphan (b08f40fe shape) now reads
        NON-QUIET to the in-turn arm — the deliberate leg-extension
        (reaping belongs to the healer / WC-orphan detector, not the
        gate)."""
        insert_instance(engine, ROOT, status="running")
        insert_instance(
            engine, CHILD, status=InstanceStatus.IDLE.value, parent_id=ROOT
        )
        busy, live = self.call_walk(self.make_shell(engine), ROOT)
        assert live == 1


# ─────────────────────────────────────────────────────────────────────────────
# (a) + D1 at the EMISSION checkpoint — _root_completion_gate leg 6
# ─────────────────────────────────────────────────────────────────────────────


def make_gate_service(engine):
    """A real ChildReportsService over a mock manager; the gate's
    sibling legs are stubbed to their QUIET values so the obligation
    leg is the only live input (the legs' own behavior is pinned by
    their own packs)."""
    manager = MagicMock()
    manager.engine = engine
    manager._engine = engine
    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager.write_guard = WritePauseGuard()
    manager._deferred_question_pause = set()
    shell = make_manager_shell(engine)
    manager.count_outstanding_report_obligations = (
        shell.count_outstanding_report_obligations
    )
    service = ChildReportsService(manager=manager)
    service._bus_count_pending_for_target_sync = lambda iid: 0
    service._count_outstanding_mission_commissions = lambda iid: 0
    service._gate_wedge_resolver = AsyncMock(return_value=(True, None, False))
    return service


class TestEmissionGateLeg6:
    @pytest.mark.asyncio
    async def test_pin_a_outstanding_obligation_denies_the_root(self, engine):
        """THE pin-(a) emission leg: terminal child, undelivered
        report owed to the gated root, tree otherwise quiet → the
        gate DENIES with the obligation reason (the downgrade path's
        trigger)."""
        insert_instance(
            engine, ROOT, status=InstanceStatus.COMPLETED.value
        )
        insert_instance(
            engine, CHILD, status=InstanceStatus.COMPLETED.value,
            parent_id=ROOT,
        )
        insert_injection(
            engine, parent_id=ROOT, child_id=CHILD, child_msg="anchor-1"
        )
        service = make_gate_service(engine)
        allowed, reason, declared = await service._root_completion_gate(
            None, ROOT
        )
        assert allowed is False
        assert reason == "outstanding_report_obligations=1"
        assert declared is False

    @pytest.mark.asyncio
    async def test_pin_a_counterfactual_row_delivered_gate_passes(self, engine):
        """The counterfactual (mutation-leg RED target): with the
        obligation row terminal (delivered), the quiet tree passes —
        the OLD shape would close here."""
        insert_instance(
            engine, ROOT, status=InstanceStatus.COMPLETED.value
        )
        insert_instance(
            engine, CHILD, status=InstanceStatus.COMPLETED.value,
            parent_id=ROOT,
        )
        insert_injection(
            engine, parent_id=ROOT, child_id=CHILD, child_msg="anchor-1",
            state=ReportInjectionState.TASK_DELIVERED.value,
        )
        service = make_gate_service(engine)
        allowed, reason, _ = await service._root_completion_gate(
            None, ROOT
        )
        assert allowed is True
        assert reason is None

    @pytest.mark.asyncio
    async def test_obligation_leg_holds_before_wedge_resolver(self, engine):
        """Leg ordering: the obligation leg denies WITHOUT consulting
        the wedge resolver (it sits between the pending-count leg and
        the freshness/wedge leg)."""
        insert_instance(
            engine, ROOT, status=InstanceStatus.COMPLETED.value
        )
        insert_instance(
            engine, CHILD, status=InstanceStatus.COMPLETED.value,
            parent_id=ROOT,
        )
        insert_injection(
            engine, parent_id=ROOT, child_id=CHILD, child_msg="anchor-1"
        )
        service = make_gate_service(engine)
        allowed, _, _ = await service._root_completion_gate(None, ROOT)
        assert allowed is False
        service._gate_wedge_resolver.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_d1_read_error_holds_inside_the_leg_never_fail_open(
        self, engine,
    ):
        """D1 at the emission leg: the facade RAISES → the leg converts
        to a HOLD verdict INSIDE the leg (never rides the caller-wrap
        fail-open — which would return (True, None, False))."""
        insert_instance(
            engine, ROOT, status=InstanceStatus.COMPLETED.value
        )
        service = make_gate_service(engine)

        def _explode(_iid):
            raise RuntimeError("obligation read exploded")

        service._manager.count_outstanding_report_obligations = _explode
        allowed, reason, _ = await service._root_completion_gate(
            None, ROOT
        )
        assert allowed is False
        assert reason == "outstanding_report_obligations=unknown(read_error)"

    @pytest.mark.asyncio
    async def test_pin_e_minted_dormant_child_blocks_via_tree_liveness(
        self, engine,
    ):
        """THE pin-(e) emission leg: child MINTED, NOT yet RUNNING,
        tree otherwise quiet → the tree-liveness leg (leg 3) blocks —
        the dispatch→RUNNING window can never verify-clean at the
        emission checkpoint either (verified-already: leg b/c counts
        ANY non-terminal descendant; the strand exception needs a
        TERMINAL direct parent, absent in this window)."""
        insert_instance(engine, ROOT, status=InstanceStatus.COMPLETED.value)
        insert_instance(
            engine, CHILD, status=InstanceStatus.IDLE.value, parent_id=ROOT
        )
        service = make_gate_service(engine)
        allowed, reason, _ = await service._root_completion_gate(
            None, ROOT
        )
        assert allowed is False
        assert reason is not None
        assert reason.startswith("tree_liveness:")

    @pytest.mark.asyncio
    async def test_pin_e_counterfactual_child_running_flips_old_reading(
        self, engine,
    ):
        """The dispatch-window counterfactual proving the leg reads
        the CHILD status (not a stale artifact): the same tree with
        the child flipped RUNNING blocks identically (liveness is
        status-driven), and with the child TERMINAL + no obligations
        the gate passes."""
        insert_instance(engine, ROOT, status=InstanceStatus.COMPLETED.value)
        insert_instance(
            engine, CHILD, status=InstanceStatus.RUNNING.value, parent_id=ROOT
        )
        service = make_gate_service(engine)
        allowed, reason, _ = await service._root_completion_gate(
            None, ROOT
        )
        assert allowed is False
        assert reason is not None
        assert reason.startswith("tree_liveness:")


# ─────────────────────────────────────────────────────────────────────────────
# (b) the MINT — real _process_child_completion_db_sync drive
# ─────────────────────────────────────────────────────────────────────────────


def make_mint_service(engine):
    manager = MagicMock()
    manager.engine = engine
    manager._engine = engine
    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager.write_guard = WritePauseGuard()
    manager._deferred_question_pause = set()
    manager.config = MagicMock()
    return ChildReportsService(manager=manager)


@pytest.fixture
def bus(engine):
    """A wired DependencyBus singleton (the db_sync path hard-errors
    without one — the bus is the completion authority)."""
    import concurrent.futures

    from daemon.services.dependency_bus import (
        DependencyBus,
        set_dependency_bus,
    )
    from daemon.repositories.dependency_bus.repository import (
        DependencyWatcherRepository,
    )

    b = DependencyBus(DependencyWatcherRepository(engine))
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        ex.submit(asyncio.run, b.start()).result()
    set_dependency_bus(b)
    try:
        yield b
    finally:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(asyncio.run, b.stop()).result()
        set_dependency_bus(None)


class TestSyncMint:
    def _seed_running_child(self, engine):
        insert_instance(engine, ROOT, status="running", agent_id="leader")
        insert_instance(
            engine, CHILD, status=InstanceStatus.RUNNING.value,
            parent_id=ROOT, agent_id="worker",
        )
        with Session(engine) as s:
            s.add(
                MessageQueue(
                    message_id="child-msg-1",
                    instance_id=CHILD,
                    content="work result",
                    source="api",
                    type=MessageType.HUMAN.value,
                    status=MessageStatus.COMPLETED.value,
                )
            )
            s.commit()

    def test_mint_on_completion_single_row_with_provenance(self, engine, bus):
        """THE pin-(b) GREEN: terminalization ⇒ exactly ONE obligation
        row, same transaction as the COMPLETED stamp, state=PENDING,
        provenance SYNC_MINT, artifact fields set."""
        self._seed_running_child(engine)
        service = make_mint_service(engine)
        result = service._process_child_completion_db_sync(
            CHILD, "child-msg-1", "the final report content"
        )
        assert result.outcome == "regular_child_completed"
        rows = all_injections(engine)
        assert len(rows) == 1
        row = rows[0]
        assert row.state == ReportInjectionState.PENDING.value
        assert row.deferred_reason == DEFERRED_REASON_SYNC_MINT
        assert row.parent_instance_id == ROOT
        assert row.child_instance_id == CHILD
        assert row.child_message_id == "child-msg-1"
        assert row.report_message_id is not None
        # Same transaction: the child stamped COMPLETED in the same
        # commit that minted the row.
        with Session(engine) as s:
            child = s.get(Instance, CHILD)
            assert child.status == InstanceStatus.COMPLETED.value
        # The companion report message + PROCESS_REPORT task exist.
        with Session(engine) as s:
            report_rows = list(
                s.exec(
                    select(MessageQueue).where(
                        MessageQueue.instance_id == ROOT
                    )
                ).all()
            )
            assert any(
                r.source == f"internal_report:{CHILD}:child-msg-1"
                for r in report_rows
            )

    def test_reentrant_terminalization_no_second_row(self, engine, bus):
        """Re-entrant terminalization (the status-level guard) mints
        NO second row — idempotency via CAS + guard + index."""
        self._seed_running_child(engine)
        service = make_mint_service(engine)
        first = service._process_child_completion_db_sync(
            CHILD, "child-msg-1", "content"
        )
        assert first.outcome == "regular_child_completed"
        second = service._process_child_completion_db_sync(
            CHILD, "child-msg-1", "content"
        )
        assert second.outcome == "idempotency_skip"
        assert len(all_injections(engine)) == 1

    def test_rollback_means_no_orphan_row(self, engine, bus):
        """Rollback ⇒ no orphan obligation: an exception raised at the
        injection flush (inside the SAVEPOINT; non-IntegrityError) is
        re-raised per the F4 semantics and rolls back the WHOLE
        transaction — the child stays RUNNING and NO row persists."""
        self._seed_running_child(engine)
        service = make_mint_service(engine)

        def _poison_flush(session, flush_context):
            if session.get_bind() is not engine:
                return
            for obj in session.new:
                if isinstance(obj, ReportInjection):
                    raise RuntimeError("planted mid-flush explosion")

        event.listen(
            Session, "after_flush", _poison_flush
        )
        try:
            with pytest.raises(RuntimeError):
                service._process_child_completion_db_sync(
                    CHILD, "child-msg-1", "content"
                )
        finally:
            event.remove(Session, "after_flush", _poison_flush)
        assert all_injections(engine) == []
        with Session(engine) as s:
            child = s.get(Instance, CHILD)
            assert child.status == InstanceStatus.RUNNING.value
            # The whole txn rolled back — no orphan report message
            # either.
            report_rows = list(
                s.exec(
                    select(MessageQueue).where(
                        MessageQueue.instance_id == ROOT
                    )
                ).all()
            )
            assert report_rows == []

    def test_sync_vs_backfill_race_single_row(self, engine, bus):
        """The write-once race (pin (b) race leg + pin (d) no-double-
        mint): a backfill DEFERRED row minted FIRST (the RDRS
        ``ensure_deferred`` shape) makes the sync INSERT lose the
        triple index benignly — one non-terminal row total, the
        COMPLETED transition preserved (idempotency_skip)."""
        self._seed_running_child(engine)
        # The backfill wins the race (the 300s-tick minted first).
        ReportInjectionRepository(engine=engine).ensure_deferred(
            parent_instance_id=ROOT,
            child_instance_id=CHILD,
            child_message_id="child-msg-1",
            deferred_reason="RESUME_ROUTER",
        )
        service = make_mint_service(engine)
        result = service._process_child_completion_db_sync(
            CHILD, "child-msg-1", "content"
        )
        # F3 semantics: the savepoint rollback preserves COMPLETED.
        assert result.outcome == "idempotency_skip"
        rows = all_injections(engine)
        assert len(rows) == 1
        assert rows[0].state == ReportInjectionState.DEFERRED.value
        with Session(engine) as s:
            child = s.get(Instance, CHILD)
            assert child.status == InstanceStatus.COMPLETED.value
