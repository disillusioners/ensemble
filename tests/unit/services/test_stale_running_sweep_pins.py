"""Stale-running sweep pins (fix/post-outage-recovery SCOPE b4, Shape B).

The status-stale defect class: an instance whose WORK IS FINISHED (every
task row terminal, every message row proven-terminal, nothing holds the
posture) but whose row still reads ``status='running'`` with a frozen
last_activity. The lane is the STEADY-STATE terminalizer the existing
seams lack:

* ``child_reports`` (parent flip / own stamp) + ``job_feedback_observer``
  (job finalize) are EVENT-DRIVEN — no completion event ⇒ no stamp.
* the b2 boot reconciler is BOOT-ONLY (and its ``deleted_trace`` family
  requires the message_queue rows to be GONE — the measured specimens
  carried surviving terminal message rows).
* the strand healer's running-zombie anchor JOINs a terminal-COMPLETED
  task on a ROOT — the measured specimens (NO task row at all: the
  rows were wiped while the message rows survived; and a NON-ROOT
  member) fall outside the anchor entirely.

THE POSITIVE PINS ARE FAIL-HONEST: each one REDs against the pre-b4
base (19faa6a67) — the lane does not exist there, so the module import
itself fails — and GREENs after the fix lands.

Harness: real repositories + a real ``ChildReportsService`` over a
SQLite in-memory engine (mirrors ``tests/job_queue/
test_running_zombie_sweep.py``); the transport boundaries (the drive
seams, the dependency bus singleton) are fakes.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.constants import TERMINAL_INSTANCE_STATUSES
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.task.models import Task
from daemon.services.timestamps import now_utc_naive

from daemon.services import stale_running_sweep as srs

ROOT = "stale-root"
CHILD = "stale-child-terminal"
LIVE_CHILD = "stale-child-live"

#: The completion evidence age — comfortably past the 7200s bound.
EVIDENCE_AGE = timedelta(hours=3)


# ─────────────────────────────────────────────────────────────────────────────
# Engine + seed helpers (raw SQL — mirrors the sibling strand suite)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def srs_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def dependency_bus():
    """The ADR-011 mock DependencyBus singleton (the tests/job_queue
    conftest pattern, inlined here — tests/unit/services/ has no bus
    conftest). The production drive chain hard-requires the singleton
    (``child_reports`` raises ``DependencyBus is not initialized``);
    the mock returns ZERO pending watchers so the cascade lane's
    ``is_parent_complete`` check is satisfied. The Shape-B bus-pending
    TRAP pin overrides ``count_fulfillable_pending_for_target_sync``.
    """
    bus = MagicMock()

    async def _lock_factory(parent_id):
        return asyncio.Lock()

    bus.count_pending_for_target_sync = MagicMock(return_value=0)
    bus.count_fulfillable_pending_for_target_sync = MagicMock(return_value=0)
    bus.count_pending_for_target = AsyncMock(return_value=0)
    bus.count_fulfillable_pending_for_target = AsyncMock(return_value=0)
    bus.had_parent_error = MagicMock(return_value=False)
    bus.parent_error_message = MagicMock(return_value=None)
    bus.get_generation = MagicMock(return_value=0)
    bus.emit_terminal = AsyncMock(return_value=[])
    bus.emit_terminal_for_child_instance = AsyncMock(return_value=[])
    bus._get_parent_lock = _lock_factory
    bus.mark_enqueued_by_source_target = AsyncMock(return_value=None)

    from daemon.services.dependency_bus import set_dependency_bus
    set_dependency_bus(bus)
    holder = type("BusHolder", (), {"fake": bus})()
    yield holder
    set_dependency_bus(None)


@pytest.fixture
def srs_harness(srs_engine):
    instance_repo = SQLModelInstanceRepository(srs_engine)
    task_repo = __import__(
        "daemon.repositories.task.repository", fromlist=["TaskRepository"]
    ).TaskRepository(srs_engine)

    manager = MagicMock()
    manager.engine = srs_engine
    manager._engine = srs_engine
    manager._instance_repository = instance_repo
    manager._task_repo = task_repo
    from daemon.write_pause_guard import WritePauseGuard
    manager.write_guard = WritePauseGuard()
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-srs")
    )
    manager.config = MagicMock()
    manager.config.report_repair = MagicMock()
    manager.config.report_repair.enabled = False
    manager.config.report_repair.repair_excluded_agents = []
    manager.config.report_repair.size_ratio_threshold = 5.0
    manager.config.report_repair.lookback_messages = 3
    manager.config.report_integrity = MagicMock()
    manager.config.report_integrity.b_terminal_waiting_guard_enabled = True
    manager._question_manager = MagicMock()
    manager._question_manager.get_pending_packs_for_instances = MagicMock(
        return_value={}
    )

    from daemon.repositories.event.repository import EventRepository
    event_bus = __import__(
        "daemon.services.event_bus", fromlist=["EventBus"]
    ).EventBus(event_repo=EventRepository(srs_engine))
    manager._event_bus = event_bus

    from daemon.services.child_reports import ChildReportsService
    events_service = MagicMock()
    events_service._publish_instance_lifecycle_event = AsyncMock()
    service = ChildReportsService(manager=manager, events_service=events_service)
    manager._child_reports_service = service
    manager._mission_strand_healer = None

    return type(
        "SrsHarness",
        (),
        {
            "engine": srs_engine,
            "manager": manager,
            "instance_repo": instance_repo,
            "service": service,
            "events_service": events_service,
        },
    )()


def _make_sweep(harness, **kwargs):
    kwargs.setdefault("interval_seconds", 300)
    return srs.StaleRunningSweepService(harness.manager, **kwargs)


def _patch_drive_seams(history: list[dict] | None = None):
    """The drive-seam patch set — identical to the sibling strand suite:
    the production completion path reads the assistant history through
    these seams."""
    if history is None:
        history = [
            {
                "role": "user",
                "content": "run the mission",
                "created_at": "2026-11-23T09:00:00+00:00",
            },
            {
                "role": "assistant",
                "content": "mission complete — final report",
                "created_at": "2026-11-23T10:00:00+00:00",
            },
        ]

    @contextmanager
    def _cm():
        stack = ExitStack()
        stack.enter_context(patch(
            "daemon.services.child_reports.get_instance_messages",
            new_callable=AsyncMock,
            return_value=history,
        ))
        stack.enter_context(patch(
            "daemon.services.child_reports.get_last_assistant_timestamp",
            new_callable=AsyncMock,
            return_value="2026-11-23T10:00:00+00:00",
        ))
        stack.enter_context(patch(
            "daemon.services.child_reports.MainLoopBridge.run_async_no_wait"
        ))
        try:
            yield stack
        finally:
            stack.close()

    return _cm()


from contextlib import ExitStack  # noqa: E402  (kept beside its only use)


def _insert_instance(
    engine,
    instance_id: str,
    *,
    status: str,
    parent_id: str | None = None,
    agent_id: str = "leader",
    version: int = 1,
    last_activity_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    activity_iso = (
        last_activity_at.isoformat() if last_activity_at is not None else None
    )
    updated_iso = updated_at.isoformat() if updated_at is not None else now
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, last_activity_at, version,
                     parent_id)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status,
                     :project_id, :created_at, :updated_at,
                     :last_activity_at, :version, :parent_id)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": agent_id,
                "agent_dir": f"agents/{agent_id}",
                "status": status,
                "project_id": "test-project",
                "created_at": now,
                "updated_at": updated_iso,
                "last_activity_at": activity_iso,
                "version": version,
                "parent_id": parent_id,
            },
        )


def _insert_task(
    engine,
    *,
    instance_id: str,
    status: str,
    completed_at: datetime | None,
    work_id: str = "work-srs-1",
    created_at: datetime | None = None,
    suspension_reason: str | None = None,
    resume_target_turn_id: str | None = None,
) -> None:
    created_iso = (created_at or datetime.now(timezone.utc)).isoformat()
    completed_iso = (
        completed_at.isoformat() if completed_at is not None else None
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, status, retry_count,
                     created_at, cancel_requested, retry_scheduled,
                     work_id, is_deferred, is_background, completed_at,
                     suspension_reason, resume_target_turn_id)
                VALUES
                    ('process_message', :instance_id, :status, 0,
                     :created_at, 0, 0, :work_id, 0, 0, :completed_at,
                     :suspension_reason, :resume_target_turn_id)
                """
            ),
            {
                "instance_id": instance_id,
                "status": status,
                "created_at": created_iso,
                "work_id": work_id,
                "completed_at": completed_iso,
                "suspension_reason": suspension_reason,
                "resume_target_turn_id": resume_target_turn_id,
            },
        )


def _insert_message(
    engine,
    *,
    instance_id: str,
    status: str,
    completed_at: datetime | None = None,
    message_id: str = "msg-srs-1",
    content: str = "scheduled mission",
) -> None:
    completed_iso = (
        completed_at.isoformat() if completed_at is not None else None
    )
    enqueued_iso = (
        (datetime.now(timezone.utc) - EVIDENCE_AGE).isoformat()
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO message_queue
                    (message_id, instance_id, content, type, source,
                     status, priority, retry_count, max_retries,
                     enqueued_at, completed_at, metadata)
                VALUES
                    (:message_id, :instance_id, :content, 'agent', 'api',
                     :status, 1, 0, 3, :enqueued_at, :completed_at, :metadata)
                """
            ),
            {
                "message_id": message_id,
                "instance_id": instance_id,
                "content": content,
                "status": status,
                "enqueued_at": enqueued_iso,
                "completed_at": completed_iso,
                "metadata": json.dumps({}),
            },
        )


def _insert_report_injection(
    engine,
    *,
    parent_id: str,
    state: str = "PENDING",
    injection_id: str = "inj-srs-1",
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO report_injections
                    (injection_id, parent_instance_id, child_instance_id,
                     child_message_id, report_message_id, content,
                     created_at, state)
                VALUES
                    (:injection_id, :parent_id, 'child-x', 'cm-1', 'rm-1',
                     'report', :created_at, :state)
                """
            ),
            {
                "injection_id": injection_id,
                "parent_id": parent_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "state": state,
            },
        )


def _get_instance_row(engine, instance_id: str) -> Instance:
    with Session(engine) as session:
        return session.get(Instance, instance_id)


def _seed_shape_b(
    engine,
    *,
    instance_id: str = ROOT,
    parent_id: str | None = None,
    with_task: bool = True,
    with_message: bool = True,
    task_status: str = "completed",
    message_status: str = "completed",
    evidence_age: timedelta = EVIDENCE_AGE,
    extra_children: bool = False,
) -> str:
    """Seed the Shape-B specimen: ``running`` posture, every work row
    terminal, completion evidence aged past the bound."""
    _insert_instance(
        engine, instance_id,
        status=InstanceStatus.RUNNING.value,
        parent_id=parent_id,
        agent_id="sonic-leader",
        last_activity_at=now_utc_naive() - timedelta(hours=30),
    )
    if extra_children:
        _insert_instance(
            engine, CHILD,
            status=InstanceStatus.COMPLETED.value,
            parent_id=instance_id,
            agent_id="worker",
            last_activity_at=now_utc_naive() - timedelta(hours=28),
        )
    aged = datetime.now(timezone.utc) - evidence_age
    if with_task:
        _insert_task(
            engine,
            instance_id=instance_id,
            status=task_status,
            completed_at=aged if task_status == "completed" else None,
        )
    if with_message:
        _insert_message(
            engine,
            instance_id=instance_id,
            status=message_status,
            completed_at=aged if message_status == "completed" else None,
            message_id="msg-shape-b",
        )
    return instance_id


# ─────────────────────────────────────────────────────────────────────────────
# POSITIVE pins (fail-honest: RED at base — the lane does not exist there)
# ─────────────────────────────────────────────────────────────────────────────


class TestShapeBPositive:
    @pytest.mark.asyncio
    async def test_completed_task_root_transitions_out_of_running(
        self, srs_harness,
    ):
        """The canonical positive: a running ROOT whose work finished
        (a terminal-COMPLETED task + a terminal message, nothing
        holding) LEAVES ``running`` — through the production completion
        chain, with a typed reason — and the census-style count drops
        to zero."""
        engine = srs_harness.engine
        _seed_shape_b(engine)
        sweep = _make_sweep(srs_harness)
        assert sweep.census_stale_running() == 1
        with _patch_drive_seams():
            result = await sweep.sweep_once()
        assert result.healed == 1, result
        assert result.errors == 0, result
        row = _get_instance_row(engine, ROOT)
        assert row.status == InstanceStatus.COMPLETED.value
        # The census-style count reflects the transition.
        assert sweep.census_stale_running() == 0

    @pytest.mark.asyncio
    async def test_no_task_row_completed_message_specimen_heals(
        self, srs_harness,
    ):
        """The measured specimen shape (28a21d1c / 5aba1b33): NO task
        row at all (the rows were wiped while the message rows
        survived), a terminal message, terminal children. The healer's
        anchor JOINs a COMPLETED task and DROPS this shape forever; this
        lane heals it."""
        engine = srs_harness.engine
        _seed_shape_b(
            engine, instance_id="specimen-no-task",
            with_task=False, with_message=True, extra_children=True,
        )
        sweep = _make_sweep(srs_harness)
        with _patch_drive_seams():
            result = await sweep.sweep_once()
        assert result.healed == 1, result
        row = _get_instance_row(engine, "specimen-no-task")
        assert row.status == InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_non_root_shape_heals(self, srs_harness):
        """The measured e1a631be shape: a NON-ROOT running member. The
        healer's running-zombie strand is root-only
        (``parent_id IS NULL``); the b2 reconciler finalizes non-roots
        at BOOT only. This lane is the steady-state seam for them."""
        engine = srs_harness.engine
        _insert_instance(
            engine, "the-parent",
            status=InstanceStatus.COMPLETED.value,
            parent_id=None,
            agent_id="leader",
            last_activity_at=now_utc_naive() - timedelta(hours=30),
        )
        _seed_shape_b(
            engine, instance_id="specimen-non-root",
            parent_id="the-parent",
        )
        sweep = _make_sweep(srs_harness)
        with _patch_drive_seams():
            result = await sweep.sweep_once()
        assert result.healed == 1, result
        row = _get_instance_row(engine, "specimen-non-root")
        assert row.status == InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_kill_switch_is_a_complete_noop(self, srs_harness, monkeypatch):
        engine = srs_harness.engine
        _seed_shape_b(engine)
        monkeypatch.setenv(srs.KILL_SWITCH_ENV, "0")
        sweep = _make_sweep(srs_harness)
        result = await sweep.sweep_once()
        assert result.mode == "off"
        assert result.skipped_kill_switch == 1
        assert result.candidates == 0
        assert _get_instance_row(engine, ROOT).status == "running"


# ─────────────────────────────────────────────────────────────────────────────
# TRAP pins — the legitimate-running postures must NEVER be touched
# ─────────────────────────────────────────────────────────────────────────────


class TestShapeBTraps:
    """Two layers per trap:

    * the SELECTION layer — the anchored SQL selector must leave the
      holding posture INVISIBLE (``candidates == 0``) so a holding
      shape can never even be examined;
    * the LEG layer — the per-candidate discriminator (the TOCTOU net
      that re-checks everything at drive time) must SKIP with its
      named skip class. Driven directly with a synthetic candidate row
      so the leg is pinned even though the selector already excluded
      the shape.
    """

    async def _drive_leg(
        self, srs_harness, instance_id: str,
        *, task_age: timedelta = EVIDENCE_AGE,
    ) -> srs.SweepResult:
        sweep = _make_sweep(srs_harness)
        aged = datetime.now(timezone.utc) - task_age
        candidate = {
            "instance_id": instance_id,
            "task_completed_at": aged,
            "message_completed_at": aged,
        }
        result = srs.SweepResult()
        await sweep._process_candidate(candidate, 7200, result)
        return result

    @pytest.mark.asyncio
    async def test_pending_task_stays_running(self, srs_harness):
        """TRAP (a): a PENDING task row holds the posture."""
        engine = srs_harness.engine
        _seed_shape_b(engine, instance_id="trap-pending-task")
        _insert_task(
            engine, instance_id="trap-pending-task", status="pending",
            completed_at=None, work_id="work-pending",
        )
        sweep = _make_sweep(srs_harness)
        result = await sweep.sweep_once()
        # Selection layer: the anchored selector leaves it invisible.
        assert result.candidates == 0, result
        assert result.healed == 0
        assert _get_instance_row(engine, "trap-pending-task").status == "running"
        # Leg layer: the TOCTOU net names the skip class.
        leg = await self._drive_leg(srs_harness, "trap-pending-task")
        assert leg.skipped_non_terminal_task == 1, leg

    @pytest.mark.asyncio
    async def test_running_task_stays_running(self, srs_harness):
        """TRAP (a'): a RUNNING task row (a turn in flight) holds."""
        engine = srs_harness.engine
        _seed_shape_b(engine, instance_id="trap-running-task")
        _insert_task(
            engine, instance_id="trap-running-task", status="running",
            completed_at=None, work_id="work-running",
        )
        sweep = _make_sweep(srs_harness)
        result = await sweep.sweep_once()
        assert result.candidates == 0, result
        assert _get_instance_row(engine, "trap-running-task").status == "running"
        leg = await self._drive_leg(srs_harness, "trap-running-task")
        assert leg.skipped_non_terminal_task == 1, leg

    @pytest.mark.asyncio
    async def test_ready_message_stays_running(self, srs_harness):
        """TRAP (b): a READY message row holds the posture."""
        engine = srs_harness.engine
        _seed_shape_b(
            engine, instance_id="trap-ready-msg", message_status="ready",
        )
        sweep = _make_sweep(srs_harness)
        result = await sweep.sweep_once()
        assert result.candidates == 0, result
        assert result.healed == 0
        assert _get_instance_row(engine, "trap-ready-msg").status == "running"
        leg = await self._drive_leg(srs_harness, "trap-ready-msg")
        assert leg.skipped_holding_message == 1, leg

    @pytest.mark.asyncio
    async def test_unknown_message_status_stays_running(self, srs_harness):
        """TRAP (b', the off-shape rule): a message status OUTSIDE the
        known vocabulary (the c6ad0537 observation) does NOT satisfy
        'nothing holding' unless proven terminal — conservative."""
        engine = srs_harness.engine
        _seed_shape_b(
            engine, instance_id="trap-unknown-msg",
            message_status="deferred_unknown",
        )
        sweep = _make_sweep(srs_harness)
        result = await sweep.sweep_once()
        assert result.candidates == 0, result
        assert _get_instance_row(
            engine, "trap-unknown-msg"
        ).status == "running"
        leg = await self._drive_leg(srs_harness, "trap-unknown-msg")
        assert leg.skipped_holding_message == 1, leg
        assert not srs.is_proven_terminal_message_status("deferred_unknown")

    @pytest.mark.asyncio
    async def test_live_subtree_member_stays_running(self, srs_harness):
        """TRAP (c): a LIVE (non-terminal, recently-active) subtree
        member holds the posture — the mission-live guard says live."""
        engine = srs_harness.engine
        _seed_shape_b(
            engine, instance_id="trap-live-tree", extra_children=True,
        )
        # A second, RECENTLY-ACTIVE non-terminal child ⇒ the tree is live.
        _insert_instance(
            engine, LIVE_CHILD,
            status=InstanceStatus.RUNNING.value,
            parent_id="trap-live-tree",
            agent_id="worker",
            last_activity_at=now_utc_naive() - timedelta(seconds=30),
        )
        sweep = _make_sweep(srs_harness)
        result = await sweep.sweep_once()
        assert result.healed == 0, result
        assert (
            result.skipped_live_tree + result.skipped_member_live_task
        ) >= 1, result
        assert _get_instance_row(engine, "trap-live-tree").status == "running"

    @pytest.mark.asyncio
    async def test_pending_bus_watcher_stays_running(
        self, srs_harness, dependency_bus,
    ):
        """TRAP (d-a): a fulfillable-pending dependency-bus watcher
        holds the posture (D14/D21: pending children own it)."""
        engine = srs_harness.engine
        _seed_shape_b(engine, instance_id="trap-bus")
        dependency_bus.fake.count_fulfillable_pending_for_target_sync = (
            MagicMock(return_value=2)
        )
        sweep = _make_sweep(srs_harness)
        result = await sweep.sweep_once()
        assert result.skipped_bus_pending == 1, result
        assert result.healed == 0
        assert _get_instance_row(engine, "trap-bus").status == "running"

    @pytest.mark.asyncio
    async def test_open_report_injection_stays_running(self, srs_harness):
        """TRAP (d-b): an open (PENDING/DEFERRED) report injection owns
        the posture."""
        engine = srs_harness.engine
        _seed_shape_b(engine, instance_id="trap-injection")
        _insert_report_injection(engine, parent_id="trap-injection")
        sweep = _make_sweep(srs_harness)
        result = await sweep.sweep_once()
        assert result.skipped_open_injection == 1, result
        assert result.healed == 0
        assert _get_instance_row(
            engine, "trap-injection"
        ).status == "running"

    @pytest.mark.asyncio
    async def test_waiting_children_is_never_touched(self, srs_harness):
        """TRAP (e): the WAITING_CHILDREN posture stays BUS-OWNED
        (D14/D21) — never a candidate, never written."""
        engine = srs_harness.engine
        _seed_shape_b(engine, instance_id="trap-wc")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET status = 'waiting_children' "
                    "WHERE instance_id = :i"
                ),
                {"i": "trap-wc"},
            )
        sweep = _make_sweep(srs_harness)
        result = await sweep.sweep_once()
        assert result.candidates == 0, result
        row = _get_instance_row(engine, "trap-wc")
        assert row.status == "waiting_children"

    @pytest.mark.asyncio
    async def test_paused_is_never_touched(self, srs_harness):
        """TRAP (f): the PAUSED posture is operator domain — never a
        candidate, never written."""
        engine = srs_harness.engine
        _seed_shape_b(engine, instance_id="trap-paused")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET status = 'paused' "
                    "WHERE instance_id = :i"
                ),
                {"i": "trap-paused"},
            )
        sweep = _make_sweep(srs_harness)
        result = await sweep.sweep_once()
        assert result.candidates == 0, result
        assert _get_instance_row(engine, "trap-paused").status == "paused"

    @pytest.mark.asyncio
    async def test_young_evidence_stays_running(self, srs_harness):
        """TRAP (g): completion evidence YOUNGER than the age bound is
        not yet a stale shape — the normal completion stamp may still
        be landing."""
        engine = srs_harness.engine
        _seed_shape_b(
            engine, instance_id="trap-young",
            evidence_age=timedelta(seconds=30),
        )
        sweep = _make_sweep(srs_harness)
        result = await sweep.sweep_once()
        assert result.skipped_young == 1, result
        assert result.healed == 0
        assert _get_instance_row(engine, "trap-young").status == "running"

    @pytest.mark.asyncio
    async def test_failed_only_shape_is_out_of_scope(self, srs_harness):
        """A shape whose only work artifacts are FAILED is OUT OF
        SCOPE: driving the completion chain there would stamp
        ``completed`` over a turn that did not succeed. No positive
        completion evidence ⇒ not a candidate."""
        engine = srs_harness.engine
        _insert_instance(
            engine, "trap-failed-only",
            status=InstanceStatus.RUNNING.value,
            parent_id=None,
            agent_id="worker",
            last_activity_at=now_utc_naive() - timedelta(hours=30),
        )
        _insert_task(
            engine, instance_id="trap-failed-only", status="failed",
            completed_at=datetime.now(timezone.utc) - EVIDENCE_AGE,
            work_id="work-failed",
        )
        sweep = _make_sweep(srs_harness)
        result = await sweep.sweep_once()
        assert result.candidates == 0, result
        assert _get_instance_row(engine, "trap-failed-only").status == "running"


# ─────────────────────────────────────────────────────────────────────────────
# Structural pins — knob hygiene, conservative predicate, never-touched set
# ─────────────────────────────────────────────────────────────────────────────


class TestStructural:
    def test_proven_terminal_predicate_is_closed(self):
        assert srs.is_proven_terminal_message_status("completed")
        assert srs.is_proven_terminal_message_status("failed")
        for status in ("pending", "ready", "processing", "retrying"):
            assert not srs.is_proven_terminal_message_status(status)
        # ANY unknown value holds (conservative).
        assert not srs.is_proven_terminal_message_status("mystery")
        assert not srs.is_proven_terminal_message_status(None)
        assert not srs.is_proven_terminal_message_status("")

    def test_never_touched_statuses_are_documented(self):
        assert srs._NEVER_TOUCHED_STATUSES == frozenset(
            {"waiting_children", "paused"}
        )

    def test_default_knobs_mirror_the_house_bounds(self):
        assert srs.DEFAULT_INTERVAL_SECONDS == 300
        assert srs.DEFAULT_AGE_BOUND_SECONDS == 7200
        assert srs.DEFAULT_BATCH_CAP == 25
        assert srs._MAX_TREE_PROBE_SIZE == 256

    def test_kill_switch_defaults_on(self, monkeypatch):
        monkeypatch.delenv(srs.KILL_SWITCH_ENV, raising=False)
        assert srs.kill_switch_enabled() is True
        monkeypatch.setenv(srs.KILL_SWITCH_ENV, "0")
        assert srs.kill_switch_enabled() is False

    def test_terminal_statuses_import_is_the_constant(self):
        """The lane's terminal vocabulary is the house constant."""
        assert TERMINAL_INSTANCE_STATUSES >= {"completed", "error",
                                              "terminated", "failed"}


# ─────────────────────────────────────────────────────────────────────────────
# Revive-wedge drive outcome — NEW drive-outcome pin (L5)
# ─────────────────────────────────────────────────────────────────────────────


class TestReviveWedgeDriveOutcome:
    @pytest.mark.asyncio
    async def test_revive_wedge_victim_lands_in_healed(
        self, srs_harness, caplog,
    ):
        """L5: the sweep's drive on a revive-wedge victim terminalizes the
        child and lands in ``healed`` (P-DRIVE-OUTCOME), NEVER
        ``drive-no-effect``. The P-DRIVE-LOG line fires with its
        unchanged format, and the terminal reason is persisted onto the
        row (NIT-6).

        RED at base (the drive leaves the victim RUNNING, class=
        drive-no-effect); GREEN at merge (the victim terminalizes).
        """
        import logging

        caplog.set_level(logging.WARNING)
        engine = srs_harness.engine
        _seed_shape_b(engine)
        sweep = _make_sweep(srs_harness)
        assert sweep.census_stale_running() == 1
        with _patch_drive_seams():
            result = await sweep.sweep_once()
        assert result.healed == 1, result
        assert result.drive_no_effect == 0, result
        assert result.errors == 0, result
        row = _get_instance_row(engine, ROOT)
        assert row.status == InstanceStatus.COMPLETED.value
        # NIT-6 — the typed reason is persisted onto the row.
        assert (row.instance_metadata or {}).get(
            "terminal_transition_reason"
        ) == "finalize_gap_steady_state", row.instance_metadata
        # P-DRIVE-LOG — the unchanged shape-b WARNING fires.
        shape_b_lines = [
            r.getMessage() for r in caplog.records
            if "stale_running_sweep|shape-b" in r.getMessage()
        ]
        assert len(shape_b_lines) == 1, shape_b_lines
        assert shape_b_lines[0].startswith(
            f"stale_running_sweep|shape-b: instance={ROOT} "
        ), shape_b_lines[0]
        assert "transitioned=completed" in shape_b_lines[0]
        assert "reason=finalize_gap_steady_state" in shape_b_lines[0]
        assert sweep.census_stale_running() == 0

    @pytest.mark.asyncio
    async def test_class_young_still_skipped_for_non_victim(
        self, srs_harness,
    ):
        """A non-victim whose completion evidence is younger than the age
        bound is still skipped as ``class=young`` (P-SKIP-LOG) — the
        drive-outcome change does not collapse the age gate."""
        engine = srs_harness.engine
        _seed_shape_b(
            engine, instance_id="trap-young-2",
            evidence_age=__import__("datetime").timedelta(seconds=30),
        )
        sweep = _make_sweep(srs_harness)
        result = await sweep.sweep_once()
        assert result.skipped_young >= 1, result
        assert result.healed == 0, result
        assert _get_instance_row(engine, "trap-young-2").status == "running"
