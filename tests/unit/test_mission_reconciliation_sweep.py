"""Unit tests — the RECONCILIATION SWEEP (RDRS lane 7, Proposal B PR3).

The sweep is the ESCAPE VALVE for the "eternal mission" class: an
outstanding ``mission_commissions`` row whose commissioned work will
never produce a report holds its mission open forever (the D2(c)
settle-hold refuses to settle while ``outstanding_commissions > 0``).
Lane 7 resolves the provably-dead rows to ``abandoned`` (and the
provably-delivered ones to ``satisfied``).

THE CONTRACT THIS FILE PINS (the design's §2 posture table):

* **P1 — fail-MISS.** EVERY probe error HOLDS the row: the commission
  stays ``outstanding``, ``probe_errors`` increments, ZERO writes. The
  sweep NEVER resolves a commission on a failed probe — abandoning a
  commission is a wrong-side terminal transition in the same moral
  category as finalizing a live mission.
* **F2 — the DIAGNOSTIC legs are the ONE exception to P1.** The
  delivery-evidence legs 2/3 (the queue-side + parent-history PREFIX
  ledgers) are DIAGNOSTIC ONLY: they may never authorise a
  ``satisfied`` and may never authorise a hold-against-abandon. Their
  fault is counted in ``diagnostic_probe_errors`` (its OWN counter, the
  ``masked_probe_errors`` precedent) and the row proceeds. ONLY the
  AUTHORITY leg (leg 1 — the commission-bound, state-filtered
  injection-row read) may HOLD on a fault.
* **P2 — loop-crash inherits the host's per-row posture.** A crashed
  row is logged + counted in ``errors`` + left untouched.
* **P3 — the TTL is a TIME-BOUNDED fail-MISS.** Past the horizon AND
  with a SUCCESSFUL "not live" liveness probe, the unprovable state
  resolves to ``abandoned`` LOUDLY. A liveness-probe ERROR still holds
  at any age.
* **R5 — exactly-once.** The guarded transition's rowcount decides:
  ``rowcount == 0`` means another actor won → ``already_resolved``,
  NEVER a retry in place.
* **R6 — the sweep writes ONLY ``mission_commissions``.** No instance
  status, no mission_records, no report_injections, no
  dependency_watchers.
* **R7 — the mode ladder.** ``off`` → the lane is NOT EVEN QUERIED;
  ``dry`` → detect + counters, ZERO writes; ``enforce`` → writes.

The test matrix maps to the design's T1-T9 (T10's boot E2E is the
TESTER's lane; the escape-valve CHAIN is proven here at the service
level).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.mission_record.models import (
    CommissionState,
    MissionCommission,
)
from daemon.repositories.mission_record.repository import (
    MissionRecordRepository,
)
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.services.mission_record_service import (
    MISSION_RECORDS_MODE_ENV,
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
)
from daemon.services.report_delivery_recovery import (
    ReportDeliveryRecoveryService,
)

TTL_SECONDS = 3600


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_mode_cache():
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'pr3-sweep.sqlite'}",
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


@pytest.fixture
def manager_loop():
    """A REAL event loop on a background thread.

    The parent-history PREFIX probe is a coroutine bridged onto the
    manager's loop (``_run_async_on_manager_loop``) — the test drives
    the REAL bridge, not a stub.
    """
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


class FakeInstanceRepo:
    """The instance reads the sweep needs: the worker row + liveness."""

    def __init__(
        self,
        *,
        statuses: dict[str, str] | None = None,
        live: bool = False,
        engine=None,
    ):
        self.statuses = statuses or {}
        self.live = live
        # The task-stamp probe (MAJ-4) reads the TASK table through the
        # repository's ``engine`` — the same attribute the mission-live
        # guard uses. ``None`` (the default) makes the probe fault,
        # which the sweep treats fail-safe (the member stays live).
        self.engine = engine
        self.get_error: Exception | None = None
        self.live_error: Exception | None = None

    def get(self, instance_id: str):
        if self.get_error is not None:
            raise self.get_error
        status = self.statuses.get(instance_id)
        if status is None:
            return None
        return SimpleNamespace(instance_id=instance_id, status=status)

    def has_live_work(self, instance_id: str) -> bool:
        if self.live_error is not None:
            raise self.live_error
        return self.live


class FakeQueueRepo:
    """The queue-side reads: the PREFIX evidence + the masked probe."""

    def __init__(self, *, delivered: bool = False, has_rows: bool = True):
        self.delivered = delivered
        self.has_rows = has_rows
        self.evidence_error: Exception | None = None
        self.masked_error: Exception | None = None

    def find_wake_already_delivered_evidence(self, parent_id, child_id) -> bool:
        if self.evidence_error is not None:
            raise self.evidence_error
        return self.delivered

    def has_row_since(self, instance_id, since) -> bool:
        if self.masked_error is not None:
            raise self.masked_error
        return self.has_rows


class FakeInjectionRepo:
    """The report-injection repository: LEG 1 (the evidence AUTHORITY).

    ``find_delivered_for_pair`` is the only method the mission-
    reconciliation lane reads from it. ``error`` forces the leg to
    FAULT — the authority-fault pin (F2 re-anchor): only leg 1 may HOLD
    the row on a probe error.
    """

    def __init__(self, *, rows=None, error: Exception | None = None):
        self.rows = rows or []
        self.error = error

    def find_delivered_for_pair(self, parent_id, child_id):
        if self.error is not None:
            raise self.error
        return list(self.rows)


class FakeBus:
    """The dependency bus: the PENDING-watcher probe (R4)."""

    def __init__(self, *, pending: bool = False):
        self.pending = pending
        self.error: Exception | None = None

    def pending_watchers_for_pair(self, target, child):
        if self.error is not None:
            raise self.error
        return [SimpleNamespace(watcher_id="w-1")] if self.pending else []


class FakeJobRepo:
    """The job repository: the JobItem-live probe (OQ-7)."""

    def __init__(self, *, admission_state: str | None = None):
        self.admission_state = admission_state
        self.error: Exception | None = None

    def get(self, job_id):
        if self.error is not None:
            raise self.error
        if self.admission_state is None:
            return None
        return SimpleNamespace(job_id=job_id, admission_state=self.admission_state)


def _build_service(
    engine,
    *,
    manager_loop,
    mode: str = "enforce",
    instance_repo=None,
    queue_repo=None,
    bus=None,
    job_repo=None,
    mission_record_repo="default",
    lane_enabled: bool = True,
    ttl_seconds: int = TTL_SECONDS,
    enabled: bool = True,
    mission_record_service=None,
    report_injection_repo=None,
):
    """Build the sweep service with the fakes wired."""
    import os

    os.environ[MISSION_RECORDS_MODE_ENV] = mode
    _reset_mission_records_mode_for_tests()

    manager = MagicMock()
    manager._loop = manager_loop
    manager._checkpointer = SimpleNamespace(name="fake-checkpointer")
    manager._job_queue_service = None

    repo = (
        MissionRecordRepository(engine)
        if mission_record_repo == "default"
        else mission_record_repo
    )

    service = ReportDeliveryRecoveryService(
        task_repo=MagicMock(),
        report_injection_repo=(
            report_injection_repo
            if report_injection_repo is not None
            else ReportInjectionRepository(engine=engine)
        ),
        queue_repo=queue_repo if queue_repo is not None else FakeQueueRepo(),
        instance_repo=(
            instance_repo if instance_repo is not None else FakeInstanceRepo()
        ),
        manager_ref=manager,
        interval_seconds=300,
        age_bound_minutes=10,
        batch_cap=50,
        enabled=enabled,
        lane_deferred=False,
        lane_no_row_backstop=False,
        lane_pending_age=False,
        lane_recovery_retry=False,
        lane_orphan=False,
        lane_stuck_wake=False,
        lane_mission_reconciliation=lane_enabled,
        mission_record_repo=repo,
        # MAJ-6: the sweep's WRITER is the mission-record SERVICE (the
        # ONE guarded UPDATE that owns the resolution stamps).
        mission_record_service=(
            mission_record_service
            if mission_record_service is not None
            else MissionRecordService(engine)
        ),
        dependency_bus=bus if bus is not None else FakeBus(),
        job_repo=job_repo if job_repo is not None else FakeJobRepo(),
        mission_commission_ttl_seconds=ttl_seconds,
    )
    return service, manager


def _seed_instance(engine, instance_id: str, status: str = "running") -> None:
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="leader",
                agent_dir=f"agents/leader/{instance_id[:8]}",
                status=status,
                project_id=None,
            )
        )
        session.commit()


def _mint_commission(
    engine,
    *,
    mission_id: str = "mission-1",
    commissioner: str = "commissioner-1",
    worker: str = "worker-1",
    age_seconds: int = 0,
    created_at: str | None = None,
    work_id: str | None = None,
    advisory: bool = False,
) -> str:
    """Insert one outstanding commission row."""
    from datetime import datetime, timedelta, timezone

    commission_id = str(uuid.uuid4())
    stamp = created_at
    if stamp is None:
        stamp = (
            datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
        ).isoformat()
    with Session(engine) as session:
        session.add(
            MissionCommission(
                commission_id=commission_id,
                mission_id=mission_id,
                commissioner_instance_id=commissioner,
                worker_instance_id=worker,
                work_id=work_id,
                lane="send_message",
                state=CommissionState.OUTSTANDING.value,
                created_at=stamp,
                advisory_backfill=advisory,
            )
        )
        session.commit()
    return commission_id


def _row(engine, commission_id: str) -> MissionCommission | None:
    with Session(engine) as session:
        return session.get(MissionCommission, commission_id)


def _iso_seconds_ago(seconds: float) -> str:
    """An aware UTC ISO stamp ``seconds`` in the past."""
    from datetime import datetime, timedelta, timezone

    return (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).isoformat()


def _seed_injection(
    engine,
    *,
    state: str,
    created_at: str,
    parent: str = "commissioner-1",
    child: str = "worker-1",
    child_message_id: str = "m-1",
) -> str:
    """Insert one ``report_injections`` row for the (parent, child) pair."""
    from daemon.repositories.report_injection.models import ReportInjection

    injection_id = str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            ReportInjection(
                injection_id=injection_id,
                parent_instance_id=parent,
                child_instance_id=child,
                child_message_id=child_message_id,
                created_at=created_at,
                state=state,
            )
        )
        session.commit()
    return injection_id


@pytest.fixture(autouse=True)
def _no_parent_history(monkeypatch):
    """The parent-history PREFIX ledger reads NO evidence by default."""
    import daemon.persistence as persistence

    async def _fake_messages(checkpointer, parent_id, manager=None):
        return []

    monkeypatch.setattr(persistence, "get_instance_messages", _fake_messages)


# ---------------------------------------------------------------------------
# T1 — every probe error HOLDS the row (fail-MISS)
# ---------------------------------------------------------------------------


class TestTheProbeErrorsHold:
    """P1: an unprovable probe must never authorize the transition."""

    def test_the_worker_read_error_holds_the_row(self, engine, manager_loop):
        commission_id = _mint_commission(engine, age_seconds=99_999)
        instance_repo = FakeInstanceRepo()
        instance_repo.get_error = RuntimeError("db down")
        service, _ = _build_service(
            engine, manager_loop=manager_loop, instance_repo=instance_repo
        )

        result = service.recover_now()
        lane = result.lanes["mission_reconciliation"]

        assert lane.probe_errors == 1
        assert lane.abandoned_worker_terminal == 0
        assert lane.abandoned_ttl == 0
        assert lane.recovered == 0
        row = _row(engine, commission_id)
        assert row is not None
        assert row.state == CommissionState.OUTSTANDING.value
        assert row.abandon_reason is None
        assert row.resolved_at is None

    def test_the_liveness_probe_error_holds_the_row(self, engine, manager_loop):
        commission_id = _mint_commission(engine, age_seconds=99_999)
        instance_repo = FakeInstanceRepo(statuses={"worker-1": "running"})
        instance_repo.live_error = RuntimeError("probe exploded")
        service, _ = _build_service(
            engine, manager_loop=manager_loop, instance_repo=instance_repo
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.probe_errors == 1
        assert lane.abandoned_ttl == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )

    def test_the_bus_probe_error_holds_the_row(self, engine, manager_loop):
        commission_id = _mint_commission(engine, age_seconds=99_999)
        bus = FakeBus()
        bus.error = RuntimeError("bus down")
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
            bus=bus,
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.probe_errors == 1
        assert lane.abandoned_ttl == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )

    def test_the_delivery_evidence_probe_error_holds_the_row(
        self, engine, manager_loop
    ):
        """F2 RE-ANCHOR: the fault is forced in the AUTHORITY leg.

        **The MECHANISM changed because the leader ruling changed the
        contract.** The old pin forced the fault in the QUEUE repo
        (``FakeQueueRepo.evidence_error``) — that is LEG 2, which the
        ruling demoted to a DIAGNOSTIC leg. A diagnostic leg's fault
        does NOT hold the row (the family's own contract now says the
        diagnostic fault is counted in its own counter and the row
        proceeds); ONLY the AUTHORITY leg (leg 1 — the commission-bound,
        state-filtered injection-row read) may HOLD.

        So the family's contract ("a probe error holds the row") is
        re-anchored onto leg 1: the report-injection repository's
        ``find_delivered_for_pair`` raises. The assertion shape is
        unchanged (probe error ⇒ HELD, zero writes, no resolution).
        """
        commission_id = _mint_commission(engine, age_seconds=99_999)
        injection_repo = FakeInjectionRepo(
            error=RuntimeError("injection repo down")
        )
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
            report_injection_repo=injection_repo,
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.probe_errors == 1
        # The diagnostic legs did not fault ⇒ their own counter stays 0.
        assert lane.diagnostic_probe_errors == 0
        assert lane.abandoned_worker_terminal == 0
        assert lane.abandoned_ttl == 0
        row = _row(engine, commission_id)
        assert row.state == CommissionState.OUTSTANDING.value
        assert row.abandon_reason is None
        assert row.resolved_at is None

    def test_the_unparsable_created_at_holds_the_row(self, engine, manager_loop):
        commission_id = _mint_commission(engine, created_at="not-a-timestamp")
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.probe_errors == 1
        assert lane.abandoned_worker_terminal == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )

    def test_the_unwired_bus_holds_the_row(self, engine, manager_loop):
        """An unwired dependency is a probe that CANNOT RUN — HOLD."""
        commission_id = _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
            bus=None,
        )
        # Force the unwired shape (the constructor's default is a fake).
        service._dependency_bus = None
        service._resolve_dependency_bus = lambda: None  # type: ignore[method-assign]

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.probe_errors == 1
        assert lane.abandoned_worker_terminal == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )


# ---------------------------------------------------------------------------
# T2 — the TTL matrix
# ---------------------------------------------------------------------------


class TestTheTtlMatrix:
    """P3 / R2: the time-bounded fail-MISS backstop."""

    def test_a_live_old_worker_is_held(self, engine, manager_loop):
        commission_id = _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(
                statuses={"worker-1": "running"}, live=True
            ),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.skipped_live == 1
        assert lane.abandoned_ttl == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )

    def test_an_idle_old_worker_is_abandoned(self, engine, manager_loop):
        commission_id = _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.abandoned_ttl == 1
        assert lane.recovered == 1
        row = _row(engine, commission_id)
        assert row.state == CommissionState.ABANDONED.value
        assert row.abandon_reason == "ttl_expired_not_live"
        assert row.resolved_at is not None

    def test_a_missing_worker_row_is_abandoned_when_old(
        self, engine, manager_loop
    ):
        commission_id = _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.abandoned_worker_missing == 1
        row = _row(engine, commission_id)
        assert row.state == CommissionState.ABANDONED.value
        assert row.abandon_reason == "worker_row_missing"

    def test_a_missing_worker_row_is_held_when_young(self, engine, manager_loop):
        commission_id = _mint_commission(engine, age_seconds=10)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.below_ttl_hold == 1
        assert lane.abandoned_worker_missing == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )

    def test_an_unparsable_created_at_is_held(self, engine, manager_loop):
        commission_id = _mint_commission(engine, created_at="???")
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.probe_errors == 1
        assert lane.abandoned_ttl == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )


# ---------------------------------------------------------------------------
# T3 — R1 (worker terminal) + R1b (satisfy on evidence)
# ---------------------------------------------------------------------------


class TestTheWorkerTerminalRule:
    def test_a_terminal_worker_without_evidence_is_abandoned(
        self, engine, manager_loop
    ):
        commission_id = _mint_commission(engine)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "completed"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.abandoned_worker_terminal == 1
        assert lane.recovered == 1
        row = _row(engine, commission_id)
        assert row.state == CommissionState.ABANDONED.value
        assert row.abandon_reason == "worker_terminal_no_report"

    def test_a_paused_worker_is_not_terminal(self, engine, manager_loop):
        commission_id = _mint_commission(engine)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "paused"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.abandoned_worker_terminal == 0
        assert lane.below_ttl_hold == 1
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )

    def test_delivery_evidence_satisfies_the_commission(
        self, engine, manager_loop
    ):
        """R1b: the delivery happened; the natural satisfy write was lost.

        F2 RE-ANCHOR: the evidence is now the AUTHORITY leg's — a
        DELIVERED injection row bound to THIS commission. The old pin
        drove the satisfy from the queue-side PREFIX ledger (leg 2),
        which the leader ruling demoted to DIAGNOSTIC ONLY (it may
        never authorise a ``satisfied``).
        """
        from daemon.repositories.report_injection.models import (
            ReportInjectionState,
        )

        commission_id = _mint_commission(
            engine, created_at=_iso_seconds_ago(60)
        )
        _seed_injection(
            engine,
            state=ReportInjectionState.INJECTED.value,
            created_at=_iso_seconds_ago(30),
        )
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "completed"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.satisfied_on_evidence == 1
        assert lane.abandoned_worker_terminal == 0
        row = _row(engine, commission_id)
        assert row.state == CommissionState.SATISFIED.value
        assert row.resolved_at is not None

    def test_an_injection_row_is_delivery_evidence(self, engine, manager_loop):
        """MAJ-1: a DELIVERED injection row IS evidence — for the
        commission that was minted BEFORE it."""
        from daemon.repositories.report_injection.models import (
            ReportInjectionState,
        )

        commission_id = _mint_commission(
            engine, created_at=_iso_seconds_ago(7200)
        )
        _seed_injection(
            engine,
            state=ReportInjectionState.INJECTED.value,
            created_at=_iso_seconds_ago(3600),
        )
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "completed"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.satisfied_on_evidence == 1
        assert lane.abandoned_worker_terminal == 0
        assert _row(engine, commission_id).state == (
            CommissionState.SATISFIED.value
        )

    def test_a_pending_bus_watcher_holds_the_row(self, engine, manager_loop):
        commission_id = _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
            bus=FakeBus(pending=True),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.skipped_watcher_pending == 1
        assert lane.abandoned_ttl == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )


# ---------------------------------------------------------------------------
# MAJ-1 — the delivery-evidence probe is DELIVERED-states-only and
#          bounded to THIS commission
# ---------------------------------------------------------------------------


class TestTheDeliveryEvidenceBound:
    """MAJ-1 (the binding ruling): the evidence read is narrowed.

    The probe was doubly coarse:

    * (a) NO STATE FILTER — a PENDING / DEFERRED obligation row (the
      delivery has NOT happened) or a FAILED row (the delivery path
      gave up) counted as delivery proof;
    * (b) NO COMMISSION LINKAGE — a PRIOR commission's injection row
      satisfied a FRESH commission on the same (commissioner, worker)
      pair.

    Both produced the same harm: a premature ``satisfied`` ⇒ a premature
    publish (the D2 class, re-created from the far side).
    """

    def test_a_prior_commissions_delivery_does_not_satisfy_the_new_commission(
        self, engine, manager_loop
    ):
        """The PIN: two commissions on one pair; the FIRST has a
        delivered injection row; the SECOND stays OUTSTANDING."""
        from daemon.repositories.report_injection.models import (
            ReportInjectionState,
        )

        # Commission A (older) — minted 2 h ago.
        first_id = _mint_commission(
            engine, created_at=_iso_seconds_ago(7200)
        )
        # The delivery evidence — written 1 h ago: AFTER A's mint, BEFORE
        # B's mint. It is A's evidence, not B's.
        _seed_injection(
            engine,
            state=ReportInjectionState.INJECTED.value,
            created_at=_iso_seconds_ago(3600),
        )
        # Commission B (newer) — minted 60 s ago (below the TTL, so the
        # OTHER legs hold it rather than abandoning it).
        second_id = _mint_commission(
            engine, created_at=_iso_seconds_ago(60)
        )
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        # A is satisfied by ITS delivery …
        assert lane.satisfied_on_evidence == 1
        assert _row(engine, first_id).state == CommissionState.SATISFIED.value
        # … and B is NOT: the prior commission's delivery is not B's
        # evidence, so B stays outstanding (held below the TTL).
        assert _row(engine, second_id).state == (
            CommissionState.OUTSTANDING.value
        )
        assert lane.abandoned_ttl == 0
        assert lane.abandoned_worker_terminal == 0

    def test_an_undelivered_injection_row_is_not_delivery_evidence(
        self, engine, manager_loop
    ):
        """A PENDING obligation row is NOT a delivery (the state filter)."""
        from daemon.repositories.report_injection.models import (
            ReportInjectionState,
        )

        commission_id = _mint_commission(
            engine, created_at=_iso_seconds_ago(7200)
        )
        _seed_injection(
            engine,
            state=ReportInjectionState.PENDING.value,
            created_at=_iso_seconds_ago(3600),
        )
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "completed"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.satisfied_on_evidence == 0
        # No evidence ⇒ the terminal-worker leg abandons it (the row is
        # NOT satisfied by an undelivered obligation).
        assert lane.abandoned_worker_terminal == 1
        assert _row(engine, commission_id).state == (
            CommissionState.ABANDONED.value
        )

    def test_a_failed_injection_row_is_not_delivery_evidence(
        self, engine, manager_loop
    ):
        """A FAILED row is a delivery FAILURE — not a delivery."""
        from daemon.repositories.report_injection.models import (
            ReportInjectionState,
        )

        commission_id = _mint_commission(
            engine, created_at=_iso_seconds_ago(7200)
        )
        _seed_injection(
            engine,
            state=ReportInjectionState.FAILED.value,
            created_at=_iso_seconds_ago(3600),
        )
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "completed"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.satisfied_on_evidence == 0
        assert lane.abandoned_worker_terminal == 1
        assert _row(engine, commission_id).state == (
            CommissionState.ABANDONED.value
        )

    def test_the_repository_read_returns_the_delivered_states_only(
        self, engine
    ):
        """The repository read itself is state-filtered (the pin on the
        data plane, not only on the sweep's verdict)."""
        from daemon.repositories.report_injection.models import (
            ReportInjectionState,
        )
        from daemon.repositories.report_injection.repository import (
            ReportInjectionRepository,
        )

        _seed_injection(
            engine,
            state=ReportInjectionState.PENDING.value,
            created_at=_iso_seconds_ago(100),
            child_message_id="m-pending",
        )
        _seed_injection(
            engine,
            state=ReportInjectionState.DEFERRED.value,
            created_at=_iso_seconds_ago(90),
            child_message_id="m-deferred",
        )
        _seed_injection(
            engine,
            state=ReportInjectionState.FAILED.value,
            created_at=_iso_seconds_ago(80),
            child_message_id="m-failed",
        )
        _seed_injection(
            engine,
            state=ReportInjectionState.INJECTED.value,
            created_at=_iso_seconds_ago(70),
            child_message_id="m-injected",
        )
        _seed_injection(
            engine,
            state=ReportInjectionState.TASK_DELIVERED.value,
            created_at=_iso_seconds_ago(60),
            child_message_id="m-task-delivered",
        )

        rows = ReportInjectionRepository(engine=engine).find_delivered_for_pair(
            "commissioner-1", "worker-1"
        )

        assert {row.state for row in rows} == {
            ReportInjectionState.INJECTED.value,
            ReportInjectionState.TASK_DELIVERED.value,
        }
        assert len(rows) == 2
        # The former state-AGNOSTIC read is GONE (one spelling only).
        assert not hasattr(
            ReportInjectionRepository, "find_any_for_pair"
        )


# ---------------------------------------------------------------------------
# F2 — the LEADER RULING: the delivery-evidence legs 2/3 are DIAGNOSTIC
#       ONLY (they never authorise a transition)
# ---------------------------------------------------------------------------


class TestTheDiagnosticEvidenceLegs:
    """F2 (the leader ruling): ONE authority, TWO diagnostics.

    The delivery-evidence read used to be a TRILET whose three legs
    were interchangeable: any of them could authorise the ``satisfied``
    transition. Legs 2 (the queue-side PREFIX ledger) and 3 (the
    parent-history PREFIX ledger) are PAIR-keyed and TIME-unbounded —
    they carry no commission id — so an EARLIER commission's delivery
    (or a mere ENQUEUE, which leg 2 counts as evidence) satisfied a
    FRESH commission and released the publish hold EARLY (the
    premature-publish harm class).

    The ruling: legs 2/3 may NEVER authorise a ``satisfied`` and may
    NEVER authorise a hold-against-abandon. Leg 1 (the commission-bound,
    state-filtered injection-row read) is the ONLY authority. The
    diagnostic legs KEEP RUNNING (their result is logged; their FAULTS
    are counted in their own counter).
    """

    def test_an_earlier_commissions_queue_enqueue_does_not_satisfy_a_fresh_commission(
        self, engine, manager_loop
    ):
        """(a) THE F2 TRACE: leg 2 says "delivered", leg 1 says nothing.

        The queue-side PREFIX ledger reports a delivery on the pair
        (here: an EARLIER commission's enqueue — the ledger carries no
        commission id, so it cannot tell), and there is NO leg-1
        injection row for THIS commission. The fresh commission must
        NOT be satisfied.
        """
        commission_id = _mint_commission(
            engine, created_at=_iso_seconds_ago(60)
        )
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
            queue_repo=FakeQueueRepo(delivered=True),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.satisfied_on_evidence == 0
        assert lane.recovered == 0
        row = _row(engine, commission_id)
        assert row.state == CommissionState.OUTSTANDING.value
        assert row.satisfied_at is None
        assert row.resolved_at is None
        assert row.abandon_reason is None

    def test_a_leg_one_delivered_commission_still_satisfies(
        self, engine, manager_loop
    ):
        """(b) The AUTHORITY leg still authorises the satisfy."""
        from daemon.repositories.report_injection.models import (
            ReportInjectionState,
        )

        commission_id = _mint_commission(
            engine, created_at=_iso_seconds_ago(60)
        )
        _seed_injection(
            engine,
            state=ReportInjectionState.TASK_DELIVERED.value,
            created_at=_iso_seconds_ago(30),
        )
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "completed"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.satisfied_on_evidence == 1
        row = _row(engine, commission_id)
        assert row.state == CommissionState.SATISFIED.value
        assert row.resolved_at is not None

    def test_a_fresh_commission_without_leg_one_evidence_is_not_satisfied(
        self, engine, manager_loop
    ):
        """(c) NO leg-1 evidence ⇒ NOT satisfied (and not yet abandoned).

        The row is YOUNG and its worker is non-terminal ⇒ the TTL
        backstop has not fired ⇒ the row is HELD (``below_ttl_hold``).
        The assertion proves "not satisfied", not "abandoned".

        The DIAGNOSTIC legs are deliberately POSITIVE here (the queue
        ledger reports a delivery on the pair) so the pin proves the
        AUTHORITY — the absence of a leg-1 row is the ONLY reason the
        row is not satisfied, not the absence of any signal at all.
        """
        commission_id = _mint_commission(
            engine, created_at=_iso_seconds_ago(60)
        )
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
            queue_repo=FakeQueueRepo(delivered=True),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.satisfied_on_evidence == 0
        assert lane.below_ttl_hold == 1
        assert lane.abandoned_ttl == 0
        row = _row(engine, commission_id)
        assert row.state == CommissionState.OUTSTANDING.value
        assert row.satisfied_at is None
        assert row.resolved_at is None

    def test_a_diagnostic_probe_fault_does_not_hold_the_row(
        self, engine, manager_loop
    ):
        """(d) A DIAGNOSTIC leg's fault does NOT hold the row.

        The ruling's promise is that the publish hold "releases NO
        EARLIER than the TTL horizon — fail-closed, bounded, safe". A
        diagnostic leg that kept faulting would HOLD the row on every
        pass ⇒ an UNBOUNDED hold ⇒ the promise broken. So the fault is
        logged + counted in its OWN counter (``diagnostic_probe_errors``)
        and the row proceeds to the TTL backstop. ``probe_errors`` stays
        0 — its contract ("a DECISION probe could not prove its
        predicate ⇒ the row was HELD") is untouched.
        """
        commission_id = _mint_commission(engine, age_seconds=99_999)
        queue_repo = FakeQueueRepo()
        queue_repo.evidence_error = RuntimeError("queue down")
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
            queue_repo=queue_repo,
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        # The fault is counted in the DIAGNOSTIC counter, NOT in
        # ``probe_errors``.
        assert lane.diagnostic_probe_errors == 1
        assert lane.probe_errors == 0
        # The row is NOT held by the diagnostic fault — the TTL backstop
        # is still reachable.
        assert lane.abandoned_ttl == 1
        assert lane.recovered == 1
        row = _row(engine, commission_id)
        assert row.state == CommissionState.ABANDONED.value
        assert row.abandon_reason == "ttl_expired_not_live"

    def test_the_lane_dict_carries_the_diagnostic_counter(self):
        """The counter is SURFACED (the endpoint returns the lane dict)."""
        from daemon.services.report_delivery_recovery import (
            MissionReconciliationLaneResult,
        )

        payload = MissionReconciliationLaneResult().to_dict()
        assert payload["diagnostic_probe_errors"] == 0
        assert payload["probe_errors"] == 0
        assert payload["masked_probe_errors"] == 0

    def test_the_diagnostic_positive_result_is_logged(
        self, engine, manager_loop, caplog
    ):
        """V-F1: the diagnostic POSITIVE path is PINNED, not merely run.

        The F2 ruling says the diagnostic legs "keep running; their
        result is logged and counted" — but the code counts only the
        FAULTS (in ``diagnostic_probe_errors``); the POSITIVE result
        is LOGGED (the operator signal) and nothing else. Without a
        pin, DISCARDING the positive result entirely (the mutation:
        never set ``evidence = True``) left this whole file GREEN: the
        positive path was UNPINNED and could have been silently
        deleted without any test noticing.

        The pin asserts the operator-visible INFO line IS emitted for a
        commission whose leg-2 (queue-side PREFIX ledger) read returns
        POSITIVE. It also pins the DIAGNOSTIC-ONLY invariant on the
        same pass: the positive diagnostic must NOT satisfy the row.
        """
        commission_id = _mint_commission(
            engine, created_at=_iso_seconds_ago(60)
        )
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
            queue_repo=FakeQueueRepo(delivered=True),
        )

        with caplog.at_level(
            logging.INFO, logger="daemon.services.report_delivery_recovery"
        ):
            lane = service.recover_now().lanes["mission_reconciliation"]

        diagnostic_lines = [
            record.getMessage()
            for record in caplog.records
            if "DIAGNOSTIC delivery evidence" in record.getMessage()
        ]
        assert diagnostic_lines, (
            "the diagnostic legs' POSITIVE result MUST be LOGGED (the "
            "operator signal the F2 ruling promises) — no INFO line was "
            "emitted, so the positive path is UNPINNED"
        )
        assert commission_id in diagnostic_lines[0], (
            "the diagnostic INFO line MUST carry the commission id (the "
            "operator needs to trace WHICH row); got "
            f"{diagnostic_lines[0]!r}"
        )
        # The diagnostic stays DIAGNOSTIC-ONLY: the positive leg-2
        # signal never satisfies the row (leg 1 is the only authority).
        assert lane.satisfied_on_evidence == 0
        row = _row(engine, commission_id)
        assert row.state == CommissionState.OUTSTANDING.value


# ---------------------------------------------------------------------------
# T4 — the guarded transition (exactly-once)
# ---------------------------------------------------------------------------


class TestTheGuardedTransition:
    def test_a_lost_race_counts_already_resolved_and_never_retries(
        self, engine, manager_loop
    ):
        """The guarded transition's rowcount is the arbiter.

        The row is ADMITTED as outstanding and another actor resolves it
        between the admission read and the write → the guarded
        transition's rowcount is 0 → ``already_resolved`` (never a retry
        in place, never an overwrite of the winner's transition).
        """
        commission_id = _mint_commission(engine)
        repo = MagicMock(wraps=MissionRecordRepository(engine))
        # The race: the guarded transition moves NOTHING. MAJ-6: the
        # write goes through the SERVICE (the one writer), so the race
        # is simulated on the service seam.
        mission_service = MissionRecordService(engine)
        mission_service.abandon_commission = MagicMock(return_value=False)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            mission_record_repo=repo,
            mission_record_service=mission_service,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "completed"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.already_resolved == 1
        assert lane.recovered == 0
        assert lane.abandoned_worker_terminal == 0
        row = _row(engine, commission_id)
        assert row.state == CommissionState.OUTSTANDING.value

    def test_an_already_resolved_row_is_never_admitted(
        self, engine, manager_loop
    ):
        """A row another actor already resolved is not outstanding —
        the ADMISSION query never sees it."""
        commission_id = _mint_commission(engine)
        repo = MissionRecordRepository(engine)
        assert (
            repo.transition_commission_state(
                commission_id, CommissionState.SATISFIED.value
            )
            is True
        )
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "completed"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.abandoned_worker_terminal == 0
        assert lane.already_resolved == 0
        assert lane.below_ttl_hold == 0


# ---------------------------------------------------------------------------
# T5 — the mode ladder
# ---------------------------------------------------------------------------


class TestTheModeLadder:
    def test_off_does_not_even_query_the_lane(self, engine, manager_loop):
        _mint_commission(engine, age_seconds=99_999)
        repo = MagicMock(wraps=MissionRecordRepository(engine))
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            mode="off",
            mission_record_repo=repo,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert repo.find_outstanding_commissions.call_count == 0
        assert lane.abandoned_ttl == 0
        assert lane.probe_errors == 0

    def test_dry_detects_but_never_writes(self, engine, manager_loop):
        commission_id = _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            mode="dry",
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.abandoned_ttl == 1
        assert lane.recovered == 0
        row = _row(engine, commission_id)
        assert row.state == CommissionState.OUTSTANDING.value
        assert row.resolved_at is None

    def test_enforce_writes(self, engine, manager_loop):
        commission_id = _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            mode="enforce",
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.abandoned_ttl == 1
        assert lane.recovered == 1
        assert _row(engine, commission_id).state == (
            CommissionState.ABANDONED.value
        )


# ---------------------------------------------------------------------------
# T6 — the sweep writes ONLY mission_commissions
# ---------------------------------------------------------------------------


class TestTheWriteBoundary:
    def test_the_sweep_writes_no_instance_row(self, engine, manager_loop):
        _seed_instance(engine, "worker-1", status="running")
        _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
        )

        service.recover_now()

        with Session(engine) as session:
            row = session.get(Instance, "worker-1")
        assert row is not None
        assert row.status == "running"

    def test_the_admission_state_writer_census_is_untouched(self):
        """R6: the sweep adds NO admission-state writer."""
        from pathlib import Path as _Path

        census = _Path(__file__).resolve().parents[2] / (
            "tests/job_queue/test_terminal_write_census.py"
        )
        source = census.read_text(encoding="utf-8")
        assert "report_delivery_recovery" not in source

    def test_the_sweep_writes_no_mission_record_row(self, engine, manager_loop):
        from daemon.repositories.mission_record.models import MissionRecord

        _mint_commission(engine, age_seconds=99_999)
        with Session(engine) as session:
            session.add(
                MissionRecord(
                    mission_id="mission-1",
                    anchor_instance_id="commissioner-1",
                    epoch_seq=1,
                    founding_source="user_api",
                    status="open",
                )
            )
            session.commit()
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
        )

        service.recover_now()

        with Session(engine) as session:
            record = session.get(MissionRecord, "mission-1")
        assert record is not None
        assert record.status == "open"
        assert record.settled_at is None


# ---------------------------------------------------------------------------
# MAJ-2 — the MASTER kill-switch is honoured by the on-demand entry point
# ---------------------------------------------------------------------------


class TestTheMasterKillSwitch:
    """MAJ-2: the crash-recovery endpoint must not run the sweep with the
    master kill-switch OFF.

    The service is CONSTRUCTED even when ``enabled=False`` (the wiring
    passes the knob through), so "wired" was never "enabled" — and
    ``recover_now()`` never looked at the switch at all. In ``enforce``
    that let the operator-facing endpoint WRITE ``mission_commissions``
    rows while the master kill-switch was OFF.
    """

    def test_a_disabled_service_runs_no_lane(self, engine, manager_loop):
        """THE PIN: master off + recover_now() ⇒ lane 7 is ABSENT from
        the returned result's lanes (an empty SweepResult)."""
        _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine, manager_loop=manager_loop, enabled=False
        )

        result = service.recover_now()

        assert result.lanes == {}
        assert result.total_recovered == 0

    def test_a_disabled_service_writes_no_commission_row(
        self, engine, manager_loop
    ):
        """The switch gates the WRITES too — not just the counters."""
        commission_id = _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine, manager_loop=manager_loop, enabled=False
        )

        service.recover_now()

        row = _row(engine, commission_id)
        assert row.state == CommissionState.OUTSTANDING.value
        assert row.resolved_at is None


# ---------------------------------------------------------------------------
# T7 — the lane wiring (order, kill-switch, unwired degrade)
# ---------------------------------------------------------------------------


class TestTheLaneWiring:
    def test_lane_seven_runs_after_the_delivery_lanes(self, engine, manager_loop):
        service, _ = _build_service(engine, manager_loop=manager_loop)
        service._lane_deferred = True
        result = service.recover_now()
        lane_names = list(result.lanes.keys())
        assert lane_names[-1] == "mission_reconciliation"

    def test_the_lane_kill_switch_removes_it(self, engine, manager_loop):
        service, _ = _build_service(
            engine, manager_loop=manager_loop, lane_enabled=False
        )
        result = service.recover_now()
        assert "mission_reconciliation" not in result.lanes

    def test_the_unwired_repository_degrades_to_zero(self, engine, manager_loop):
        service, _ = _build_service(
            engine, manager_loop=manager_loop, mission_record_repo=None
        )
        result = service.recover_now()
        lane = result.lanes["mission_reconciliation"]
        assert lane.probe_errors == 0
        assert lane.abandoned_ttl == 0
        assert lane.recovered == 0

    def test_the_stop_event_is_honored_mid_batch(self, engine, manager_loop):
        for index in range(5):
            _mint_commission(engine, worker=f"worker-{index}")
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(
                statuses={f"worker-{i}": "completed" for i in range(5)}
            ),
        )
        service._stop_event.set()
        lane = service.recover_now().lanes["mission_reconciliation"]
        assert lane.abandoned_worker_terminal == 0


# ---------------------------------------------------------------------------
# T8 — the masked-enqueue observability
# ---------------------------------------------------------------------------


class TestTheMaskedEnqueueObservability:
    def test_a_masked_commission_is_counted_but_never_abandoned(
        self, engine, manager_loop
    ):
        """The signal is OBSERVATION ONLY: no auto-action."""
        commission_id = _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
            queue_repo=FakeQueueRepo(has_rows=False),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.masked_suspected == 1
        row = _row(engine, commission_id)
        # The TTL abandoned it (the worker is idle + old) — the masked
        # signal itself NEVER abandoned anything.
        assert row.state == CommissionState.ABANDONED.value
        assert row.abandon_reason == "ttl_expired_not_live"

    def test_a_young_commission_is_never_flagged_as_masked(
        self, engine, manager_loop
    ):
        _mint_commission(engine, age_seconds=10)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
            queue_repo=FakeQueueRepo(has_rows=False),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.masked_suspected == 0

    def test_a_commission_with_queue_rows_is_not_flagged(
        self, engine, manager_loop
    ):
        _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
            queue_repo=FakeQueueRepo(has_rows=True),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.masked_suspected == 0


# ---------------------------------------------------------------------------
# MAJ-3 — the masked probe has its OWN counter and HOLDS the row
# ---------------------------------------------------------------------------


class TestTheMaskedProbeHolds:
    """MAJ-3: a masked-probe FAULT holds the row for that pass.

    The masked-enqueue signal is an OBSERVABILITY probe (it never
    authorises an action by itself), but its fault used to increment
    ``probe_errors`` — whose contract says "the row was HELD" — and
    then let the caller proceed to the abandon / the evidence probe in
    the SAME pass. The counter was lying about the row's fate.
    """

    def test_a_masked_probe_fault_holds_the_missing_worker_row(
        self, engine, manager_loop
    ):
        """THE PIN (the worker-missing branch): no abandon."""
        commission_id = _mint_commission(engine, age_seconds=99_999)
        queue_repo = FakeQueueRepo()
        queue_repo.masked_error = RuntimeError("queue read exploded")
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={}),
            queue_repo=queue_repo,
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.masked_probe_errors == 1
        assert lane.probe_errors == 0
        assert lane.abandoned_worker_missing == 0
        assert lane.abandoned_ttl == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )

    def test_a_masked_probe_fault_holds_the_terminal_worker_row(
        self, engine, manager_loop
    ):
        """THE PIN (the §6 site): no abandon."""
        commission_id = _mint_commission(engine, age_seconds=99_999)
        queue_repo = FakeQueueRepo()
        queue_repo.masked_error = RuntimeError("queue read exploded")
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "completed"}),
            queue_repo=queue_repo,
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.masked_probe_errors == 1
        assert lane.probe_errors == 0
        assert lane.abandoned_worker_terminal == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )

    def test_a_masked_probe_fault_holds_the_satisfy(self, engine, manager_loop):
        """THE PIN (the §6 site): no satisfy either."""
        from daemon.repositories.report_injection.models import (
            ReportInjectionState,
        )

        commission_id = _mint_commission(
            engine, created_at=_iso_seconds_ago(7200)
        )
        _seed_injection(
            engine,
            state=ReportInjectionState.INJECTED.value,
            created_at=_iso_seconds_ago(3600),
        )
        queue_repo = FakeQueueRepo()
        queue_repo.masked_error = RuntimeError("queue read exploded")
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "completed"}),
            queue_repo=queue_repo,
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.masked_probe_errors == 1
        assert lane.probe_errors == 0
        assert lane.satisfied_on_evidence == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )


# ---------------------------------------------------------------------------
# T9 — the endpoint shape
# ---------------------------------------------------------------------------


class TestTheEndpointShape:
    def test_the_lane_dict_exposes_the_dispositions(self, engine, manager_loop):
        _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
        )

        payload = service.recover_now().to_dict()
        lane = payload["lanes"]["mission_reconciliation"]

        assert lane["abandoned_ttl"] == 1
        assert lane["skipped_live"] == 0
        assert lane["probe_errors"] == 0
        assert lane["masked_probe_errors"] == 0
        assert lane["diagnostic_probe_errors"] == 0
        assert lane["masked_suspected"] == 0
        assert lane["already_resolved"] == 0
        assert lane["below_ttl_hold"] == 0
        assert lane["errors"] == 0

    def test_probe_errors_and_skipped_live_are_distinguishable(
        self, engine, manager_loop
    ):
        _mint_commission(engine, worker="live-worker", age_seconds=99_999)
        _mint_commission(engine, worker="dead-worker", age_seconds=99_999)
        instance_repo = FakeInstanceRepo(
            statuses={"dead-worker": "running", "live-worker": "running"}
        )
        instance_repo.live = False
        service, _ = _build_service(
            engine, manager_loop=manager_loop, instance_repo=instance_repo
        )
        # The live worker: has_live_work returns True for it only.
        original_has_live_work = instance_repo.has_live_work

        def _has_live_work(instance_id: str) -> bool:
            return instance_id == "live-worker"

        instance_repo.has_live_work = _has_live_work  # type: ignore[method-assign]

        payload = service.recover_now().to_dict()
        lane = payload["lanes"]["mission_reconciliation"]

        assert lane["skipped_live"] == 1
        assert lane["abandoned_ttl"] == 1
        assert lane["probe_errors"] == 0
        assert original_has_live_work is not None


# ---------------------------------------------------------------------------
# The escape-valve CHAIN (the design's §5 — T10's service-level leg)
# ---------------------------------------------------------------------------


class TestTheEscapeValveChain:
    def test_the_count_reads_zero_after_the_abandonment(
        self, engine, manager_loop
    ):
        repo = MissionRecordRepository(engine)
        commission_id = _mint_commission(engine, age_seconds=99_999)
        assert (
            repo.count_outstanding_commissions_for_mission("mission-1") == 1
        )
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
        )

        service.recover_now()

        assert (
            repo.count_outstanding_commissions_for_mission("mission-1") == 0
        )
        row = _row(engine, commission_id)
        assert row.state == CommissionState.ABANDONED.value
        assert row.abandon_reason == "ttl_expired_not_live"


# ---------------------------------------------------------------------------
# The TTL knob (env override + the house default)
# ---------------------------------------------------------------------------


class TestTheTtlKnob:
    def test_the_house_default_is_six_hours(self):
        from daemon.constants import (
            DEFAULT_MISSION_COMMISSION_TTL_SECONDS,
            MISSION_COMMISSION_TTL_ENV,
        )

        assert DEFAULT_MISSION_COMMISSION_TTL_SECONDS == 21600
        assert MISSION_COMMISSION_TTL_ENV == (
            "ENSEMBLE_MISSION_COMMISSION_TTL_SECONDS"
        )

    def test_the_env_knob_overrides_the_configured_default(self, monkeypatch):
        from daemon.constants import MISSION_COMMISSION_TTL_ENV
        from daemon.services.mission_record_service import (
            _reset_mission_commission_ttl_for_tests,
            resolve_mission_commission_ttl_seconds,
        )

        monkeypatch.setenv(MISSION_COMMISSION_TTL_ENV, "60")
        _reset_mission_commission_ttl_for_tests()
        try:
            assert resolve_mission_commission_ttl_seconds(default=3600) == 60
        finally:
            _reset_mission_commission_ttl_for_tests()

    def test_an_invalid_env_knob_falls_back_and_never_raises(self, monkeypatch):
        from daemon.constants import MISSION_COMMISSION_TTL_ENV
        from daemon.services.mission_record_service import (
            _reset_mission_commission_ttl_for_tests,
            resolve_mission_commission_ttl_seconds,
        )

        monkeypatch.setenv(MISSION_COMMISSION_TTL_ENV, "not-a-number")
        _reset_mission_commission_ttl_for_tests()
        try:
            assert resolve_mission_commission_ttl_seconds(default=3600) == 3600
        finally:
            _reset_mission_commission_ttl_for_tests()

    def test_without_the_env_knob_the_configured_default_applies(
        self, monkeypatch
    ):
        from daemon.constants import MISSION_COMMISSION_TTL_ENV
        from daemon.services.mission_record_service import (
            _reset_mission_commission_ttl_for_tests,
            resolve_mission_commission_ttl_seconds,
        )

        monkeypatch.delenv(MISSION_COMMISSION_TTL_ENV, raising=False)
        _reset_mission_commission_ttl_for_tests()
        try:
            assert resolve_mission_commission_ttl_seconds(default=99) == 99
        finally:
            _reset_mission_commission_ttl_for_tests()


# ---------------------------------------------------------------------------
# The advisory-row exclusion (ruling: advisory rows are audit-only)
# ---------------------------------------------------------------------------


class TestTheAdvisoryExclusion:
    def test_an_advisory_row_is_never_admitted(self, engine, manager_loop):
        _mint_commission(engine, age_seconds=99_999, advisory=True)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.abandoned_ttl == 0
        assert lane.below_ttl_hold == 0


# ---------------------------------------------------------------------------
# MAJ-5 — the worker-missing branch runs the R4 bus-watcher probe FIRST
# ---------------------------------------------------------------------------


class TestTheWorkerMissingBusWatcher:
    """MAJ-5: the worker-missing branch must not abandon a commission
    whose obligation is still TRACKED.

    The branch used to abandon a past-TTL commission whenever the
    worker's own row had disappeared — WITHOUT asking the bus whether
    a pending watcher still tracks the obligation. A live obligation
    evidence (the R4 probe) must be read BEFORE the R2c abandon.
    """

    def test_a_pending_bus_watcher_holds_the_missing_worker_row(
        self, engine, manager_loop
    ):
        """THE PIN: worker row missing + past TTL + a pending bus
        watcher ⇒ NO abandon (held)."""
        commission_id = _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={}),
            bus=FakeBus(pending=True),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.skipped_watcher_pending == 1
        assert lane.abandoned_worker_missing == 0
        assert lane.abandoned_ttl == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )

    def test_a_bus_probe_fault_holds_the_missing_worker_row(
        self, engine, manager_loop
    ):
        """A bus-watcher probe that cannot run HOLDS the row (fail-MISS)."""
        commission_id = _mint_commission(engine, age_seconds=99_999)
        bus = FakeBus()
        bus.error = RuntimeError("bus down")
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={}),
            bus=bus,
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.probe_errors == 1
        assert lane.abandoned_worker_missing == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )

    def test_without_a_watcher_the_missing_worker_row_is_still_abandoned(
        self, engine, manager_loop
    ):
        """The R4 probe does not disarm the abandon: no watcher ⇒ the
        old behaviour stands."""
        commission_id = _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={}),
            bus=FakeBus(pending=False),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.skipped_watcher_pending == 0
        assert lane.abandoned_worker_missing == 1
        assert _row(engine, commission_id).state == (
            CommissionState.ABANDONED.value
        )


# ---------------------------------------------------------------------------
# MAJ-6 — ONE guarded-UPDATE writer for the commission resolution
# ---------------------------------------------------------------------------


class TestTheSingleResolutionWriter:
    """MAJ-6: the commission resolution has ONE implementation.

    Two guarded-UPDATE implementations of the same transition (the
    service path's ``transition_commission_state`` vs the sweep path's
    ``resolve_commission``) can drift on the ``resolved_at`` /
    ``satisfied_at`` stamps. The sweep now resolves through the
    SERVICE and the repository's duplicate is gone.
    """

    def test_the_sweep_resolves_through_the_service_seam(
        self, engine, manager_loop
    ):
        """THE PIN: the sweep's write INVOKES the service."""
        commission_id = _mint_commission(engine, age_seconds=99_999)
        mission_service = MagicMock(wraps=MissionRecordService(engine))
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            mission_record_service=mission_service,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.abandoned_ttl == 1
        mission_service.abandon_commission.assert_called_once()
        args, kwargs = mission_service.abandon_commission.call_args
        assert args[0] == commission_id
        assert kwargs["reason"] == "ttl_expired_not_live"

    def test_the_satisfy_on_evidence_goes_through_the_service_seam(
        self, engine, manager_loop
    ):
        """F2 RE-ANCHOR: the satisfy is driven by the AUTHORITY leg.

        The old pin drove it from the queue-side PREFIX ledger (leg 2),
        which the leader ruling demoted to DIAGNOSTIC ONLY. The seam
        being pinned here (the ONE guarded writer) is unchanged; only
        the evidence source moved to leg 1.
        """
        from daemon.repositories.report_injection.models import (
            ReportInjectionState,
        )

        commission_id = _mint_commission(
            engine, created_at=_iso_seconds_ago(60)
        )
        _seed_injection(
            engine,
            state=ReportInjectionState.INJECTED.value,
            created_at=_iso_seconds_ago(30),
        )
        mission_service = MagicMock(wraps=MissionRecordService(engine))
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            mission_record_service=mission_service,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "completed"}),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.satisfied_on_evidence == 1
        mission_service.satisfy_commission.assert_called_once_with(
            commission_id
        )

    def test_the_repository_has_no_second_guarded_update(self):
        """The duplicate repository method is GONE — one spelling."""
        assert not hasattr(MissionRecordRepository, "resolve_commission")
        assert hasattr(
            MissionRecordRepository, "transition_commission_state"
        )

    def test_the_resolution_stamps_survive_the_collapse(
        self, engine, manager_loop
    ):
        """The ONE writer still owns both stamps."""
        commission_id = _mint_commission(engine, age_seconds=99_999)
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
        )

        service.recover_now()

        row = _row(engine, commission_id)
        assert row.state == CommissionState.ABANDONED.value
        assert row.resolved_at is not None
        assert row.abandon_reason == "ttl_expired_not_live"
        # An abandoned row was never SATISFIED — the two stamps stay
        # distinct.
        assert row.satisfied_at is None


# ---------------------------------------------------------------------------
# MAJ-4 — the liveness leg carries the guard's task-stamp HORIZON
# ---------------------------------------------------------------------------


class TestTheLivenessHorizon:
    """MAJ-4: a live-work member with a STALLED task row is not live.

    The member-alive leg used to be a bare ``bool(has_live_work(...))``
    — NO horizon — so a permanently-stalled RUNNING task read as live
    FOREVER: the commission was never abandoned, the L3 hold never
    released, and the only escape was StaleTaskRecovery (an unnamed
    cross-subsystem dependency).

    The horizon predicate is the mission-live guard's OWN
    ``member_live_task_attempt_at`` (reused, not re-derived) and the
    horizon is the sweep's TTL (the clock its own abandon backstop is
    measured against).
    """

    def _seed_stalled_task(self, engine, *, age_hours: int) -> None:
        """A non-terminal (RUNNING) task row with an ANCIENT stamp."""
        from datetime import datetime, timedelta, timezone

        from daemon.repositories.task.models import Task, TaskStatus

        stamp = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            hours=age_hours
        )
        with Session(engine) as session:
            session.add(
                Task(
                    work_id=str(uuid.uuid4()),
                    instance_id="worker-1",
                    status=TaskStatus.RUNNING.value,
                    created_at=stamp,
                    started_at=stamp,
                )
            )
            session.commit()

    def test_a_stalled_running_task_is_not_live(self, engine, manager_loop):
        """THE PIN: has_live_work True + an ancient stamp ⇒ NOT live ⇒
        the abandon path is reachable."""
        self._seed_stalled_task(engine, age_hours=26)
        commission_id = _mint_commission(engine, age_seconds=99_999)
        instance_repo = FakeInstanceRepo(
            statuses={"worker-1": "running"}, live=True, engine=engine
        )
        service, _ = _build_service(
            engine, manager_loop=manager_loop, instance_repo=instance_repo
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.skipped_live == 0
        assert lane.abandoned_ttl == 1
        row = _row(engine, commission_id)
        assert row.state == CommissionState.ABANDONED.value
        assert row.abandon_reason == "ttl_expired_not_live"

    def test_a_young_running_task_is_still_live(self, engine, manager_loop):
        """The fail-safe direction: a FRESH task stamp keeps the live
        verdict (the row is held, never abandoned)."""
        self._seed_stalled_task(engine, age_hours=0)
        commission_id = _mint_commission(engine, age_seconds=99_999)
        instance_repo = FakeInstanceRepo(
            statuses={"worker-1": "running"}, live=True, engine=engine
        )
        service, _ = _build_service(
            engine, manager_loop=manager_loop, instance_repo=instance_repo
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.skipped_live == 1
        assert lane.abandoned_ttl == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )

    def test_an_unprobeable_task_stamp_keeps_the_member_live(
        self, engine, manager_loop
    ):
        """Fail-safe: a task-stamp probe that CANNOT RUN holds the row."""
        commission_id = _mint_commission(engine, age_seconds=99_999)
        # No ``engine`` on the fake repository ⇒ the probe faults.
        instance_repo = FakeInstanceRepo(
            statuses={"worker-1": "running"}, live=True
        )
        service, _ = _build_service(
            engine, manager_loop=manager_loop, instance_repo=instance_repo
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.skipped_live == 1
        assert lane.abandoned_ttl == 0
        assert lane.probe_errors == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )


# ---------------------------------------------------------------------------
# The JobItem-live check (OQ-7)
# ---------------------------------------------------------------------------


class TestTheJobItemLiveCheck:
    """The OQ-7 JobItem-live leg — the CODE PATH, not production wiring.

    HONEST SCOPE (PR3 review, 2026-10-26): the rows below are
    HAND-BUILT by ``_mint_commission`` — the helper inserts the
    commission row directly with ``work_id="job-1"``. NO production
    mint lane stamps ``work_id`` on a commission row (the
    ``send_message`` + ``instance_nudge`` lanes mint without it; the
    ``job_continue`` lane attaches the work id to the EPOCH row and
    mints no commission row), so this leg is INERT in production and
    these tests prove the PROBE + the branch logic, NOT the wiring.

    The sweep stays fail-safe meanwhile: the worker-side
    ``has_live_work`` leg covers the JobItem-queued-on-the-worker
    case. The wiring gap is an explicit OPEN item (the B-arc closure
    table §(d.2)), not a defect this test suite hides.
    """

    def test_a_non_terminal_job_item_holds_the_row(self, engine, manager_loop):
        # A HAND-BUILT commission row carrying a work id — the shape no
        # production mint lane currently produces (see the class
        # docstring). Proves the leg's HOLD branch, not its wiring.
        commission_id = _mint_commission(
            engine, age_seconds=99_999, work_id="job-1"
        )
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
            job_repo=FakeJobRepo(admission_state="active"),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.skipped_live == 1
        assert lane.abandoned_ttl == 0
        assert _row(engine, commission_id).state == (
            CommissionState.OUTSTANDING.value
        )

    def test_a_terminal_job_item_does_not_hold_the_row(
        self, engine, manager_loop
    ):
        commission_id = _mint_commission(
            engine, age_seconds=99_999, work_id="job-1"
        )
        service, _ = _build_service(
            engine,
            manager_loop=manager_loop,
            instance_repo=FakeInstanceRepo(statuses={"worker-1": "running"}),
            job_repo=FakeJobRepo(admission_state="done"),
        )

        lane = service.recover_now().lanes["mission_reconciliation"]

        assert lane.abandoned_ttl == 1
        assert _row(engine, commission_id).state == (
            CommissionState.ABANDONED.value
        )
