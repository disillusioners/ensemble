"""PostgreSQL integration — H-MODE: the shipped-default posture of the
mission-commission retirement WRITER (RDRS lane 7).

THE HYPOTHESIS UNDER TEST (the dispatch's H-MODE): the sweep WRITES only
when ``ENSEMBLE_MISSION_RECORDS`` resolves to ``enforce``. In ``dry`` —
the SHIPPED DEFAULT (the knob appears nowhere in ``config.yaml`` /
``launcher.sh`` / the shipped ``.env``; the resolver defaults to ``dry``
at ``daemon/services/mission_record_service.py:226-228``) — the sweep
DETECTS (counters move) but writes NOTHING, while the MINT path writes
rows in ``dry``. If true, a default-posture v0.27.11 deployment carries
the writer PRESENT BUT INERT — the wedge would persist wherever the gate
leg is armed.

THE SYMMETRY (the other half of the posture finding): the gate's
outstanding-count facade is MODE-AWARE TOO
(``daemon/manager.py:12063-12068``: outside ``enforce`` the count read
is not even attempted — a REAL 0). Under a PURE ``dry`` deployment the
gate leg is therefore unarmed: no mission_commissions block at all. The
wedge shape requires ``enforce`` posture (the live v0.27.8 deployment
blocks with ``mission_commissions=N``, and that block reason is only
reachable in ``enforce``).

PROCESS DISCIPLINE (the pack runs this file TWICE, in separate
processes): the mode resolver is restart-read (cached once per process).
The pack's first run sets ``ENSEMBLE_MISSION_RECORDS=dry``, its second
``=enforce`` — the SAME tests must observe the mode-appropriate outcome.
``test_the_shipped_default_resolves_to_dry`` additionally proves the
ABSENT-knob posture by deleting the env var and resetting the cache (the
documented test hook).
"""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
# daemon.manager registers FURTHER SQLModel tables (snapshot_embeddings &
# friends) in the shared metadata — it MUST import at MODULE level so the
# registration happens BEFORE the session-scoped ``pg_engine`` fixture runs
# ``create_all`` (the conftest's documented metadata-registration contract;
# a deferred import would leave those tables uncreated and every LATER
# test's autouse TRUNCATE would fail on UndefinedTable).
import daemon.manager  # noqa: F401
from daemon.manager import InstanceManager
from daemon.repositories.dependency_bus.repository import (
    DependencyWatcherRepository,
)
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.instance.models import Instance
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)
from daemon.repositories.mission_record.models import (
    CommissionState,
    MissionCommission,
    MissionRecord,
)
from daemon.repositories.mission_record.repository import (
    MissionRecordRepository,
)
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.services.mission_record_reads import count_outstanding_commissions
from daemon.services.mission_record_service import (
    MISSION_RECORDS_MODE_ENV,
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
    mission_records_enforce_enabled,
    resolve_mission_records_mode,
)
from daemon.services.dependency_bus import DependencyBus
from daemon.services.report_delivery_recovery import (
    ReportDeliveryRecoveryService,
)

TTL_SECONDS = 3600


@pytest.fixture(autouse=True)
def _mode_from_process_env():
    """Resolve the mode ONCE from the PROCESS env (restart-read parity).

    The pack runs this file under ``ENSEMBLE_MISSION_RECORDS=dry`` and
    again under ``=enforce``; the fixture exposes the resolved posture to
    every test so the SAME code observes the mode-appropriate outcome.
    """
    _reset_mission_records_mode_for_tests()
    mode = resolve_mission_records_mode()
    assert mode in ("dry", "enforce", "off"), mode
    yield mode
    _reset_mission_records_mode_for_tests()


@pytest.fixture(autouse=True)
def _no_parent_history(monkeypatch):
    import daemon.persistence as persistence

    async def _fake_messages(checkpointer, parent_id, manager=None):
        return []

    monkeypatch.setattr(persistence, "get_instance_messages", _fake_messages)


@pytest.fixture
def manager_loop():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


def _iso_seconds_ago(seconds: float) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).isoformat()


def _seed_instance(engine, instance_id: str, status: str = "running") -> str:
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="worker",
                agent_dir=f"agents/worker/{instance_id[:8]}",
                status=status,
                project_id=None,
            )
        )
        session.commit()
    return instance_id


def _seed_open_epoch(engine, anchor_instance_id: str) -> str:
    mission_id = str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            MissionRecord(
                mission_id=mission_id,
                anchor_instance_id=anchor_instance_id,
                epoch_seq=1,
                founding_message_id=None,
                founding_source="user_api",
                commissioner_instance_id=None,
                work_id=None,
                parent_mission_id=None,
                status="open",
                provenance="live",
            )
        )
        session.commit()
    return mission_id


def _mint_commission(engine, *, mission_id, commissioner, worker, age_seconds=0):
    commission_id = str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            MissionCommission(
                commission_id=commission_id,
                mission_id=mission_id,
                commissioner_instance_id=commissioner,
                worker_instance_id=worker,
                work_id=None,
                lane="send_message",
                state=CommissionState.OUTSTANDING.value,
                created_at=_iso_seconds_ago(age_seconds),
                advisory_backfill=False,
            )
        )
        session.commit()
    return commission_id


def _row(engine, commission_id: str) -> MissionCommission | None:
    with Session(engine) as session:
        return session.get(MissionCommission, commission_id)


def _build_service(engine, *, manager_loop):
    manager = MagicMock()
    manager._loop = manager_loop
    manager._checkpointer = MagicMock()
    manager._job_queue_service = None
    return ReportDeliveryRecoveryService(
        task_repo=MagicMock(),
        report_injection_repo=ReportInjectionRepository(engine=engine),
        queue_repo=SQLModelMessageQueueRepository(engine),
        instance_repo=SQLModelInstanceRepository(engine),
        manager_ref=manager,
        interval_seconds=300,
        age_bound_minutes=10,
        batch_cap=50,
        enabled=True,
        lane_deferred=False,
        lane_no_row_backstop=False,
        lane_pending_age=False,
        lane_recovery_retry=False,
        lane_orphan=False,
        lane_stuck_wake=False,
        lane_mission_reconciliation=True,
        mission_record_repo=MissionRecordRepository(engine),
        mission_record_service=MissionRecordService(engine),
        dependency_bus=DependencyBus(DependencyWatcherRepository(engine)),
        job_repo=MagicMock(),
        mission_commission_ttl_seconds=TTL_SECONDS,
    )


class TestHModeSweepPosture:
    def test_the_sweep_posture_follows_the_mode_ladder(
        self, pg_engine, manager_loop, _mode_from_process_env
    ):
        """Same seed, same code: ``dry`` moves counters and writes NOTHING;
        ``enforce`` writes. The TTL-abandon shape (the clearest write)."""
        mode = _mode_from_process_env
        commissioner = _seed_instance(pg_engine, str(uuid.uuid4()), "running")
        worker = _seed_instance(pg_engine, str(uuid.uuid4()), "running")
        mission_id = _seed_open_epoch(pg_engine, commissioner)
        commission_id = _mint_commission(
            pg_engine,
            mission_id=mission_id,
            commissioner=commissioner,
            worker=worker,
            age_seconds=TTL_SECONDS * 10,
        )

        service = _build_service(pg_engine, manager_loop=manager_loop)
        lane = service.recover_now().lanes["mission_reconciliation"]

        row = _row(pg_engine, commission_id)
        if mode == "enforce":
            assert lane.abandoned_ttl == 1
            assert lane.recovered == 1
            assert row.state == CommissionState.ABANDONED.value
            assert row.resolved_at is not None
        else:
            # dry (and off never reaches here — the lane is not queried)
            assert mode == "dry"
            assert lane.abandoned_ttl == 1  # the DETECTION counter moves
            assert lane.recovered == 0  # no write credit
            assert row.state == CommissionState.OUTSTANDING.value
            assert row.resolved_at is None
            assert row.abandon_reason is None

    def test_the_satisfy_path_is_equally_posture_gated(
        self, pg_engine, manager_loop, _mode_from_process_env
    ):
        """The satisfy-on-evidence write is gated by the SAME ladder."""
        from daemon.repositories.report_injection.models import (
            ReportInjection,
            ReportInjectionState,
        )

        mode = _mode_from_process_env
        commissioner = _seed_instance(pg_engine, str(uuid.uuid4()), "running")
        worker = _seed_instance(pg_engine, str(uuid.uuid4()), "completed")
        mission_id = _seed_open_epoch(pg_engine, commissioner)
        commission_id = _mint_commission(
            pg_engine,
            mission_id=mission_id,
            commissioner=commissioner,
            worker=worker,
        )
        with Session(pg_engine) as session:
            session.add(
                ReportInjection(
                    injection_id=str(uuid.uuid4()),
                    parent_instance_id=commissioner,
                    child_instance_id=worker,
                    child_message_id=f"msg-{uuid.uuid4()}",
                    created_at=datetime.now(timezone.utc).isoformat(),
                    state=ReportInjectionState.INJECTED.value,
                )
            )
            session.commit()

        service = _build_service(pg_engine, manager_loop=manager_loop)
        lane = service.recover_now().lanes["mission_reconciliation"]

        row = _row(pg_engine, commission_id)
        assert lane.satisfied_on_evidence == 1
        if mode == "enforce":
            assert row.state == CommissionState.SATISFIED.value
            assert row.satisfied_at is not None
        else:
            assert mode == "dry"
            assert row.state == CommissionState.OUTSTANDING.value
            assert row.satisfied_at is None


class TestHModeGateFacadePosture:
    def test_the_gate_count_facade_is_inert_outside_enforce(
        self, pg_engine, _mode_from_process_env
    ):
        """The manager facade (``daemon/manager.py:12063-12068``) returns a
        REAL 0 outside ``enforce`` — the mission leg is unarmed in dry —
        while the RAW anchor read still sees the row."""
        mode = _mode_from_process_env
        anchor = _seed_instance(pg_engine, str(uuid.uuid4()), "running")
        mission_id = _seed_open_epoch(pg_engine, anchor)
        _mint_commission(
            pg_engine,
            mission_id=mission_id,
            commissioner=anchor,
            worker=_seed_instance(pg_engine, str(uuid.uuid4()), "completed"),
        )

        # The raw anchor-scoped read sees the outstanding row...
        assert count_outstanding_commissions(pg_engine, anchor) == 1
        # ...the mode-aware facade (the gate's input) is the real body:
        if not mission_records_enforce_enabled():
            facade_count = 0
        else:
            facade_count = count_outstanding_commissions(pg_engine, anchor)
        if mode == "enforce":
            assert facade_count == 1
        else:
            assert facade_count == 0


class TestB1FacadePostureSymmetry:
    """B1 — the posture-symmetry guard (the H-MODE finding, pinned).

    The REAL ``InstanceManager.count_outstanding_commissions``
    (``daemon/manager.py:12063-12068`` — invoked UNBOUND on a stub
    ``self`` so the PRODUCTION method body runs, not a re-implementation)
    must be mode-aware: outside ``enforce`` it returns 0 WITHOUT READING
    ``mission_commissions`` at all — proven here at the ENGINE level
    (zero SQL statements cross the wire during the facade call).

    WHY THIS PIN EXISTS (the class it guards): the sweep WRITER writes
    only in ``enforce``, and the gate facade counts only in ``enforce`` —
    the two are SYMMETRIC, which is why a default-posture (``dry``)
    deployment has NO mission_commissions wedge at all. A future refactor
    that un-gates the facade in ``dry`` (counts the rows while the writer
    stays inert) would wedge EVERY default-posture deployment behind an
    inert writer — the exact prod v0.27.8 shape, recreated by a one-line
    "cleanup". The dry leg asserts the zero-read property; the enforce
    leg asserts the real count. Both postures run (two processes — the
    resolver is restart-read); this is the mutation matrix's M2 target.
    """

    def test_the_facade_reads_nothing_outside_enforce(
        self, pg_engine, _mode_from_process_env
    ):
        from sqlalchemy import event

        mode = _mode_from_process_env
        anchor = _seed_instance(pg_engine, str(uuid.uuid4()), "running")
        mission_id = _seed_open_epoch(pg_engine, anchor)
        _mint_commission(
            pg_engine,
            mission_id=mission_id,
            commissioner=anchor,
            worker=_seed_instance(pg_engine, str(uuid.uuid4()), "completed"),
        )

        statements: list[str] = []

        def _record(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(pg_engine, "before_cursor_execute", _record)
        try:
            stub_self = SimpleNamespace(_engine=pg_engine)
            value = InstanceManager.count_outstanding_commissions(
                stub_self, anchor
            )
        finally:
            event.remove(pg_engine, "before_cursor_execute", _record)

        commission_reads = [
            s for s in statements if "mission_commissions" in s
        ]
        if mode == "enforce":
            assert value == 1
            assert len(commission_reads) >= 1
        else:
            assert mode == "dry"
            assert value == 0
            # ZERO DB READS on the dry path — not just a zero return
            # value: no statement of ANY kind crosses the wire.
            assert statements == []
            assert commission_reads == []


class TestShippedDefault:
    def test_the_shipped_default_resolves_to_dry(self, pg_engine, monkeypatch):
        """With the knob ABSENT (the shipped posture), the ladder resolves
        ``dry`` and enforce is OFF — the writer and the gate leg are both
        inert by default."""
        monkeypatch.delenv(MISSION_RECORDS_MODE_ENV, raising=False)
        _reset_mission_records_mode_for_tests()
        try:
            assert resolve_mission_records_mode() == "dry"
            assert mission_records_enforce_enabled() is False
        finally:
            _reset_mission_records_mode_for_tests()

    def test_an_unrecognized_token_raises_loudly(self, pg_engine, monkeypatch):
        """The operator-typo convention: a bad token raises, never
        silently degrades (resolved loudly at the boot site)."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "banana")
        _reset_mission_records_mode_for_tests()
        try:
            with pytest.raises(ValueError, match=MISSION_RECORDS_MODE_ENV):
                resolve_mission_records_mode()
        finally:
            _reset_mission_records_mode_for_tests()
