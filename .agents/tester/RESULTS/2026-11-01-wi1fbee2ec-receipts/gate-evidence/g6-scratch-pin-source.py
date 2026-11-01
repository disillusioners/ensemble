"""wi-1fbee2ec G6 — TEMPORARY scratch pin (tester functional gate; DELETED
after the run — this file is NOT part of the permanent pin set; its
permanent commit is routed to the dev lane).

REQUIRED SHAPE (routed from tidier): a ``MISSION_READ_UNKNOWN`` (-1)
sentinel on the commissions leg (leg 2) CO-OCCURRING with a GENUINE
report-obligation read raise on the emission leg 6 of
``child_reports._root_completion_gate``. The sentinel's fail-OPEN
(byte-frozen polarity) must NOT discard the genuine D1 error into a
verify-clean verdict — the gate must HOLD.

DISCRIMINATOR: ``_gate_wedge_resolver`` is stubbed to return
``(True, None, False)`` — the verify-clean verdict. If the sentinel
path (or anything else) short-circuits past leg 6, the gate surfaces
verify-clean and these pins go RED (the hole proven). If leg 6's
isolated D1 catch fires, the gate returns
``(False, "outstanding_report_obligations=unknown(read_error)", False)``
— GREEN.

Fixture pattern mirrors ``test_report_obligation_emission_pins.py``
(file-backed SQLite, real ``ChildReportsService``, REAL commissions
helper ``_count_outstanding_mission_commissions`` driven through the
manager facade at its documented contract boundary — the facade
constructs the sentinel itself per Proposal B PR2 fail-open).
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.services.child_reports import ChildReportsService
from daemon.services.mission_record_reads import MISSION_READ_UNKNOWN
from daemon.write_pause_guard import WritePauseGuard

ROOT = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def engine(tmp_path):
    """File-backed SQLite — same isolation rationale as the emission
    pins pack (each session gets its own connection)."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/g6-scratch.db",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def insert_instance(engine, instance_id: str, *, status: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="worker",
                agent_dir="agents/worker",
                status=status,
                parent_id=None,
                version=2,
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()


def make_gate_service(engine, *, commissions_facade, obligation_facade):
    """The gate with every sibling leg QUIET except the two inputs under
    test. The commissions helper is left REAL (exercises its own
    sentinel construction / catch); the facade is driven at its
    contract boundary. The wedge resolver returns verify-clean — any
    fall-through past leg 6 is immediately visible."""
    manager = MagicMock()
    manager.engine = engine
    manager._engine = engine
    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager.write_guard = WritePauseGuard()
    manager._deferred_question_pause = set()
    manager.count_outstanding_commissions = commissions_facade
    manager.count_outstanding_report_obligations = obligation_facade
    service = ChildReportsService(manager=manager)
    service._bus_count_pending_for_target_sync = lambda iid: 0
    service._gate_wedge_resolver = AsyncMock(return_value=(True, None, False))
    return service


def _raise_obligation(_iid):
    raise RuntimeError("obligation read exploded (G6 genuine error)")


class TestG6UnknownSentinelCoPresentWithObligationError:
    @pytest.mark.asyncio
    async def test_g6_sentinel_plus_genuine_obligation_error_holds(
        self, engine,
    ):
        """THE G6 shape: the facade constructs the MISSION_READ_UNKNOWN
        sentinel (its documented fail-open) AND the obligation facade
        genuinely raises → the gate must HOLD (D1), never verify-clean
        through the wedge resolver."""
        insert_instance(engine, ROOT, status=InstanceStatus.COMPLETED.value)
        service = make_gate_service(
            engine,
            commissions_facade=lambda iid: MISSION_READ_UNKNOWN,
            obligation_facade=_raise_obligation,
        )
        allowed, reason, declared = await service._root_completion_gate(
            None, ROOT
        )
        assert allowed is False, (
            "G6 HOLE: the MISSION_READ_UNKNOWN sentinel discarded the "
            "genuine obligation read error into a verify-clean verdict"
        )
        assert reason == (
            "outstanding_report_obligations=unknown(read_error)"
        )
        assert declared is False
        # The gate never fell past leg 6 to the (verify-clean) resolver.
        service._gate_wedge_resolver.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_g6_faclade_raise_variant_same_hold(self, engine):
        """Variant: the commissions facade RAISES instead of returning
        the sentinel — the REAL helper's own catch constructs the same
        MISSION_READ_UNKNOWN sentinel and the gate must hold
        identically (both production constructions of the sentinel)."""
        insert_instance(engine, ROOT, status=InstanceStatus.COMPLETED.value)

        def _raise_commissions(_iid):
            raise RuntimeError("commissions read exploded")

        service = make_gate_service(
            engine,
            commissions_facade=_raise_commissions,
            obligation_facade=_raise_obligation,
        )
        allowed, reason, declared = await service._root_completion_gate(
            None, ROOT
        )
        assert allowed is False
        assert reason == (
            "outstanding_report_obligations=unknown(read_error)"
        )
        assert declared is False
        service._gate_wedge_resolver.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_g6_control_same_arrangement_quiet_obligation_verifies_clean(
        self, engine,
    ):
        """NON-VACUITY control: the SAME fixture arrangement with a
        QUIET obligation read (facade returns 0) must reach the wedge
        resolver and verify-clean — proving the HOLDs above are
        attributable to leg 6's D1 catch, not to a stub artifact (a
        test that could not produce verify-clean could not prove the
        hole either)."""
        insert_instance(engine, ROOT, status=InstanceStatus.COMPLETED.value)
        service = make_gate_service(
            engine,
            commissions_facade=lambda iid: MISSION_READ_UNKNOWN,
            obligation_facade=lambda iid: 0,
        )
        allowed, reason, _ = await service._root_completion_gate(None, ROOT)
        assert allowed is True
        assert reason is None
        service._gate_wedge_resolver.assert_awaited_once()
