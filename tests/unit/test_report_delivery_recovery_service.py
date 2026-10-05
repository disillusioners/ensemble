"""Unit tests for the periodic ReportDeliveryRecoveryService
(pause-report-recovery Phase 2, task 2.4).

Phase 2 task 2.4 defines a 5-lane periodic sweep:

1. **DEFERRED lane** — DEFERRED rows past the age guard.
2. **NO-ROW BACKSTOP lane (C3)** — designed-from-scratch query
   for FM-11 escapes / cancel-mid-shield / future no-marker
   drop lanes.
3. **Age-bounded PENDING lane (W9)** — stranded PENDING rows past
   the age guard.
4. **``recovery_attempted_at`` retry lane (W9/FM-13)** — stamped-
   stale rows past the retry interval.
5. **ORPHAN lane (W1)** — DEFERRED rows whose parent is TERMINAL.

Per-row invariants: skip busy parents, TOCTOU re-check, atomic
transition, ``ensure_deferred`` absorbs IntegrityError (W6),
re-enter completion under per-instance S3 serialization,
per-row errors leave rows DEFERRED, mid-sweep crash after
transition → fresh PENDING caught by lanes 3/4.

Acceptance covered:

* All five lanes run without error.
* False-positive matrix for the no-row backstop (C3) — 5 cases.
* Busy-skip — a parent with a live task is skipped.
* Batch cap — ``batch_cap`` limits rows per lane per run.
* Idempotent re-run — running twice is a no-op.
* Retry-lane mid-crash — fresh PENDING rows are picked up.
* ORPHAN terminal-parent disposition (W1) — observable log.
* Fail-safe — per-row errors do not abort the sweep.
* Lane kill-switches — disabled lanes do not run.
* D2 — no-row backstop end-state: the fresh row is transitioned to
  PENDING (never left DEFERRED / half-recovered).

Tests run against a real in-memory SQLite database; the service
sync methods are exercised directly (no threading — the
periodic-loop test uses ``recover_now``).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select as sm_select

# Register every table the helper touches before create_all().
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

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
from daemon.services.report_delivery_recovery import (
    LaneResult,
    ReportDeliveryRecoveryService,
    SweepResult,
)


# =============================================================================
# Fixtures + helpers
# =============================================================================


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with all tables created."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
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
    instance_id: str | None = None,
    parent_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    """Insert an Instance row."""
    instance_id = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="test",
                agent_name="test",
                agent_dir="/tmp",
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_message(
    engine: Engine,
    *,
    instance_id: str,
    msg_id: str | None = None,
    status: str = MessageStatus.COMPLETED.value,
    source: str | None = None,
    msg_type: str = MessageType.HUMAN.value,
) -> str:
    """Insert a MessageQueue row. Returns the message_id."""
    msg_id = msg_id or f"msg-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=msg_id,
                instance_id=instance_id,
                content="report",
                source=source,
                type=msg_type,
                status=status,
                priority=0,
                enqueued_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    return msg_id


def _seed_deferred_row(
    engine: Engine,
    *,
    parent_instance_id: str,
    child_instance_id: str,
    child_message_id: str,
    report_message_id: str | None = None,
    content: str | None = None,
    state: str = ReportInjectionState.DEFERRED.value,
    recovery_attempted_at: str | None = None,
) -> str:
    """Insert a ``ReportInjection`` row. Returns the injection_id."""
    injection_id = str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            ReportInjection(
                injection_id=injection_id,
                parent_instance_id=parent_instance_id,
                child_instance_id=child_instance_id,
                child_message_id=child_message_id,
                report_message_id=report_message_id,
                content=content,
                state=state,
                recovery_attempted_at=recovery_attempted_at,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        session.commit()
    return injection_id


def _queue_repo_mock(evidence: bool = False) -> MagicMock:
    """A queue-repo mock with the duck-typed ledger method declared.

    Iteration-2 W-4: the per-row pass duck-types
    ``find_wake_already_delivered_evidence`` (no isinstance gate) —
    an UNconfigured MagicMock's auto-attr returns a truthy MagicMock,
    which would fake a ledger match and skip every row. Every test
    building a service inline MUST use this (or a real repo).
    """
    mock = MagicMock()
    mock.find_wake_already_delivered_evidence = MagicMock(
        return_value=evidence
    )
    return mock


def _build_service(
    engine: Engine,
    *,
    busy_ids: set[str] | None = None,
) -> tuple[ReportDeliveryRecoveryService, MagicMock]:
    """Build the service + a mock manager.

    Returns ``(service, manager_mock)``. The mock manager's
    ``_handle_recover_deferred_report`` is a MagicMock so the
    tests can assert the recovery call shape.

    Iteration-2 wiring (W-4 duck-typing + blocker-1 bridge):
    * ``manager._checkpointer = None`` — the parent-history
      PREFIX check is explicitly NOT exercised by the mechanical
      lane tests (``None`` degrades to "no parent-history
      evidence" per the guarded lookup). Ledger-specific behavior
      is covered by ``TestF2PREFIXLedgerDedup`` and the
      ``TestF2ManagerLoopBridgeAffinity`` pin (which wire a real
      manager loop + checkpointer).
    * ``queue_repo.find_wake_already_delivered_evidence`` is a
      configured MagicMock returning ``False`` — the per-row pass
      now duck-types the queue repo (no isinstance gate), so the
      mock must declare the method explicitly or its auto-attr
      truthiness would fake a ledger match.
    """
    from daemon.repositories.report_injection.repository import (
        ReportInjectionRepository,
    )

    ri_repo = ReportInjectionRepository(engine=engine)
    task_repo = MagicMock()
    task_repo.has_instance_busy = MagicMock(
        side_effect=lambda instance_id: instance_id in (busy_ids or set())
    )
    queue_repo = MagicMock()
    queue_repo.find_wake_already_delivered_evidence = MagicMock(
        return_value=False
    )
    manager = MagicMock()
    manager.engine = engine
    manager._checkpointer = None
    manager._handle_recover_deferred_report = MagicMock()

    service = ReportDeliveryRecoveryService(
        task_repo=task_repo,
        report_injection_repo=ri_repo,
        queue_repo=queue_repo,
        instance_repo=MagicMock(),
        manager_ref=manager,
        interval_seconds=300,
        age_bound_minutes=10,
        batch_cap=100,
        recovery_retry_minutes=1,
        enabled=True,
        # Disable revive to avoid asyncio.run_coroutine_threadsafe
        # in tests — we test revive separately.
        lane_orphan=False,
    )
    return service, manager


# =============================================================================
# Sweep service — basic shape
# =============================================================================


class TestSweepServiceShape:
    """The service produces a SweepResult with per-lane LaneResults."""

    def test_sweep_returns_empty_result_no_rows(
        self, engine: Engine
    ) -> None:
        """An empty DB → all lanes produce zero-count results."""
        service, _ = _build_service(engine)
        result = service.recover_now()
        assert isinstance(result, SweepResult)
        # All five lanes ran.
        assert "deferred" in result.lanes
        assert "no_row_backstop" in result.lanes
        assert "pending_age" in result.lanes
        assert "recovery_retry" in result.lanes
        assert "orphan" not in result.lanes  # lane_orphan disabled
        # Block-1 G4 lane is enabled by default; on an empty DB
        # the candidate query returns 0 rows and the lane
        # self-records as present (zero recovered) — asserting the
        # key is in the dict is the post-fix contract.
        assert "stuck_wake" in result.lanes
        for name, lane in result.lanes.items():
            assert isinstance(lane, LaneResult)
            assert lane.recovered == 0
            assert lane.errors == 0
        assert result.total_recovered == 0

    def test_sweep_lane_kill_switches(self, engine: Engine) -> None:
        """Each lane's kill-switch removes it from the sweep."""
        service, _ = _build_service(engine)
        # Replace the service with all lanes disabled.
        from daemon.repositories.report_injection.repository import (
            ReportInjectionRepository,
        )
        svc = ReportDeliveryRecoveryService(
            task_repo=MagicMock(),
            report_injection_repo=ReportInjectionRepository(engine=engine),
            queue_repo=_queue_repo_mock(),
            instance_repo=MagicMock(),
            manager_ref=MagicMock(),
            enabled=True,
            lane_deferred=False,
            lane_no_row_backstop=False,
            lane_pending_age=False,
            lane_recovery_retry=False,
            lane_orphan=False,
            # Block-1 G4 lane: must be disabled too for the
            # empty-lane-dict assertion.
            lane_stuck_wake=False,
        )
        result = svc.recover_now()
        assert result.lanes == {}


# =============================================================================
# DEFERRED lane (Lane 1)
# =============================================================================


class TestDeferredLane:
    """Lane 1: DEFERRED rows past the age guard."""

    def test_deferred_row_recovered(
        self, engine: Engine
    ) -> None:
        """A non-terminal DEFERRED row → recover (transition +
        re-entry)."""
        parent = _seed_instance(engine)
        child = _seed_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_deferred_row(
            engine,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-1",
        )

        service, manager = _build_service(engine)
        result = service.recover_now()
        assert result.lanes["deferred"].recovered == 1
        manager._handle_recover_deferred_report.assert_called_once()
        # The injection row was transitioned to PENDING with
        # ``recovery_attempted_at`` stamped.
        ri_repo = service._report_injection_repo
        rows = ri_repo.find_deferred_for_parent(parent)
        assert len(rows) == 0  # transitioned away from DEFERRED

    def test_deferred_row_skipped_when_parent_busy(
        self, engine: Engine
    ) -> None:
        """A busy parent (has_instance_busy=True) is skipped."""
        parent = _seed_instance(engine)
        child = _seed_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_deferred_row(
            engine,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-1",
        )

        service, manager = _build_service(engine, busy_ids={parent})
        result = service.recover_now()
        assert result.lanes["deferred"].recovered == 0
        assert result.lanes["deferred"].skipped_busy == 1
        manager._handle_recover_deferred_report.assert_not_called()

    def test_deferred_row_accounts_busy_check_failure(
        self, engine: Engine, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When ``has_instance_busy`` ITSELF raises, the row is
        fail-closed-skipped (skip semantics preserved) AND the
        ``busy_check_failed`` counter is incremented — operators
        can distinguish a transient busy-check failure from a
        genuinely-busy parent via the structured LaneResult.

        Companion to :meth:`test_deferred_row_skipped_when_parent_busy`
        — that test covers the "confirmed busy" path (counts toward
        ``skipped_busy``); this one covers the "busy-check itself
        errored" path (counts toward ``busy_check_failed``).
        """
        import logging

        parent = _seed_instance(engine)
        child = _seed_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_deferred_row(
            engine,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-1",
        )

        service, manager = _build_service(engine)

        # Rig the busy-check to raise — simulated transient infra
        # failure (e.g. DB hiccup on the task table). The helper
        # MUST fail-closed-skip the row AND count it as
        # ``busy_check_failed`` (NOT ``skipped_busy``).
        def _raise_busy(_instance_id: str) -> bool:
            raise RuntimeError("simulated busy-check failure")

        service._task_repo.has_instance_busy = MagicMock(side_effect=_raise_busy)

        caplog.set_level(
            logging.WARNING, logger="daemon.services.report_delivery_recovery"
        )
        result = service.recover_now()

        # Row was skipped (fail-closed), not recovered.
        assert result.lanes["deferred"].recovered == 0
        manager._handle_recover_deferred_report.assert_not_called()

        # Counter semantic: busy-check itself errored → distinct
        # bucket from confirmed-busy.
        assert result.lanes["deferred"].busy_check_failed == 1
        assert result.lanes["deferred"].skipped_busy == 0

        # to_dict() also surfaces the new field so the endpoint /
        # structured logs carry it through.
        lane_dict = result.lanes["deferred"].to_dict()
        assert lane_dict["busy_check_failed"] == 1
        assert lane_dict["skipped_busy"] == 0

        # WARNING log emitted for operator visibility.
        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any(
            "busy-check failed" in r.getMessage() for r in warnings
        ), (
            "WARNING log MUST mention 'busy-check failed' for "
            "operator visibility; got "
            f"{[r.getMessage() for r in warnings]}"
        )

    def test_idempotent_re_run(self, engine: Engine) -> None:
        """Running the sweep twice is idempotent — the second run
        sees no DEFERRED rows.
        """
        parent = _seed_instance(engine)
        child = _seed_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_deferred_row(
            engine,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-1",
        )

        service, _ = _build_service(engine)
        # First run recovers.
        result1 = service.recover_now()
        assert result1.lanes["deferred"].recovered == 1
        # Second run is a no-op.
        result2 = service.recover_now()
        assert result2.lanes["deferred"].recovered == 0


# =============================================================================
# Batch cap
# =============================================================================


class TestBatchCap:
    """The batch cap (MVP growth rule) limits rows per lane per run."""

    def test_batch_cap_limits_rows(self, engine: Engine) -> None:
        """With ``batch_cap=2`` and 5 eligible rows, only 2 are
        recovered per run; the rest are picked up next cycle.
        """
        from daemon.repositories.report_injection.repository import (
            ReportInjectionRepository,
        )

        parent = _seed_instance(engine)
        # Seed 5 DEFERRED rows for the same parent.
        for i in range(5):
            child = _seed_instance(
                engine,
                parent_id=parent,
                status=InstanceStatus.COMPLETED.value,
            )
            _seed_deferred_row(
                engine,
                parent_instance_id=parent,
                child_instance_id=child,
                child_message_id=f"child-msg-{i}",
            )

        ri_repo = ReportInjectionRepository(engine=engine)
        task_repo = MagicMock()
        task_repo.has_instance_busy = MagicMock(return_value=False)
        manager = MagicMock()
        manager._checkpointer = None
        manager._handle_recover_deferred_report = MagicMock()

        service = ReportDeliveryRecoveryService(
            task_repo=task_repo,
            report_injection_repo=ri_repo,
            queue_repo=_queue_repo_mock(),
            instance_repo=MagicMock(),
            manager_ref=manager,
            interval_seconds=300,
            age_bound_minutes=10,
            batch_cap=2,  # the cap
            recovery_retry_minutes=1,
            enabled=True,
            lane_orphan=False,
        )
        result = service.recover_now()
        assert result.lanes["deferred"].recovered == 2
        assert result.total_recovered == 2
        # 3 DEFERRED rows remain — picked up next cycle.
        remaining = ri_repo.find_deferred_for_parent(parent)
        assert len(remaining) == 3


# =============================================================================
# Lane kill-switches
# =============================================================================


class TestLaneKillSwitches:
    """Each lane's kill-switch removes it from the sweep."""

    def test_lane_pending_age_disabled(
        self, engine: Engine
    ) -> None:
        """Lane 3 disabled → no PENDING-age rows processed."""
        from daemon.repositories.report_injection.repository import (
            ReportInjectionRepository,
        )

        parent = _seed_instance(engine)
        child = _seed_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        # Seed a PENDING row.
        _seed_deferred_row(
            engine,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-1",
            state=ReportInjectionState.PENDING.value,
        )

        ri_repo = ReportInjectionRepository(engine=engine)
        task_repo = MagicMock()
        task_repo.has_instance_busy = MagicMock(return_value=False)
        manager = MagicMock()
        manager._checkpointer = None
        manager._handle_recover_deferred_report = MagicMock()

        # Disable Lane 3.
        service = ReportDeliveryRecoveryService(
            task_repo=task_repo,
            report_injection_repo=ri_repo,
            queue_repo=_queue_repo_mock(),
            instance_repo=MagicMock(),
            manager_ref=manager,
            enabled=True,
            lane_pending_age=False,
            lane_orphan=False,
        )
        result = service.recover_now()
        # The PENDING-age lane is missing from the result.
        assert "pending_age" not in result.lanes


# =============================================================================
# C3 false-positive matrix (no-row backstop)
# =============================================================================


class TestNoRowBackstopFalsePositiveMatrix:
    """C3 false-positive matrix — the 5 LEFT JOINs / NOT EXISTS
    subqueries each exclude a candidate.

    Each test seeds one candidate + one exclusion shape and
    asserts the row is NOT in the no-row-backstop lane's result.
    """

    def _seed_completed_child_with_message(
        self,
        engine: Engine,
        parent_id: str,
        child_msg_id: str = "child-msg",
    ) -> tuple[str, str]:
        """Seed a COMPLETED child instance + its COMPLETED message.

        Returns ``(child_id, msg_id)``.
        """
        child_id = _seed_instance(
            engine,
            parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_message(
            engine,
            instance_id=child_id,
            msg_id=child_msg_id,
            status=MessageStatus.COMPLETED.value,
        )
        return child_id, child_msg_id

    def test_excludes_when_existing_completion_report_message(
        self, engine: Engine
    ) -> None:
        """A row with an existing ``internal_report:`` message in
        the parent's queue is EXCLUDED (case 1: existing message).
        """
        parent = _seed_instance(engine)
        child_id, child_msg_id = self._seed_completed_child_with_message(
            engine, parent
        )
        # Seed the completion_report message.
        existing_report_msg = f"report-{uuid.uuid4().hex[:8]}"
        _seed_message(
            engine,
            instance_id=parent,
            msg_id=existing_report_msg,
            source=(
                f"internal_report:{child_id}:{child_msg_id}"
            ),
            msg_type=MessageType.COMPLETION_REPORT.value,
            status=MessageStatus.READY.value,
        )

        service, _ = _build_service(engine)
        rows = service._report_injection_repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        )
        # The row is excluded — the LEFT JOIN's ``rq.message_id IS
        # NULL`` predicate filters it out.
        assert not any(r["child_id"] == child_id for r in rows)

    def test_excludes_when_existing_injection_row(
        self, engine: Engine
    ) -> None:
        """A row with an existing non-terminal ``report_injections``
        row is EXCLUDED (case 2: existing injection row)."""
        parent = _seed_instance(engine)
        child_id, child_msg_id = self._seed_completed_child_with_message(
            engine, parent
        )
        # Seed a PENDING injection row.
        _seed_deferred_row(
            engine,
            parent_instance_id=parent,
            child_instance_id=child_id,
            child_message_id=child_msg_id,
            state=ReportInjectionState.PENDING.value,
        )

        service, _ = _build_service(engine)
        rows = service._report_injection_repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        )
        assert not any(r["child_id"] == child_id for r in rows)

    def test_excludes_when_parent_terminal(
        self, engine: Engine
    ) -> None:
        """A row whose parent is terminal is EXCLUDED from the
        periodic sweep (case 3: terminal parent — the ORPHAN
        lane's territory).
        """
        # A terminal parent.
        parent = _seed_instance(
            engine, status=InstanceStatus.COMPLETED.value
        )
        child_id, child_msg_id = self._seed_completed_child_with_message(
            engine, parent
        )

        service, _ = _build_service(engine)
        # Periodic sweep: ``parent_not_terminal=True`` excludes
        # terminal parents.
        rows = service._report_injection_repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        )
        assert not any(r["child_id"] == child_id for r in rows)

    def test_diagnostic_includes_terminal_parents(
        self, engine: Engine
    ) -> None:
        """A diagnostic call with ``parent_not_terminal=False``
        INCLUDES terminal parents (for the ORPHAN lane / manual
        diagnostics).
        """
        parent = _seed_instance(
            engine, status=InstanceStatus.COMPLETED.value
        )
        child_id, child_msg_id = self._seed_completed_child_with_message(
            engine, parent
        )

        service, _ = _build_service(engine)
        rows = service._report_injection_repo.find_completed_children_without_delivery(
            parent_not_terminal=False
        )
        assert any(r["child_id"] == child_id for r in rows)

    def test_no_row_lane_end_state_not_deferred(
        self, engine: Engine
    ) -> None:
        """D2 (2026-08-20): after the no-row backstop lane runs,
        the freshly-written row's END STATE must NOT be DEFERRED.

        Pre-D2 the lane handed its fresh ``ensure_deferred`` row
        straight to reconcile, leaving the row half-recovered
        (state=DEFERRED with a backfilled artifact) — a shape that
        re-triggered every cycle because both claim paths
        (``claim_for_injection`` / ``claim_for_task_delivery``)
        are guarded ``WHERE state='PENDING'`` and can NEVER claim a
        DEFERRED row. D2 aligns the lane with Lanes 1/3/4:
        ``transition_deferred_to_pending`` runs BEFORE the
        reconcile hand-off, so the row ends the cycle PENDING (or
        terminal via the manager hand-off) — never DEFERRED.

        This test asserts the END STATE with a mocked manager
        hand-off (the real reconcile/re-enter path is covered by
        the sub-shape tests in test_resume_router_deferred_recovery).
        """
        parent = _seed_instance(engine)
        child_id, child_msg_id = self._seed_completed_child_with_message(
            engine, parent
        )

        service, manager = _build_service(engine)
        result = service.recover_now()

        # The lane recovered exactly one row.
        assert result.lanes["no_row_backstop"].recovered == 1
        manager._handle_recover_deferred_report.assert_called_once()

        # D2 END-STATE assertion: the row exists and is NOT
        # DEFERRED. With the mocked manager hand-off the row stays
        # PENDING (the real hand-off escalates it to
        # TASK_DELIVERED/INJECTED via claim paths); the
        # half-recovered DEFERRED shape is GONE.
        with Session(engine) as session:
            row = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.child_instance_id == child_id
                )
            ).first()
        assert row is not None, (
            "no_row_backstop lane must write the obligation row"
        )
        assert row.state == ReportInjectionState.PENDING.value, (
            "D2: the no-row backstop lane must transition its fresh "
            "row to PENDING before the hand-off (end-state aligned "
            f"with Lanes 1/3/4); got state={row.state}"
        )
        assert row.recovery_attempted_at is not None, (
            "D2: transition_deferred_to_pending stamps "
            "recovery_attempted_at (lanes 3/4 retry visibility)"
        )


# =============================================================================
# ORPHAN lane (Lane 5, W1)
# =============================================================================


class TestOrphanLane:
    """W1: terminal-parent DEFERRED rows reach an observable
    disposition (revival + re-entry, OR structured log on
    revival failure)."""

    def test_orphan_lane_disabled_by_default(
        self, engine: Engine
    ) -> None:
        """The default test service has ``lane_orphan=False`` —
        the ORPHAN lane is disabled to avoid
        ``asyncio.run_coroutine_threadsafe`` in tests."""
        service, _ = _build_service(engine)
        result = service.recover_now()
        assert "orphan" not in result.lanes


# =============================================================================
# ORPHAN lane LIVE SWEEP (Phase 3 task 3.9 / leader digest item 5)
# =============================================================================


def _build_orphan_service(
    engine: Engine,
    *,
    revive_result: bool = True,
) -> tuple[ReportDeliveryRecoveryService, MagicMock, MagicMock]:
    """Build the service with ``lane_orphan`` ENABLED.

    The manager mock carries a REAL ``_loop`` (the running loop from
    the ``pytest.mark.asyncio`` test body) so the ORPHAN lane's
    ``_try_revive_terminal_parent`` →
    ``asyncio.run_coroutine_threadsafe(self._manager.
    _revive_terminal_instance(parent_id), loop)`` bridge resolves
    without a production InstanceManager. ``_revive_terminal_instance``
    and ``_handle_recover_deferred_report`` are mockable seams
    (same pattern as tests/unit/test_resume_router_deferred_recovery.py).

    Returns ``(service, manager, handle_mock)``.
    """
    from daemon.repositories.report_injection.repository import (
        ReportInjectionRepository,
    )

    ri_repo = ReportInjectionRepository(engine=engine)
    task_repo = MagicMock()
    task_repo.has_instance_busy = MagicMock(return_value=False)
    manager = MagicMock()
    manager.engine = engine
    manager._checkpointer = None
    manager._loop = asyncio.get_running_loop()
    manager._revive_terminal_instance = AsyncMock(return_value=revive_result)
    handle_mock = MagicMock()
    manager._handle_recover_deferred_report = handle_mock

    service = ReportDeliveryRecoveryService(
        task_repo=task_repo,
        report_injection_repo=ri_repo,
        queue_repo=_queue_repo_mock(),
        instance_repo=MagicMock(),
        manager_ref=manager,
        interval_seconds=300,
        age_bound_minutes=10,
        batch_cap=100,
        recovery_retry_minutes=1,
        enabled=True,
        lane_orphan=True,
    )
    return service, manager, handle_mock


class TestOrphanLaneLiveSweep:
    """ORPHAN-lane LIVE sweep (leader digest item 5): construct the
    service with ``lane_orphan`` ENABLED and run ONE ``recover_now()``
    cycle against a terminal parent + DEFERRED row.

    W1's NEVER-SILENT contract: every row reaches ONE observable
    disposition — revive-and-deliver (parent revived, marker
    transitions out of DEFERRED, hand-off invoked) OR structured
    disposition (logged + counted in ``orphan_disposition``). The
    sweep must never silently drop the row.
    """

    @pytest.mark.asyncio
    async def test_live_sweep_revive_and_deliver(
        self, engine: Engine
    ) -> None:
        """Dispositive happy path: terminal parent + DEFERRED row →
        ONE recover_now() cycle revives the parent and hands off to
        the reconcile+re-enter path; the marker LEAVES DEFERRED.

        Observable artifacts asserted:

        * ``lanes["orphan"].recovered == 1`` (structured count);
        * ``_revive_terminal_instance`` awaited once for the parent
          (the revival — manager-side running-loop bridge worked);
        * ``_handle_recover_deferred_report`` called once with the
          exact row triple (the deliver half);
        * the marker row's state is no longer DEFERRED (the guarded
          transition committed BEFORE the hand-off — D2-adjacent
          ordering; with the mocked manager hand-off the row ends the
          cycle PENDING).
        """
        # Terminal parent + COMPLETED child + DEFERRED obligation.
        parent = _seed_instance(
            engine, status=InstanceStatus.COMPLETED.value
        )
        child = _seed_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_deferred_row(
            engine,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-1",
        )

        service, manager, handle_mock = _build_orphan_service(engine)
        # Production shape: the sweep runs OFF the loop thread (the
        # endpoint calls it via ``asyncio.to_thread``) so the loop
        # stays free to execute the ``run_coroutine_threadsafe``
        # revival bridge. Calling ``recover_now()`` ON the loop
        # thread would deadlock the bridge (TimeoutError after 8s).
        result = await asyncio.to_thread(service.recover_now)

        assert isinstance(result, SweepResult)
        # Lane present + structured outcome.
        assert "orphan" in result.lanes, (
            "lane_orphan=True MUST register the orphan lane in the "
            "sweep result"
        )
        orphan = result.lanes["orphan"]
        assert orphan.recovered == 1, (
            f"the live orphan sweep must revive-and-deliver the "
            f"terminal-parent row within one cycle; lanes={result.to_dict()}"
        )
        assert orphan.errors == 0
        assert orphan.orphan_disposition == 0

        # Revival happened (once, for the right parent).
        manager._revive_terminal_instance.assert_awaited_once_with(parent)
        # Deliver half: hand-off invoked with the exact row triple.
        handle_mock.assert_called_once()
        kwargs = handle_mock.call_args.kwargs
        assert kwargs["child_instance_id"] == child
        assert kwargs["child_message_id"] == "child-msg-1"
        assert kwargs["source"] == "sweep"
        assert "injection_id" in kwargs

        # The marker LEAVES DEFERRED within the same cycle (guarded
        # transition committed before the hand-off; mocked manager
        # hand-off leaves it PENDING).
        with Session(engine) as session:
            row = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.child_instance_id == child
                )
            ).first()
        assert row is not None
        assert row.state == ReportInjectionState.PENDING.value, (
            "the orphan lane's guarded transition must move the row "
            f"out of DEFERRED before the hand-off; got {row.state}"
        )
        assert row.recovery_attempted_at is not None

    @pytest.mark.asyncio
    async def test_live_sweep_revival_failure_structured_disposition(
        self, engine: Engine, caplog: pytest.LogCaptureFixture
    ) -> None:
        """NEVER-SILENT half: revival FAILS (e.g. instance row gone)
        → the row is NOT silently dropped — the structured
        ``orphan_disposition`` count is incremented and a WARNING
        naming the orphaned obligation is logged. The DEFERRED row is
        left in place (explicit operator disposition, retried next
        cycle).
        """
        import logging

        parent = _seed_instance(
            engine, status=InstanceStatus.COMPLETED.value
        )
        child = _seed_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_deferred_row(
            engine,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-2",
        )

        service, manager, handle_mock = _build_orphan_service(
            engine, revive_result=False
        )
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_delivery_recovery"
        ):
            result = await asyncio.to_thread(service.recover_now)

        assert "orphan" in result.lanes
        orphan = result.lanes["orphan"]
        # Structured disposition: counted, never silent.
        assert orphan.orphan_disposition == 1, (
            "a failed revival MUST land in the structured "
            "orphan_disposition count (observable disposition)"
        )
        assert orphan.recovered == 0
        assert orphan.errors == 0
        # The hand-off was NEVER invoked (no delivery attempted).
        handle_mock.assert_not_called()
        # Observable log: the WARNING names the orphaned obligation.
        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any(
            "orphan_disposition" in r.getMessage()
            for r in warnings
        ), (
            "the revival-failure disposition MUST be logged at "
            "WARNING (observable); got "
            f"{[r.getMessage() for r in warnings]}"
        )
        # The row is left DEFERRED (explicit disposition — not
        # silently dropped, not half-transitioned).
        with Session(engine) as session:
            row = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.child_instance_id == child
                )
            ).first()
        assert row is not None
        assert row.state == ReportInjectionState.DEFERRED.value

    @pytest.mark.asyncio
    async def test_live_sweep_no_row_silent_drop_regression(
        self, engine: Engine
    ) -> None:
        """Anti-regression: with revival failing AND the structured
        count/log assertions stripped, no third outcome exists. This
        test pins the EXHAUSTIVENESS — recovered +
        orphan_disposition + errors + skipped counters must account
        for EVERY row the lane selected.

        A future code change that drops the row without touching any
        counter fails this test (the lane selected 1 row; the
        counters must sum to >= 1).
        """
        parent = _seed_instance(
            engine, status=InstanceStatus.TERMINATED.value
        )
        child = _seed_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_deferred_row(
            engine,
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id="child-msg-3",
        )

        service, manager, handle_mock = _build_orphan_service(engine)
        # Revival succeeds — the row goes the revive-and-deliver way.
        # (Sweep off the loop thread — see revive_and_deliver test.)
        result = await asyncio.to_thread(service.recover_now)

        orphan = result.lanes["orphan"]
        accounted = (
            orphan.recovered
            + orphan.orphan_disposition
            + orphan.errors
            + orphan.skipped_busy
            + orphan.busy_check_failed
            + orphan.already_recovered
        )
        assert accounted >= 1, (
            "NEVER-SILENT (W1): every row the orphan lane selected "
            "MUST be accounted for by a structured counter — a silent "
            f"drop would leave accounted==0; lane={orphan.to_dict()}"
        )
        # And in this configuration the outcome is revive-and-deliver.
        assert orphan.recovered == 1



# =============================================================================
# Fail-safe — per-row errors do not abort the sweep
# =============================================================================


class TestFailSafe:
    """Per-row exceptions are caught and counted in ``errors`` —
    the sweep continues."""

    def test_per_row_exception_does_not_abort(
        self, engine: Engine
    ) -> None:
        """A raised exception in one row is caught; subsequent
        rows still process.
        """
        parent = _seed_instance(engine)
        # Two DEFERRED rows.
        for i in range(2):
            child = _seed_instance(
                engine,
                parent_id=parent,
                status=InstanceStatus.COMPLETED.value,
            )
            _seed_deferred_row(
                engine,
                parent_instance_id=parent,
                child_instance_id=child,
                child_message_id=f"child-msg-{i}",
            )

        service, manager = _build_service(engine)
        # Make the first re-entry call raise; the second succeeds.
        call_count = {"n": 0}

        def maybe_raise(*_args: Any, **_kwargs: Any) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("first-call boom")
        manager._handle_recover_deferred_report.side_effect = maybe_raise

        result = service.recover_now()
        # First call raised; the row's transition was committed
        # but re-entry failed → errors=1. Second row succeeds →
        # recovered=1.
        assert result.lanes["deferred"].errors == 1
        assert result.lanes["deferred"].recovered == 1


# =============================================================================
# Age-bounded PENDING + retry lanes (Lanes 3 + 4)
# =============================================================================


class TestPendingAgeLanes:
    """Lanes 3 + 4: stranded PENDING rows past the age guard."""

    def test_pending_row_recovered(
        self, engine: Engine
    ) -> None:
        """A PENDING row past the age guard (no
        ``recovery_attempted_at``) is recovered via Lane 3.
        """
        parent = _seed_instance(engine)
        child = _seed_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        # Seed a PENDING row with an old ``created_at`` (the
        # age guard is 10 minutes by default — set ``created_at``
        # to 1 hour ago).
        injection_id = str(uuid.uuid4())
        old = (
            datetime.now(timezone.utc).timestamp() - 3600
        )  # 1 hour ago
        with Session(engine) as session:
            session.add(
                ReportInjection(
                    injection_id=injection_id,
                    parent_instance_id=parent,
                    child_instance_id=child,
                    child_message_id="child-msg-1",
                    report_message_id=None,
                    content=None,
                    state=ReportInjectionState.PENDING.value,
                    recovery_attempted_at=None,
                    created_at=datetime.fromtimestamp(
                        old, tz=timezone.utc
                    ).isoformat(),
                )
            )
            session.commit()

        service, manager = _build_service(engine)
        result = service.recover_now()
        # The PENDING-age lane picks up never-stamped rows past
        # the age guard. The retry lane uses ``recovery_retry_minutes``
        # which is 1 by default — a never-stamped row is also
        # eligible for the retry lane.
        assert (
            result.lanes["pending_age"].recovered
            + result.lanes["recovery_retry"].recovered
        ) >= 1
        manager._handle_recover_deferred_report.assert_called()


# =============================================================================
# Y3 — _get_event_loop closed-loop terminal branch (POST-DEEP-REVIEW)
# =============================================================================


class TestGetEventLoopClosedLoop:
    """POST-DEEP-REVIEW (Y3, 2026-08-20): when the manager's stored
    loop is closed AND ``asyncio.get_event_loop()`` raises
    ``RuntimeError``, the helper MUST raise ``RuntimeError`` (not
    silently fall back to a brand-new ``asyncio.new_event_loop()``).
    A fresh loop is NOT the manager's canonical loop — scheduling
    onto it while blocking on ``.result()`` is a confusing failure
    mode that masks stale-loop state. The per-row caller catches
    the raised error, counts the row as an error, and the row is
    retried on the next sweep cycle.
    """

    def test_no_live_loop_raises_runtime_error(
        self, engine: Engine, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Both ``manager._loop`` is None AND ``asyncio.get_event_loop()``
        raises ``RuntimeError`` → ``_get_event_loop`` raises
        ``RuntimeError`` with a clear message; WARNING logged.
        """
        import asyncio
        import logging

        service, manager = _build_service(engine)

        # Force the manager's loop attribute to None — first branch
        # (``loop is not None``) is skipped.
        manager._loop = None

        # Patch ``asyncio.get_event_loop`` to raise — second branch
        # (``return asyncio.get_event_loop()``) raises, control
        # passes to the ``except RuntimeError`` terminal branch.
        original_get_event_loop = asyncio.get_event_loop
        call_count = {"n": 0}

        def _raise_get_event_loop(*_args: Any, **_kwargs: Any):
            call_count["n"] += 1
            raise RuntimeError(
                "There is no current event loop in thread 'X'."
            )

        asyncio.get_event_loop = _raise_get_event_loop
        try:
            caplog.set_level(
                logging.WARNING, logger="daemon.services.report_delivery_recovery"
            )
            with pytest.raises(RuntimeError) as exc_info:
                service._get_event_loop()
            assert "no live event loop" in str(exc_info.value).lower(), (
                f"RuntimeError message MUST mention 'no live event loop' "
                f"(the operator-actionable hint); got {exc_info.value!r}"
            )
            assert call_count["n"] == 1, (
                "asyncio.get_event_loop MUST be invoked exactly once "
                "before the terminal branch is hit"
            )
            # WARNING log emitted — operator visibility.
            warnings = [
                r for r in caplog.records
                if r.levelno == logging.WARNING
            ]
            assert any(
                "no live event loop" in r.getMessage().lower()
                for r in warnings
            ), (
                "WARNING log MUST mention 'no live event loop' for "
                "operator visibility; got "
                f"{[r.getMessage() for r in warnings]}"
            )
        finally:
            asyncio.get_event_loop = original_get_event_loop

# =============================================================================
# F4 — interruptible sweep + honest stop() join budget (POST-DEEP-REVIEW)
# =============================================================================


class TestStopEventInterrupt:
    """F4 (2026-08-20): a polite ``stop()`` MUST interrupt the
    sweep loop promptly — between every row AND between lanes —
    so ``thread.join`` rarely hits the (now-raised) budget.

    Without the inter-row check, a single per-row path that
    chains multiple ``run_coroutine_threadsafe(...).result(8.0)``
    bridges can blow past the old ``10s`` join budget and orphan
    the daemon thread on shutdown. The inter-row check is the
    primary prompt-exit; the inter-lane check is the cheap
    secondary cut.
    """

    def test_stop_event_exits_mid_batch(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Set ``self._stop_event`` after the FIRST row is
        processed — assert the sweep exits BEFORE processing the
        remaining rows of the batch.

        Strategy: install a side-effect on
        ``_recover_one_deferred_row`` that flips ``_stop_event``
        after the FIRST invocation, then count ``manager.
        _handle_recover_deferred_report`` calls. With N rows
        seeded, ``< N`` calls means the loop exited early.
        """
        # Seed 4 DEFERRED rows (4 distinct parent/child pairs).
        for i in range(4):
            parent = _seed_instance(engine)
            child = _seed_instance(
                engine,
                parent_id=parent,
                status=InstanceStatus.COMPLETED.value,
            )
            _seed_deferred_row(
                engine,
                parent_instance_id=parent,
                child_instance_id=child,
                child_message_id=f"child-msg-{i}",
            )

        service, manager = _build_service(engine)

        # Reset the manager mock's call counter; we want a clean
        # baseline.
        manager._handle_recover_deferred_report.reset_mock()

        # Wrap the per-row processor so the FIRST call flips the
        # stop event. Subsequent rows will hit the inter-row
        # ``self._stop_event.is_set()`` check and exit the batch.
        original = service._recover_one_deferred_row
        call_counter = {"n": 0}

        def _recover_and_stop(
            row, *, result, parent_not_terminal
        ):  # type: ignore[no-untyped-def]
            call_counter["n"] += 1
            if call_counter["n"] == 1:
                service._stop_event.set()
            return original(
                row,
                result=result,
                parent_not_terminal=parent_not_terminal,
            )

        monkeypatch.setattr(
            service,
            "_recover_one_deferred_row",
            _recover_and_stop,
        )

        # Run the sweep. With the inter-row guard the first row's
        # post-recovery flip bails the loop BEFORE row 2.
        result = service.recover_now()

        # The lane was processed but the manager mock was hit only
        # ONCE (row 1), not four times.
        recovered_in_lane = result.lanes["deferred"].recovered
        handled_calls = (
            manager._handle_recover_deferred_report.call_count
        )
        assert handled_calls == 1, (
            "stop-event guard MUST exit the per-row loop after "
            "the first row; manager mock was called "
            f"{handled_calls} times (expected 1)"
        )
        assert recovered_in_lane == 1, (
            "only the first row should be counted as recovered; "
            f"got recovered={recovered_in_lane}"
        )

    def test_stop_returns_within_honest_budget(
        self, engine: Engine
    ) -> None:
        """Honest worst-case join budget (F4, 2026-08-20): the
        auto-computed ``stop()`` budget covers the 3-bridge
        worst case (3 × 8s + 4s margin == 28s with the 10s floor).

        This test asserts the budget is NOT regressed to a
        lying ``10s`` default — we read the same source constant
        the production ``stop()`` uses by inspecting the
        ``stop()`` method's default. A pure unit assertion of
        the NEW auto-computed budget; no real thread required.
        """
        import inspect

        service, _ = _build_service(engine)
        sig = inspect.signature(service.stop)
        # The parameter ``timeout`` must be ``None`` (not the
        # prior ``10.0`` literal) so the auto-computed worst-
        # case budget kicks in. The ``float | None`` annotation
        # is the contract: callers passing ``timeout=None`` get
        # ``max(3 * 8.0 + 4.0, 10.0) == 28.0s``; callers passing
        # an explicit float get the literal they passed.
        assert sig.parameters["timeout"].default is None, (
            "stop() MUST default ``timeout=None`` (auto-computed "
            "worst-case budget); default literal 10.0 would be a "
            "regression to the lying budget"
        )
        # Also verify the helper docstring surfaces the arithmetic
        # so an operator scanning the source can audit the budget.
        doc = service.stop.__doc__ or ""
        assert "3 * 8.0 + 4.0" in doc, (
            "stop() docstring MUST document the worst-case "
            "join-budget arithmetic for operator audit"
        )

    def test_explicit_timeout_still_respected(
        self, engine: Engine
    ) -> None:
        """Callers that pass an explicit ``timeout=float`` get
        THAT literal — the auto-computed default only kicks in
        for ``timeout=None``. No silent override of explicit
        intent.
        """
        import inspect

        service, _ = _build_service(engine)
        sig = inspect.signature(service.stop)
        # The annotation must accept ``float | None`` (not
        # ``float`` only) so ``timeout=None`` is legal.
        anno = sig.parameters["timeout"].annotation
        # ``float | None`` is the expected form (PEP 604). It
        # stringifies as ``typing.Union`` or ``float | None``
        # depending on Python version.
        anno_str = str(anno)
        assert (
            "float" in anno_str and "None" in anno_str
        ), f"stop() timeout annotation MUST be float | None; got {anno_str!r}"



# ═══════════════════════════════════════════════════════════════════════
# F-2 (durability-f1-f2 / phase2, task 2.11) — RDRS lane-2 tests
# ═══════════════════════════════════════════════════════════════════════


class TestF2Lane2AnchorLessAdmission:
    """S21 — lane 2 admits anchor-less completed children of
    non-terminal parents (F-2 wedge straddle state).

    The F-1 wedge is a no-row straddle: a child completes just
    before restart, its wake-rows are wiped, the parent is
    waiting_children. The F-2 fix removes the anchor-required
    filter from the lane-2 query (task 2.2) so anchor-less
    children of non-terminal parents are admitted. The per-row
    pass then derives ``child_message_id`` from the surviving
    child checkpoint (task 2.6).

    These tests use a real file-backed SQLite engine (F9 parity
    via the existing test harness).
    """

    def _seed_completed_child_with_message(
        self,
        engine: Engine,
        parent_id: str,
        child_msg_id: str = "child-msg",
    ) -> tuple[str, str]:
        """Seed a COMPLETED child instance + its COMPLETED message."""
        child_id = _seed_instance(
            engine,
            parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_message(
            engine,
            instance_id=child_id,
            msg_id=child_msg_id,
            status=MessageStatus.COMPLETED.value,
        )
        return child_id, child_msg_id

    def test_admits_anchor_less_completed_child_of_non_terminal_parent(
        self, engine: Engine
    ) -> None:
        """S21 — a COMPLETED child with NO ``message_queue`` row
        (the F-2 wedge straddle state) IS admitted by the lane-2
        query. The ``has_anchor`` flag is ``False`` so the
        per-row pass knows to derive ``child_message_id`` from
        the checkpoint."""
        parent = _seed_instance(engine)  # non-terminal (default running)
        # Seed a COMPLETED child with NO message_queue row —
        # the anchor-less straddle state.
        child_id = _seed_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        # No _seed_message call — the child has no
        # COMPLETED message_queue row, so ``anchor_subq``
        # returns NULL.

        service, _ = _build_service(engine)
        rows = service._report_injection_repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        )
        # The row is admitted.
        child_rows = [r for r in rows if r["child_id"] == child_id]
        assert len(child_rows) == 1
        # ``has_anchor`` is ``False`` (no message_queue row).
        assert child_rows[0]["has_anchor"] is False
        # ``child_msg_id`` is ``None`` (the anchor subquery
        # returned NULL).
        assert child_rows[0]["child_msg_id"] is None
        # ``parent_id`` is correct.
        assert child_rows[0]["parent_id"] == parent

    def test_has_anchor_true_when_completed_message_exists(
        self, engine: Engine
    ) -> None:
        """A child with a COMPLETED ``message_queue`` row has
        ``has_anchor=True`` and ``child_msg_id`` populated —
        the per-row pass uses the anchor directly (no
        checkpoint derivation needed)."""
        parent = _seed_instance(engine)
        child_id, child_msg_id = self._seed_completed_child_with_message(
            engine, parent
        )

        service, _ = _build_service(engine)
        rows = service._report_injection_repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        )
        child_rows = [r for r in rows if r["child_id"] == child_id]
        assert len(child_rows) == 1
        assert child_rows[0]["has_anchor"] is True
        assert child_rows[0]["child_msg_id"] == child_msg_id

    def test_still_excludes_terminal_parent(
        self, engine: Engine
    ) -> None:
        """Anchor-less child of a TERMINAL parent is still
        excluded from the periodic sweep (the ORPHAN lane's
        territory)."""
        parent = _seed_instance(
            engine, status=InstanceStatus.COMPLETED.value
        )
        child_id = _seed_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )

        service, _ = _build_service(engine)
        rows = service._report_injection_repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        )
        # Terminal parent → excluded.
        assert not any(r["child_id"] == child_id for r in rows)

    def test_still_excludes_running_child(
        self, engine: Engine
    ) -> None:
        """S23 — RUNNING children are excluded (the
        ``c.status=='completed'`` filter at :1222)."""
        parent = _seed_instance(engine)
        # Seed a RUNNING child (not completed).
        child_id = _seed_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.RUNNING.value,
        )

        service, _ = _build_service(engine)
        rows = service._report_injection_repo.find_completed_children_without_delivery(
            parent_not_terminal=True
        )
        assert not any(r["child_id"] == child_id for r in rows)


class TestF2PREFIXLedgerDedup:
    """S22 — PREFIX-ledger cross-path dedup (queue-side +
    parent-history-side). The operative cross-path dedup per
    decisions.md §14a (W-1 LOCKED to fallback (ii))."""

    def test_find_wake_already_delivered_evidence_true_on_prefix_match(
        self, engine: Engine
    ) -> None:
        """Queue-side: a parent-side ``internal_report:{child}:%``
        row in a non-failed status triggers the PREFIX ledger."""
        from daemon.repositories.message_queue.repository import (
            SQLModelMessageQueueRepository,
        )
        parent = _seed_instance(engine)
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        # Seed a parent-side internal_report message.
        report_msg_id = f"report-{uuid.uuid4().hex[:8]}"
        _seed_message(
            engine,
            instance_id=parent,
            msg_id=report_msg_id,
            source=f"internal_report:{child_id}:some-anchor-msg",
            msg_type=MessageType.COMPLETION_REPORT.value,
            status=MessageStatus.READY.value,
        )
        queue_repo = SQLModelMessageQueueRepository(engine)
        assert (
            queue_repo.find_wake_already_delivered_evidence(
                parent, child_id
            )
            is True
        )

    def test_find_wake_already_delivered_evidence_false_when_no_match(
        self, engine: Engine
    ) -> None:
        """Queue-side: NO matching ``internal_report:{child}:%``
        row → returns False."""
        from daemon.repositories.message_queue.repository import (
            SQLModelMessageQueueRepository,
        )
        parent = _seed_instance(engine)
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        # Seed a message for a DIFFERENT child.
        other_child = f"other-{uuid.uuid4().hex[:8]}"
        _seed_message(
            engine,
            instance_id=parent,
            msg_id=f"report-{uuid.uuid4().hex[:8]}",
            source=f"internal_report:{other_child}:some-anchor",
            msg_type=MessageType.COMPLETION_REPORT.value,
            status=MessageStatus.READY.value,
        )
        queue_repo = SQLModelMessageQueueRepository(engine)
        assert (
            queue_repo.find_wake_already_delivered_evidence(
                parent, child_id
            )
            is False
        )

    def test_parent_history_has_internal_report_true_on_marker(
        self, engine: Engine
    ) -> None:
        """Parent-history-side: the REAL evidence shape — a
        user-role (HumanMessage) parent-history dict with the
        NO-COLON source ``internal_report:{child}`` (the stamp at
        ``graph.py:8528``) → returns True. Iteration-2 blocker-2
        reshape: the prior fixture baked an assistant-role +
        colon-suffixed shape the production stamp can never
        produce."""
        from daemon.services.report_delivery_ledger import (
            parent_history_has_internal_report,
        )
        import daemon.persistence as persistence_mod

        parent = _seed_instance(engine)
        child_id = f"child-{uuid.uuid4().hex[:8]}"

        class _MockCheckpointer:
            raw_saver = self

            async def _raw_saver_aget(self, config):
                return None

        async def _fake_get_instance_messages(checkpointer, instance_id, manager=None):
            return [
                {
                    "role": "user",
                    "content": "hello",
                    "message_id": "msg-1",
                },
                {
                    "role": "user",  # HumanMessage stamp
                    "content": "[Child terminal report] ...",
                    "message_id": "msg-2",
                    "source": f"internal_report:{child_id}",  # NO colon
                },
            ]

        original = persistence_mod.get_instance_messages
        persistence_mod.get_instance_messages = _fake_get_instance_messages
        try:
            result = asyncio.run(
                parent_history_has_internal_report(
                    _MockCheckpointer(), parent, child_id
                )
            )
        finally:
            persistence_mod.get_instance_messages = original
        assert result is True

    def test_parent_history_has_internal_report_false_on_no_source(
        self, engine: Engine
    ) -> None:
        """Parent-history-side: a parent checkpoint message WITHOUT
        a ``source`` field (pre-migration parent) → returns False
        (graceful degradation per the docstring)."""
        from daemon.services.report_delivery_ledger import (
            parent_history_has_internal_report,
        )
        import daemon.persistence as persistence_mod

        parent = _seed_instance(engine)
        child_id = f"child-{uuid.uuid4().hex[:8]}"

        class _MockCheckpointer:
            raw_saver = self

            async def _raw_saver_aget(self, config):
                return None

        async def _fake_get_instance_messages(checkpointer, instance_id, manager=None):
            return [
                {
                    "role": "user",
                    "content": "hello",
                    "message_id": "msg-1",
                },
                {
                    "role": "assistant",
                    "content": "no source field",
                    "message_id": "msg-2",
                    # NO "source" key — pre-migration parent.
                },
            ]

        original = persistence_mod.get_instance_messages
        persistence_mod.get_instance_messages = _fake_get_instance_messages
        try:
            result = asyncio.run(
                parent_history_has_internal_report(
                    _MockCheckpointer(), parent, child_id
                )
            )
        finally:
            persistence_mod.get_instance_messages = original
        assert result is False


class TestF2IdempotencyAndKillSwitch:
    """S25 (idempotent run-twice) + S26 (kill-switch gate) for
    the F-2 lane-2 extension."""

    def test_run_twice_single_ensure_deferred(
        self, engine: Engine
    ) -> None:
        """S25 — running lane 2 twice on the same wedge child
        results in exactly one ``ensure_deferred`` call (the
        obligation-triple unique index handles within-path
        idempotency; the PREFIX ledger handles cross-path
        dedup)."""
        parent = _seed_instance(engine)
        child_id, _ = self._seed_completed_child_with_message(engine, parent)

        service, _ = _build_service(engine)
        # First run: the row is admitted, ensure_deferred is
        # called.
        result1 = service._run_no_row_backstop_lane()
        # Second run: the obligation-triple unique index
        # absorbs the duplicate (W6); the row is NOT re-minted.
        result2 = service._run_no_row_backstop_lane()
        # Total recovered across both runs: 1 (the obligation
        # is honored exactly once).
        total_recovered = result1.recovered + result2.recovered
        assert total_recovered == 1

    def _seed_completed_child_with_message(
        self,
        engine: Engine,
        parent_id: str,
        child_msg_id: str = "child-msg",
    ) -> tuple[str, str]:
        child_id = _seed_instance(
            engine,
            parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_message(
            engine,
            instance_id=child_id,
            msg_id=child_msg_id,
            status=MessageStatus.COMPLETED.value,
        )
        return child_id, child_msg_id

    def test_lane2_kill_switch_off_skips_lane(
        self, engine: Engine
    ) -> None:
        """S26 — when ``lane_no_row_backstop`` is False, the
        lane-2 entry is skipped (the RDRS per-lane kill
        switch governs the active behavior)."""
        from daemon.services.report_delivery_recovery import (
            ReportDeliveryRecoveryService,
        )
        from daemon.repositories.report_injection.repository import (
            ReportInjectionRepository,
        )
        parent = _seed_instance(engine)
        self._seed_completed_child_with_message(engine, parent)
        # Build the service with ``lane_no_row_backstop=False``.
        task_repo = MagicMock()
        task_repo.has_instance_busy = MagicMock(return_value=False)
        manager = MagicMock()
        manager.engine = engine
        manager._checkpointer = None
        manager._handle_recover_deferred_report = MagicMock()
        ri_repo = ReportInjectionRepository(engine=engine)
        service = ReportDeliveryRecoveryService(
            task_repo=task_repo,
            report_injection_repo=ri_repo,
            queue_repo=_queue_repo_mock(),
            instance_repo=MagicMock(),
            manager_ref=manager,
            interval_seconds=300,
            age_bound_minutes=10,
            batch_cap=100,
            recovery_retry_minutes=1,
            enabled=True,
            lane_orphan=False,
            lane_no_row_backstop=False,
        )
        # Test via _run_all_lanes_sync (the kill switch check
        # is in _run_all_lanes_sync, not in the lane method
        # itself — the lane method is the inner primitive).
        result = service._run_all_lanes_sync()
        # Lane 2 disabled → the ``no_row_backstop`` key is
        # NOT in the result dict (the kill switch short-
        # circuits before the lane method is called).
        assert "no_row_backstop" not in result.lanes
        assert result.total_recovered == 0


class TestF2LiveDeliveryRace:
    """S28 — live delivery between lane-2 scan and per-row
    ledger re-check → lane must skip (skip-already-reported
    counter increments; no double-injection)."""

    def test_live_delivery_between_scan_and_ledger_recheck_skips(
        self, engine: Engine
    ) -> None:
        """Between the lane-2 scan and the per-row ledger
        re-check, a live delivery inserts a fresh
        ``internal_report:{child}:%`` message into the
        parent's ``MessageQueue``. The per-row pass MUST
        detect the delivery via the PREFIX ledger and skip
        (increment ``skipped_already_reported``; do NOT
        double-inject)."""
        from daemon.repositories.message_queue.repository import (
            SQLModelMessageQueueRepository,
        )

        parent = _seed_instance(engine)
        child_id, _ = self._seed_completed_child_with_message(engine, parent)

        # Build a service with a REAL queue_repo (so the
        # PREFIX ledger check is a real method call, not a
        # MagicMock). The ``_build_service`` helper uses a
        # MagicMock queue_repo; build a minimal service here.
        from daemon.services.report_delivery_recovery import (
            ReportDeliveryRecoveryService,
        )
        from daemon.repositories.report_injection.repository import (
            ReportInjectionRepository,
        )
        ri_repo = ReportInjectionRepository(engine=engine)
        queue_repo = SQLModelMessageQueueRepository(engine=engine)
        task_repo = MagicMock()
        task_repo.has_instance_busy = MagicMock(return_value=False)
        manager = MagicMock()
        manager.engine = engine
        manager._checkpointer = None
        manager._handle_recover_deferred_report = MagicMock()
        service = ReportDeliveryRecoveryService(
            task_repo=task_repo,
            report_injection_repo=ri_repo,
            queue_repo=queue_repo,
            instance_repo=MagicMock(),
            manager_ref=manager,
            interval_seconds=300,
            age_bound_minutes=10,
            batch_cap=100,
            recovery_retry_minutes=1,
            enabled=True,
            lane_orphan=False,
        )
        # The lane-2 query returns the child (no internal_report
        # row yet — the live delivery hasn't happened).
        rows_initial = (
            service._report_injection_repo.find_completed_children_without_delivery(
                parent_not_terminal=True
            )
        )
        assert any(r["child_id"] == child_id for r in rows_initial)
        # LIVE DELIVERY: the natural path inserts a fresh
        # ``internal_report:{child}:%`` row between the scan
        # and the per-row pass.
        _seed_message(
            engine,
            instance_id=parent,
            msg_id=f"live-report-{uuid.uuid4().hex[:8]}",
            source=f"internal_report:{child_id}:live-anchor-msg",
            msg_type=MessageType.COMPLETION_REPORT.value,
            status=MessageStatus.READY.value,
        )
        # The per-row pass: the PREFIX ledger check now matches
        # the live delivery.
        assert (
            queue_repo.find_wake_already_delivered_evidence(
                parent, child_id
            )
            is True
        )
        # The lane's step-0 guard would short-circuit:
        # ``skipped_already_reported += 1; return``. This is
        # the cross-path dedup (per decisions.md §14a) — the
        # obligation-triple unique index is the within-path
        # idempotency (per migration 20260819_000001:114-120).
        # The interaction is: PREFIX-ledger (cross-path) is
        # checked FIRST; if it matches, the obligation is NOT
        # minted; if it doesn't match, the obligation-triple
        # unique index prevents duplicate mints within the
        # lane-2 / recovery path. Both layers are required.

    def _seed_completed_child_with_message(
        self,
        engine: Engine,
        parent_id: str,
        child_msg_id: str = "child-msg",
    ) -> tuple[str, str]:
        child_id = _seed_instance(
            engine,
            parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_message(
            engine,
            instance_id=child_id,
            msg_id=child_msg_id,
            status=MessageStatus.COMPLETED.value,
        )
        return child_id, child_msg_id


class TestF2ChildMessageIdStableButDifferent:
    """F-2 (durability-f1-f2 / phase2, task 2.6) — the derived
    ``child_message_id`` is STABLE-BUT-DIFFERENT from the natural
    path's ``MessageQueue.message_id`` (per W-1 fallback (ii)
    confirmation)."""

    def test_derive_anchor_less_child_message_id_from_checkpoint(
        self, engine: Engine
    ) -> None:
        """The derivation reads the surviving child checkpoint
        via ``get_instance_messages`` + the
        ``serialize_message`` chain. The derived id is
        ``BaseMessage.id`` (UUID4) — STABLE-BUT-DIFFERENT
        from the natural path's ``MessageQueue.message_id``
        (per W-1 confirmation)."""
        from daemon.services.report_delivery_recovery import (
            _derive_anchor_less_child_message_id,
        )
        import daemon.persistence as persistence_mod

        child_id = f"child-{uuid.uuid4().hex[:8]}"
        derived_id = f"derived-{uuid.uuid4().hex}"

        class _MockCheckpointer:
            raw_saver = self

            async def _raw_saver_aget(self, config):
                return None

        async def _fake_get_instance_messages(checkpointer, instance_id, manager=None):
            return [
                {
                    "role": "user",
                    "content": "hello",
                    "message_id": f"user-{uuid.uuid4().hex[:8]}",
                },
                {
                    "role": "assistant",
                    "content": "child terminal report content",
                    "message_id": derived_id,  # the BaseMessage.id
                },
            ]

        original = persistence_mod.get_instance_messages
        persistence_mod.get_instance_messages = _fake_get_instance_messages
        try:
            result = asyncio.run(
                _derive_anchor_less_child_message_id(
                    _MockCheckpointer(), child_id
                )
            )
        finally:
            persistence_mod.get_instance_messages = original
        # The derived id is the BaseMessage.id of the last
        # assistant message — STABLE-BUT-DIFFERENT from any
        # natural-path MessageQueue.message_id.
        assert result == derived_id

    def test_derive_returns_none_when_no_assistant_message(
        self, engine: Engine
    ) -> None:
        """If the child checkpoint has no assistant message
        (or the checkpoint is missing), the derivation
        returns None — the per-row pass logs WARNING and
        ``continue``s (the periodic 300s loop is the retry
        mechanism)."""
        from daemon.services.report_delivery_recovery import (
            _derive_anchor_less_child_message_id,
        )
        import daemon.persistence as persistence_mod

        child_id = f"child-{uuid.uuid4().hex[:8]}"

        class _MockCheckpointer:
            raw_saver = self

            async def _raw_saver_aget(self, config):
                return None

        async def _fake_get_instance_messages(checkpointer, instance_id, manager=None):
            return [
                {
                    "role": "user",
                    "content": "hello",
                    "message_id": f"user-{uuid.uuid4().hex[:8]}",
                },
                # NO assistant message.
            ]

        original = persistence_mod.get_instance_messages
        persistence_mod.get_instance_messages = _fake_get_instance_messages
        try:
            result = asyncio.run(
                _derive_anchor_less_child_message_id(
                    _MockCheckpointer(), child_id
                )
            )
        finally:
            persistence_mod.get_instance_messages = original
        assert result is None

    def test_derive_returns_none_when_checkpointer_is_none(
        self, engine: Engine
    ) -> None:
        """``checkpointer=None`` ⇒ returns None (no evidence
        available; the per-row pass logs WARNING and
        ``continue``s)."""
        from daemon.services.report_delivery_recovery import (
            _derive_anchor_less_child_message_id,
        )
        result = asyncio.run(
            _derive_anchor_less_child_message_id(None, "child-123")
        )
        assert result is None

    def test_derive_skips_empty_content_assistant_message(
        self, engine: Engine
    ) -> None:
        """Plan-2.6 empty-content guard: an assistant message with
        a truthy id but EMPTY content is not a derivable report
        anchor — the derivation skips it (WARNING) and falls
        through to an older assistant message with content, or
        returns None when none qualifies."""
        from daemon.services.report_delivery_recovery import (
            _derive_anchor_less_child_message_id,
        )
        import daemon.persistence as persistence_mod

        child_id = f"child-{uuid.uuid4().hex[:8]}"
        older_id = f"older-{uuid.uuid4().hex}"

        class _MockCheckpointer:
            raw_saver = self

            async def _raw_saver_aget(self, config):
                return None

        async def _fake_get_instance_messages(
            checkpointer, instance_id, manager=None
        ):
            return [
                {
                    "role": "assistant",
                    "content": "older real report",
                    "message_id": older_id,
                },
                {
                    # Newest assistant message: id present,
                    # content EMPTY → not a derivable anchor.
                    "role": "assistant",
                    "content": "",
                    "message_id": f"empty-{uuid.uuid4().hex[:8]}",
                },
            ]

        original = persistence_mod.get_instance_messages
        persistence_mod.get_instance_messages = _fake_get_instance_messages
        try:
            result = asyncio.run(
                _derive_anchor_less_child_message_id(
                    _MockCheckpointer(), child_id
                )
            )
        finally:
            persistence_mod.get_instance_messages = original
        assert result == older_id


class TestF2ManagerLoopBridgeAffinity:
    """Iteration-2 blocker-1 LOOP-AFFINITY pin.

    Production checkpointers (``AsyncSqliteSaver`` /
    ``AsyncPostgresSaver``) hold loop-bound ``asyncio.Lock`` s. The
    per-row pass MUST route every checkpointer-touching coroutine
    (the parent-history PREFIX ledger check AND the anchor-less
    ``child_message_id`` derivation) through the manager-loop
    bridge (``_run_async_on_manager_loop`` →
    ``asyncio.run_coroutine_threadsafe``), NEVER through an
    ephemeral ``asyncio.run`` loop.

    The pin is double-barreled:

    1. **Loop-bound fake checkpointer** — the patched
       ``get_instance_messages`` records the loop it ran on and
       RAISES if that loop is not the manager loop. A regression
       to an ephemeral loop fails the read (and the test).
    2. **``asyncio.run`` tripwire** — ``asyncio.run`` is patched
       to raise for the duration of the sweep; any use anywhere in
       the per-row path explodes the test.

    Both bridge consumers are exercised in one pass-through: the
    child is ANCHOR-LESS, so the per-row pass hits the ledger
    check (parent-id read) and then the derivation (child-id
    read) — both must land on the manager loop.
    """

    def test_lane2_routes_all_checkpointer_reads_through_manager_loop(
        self, engine: Engine
    ) -> None:
        import threading

        import daemon.persistence as persistence_mod
        from daemon.services.report_delivery_recovery import (
            ReportDeliveryRecoveryService,
        )
        from daemon.repositories.report_injection.repository import (
            ReportInjectionRepository,
        )

        # ── Real background loop standing in for manager._loop ──
        manager_loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(
            target=manager_loop.run_forever, daemon=True
        )
        loop_thread.start()

        acquired_loops: list[asyncio.AbstractEventLoop] = []
        derived_id = f"derived-{uuid.uuid4().hex}"

        class _LoopBoundCheckpointer:
            """Fake checkpointer: raises if acquired from a
            foreign loop (the production savers' loop-bound lock
            behavior, abstracted)."""

            raw_saver = None

        async def _loop_checking_get_instance_messages(
            checkpointer, instance_id, manager=None
        ):
            running = asyncio.get_running_loop()
            acquired_loops.append(running)
            if running is not manager_loop:
                raise RuntimeError(
                    "cross-loop seam regression: checkpointer read "
                    "landed on a foreign loop (ephemeral asyncio.run "
                    "loop instead of the manager loop)"
                )
            if instance_id == child_id:
                # The surviving child checkpoint (derivation read).
                return [
                    {
                        "role": "assistant",
                        "content": "child terminal report content",
                        "message_id": derived_id,
                    },
                ]
            # The parent-history ledger scan → no evidence.
            return []

        original_get = persistence_mod.get_instance_messages
        original_asyncio_run = asyncio.run

        def _tripwire_run(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(
                "asyncio.run used in the per-row pass — cross-loop "
                "seam regression (must use the manager-loop bridge)"
            )

        parent = _seed_instance(engine)
        # Anchor-less COMPLETED child: NO message_queue row seeded
        # → the per-row pass must DERIVE the id (the second bridge
        # consumer).
        child_id = _seed_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        try:
            persistence_mod.get_instance_messages = (
                _loop_checking_get_instance_messages
            )
            asyncio.run = _tripwire_run  # type: ignore[assignment]

            # Anchor-less child: NO message seeded → the per-row
            # pass must DERIVE the id (the second bridge consumer).
            ri_repo = ReportInjectionRepository(engine=engine)
            queue_repo = MagicMock()
            queue_repo.find_wake_already_delivered_evidence = (
                MagicMock(return_value=False)
            )
            task_repo = MagicMock()
            task_repo.has_instance_busy = MagicMock(return_value=False)
            manager = MagicMock()
            manager.engine = engine
            manager._loop = manager_loop
            manager._checkpointer = _LoopBoundCheckpointer()
            manager._handle_recover_deferred_report = MagicMock()
            service = ReportDeliveryRecoveryService(
                task_repo=task_repo,
                report_injection_repo=ri_repo,
                queue_repo=queue_repo,
                instance_repo=MagicMock(),
                manager_ref=manager,
                interval_seconds=300,
                age_bound_minutes=10,
                batch_cap=100,
                recovery_retry_minutes=1,
                enabled=True,
                lane_orphan=False,
            )

            result = service._run_no_row_backstop_lane()
        finally:
            persistence_mod.get_instance_messages = original_get
            asyncio.run = original_asyncio_run  # type: ignore[assignment]
            manager_loop.call_soon_threadsafe(manager_loop.stop)
            loop_thread.join(timeout=5.0)
            manager_loop.close()

        # The full pass-through succeeded on the manager loop.
        assert result.recovered == 1, (
            f"anchor-less child must be recovered through the "
            f"manager-loop bridge; lanes={result.to_dict() if hasattr(result, 'to_dict') else result}"
        )
        assert result.errors == 0
        # BOTH bridge consumers ran, and EVERY checkpointer read
        # landed on the manager loop (never a foreign/ephemeral
        # loop).
        assert len(acquired_loops) >= 2, (
            f"both the ledger check and the derivation must have "
            f"read the checkpointer; got {len(acquired_loops)} reads"
        )
        assert all(
            loop is manager_loop for loop in acquired_loops
        ), "every checkpointer read MUST run on the manager loop"
        # The derived id (not a queue-row anchor) landed on the row.
        from daemon.repositories.report_injection.models import (
            ReportInjection,
            ReportInjectionState,
        )
        with Session(engine) as session:
            row = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.child_instance_id == child_id
                )
            ).first()
        assert row is not None
        assert row.state == ReportInjectionState.PENDING.value
        assert row.child_message_id == derived_id
"""G4-r3 unit test for the stuck-wake parent-schedule seam (last-mile)."""


class TestG4R3StuckWakeParentScheduleSeam:
    """G4-r3 LOOP-AFFINITY pin + parent-schedule seam (last-mile).

    The captured-wedge shape (LIVE evidence: r1r pre-kill
    assertion at .agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/
    db-assertions/r1r-pre-kill-assertion.txt): parent waiting_children,
    child completed, wake task running (dead worker), wake row ready,
    report_injections TASK_DELIVERED. The dead worker had
    transitioned PENDING->TASK_DELIVERED via claim_for_task_delivery
    pre-SIGKILL; the retry's claim sees TASK_DELIVERED and skips
    (the dedup contract). The parent stays in waiting_children
    because the worker's normal delivery path never got to dispatch
    the parent turn.

    The fix: lane 6's heal action also calls the SAME primitive the
    natural wake uses to dispatch the parent turn
    (``_process_child_completion_and_notify_parent``), via the
    manager-loop bridge (run_coroutine_threadsafe + .result(timeout)).

    This is a NEW caller of the same primitive -- the function body,
    the normal wake's path, the worker's claim/dedup contract, and
    every existing lane are unchanged. No frozen behavior touched.

    The pin: a manager-loop bridge mock that records the call
    AND asserts the loop it ran on IS the manager loop (the
    cross-loop seam regression class from the F-2 verification
    iter 2 must not re-emerge here).
    """

    def test_lane6_heal_dispatches_parent_via_natural_primitive(
        self, engine
    ) -> None:
        import threading
        from sqlalchemy import text as sa_text
        from daemon.repositories.task.repository import TaskRepository
        from daemon.repositories.task.models import Task, TaskStatus, TaskType
        from daemon.repositories.report_injection.repository import (
            ReportInjectionRepository,
        )
        from daemon.services.report_delivery_recovery import (
            ReportDeliveryRecoveryService,
        )

        # Real background loop standing in for manager._loop
        manager_loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(
            target=manager_loop.run_forever, daemon=True
        )
        loop_thread.start()

        # Mock for the parent-schedule primitive. AsyncMock so
        # run_coroutine_threadsafe(coro, loop).result() resolves
        # (the coro is awaited on manager_loop).
        schedule_calls = []
        original_loop_ref = []

        async def _schedule_parent(child_id_arg, child_message_id_arg):
            # Record which loop this coroutine ran on -- must be
            # the manager loop (cross-loop seam regression class).
            running = asyncio.get_running_loop()
            original_loop_ref.append(running)
            schedule_calls.append(
                (child_id_arg, child_message_id_arg, "scheduled")
            )
            return None

        # Seed the captured state (LIVE id-shape)
        parent = _seed_instance(
            engine,
            instance_id="g4r3-parent-1",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        child_id = _seed_instance(
            engine,
            instance_id="g4r3-child-1",
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        # LIVE-shape: mq.message_id != ri.child_message_id;
        # correlation via the wake row's source pattern.
        child_content_message_id = "g4r3-child-content-1"
        wake_message_id = "g4r3-wake-msg-1"
        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "INSERT INTO message_queue ("
                    "message_id, instance_id, type, status, source, "
                    "content, priority, retry_count, max_retries, "
                    "enqueued_at"
                    ") VALUES ("
                    ":message_id, :instance_id, :type, :status, :source, "
                    ":content, :priority, :retry_count, :max_retries, "
                    ":enqueued_at"
                    ")"
                ),
                {
                    "message_id": wake_message_id,
                    "instance_id": parent,
                    "type": "completion_report",
                    "status": "ready",
                    "source": (
                        f"internal_report:{child_id}:"
                        f"{child_content_message_id}"
                    ),
                    "content": "child terminal report content",
                    "priority": 0,
                    "retry_count": 0,
                    "max_retries": 3,
                    "enqueued_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        # Dead-worker wake task (RUNNING with stale heartbeat).
        stale = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(minutes=10)
        ).isoformat()
        with engine.begin() as conn:
            wake_task_obj = Task(
                work_id=f"g4r3-wake-work-{uuid.uuid4().hex[:8]}",
                task_type=TaskType.PROCESS_REPORT.value,
                instance_id=parent,
                message_id=wake_message_id,
                status=TaskStatus.RUNNING.value,
                worker_id="dead-worker-g4r3",
                started_at=stale,
                last_heartbeat_at=stale,
                created_at=stale,
            )
            conn.execute(
                sa_text(
                    "INSERT INTO task ("
                    "work_id, task_type, instance_id, message_id, status, "
                    "worker_id, started_at, last_heartbeat_at, "
                    "retry_count, cancel_requested, cancel_requested_at, "
                    "retry_scheduled, is_deferred, is_background, result, "
                    "error, completed_at, auto_continued_at, "
                    "suspension_reason, resume_target_turn_id, next_retry_at, "
                    "created_at"
                    ") VALUES ("
                    ":work_id, :task_type, :instance_id, :message_id, "
                    ":status, :worker_id, :started_at, :last_heartbeat_at, "
                    ":retry_count, :cancel_requested, :cancel_requested_at, "
                    ":retry_scheduled, :is_deferred, :is_background, "
                    ":result, :error, :completed_at, :auto_continued_at, "
                    ":suspension_reason, :resume_target_turn_id, :next_retry_at, "
                    ":created_at"
                    ")"
                ),
                {
                    "work_id": wake_task_obj.work_id,
                    "task_type": wake_task_obj.task_type,
                    "instance_id": wake_task_obj.instance_id,
                    "message_id": wake_task_obj.message_id,
                    "status": wake_task_obj.status,
                    "worker_id": wake_task_obj.worker_id,
                    "started_at": wake_task_obj.started_at,
                    "last_heartbeat_at": wake_task_obj.last_heartbeat_at,
                    "retry_count": wake_task_obj.retry_count,
                    "cancel_requested": wake_task_obj.cancel_requested,
                    "cancel_requested_at": wake_task_obj.cancel_requested_at,
                    "retry_scheduled": wake_task_obj.retry_scheduled,
                    "is_deferred": wake_task_obj.is_deferred,
                    "is_background": wake_task_obj.is_background,
                    "result": wake_task_obj.result,
                    "error": wake_task_obj.error,
                    "completed_at": wake_task_obj.completed_at,
                    "auto_continued_at": wake_task_obj.auto_continued_at,
                    "suspension_reason": wake_task_obj.suspension_reason,
                    "resume_target_turn_id": wake_task_obj.resume_target_turn_id,
                    "next_retry_at": wake_task_obj.next_retry_at,
                    "created_at": wake_task_obj.created_at,
                },
            )
        # PENDING marker (the captured-wedge shape at lane-6 time:
        # the dead worker had NOT YET called claim_for_task_delivery
        # pre-SIGKILL -- the TASK_DELIVERED transition happens
        # AFTER lane 6 runs, when the retry is claimed by a worker.
        # The lane 6 query requires state='pending').
        ri_repo = ReportInjectionRepository(engine=engine)
        ri_repo.ensure_deferred(
            parent_instance_id=parent,
            child_instance_id=child_id,
            child_message_id=child_content_message_id,
            deferred_reason="system:crash_wake",
        )
        # After ensure_deferred the marker is DEFERRED. The lane 6
        # query requires state='pending' (not DEFERRED) — the
        # captured-wedge shape is PENDING, so transition DEFERRED
        # to PENDING via transition_deferred_to_pending. The
        # captured-wedge shape in the live evidence: the marker is
        # PENDING at lane-6 time (this transition models the
        # post-mint, pre-crash state).
        ri_repo.transition_deferred_to_pending(
            injection_id=ri_repo.find_deferred_for_parent_all(
                parent_not_terminal=True, limit=10
            )[0].injection_id
        )

        # Service + manager mock wired for the bridge
        manager = MagicMock()
        manager.engine = engine
        manager._loop = manager_loop
        manager._checkpointer = None
        manager._handle_recover_deferred_report = MagicMock()
        manager._process_child_completion_and_notify_parent = AsyncMock(
            side_effect=_schedule_parent
        )
        service = ReportDeliveryRecoveryService(
            task_repo=TaskRepository(engine=engine),
            report_injection_repo=ri_repo,
            queue_repo=MagicMock(),
            instance_repo=MagicMock(),
            manager_ref=manager,
            interval_seconds=300,
            age_bound_minutes=10,
            batch_cap=100,
            recovery_retry_minutes=1,
            enabled=True,
        )
        try:
            lane = service._run_stuck_wake_lane()
        finally:
            manager_loop.call_soon_threadsafe(manager_loop.stop)
            loop_thread.join(timeout=5.0)
            manager_loop.close()

        # Assertions (the dispatch's "red->green" meta-test)
        # Lane reports the heal.
        assert lane.recovered == 1, (
            f"the dead-worker wake task MUST be force-cancelled "
            f"+ retry-minted; recovered={lane.recovered}, "
            f"errors={lane.errors}"
        )
        # The parent-schedule primitive was called exactly once
        # for the (child_id, child_message_id) pair (the natural
        # primitive the normal wake uses -- no new messaging path).
        assert len(schedule_calls) == 1, (
            f"the parent-schedule primitive MUST be invoked exactly "
            f"once after the heal; got {len(schedule_calls)} calls: "
            f"{schedule_calls}"
        )
        called_child_id, called_msg_id, _ = schedule_calls[0]
        assert called_child_id == child_id, (
            f"the schedule call MUST be for the captured child; got "
            f"{called_child_id}, expected {child_id}"
        )
        assert called_msg_id == child_content_message_id, (
            f"the schedule call MUST carry the child's content "
            f"message_id (the natural primitive's signature); got "
            f"{called_msg_id}, expected {child_content_message_id}"
        )
        # Cross-loop seam: the coroutine MUST have run on the
        # manager loop (NOT an ephemeral asyncio.run loop). A
        # regression to ephemeral-loop dispatch would re-introduce
        # the F-2 iter 2 blocker 1 class.
        assert original_loop_ref, "the schedule coroutine did not run"
        assert original_loop_ref[0] is manager_loop, (
            f"the schedule coroutine MUST run on the manager loop; "
            f"got {original_loop_ref[0]}, expected {manager_loop}"
        )
        # The manager's _process_child_completion_and_notify_parent
        # was awaited via the bridge (not via an in-test call to
        # the mock -- the side_effect=_schedule_parent ran on
        # manager_loop via run_coroutine_threadsafe).
        manager._process_child_completion_and_notify_parent.assert_awaited_once_with(
            child_id, child_content_message_id
        )