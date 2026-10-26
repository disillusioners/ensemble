"""Unit tests — MAJOR-1 = ruling (c) CROSS-EPOCH HOLD (Proposal B PR3).

The live-captured orphan scenario (PR2 runtime-gate item 4, scratch
``ensemble_mrpr2_maj1c_5222``): a ``job_continue`` continuation mints a
NEW epoch and SUPERSEDES the anchor's prior open epoch — the prior
epoch's commission rows are ORPHANED (they keep
``state='outstanding'`` and keep pointing at the superseded epoch;
nothing transitions them). Under the PR2 open-epoch-scoped read the
orphan was invisible: the gate read a fake quiescence while the
commissioned work was still running.

Ruling (c) — CROSS-EPOCH HOLD: NO re-pointing, NO auto-supersede of the
orphaned commissions. The outstanding-commission count is ANCHOR-scoped
(open AND superseded epochs), wired into BOTH the L3 publish hold and
the R2 quiet predicate: publish/quiet only when EVERY epoch of the
anchor is commission-quiet. The fail-open ``-1`` sentinel contract is
preserved exactly (never a 0 default).

The attestation CENSUS deliberately STAYS OPEN-EPOCH-SCOPED (the scope
ruling names the L3 hold + the R2 quiet input only) — pinned here so
the scope decision is visible, not silently widened.

PR2 caveat (e) is respected: the fixture seeds a LIVE non-terminal
descendant so the test proves the MISSION leg produces the hold (the
mission leg precedes the tree-liveness leg in
``_root_completion_gate``) — the hold reason is
``mission_commissions=N``, not a liveness deferral.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session, select

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.mission_record.models import MissionCommission
from daemon.repositories.mission_record.repository import (
    MissionRecordRepository,
)
from daemon.services.attestation_gate import (
    DEFAULT_GATE_SETTINGS,
    Decision,
    evaluate,
)
from daemon.services.child_reports import ChildReportsService
from daemon.services.mission_record_reads import (
    MISSION_READ_UNKNOWN,
    count_outstanding_commissions,
    read_mission_census,
)
from daemon.services.mission_record_service import (
    MISSION_RECORDS_MODE_ENV,
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
)

ANCHOR = "11111111-1111-4111-8111-111111111111"
COMMISSIONER = "33333333-3333-4333-8333-333333333333"
WORKER = "22222222-2222-4222-8222-222222222222"
LIVE_CHILD = "44444444-4444-4444-8444-444444444444"


@pytest.fixture(autouse=True)
def _reset_mode_cache():
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'pr3-cross-epoch.sqlite'}",
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
    yield eng
    eng.dispose()


@pytest.fixture
def enforce_mode(monkeypatch):
    monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "enforce")
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


def _seed_instance(engine, iid: str, status: str = "running", parent=None):
    with Session(engine) as session:
        if session.get(Instance, iid) is None:
            session.add(
                Instance(
                    instance_id=iid,
                    agent_id="leader",
                    agent_dir=f"agents/leader/{iid[:8]}",
                    status=status,
                    parent_id=parent,
                    project_id=None,
                )
            )
            session.commit()
    return iid


def _orphan_scenario(engine) -> MissionRecordService:
    """The live-captured MAJOR-1 chain, step by step.

    epoch 1 (open) mints a commission → ``job_continue`` continuation
    mints epoch 2 and SUPERSEDES epoch 1 → the commission is orphaned
    outstanding on the (now superseded) epoch 1.
    """
    _seed_instance(engine, ANCHOR)
    _seed_instance(engine, WORKER)
    _seed_instance(engine, COMMISSIONER)
    service = MissionRecordService(engine)
    epoch1 = service.resolve_or_mint(ANCHOR, "user_api")
    service.mint_commission(
        epoch1.mission_id,
        commissioner_instance_id=COMMISSIONER,
        worker_instance_id=WORKER,
        lane="send_message",
    )
    # The job_continue continuation: a NEW epoch; the prior open epoch
    # is superseded RECORD-SIDE (the F1 ordering: supersede → insert).
    service.mint_continuation_epoch(ANCHOR)
    repo = MissionRecordRepository(engine)
    superseded = repo.find_open_epoch_by_anchor(ANCHOR)
    assert superseded is not None and superseded.mission_id != epoch1.mission_id
    epoch1_row = repo.get_mission(epoch1.mission_id)
    assert epoch1_row.status == "superseded"
    return service


def _child_reports(engine):
    manager = MagicMock()
    manager.engine = engine
    manager._engine = engine
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._checkpointer = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(
        return_value=MagicMock(agent_id="leader", instance_metadata={})
    )
    manager._queue_repository = MagicMock()
    manager.config = MagicMock()
    manager.config.report_repair = MagicMock()
    manager.config.report_repair.enabled = False
    manager.config.report_repair.repair_excluded_agents = []
    manager.config.report_repair.size_ratio_threshold = 5.0
    manager.config.report_repair.lookback_messages = 3
    # The PR2/PR3 facade — wired to the REAL read (anchor-scoped).
    manager.count_outstanding_commissions = (
        lambda iid: count_outstanding_commissions(engine, iid)
    )
    events_service = MagicMock()
    events_service._publish_instance_lifecycle_event = AsyncMock()
    service = ChildReportsService(
        manager=manager, events_service=events_service
    )
    return service, manager


class _GateManager:
    """A manager double wired to the REAL read (the R2 evaluate path)."""

    def __init__(self, engine):
        self._engine = engine
        self.count_pending_children = lambda _iid: 0
        self.get_queued_or_expected_wakeups = lambda _iid: 0
        self.count_live_descendants = lambda _iid: 0
        self.count_busy_descendants = lambda _iid: 0
        self.has_open_user_answer = lambda _iid: False
        self.mission_has_commissions = lambda iid: read_mission_census(
            engine, iid
        )
        self.get_mission_founding_message_id = lambda iid: None
        self.count_outstanding_commissions = (
            lambda iid: count_outstanding_commissions(engine, iid)
        )
        self.get_mission_id = lambda iid: None
        self.list_mission_outstanding_commissions = lambda iid: []
        self.get_tree_ids_permanent = lambda _iid: []
        self._instance_repository = None


class TestCrossEpochHold:
    async def test_the_orphaned_commission_on_a_superseded_epoch_still_holds_the_publish(
        self, engine, enforce_mode
    ):
        """THE mandatory orphan-hold pin (MAJOR-1 ruling (c)).

        The exact live-captured chain: epoch 1 mints a commission →
        job_continue supersedes epoch 1 → the commission is orphaned
        outstanding → the L3 publish hold MUST still fire — with the
        gate actually REACHED (a live non-terminal descendant is
        seeded; the hold reason names the mission leg, proving the
        mission leg fired BEFORE the liveness leg could defer).
        """
        service = _orphan_scenario(engine)
        # PR2 caveat (e): a LIVE descendant in the tree — the hold must
        # come from the MISSION leg, not be masked/replaced by a
        # liveness deferral.
        _seed_instance(engine, LIVE_CHILD, status="running", parent=ANCHOR)
        repo = MissionRecordRepository(engine)
        open_epoch = repo.find_open_epoch_by_anchor(ANCHOR)
        # The PRE-PR3 (open-epoch-scoped) read says quiet — the exact
        # hole MAJOR-1 closes:
        assert (
            repo.count_outstanding_commissions_for_mission(
                open_epoch.mission_id
            )
            == 0
        )
        # The PR3 (anchor-scoped) read sees the orphan:
        assert repo.count_outstanding_commissions_for_anchor(ANCHOR) == 1
        assert count_outstanding_commissions(engine, ANCHOR) == 1

        reports, _manager = _child_reports(engine)
        allowed, reason, declared_wait = await reports._root_completion_gate(
            None, ANCHOR
        )
        assert allowed is False
        assert reason is not None and "mission_commissions=1" in reason
        assert declared_wait is False

    async def test_the_hold_releases_when_the_orphan_is_satisfied(
        self, engine, enforce_mode
    ):
        """The terminator is the release path: satisfying the orphaned
        commission (the guarded ``outstanding → satisfied`` transition —
        the PR3 delivery-terminator shape) releases the cross-epoch
        hold; the publish proceeds."""
        service = _orphan_scenario(engine)
        reports, _manager = _child_reports(engine)
        allowed, _reason, _wait = await reports._root_completion_gate(
            None, ANCHOR
        )
        assert allowed is False

        # The orphan lives on the SUPERSEDED epoch (the open epoch has
        # none) — walk the records directly, as the sweep will.
        repo = MissionRecordRepository(engine)
        open_epoch = repo.find_open_epoch_by_anchor(ANCHOR)
        assert (
            repo.list_commissions_for_mission(open_epoch.mission_id) == []
        )
        with Session(engine) as session:
            rows = list(
                session.exec(select(MissionCommission)).all()
            )
        orphan = next(r for r in rows if r.state == "outstanding")
        assert orphan.mission_id != open_epoch.mission_id
        assert service.satisfy_commission(orphan.commission_id) is True

        allowed, reason, _wait = await reports._root_completion_gate(
            None, ANCHOR
        )
        assert allowed is True
        assert reason is None

    async def test_r2_quiet_requires_cross_epoch_quiet(
        self, engine, enforce_mode
    ):
        """The 5th R2 input reads the SAME anchor-scoped count: after
        the supersession the evaluation is NOT quiet (the commission
        arms the legitimate-pending allow), and the ``-1`` semantics
        are untouched."""
        _orphan_scenario(engine)
        manager = _GateManager(engine)
        # The PR2 gate-reads fixture's message shape (a delegation, then
        # a completion claim) — the shape that reaches the commission
        # arm of the quiet predicate.
        messages = [
            HumanMessage(content="do the thing", id="m-founding"),
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "send_message",
                        "args": {"target": "child"},
                        "id": "d1",
                    }
                ],
            ),
            AIMessage("Everything is complete."),
        ]
        result = evaluate(
            ANCHOR, 0, messages, DEFAULT_GATE_SETTINGS, manager
        )
        assert result.outstanding_commissions == 1
        assert (
            result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        )


class TestAdvisoryExclusionOnTheAnchorScopedRead:
    async def test_an_advisory_backfill_row_never_holds_across_epochs(
        self, engine, enforce_mode
    ):
        """Advisory exclusion ruling: ``advisory_backfill=True`` rows are
        excluded from EVERY enforce-read input — here pinned on the
        anchor-scoped quiet/L3 read (PR2's mission-scoped count already
        excluded them; this pins the NEW cross-epoch shape)."""
        _seed_instance(engine, ANCHOR)
        _seed_instance(engine, WORKER)
        service = MissionRecordService(engine)
        epoch1 = service.resolve_or_mint(ANCHOR, "user_api")
        commission = service.mint_commission(
            epoch1.mission_id,
            commissioner_instance_id=ANCHOR,
            worker_instance_id=WORKER,
            lane="instance_nudge",
            advisory_backfill=True,
            provenance="backfill",
        )
        assert commission.advisory_backfill is True
        service.mint_continuation_epoch(ANCHOR)  # orphan the advisory row
        assert count_outstanding_commissions(engine, ANCHOR) == 0
        reports, _manager = _child_reports(engine)
        allowed, reason, _wait = await reports._root_completion_gate(
            None, ANCHOR
        )
        assert allowed is True
        assert reason is None


class TestFailOpenAndScopeRulings:
    async def test_the_unknown_sentinel_is_preserved(
        self, engine, enforce_mode, monkeypatch
    ):
        """A failed anchor-scoped read constructs the ``-1`` sentinel —
        NEVER a 0 default (the fail-open contract is preserved
        exactly)."""

        def _boom(_anchor):
            raise RuntimeError("records read is down")

        monkeypatch.setattr(
            MissionRecordRepository,
            "count_outstanding_commissions_for_anchor",
            _boom,
        )
        assert count_outstanding_commissions(engine, ANCHOR) == (
            MISSION_READ_UNKNOWN
        )

    async def test_no_epochs_at_all_is_a_real_zero(
        self, engine, enforce_mode
    ):
        """An anchor with NO recorded epochs reads ``0`` (quiet) — a
        real answer, not the unknown sentinel."""
        _seed_instance(engine, ANCHOR)
        assert count_outstanding_commissions(engine, ANCHOR) == 0

    async def test_the_census_stays_open_epoch_scoped(
        self, engine, enforce_mode
    ):
        """SCOPE RULING (documented, not silently widened): the
        attestation census reads the OPEN epoch only — the orphaned
        commission on the superseded epoch does NOT arm it."""
        _orphan_scenario(engine)
        # The open epoch (epoch 2) has no commission rows:
        assert read_mission_census(engine, ANCHOR) is False
        # ...while the anchor-scoped hold read still sees the orphan:
        assert count_outstanding_commissions(engine, ANCHOR) == 1
