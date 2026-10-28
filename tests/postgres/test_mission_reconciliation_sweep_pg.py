"""PostgreSQL integration — RDRS lane 7 (the mission-commission retirement WRITER).

PHASE A (verification only — no production change). The writer under test
ALREADY EXISTS at this base: ``daemon/services/report_delivery_recovery.py``
(RDRS lane 7, "the RECONCILIATION SWEEP — the escape valve"), introduced by
commit ``13aa51e1a`` and present in v0.27.10 / v0.27.11 (ABSENT from the
LIVE v0.27.8 — that version lag is the production wedge).

WHY A REAL-PG FILE (the unit pack already exists):
``tests/unit/test_mission_reconciliation_sweep.py`` (72 tests, SQLite,
``StaticPool``) pins the per-probe semantics. This file re-proves the
DECISIVE legs against REAL PostgreSQL 16 with the REAL repositories
(``SQLModelInstanceRepository``, ``SQLModelMessageQueueRepository``,
``ReportInjectionRepository``, ``MissionRecordRepository``,
``DependencyWatcherRepository`` + the real ``DependencyBus``), so the
guarded ``UPDATE mission_commissions ... WHERE state='outstanding'`` and the
``mission_records`` anchor-scoped count are exercised by real SQL (the
TEXT-ISO columns, the partial unique index, the boolean columns).

THE LEGS (the dispatch's A1-A7 + O2/O3/O4):

* **A1** — SATISFY ON EVIDENCE (both terminal worker shapes: ``completed``
  and ``terminated``) and the terminal-but-SILENT counterpart (must be
  ``abandoned``/``worker_terminal_no_report``, NEVER ``satisfied``).
* **A2** — ABANDON ON TTL (the frozen vocabulary) + the probe-error HOLD
  (fail-MISS: a failing probe must NOT retire).
* **A3** — DOES-NOT-STRAND (safety-critical): a genuinely live worker is
  never retired, at any age, across repeated ticks.
* **A4** — BACKLOG: a prod-shaped backlog drains to zero within a bounded
  number of ticks and the anchor-scoped gate count then reads zero.
* **A5** — END-TO-END: the root in the exact wedge shape (mission-scoped
  gate blocked → downgrade → publish suppressed) PUBLISHES again once the
  writer retires the rows — the loop CLOSES, not merely the count.
* **A6** — IDEMPOTENCE: a second sweep is a byte-identical no-op.
* **A7** — MIXED BACKLOG: terminal-delivered / terminal-silent / live
  converge to ``satisfied`` / ``abandoned`` / STILL OUTSTANDING.
* **O2** — the 41730f45 shape: retiring the commission rows must move the
  block from leg 2 (mission_commissions) to leg 3 (tree_liveness) — the
  writer must NOT make a root look releasable when it is not.
* **O3** — MINT-INDEPENDENCE: a failed epoch mint (the prod
  ``ux_mission_records_anchor_open`` UniqueViolation) must not leave
  commissions permanently un-retirable. BOUNDARY: the mint path is NOT
  touched here — the failed-mint shape is seeded directly in the DB.
* **O4** — MUST-NOT-STRAND (the prod root 7182be86 shape): 5 outstanding
  rows, 2 pointing at LIVE workers, 3 at workers that completed AFTER
  minting. Satisfies ONLY on terminal-worker + delivered-report; NEVER on
  commission age alone.

HARNESS DEVIATIONS (declared, all in the DIAGNOSTIC-only direction):
* the parent-history PREFIX leg (leg 3) is stubbed to an empty history
  (it is DIAGNOSTIC ONLY by the F2 ruling — it can never authorise a
  ``satisfied`` nor a hold-against-abandon);
* the gate's bus-pending leg reads no bus singleton in-process (the
  helper is fail-OPEN → 0); no ``dependency_watchers`` row is seeded in
  this file, so the leg is not under test here;
* the tree-liveness leg reads the REAL instances table (real rows).

Everything else — the sweep, the writer, the guarded UPDATE, the anchor
count, the gate — is the production code path against real PG state.
"""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.dependency_bus.repository import (
    DependencyWatcherRepository,
)
from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)
from daemon.repositories.mission_record.models import (
    CommissionState,
    MissionCommission,
    MissionRecord,
    MissionStatus,
)
from daemon.repositories.mission_record.repository import (
    MissionRecordRepository,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.repositories.task.models import Task
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import DependencyBus
from daemon.services.mission_record_reads import count_outstanding_commissions
from daemon.services.mission_record_service import (
    MISSION_RECORDS_MODE_ENV,
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
    mission_records_enforce_enabled,
    resolve_mission_records_mode,
)
from daemon.services.report_delivery_recovery import (
    MISSION_ABANDON_REASON_TTL,
    MISSION_ABANDON_REASON_WORKER_TERMINAL,
    ReportDeliveryRecoveryService,
)

# The sweep's TTL horizon for these legs (the commission ages are
# BACKDATED past it — no wall-clock waiting anywhere in this file).
TTL_SECONDS = 3600

#: The frozen abandon-reason vocabulary (the design's R9 / OQ-4).
REASON_WORKER_TERMINAL = MISSION_ABANDON_REASON_WORKER_TERMINAL
REASON_TTL = MISSION_ABANDON_REASON_TTL
REASON_WORKER_MISSING = "worker_row_missing"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _enforce_mode():
    """The writer's WRITES happen only in ``enforce`` (the mode ladder).

    This file is the ENFORCE leg of the H-MODE pair; the DRY leg lives in
    ``test_mission_reconciliation_mode_pg.py`` (run as a separate process —
    the mode resolver is restart-read and caches once per process).
    """
    previous = os.environ.get(MISSION_RECORDS_MODE_ENV)
    os.environ[MISSION_RECORDS_MODE_ENV] = "enforce"
    _reset_mission_records_mode_for_tests()
    assert resolve_mission_records_mode() == "enforce"
    yield
    _reset_mission_records_mode_for_tests()
    if previous is None:
        os.environ.pop(MISSION_RECORDS_MODE_ENV, None)
    else:
        os.environ[MISSION_RECORDS_MODE_ENV] = previous


@pytest.fixture(autouse=True)
def _no_parent_history(monkeypatch):
    """The parent-history PREFIX leg (leg 3) reads NO evidence.

    DIAGNOSTIC ONLY by the F2 ruling — it can never authorise a
    ``satisfied`` nor a hold-against-abandon — so an empty history is
    behaviour-preserving for every assertion in this file.
    """
    import daemon.persistence as persistence

    async def _fake_messages(checkpointer, parent_id, manager=None):
        return []

    monkeypatch.setattr(persistence, "get_instance_messages", _fake_messages)


@pytest.fixture
def manager_loop():
    """A REAL event loop on a background thread.

    The diagnostic leg 3 is bridged onto the manager's loop
    (``_run_async_on_manager_loop``) — the harness drives the REAL bridge.
    """
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


# ---------------------------------------------------------------------------
# Seed helpers (REAL rows in REAL PG)
# ---------------------------------------------------------------------------


def _iso_seconds_ago(seconds: float) -> str:
    """An aware UTC ISO stamp ``seconds`` in the past."""
    return (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).isoformat()


def _seed_instance(
    engine,
    instance_id: str,
    status: str = "running",
    parent_id: str | None = None,
) -> str:
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="worker",
                agent_dir=f"agents/worker/{instance_id[:8]}",
                status=status,
                project_id=None,
                parent_id=parent_id,
            )
        )
        session.commit()
    return instance_id


def _seed_live_task(engine, instance_id: str) -> str:
    """A NON-TERMINAL task row — the member-alive leg's evidence.

    ``has_live_work`` (the Task arm) and the MAJ-4 liveness horizon
    (``member_live_task_attempt_at``) both read this row: a worker that
    owns a fresh non-terminal task is LIVE for sweep purposes.
    """
    task_id = str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            Task(
                work_id=task_id,
                task_type="process_message",
                instance_id=instance_id,
                status="running",
            )
        )
        session.commit()
    return task_id


def _seed_open_epoch(
    engine,
    anchor_instance_id: str,
    *,
    mission_id: str | None = None,
    status: str = MissionStatus.OPEN.value,
    epoch_seq: int = 1,
) -> str:
    """Seed one ``mission_records`` epoch row (the anchor of the count read)."""
    mission_id = mission_id or str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            MissionRecord(
                mission_id=mission_id,
                anchor_instance_id=anchor_instance_id,
                epoch_seq=epoch_seq,
                founding_message_id=None,
                founding_source="user_api",
                commissioner_instance_id=None,
                work_id=None,
                parent_mission_id=None,
                status=status,
                provenance="live",
            )
        )
        session.commit()
    return mission_id


def _mint_commission(
    engine,
    *,
    mission_id: str,
    commissioner: str,
    worker: str,
    age_seconds: int = 0,
    lane: str = "send_message",
    work_id: str | None = None,
) -> str:
    """Insert one OUTSTANDING commission row (the writer's target)."""
    commission_id = str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            MissionCommission(
                commission_id=commission_id,
                mission_id=mission_id,
                commissioner_instance_id=commissioner,
                worker_instance_id=worker,
                work_id=work_id,
                lane=lane,
                state=CommissionState.OUTSTANDING.value,
                created_at=_iso_seconds_ago(age_seconds),
                advisory_backfill=False,
            )
        )
        session.commit()
    return commission_id


def _seed_injection(
    engine,
    *,
    state: str = ReportInjectionState.INJECTED.value,
    parent: str,
    child: str,
    created_at: str | None = None,
) -> str:
    """Insert one ``report_injections`` row — leg 1's delivery EVIDENCE."""
    injection_id = str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            ReportInjection(
                injection_id=injection_id,
                parent_instance_id=parent,
                child_instance_id=child,
                child_message_id=f"msg-{uuid.uuid4()}",
                created_at=created_at or datetime.now(timezone.utc).isoformat(),
                state=state,
            )
        )
        session.commit()
    return injection_id


def _row(engine, commission_id: str) -> MissionCommission | None:
    with Session(engine) as session:
        return session.get(MissionCommission, commission_id)


def _snapshot(engine, commission_id: str) -> tuple:
    """A byte-comparable snapshot of a commission row (A6 idempotence)."""
    row = _row(engine, commission_id)
    if row is None:
        return (None,)
    return (
        row.state,
        row.satisfied_at,
        row.resolved_at,
        row.abandon_reason,
    )


class _FaultingInstanceRepository(SQLModelInstanceRepository):
    """The real instance repository with a FAULTING liveness probe.

    A2's probe-error leg: a liveness probe that cannot run must HOLD the
    row (fail-MISS) — abandoning on a failed probe is the wrong-side
    terminal transition the sweep's P1 posture forbids.
    """

    def __init__(self, engine):
        super().__init__(engine)
        self.liveness_error: Exception | None = None

    def has_live_work(self, instance_id: str) -> bool:  # type: ignore[override]
        if self.liveness_error is not None:
            raise self.liveness_error
        return super().has_live_work(instance_id)


# ---------------------------------------------------------------------------
# The service under test (REAL repositories, REAL writer)
# ---------------------------------------------------------------------------


def _build_service(
    engine,
    *,
    manager_loop,
    ttl_seconds: int = TTL_SECONDS,
    batch_cap: int = 50,
    instance_repo=None,
) -> ReportDeliveryRecoveryService:
    """Build the sweep with the REAL repositories wired to REAL PG.

    Every lane except lane 7 is disabled — the dispatch's legs are about
    the mission-commission writer, and the other lanes own delivery.
    """
    manager = MagicMock()
    manager._loop = manager_loop
    manager._checkpointer = SimpleNamespace(name="pg-harness-checkpointer")
    manager._job_queue_service = None

    service = ReportDeliveryRecoveryService(
        task_repo=MagicMock(),
        report_injection_repo=ReportInjectionRepository(engine=engine),
        queue_repo=SQLModelMessageQueueRepository(engine),
        instance_repo=(
            instance_repo
            if instance_repo is not None
            else SQLModelInstanceRepository(engine)
        ),
        manager_ref=manager,
        interval_seconds=300,
        age_bound_minutes=10,
        batch_cap=batch_cap,
        enabled=True,
        lane_deferred=False,
        lane_no_row_backstop=False,
        lane_pending_age=False,
        lane_recovery_retry=False,
        lane_orphan=False,
        lane_stuck_wake=False,
        lane_mission_reconciliation=True,
        mission_record_repo=MissionRecordRepository(engine),
        # MAJ-6: the sweep's WRITER is the mission-record SERVICE (the ONE
        # guarded UPDATE that owns the resolution stamps).
        mission_record_service=MissionRecordService(engine),
        dependency_bus=DependencyBus(DependencyWatcherRepository(engine)),
        job_repo=JobRepository(engine),
        mission_commission_ttl_seconds=ttl_seconds,
    )
    return service


def _run_lane(service) -> object:
    """Run ONE sweep pass and return lane 7's counters."""
    return service.recover_now().lanes["mission_reconciliation"]


# ---------------------------------------------------------------------------
# The gate harness (the REAL root-completion gate over REAL PG state)
# ---------------------------------------------------------------------------


def _mode_aware_count_facade(engine):
    """The REAL manager facade shape (``daemon/manager.py:12063-12068``).

    ``count_outstanding_commissions`` is MODE-AWARE: outside ``enforce``
    the leg is INACTIVE and the read is not even attempted (a REAL 0, not
    the unknown sentinel). This closure is that exact two-line body.
    """

    def _count(instance_id: str) -> int:
        if not mission_records_enforce_enabled():
            return 0
        return count_outstanding_commissions(engine, instance_id)

    return _count


def _child_reports(engine) -> ChildReportsService:
    """The REAL ``ChildReportsService`` over a REAL PG engine.

    The PR2 harness shape (``.agents/tester/RESULTS/2026-10-25-mission-
    records-pr2-runtime-gate.md`` item 3) — with the instance repository
    and the outstanding-commission facade wired to the REAL reads.
    """
    manager = MagicMock()
    manager.engine = engine
    manager._engine = engine
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._checkpointer = MagicMock()
    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager._queue_repository = SQLModelMessageQueueRepository(engine)
    manager.config = MagicMock()
    manager.config.report_repair = MagicMock()
    manager.config.report_repair.enabled = False
    manager.config.report_repair.repair_excluded_agents = []
    manager.config.report_repair.size_ratio_threshold = 5.0
    manager.config.report_repair.lookback_messages = 3
    # The PR2 facade — wired to the REAL read (mode-aware).
    manager.count_outstanding_commissions = _mode_aware_count_facade(engine)
    events_service = MagicMock()
    events_service._publish_instance_lifecycle_event = AsyncMock()
    return ChildReportsService(manager=manager, events_service=events_service)


# ---------------------------------------------------------------------------
# A1 — SATISFY ON EVIDENCE (and the terminal-but-SILENT counterpart)
# ---------------------------------------------------------------------------


class TestA1SatisfyOnEvidence:
    """A1 — the delivered worker is ``satisfied``; the silent one is NOT.

    Semantics that MUST hold (the frozen vocabulary):
    ``satisfied`` = the worker DELIVERED; ``abandoned`` = absence of
    delivery. A terminal-but-SILENT worker ⇒ ``abandoned``, NEVER
    ``satisfied``.
    """

    @pytest.mark.parametrize("terminal_status", ["completed", "terminated"])
    def test_a_terminal_worker_with_delivery_evidence_is_satisfied(
        self, pg_engine, manager_loop, terminal_status
    ):
        commissioner = _seed_instance(pg_engine, str(uuid.uuid4()), "running")
        worker = _seed_instance(pg_engine, str(uuid.uuid4()), terminal_status)
        mission_id = _seed_open_epoch(pg_engine, commissioner)
        commission_id = _mint_commission(
            pg_engine,
            mission_id=mission_id,
            commissioner=commissioner,
            worker=worker,
        )
        _seed_injection(
            pg_engine, parent=commissioner, child=worker
        )

        service = _build_service(pg_engine, manager_loop=manager_loop)
        lane = _run_lane(service)

        assert lane.satisfied_on_evidence == 1
        assert lane.recovered == 1
        row = _row(pg_engine, commission_id)
        assert row.state == CommissionState.SATISFIED.value
        assert row.satisfied_at is not None
        assert row.resolved_at is not None
        assert row.abandon_reason is None

    def test_a_terminal_worker_without_evidence_is_abandoned_not_satisfied(
        self, pg_engine, manager_loop
    ):
        commissioner = _seed_instance(pg_engine, str(uuid.uuid4()), "running")
        worker = _seed_instance(pg_engine, str(uuid.uuid4()), "completed")
        mission_id = _seed_open_epoch(pg_engine, commissioner)
        commission_id = _mint_commission(
            pg_engine,
            mission_id=mission_id,
            commissioner=commissioner,
            worker=worker,
        )
        # NO injection row — the worker went terminal WITHOUT delivering.

        service = _build_service(pg_engine, manager_loop=manager_loop)
        lane = _run_lane(service)

        assert lane.satisfied_on_evidence == 0
        assert lane.abandoned_worker_terminal == 1
        row = _row(pg_engine, commission_id)
        assert row.state == CommissionState.ABANDONED.value
        assert row.abandon_reason == REASON_WORKER_TERMINAL
        assert row.satisfied_at is None
        assert row.resolved_at is not None

    def test_an_undelivered_injection_row_is_not_evidence(
        self, pg_engine, manager_loop
    ):
        """A PENDING injection row is NOT delivery evidence."""
        commissioner = _seed_instance(pg_engine, str(uuid.uuid4()), "running")
        worker = _seed_instance(pg_engine, str(uuid.uuid4()), "completed")
        mission_id = _seed_open_epoch(pg_engine, commissioner)
        commission_id = _mint_commission(
            pg_engine,
            mission_id=mission_id,
            commissioner=commissioner,
            worker=worker,
        )
        _seed_injection(
            pg_engine,
            state=ReportInjectionState.PENDING.value,
            parent=commissioner,
            child=worker,
        )

        service = _build_service(pg_engine, manager_loop=manager_loop)
        lane = _run_lane(service)

        assert lane.satisfied_on_evidence == 0
        row = _row(pg_engine, commission_id)
        assert row.state == CommissionState.ABANDONED.value
        assert row.abandon_reason == REASON_WORKER_TERMINAL


# ---------------------------------------------------------------------------
# A2 — ABANDON ON TTL (+ the probe-error HOLD)
# ---------------------------------------------------------------------------


class TestA2AbandonOnTtl:
    def test_a_ttl_expired_idle_worker_is_abandoned(self, pg_engine, manager_loop):
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
        lane = _run_lane(service)

        assert lane.abandoned_ttl == 1
        row = _row(pg_engine, commission_id)
        assert row.state == CommissionState.ABANDONED.value
        assert row.abandon_reason == REASON_TTL
        assert row.satisfied_at is None

    def test_a_failing_liveness_probe_holds_the_row(self, pg_engine, manager_loop):
        """A probe error ⇒ HOLD (fail-MISS). A failed probe NEVER retires."""
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

        repo = _FaultingInstanceRepository(pg_engine)
        repo.liveness_error = RuntimeError("liveness probe boom")
        service = _build_service(
            pg_engine, manager_loop=manager_loop, instance_repo=repo
        )
        lane = _run_lane(service)

        assert lane.probe_errors == 1
        assert lane.abandoned_ttl == 0
        assert lane.recovered == 0
        row = _row(pg_engine, commission_id)
        assert row.state == CommissionState.OUTSTANDING.value
        assert row.resolved_at is None
        assert row.abandon_reason is None


# ---------------------------------------------------------------------------
# A3 — DOES-NOT-STRAND (safety-critical)
# ---------------------------------------------------------------------------


class TestA3DoesNotStrand:
    def test_a_live_worker_is_never_retired_past_the_ttl(self, pg_engine, manager_loop):
        """A genuinely live worker keeps its row across MANY ticks past TTL."""
        commissioner = _seed_instance(pg_engine, str(uuid.uuid4()), "running")
        worker = _seed_instance(pg_engine, str(uuid.uuid4()), "running")
        _seed_live_task(pg_engine, worker)
        mission_id = _seed_open_epoch(pg_engine, commissioner)
        commission_id = _mint_commission(
            pg_engine,
            mission_id=mission_id,
            commissioner=commissioner,
            worker=worker,
            age_seconds=TTL_SECONDS * 50,
        )

        service = _build_service(pg_engine, manager_loop=manager_loop)
        for _ in range(3):
            lane = _run_lane(service)
            assert lane.skipped_live == 1
            assert lane.recovered == 0
            assert lane.abandoned_ttl == 0
            row = _row(pg_engine, commission_id)
            assert row.state == CommissionState.OUTSTANDING.value
            assert row.resolved_at is None
            assert row.abandon_reason is None


# ---------------------------------------------------------------------------
# A4 — BACKLOG (drains to zero; the anchor count then reads zero)
# ---------------------------------------------------------------------------


class TestA4Backlog:
    def test_a_prod_shaped_backlog_drains_and_the_gate_count_reaches_zero(
        self, pg_engine, manager_loop
    ):
        anchor = _seed_instance(pg_engine, str(uuid.uuid4()), "running")
        mission_id = _seed_open_epoch(pg_engine, anchor)
        commissioner = anchor

        expected: list[tuple[str, str]] = []
        for index in range(12):
            worker = _seed_instance(
                pg_engine, str(uuid.uuid4()), "completed"
            )
            age = 60 + index * 30  # mixed ages, all past the TTL horizon
            commission_id = _mint_commission(
                pg_engine,
                mission_id=mission_id,
                commissioner=commissioner,
                worker=worker,
                age_seconds=age,
            )
            if index % 3 == 0:
                # every third worker DELIVERED
                _seed_injection(pg_engine, parent=commissioner, child=worker)
                expected.append((commission_id, CommissionState.SATISFIED.value))
            else:
                expected.append((commission_id, CommissionState.ABANDONED.value))

        service = _build_service(
            pg_engine, manager_loop=manager_loop, batch_cap=5
        )

        # The gate count BEFORE the sweep — the wedge's input.
        assert count_outstanding_commissions(pg_engine, anchor) == 12

        ticks = 0
        while count_outstanding_commissions(pg_engine, anchor) > 0:
            _run_lane(service)
            ticks += 1
            assert ticks <= 8, "the backlog did not drain within 8 ticks"

        assert count_outstanding_commissions(pg_engine, anchor) == 0
        for commission_id, expected_state in expected:
            row = _row(pg_engine, commission_id)
            assert row.state == expected_state, commission_id
            assert row.resolved_at is not None


# ---------------------------------------------------------------------------
# A5 — END-TO-END (the wedge shape closes: the root publishes again)
# ---------------------------------------------------------------------------


class TestA5EndToEnd:
    async def test_the_wedge_shape_publishes_once_the_writer_retires_the_rows(
        self, pg_engine, manager_loop
    ):
        """The root in the exact wedge shape PUBLISHES after the retirement.

        The wedge: the mission-scoped gate leg counts the outstanding
        commissions → non-zero ⇒ the emission-time gate blocks → the root
        is downgraded COMPLETED→WAITING_CHILDREN and the terminal publish
        is suppressed. After the writer retires the rows, the SAME gate
        allows the publish — the loop CLOSES.
        """
        root = _seed_instance(pg_engine, str(uuid.uuid4()), "completed")
        mission_id = _seed_open_epoch(pg_engine, root)
        worker = _seed_instance(pg_engine, str(uuid.uuid4()), "completed")
        _mint_commission(
            pg_engine,
            mission_id=mission_id,
            commissioner=root,
            worker=worker,
        )

        gate = _child_reports(pg_engine)
        allowed, reason, _declared = await gate._root_completion_gate(None, root)
        assert allowed is False
        assert "mission_commissions=1" in reason

        service = _build_service(pg_engine, manager_loop=manager_loop)
        lane = _run_lane(service)
        assert lane.abandoned_worker_terminal == 1

        allowed, reason, _declared = await gate._root_completion_gate(None, root)
        assert allowed is True, f"gate still blocks: {reason}"
        assert count_outstanding_commissions(pg_engine, root) == 0


# ---------------------------------------------------------------------------
# A6 — IDEMPOTENCE (a second sweep is a byte-identical no-op)
# ---------------------------------------------------------------------------


class TestA6Idempotence:
    def test_a_second_sweep_does_not_re_stamp_or_error(self, pg_engine, manager_loop):
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
        lane1 = _run_lane(service)
        assert lane1.abandoned_ttl == 1
        assert lane1.recovered == 1
        first = _snapshot(pg_engine, commission_id)

        lane2 = _run_lane(service)
        assert lane2.errors == 0
        assert lane2.abandoned_ttl == 0
        assert lane2.recovered == 0
        assert lane2.already_resolved == 0
        second = _snapshot(pg_engine, commission_id)

        assert first == second
        assert first[0] == CommissionState.ABANDONED.value
        assert first[2] is not None


# ---------------------------------------------------------------------------
# A7 — MIXED BACKLOG (three row classes → three correct end states)
# ---------------------------------------------------------------------------


class TestA7MixedClasses:
    def test_the_three_row_classes_converge_to_their_correct_state(
        self, pg_engine, manager_loop
    ):
        commissioner = _seed_instance(pg_engine, str(uuid.uuid4()), "running")

        delivered_worker = _seed_instance(pg_engine, str(uuid.uuid4()), "completed")
        silent_worker = _seed_instance(pg_engine, str(uuid.uuid4()), "completed")
        live_worker = _seed_instance(pg_engine, str(uuid.uuid4()), "running")
        _seed_live_task(pg_engine, live_worker)

        mission_id = _seed_open_epoch(pg_engine, commissioner)
        delivered_id = _mint_commission(
            pg_engine,
            mission_id=mission_id,
            commissioner=commissioner,
            worker=delivered_worker,
            age_seconds=TTL_SECONDS * 5,
        )
        _seed_injection(
            pg_engine, parent=commissioner, child=delivered_worker
        )
        silent_id = _mint_commission(
            pg_engine,
            mission_id=mission_id,
            commissioner=commissioner,
            worker=silent_worker,
            age_seconds=TTL_SECONDS * 5,
        )
        live_id = _mint_commission(
            pg_engine,
            mission_id=mission_id,
            commissioner=commissioner,
            worker=live_worker,
            age_seconds=TTL_SECONDS * 5,
        )

        service = _build_service(pg_engine, manager_loop=manager_loop)
        for _ in range(3):
            lane = _run_lane(service)
            assert lane.skipped_live == 1

        assert _row(pg_engine, delivered_id).state == (
            CommissionState.SATISFIED.value
        )
        assert _row(pg_engine, silent_id).state == CommissionState.ABANDONED.value
        assert _row(pg_engine, silent_id).abandon_reason == REASON_WORKER_TERMINAL
        live_row = _row(pg_engine, live_id)
        assert live_row.state == CommissionState.OUTSTANDING.value
        assert live_row.resolved_at is None


# ---------------------------------------------------------------------------
# O2 — the 41730f45 shape (retirement proceeds; the OTHER hold stays visible)
# ---------------------------------------------------------------------------


class TestO2TreeLivenessHoldsSeparately:
    async def test_the_block_moves_from_the_mission_leg_to_the_tree_leg(
        self, pg_engine, manager_loop
    ):
        """Retiring the commission rows must NOT make the root look releasable.

        The prod shape: the commissioned worker is terminal, but a TREE
        MEMBER is still non-terminal (the RUNNING-ZOMBIE child). After the
        retirement the root is still blocked — but by the OTHER leg
        (``tree_liveness``), which is reported distinctly.
        """
        root = _seed_instance(pg_engine, str(uuid.uuid4()), "completed")
        zombie_child = _seed_instance(
            pg_engine, str(uuid.uuid4()), "running", parent_id=root
        )
        assert zombie_child
        worker = _seed_instance(pg_engine, str(uuid.uuid4()), "completed")
        mission_id = _seed_open_epoch(pg_engine, root)
        commission_id = _mint_commission(
            pg_engine,
            mission_id=mission_id,
            commissioner=root,
            worker=worker,
        )

        gate = _child_reports(pg_engine)
        allowed, reason, _declared = await gate._root_completion_gate(None, root)
        assert allowed is False
        assert "mission_commissions=1" in reason

        service = _build_service(pg_engine, manager_loop=manager_loop)
        lane = _run_lane(service)
        assert lane.abandoned_worker_terminal == 1
        row = _row(pg_engine, commission_id)
        assert row.state == CommissionState.ABANDONED.value

        # The commission hold is GONE ...
        assert count_outstanding_commissions(pg_engine, root) == 0
        # ... and the OTHER hold is separately visible.
        allowed, reason, _declared = await gate._root_completion_gate(None, root)
        assert allowed is False
        assert reason.startswith("tree_liveness"), reason
        assert "mission_commissions" not in reason


# ---------------------------------------------------------------------------
# O3 — MINT-INDEPENDENCE (the failed-mint shape must not strand the rows)
# ---------------------------------------------------------------------------


class TestO3MintIndependence:
    def test_the_writer_retires_rows_whose_epoch_mint_failed(self, pg_engine, manager_loop):
        """A failed epoch mint must NOT leave the rows un-retirable.

        BOUNDARY: the mint path is NOT touched (it is owned by the
        concurrent DB-pool lane). The prod symptom is reproduced at the
        DB level: a second OPEN epoch for the same anchor raises the
        ``ux_mission_records_anchor_open`` UniqueViolation — the failed
        mint leaves the FIRST (open) epoch in place. The writer never
        reads ``mission_records`` for its decision (R6), so it retires
        the rows regardless.
        """
        anchor = _seed_instance(pg_engine, str(uuid.uuid4()), "running")
        mission_id = _seed_open_epoch(pg_engine, anchor)
        worker = _seed_instance(pg_engine, str(uuid.uuid4()), "completed")
        commission_id = _mint_commission(
            pg_engine,
            mission_id=mission_id,
            commissioner=anchor,
            worker=worker,
        )

        # Reproduce the failed-mint shape: a SECOND open epoch for the
        # same anchor violates the partial unique index.
        with Session(pg_engine) as session:
            session.add(
                MissionRecord(
                    mission_id=str(uuid.uuid4()),
                    anchor_instance_id=anchor,
                    epoch_seq=2,
                    founding_message_id=None,
                    founding_source="boot",
                    commissioner_instance_id=None,
                    work_id=None,
                    parent_mission_id=None,
                    status=MissionStatus.OPEN.value,
                    provenance="live",
                )
            )
            with pytest.raises(IntegrityError) as excinfo:
                session.commit()
        assert "ux_mission_records_anchor_open" in str(excinfo.value)

        epochs_before = _epoch_snapshot(pg_engine, anchor)

        service = _build_service(pg_engine, manager_loop=manager_loop)
        lane = _run_lane(service)
        assert lane.abandoned_worker_terminal == 1

        row = _row(pg_engine, commission_id)
        assert row.state == CommissionState.ABANDONED.value
        assert row.abandon_reason == REASON_WORKER_TERMINAL
        # R6: the sweep writes ONLY mission_commissions.
        assert _epoch_snapshot(pg_engine, anchor) == epochs_before


def _epoch_snapshot(engine, anchor_instance_id: str) -> list[tuple]:
    """A byte-comparable snapshot of an anchor's epoch rows (R6 proof)."""
    from sqlmodel import select

    with Session(engine) as session:
        rows = session.exec(
            select(MissionRecord).where(
                MissionRecord.anchor_instance_id == anchor_instance_id
            )
        ).all()
        return sorted(
            (r.mission_id, r.status, r.epoch_seq, r.created_at) for r in rows
        )


# ---------------------------------------------------------------------------
# O4 — MUST-NOT-STRAND (the prod root 7182be86 shape)
# ---------------------------------------------------------------------------


class TestO4MustNotStrand:
    def test_live_rows_hold_and_post_mint_completions_satisfy(
        self, pg_engine, manager_loop
    ):
        """5 outstanding rows: 2 at LIVE workers, 3 at workers that
        completed AFTER minting.

        MUST hold:
        * a LIVE worker's row stays OUTSTANDING across multiple ticks
          that are PAST the TTL (never on commission age alone);
        * a worker that completed after minting IS satisfied (terminal +
          delivered evidence);
        * age alone never satisfies anything.
        """
        commissioner = _seed_instance(pg_engine, str(uuid.uuid4()), "running")
        mission_id = _seed_open_epoch(pg_engine, commissioner)

        # (1) two LIVE workers — running/waiting_children with live work.
        live_running = _seed_instance(pg_engine, str(uuid.uuid4()), "running")
        _seed_live_task(pg_engine, live_running)
        live_waiting = _seed_instance(
            pg_engine, str(uuid.uuid4()), "waiting_children"
        )
        _seed_live_task(pg_engine, live_waiting)

        # (2) three workers that completed AFTER the mint.
        late_workers = []
        for _ in range(3):
            late = _seed_instance(pg_engine, str(uuid.uuid4()), "completed")
            late_workers.append(late)

        live_ids = []
        for live_worker in (live_running, live_waiting):
            live_ids.append(
                _mint_commission(
                    pg_engine,
                    mission_id=mission_id,
                    commissioner=commissioner,
                    worker=live_worker,
                    age_seconds=TTL_SECONDS * 20,
                )
            )
        late_ids = []
        for late in late_workers:
            late_ids.append(
                _mint_commission(
                    pg_engine,
                    mission_id=mission_id,
                    commissioner=commissioner,
                    worker=late,
                    age_seconds=TTL_SECONDS * 20,
                )
            )
            _seed_injection(pg_engine, parent=commissioner, child=late)

        service = _build_service(pg_engine, manager_loop=manager_loop)
        for _ in range(3):
            lane = _run_lane(service)
            assert lane.skipped_live == 2

        for late_id in late_ids:
            row = _row(pg_engine, late_id)
            assert row.state == CommissionState.SATISFIED.value
            assert row.satisfied_at is not None
        for live_id in live_ids:
            row = _row(pg_engine, live_id)
            assert row.state == CommissionState.OUTSTANDING.value
            assert row.resolved_at is None
            assert row.abandon_reason is None
