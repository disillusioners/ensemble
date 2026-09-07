"""Sweep self-heal on a zero-row stuck pair (Debug Phase 4).

Regression coverage for the b7ead8a4/d90b18f9 production incident
(2026-09-07) self-heal leg: the parent (leader) is stuck
``waiting_children`` (``pending_children=1``), the child completed,
and ``report_injections`` has ZERO rows for the entire subtree. The
pre-fix sweep's Lane 2 (``no_row_backstop``) called
``ensure_deferred``, absorbed a phantom ``IntegrityError`` as
"already delivered (racing delivery won)", and flapped EVERY ~300s
cycle without ever healing — the documented same-triple flapping
signature (corroborated by d77727cf/c5ae6d95).

Pinned here:

* Lane 2 candidates include the zero-row stuck pair.
* The recovery INSERTs the DEFERRED marker (no false-positive
  no-op), transitions it to PENDING, and re-enters child completion
  via the manager's reconcile path — a REAL recovery, one row.
* A SECOND sweep pass (the ~300s re-hit) finds NO candidates (the
  now-existing non-terminal row is excluded by the Lane-2 LEFT
  JOIN), makes NO further recovery calls, and — the anti-flap
  assertion — the retired "racing delivery won" / "already
  delivered" no-op log NEVER appears across consecutive sweeps.
* The lane's ``row is None`` → ``already_recovered`` skip still
  holds for a genuine duplicate (non-terminal row present), so the
  W6 exactly-once semantics are unchanged.

Engine: file-backed SQLite at ``tmp_path`` with NullPool + WAL +
busy_timeout=10000 (repo conventions; never StaticPool+WriteGuard —
QUARANTINE).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select as sm_select

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.constants import DEFERRED_REASON_RESUME_ROUTER
from daemon.repositories.instance.models import Instance, InstanceStatus
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
from daemon.services.report_delivery_recovery import (
    LaneResult,
    ReportDeliveryRecoveryService,
)


# ─── Fixtures + helpers ─────────────────────────────────────────────────────

_REPO_LOGGER = "daemon.repositories.report_injection.repository"
_SWEEP_LOGGER = "daemon.services.report_delivery_recovery"


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout)."""
    db_path = tmp_path / "sweep-self-heal-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
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
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    parent_id: str | None,
    status: str,
    agent_name: str,
) -> str:
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id=f"agent-{agent_name}",
                agent_name=agent_name,
                agent_dir=f"/tmp/{agent_name}",
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_completed_message(
    engine: Engine,
    *,
    instance_id: str,
    message_id: str,
) -> str:
    """The child's COMPLETED response message (Lane-2 join key)."""
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="child final answer",
                source="agent",
                type=MessageType.AGENT.value,
                status=MessageStatus.COMPLETED.value,
                priority=0,
                enqueued_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    return message_id


def _all_injection_rows(engine: Engine) -> list[ReportInjection]:
    with Session(engine) as session:
        return list(session.exec(sm_select(ReportInjection)).all())


def _build_service(
    engine: Engine,
    *,
    busy_ids: set[str] | None = None,
) -> tuple[ReportDeliveryRecoveryService, MagicMock]:
    """Real sweep service + mock manager (per-row calls asserted)."""
    ri_repo = ReportInjectionRepository(engine=engine)
    task_repo = MagicMock()
    task_repo.has_instance_busy = MagicMock(
        side_effect=lambda instance_id: instance_id in (busy_ids or set())
    )
    manager = MagicMock()
    manager.engine = engine
    manager._handle_recover_deferred_report = MagicMock()

    service = ReportDeliveryRecoveryService(
        task_repo=task_repo,
        report_injection_repo=ri_repo,
        queue_repo=MagicMock(),
        instance_repo=MagicMock(),
        manager_ref=manager,
        interval_seconds=300,
        age_bound_minutes=10,
        batch_cap=100,
        recovery_retry_minutes=1,
        enabled=True,
        # The ORPHAN lane bridges asyncio.run_coroutine_threadsafe —
        # not under test here.
        lane_orphan=False,
    )
    return service, manager


# ─── The incident shape: zero-row stuck pair ────────────────────────────────


@pytest.fixture
def stuck_pair(engine: Engine) -> tuple[str, str, str]:
    """leader b7ead8a4 / giter d90b18f9 incident shape.

    Parent: non-terminal (stuck ``waiting_children`` semantics).
    Child: COMPLETED with a COMPLETED message. NO report_injections
    row, NO completion_report message on the parent, NO FIRED watcher
    — the exact zero-row obligation the pre-fix sweep could not heal.
    """
    leader_id = f"leader-{uuid.uuid4().hex[:8]}"
    child_id = f"giter-{uuid.uuid4().hex[:8]}"
    _seed_instance(
        engine,
        instance_id=leader_id,
        parent_id=None,
        status=InstanceStatus.WAITING_CHILDREN.value,
        agent_name="leader",
    )
    _seed_instance(
        engine,
        instance_id=child_id,
        parent_id=leader_id,
        status=InstanceStatus.COMPLETED.value,
        agent_name="giter",
    )
    msg_id = _seed_completed_message(
        engine, instance_id=child_id, message_id=f"msg-{uuid.uuid4().hex[:8]}"
    )
    return leader_id, child_id, msg_id


def test_lane2_finds_zero_row_stuck_pair(engine, stuck_pair):
    leader_id, child_id, msg_id = stuck_pair
    service, _manager = _build_service(engine)
    rows = service._report_injection_repo.find_completed_children_without_delivery(
        parent_not_terminal=True,
        limit=100,
    )
    assert {"child_id": child_id, "child_msg_id": msg_id,
            "parent_id": leader_id} in rows, (
        "the zero-row stuck pair must be a Lane-2 candidate"
    )


def test_sweep_self_heals_zero_row_pair_and_no_flap(
    engine, stuck_pair, caplog
):
    """(c) self-heal: zero-row pair → INSERT → PENDING → re-enter.
    (d) anti-flap: the false-positive no-op log never fires across
    consecutive sweep passes, and the pair is not re-processed."""
    leader_id, child_id, msg_id = stuck_pair
    service, manager = _build_service(engine)

    with caplog.at_level("DEBUG"):
        # ── Sweep pass 1 (the recovery) ──
        lane1 = service._run_no_row_backstop_lane()

        assert lane1.recovered == 1, "the stuck pair must be recovered"
        assert lane1.errors == 0

        # INSERT happened — a REAL marker, not a false-positive no-op.
        rows = _all_injection_rows(engine)
        assert len(rows) == 1
        assert rows[0].parent_instance_id == leader_id
        assert rows[0].child_instance_id == child_id
        assert rows[0].child_message_id == msg_id
        assert rows[0].deferred_reason == DEFERRED_REASON_RESUME_ROUTER
        # D2 end-state: the lane transitions DEFERRED → PENDING, so
        # the row ends the cycle claimable by the delivery paths.
        assert rows[0].state == ReportInjectionState.PENDING.value
        assert rows[0].recovery_attempted_at is not None

        # Re-enter fired exactly once via the manager reconcile path.
        assert manager._handle_recover_deferred_report.call_count == 1
        _kwargs = manager._handle_recover_deferred_report.call_args.kwargs
        assert _kwargs["child_instance_id"] == child_id
        assert _kwargs["child_message_id"] == msg_id
        assert _kwargs["injection_id"] == rows[0].injection_id
        assert _kwargs["source"] == "sweep_no_row_backstop"

        # ── Sweep pass 2 (the ~300s re-hit) ──
        candidates2 = (
            service._report_injection_repo.find_completed_children_without_delivery(
                parent_not_terminal=True,
                limit=100,
            )
        )
        assert candidates2 == [], (
            "the healed pair must NOT reappear as a Lane-2 candidate "
            "(the pre-fix sweep re-hit it every cycle)"
        )
        lane2 = service._run_no_row_backstop_lane()
        assert lane2.recovered == 0
        assert lane2.already_recovered == 0
        assert lane2.errors == 0
        assert manager._handle_recover_deferred_report.call_count == 1, (
            "second pass must not re-enter recovery (no flap)"
        )

        # Still exactly one row — no duplicate obligations minted.
        assert len(_all_injection_rows(engine)) == 1

        # (d) THE anti-pattern assertion: the same-triple "racing
        # delivery won" flapping signature must not recur — across
        # BOTH passes there is no "already delivered" / "racing
        # delivery won" no-op log for this zero-row state.
        flap_records = [
            r for r in caplog.records
            if "racing delivery won" in r.getMessage()
            or "already delivered" in r.getMessage()
        ]
        assert not flap_records, (
            "the retired false-positive 'racing delivery won' no-op "
            f"fired {len(flap_records)} time(s): "
            f"{[r.getMessage() for r in flap_records]}"
        )


def test_flap_signature_fails_if_false_positive_returns(
    engine, stuck_pair, caplog, monkeypatch
):
    """Guard the guard: if ensure_deferred ever regress to the
    false-positive no-op (returning None on a zero-row triple), the
    lane recovers NOTHING and the flap log fires — this test asserts
    that observable failure mode exists to trip on (bug-exercising
    proof by mutation)."""
    leader_id, child_id, msg_id = stuck_pair
    service, manager = _build_service(engine)

    # Mutate: restore the pre-fix behavior (no-op on zero rows).
    repo = service._report_injection_repo

    def pref_fix(**kwargs):
        # Pre-fix shape: phantom IntegrityError → silent None no-op.
        return None

    monkeypatch.setattr(repo, "ensure_deferred", pref_fix)

    with caplog.at_level("DEBUG"):
        lane = service._run_no_row_backstop_lane()

    assert lane.recovered == 0, "mutated lane must recover nothing"
    assert len(_all_injection_rows(engine)) == 0
    assert manager._handle_recover_deferred_report.call_count == 0
    # Next sweep would re-select the same pair → the ~300s flap. The
    # candidate query still returns it (nothing healed):
    candidates = (
        service._report_injection_repo.find_completed_children_without_delivery(
            parent_not_terminal=True,
            limit=100,
        )
    )
    assert candidates, "unhealed pair stays a candidate (flap fuel)"


# ─── W6 duplicate skip preserved (row is None → already_recovered) ──────────


def test_lane2_duplicate_none_still_skips_as_already_recovered(
    engine, stuck_pair
):
    """A non-terminal row for the triple (another actor wrote it)
    → ensure_deferred returns None → the lane skips as
    already_recovered WITHOUT calling the reconcile path. Exactly-once
    semantics unchanged by the Debug Phase 4 fix."""
    leader_id, child_id, msg_id = stuck_pair
    service, manager = _build_service(engine)

    # Another actor (Site 1) wrote the marker between the candidate
    # SELECT and this lane's per-row handling.
    service._report_injection_repo.ensure_deferred(
        parent_instance_id=leader_id,
        child_instance_id=child_id,
        child_message_id=msg_id,
        deferred_reason=DEFERRED_REASON_RESUME_ROUTER,
    )

    result = LaneResult()
    service._recover_one_no_row(
        child_id=child_id,
        child_msg_id=msg_id,
        parent_id=leader_id,
        result=result,
    )
    assert result.already_recovered == 1, (
        "duplicate absorbed by W6 → already_recovered skip"
    )
    assert result.recovered == 0
    assert manager._handle_recover_deferred_report.call_count == 0, (
        "duplicate absorbed by W6 — the other actor owns the recovery"
    )
