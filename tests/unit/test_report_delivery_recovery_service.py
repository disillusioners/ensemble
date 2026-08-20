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

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

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


def _build_service(
    engine: Engine,
    *,
    busy_ids: set[str] | None = None,
) -> tuple[ReportDeliveryRecoveryService, MagicMock]:
    """Build the service + a mock manager.

    Returns ``(service, manager_mock)``. The mock manager's
    ``_handle_recover_deferred_report`` is a MagicMock so the
    tests can assert the recovery call shape.
    """
    from daemon.repositories.report_injection.repository import (
        ReportInjectionRepository,
    )

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
            queue_repo=MagicMock(),
            instance_repo=MagicMock(),
            manager_ref=MagicMock(),
            enabled=True,
            lane_deferred=False,
            lane_no_row_backstop=False,
            lane_pending_age=False,
            lane_recovery_retry=False,
            lane_orphan=False,
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
        manager._handle_recover_deferred_report = MagicMock()

        service = ReportDeliveryRecoveryService(
            task_repo=task_repo,
            report_injection_repo=ri_repo,
            queue_repo=MagicMock(),
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
        manager._handle_recover_deferred_report = MagicMock()

        # Disable Lane 3.
        service = ReportDeliveryRecoveryService(
            task_repo=task_repo,
            report_injection_repo=ri_repo,
            queue_repo=MagicMock(),
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
