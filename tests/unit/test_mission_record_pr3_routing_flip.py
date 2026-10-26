"""Unit tests — the PR3 D1 ROUTING FLIP + the C3 cross-parent firewall.

Proposal B §3 D1, the final PR: routing authority moves from the
A-resolver (the audit-recency heuristic) to the mission_commissions
lookup —

  LEG 1 (enforce only): the child's OLDEST OUTSTANDING commission row
  (``worker_instance_id == child``, ``advisory_backfill = False`` —
  excluded from routing authority FOREVER) → ``commissioner_instance_id``
  (ruling ④: the oldest-outstanding tie-break);
  LEG 2: the audit-recency heuristic — the DECLARED LEGACY FALLBACK;
  LEG 3: the structural parent.

The flip lives INSIDE ``resolve_report_recipient`` (the design's "teach
the RESOLUTION, not the downstream guards"): every production call site
(child_reports producer, pipeline Site-1 marker, RDRS lane 2) inherits
it with zero call-site churn, and its recipient-aware guards keep
reading the resolved recipient.

THE C3 BACKSTOP (the duplication firewall, same-PR mandate): the
no-row backstop must see a mission-routed delivery as DELIVERED — the
admission predicate is CHILD-KEYED (since 4aa1bb690), so a
commissioner-addressed report + its injection row exclude the child
from ``find_completed_children_without_delivery`` and no second
obligation is minted for the structural parent. The focused test pins
the cross-parent shape end-to-end.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.instance_ops.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.mission_record.repository import (
    MissionRecordRepository,
)
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.report_injection.models import ReportInjection
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.services.mission_record_service import (
    MISSION_RECORDS_MODE_ENV,
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
)
from daemon.services.report_recipient import (
    resolve_report_recipient,
    resolve_report_recipient_with_commission,
)

CHILD = "child-0001"
STRUCTURAL_PARENT = "parent-structural"
COMMISSIONER = "commissioner-0001"
COMMISSIONER_2 = "commissioner-0002"


@pytest.fixture(autouse=True)
def _reset_mode_cache():
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'pr3-routing.sqlite'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def enforce_mode(monkeypatch):
    monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "enforce")
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


def _seed_instance(
    engine,
    instance_id,
    status=InstanceStatus.RUNNING.value,
    parent=None,
):
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


def _seed_base(engine):
    _seed_instance(engine, CHILD, parent=STRUCTURAL_PARENT)
    _seed_instance(engine, STRUCTURAL_PARENT)
    _seed_instance(engine, COMMISSIONER)


def _seed_commission(
    engine,
    *,
    commissioner=COMMISSIONER,
    worker=CHILD,
    lane="send_message",
    advisory=False,
    created_at=None,
    state="outstanding",
):
    service = MissionRecordService(engine)
    resolution = service.resolve_or_mint(commissioner, "user_api")
    return service.mint_commission(
        resolution.mission_id,
        commissioner_instance_id=commissioner,
        worker_instance_id=worker,
        lane=lane,
        advisory_backfill=advisory,
    )


def _resolve(engine, **kwargs):
    return resolve_report_recipient(
        engine=engine,
        child_instance_id=CHILD,
        structural_parent_id=STRUCTURAL_PARENT,
        **kwargs,
    )


class TestTheRoutingFlip:
    def test_enforce_mode_routes_to_the_mission_commissioner(
        self, engine, enforce_mode
    ):
        """LEG 1: the outstanding commission routes the report to its
        commissioner — with NO nudge audit row at all (the shape the
        audit heuristic can never see)."""
        _seed_base(engine)
        commission = _seed_commission(engine)
        recipient, deciding = resolve_report_recipient_with_commission(
            engine=engine,
            child_instance_id=CHILD,
            structural_parent_id=STRUCTURAL_PARENT,
        )
        assert recipient == COMMISSIONER
        assert deciding is not None
        assert deciding.commission_id == commission.commission_id
        assert deciding.mission_id == commission.mission_id

    def test_dry_mode_keeps_the_legacy_resolution(
        self, engine, enforce_mode, monkeypatch
    ):
        """Zero behavior change outside ``enforce``: the commission row
        exists but the mode ladder keeps leg 1 INERT — the audit
        heuristic (leg 2) decides, byte-for-byte the PR1/PR2
        behavior."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "dry")
        _reset_mission_records_mode_for_tests()
        _seed_base(engine)
        _seed_commission(engine)
        recipient, deciding = resolve_report_recipient_with_commission(
            engine=engine,
            child_instance_id=CHILD,
            structural_parent_id=STRUCTURAL_PARENT,
        )
        # No audit nudge → the structural parent (the legacy answer).
        assert recipient == STRUCTURAL_PARENT
        assert deciding is None

    def test_advisory_backfill_rows_never_route(self, engine, enforce_mode):
        """The routing-authority rule, pinned on the flip: an advisory
        backfill commission is EXCLUDED FOREVER — the resolution falls
        through to the audit leg."""
        _seed_base(engine)
        _seed_commission(engine, advisory=True)
        recipient, deciding = resolve_report_recipient_with_commission(
            engine=engine,
            child_instance_id=CHILD,
            structural_parent_id=STRUCTURAL_PARENT,
        )
        assert recipient == STRUCTURAL_PARENT
        assert deciding is None

    def test_multi_commissioner_takes_the_oldest_outstanding(
        self, engine, enforce_mode
    ):
        """Ruling ④: the OLDEST-outstanding discriminator is the
        multi-commissioner tie-break."""
        _seed_base(engine)
        _seed_instance(engine, COMMISSIONER_2)
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        new = datetime.now(timezone.utc)
        _seed_commission(
            engine,
            commissioner=COMMISSIONER,
            created_at=old.isoformat(),
        )
        _seed_commission(
            engine,
            commissioner=COMMISSIONER_2,
            created_at=new.isoformat(),
        )
        recipient, deciding = resolve_report_recipient_with_commission(
            engine=engine,
            child_instance_id=CHILD,
            structural_parent_id=STRUCTURAL_PARENT,
        )
        assert recipient == COMMISSIONER
        assert deciding is not None
        assert deciding.commissioner_instance_id == COMMISSIONER

    def test_a_satisfied_commission_does_not_route(
        self, engine, enforce_mode
    ):
        """Only OUTSTANDING rows carry routing authority — a satisfied
        commission is retired; the audit leg decides."""
        _seed_base(engine)
        commission = _seed_commission(engine)
        assert (
            MissionRecordRepository(engine).transition_commission_state(
                commission.commission_id, "satisfied"
            )
            is True
        )
        recipient, deciding = resolve_report_recipient_with_commission(
            engine=engine,
            child_instance_id=CHILD,
            structural_parent_id=STRUCTURAL_PARENT,
        )
        assert recipient == STRUCTURAL_PARENT
        assert deciding is None

    def test_a_commission_leg_failure_falls_through_to_the_audit_leg(
        self, engine, enforce_mode, monkeypatch
    ):
        """FAIL-OPEN: a records-side failure never breaks delivery —
        leg 1 degrades to leg 2 (the audit heuristic)."""
        _seed_base(engine)
        _seed_commission(engine)

        def _boom(_worker, *, session=None):
            raise RuntimeError("records read is down")

        monkeypatch.setattr(
            MissionRecordRepository,
            "find_routing_commission_for_worker",
            _boom,
        )
        # No audit nudge → leg 3 (structural parent); NO raise.
        assert _resolve(engine) == STRUCTURAL_PARENT

    def test_the_audit_leg_still_wins_when_no_commission_exists(
        self, engine, enforce_mode
    ):
        """LEG 2 is intact under enforce: a fresh committed nudge (no
        commission row) routes to the nudger exactly as before."""
        _seed_base(engine)
        with Session(engine) as session:
            session.add(
                _audit_row()
            )
            session.commit()
        recipient, deciding = resolve_report_recipient_with_commission(
            engine=engine,
            child_instance_id=CHILD,
            structural_parent_id=STRUCTURAL_PARENT,
        )
        assert recipient == COMMISSIONER
        assert deciding is None


def _audit_row():
    from daemon.repositories.instance_ops.models import InstanceOpsAudit

    return InstanceOpsAudit(
        action="instance_nudge",
        target=CHILD,
        reason="r",
        dry_run=False,
        outcome="committed",
        created_by_instance_id=COMMISSIONER,
        created_by_agent_id="agent",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


class TestC3CrossParentFirewall:
    def test_a_mission_routed_delivery_is_delivered_no_cross_parent_duplicate(
        self, engine, enforce_mode
    ):
        """THE same-PR duplication firewall (proposal-b §3 D1 seam
        patch + §8 'Double delivery').

        The mission-routed shape end-to-end: the child completed; its
        report was DELIVERED to the COMMISSIONER (the queue row + the
        injection row both address the commissioner). The C3 no-row
        backstop must NOT admit the child — no second obligation for
        the structural parent — and the lane-2 resolution names the
        commissioner so its evidence guards would query the right
        queue."""
        _seed_base(engine)
        _seed_commission(engine)
        with Session(engine) as session:
            session.exec(
                update(Instance)
                .where(Instance.instance_id == CHILD)
                .values(status=InstanceStatus.COMPLETED.value)
            )
            # The delivered report: the queue row on the COMMISSIONER's
            # queue (the natural path's destination), child-keyed
            # source.
            session.add(
                MessageQueue(
                    instance_id=COMMISSIONER,
                    content="done",
                    source=f"internal_report:{CHILD}:anchor-1",
                    status="completed",
                )
            )
            # The injection row addressed to the commissioner (the
            # PR3 writer shape — mission provenance set).
            session.add(
                ReportInjection(
                    parent_instance_id=COMMISSIONER,
                    child_instance_id=CHILD,
                    child_message_id="anchor-1",
                    report_message_id="rmsg-1",
                    content="done",
                    mission_id="m-1",
                    commissioner_instance_id=COMMISSIONER,
                    state="INJECTED",
                )
            )
            session.commit()

        candidates = ReportInjectionRepository(
            engine
        ).find_completed_children_without_delivery(
            parent_not_terminal=True, limit=100
        )
        admitted = [c["child_id"] for c in candidates]
        assert CHILD not in admitted, (
            "the backstop re-admitted a mission-delivered child — the "
            "cross-parent double-delivery the same-PR firewall forbids"
        )

        # The resolution (lane 2's first act) names the commissioner:
        assert _resolve(engine) == COMMISSIONER

    def test_a_truly_undelivered_commissioned_child_is_still_admitted(
        self, engine, enforce_mode
    ):
        """The firewall is not over-tight: a commissioned child with NO
        delivery evidence anywhere is still a no-row obligation (lane 2
        heals it — addressed to the commissioner via the resolution)."""
        _seed_base(engine)
        _seed_commission(engine)
        with Session(engine) as session:
            session.exec(
                update(Instance)
                .where(Instance.instance_id == CHILD)
                .values(status=InstanceStatus.COMPLETED.value)
            )
            session.commit()
        candidates = ReportInjectionRepository(
            engine
        ).find_completed_children_without_delivery(
            parent_not_terminal=True, limit=100
        )
        admitted = [c["child_id"] for c in candidates]
        assert CHILD in admitted
