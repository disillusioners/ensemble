"""wi-1fbee2ec — parts 3+4 pins: RDRS root-terminal revival suppression
+ the WC-orphan detector lane.

Pins covered:

* **(d) part-3 suppression pin** — terminal root + deferred obligation
  ⇒ NO revival (the 10:41 apparition prevented); the row is HELD
  DEFERRED (D3) with the dedicated ``suppressed_root_terminal``
  counter + a structured log — and the mutation counterfactual (root
  LIVE ⇒ revival proceeds) proves the check composes with, not
  fights, live trees (T1 funnel-top / send_message seams untouched —
  the no-regression leg lives in the canonical
  ``test_terminal_row_status_strand.py`` pack, run alongside).
* **(c) WC-orphan detector pin** — WC instance + TERMINAL parent
  (+ the pure-WC-grandchildren variant) ⇒ the detector FIRES on the
  sweep-side surface (``wc_orphan_detector`` lane) with structured
  counters/logs; mutation leg (lane disabled ⇒ no fire) and the
  live-authority / root-WC non-fires.
"""

import asyncio
import logging
import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.report_injection.models import ReportInjection
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.services.report_delivery_recovery import (
    ReportDeliveryRecoveryService,
)

ROOT = "aaaaaaaa-0000-4000-8000-000000000001"
MID = "aaaaaaaa-0000-4000-8000-000000000002"
WC_CHILD = "aaaaaaaa-0000-4000-8000-000000000003"
WC_GRANDCHILD = "aaaaaaaa-0000-4000-8000-000000000004"
LIVE_ROOT = "aaaaaaaa-0000-4000-8000-000000000005"
LIVE_CHILD = "aaaaaaaa-0000-4000-8000-000000000006"

TERMINAL = InstanceStatus.COMPLETED.value
WC = InstanceStatus.WAITING_CHILDREN.value
RUNNING = InstanceStatus.RUNNING.value


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path}/wc-pins.db",
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
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="worker",
                agent_dir="agents/worker",
                status=status,
                parent_id=parent_id,
                version=2,
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()


def insert_deferred(engine, *, parent_id: str, child_id: str) -> None:
    with Session(engine) as s:
        s.add(
            ReportInjection(
                parent_instance_id=parent_id,
                child_instance_id=child_id,
                child_message_id="child-msg-1",
                report_message_id=None,
                content="",
                state=ReportInjectionState_DEFERRED,
                deferred_reason="RESUME_ROUTER",
            )
        )
        s.commit()


from daemon.repositories.report_injection.models import (
    ReportInjectionState,
)
ReportInjectionState_DEFERRED = ReportInjectionState.DEFERRED.value


def make_service(engine, **kwargs):
    """RDRS service with real repos over the engine; the manager ref is
    a MagicMock whose revival coroutine the tests instrument."""
    manager = MagicMock()
    manager.engine = engine
    service = ReportDeliveryRecoveryService(
        task_repo=MagicMock(),
        report_injection_repo=ReportInjectionRepository(engine=engine),
        queue_repo=MagicMock(),
        instance_repo=SQLModelInstanceRepository(engine),
        manager_ref=manager,
        **kwargs,
    )
    return service


def make_row(engine, parent_id: str) -> MagicMock:
    """A DEFERRED-row stand-in shaped like the repo rows the orphan
    lane iterates (``find_deferred_for_parent_all`` returns ORM rows —
    we fetch a real one)."""
    with Session(engine) as s:
        row = s.exec(
            select(ReportInjection).where(
                ReportInjection.parent_instance_id == parent_id
            )
        ).first()
        # Detach-with-fields: the lane only reads injection_id /
        # child_instance_id / child_message_id / deferred_reason.
        return row


# ─────────────────────────────────────────────────────────────────────────────
# PART 3 — root-terminal revival suppression
# ─────────────────────────────────────────────────────────────────────────────


class TestRootTerminalSuppression:
    def _drive_orphan_row(self, service, engine, parent_id):
        from daemon.services.report_delivery_recovery import LaneResult

        result = LaneResult()
        row = make_row(engine, parent_id)
        assert row is not None
        service._recover_one_deferred_row(
            row, result=result, parent_not_terminal=False
        )
        return result

    def test_terminal_root_suppresses_revival_holds_deferred(
        self, engine, caplog,
    ):
        """THE pin-(d) suppression leg: recipient terminal AND its tree
        root terminal ⇒ NO revival (the apparition prevented); the row
        stays DEFERRED (D3 — held convergence record, never FAILED)."""
        # Tree: ROOT (terminal) → MID (terminal, the recipient).
        insert_instance(engine, ROOT, status=TERMINAL)
        insert_instance(engine, MID, status=TERMINAL, parent_id=ROOT)
        insert_deferred(engine, parent_id=MID, child_id="c-1")
        service = make_service(engine)
        # busy-check must pass (idle recipient).
        service._task_repo = MagicMock()
        service._task_repo.has_instance_busy = MagicMock(return_value=False)

        with caplog.at_level(logging.WARNING):
            result = self._drive_orphan_row(service, engine, MID)

        assert result.suppressed_root_terminal == 1
        assert result.orphan_disposition == 0
        assert result.recovered == 0
        # The revival seam was NEVER invoked.
        service._manager._revive_terminal_instance.assert_not_called()
        # D3: the row is HELD DEFERRED (not transitioned, not FAILED).
        with Session(engine) as s:
            row = s.exec(select(ReportInjection)).one()
            assert row.state == ReportInjectionState.DEFERRED.value
        assert any(
            "suppressed_root_terminal" in rec.message for rec in caplog.records
        )

    def test_live_root_lets_revival_proceed_composes_not_fights(
        self, engine,
    ):
        """The composition leg (pin (d) both directions): a LIVE root ⇒
        the suppression does NOT fire; the revival path proceeds (the
        root's obligation-hold inputs keep the mission open — RDRS
        revival under a live root is the DESIRED convergence)."""
        insert_instance(engine, LIVE_ROOT, status=RUNNING)
        insert_instance(
            engine, LIVE_CHILD, status=TERMINAL, parent_id=LIVE_ROOT
        )
        insert_deferred(engine, parent_id=LIVE_CHILD, child_id="c-2")
        service = make_service(engine)
        service._task_repo = MagicMock()
        service._task_repo.has_instance_busy = MagicMock(return_value=False)

        # Give the revival seam a working loop + an async stub.
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        revived = {"called": 0}

        async def _fake_revive(iid):
            revived["called"] += 1
            return True

        try:
            service._manager._revive_terminal_instance = _fake_revive
            service._get_event_loop = lambda: loop
            result = self._drive_orphan_row(service, engine, LIVE_CHILD)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()

        assert result.suppressed_root_terminal == 0
        assert revived["called"] == 1
        # transition_deferred_to_pending ran (guarded UPDATE).
        with Session(engine) as s:
            row = s.exec(select(ReportInjection)).one()
            assert row.state == ReportInjectionState.PENDING.value

    def test_try_revive_terminal_parent_returns_false_on_terminal_root(
        self, engine,
    ):
        """The in-method defense-in-depth layer: the check ALSO lives
        inside ``_try_revive_terminal_parent`` so any future caller
        inherits the suppression."""
        insert_instance(engine, ROOT, status=TERMINAL)
        insert_instance(engine, MID, status=TERMINAL, parent_id=ROOT)
        service = make_service(engine)
        assert service._try_revive_terminal_parent(MID) is False
        service._manager._revive_terminal_instance.assert_not_called()

    def test_ancestor_walk_failopen_on_broken_chain(self, engine):
        """A broken chain (missing row) fails OPEN to revival — the
        walk is a suppression filter, never a liveness authority."""
        service = make_service(engine)
        insert_instance(
            engine, MID, status=TERMINAL, parent_id="missing-parent"
        )
        terminal, root_id = service._ancestor_root_is_terminal(MID)
        assert terminal is False
        assert root_id is None

    def test_ancestor_walk_root_live_reads_not_terminal(self, engine):
        insert_instance(engine, LIVE_ROOT, status=RUNNING)
        insert_instance(
            engine, LIVE_CHILD, status=TERMINAL, parent_id=LIVE_ROOT
        )
        service = make_service(engine)
        terminal, root_id = service._ancestor_root_is_terminal(LIVE_CHILD)
        assert terminal is False
        assert root_id == LIVE_ROOT

    def test_lane_result_carries_the_new_counter(self):
        from daemon.services.report_delivery_recovery import LaneResult

        lr = LaneResult()
        d = lr.to_dict()
        assert "suppressed_root_terminal" in d
        assert d["suppressed_root_terminal"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# PART 4 — the WC-orphan detector lane
# ─────────────────────────────────────────────────────────────────────────────


class TestWcOrphanDetector:
    def test_direct_shape_fires(self, engine, caplog):
        """THE pin-(c) direct shape: WC instance + TERMINAL direct
        parent ⇒ the detector FIRES (counter + structured log)."""
        insert_instance(engine, ROOT, status=TERMINAL)
        insert_instance(engine, WC_CHILD, status=WC, parent_id=ROOT)
        service = make_service(engine)
        with caplog.at_level(logging.WARNING):
            out = service._run_wc_orphan_detector_lane()
        assert out.wc_orphan_direct == 1
        assert out.wc_orphan_pure_chain == 0
        assert any(
            "wc_orphan_detector FIRE (direct)" in rec.message
            for rec in caplog.records
        )

    def test_pure_wc_grandchildren_subshape_fires(self, engine, caplog):
        """THE pin-(c) pure-WC-grandchildren variant: WC grandchild
        under a WC parent under a TERMINAL root ⇒ fires as
        pure_chain (the double-blind shape no other lane sees)."""
        insert_instance(engine, ROOT, status=TERMINAL)
        insert_instance(engine, MID, status=WC, parent_id=ROOT)
        insert_instance(
            engine, WC_GRANDCHILD, status=WC, parent_id=MID
        )
        service = make_service(engine)
        with caplog.at_level(logging.WARNING):
            out = service._run_wc_orphan_detector_lane()
        # The GRANDCHILD fires as pure_chain; note the MID row is ALSO
        # itself a direct shape (WC under a TERMINAL direct parent) and
        # correctly fires direct — one row, one classification.
        assert out.wc_orphan_pure_chain == 1
        assert out.wc_orphan_direct == 1
        assert any(
            "wc_orphan_detector FIRE (pure_chain)" in rec.message
            for rec in caplog.records
        )

    def test_live_authority_does_not_fire(self, engine):
        """A WC child under a LIVE root is NORMAL waiting state — no
        fire (R6 false-positive guard)."""
        insert_instance(engine, LIVE_ROOT, status=RUNNING)
        insert_instance(engine, WC_CHILD, status=WC, parent_id=LIVE_ROOT)
        service = make_service(engine)
        out = service._run_wc_orphan_detector_lane()
        assert out.wc_orphan_direct == 0
        assert out.wc_orphan_pure_chain == 0
        assert out.skipped_live_authority == 1

    def test_root_wc_itself_is_healer_territory_not_fired(self, engine):
        """A WC ROOT is the strand healer's roots-only territory — the
        detector deliberately excludes it (D2: healer widening
        rejected)."""
        insert_instance(engine, ROOT, status=WC)  # a ROOT in WC
        insert_instance(engine, WC_CHILD, status=WC, parent_id=ROOT)
        service = make_service(engine)
        out = service._run_wc_orphan_detector_lane()
        assert out.skipped_root_wc == 1
        assert out.wc_orphan_direct == 0
        assert out.wc_orphan_pure_chain == 0

    def test_detector_is_detection_only_no_state_writes(self, engine):
        """The lane NEVER acts: running it leaves every instance row's
        status untouched (observe → fire only)."""
        insert_instance(engine, ROOT, status=TERMINAL)
        insert_instance(engine, WC_CHILD, status=WC, parent_id=ROOT)
        service = make_service(engine)
        service._run_wc_orphan_detector_lane()
        with Session(engine) as s:
            assert (
                s.get(Instance, WC_CHILD).status
                == InstanceStatus.WAITING_CHILDREN.value
            )
            assert s.get(Instance, ROOT).status == TERMINAL

    def test_kill_switch_disables_the_lane(self, engine):
        """The per-lane kill-switch: lane_wc_orphan_detector=False ⇒ the
        lane is absent from the sweep (the mutation leg's structural
        equivalent)."""
        insert_instance(engine, ROOT, status=TERMINAL)
        insert_instance(engine, WC_CHILD, status=WC, parent_id=ROOT)
        service_off = make_service(engine, lane_wc_orphan_detector=False)
        sweep = service_off._run_all_lanes_sync()
        assert "wc_orphan_detector" not in sweep.lanes
        # …and ON by default.
        service_on = make_service(engine)
        sweep_on = service_on._run_all_lanes_sync()
        assert "wc_orphan_detector" in sweep_on.lanes

    def test_batch_cap_bounds_the_feed(self, engine):
        """The MVP growth rule: the candidate feed is capped by the
        lane's batch cap."""
        insert_instance(engine, ROOT, status=TERMINAL)
        for i in range(7):
            insert_instance(
                engine,
                f"bbbbbbbb-0000-4000-8000-{i:012d}",
                status=WC,
                parent_id=ROOT,
            )
        service = make_service(engine, batch_cap=3)
        out = service._run_wc_orphan_detector_lane()
        assert out.wc_orphan_direct == 3

    def test_classifier_shapes_directly(self, engine):
        insert_instance(engine, ROOT, status=TERMINAL)
        insert_instance(engine, MID, status=WC, parent_id=ROOT)
        insert_instance(engine, WC_GRANDCHILD, status=WC, parent_id=MID)
        insert_instance(engine, LIVE_ROOT, status=RUNNING)
        insert_instance(engine, WC_CHILD, status=WC, parent_id=LIVE_ROOT)
        service = make_service(engine)
        assert service._classify_wc_orphan(MID)[0] == "direct"
        assert service._classify_wc_orphan(WC_GRANDCHILD)[0] == "pure_chain"
        assert (
            service._classify_wc_orphan(WC_CHILD)[0] == "live_authority"
        )
