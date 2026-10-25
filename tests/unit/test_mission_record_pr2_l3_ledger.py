"""Unit tests — the L3 mission leg + the epoch-boundary ledger (PR2).

Two PR2 behaviours live here:

* the L3 ROOT-COMPLETION GATE's MISSION-SCOPED leg: the terminal
  "completed" publish is HELD while the open mission has outstanding
  commissions (the out-of-tree commissioned worker the tree-scoped
  bus-pending leg cannot see — the D2 premature-settle class). The
  watchers (the bus-pending leg) stay EXACTLY as they are;
* the EPOCH-BOUNDARY LEDGER CLEAR + the EPOCH-TAGGED deny ledger
  (proposal-b §2.4): a new epoch clears the deny counter, the
  escalation flag and the O4 dedup array; the deny entries carry their
  owning mission so the same fingerprint under a DIFFERENT epoch counts
  again.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.services.child_reports import ChildReportsService
from daemon.services.mission_record_reads import count_outstanding_commissions
from daemon.services.mission_record_service import (
    MISSION_RECORDS_MODE_ENV,
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_mode_cache():
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'pr2-l3-ledger.sqlite'}",
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


def _seed_instance(engine, iid: str, status: str = "running") -> str:
    with Session(engine) as session:
        if session.get(Instance, iid) is None:
            session.add(
                Instance(
                    instance_id=iid,
                    agent_id="leader",
                    agent_dir=f"agents/leader/{iid[:8]}",
                    status=status,
                    project_id=None,
                )
            )
            session.commit()
    return iid


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
    # The PR2 facade — wired to the REAL read.
    manager.count_outstanding_commissions = (
        lambda iid: count_outstanding_commissions(engine, iid)
    )
    events_service = MagicMock()
    events_service._publish_instance_lifecycle_event = AsyncMock()
    service = ChildReportsService(
        manager=manager, events_service=events_service
    )
    return service, manager


class TestL3MissionLeg:
    async def test_an_outstanding_commission_holds_the_publish(
        self, engine
    ):
        iid = _seed_instance(engine, str(uuid.uuid4()))
        worker = _seed_instance(engine, str(uuid.uuid4()))
        service = MissionRecordService(engine)
        resolution = service.resolve_or_mint(iid, "user_api")
        service.mint_commission(
            resolution.mission_id,
            commissioner_instance_id=iid,
            worker_instance_id=worker,
            lane="instance_nudge",
        )
        reports, _manager = _child_reports(engine)

        allowed, reason, declared_wait = await reports._root_completion_gate(
            None, iid
        )
        assert allowed is False
        assert "mission_commissions=1" in reason
        assert declared_wait is False

    async def test_a_satisfied_commission_releases_the_publish(
        self, engine
    ):
        iid = _seed_instance(engine, str(uuid.uuid4()))
        worker = _seed_instance(engine, str(uuid.uuid4()))
        service = MissionRecordService(engine)
        resolution = service.resolve_or_mint(iid, "user_api")
        commission = service.mint_commission(
            resolution.mission_id,
            commissioner_instance_id=iid,
            worker_instance_id=worker,
            lane="instance_nudge",
        )
        reports, _manager = _child_reports(engine)

        allowed, _reason, _wait = await reports._root_completion_gate(
            None, iid
        )
        assert allowed is False

        service.satisfy_commission(commission.commission_id)
        allowed, reason, _wait = await reports._root_completion_gate(
            None, iid
        )
        assert allowed is True
        assert reason is None

    async def test_the_unknown_sentinel_fails_open(self, engine):
        """A failed read must NEVER hold the publish (the gate's
        fail-open posture: a missing terminal is worse than an extra
        premature one)."""
        iid = _seed_instance(engine, str(uuid.uuid4()))
        reports, manager = _child_reports(engine)
        manager.count_outstanding_commissions = lambda _iid: -1

        allowed, _reason, _wait = await reports._root_completion_gate(
            None, iid
        )
        assert allowed is True

    async def test_the_fail_open_is_logged(self, engine, caplog):
        """The −1 fail-open is NOT silent: the read fault is logged
        (parity with the attestation gate's own short-circuit log) so
        the operator can see that the L3 mission leg degraded to
        "proceed" on an unknown count."""
        iid = _seed_instance(engine, str(uuid.uuid4()))
        reports, manager = _child_reports(engine)

        def _boom(_iid):
            raise RuntimeError("commission read is down")

        manager.count_outstanding_commissions = _boom

        with caplog.at_level("WARNING"):
            allowed, _reason, _wait = await reports._root_completion_gate(
                None, iid
            )

        assert allowed is True
        assert any(
            "mission-commission count failed" in record.message
            for record in caplog.records
        ), [record.message for record in caplog.records]

    async def test_a_missing_facade_is_inactive(self, engine):
        iid = _seed_instance(engine, str(uuid.uuid4()))
        reports, manager = _child_reports(engine)
        del manager.count_outstanding_commissions

        allowed, _reason, _wait = await reports._root_completion_gate(
            None, iid
        )
        assert allowed is True

    async def test_the_bus_pending_leg_is_untouched(self, engine):
        """The watchers stay as they are — the mission leg is ADDITIVE."""
        iid = _seed_instance(engine, str(uuid.uuid4()))
        reports, manager = _child_reports(engine)
        # A bus-pending leg that reports a pending watcher.
        manager._bus_count_pending_for_target_sync = lambda _iid: 1
        reports._bus_count_pending_for_target_sync = (
            lambda _iid: 1
        )
        allowed, reason, _wait = await reports._root_completion_gate(
            None, iid
        )
        assert allowed is False
        assert "bus_pending=1" in reason


class TestEpochBoundaryLedgerClear:
    def test_a_new_epoch_clears_the_ledger(self, engine, monkeypatch):
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "enforce")
        _reset_mission_records_mode_for_tests()
        iid = _seed_instance(engine, str(uuid.uuid4()))
        repo = SQLModelInstanceRepository(engine)
        repo.increment_attestation_denied_count(iid, "epoch-1")
        with Session(engine) as session:
            row = session.get(Instance, iid)
            row.completion_gate_escalated = True
            session.commit()
        assert repo.get_attestation_denied_count(iid) == 1

        service = MissionRecordService(engine)
        service.resolve_or_mint(iid, "user_api")

        assert repo.get_attestation_denied_count(iid) == 0
        with Session(engine) as session:
            row = session.get(Instance, iid)
            assert row.completion_gate_escalated is False
            assert (
                row.instance_metadata or {}
            ).get("attestation:denial_epochs") == []

    def test_the_clear_is_armed_in_enforce_mode_only(
        self, engine, monkeypatch
    ):
        """The mode ladder's ``dry`` contract: the ledger clear is a
        GATE-visible behavior change — it is armed with the enforce
        promotion, not with the mints."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "dry")
        _reset_mission_records_mode_for_tests()
        iid = _seed_instance(engine, str(uuid.uuid4()))
        repo = SQLModelInstanceRepository(engine)
        repo.increment_attestation_denied_count(iid, "epoch-1")

        service = MissionRecordService(engine)
        service.resolve_or_mint(iid, "user_api")

        assert repo.get_attestation_denied_count(iid) == 1

    def test_a_reuse_does_not_clear_the_ledger(self, engine, monkeypatch):
        """Rung 1 (the open epoch continues) is NOT a boundary."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "enforce")
        _reset_mission_records_mode_for_tests()
        iid = _seed_instance(engine, str(uuid.uuid4()))
        service = MissionRecordService(engine)
        service.resolve_or_mint(iid, "user_api")
        repo = SQLModelInstanceRepository(engine)
        repo.increment_attestation_denied_count(iid, "epoch-1")

        service.resolve_or_mint(iid, "user_api")
        assert repo.get_attestation_denied_count(iid) == 1


class TestEpochTaggedDenyLedger:
    def test_the_entry_carries_the_owning_mission(self, engine):
        iid = _seed_instance(engine, str(uuid.uuid4()))
        repo = SQLModelInstanceRepository(engine)
        repo.increment_attestation_denied_count(
            iid, "fingerprint-1", "mission-a"
        )
        with Session(engine) as session:
            row = session.get(Instance, iid)
            entries = list(
                (row.instance_metadata or {}).get(
                    "attestation:denial_epochs"
                )
                or []
            )
        assert entries == [
            {"epoch": "fingerprint-1", "mission_id": "mission-a"}
        ]

    def test_a_replay_within_the_epoch_is_swallowed(self, engine):
        iid = _seed_instance(engine, str(uuid.uuid4()))
        repo = SQLModelInstanceRepository(engine)
        repo.increment_attestation_denied_count(
            iid, "fingerprint-1", "mission-a"
        )
        # The replay returns the UNCHANGED count.
        assert (
            repo.increment_attestation_denied_count(
                iid, "fingerprint-1", "mission-a"
            )
            == 1
        )
        assert repo.get_attestation_denied_count(iid) == 1

    def test_the_same_fingerprint_under_a_new_epoch_counts(self, engine):
        """The D5 carryover lane dies: the previous mission's deny
        ledger does NOT ride forward."""
        iid = _seed_instance(engine, str(uuid.uuid4()))
        repo = SQLModelInstanceRepository(engine)
        repo.increment_attestation_denied_count(
            iid, "fingerprint-1", "mission-a"
        )
        assert (
            repo.increment_attestation_denied_count(
                iid, "fingerprint-1", "mission-b"
            )
            == 2
        )
        assert repo.get_attestation_denied_count(iid) == 2

    def test_the_legacy_untagged_shape_is_unchanged(self, engine):
        """A caller that does not consult the records keeps the legacy
        plain-string entry shape, byte-for-byte."""
        iid = _seed_instance(engine, str(uuid.uuid4()))
        repo = SQLModelInstanceRepository(engine)
        repo.increment_attestation_denied_count(iid, "fingerprint-1")
        with Session(engine) as session:
            row = session.get(Instance, iid)
            entries = list(
                (row.instance_metadata or {}).get(
                    "attestation:denial_epochs"
                )
                or []
            )
        assert entries == ["fingerprint-1"]
        # The legacy dedup still swallows the replay.
        assert (
            repo.increment_attestation_denied_count(iid, "fingerprint-1")
            == 1
        )


class TestManagerFacadeModeGate:
    def test_the_facade_is_inactive_outside_enforce(self, monkeypatch):
        from daemon.manager import InstanceManager

        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "dry")
        _reset_mission_records_mode_for_tests()
        stub = MagicMock()
        stub._engine = MagicMock()
        # The INACTIVE input is a real 0 (the legacy quiet predicate is
        # preserved exactly) — NOT the unknown sentinel.
        assert (
            InstanceManager.count_outstanding_commissions(stub, "iid") == 0
        )

    def test_the_census_facade_has_no_answer_outside_enforce(
        self, monkeypatch
    ):
        from daemon.manager import InstanceManager

        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "off")
        _reset_mission_records_mode_for_tests()
        stub = MagicMock()
        stub._engine = MagicMock()
        assert InstanceManager.mission_has_commissions(stub, "iid") is None
        assert (
            InstanceManager.get_mission_founding_message_id(stub, "iid")
            is None
        )
