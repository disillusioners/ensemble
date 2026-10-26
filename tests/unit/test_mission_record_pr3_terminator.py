"""Unit tests — the PR3 COMMISSION TERMINATOR (delivery-side satisfy).

The enforce-promotion precondition (the PR2 adoption notes §(f)): the
record got LOUD in PR2 (the L3 hold + the R2 quiet predicate) but
shipped NO writer that retires a commission row. PR3 wires the
DELIVERY-side terminator: when a LEG-1 (mission-commission)-routed
report is actually written + addressed to its commissioner on the
natural completion write path, the routing commission transitions
``outstanding → satisfied`` —

* guarded ``UPDATE ... WHERE state='outstanding'`` (rowcount 0 = lost
  race — counted, never retried in place);
* ATOMIC with the delivery write (rides the WriteGuardSession via a
  SAVEPOINT — a rolled-back delivery can never leave a satisfied
  commission behind);
* FAIL-OPEN (a satisfy failure never breaks delivery);
* ``dry``/``off`` inert (off: the wrapper refuses; dry: leg 1 never
  routes, so there is nothing to satisfy).

Deliberately NOT satisfied on this path (each stays the sweep's /
another path's): a dead commissioner (the guard fell back — the row
stays outstanding, honestly), deferred markers (no delivery),
idempotency / recovered-marker races.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.mission_record.models import MissionCommission
from daemon.repositories.mission_record.repository import (
    MissionRecordRepository,
)
from daemon.repositories.report_injection.models import ReportInjection
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.mission_record_service import (
    MISSION_RECORDS_MODE_ENV,
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
    satisfy_commission_fail_open,
)
from daemon.write_pause_guard import WritePauseGuard

PARENT = "parent-1"
COMMISSIONER = "commissioner-1"


@pytest.fixture(autouse=True)
def _reset_mode_cache():
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'pr3-terminator.sqlite'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def dependency_bus():
    bus = MagicMock(name="DependencyBus")
    bus.count_pending_for_target_sync.return_value = 0
    set_dependency_bus(bus)
    try:
        yield bus
    finally:
        set_dependency_bus(None)


@pytest.fixture
def enforce_mode(monkeypatch):
    monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "enforce")
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


def _service(engine) -> ChildReportsService:
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager._engine = engine
    manager.write_guard = WritePauseGuard()
    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    return service


def _seed(engine, instance_id, *, parent=None, status=InstanceStatus.RUNNING.value):
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="agent",
                agent_dir=f"/tmp/{instance_id[:8]}",
                parent_id=parent,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _commission_row(engine, commission_id) -> MissionCommission | None:
    with Session(engine) as session:
        return session.get(MissionCommission, commission_id)


def _injection_rows(engine, child_id) -> list[ReportInjection]:
    with Session(engine) as session:
        return list(
            session.exec(
                select(ReportInjection).where(
                    ReportInjection.child_instance_id == child_id
                )
            ).all()
        )


def _seed_commission(engine, *, worker_id: str, commissioner=COMMISSIONER):
    _seed(engine, commissioner)
    service = MissionRecordService(engine)
    resolution = service.resolve_or_mint(commissioner, "user_api")
    return service.mint_commission(
        resolution.mission_id,
        commissioner_instance_id=commissioner,
        worker_instance_id=worker_id,
        lane="send_message",
    )


class TestTheDeliveryTerminator:
    def test_a_mission_routed_delivery_satisfies_the_routing_commission(
        self, engine, enforce_mode
    ):
        """THE terminator: leg-1-routed report written + addressed to
        the commissioner ⇒ the routing commission is SATISFIED,
        atomically with the delivery write."""
        _seed(engine, PARENT)
        child = _seed(engine, f"child-{uuid.uuid4().hex[:8]}", parent=PARENT)
        commission = _seed_commission(engine, worker_id=child)

        result = _service(engine)._process_child_completion_db_sync(
            instance_id=child,
            completed_message_id="completed-1",
            last_content="done",
        )
        assert result.outcome == "regular_child_completed"

        row = _commission_row(engine, commission.commission_id)
        assert row.state == "satisfied"
        assert row.satisfied_at is not None
        # OQ-3(b): the terminator's guarded transition stamps the
        # resolution stamp too — the row left ``outstanding``, so
        # ``resolved_at`` must not stay NULL.
        assert row.resolved_at is not None

        # The delivery landed on the commissioner + carries provenance.
        rows = _injection_rows(engine, child)
        assert len(rows) == 1
        assert rows[0].parent_instance_id == COMMISSIONER
        assert rows[0].mission_id == commission.mission_id
        assert rows[0].commissioner_instance_id == COMMISSIONER

    def test_dry_mode_never_satisfies(self, engine, monkeypatch):
        """The terminator is inert outside ``enforce``: in ``dry`` leg 1
        never routes (structural-parent delivery) and nothing is
        satisfied."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "dry")
        _reset_mission_records_mode_for_tests()
        _seed(engine, PARENT)
        child = _seed(engine, f"child-{uuid.uuid4().hex[:8]}", parent=PARENT)
        commission = _seed_commission(engine, worker_id=child)

        result = _service(engine)._process_child_completion_db_sync(
            instance_id=child,
            completed_message_id="completed-1",
            last_content="done",
        )
        assert result.outcome == "regular_child_completed"
        row = _commission_row(engine, commission.commission_id)
        assert row.state == "outstanding"
        rows = _injection_rows(engine, child)
        assert rows[0].parent_instance_id == PARENT
        assert rows[0].mission_id is None
        # A LEGACY-leg delivery carries NO record-based attribution:
        # ``commissioner_instance_id`` is COMMISSION-SCOPED (leg 1 only),
        # so it is an honest NULL here — the destination lives in
        # ``parent_instance_id``.
        assert rows[0].commissioner_instance_id is None

    def test_a_dead_commissioner_keeps_the_commission_outstanding(
        self, engine, enforce_mode
    ):
        """The dead-recipient guard falls back to the structural parent;
        the report never reached the commissioner — the commission
        stays OUTSTANDING (honest non-delivery; the sweep owns it)."""
        _seed(engine, PARENT)
        child = _seed(engine, f"child-{uuid.uuid4().hex[:8]}", parent=PARENT)
        commission = _seed_commission(engine, worker_id=child)
        # The commissioner died between mint and delivery.
        with Session(engine) as session:
            row = session.get(Instance, COMMISSIONER)
            row.status = InstanceStatus.TERMINATED.value
            session.add(row)
            session.commit()

        result = _service(engine)._process_child_completion_db_sync(
            instance_id=child,
            completed_message_id="completed-1",
            last_content="done",
        )
        assert result.outcome == "regular_child_completed"
        row = _commission_row(engine, commission.commission_id)
        assert row.state == "outstanding"
        rows = _injection_rows(engine, child)
        # The report went to the structural parent — the DESTINATION is
        # recorded in ``parent_instance_id``. The provenance columns stay
        # COMMISSION-SCOPED: ``commissioner_instance_id`` names the
        # commission's commissioner (the dead one), NOT the structural
        # parent the report actually fell back to.
        assert rows[0].parent_instance_id == PARENT
        assert rows[0].commissioner_instance_id == COMMISSIONER
        assert rows[0].mission_id == commission.mission_id


class TestSatisfyFailOpenWrapper:
    def test_happy_path_repo_owned(self, engine):
        commission = _seed_commission(engine, worker_id=_seed(engine, "w-1"))
        moved = satisfy_commission_fail_open(
            MissionRecordService(engine), commission.commission_id
        )
        assert moved is True
        assert _commission_row(engine, commission.commission_id).state == (
            "satisfied"
        )

    def test_staged_on_the_callers_session_then_durable_on_commit(
        self, engine
    ):
        """With ``session=`` the write rides the caller's transaction
        (SAVEPOINT) — visible in-transaction before the caller commits,
        durable after it.

        DRIVER CAVEAT (documented, not a defect): the "durable ONLY on
        commit" half is PostgreSQL-verifiable — the pysqlite driver's
        legacy isolation mode turns ``RELEASE SAVEPOINT`` into a full
        commit (the same engine-level quirk the F3/F5 fences in
        child_reports live with), so a post-RELEASE outer rollback
        cannot be pinned on SQLite. Production (PG) savepoints are
        real: a rolled-back delivery can never leave a satisfied
        commission behind there.
        """
        commission = _seed_commission(engine, worker_id=_seed(engine, "w-1"))
        with Session(engine) as session:
            moved = satisfy_commission_fail_open(
                MissionRecordService(engine),
                commission.commission_id,
                session=session,
            )
            assert moved is True
            # Visible inside the caller's transaction:
            staged = session.get(MissionCommission, commission.commission_id)
            assert staged.state == "satisfied"
            session.commit()
        assert _commission_row(engine, commission.commission_id).state == (
            "satisfied"
        )

    def test_a_staged_failure_is_contained_and_never_poisons(
        self, engine, monkeypatch
    ):
        """Containment: a satisfy failure inside the caller's
        transaction rolls back ONLY the savepoint — the caller's
        transaction stays usable (the fail-open contract's half that
        SQLite CAN pin)."""
        commission = _seed_commission(engine, worker_id=_seed(engine, "w-1"))
        with Session(engine) as session:
            moved = satisfy_commission_fail_open(
                MissionRecordService(engine),
                "no-such-commission",
                session=session,
            )
            assert moved is False  # rowcount 0 — no raise, no poison
            # The caller's transaction is still fully usable:
            assert session.get(
                MissionCommission, commission.commission_id
            ).state == "outstanding"

    def test_lost_race_is_counted_never_retried(self, engine):
        commission = _seed_commission(engine, worker_id=_seed(engine, "w-1"))
        # Another actor wins the guarded transition first.
        assert (
            MissionRecordRepository(engine).transition_commission_state(
                commission.commission_id, "satisfied"
            )
            is True
        )
        moved = satisfy_commission_fail_open(
            MissionRecordService(engine), commission.commission_id
        )
        assert moved is False  # rowcount 0 — counted, not retried
        assert _commission_row(engine, commission.commission_id).state == (
            "satisfied"
        )

    def test_a_records_side_failure_never_raises(self, engine, monkeypatch):
        commission = _seed_commission(engine, worker_id=_seed(engine, "w-1"))

        def _boom(_cid, *, session=None):
            raise RuntimeError("records store is down")

        monkeypatch.setattr(
            MissionRecordRepository, "transition_commission_state", _boom
        )
        moved = satisfy_commission_fail_open(
            MissionRecordService(engine), commission.commission_id
        )
        assert moved is False  # fail-open, no raise

    def test_mode_off_and_none_inputs_are_false(self, engine, monkeypatch):
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "off")
        _reset_mission_records_mode_for_tests()
        assert (
            satisfy_commission_fail_open(
                MissionRecordService(engine), "any-id"
            )
            is False
        )
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "enforce")
        _reset_mission_records_mode_for_tests()
        assert satisfy_commission_fail_open(None, "any-id") is False
        assert satisfy_commission_fail_open(
            MissionRecordService(engine), None
        ) is False


class TestDeliveryAdjacency:
    def test_a_pending_message_skip_never_satisfies(
        self, engine, enforce_mode
    ):
        """No delivery ⇒ no satisfy: the pending-messages skip writes a
        DEFERRED marker and leaves the commission untouched."""
        _seed(engine, PARENT)
        child = _seed(engine, f"child-{uuid.uuid4().hex[:8]}", parent=PARENT)
        commission = _seed_commission(engine, worker_id=child)
        with Session(engine) as session:
            session.add(
                MessageQueue(
                    instance_id=child,
                    content="",
                    source="http",
                    type=MessageType.HUMAN.value,
                    status=MessageStatus.PROCESSING.value,
                )
            )
            session.commit()

        result = _service(engine)._process_child_completion_db_sync(
            instance_id=child,
            completed_message_id="completed-1",
            last_content="done",
        )
        assert result.outcome == "idempotency_skip"
        assert _commission_row(engine, commission.commission_id).state == (
            "outstanding"
        )
