"""Running-zombie strand — the THIRD member of the strand family.

Commission "running-zombie instances" (2026-10-21). Live shape
(the 2026-10-19/20 mission-root incident): a MISSION ROOT whose
instance row
is stuck in ``running`` while the root's own latest task row is
terminal-COMPLETED and the tree holds a non-terminal member.

**PREMISE (corrected 2026-10-21, cycle 2).** The cycle-1 text said
"the completion EVENT was lost" — that is REFUTED by the live logs.
The stamp was **REFUSED**: the PRE-STAMP recursive-liveness hold
(``child_reports.py:2700-2766``) fired and skipped the COMPLETED stamp
entirely, 19 times on 2026-10-19 and 455 hold lines across ~40 roots
in total. The hold fired because the guard's freshness anchor
INCLUDED the root's own ``last_activity_at`` — which is refreshed on
every message enqueue, so the "freshest tree" signal was 22s old on a
root whose mission had been dead for hours. Nothing re-armed the hold
on a stranded tree (no future message-completed signal exists), so the
instance row stayed ``running`` forever (25h in the live incident).

The direct children were ALL terminal; the real blocker was a
DEPTH-3 ``idle`` member (reproduced by the synthetic incident fixture
below — the specimen snapshot itself is READ-ONLY and never committed).

The heal is HOME in ``daemon/services/mission_strand_healer.py`` (new
running-zombie strand shape) and drives the PRODUCTION completion
chain — ``ChildReportsService._process_child_completion_and_notify_
parent(root, …)`` — NEVER a raw status UPDATE, NEVER
``terminate_instance`` on the root.

PREDICATE (all legs must hold; every failure mode errs toward MISS):

  1. root.status == RUNNING AND parent_id IS NULL
  2. the latest task row is terminal-COMPLETED with a ``completed_at``
     stamp, and NO non-terminal task row exists
  3. the age bound (default 7200s) applies to ``task.completed_at``
     — NOT ``instances.last_activity_at`` (frozen-by-design)
  4. **WIDENED (cycle 2)** — the canonical mission-live guard says
     ``live=False``: the all-terminal short-circuit OR the 6h
     STALENESS EXPIRY on a non-terminal member (reachable only
     because the anchor is computed over the NON-ROOT members), and
     the enumeration is neither breadth- nor depth-truncated
  5. NO pending question anywhere in the tree
  6. dependency-bus pending 0 AND the root's own queue is empty

This module pins the legs (positive + negative), the end-to-end shape
and the negatives (live child / pending question / live turn / young
zombie / kill-switch).

Harness: real repositories + a real ``ChildReportsService`` over a
SQLite in-memory engine (mirrors
``tests/job_queue/test_watcher_loss_terminal_strand.py``); only the
transport boundary (``enqueue_message``) and the live hub are mocks.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_repository import (
    JobWatcherRepository,
)
from daemon.services.timestamps import now_utc_naive

# ── Fixtures / identities ──────────────────────────────────────────

ROOT = "root-zombie"
CHILD = "child-zombie-terminal"
RECEIPT_JOB = "job-zombie-receipt-1"

T_FRESH_ASSISTANT = "2026-10-20T10:00:00+00:00"

#: The live incident's age: the task completed 25h before the sweep.
ZOMBIE_AGE = timedelta(hours=25)


# ─────────────────────────────────────────────────────────────────────────────
# Engine + seed helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def zombie_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


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
    """Insert an ``instances`` row (raw SQL — mirrors the sibling
    strand suite's seeder so the row can carry an explicit
    ``last_activity_at`` / ``updated_at``)."""
    now = datetime.now(timezone.utc).isoformat()
    activity_iso = (
        last_activity_at.isoformat() if last_activity_at is not None else None
    )
    updated_iso = (
        updated_at.isoformat() if updated_at is not None else now
    )
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
    work_id: str = "work-zombie-1",
    task_type: str = "process_message",
    suspension_reason: str | None = None,
    resume_target_turn_id: str | None = None,
    created_at: datetime | None = None,
    started_at: datetime | None = None,
) -> None:
    """Insert a ``task`` row (raw SQL — the ``task`` table has several
    non-nullable columns the ORM default would otherwise fill).

    ``started_at`` is written only when given (the column is nullable
    and defaults to NULL — the "minted but not yet claimed" shape)."""
    created = created_at or datetime.now(timezone.utc)
    created_iso = created.isoformat()
    completed_iso = (
        completed_at.isoformat() if completed_at is not None else None
    )
    started_iso = started_at.isoformat() if started_at is not None else None
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, status, retry_count,
                     created_at, cancel_requested, retry_scheduled,
                     work_id, is_deferred, is_background, completed_at,
                     suspension_reason, resume_target_turn_id,
                     started_at)
                VALUES
                    (:task_type, :instance_id, :status, 0,
                     :created_at, 0, 0, :work_id, 0, 0, :completed_at,
                     :suspension_reason, :resume_target_turn_id,
                     :started_at)
                """
            ),
            {
                "task_type": task_type,
                "instance_id": instance_id,
                "status": status,
                "created_at": created_iso,
                "work_id": work_id,
                "completed_at": completed_iso,
                "suspension_reason": suspension_reason,
                "resume_target_turn_id": resume_target_turn_id,
                "started_at": started_iso,
            },
        )


def _insert_job(engine, *, admission_state: str = "done") -> None:
    """Seed the receipt JobItem in the f2-finalized state (``done``).

    The live incident: the drift finalizer already healed the JobItem
    (mission-live guard all-terminal short-circuit) but the INSTANCE
    row stayed ``running``. The heal must be idempotent against the
    already-done JobItem."""
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source,
                     project_id, queue_id, priority, admission_state,
                     created_at, instance_id, job_type, retry_count,
                     metadata)
                VALUES
                    (:job_id, 'leader', 'agents/leader', 'mission',
                     'api', 'test-project', NULL, 0, :admission_state,
                     :created_at, :instance_id, 'task', 0, :metadata)
                """
            ),
            {
                "job_id": RECEIPT_JOB,
                "created_at": now,
                "instance_id": ROOT,
                "admission_state": admission_state,
                "metadata": json.dumps({}),
            },
        )


def _seed_zombie_tree(
    engine,
    *,
    child_status: str = InstanceStatus.COMPLETED.value,
    task_status: str = "completed",
    task_age: timedelta = ZOMBIE_AGE,
    with_task: bool = True,
    child_activity_age: timedelta = timedelta(hours=26),
) -> None:
    """Seed the exact live shape.

    root: ``running``, no parent, ``last_activity_at`` FROZEN (the
    predicate correction: the age keys on ``task.completed_at``, never
    on ``last_activity_at``).
    child: terminal (the tree is closed).
    task: terminal-COMPLETED, ``completed_at`` aged by ``task_age``.

    ``child_activity_age`` controls the child's ``last_activity_at``
    (the guard's freshness anchor is computed over the NON-ROOT
    members — 2026-10-21): a RECENT value keeps the mission live
    (the tree leg skips), a STALE one lets the zombie-backstop fire.
    """
    _insert_instance(
        engine, ROOT,
        status=InstanceStatus.RUNNING.value,
        parent_id=None,
        agent_id="leader",
        last_activity_at=now_utc_naive() - timedelta(hours=30),
    )
    _insert_instance(
        engine, CHILD,
        status=child_status,
        parent_id=ROOT,
        agent_id="worker",
        last_activity_at=now_utc_naive() - child_activity_age,
    )
    if with_task:
        _insert_task(
            engine,
            instance_id=ROOT,
            status=task_status,
            completed_at=(
                datetime.now(timezone.utc) - task_age
                if task_status == "completed"
                else None
            ),
        )


# ── The live incident shape (2026-10-21) ─────────────────────────
#
# SYNTHETIC fixture, rebuilt from the read-only specimen snapshot
# (``/home/nea/ensemble-src/.agents/shared/planning/running-zombie-fix/
# specimen-<live-root>-snapshot.md`` — READ ONLY, never committed here;
# the live id is NOT reproduced in this file — the fixture ids below are
# descriptive only).
# The specimen's shape:
#
#   * the ROOT: ``running``, no parent, its own ``last_activity_at``
#     stamped at 04:09:09 — the enqueue-refreshed stamp that made the
#     6h backstop unreachable (the "22s old" live log line);
#   * 7 DIRECT children — ALL terminal (completed);
#   * ONE task row on the root: ``process_message``, completed,
#     created 04:09:09 → completed 04:09:31 (the 22s turn);
#   * the REAL blocker was NOT a direct child: it was a DEPTH-3
#     ``idle`` explorer member (the direct children were all
#     completed) — reproduced here as ``d1 → d2 → d3``.
#
# The depth-3 member carries a COMPLETED task row (it did real work)
# so it is NOT a never-dispatched ghost — the ghost signature would
# exclude it from liveness (the strand-exclusion interaction, pinned
# separately).

INCIDENT_MEMBER = "depth3-explorer"
INCIDENT_D1 = "depth1-mid"
INCIDENT_D2 = "depth2-mid"
INCIDENT_GHOST = "never-dispatched-ghost"

#: The incident's member quiet-time: the member had been idle for far
#: longer than the guard's 6h window when the root was stranded.
INCIDENT_MEMBER_STALE = timedelta(hours=25)


def _seed_incident_tree(
    engine,
    *,
    member_status: str = InstanceStatus.IDLE.value,
    member_activity_age: timedelta = INCIDENT_MEMBER_STALE,
    member_task_status: str | None = "completed",
    member_task_created_at: datetime | None = None,
    member_task_started_at: datetime | None = None,
    chain_activity_age: timedelta = timedelta(hours=26),
    with_ghost: bool = False,
    root_activity_age: timedelta = timedelta(hours=25),
) -> None:
    """Seed the live incident shape (synthetic — see the note above)."""
    _insert_instance(
        engine, ROOT,
        status=InstanceStatus.RUNNING.value,
        parent_id=None,
        agent_id="leader",
        last_activity_at=now_utc_naive() - root_activity_age,
    )
    # The 7 direct children of the specimen — ALL terminal.
    for index in range(7):
        _insert_instance(
            engine, f"direct-child-{index}",
            status=InstanceStatus.COMPLETED.value,
            parent_id=ROOT,
            agent_id="worker",
            last_activity_at=now_utc_naive() - chain_activity_age,
        )
    # The depth chain that carries the blocker: d1 → d2 → d3.
    _insert_instance(
        engine, INCIDENT_D1,
        status=InstanceStatus.COMPLETED.value,
        parent_id=ROOT,
        agent_id="worker",
        last_activity_at=now_utc_naive() - chain_activity_age,
    )
    _insert_instance(
        engine, INCIDENT_D2,
        status=InstanceStatus.COMPLETED.value,
        parent_id=INCIDENT_D1,
        agent_id="worker",
        last_activity_at=now_utc_naive() - chain_activity_age,
    )
    _insert_instance(
        engine, INCIDENT_MEMBER,
        status=member_status,
        parent_id=INCIDENT_D2,
        agent_id="explorer",
        version=2,
        last_activity_at=now_utc_naive() - member_activity_age,
    )
    # The member did real work — a COMPLETED task row keeps it OUT of
    # the never-dispatched ghost signature. ``member_task_status=None``
    # seeds NO task row at all (the cycle-3 "member with no task row"
    # shape — the observed zombie member).
    if member_task_status is not None:
        _insert_task(
            engine,
            instance_id=INCIDENT_MEMBER,
            status=member_task_status,
            completed_at=(
                datetime.now(timezone.utc) - timedelta(hours=26)
                if member_task_status == "completed" else None
            ),
            work_id="work-incident-member",
            created_at=member_task_created_at,
            started_at=member_task_started_at,
        )
    if with_ghost:
        # The never-dispatched ghost: idle, version 1, zero messages,
        # zero task rows, no activity stamp, under a TERMINAL direct
        # parent — the strand signature the guard EXCLUDES from
        # liveness (read side) and the healer retires (write side).
        _insert_instance(
            engine, INCIDENT_GHOST,
            status=InstanceStatus.IDLE.value,
            parent_id=INCIDENT_D2,
            agent_id="worker",
            version=1,
            last_activity_at=None,
            updated_at=now_utc_naive() - timedelta(minutes=5),
        )
    # The root's own task row: the completion proof (created 04:09:09
    # → completed 04:09:31 — the 22s turn).
    _insert_task(
        engine,
        instance_id=ROOT,
        status="completed",
        completed_at=datetime.now(timezone.utc) - ZOMBIE_AGE,
        work_id="work-incident-root",
    )


def _get_instance_row(engine, instance_id: str) -> Instance:
    with Session(engine) as session:
        return session.get(Instance, instance_id)


def _job_admission_state(engine, job_id: str) -> str:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT admission_state FROM job_queue_items "
                "WHERE job_id = :j"
            ),
            {"j": job_id},
        ).first()
    return row[0] if row else "<missing>"


# ─────────────────────────────────────────────────────────────────────────────
# Harness
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def zombie_harness(zombie_engine):
    instance_repo = SQLModelInstanceRepository(zombie_engine)
    task_repo_fq = __import__(
        "daemon.repositories.task.repository", fromlist=["TaskRepository"]
    ).TaskRepository(zombie_engine)
    job_repo = JobRepository(zombie_engine)
    watcher_repo = JobWatcherRepository(zombie_engine)

    manager = MagicMock()
    manager.engine = zombie_engine
    manager._engine = zombie_engine
    manager._instance_repository = instance_repo
    manager._task_repo = task_repo_fq
    manager._watcher_repo = watcher_repo
    from daemon.write_pause_guard import WritePauseGuard
    manager.write_guard = WritePauseGuard()
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-zombie")
    )
    manager.config = MagicMock()
    manager.config.report_repair = MagicMock()
    manager.config.report_repair.enabled = False
    manager.config.report_repair.repair_excluded_agents = []
    manager.config.report_repair.size_ratio_threshold = 5.0
    manager.config.report_repair.lookback_messages = 3
    manager.config.report_integrity = MagicMock()
    manager.config.report_integrity.b_terminal_waiting_guard_enabled = True
    # The RAM question manager — no pending packs by default. The
    # pending-question test overrides ``get_pending_packs_for_instances``.
    manager._question_manager = MagicMock()
    manager._question_manager.get_pending_packs_for_instances = MagicMock(
        return_value={}
    )

    from daemon.repositories.event.repository import EventRepository
    event_bus = __import__(
        "daemon.services.event_bus", fromlist=["EventBus"]
    ).EventBus(event_repo=EventRepository(zombie_engine))
    manager._event_bus = event_bus

    from daemon.services.child_reports import ChildReportsService
    events_service = MagicMock()
    events_service._publish_instance_lifecycle_event = AsyncMock()
    service = ChildReportsService(
        manager=manager, events_service=events_service,
    )
    manager._child_reports_service = service
    # The strand healer is EXPLICITLY unwired by default: the
    # pre-stamp hold's re-arm hand-off is a no-op without it (the
    # harness must opt in explicitly — an auto-created MagicMock
    # attribute would make the hand-off fire with a non-awaitable
    # mock in every test that drives a root).
    manager._mission_strand_healer = None

    return type(
        "ZombieHarness",
        (),
        {
            "engine": zombie_engine,
            "manager": manager,
            "instance_repo": instance_repo,
            "job_repo": job_repo,
            "watcher_repo": watcher_repo,
            "service": service,
            "events_service": events_service,
        },
    )


def _make_healer(harness, **kwargs):
    from daemon.services.mission_strand_healer import MissionStrandHealer
    kwargs.setdefault("interval_seconds", 300)
    return MissionStrandHealer(manager=harness.manager, **kwargs)


def _fresh_history() -> list[dict]:
    return [
        {
            "role": "user",
            "content": "run the mission",
            "created_at": "2026-10-20T09:00:00+00:00",
        },
        {
            "role": "assistant",
            "content": "mission complete — final report",
            "created_at": T_FRESH_ASSISTANT,
        },
    ]


def _patch_drive_seams(history: list[dict] | None = None):
    """The drive-seam patch set (checkpoint history extractor +
    timestamp + bridge) — identical to the sibling strand suite's
    patch set: the production completion path reads the assistant
    history through these seams."""
    from contextlib import ExitStack

    if history is None:
        history = _fresh_history()

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
            return_value=T_FRESH_ASSISTANT,
        ))
        stack.enter_context(patch(
            "daemon.services.child_reports.MainLoopBridge.run_async_no_wait"
        ))
        try:
            yield stack
        finally:
            stack.close()

    return _cm()


# ─────────────────────────────────────────────────────────────────────────────
# Predicate legs — positive + negative
# ─────────────────────────────────────────────────────────────────────────────


class TestRunningZombieConstructorFloor:
    """W-B floor — the constructor rejects out-of-range blast-radius
    knobs LOUDLY (review cycle 1).

    Both knobs bound the blast radius of a TERMINALIZING lane, so a
    value below 1 is an operator error, not a posture. The reviewer's
    MEASURED cases: ``batch_cap=-1`` made the batch UNLIMITED (5/5
    healed at cap=-1 vs 3/3 at cap=3 — the bound was gone) and
    ``age_bound_seconds=0`` made every turn instantly
    zombie-shaped. A silent clamp would hide both; the family style
    (same precedent as the ``interval_seconds`` check) is a loud
    ``ValueError`` at construction. Production cannot reach these
    values — both config fields carry ``ge=1``.
    """

    @pytest.mark.parametrize(
        "kwargs, expected_fragment",
        [
            ({"running_zombie_batch_cap": 0}, "running_zombie_batch_cap"),
            ({"running_zombie_batch_cap": -1}, "running_zombie_batch_cap"),
            (
                {"running_zombie_age_bound_seconds": 0},
                "running_zombie_age_bound_seconds",
            ),
            (
                {"running_zombie_age_bound_seconds": -1},
                "running_zombie_age_bound_seconds",
            ),
        ],
    )
    def test_out_of_range_knobs_raise_at_construction(
        self, zombie_harness, kwargs, expected_fragment,
    ):
        with pytest.raises(ValueError) as excinfo:
            _make_healer(zombie_harness, **kwargs)
        message = str(excinfo.value)
        assert expected_fragment in message
        assert "must be >= 1" in message

    def test_the_floor_admits_the_documented_minimum(
        self, zombie_harness,
    ):
        """1 is the smallest ADMISSIBLE value for both knobs (the
        floor is ``>= 1``, not ``> 1``)."""
        healer = _make_healer(
            zombie_harness,
            running_zombie_batch_cap=1,
            running_zombie_age_bound_seconds=1,
        )
        assert healer._running_zombie_batch_cap == 1
        assert healer._running_zombie_age_bound_seconds == 1


# ─────────────────────────────────────────────────────────────────────────────
# Predicate
# ─────────────────────────────────────────────────────────────────────────────


class TestRunningZombiePredicate:
    """Each predicate leg, positive and negative."""

    @pytest.mark.asyncio
    async def test_positive_heals_the_live_shape(self, zombie_harness):
        """The exact live shape: root ``running``, all descendants
        terminal, latest task terminal-COMPLETED and aged ⇒ the root
        is driven to terminal."""
        _seed_zombie_tree(zombie_harness.engine)
        _insert_job(zombie_harness.engine)
        healer = _make_healer(zombie_harness)
        with _patch_drive_seams():
            outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is True, outcome
        row = _get_instance_row(zombie_harness.engine, ROOT)
        assert row.status == InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_already_done_job_item_is_untouched(self, zombie_harness):
        """IDEMPOTENCY: the drive may run when the JobItem is ALREADY
        f2-finalized (the 25h incident shape). The already-done JobItem
        must be left alone."""
        _seed_zombie_tree(zombie_harness.engine)
        _insert_job(zombie_harness.engine, admission_state="done")
        healer = _make_healer(zombie_harness)
        with _patch_drive_seams():
            outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is True, outcome
        assert _job_admission_state(
            zombie_harness.engine, RECEIPT_JOB
        ) == AdmissionState.DONE.value

    @pytest.mark.asyncio
    async def test_non_root_running_is_skipped(self, zombie_harness):
        """Leg 1: a NON-ROOT running instance is out of scope."""
        _insert_instance(
            zombie_harness.engine, ROOT,
            status=InstanceStatus.RUNNING.value,
            parent_id="some-parent",
        )
        _insert_task(
            zombie_harness.engine,
            instance_id=ROOT,
            status="completed",
            completed_at=datetime.now(timezone.utc) - ZOMBIE_AGE,
        )
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "not-root"

    @pytest.mark.asyncio
    async def test_non_running_root_is_skipped(self, zombie_harness):
        """Leg 1: the waiting_children posture belongs to the OTHER
        strand — the running-zombie strand must not touch it."""
        _seed_zombie_tree(
            zombie_harness.engine,
        )
        with zombie_harness.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET status = 'waiting_children' "
                    "WHERE instance_id = :i"
                ),
                {"i": ROOT},
            )
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "not-running"

    @pytest.mark.asyncio
    async def test_no_task_row_is_skipped(self, zombie_harness):
        """PREDICATE CORRECTION #2: a root with NO task row has no
        completion proof ⇒ NEVER swept (deliberate miss)."""
        _seed_zombie_tree(zombie_harness.engine, with_task=False)
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "no-task-row"
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_latest_task_failed_is_skipped(self, zombie_harness):
        """Leg 2: the latest task row must be terminal-COMPLETED —
        an ERROR/FAILED latest task is NOT a completion proof."""
        _seed_zombie_tree(
            zombie_harness.engine, task_status="failed",
        )
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "latest-task-not-completed"
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_latest_task_cancelled_is_skipped(self, zombie_harness):
        """Leg 2: ``cancelled`` is terminal but NOT completed."""
        _seed_zombie_tree(
            zombie_harness.engine, task_status="cancelled",
        )
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "latest-task-not-completed"

    @pytest.mark.parametrize(
        "live_status", ["pending", "processing"],
    )
    @pytest.mark.asyncio
    async def test_live_turn_task_row_is_skipped(
        self, zombie_harness, live_status,
    ):
        """Leg 2: a NON-TERMINAL task row (a live turn owns the
        instance) ⇒ NOT a zombie.

        The live turn is seeded OLDER than the completed turn so the
        ``latest task`` leg still sees the COMPLETED row and the
        ``non-terminal task row`` leg is the one that fires.

        ``pending`` AND ``processing`` are both covered — the
        commission's negative is the LIVE PROCESSING task row, and
        ``processing`` is the legacy task vocabulary (it is in
        ``_RUNNING_ZOMBIE_NON_TERMINAL_TASK_STATUSES``).
        """
        _seed_zombie_tree(zombie_harness.engine)
        _insert_task(
            zombie_harness.engine,
            instance_id=ROOT,
            status=live_status,
            completed_at=None,
            work_id="work-zombie-live",
            created_at=datetime.now(timezone.utc) - timedelta(hours=26),
        )
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "non-terminal-task"
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_young_zombie_is_skipped(self, zombie_harness):
        """Leg 3: a task that completed 5 minutes ago is a live turn,
        not a zombie."""
        _seed_zombie_tree(
            zombie_harness.engine, task_age=timedelta(minutes=5),
        )
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "young"
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_recently_active_descendant_is_not_healed(
        self, zombie_harness,
    ):
        """Leg 4 (WIDENED 2026-10-21): a descendant that is RECENTLY
        active holds the mission live ⇒ the tree leg SKIPS.

        The tree leg is the canonical mission-live guard now (the SAME
        call the pre-stamp gate makes) — a member whose activity is
        inside the 6h window keeps the mission plausibly alive, so the
        heal must not touch the root."""
        _seed_zombie_tree(
            zombie_harness.engine,
            child_status=InstanceStatus.RUNNING.value,
            child_activity_age=timedelta(minutes=30),
        )
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "live-tree", outcome
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_stale_depth_three_member_heals(
        self, zombie_harness,
    ):
        """Leg 4 (WIDENED 2026-10-21): the STALE-MEMBER shape — the
        live incident's real blocker.

        The root's own stamp is refreshed on every enqueue, so it can
        never prove the tree is quiet; the MEMBER's activity is the
        anchor. A depth-3 ``idle`` member that has been quiet for
        longer than the guard's 6h window no longer holds the mission
        ⇒ the tree leg passes and the heal drives.

        (The member is at DEPTH 3 on purpose: the production
        completion chain's own live-children cross-check counts DIRECT
        children only, so a DIRECT idle child would defer the stamp
        (waiting_children) instead of healing — the live incident's
        blocker was a depth-3 member, not a direct child.)"""
        _seed_incident_tree(zombie_harness.engine)
        healer = _make_healer(zombie_harness)
        with _patch_drive_seams():
            outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is True, outcome
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_breadth_truncated_tree_is_skipped(
        self, zombie_harness, monkeypatch,
    ):
        """Leg 4: a tree larger than the probe bound is NOT probed
        completely ⇒ SKIP (fail-closed)."""
        from daemon.services import mission_strand_healer as msh

        _seed_zombie_tree(zombie_harness.engine)
        monkeypatch.setattr(msh, "_RUNNING_ZOMBIE_MAX_TREE_PROBE_SIZE", 1)
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "tree-truncated"

    @pytest.mark.asyncio
    async def test_depth_truncated_tree_is_skipped(self, zombie_harness):
        """Leg 4: the repo helper only WARNs on its depth cap — the
        healer detects the truncation in Python (closure check) and
        SKIPS."""
        _seed_zombie_tree(zombie_harness.engine)
        # Simulate a depth-truncated enumeration: the helper returns
        # only the root even though a child exists.
        original = zombie_harness.instance_repo.get_tree_ids_permanent

        def _truncated(root_id: str) -> list[str]:
            return [root_id]

        zombie_harness.instance_repo.get_tree_ids_permanent = _truncated
        try:
            healer = _make_healer(zombie_harness)
            outcome = await healer.heal_running_zombie(ROOT)
        finally:
            zombie_harness.instance_repo.get_tree_ids_permanent = original
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "tree-truncated"

    @pytest.mark.asyncio
    async def test_pending_question_is_skipped(self, zombie_harness):
        """Leg 5: an outstanding question in the tree still owns the
        mission ⇒ SKIP."""
        _seed_zombie_tree(zombie_harness.engine)
        zombie_harness.manager._question_manager.get_pending_packs_for_instances = MagicMock(  # noqa: E501
            return_value={ROOT: MagicMock()}
        )
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "pending-question"
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_durable_question_shadow_is_skipped(self, zombie_harness):
        """Leg 5: the DURABLE ``question_pack_payload`` shadow is
        honoured too (not only the RAM packs)."""
        _seed_zombie_tree(zombie_harness.engine)
        with zombie_harness.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET metadata = :m "
                    "WHERE instance_id = :i"
                ),
                {
                    "m": json.dumps({
                        "question_pack_payload": {
                            "status": "pending",
                            "question_id": "q-1",
                        }
                    }),
                    "i": ROOT,
                },
            )
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "pending-question"

    @pytest.mark.asyncio
    async def test_awaiting_answer_handle_blocks_the_heal(
        self, zombie_harness,
    ):
        """Leg 5: the durable ``awaiting_answer`` suspension handle
        blocks the heal.

        DEFENSE-IN-DEPTH: with the predicate's leg order, an
        ``awaiting_answer`` handle always coexists with a PAUSED (and
        therefore non-terminal) task row, so the earlier
        ``non-terminal-task`` leg fires first. The handle probe is
        exercised DIRECTLY below; the integrated assertion here is
        that the heal does NOT happen."""
        _seed_zombie_tree(zombie_harness.engine)
        _insert_task(
            zombie_harness.engine,
            instance_id=ROOT,
            status="paused",
            completed_at=None,
            work_id="work-zombie-await",
            suspension_reason="awaiting_answer",
            resume_target_turn_id="turn-1",
            created_at=datetime.now(timezone.utc) - timedelta(hours=26),
        )
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_awaiting_answer_handle_probe_is_exact(
        self, zombie_harness,
    ):
        """The ``awaiting_answer`` handle probe matches ONLY the
        authoritative handle shape (``suspension_reason`` +
        non-null ``resume_target_turn_id`` + ``status='paused'``) —
        the same shape as
        ``TaskRepository.find_suspended_turn_for_answer``."""
        _seed_zombie_tree(zombie_harness.engine)
        healer = _make_healer(zombie_harness)
        assert await asyncio.to_thread(
            healer._has_awaiting_answer_handle, [ROOT]
        ) is False
        _insert_task(
            zombie_harness.engine,
            instance_id=ROOT,
            status="paused",
            completed_at=None,
            work_id="work-zombie-await",
            suspension_reason="awaiting_answer",
            resume_target_turn_id="turn-1",
        )
        assert await asyncio.to_thread(
            healer._has_awaiting_answer_handle, [ROOT]
        ) is True
        # A handle WITHOUT the resume target is not an answer gate.
        _insert_task(
            zombie_harness.engine,
            instance_id=CHILD,
            status="paused",
            completed_at=None,
            work_id="work-zombie-await-2",
            suspension_reason="awaiting_answer",
            resume_target_turn_id=None,
        )
        assert await asyncio.to_thread(
            healer._has_awaiting_answer_handle, [CHILD]
        ) is False

    @pytest.mark.asyncio
    async def test_bus_pending_blocks(self, zombie_harness):
        """Leg 6: pending dependency-bus watchers ⇒ SKIP."""
        from daemon.services.dependency_bus import get_dependency_bus

        _seed_zombie_tree(zombie_harness.engine)
        bus = get_dependency_bus()
        bus.count_pending_for_target_sync = MagicMock(return_value=3)
        try:
            healer = _make_healer(zombie_harness)
            outcome = await healer.heal_running_zombie(ROOT)
        finally:
            bus.count_pending_for_target_sync = MagicMock(return_value=0)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "bus-pending"

    @pytest.mark.asyncio
    async def test_pending_own_queue_message_blocks(self, zombie_harness):
        """Leg 6: a READY/PROCESSING/RETRYING message on the root's own
        queue ⇒ SKIP."""
        from daemon.repositories.message_queue.models import (
            MessageQueue,
            MessageStatus,
        )

        _seed_zombie_tree(zombie_harness.engine)
        with Session(zombie_harness.engine) as session:
            session.add(
                MessageQueue(
                    message_id="msg-zombie-pending",
                    instance_id=ROOT,
                    content="work",
                    source="api",
                    status=MessageStatus.READY.value,
                )
            )
            session.commit()
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "pending-messages"

    @pytest.mark.asyncio
    async def test_kill_switch_is_a_complete_noop(self, zombie_harness):
        """The kill-switch stops the lane COMPLETELY — no selection,
        no probe, no drive."""
        _seed_zombie_tree(zombie_harness.engine)
        healer = _make_healer(zombie_harness, running_zombie_enabled=False)
        stats = await healer.sweep_running_zombies_once()
        assert stats["scanned"] == 0
        assert stats["healed"] == 0
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_sweep_finds_and_heals_the_running_root(
        self, zombie_harness,
    ):
        """The sweep pass selects the RUNNING root and heals it."""
        _seed_zombie_tree(zombie_harness.engine)
        healer = _make_healer(zombie_harness)
        with _patch_drive_seams():
            stats = await healer.sweep_running_zombies_once()
        assert stats["scanned"] == 1
        assert stats["healed"] == 1
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_batch_cap_does_not_starve_a_real_zombie(
        self, zombie_harness,
    ):
        """F1 — the batch cap must not PERMANENTLY starve a real zombie.

        The cap bounds the candidates EXAMINED per tick, and a
        candidate that is examined and SKIPPED keeps its sort key — so
        it is examined again on the next tick, forever. Under the
        pre-F1 selection (raw ``status='running'`` ordered by
        ``updated_at``) the head of the batch was therefore occupied
        PERMANENTLY by roots that can never satisfy the completion
        proof (here: no task row at all). With more such roots than
        the cap, the real zombie sat past position ``cap`` and was
        NEVER examined — a permanent starvation.

        The ANCHORED selection (F1) never selects a root without the
        completion proof, so the un-healable population cannot occupy
        the batch and the later zombie is healed on the SAME pass.
        """
        # 4 un-healable roots (NO task row ⇒ no completion proof).
        # Their ``updated_at`` is OLDER than the zombie's, so under
        # the pre-F1 ordering they occupy the whole batch.
        for i in range(4):
            _insert_instance(
                zombie_harness.engine,
                f"head-root-{i}",
                status=InstanceStatus.RUNNING.value,
                parent_id=None,
                agent_id="leader",
                updated_at=(
                    datetime.now(timezone.utc) - timedelta(hours=48 + i)
                ),
            )
        # The real zombie — the NEWEST ``updated_at``, so position 5
        # under the pre-F1 ordering (past the cap of 3).
        _seed_zombie_tree(zombie_harness.engine)

        healer = _make_healer(
            zombie_harness, running_zombie_batch_cap=3,
        )
        with _patch_drive_seams():
            stats = await healer.sweep_running_zombies_once()
        # The un-healable roots are not candidates at all — they do
        # not consume the batch, so the zombie behind them is served.
        assert stats["scanned"] == 1
        assert stats["healed"] == 1
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.COMPLETED.value
        # The un-healable roots are untouched (no completion proof ⇒
        # no write).
        for i in range(4):
            assert _get_instance_row(
                zombie_harness.engine, f"head-root-{i}"
            ).status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_sweep_once_keeps_the_waiting_children_keys(
        self, zombie_harness,
    ):
        """The widened selection must NOT weaken the existing strand:
        the waiting_children stat keys keep their EXACT meaning."""
        _seed_zombie_tree(zombie_harness.engine)
        healer = _make_healer(zombie_harness)
        with _patch_drive_seams():
            stats = await healer.sweep_once()
        assert stats["scanned"] == 0
        assert stats["healed"] == 0
        assert stats["running_zombie_scanned"] == 1
        assert stats["running_zombie_healed"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Observability contract
# ─────────────────────────────────────────────────────────────────────────────


class TestRunningZombieObservability:
    @pytest.mark.asyncio
    async def test_heal_emits_the_greppable_line(
        self, zombie_harness, caplog,
    ):
        """Every heal emits the house-style greppable line."""
        import logging

        _seed_zombie_tree(zombie_harness.engine)
        healer = _make_healer(zombie_harness)
        with caplog.at_level(logging.WARNING):
            with _patch_drive_seams():
                await healer.heal_running_zombie(ROOT)
        joined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "mission_strand_healer|running-zombie:" in joined
        assert "transitioned=completed" in joined
        assert "reason=finalize_gap" in joined

    @pytest.mark.asyncio
    async def test_kill_switch_warns_once_per_instance(
        self, zombie_harness, caplog,
    ):
        """Advisory 4: the kill-switch-disabled WARN is emitted ONCE
        per healer instance (a fresh instance per boot). The old shape
        repeated the same WARN every 300s tick forever. The lane stays
        observable — the first WARN names the switch."""
        import logging

        _seed_zombie_tree(zombie_harness.engine)
        healer = _make_healer(
            zombie_harness, running_zombie_enabled=False,
        )
        with caplog.at_level(logging.WARNING):
            first = await healer.sweep_running_zombies_once()
            second = await healer.sweep_running_zombies_once()
        kill_switch_warnings = [
            rec for rec in caplog.records
            if rec.levelno >= logging.WARNING
            and "class=kill-switch" in rec.getMessage()
        ]
        assert len(kill_switch_warnings) == 1
        # The lane is still a complete no-op on every tick.
        assert first["scanned"] == 0
        assert second["scanned"] == 0

    @pytest.mark.asyncio
    async def test_skip_emits_a_loud_warn(
        self, zombie_harness, caplog,
    ):
        """Every skip class emits a loud WARN."""
        import logging

        _seed_zombie_tree(zombie_harness.engine, task_age=timedelta(minutes=5))
        healer = _make_healer(zombie_harness)
        with caplog.at_level(logging.WARNING):
            await healer.heal_running_zombie(ROOT)
        joined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "mission_strand_healer|running-zombie:" in joined
        assert "class=young" in joined


# ─────────────────────────────────────────────────────────────────────────────
# Sibling #4 pin — the pre-stamp ``skip_root=True`` dependence
# ─────────────────────────────────────────────────────────────────────────────


class TestRunningZombieSkipRootPin:
    """Sibling #4 pin: the drive DEPENDS on the child_reports
    PRE-STAMP recursive liveness eval semantics
    (``child_reports.py:2700-2766``, ``skip_root=True``).

    If that eval's ``skip_root`` semantics change, the pre-stamp eval
    would report ``live=True`` for a running root with terminal
    descendants, the stamp would be skipped entirely, and this lane
    would report ``gate-block`` on EVERY tick — SILENTLY (the heal
    would simply never happen). The design doc named this pin as
    absent; it is added here.
    """

    @pytest.mark.asyncio
    async def test_skip_root_true_lets_a_running_root_be_stamped(
        self, zombie_harness,
    ):
        """The running root's OWN non-terminal status must NOT count
        as a live leg when ``skip_root=True`` — that is exactly what
        lets the stamp proceed."""
        from daemon.services.mission_live_guard import (
            evaluate_mission_live,
            MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
        )

        # A FRESH tree: the zombie backstop (6h) must not fire, or
        # the verdict would be ``live=False`` for the wrong reason.
        fresh = now_utc_naive() - timedelta(minutes=1)
        _insert_instance(
            zombie_harness.engine, ROOT,
            status=InstanceStatus.RUNNING.value,
            parent_id=None,
            agent_id="leader",
            last_activity_at=fresh,
        )
        _insert_instance(
            zombie_harness.engine, CHILD,
            status=InstanceStatus.COMPLETED.value,
            parent_id=ROOT,
            agent_id="worker",
            last_activity_at=fresh,
        )
        repo = zombie_harness.instance_repo

        # skip_root=True — the mode the pre-stamp eval uses: only the
        # DESCENDANTS' liveness gates the decision.
        with_skip = await evaluate_mission_live(
            instance_repository=repo,
            instance_id=ROOT,
            bus_pending_count=None,
            timeout_seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            skip_root=True,
        )
        assert with_skip.live is False, (
            "the pre-stamp eval must NOT report live for a running "
            "root with terminal descendants — the stamp (and the "
            "whole heal) depends on it"
        )

        # skip_root=False — the root's OWN running status IS a live
        # leg. This pins the SEMANTICS, not just the outcome: if the
        # root's own status stopped counting, the two verdicts would
        # become identical and the pre-stamp early-out would be
        # vacuous.
        without_skip = await evaluate_mission_live(
            instance_repository=repo,
            instance_id=ROOT,
            bus_pending_count=None,
            timeout_seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            skip_root=False,
        )
        assert without_skip.live is True


# ─────────────────────────────────────────────────────────────────────────────
# skip_repair opt-out — the heal lane must not spend a 30s LLM repair
# ─────────────────────────────────────────────────────────────────────────────


class TestRunningZombieSkipRepairOptOut:
    """The heal drive must invoke the completion chain with
    ``skip_repair=True``: the heal lane's transcript can be 25h stale
    and the LLM report-repair would spend a 30s call on a transcript
    nobody will read (the repair exists to fix a TRUNCATED report for
    a human reader; this lane only needs the content as the completion
    payload). The flag is forwarded to the content fetch by the drive
    (``child_reports.py:2654``)."""

    @pytest.mark.asyncio
    async def test_heal_drive_passes_skip_repair_true(
        self, zombie_harness,
    ):
        captured: dict = {}
        service = zombie_harness.service
        original = service._process_child_completion_and_notify_parent

        async def spy(instance_id, completed_message_id, **kwargs):
            captured["instance_id"] = instance_id
            captured["completed_message_id"] = completed_message_id
            captured.update(kwargs)
            return await original(
                instance_id, completed_message_id, **kwargs
            )

        service._process_child_completion_and_notify_parent = spy
        _seed_zombie_tree(zombie_harness.engine)
        healer = _make_healer(zombie_harness)
        with _patch_drive_seams():
            outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is True, outcome
        # The heal lane opts out of the LLM report-repair.
        assert captured.get("skip_repair") is True
        # C5 (unchanged by this lane): the message id stays the EMPTY
        # STRING, never None.
        assert captured.get("completed_message_id") == ""


# ─────────────────────────────────────────────────────────────────────────────
# Cycle 2 (2026-10-21) — the ANCHOR fix, the widened tree leg, the
# re-arm wiring, and the wc-strand skip_repair opt-out.
# ─────────────────────────────────────────────────────────────────────────────


class TestRunningZombieAnchorFix:
    """The freshness anchor is computed over the NON-ROOT members.

    The root's own ``last_activity_at`` is refreshed on EVERY message
    enqueue (``daemon/services/instance_messaging.py:2455``) — so a
    stranded-but-messaged root looks PERMANENTLY fresh to the 6h
    backstop and the backstop is unreachable for it. The live
    2026-10-21 repro logged a 22s-old "freshest tree
    last_activity_at" on a root whose mission had been dead for hours.
    """

    @pytest.mark.asyncio
    async def test_fresh_root_cannot_hold_a_stale_member_open(
        self, zombie_harness,
    ):
        """THE defect: a FRESH root + a STALE non-terminal member ⇒
        the backstop FIRES (live=False).

        Before the fix the root's own fresh stamp was part of the
        anchor, so the tree looked alive and the stamp was refused
        forever (the live 2026-10-21 shape)."""
        from daemon.services.mission_live_guard import (
            MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            evaluate_mission_live,
        )

        fresh = now_utc_naive() - timedelta(seconds=22)
        stale = now_utc_naive() - timedelta(
            seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 60
        )
        _insert_instance(
            zombie_harness.engine, ROOT,
            status=InstanceStatus.RUNNING.value,
            parent_id=None,
            agent_id="leader",
            last_activity_at=fresh,
        )
        _insert_instance(
            zombie_harness.engine, CHILD,
            status=InstanceStatus.IDLE.value,
            parent_id=ROOT,
            agent_id="worker",
            last_activity_at=stale,
        )
        verdict = await evaluate_mission_live(
            instance_repository=zombie_harness.instance_repo,
            instance_id=ROOT,
            bus_pending_count=None,
            timeout_seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            skip_root=True,
        )
        assert verdict.live is False, (
            "the root's own enqueue-refreshed stamp must NOT be able "
            f"to hold a stale member open — got: {verdict}"
        )
        assert verdict.timed_out is True

    @pytest.mark.asyncio
    async def test_childless_root_keeps_its_own_anchor(self, zombie_harness):
        """The FALLBACK: a childless root has no members, so its own
        stamp IS the tree signal — the historical behaviour is
        preserved exactly (a fresh childless root still holds; a
        stale one still breaks the backstop)."""
        from daemon.services.mission_live_guard import (
            MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            evaluate_mission_live,
        )

        for label, age, expect_live in (
            ("fresh", timedelta(minutes=1), True),
            ("stale", timedelta(hours=9), False),
        ):
            engine = zombie_harness.engine
            _insert_instance(
                engine, f"childless-{label}",
                status=InstanceStatus.RUNNING.value,
                parent_id=None,
                agent_id="leader",
                last_activity_at=now_utc_naive() - age,
            )
            verdict = await evaluate_mission_live(
                instance_repository=zombie_harness.instance_repo,
                instance_id=f"childless-{label}",
                bus_pending_count=None,
                timeout_seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
                skip_root=False,
            )
            assert verdict.live is expect_live, (
                f"childless root ({label}): the root's own stamp is "
                f"the tree signal — got: {verdict}"
            )

    @pytest.mark.asyncio
    async def test_member_activity_is_the_anchor_not_the_root(
        self, zombie_harness,
    ):
        """A RECENT member keeps the mission live even when the root's
        own stamp is ancient — the mirror of the defect pin (the
        anchor is the members', in both directions)."""
        from daemon.services.mission_live_guard import (
            MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            evaluate_mission_live,
        )

        _insert_instance(
            zombie_harness.engine, ROOT,
            status=InstanceStatus.RUNNING.value,
            parent_id=None,
            agent_id="leader",
            last_activity_at=now_utc_naive() - timedelta(hours=30),
        )
        _insert_instance(
            zombie_harness.engine, CHILD,
            status=InstanceStatus.IDLE.value,
            parent_id=ROOT,
            agent_id="worker",
            last_activity_at=now_utc_naive() - timedelta(minutes=5),
        )
        verdict = await evaluate_mission_live(
            instance_repository=zombie_harness.instance_repo,
            instance_id=ROOT,
            bus_pending_count=None,
            timeout_seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            skip_root=True,
        )
        assert verdict.live is True, (
            "a recently-active member must still hold the mission "
            f"open — got: {verdict}"
        )

    @pytest.mark.asyncio
    async def test_member_scoped_anchor_gap_disarms_the_backstop(
        self, zombie_harness,
    ):
        """D4 (the load-bearing disarm) — a tree whose NON-ROOT members
        all carry a NULL ``last_activity_at`` while the ROOT has one is
        a DATA GAP, not zombie evidence: the backstop is DISARMED
        (``live=True``, ``timed_out=False``).

        The root's own stamp MUST NOT flip the member-scoped
        ``anchor_missing`` flag (the cycle-2 anchor fix): the guard
        HOLDS the mission rather than terminalizing it on a data gap —
        the conservative direction (it can only delay a wrong
        finalize, never cause one).
        """
        from daemon.services.mission_live_guard import (
            MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            evaluate_mission_live,
        )

        _insert_instance(
            zombie_harness.engine, ROOT,
            status=InstanceStatus.RUNNING.value,
            parent_id=None,
            agent_id="leader",
            last_activity_at=now_utc_naive() - timedelta(hours=30),
        )
        # The member: non-terminal, and its activity stamp is NULL.
        _insert_instance(
            zombie_harness.engine, CHILD,
            status=InstanceStatus.IDLE.value,
            parent_id=ROOT,
            agent_id="worker",
            last_activity_at=None,
        )
        verdict = await evaluate_mission_live(
            instance_repository=zombie_harness.instance_repo,
            instance_id=ROOT,
            bus_pending_count=None,
            timeout_seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            skip_root=True,
        )
        assert verdict.live is True, (
            "a member-scoped anchor gap must DISARM the backstop — the "
            f"live leg holds by default. Got: {verdict}"
        )
        assert verdict.timed_out is False, (
            "the backstop must NOT fire on an anchor data gap (the "
            "root's own stamp is not a member anchor)"
        )


class TestRunningZombieCycle2TreeLeg:
    """The widened tree leg (the guard) — positives and negatives."""

    @pytest.mark.asyncio
    async def test_incident_shape_heals_through_the_real_guard(
        self, zombie_harness,
    ):
        """E2E — the live incident shape (root running + 7 completed
        direct children + a depth-3 idle explorer member, quiet for
        25h) heals through the REAL guard."""
        _seed_incident_tree(zombie_harness.engine)
        healer = _make_healer(zombie_harness)
        with _patch_drive_seams():
            outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is True, outcome
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_recently_active_member_is_not_healed(
        self, zombie_harness,
    ):
        """NEGATIVE — a member that was active inside the 6h window
        holds the mission live ⇒ SKIP (the guard's freshness leg)."""
        _seed_incident_tree(
            zombie_harness.engine,
            member_activity_age=timedelta(minutes=30),
        )
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "live-tree", outcome
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_member_with_live_task_is_not_healed(
        self, zombie_harness,
    ):
        """NEGATIVE — a member with a LIVE (in-flight) task row is
        working, not stuck: it is recently active, so the guard holds
        the mission open ⇒ SKIP.

        NAMING HONESTY (cycle-2 review, C3): this negative is enforced
        by the guard's FRESHNESS leg, not by a task-row probe — the
        seed sets ``member_activity_age=10min``, so the member's
        activity sits well inside the 6h window and the guard's
        freshness leg holds the mission open. Nothing here asserts
        that the task ROW itself was consulted. See the design doc
        §2.8.2 reviewer note: a separate member-task probe would be a
        5-line addition, deliberately not taken — it would strand a
        zombie whose stuck member still owns a lingering task row."""
        _seed_incident_tree(
            zombie_harness.engine,
            member_activity_age=timedelta(minutes=10),
            member_task_status="running",
        )
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "live-tree", outcome
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_never_dispatched_ghost_member_is_excluded(
        self, zombie_harness,
    ):
        """STRAND-EXCLUSION interaction — a never-dispatched ghost
        member (idle, version 1, zero messages, zero tasks, terminal
        direct parent, aged) does NOT hold mission liveness.

        DISCRIMINATING shape: every OTHER member is terminal AND
        recently active, so the ghost is the ONLY non-terminal
        member. With the exclusion the tree is effectively closed and
        the heal proceeds; WITHOUT it the ghost would hold the tree
        open (the anchor is fresh) and the heal would skip."""
        _seed_incident_tree(
            zombie_harness.engine,
            member_status=InstanceStatus.COMPLETED.value,
            member_activity_age=timedelta(minutes=1),
            chain_activity_age=timedelta(minutes=1),
            with_ghost=True,
        )
        healer = _make_healer(zombie_harness)
        with _patch_drive_seams():
            outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is True, outcome

    @pytest.mark.asyncio
    async def test_guard_error_skips_fail_closed(self, zombie_harness):
        """NEGATIVE — the guard is fail-OPEN (``live=False,
        error=True``) but THIS lane TERMINALIZES: an unevaluatable
        tree leg must SKIP, never proceed."""
        from daemon.services import mission_live_guard as mlg

        _seed_incident_tree(zombie_harness.engine)
        original = mlg.evaluate_mission_live

        async def _fail_open(**kwargs):
            return mlg.MissionLiveVerdict(
                live=False,
                reason="unit-injected guard error",
                error=True,
                timed_out=False,
            )

        healer = _make_healer(zombie_harness)
        with patch.object(mlg, "evaluate_mission_live", _fail_open):
            outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "guard-error", outcome
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value
        assert original is not None

    @pytest.mark.asyncio
    async def test_guard_raising_skips_fail_closed(self, zombie_harness):
        """NEGATIVE — a guard that RAISES (the defensive path) also
        skips; the lane never proceeds on an unevaluatable leg."""
        from daemon.services import mission_live_guard as mlg

        _seed_incident_tree(zombie_harness.engine)

        async def _boom(**kwargs):
            raise RuntimeError("unit-boom")

        healer = _make_healer(zombie_harness)
        with patch.object(mlg, "evaluate_mission_live", _boom):
            outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "tree-probe-error", outcome
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value


class TestRunningZombieReArmWiring:
    """The RE-ARM: the pre-stamp hold hands the root to the healer
    instead of waiting for a message-completed signal that provably
    never comes on a stranded tree."""

    @pytest.mark.asyncio
    async def test_pre_stamp_hold_schedules_a_heal(self, zombie_harness):
        """The hold fires ⇒ a heal is scheduled (the lane is chosen by
        the root's posture: ``running`` ⇒ the running-zombie lane)."""
        healer = MagicMock()
        healer.heal_running_zombie = AsyncMock(
            return_value={"healed": False, "reason": "unit-mock"}
        )
        healer.heal_instance = AsyncMock(
            return_value={"healed": False, "reason": "unit-mock"}
        )
        zombie_harness.manager._mission_strand_healer = healer
        # A recently-active member ⇒ the guard holds the mission ⇒
        # the pre-stamp hold fires.
        _seed_incident_tree(
            zombie_harness.engine,
            member_activity_age=timedelta(minutes=30),
        )
        with _patch_drive_seams():
            await zombie_harness.service\
                ._process_child_completion_and_notify_parent(
                    ROOT, "msg-incident-1",
                )
        for _ in range(3):
            await asyncio.sleep(0)
        healer.heal_running_zombie.assert_awaited_once_with(ROOT)
        # The stamp was NOT applied — the hold still holds.
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_n_holds_coalesce_to_one_schedule(self, zombie_harness):
        """COALESCING — N holds on the same root produce ONE scheduled
        heal, not N (the live 2026-10-19 repro logged 455 hold lines
        across ~40 roots; a hold storm must not become a heal storm).

        The N holds are fired THROUGH THE HOLD SITE (the real drive),
        not by calling the scheduler directly: the in-flight marker is
        taken synchronously at schedule time, so the second..Nth hold
        are dropped while the first hand-off is still running."""
        release = asyncio.Event()

        async def _slow_heal(instance_id):
            # The hand-off stays in flight until the test releases it
            # — the marker is held for the task's whole lifetime.
            await release.wait()
            return {"healed": False, "reason": "unit-mock"}

        healer = MagicMock()
        healer.heal_running_zombie = AsyncMock(side_effect=_slow_heal)
        healer.heal_instance = AsyncMock(
            return_value={"healed": False, "reason": "unit-mock"}
        )
        zombie_harness.manager._mission_strand_healer = healer
        # A recently-active member ⇒ the guard holds the mission ⇒
        # every drive fires the pre-stamp hold.
        _seed_incident_tree(
            zombie_harness.engine,
            member_activity_age=timedelta(minutes=30),
        )
        with _patch_drive_seams():
            for index in range(5):
                await zombie_harness.service\
                    ._process_child_completion_and_notify_parent(
                        ROOT, f"msg-hold-{index}",
                    )
                await asyncio.sleep(0)
        # ONE scheduled heal for the five holds.
        assert healer.heal_running_zombie.await_count == 1, (
            "N holds on the same root must coalesce into ONE scheduled "
            f"heal — got {healer.heal_running_zombie.await_count}"
        )
        assert healer.heal_instance.await_count == 0
        release.set()
        for _ in range(3):
            await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_no_schedule_when_the_tree_is_not_live(
        self, zombie_harness,
    ):
        """The hand-off fires ONLY on a hold — a normal completion (the
        tree is not live) must not schedule a heal."""
        healer = MagicMock()
        healer.heal_running_zombie = AsyncMock(
            return_value={"healed": False, "reason": "unit-mock"}
        )
        healer.heal_instance = AsyncMock(
            return_value={"healed": False, "reason": "unit-mock"}
        )
        zombie_harness.manager._mission_strand_healer = healer
        _seed_zombie_tree(zombie_harness.engine)
        with _patch_drive_seams():
            await zombie_harness.service\
                ._process_child_completion_and_notify_parent(
                    ROOT, "msg-normal-1",
                )
        for _ in range(3):
            await asyncio.sleep(0)
        healer.heal_running_zombie.assert_not_called()
        healer.heal_instance.assert_not_called()
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_wc_strand_drive_passes_skip_repair_true(
        self, zombie_harness,
    ):
        """The wc-strand (waiting_children / ghost) drive opts out of
        the LLM report-repair — the same stale-transcript hazard class
        as the running-zombie lane (leader ruling, cycle 2)."""
        captured: dict = {}
        service = zombie_harness.service

        async def spy(instance_id, completed_message_id, **kwargs):
            captured["instance_id"] = instance_id
            captured["completed_message_id"] = completed_message_id
            captured.update(kwargs)
            return None

        service._process_child_completion_and_notify_parent = spy
        # A ghost-only tree under a waiting_children root — the wedge
        # lane's signature.
        _insert_instance(
            zombie_harness.engine, ROOT,
            status=InstanceStatus.WAITING_CHILDREN.value,
            parent_id=None,
            agent_id="leader",
            last_activity_at=now_utc_naive(),
        )
        _insert_instance(
            zombie_harness.engine, CHILD,
            status=InstanceStatus.COMPLETED.value,
            parent_id=ROOT,
            agent_id="worker",
            last_activity_at=now_utc_naive() - timedelta(minutes=2),
        )
        _insert_instance(
            zombie_harness.engine, "ghost-wc",
            status=InstanceStatus.IDLE.value,
            parent_id=CHILD,
            agent_id="worker",
            version=1,
            last_activity_at=None,
            updated_at=now_utc_naive() - timedelta(minutes=5),
        )
        lifecycle = MagicMock()
        lifecycle.terminate_instance = AsyncMock(return_value=True)
        zombie_harness.manager._lifecycle_service = lifecycle
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_instance(ROOT)
        assert captured.get("skip_repair") is True, captured
        assert captured.get("instance_id") == ROOT
        # C5 (unchanged): the message id stays the EMPTY STRING.
        assert captured.get("completed_message_id") == ""
        assert outcome.get("drove") is True, outcome


class TestRunningZombieMemberAliveCompanionLeg:
    """CYCLE 4 (council F1 + RULING-1b, 2026-10-21) — the MEMBER-ALIVE
    companion leg.

    ``instances.last_activity_at`` is written on the enqueue /
    child-report / observer paths — NOT on turn progress. So a member
    inside a long (>6h) turn reads STALE to the guard's zombie
    backstop, and the backstop would break the tree (finalize the
    root) while that member is still working.

    THE RULING (cycle 3): fail-MISS over false-completion — a
    non-terminal member that owns a LIVE (non-terminal) task row
    counts as ALIVE, not stale; the root is NOT finalized.

    THE REWORK (council F1 — the cycle-3 opt-in shape is REJECTED):
    the leg is UNCONDITIONAL and lives IN THE GUARD (the shared
    chokepoint), so EVERY consumer inherits it (the pre-stamp gate,
    the emission gate, the healer's tree leg, f1, f2, the boot sweep,
    the orphan release, the work notifier). There is NO opt-in
    parameter any more — the leg is never inert.

    THE TIME BOUND (RULING-1b): a member's live work counts as alive
    ONLY IF its task row is YOUNGER than the guard's staleness horizon
    (``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS`` — the SAME constant, one
    source of truth, no new knob). An OLDER task row (a crash / kill
    mid-turn) does NOT hold the tree — the member stays stale-eligible
    and the root CAN heal. Without that bound the leg would trade one
    permanent strand for another.

    TWO PINNED INVARIANTS (council):

    * **ADDITIVE** — a member with stale activity AND no live task row
      REMAINS a zombie candidate (the OBSERVED shape);
    * **FAIL-MISS** — a member with a live (YOUNG) task row ⇒ the root
      is NOT finalized.
    """

    @pytest.mark.parametrize(
        "task_status", ["pending", "running", "processing"],
    )
    @pytest.mark.asyncio
    async def test_member_with_live_task_row_is_not_healed(
        self, zombie_harness, task_status,
    ):
        """(i) NEGATIVE — a member with a LIVE task row is WORKING:
        the root is NOT healed, the drive is NOT invoked, and the
        root's status is untouched.

        The three non-terminal task statuses are exercised: the SSOT
        set (``daemon.constants.NON_TERMINAL_TASK_STATUSES``) carries
        ``processing`` as LEGACY task vocabulary (it is NOT a
        ``TaskStatus`` member) and the leg reads the set — so a legacy
        in-flight turn is recognised too.
        """
        _seed_incident_tree(
            zombie_harness.engine,
            member_task_status=task_status,
        )
        healer = _make_healer(zombie_harness)
        drive = AsyncMock()
        with patch.object(
            zombie_harness.service,
            "_process_child_completion_and_notify_parent",
            new=drive,
        ):
            outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False, outcome
        assert outcome["skip_class"] == "member-live-task", outcome
        assert drive.await_count == 0, (
            "the completion drive must NOT be invoked while a member "
            "still owns a live task row"
        )
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_member_with_undatable_task_stamp_is_not_healed(
        self, zombie_harness, monkeypatch,
    ):
        """(iii) NEGATIVE — a member that owns a non-terminal task row
        whose stamp CANNOT be dated counts as ALIVE (fail-MISS): the
        root is NOT healed, the drive is NOT invoked, and the root's
        status is untouched (review C1).

        The ROW EXISTS, so the member may be mid-turn; only its AGE is
        unreadable. Before the fix an undatable row was
        indistinguishable from "no row" (the probe returned ``None``
        for both) and the backstop fired under a possibly-live member
        — fail-STALE against the lane's declared fail-MISS posture.

        SEEDING NOTE (schema reality, verified): ``task.created_at`` is
        NOT NULL (ORM model + the migration), so a literally NULL
        stamp cannot be written; and a GARBAGE stamp raises inside
        the ORM read — which the leg already treats as a fail-MISS
        probe ERROR. The "row exists but is undatable" state is
        therefore reached here by a fault injection at the PARSE seam,
        scoped to the member's OWN task stamp, so the anchor leg
        (which reads the instances' ``last_activity_at``) is untouched.
        """
        from daemon.services import mission_live_guard as mlg

        def _same_instant(value: datetime, target: datetime) -> bool:
            def naive(d: datetime) -> datetime:
                if d.tzinfo is not None:
                    d = d.astimezone(timezone.utc)
                return d.replace(tzinfo=None)
            return naive(value) == naive(target)

        # The member's task row is OLD (26h17m) so the NORMAL path
        # would NOT hold the tree — the fail-MISS hardening is the
        # only thing that can hold it.
        member_stamp = datetime.now(timezone.utc) - timedelta(
            hours=26, minutes=17,
        )
        _seed_incident_tree(
            zombie_harness.engine,
            member_task_status="running",
            member_task_created_at=member_stamp,
        )

        real_parse = mlg.parse_completed_at_naive

        def _parse_undatable(value):
            if isinstance(value, datetime) and _same_instant(
                value, member_stamp,
            ):
                return None
            return real_parse(value)

        monkeypatch.setattr(
            mlg, "parse_completed_at_naive", _parse_undatable,
        )

        healer = _make_healer(zombie_harness)
        drive = AsyncMock()
        with patch.object(
            zombie_harness.service,
            "_process_child_completion_and_notify_parent",
            new=drive,
        ):
            outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False, outcome
        assert outcome["skip_class"] == "member-live-task", outcome
        assert drive.await_count == 0, (
            "the completion drive must NOT be invoked while a member "
            "owns a live task row whose stamp cannot be dated"
        )
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_all_terminal_tree_with_a_lingering_task_row_still_heals(
        self, zombie_harness,
    ):
        """A-1(a) — an ALL-TERMINAL tree heals even when one of its
        terminal members still owns a non-terminal task row.

        Pins the all-terminal short-circuit: the member is TERMINAL,
        so it is not a live leg and the tree finalizes immediately.
        (The companion leg never runs on an all-terminal tree — see
        A-1(b) for the MIXED-tree shape, where the terminal-candidacy
        filter IS load-bearing.)"""
        _seed_incident_tree(
            zombie_harness.engine,
            member_status=InstanceStatus.COMPLETED.value,
            member_task_status="running",
            member_task_created_at=(
                datetime.now(timezone.utc) - timedelta(minutes=5)
            ),
        )
        healer = _make_healer(zombie_harness)
        with _patch_drive_seams():
            outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is True, outcome
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.COMPLETED.value

    @pytest.mark.parametrize(
        "task_status", ["pending", "running", "paused", "processing"],
    )
    @pytest.mark.asyncio
    async def test_a_terminal_members_task_row_does_not_hold_a_mixed_tree(
        self, zombie_harness, task_status,
    ):
        """A-1(b) — the TERMINAL-CANDIDACY filter is load-bearing on a
        MIXED tree: a TERMINAL sibling's fresh non-terminal task row
        must NOT hold the mission live.

        With the filter dropped, the terminal sibling becomes a
        candidate, its FRESH task row reads as live work, and the leg
        HOLDS the tree (``member_task_hold=True``) — a SILENT
        behaviour change (the council measured: 89 passed, 0 failed
        with ONLY that filter dropped). This pin makes it RED.

        The SSOT status set is exercised (``processing`` is legacy
        vocabulary kept in ``NON_TERMINAL_TASK_STATUSES``)."""
        from daemon.services.mission_live_guard import (
            evaluate_mission_live,
            MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
        )

        # The IDLE member (NO task row) keeps the tree MIXED, so the
        # guard's staleness branch is reached (not the all-terminal
        # short-circuit).
        _seed_incident_tree(
            zombie_harness.engine,
            member_status=InstanceStatus.IDLE.value,
            member_task_status=None,
        )
        # A TERMINAL sibling carrying a FRESH non-terminal task row.
        _insert_instance(
            zombie_harness.engine,
            "terminal-sibling",
            status=InstanceStatus.COMPLETED.value,
            parent_id=INCIDENT_D2,
            agent_id="worker",
            last_activity_at=now_utc_naive() - timedelta(hours=26),
        )
        _insert_task(
            zombie_harness.engine,
            instance_id="terminal-sibling",
            status=task_status,
            completed_at=None,
            work_id="work-terminal-sibling",
            created_at=(
                datetime.now(timezone.utc) - timedelta(minutes=5)
            ),
        )
        verdict = await evaluate_mission_live(
            instance_repository=zombie_harness.instance_repo,
            instance_id=ROOT,
            bus_pending_count=None,
            timeout_seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            skip_root=True,
        )
        assert verdict.live is False, verdict
        assert verdict.member_task_hold is False, verdict

    @pytest.mark.asyncio
    async def test_an_undatable_started_at_is_bounded_by_the_row_age(
        self, zombie_harness, monkeypatch,
    ):
        """W-3b — an undateable CHOSEN stamp is BOUNDED by the row's
        own ``created_at`` (the row's age), so a hold can never last
        forever.

        The row's ``started_at`` is present but undatable and its
        ``created_at`` is 26h old (past the 6h horizon) ⇒ the row is
        OLD ⇒ the member is NOT alive ⇒ the tree is NOT held ⇒ the
        root heals. Without the bound the probe would fall back to
        "now" (recomputed on EVERY evaluation) and the member would
        hold the tree FOREVER.

        Seeding note: an unparseable ``started_at`` RAISES inside the
        ORM's DATETIME read, so the state is reached by a fault
        injection at the parse seam — scoped to that ONE stamp (the
        anchor leg reads the instances' ``last_activity_at``, which is
        untouched)."""
        from daemon.services import mission_live_guard as mlg

        def _same_instant(value: datetime, target: datetime) -> bool:
            def naive(d: datetime) -> datetime:
                if d.tzinfo is not None:
                    d = d.astimezone(timezone.utc)
                return d.replace(tzinfo=None)
            return naive(value) == naive(target)

        # TWO distinct stamps: ``started_at`` is the one the probe
        # CHOOSES (and the one made undatable); ``created_at`` is the
        # FALLBACK the bound uses (so it must not be the same value).
        started = datetime.now(timezone.utc) - timedelta(hours=26)
        created = datetime.now(timezone.utc) - timedelta(
            hours=26, minutes=17,
        )
        _seed_incident_tree(
            zombie_harness.engine,
            member_status=InstanceStatus.IDLE.value,
            member_task_status="running",
            member_task_created_at=created,
            member_task_started_at=started,
        )

        real_parse = mlg.parse_completed_at_naive

        def _parse_undatable(value):
            if isinstance(value, datetime) and _same_instant(
                value, started,
            ):
                return None
            return real_parse(value)

        monkeypatch.setattr(
            mlg, "parse_completed_at_naive", _parse_undatable,
        )

        healer = _make_healer(zombie_harness)
        with _patch_drive_seams():
            outcome = await healer.heal_running_zombie(ROOT)
        # The row is OLD (bounded by its own created_at) ⇒ the member
        # does NOT hold the tree ⇒ the heal proceeds.
        assert outcome["healed"] is True, outcome
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_member_without_task_row_is_still_healed(
        self, zombie_harness,
    ):
        """(ii) POSITIVE — the OBSERVED zombie shape is untouched: an
        idle member with NO task row is stale-eligible and still
        heals. This is the ADDITIVE invariant: the companion leg is
        never a REPLACEMENT for the staleness expiry."""
        _seed_incident_tree(
            zombie_harness.engine,
            member_task_status=None,
        )
        healer = _make_healer(zombie_harness)
        with _patch_drive_seams():
            outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is True, outcome
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_member_with_terminal_task_row_is_still_healed(
        self, zombie_harness,
    ):
        """(iii) POSITIVE — a member whose task row is terminal-COMPLETED
        but whose INSTANCE row is still non-terminal stays
        stale-eligible: the leg reads the task row's LIVE-ness, not
        the member's mere existence."""
        _seed_incident_tree(
            zombie_harness.engine,
            member_task_status="completed",
        )
        healer = _make_healer(zombie_harness)
        with _patch_drive_seams():
            outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is True, outcome
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_guard_holds_the_tree_when_a_member_has_a_live_task(
        self, zombie_harness,
    ):
        """The GUARD-level pin — the stale tree is HELD
        (``member_task_hold``), and the backstop did NOT fire."""
        from daemon.services.mission_live_guard import evaluate_mission_live

        _seed_incident_tree(
            zombie_harness.engine,
            member_task_status="running",
        )
        verdict = await evaluate_mission_live(
            instance_repository=zombie_harness.instance_repo,
            instance_id=ROOT,
            bus_pending_count=None,
            skip_root=True,
        )
        assert verdict.live is True, verdict
        assert verdict.member_task_hold is True, verdict
        assert verdict.timed_out is False, verdict

    @pytest.mark.asyncio
    async def test_the_leg_is_unconditional(self, zombie_harness):
        """F1 — the cycle-3 OPT-IN shape is GONE.

        The guard's signature carries NO ``task_engine`` parameter, and
        a consumer that passes NOTHING extra (the f2 / f1 / boot-sweep
        call shape) still gets the hold. The leg is never inert.
        """
        import inspect

        from daemon.services.mission_live_guard import evaluate_mission_live

        params = inspect.signature(evaluate_mission_live).parameters
        assert "task_engine" not in params, (
            "the member-alive companion leg is UNCONDITIONAL (council "
            "F1) — an opt-in parameter would leave the consumers that "
            "do not opt in unprotected against the same false "
            f"completion. Got: {sorted(params)}"
        )
        _seed_incident_tree(
            zombie_harness.engine,
            member_task_status="running",
        )
        verdict = await evaluate_mission_live(
            instance_repository=zombie_harness.instance_repo,
            instance_id=ROOT,
            bus_pending_count=None,
            skip_root=True,
        )
        assert verdict.live is True, verdict
        assert verdict.member_task_hold is True, verdict
        assert verdict.timed_out is False, verdict

    @pytest.mark.asyncio
    async def test_member_task_younger_than_the_horizon_holds_the_tree(
        self, zombie_harness,
    ):
        """RULING-1b (YOUNGER side) — a member whose live task row is
        YOUNGER than the guard's staleness horizon is WORKING: the tree
        is held and the backstop does NOT fire."""
        from daemon.services.mission_live_guard import (
            MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            evaluate_mission_live,
        )

        _seed_incident_tree(
            zombie_harness.engine,
            member_task_status="running",
            member_task_created_at=(
                now_utc_naive() - timedelta(hours=1)
            ),
        )
        verdict = await evaluate_mission_live(
            instance_repository=zombie_harness.instance_repo,
            instance_id=ROOT,
            bus_pending_count=None,
            timeout_seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            skip_root=True,
        )
        assert verdict.live is True, verdict
        assert verdict.member_task_hold is True, verdict
        assert verdict.timed_out is False, verdict

    @pytest.mark.asyncio
    async def test_member_task_older_than_the_horizon_does_not_hold(
        self, zombie_harness,
    ):
        """RULING-1b (OLDER side) — a member whose live task row is
        OLDER than the horizon (a crash / kill mid-turn leaves exactly
        that row behind) does NOT hold the tree: the member is
        stale-eligible and the backstop fires.

        Without this bound the leg would trade one permanent strand
        for another (the exact class this lane closes).
        """
        from daemon.services.mission_live_guard import (
            MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            evaluate_mission_live,
        )

        _seed_incident_tree(
            zombie_harness.engine,
            member_task_status="running",
            member_task_created_at=(
                now_utc_naive()
                - timedelta(seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 60)
            ),
        )
        verdict = await evaluate_mission_live(
            instance_repository=zombie_harness.instance_repo,
            instance_id=ROOT,
            bus_pending_count=None,
            timeout_seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            skip_root=True,
        )
        assert verdict.live is False, verdict
        assert verdict.member_task_hold is False, verdict
        assert verdict.timed_out is True, verdict

    @pytest.mark.asyncio
    async def test_member_with_an_old_task_row_is_still_healed(
        self, zombie_harness,
    ):
        """The HEALER-level mirror of the RULING-1b older side: the
        root heals (the stuck member's crashed task row does not hold
        the mission open forever)."""
        from daemon.services.mission_live_guard import (
            MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
        )

        _seed_incident_tree(
            zombie_harness.engine,
            member_task_status="running",
            member_task_created_at=(
                now_utc_naive()
                - timedelta(seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 60)
            ),
        )
        healer = _make_healer(zombie_harness)
        with _patch_drive_seams():
            outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is True, outcome
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_pre_stamp_gate_holds_when_a_member_has_a_live_task(
        self, zombie_harness,
    ):
        """(v) The PRE-STAMP GATE (the real one) no longer stamps a
        root whose member holds a live task row."""
        _seed_incident_tree(
            zombie_harness.engine,
            member_task_status="running",
        )
        with _patch_drive_seams():
            await zombie_harness.service\
                ._process_child_completion_and_notify_parent(
                    ROOT, "msg-member-task-hold",
                )
        for _ in range(3):
            await asyncio.sleep(0)
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value, (
            "the pre-stamp gate must HOLD: a member with a live task "
            "row is working — the root must not be stamped"
        )

    @pytest.mark.asyncio
    async def test_pre_stamp_gate_stamps_when_the_member_task_is_terminal(
        self, zombie_harness,
    ):
        """The CONTROL for the pin above: with the member's task row
        terminal, the same shape stamps (so the hold above is caused
        by the LIVE task row, not by the shape itself)."""
        _seed_incident_tree(
            zombie_harness.engine,
            member_task_status="completed",
        )
        with _patch_drive_seams():
            await zombie_harness.service\
                ._process_child_completion_and_notify_parent(
                    ROOT, "msg-member-task-control",
                )
        for _ in range(3):
            await asyncio.sleep(0)
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.COMPLETED.value


class TestRunningZombieHealPayloadIsRawAssistantMessage:
    """Point 5 hardening (D12b, council 2026-10-21) — the heal lanes'
    published completion PAYLOAD is the RAW assistant message.

    The wc-strand ``skip_repair=True`` widening (cycle 2) is a
    DELIBERATE behaviour change: the heal lanes opt out of the LLM
    report-repair (the lane's transcript can be 25h stale; the repair
    exists to fix a TRUNCATED report for a human reader).

    The existing pin covers only the FLAG-FORWARD (``skip_repair=True``
    reaches the drive) — it cannot see what the drive then PUBLISHES,
    and it cannot see whether the 30s repair call was actually spent.
    This class pins the PAYLOAD CONTENT and the SPENT CALL:

    * the published completion payload carries the RAW assistant
      message (the repair never ran);
    * the repair LLM is NEVER called by the heal lane.

    A control drives the same shape WITHOUT the flag and shows the
    REPAIRED payload — so the pin is sensitive to the behaviour it
    claims to pin (a green pin means something).
    """

    @staticmethod
    def _truncation_shaped_history() -> list[dict]:
        """A history the truncation heuristic FIRES on: the
        penultimate assistant message is far larger than the last one
        (``wc(n-1) > ratio * wc(n)``)."""
        return [
            {
                "role": "user",
                "content": "run the mission",
                "created_at": "2026-10-20T08:00:00+00:00",
            },
            {
                "role": "assistant",
                "content": "long report " * 200,
                "created_at": "2026-10-20T08:30:00+00:00",
            },
            {
                "role": "assistant",
                "content": "mission complete",
                "created_at": T_FRESH_ASSISTANT,
            },
        ]

    @staticmethod
    def _enable_repair(harness) -> None:
        harness.manager.config.report_repair.enabled = True
        harness.manager.config.report_repair.size_ratio_threshold = 5.0
        harness.manager.config.report_repair.lookback_messages = 3
        harness.manager.config.report_repair.repair_excluded_agents = []

    @pytest.mark.asyncio
    async def test_the_heal_lane_publishes_the_raw_assistant_message(
        self, zombie_harness,
    ):
        """THE PIN — the heal lane's published completion payload is
        the RAW assistant message, and the repair call is NOT spent."""
        self._enable_repair(zombie_harness)
        _seed_incident_tree(
            zombie_harness.engine, member_task_status=None,
        )
        healer = _make_healer(zombie_harness)
        repair_mock = AsyncMock(return_value="REPAIRED-PAYLOAD")
        with patch.object(
            zombie_harness.service,
            "_repair_report_with_llm",
            new=repair_mock,
        ):
            with _patch_drive_seams(self._truncation_shaped_history()):
                outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is True, outcome
        assert repair_mock.await_count == 0, (
            "the heal lane must NOT spend the 30s LLM repair call — the "
            "lane's transcript can be 25h stale"
        )
        publish = zombie_harness.events_service\
            ._publish_instance_lifecycle_event
        assert publish.await_count, (
            "the root lane must publish its lifecycle event — the pin "
            "is meaningless if the publish never happened"
        )
        payload = publish.await_args.kwargs.get("result_summary") or ""
        assert "mission complete" in payload, (
            "the published completion payload must carry the RAW "
            f"assistant message — got: {payload!r}"
        )
        assert "REPAIRED-PAYLOAD" not in payload, (
            "the published completion payload must NOT carry the "
            f"repaired text — got: {payload!r}"
        )

    @pytest.mark.asyncio
    async def test_the_control_without_the_flag_publishes_the_repaired_text(
        self, zombie_harness,
    ):
        """THE CONTROL — the same shape driven WITHOUT the flag
        publishes the REPAIRED text and spends the repair call. This is
        what makes the pin above meaningful: without the flag the
        payload would differ."""
        self._enable_repair(zombie_harness)
        _seed_incident_tree(
            zombie_harness.engine, member_task_status=None,
        )
        repair_mock = AsyncMock(return_value="REPAIRED-PAYLOAD")
        with patch.object(
            zombie_harness.service,
            "_repair_report_with_llm",
            new=repair_mock,
        ):
            with _patch_drive_seams(self._truncation_shaped_history()):
                await zombie_harness.service\
                    ._process_child_completion_and_notify_parent(
                        ROOT, "msg-payload-control",
                    )
                for _ in range(3):
                    await asyncio.sleep(0)
        assert repair_mock.await_count == 1, (
            "the CONTROL must spend the repair call — otherwise the "
            "pin above would be vacuous"
        )
        publish = zombie_harness.events_service\
            ._publish_instance_lifecycle_event
        assert publish.await_count
        payload = publish.await_args.kwargs.get("result_summary") or ""
        assert "REPAIRED-PAYLOAD" in payload, (
            "the CONTROL must publish the repaired text — got: "
            f"{payload!r}"
        )


class TestRunningZombieKillSwitchSurface:
    """The kill-switch surface of the healer module.

    The module exposes EXACTLY ONE kill-switch: the running-zombie
    lane's ``SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_ENABLED``
    (``ServicesConfig.mission_strand_heal_running_zombie_enabled``).
    The waiting_children / ghost lane is ALWAYS-ON by HARD POLICY
    (the ``ServicesConfig`` docstring says so explicitly: "ALWAYS-ON
    infrastructure (no kill-switch); the interval is the only knob") —
    so there is NO second switch to pin. This class pins that
    surface so a future switch cannot appear (or disappear) without
    a deliberate decision.
    """

    def test_the_healer_exposes_exactly_one_kill_switch(self):
        import inspect

        from daemon.services.mission_strand_healer import (
            MissionStrandHealer,
        )

        params = inspect.signature(
            MissionStrandHealer.__init__
        ).parameters
        assert "running_zombie_enabled" in params
        assert "enabled" not in params, (
            "the module exposes ONE kill-switch (the running-zombie "
            "lane's); a second master switch would change the "
            "always-on posture of the waiting_children / ghost lane"
        )

    def test_the_config_exposes_the_rz_switch_only(self):
        from daemon.config import ServicesConfig

        fields = ServicesConfig.model_fields
        assert "mission_strand_heal_running_zombie_enabled" in fields
        assert "mission_strand_heal_enabled" not in fields, (
            "the waiting_children / ghost lane is ALWAYS-ON by hard "
            "policy — a master switch would contradict it"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Cycle-5 pins (P1/P2/P3) — the already-disclosed contracts
# ─────────────────────────────────────────────────────────────────────────────


class TestRunningZombieCycle5Pins:
    """Cycle 5 — pins for the contracts the tester gate found
    unpinned (P1/P2/P3)."""

    @pytest.mark.asyncio
    async def test_aggregate_info_is_emitted_on_the_young_task_hold_path(
        self, zombie_harness, caplog,
    ):
        """P1 — the aggregate INFO is emitted on EVERY evaluation exit,
        including the young-task HOLD path. The count is TRUTHFUL (the
        number of members whose live work was reported but NOT
        dateable — 0 when none were seen) and it says NOTHING about the
        tree's verdict."""
        import logging

        _seed_incident_tree(
            zombie_harness.engine,
            member_status=InstanceStatus.IDLE.value,
            member_task_status="running",
            member_task_created_at=(
                datetime.now(timezone.utc) - timedelta(minutes=5)
            ),
        )
        healer = _make_healer(zombie_harness)
        with caplog.at_level(logging.INFO):
            outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["skip_class"] == "member-live-task", outcome
        aggregates = [
            rec.getMessage() for rec in caplog.records
            if rec.levelno == logging.INFO
            and "members_with_undatable_live_work=" in rec.getMessage()
        ]
        assert len(aggregates) == 1, aggregates
        assert "members_with_undatable_live_work=0" in aggregates[0], (
            aggregates
        )

    @pytest.mark.asyncio
    async def test_exactly_one_aggregate_info_and_debug_only_detail(
        self, zombie_harness, caplog,
    ):
        """D2 — the aggregated one-INFO-per-evaluation bound: exactly ONE
        aggregate INFO per EVALUATION, and the PER-MEMBER detail never
        rises to INFO (it stays at DEBUG).

        Pinned against a SINGLE guard evaluation (the contract is
        per-evaluation, not per-heal — a heal may drive several
        evaluations: the tree leg, the pre-stamp eval, the emission
        gate)."""
        import logging

        from daemon.services.mission_live_guard import (
            evaluate_mission_live,
            MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
        )

        # The member's task row is OLD ⇒ the leg does not hold ⇒ the
        # end-of-loop flush runs.
        _seed_incident_tree(
            zombie_harness.engine,
            member_status=InstanceStatus.IDLE.value,
            member_task_status="running",
            member_task_created_at=(
                datetime.now(timezone.utc) - timedelta(hours=26)
            ),
        )
        with caplog.at_level(logging.DEBUG):
            verdict = await evaluate_mission_live(
                instance_repository=zombie_harness.instance_repo,
                instance_id=ROOT,
                bus_pending_count=None,
                timeout_seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
                skip_root=True,
            )
        assert verdict.live is False, verdict
        aggregates = [
            rec.getMessage() for rec in caplog.records
            if rec.levelno == logging.INFO
            and "members_with_undatable_live_work=" in rec.getMessage()
        ]
        assert len(aggregates) == 1, aggregates
        per_member_info = [
            rec.getMessage() for rec in caplog.records
            if rec.levelno == logging.INFO
            and "member-alive probe" in rec.getMessage()
            and "members_with_undatable_live_work=" not in rec.getMessage()
        ]
        assert per_member_info == [], per_member_info

    @pytest.mark.asyncio
    async def test_member_with_only_a_jobitem_row_is_not_held(
        self, zombie_harness,
    ):
        """C4 — a member whose ONLY live work is a JobItem row (no task
        row) is deliberately NOT held: the task row is the only DATEABLE
        evidence class, so the member stays stale-eligible and the root
        can heal."""
        from daemon.services.mission_live_guard import (
            evaluate_mission_live,
            MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
        )

        _seed_incident_tree(
            zombie_harness.engine,
            member_status=InstanceStatus.IDLE.value,
            member_task_status=None,   # NO task row
        )
        # The member owns a live JobItem row — live work that is NOT
        # dateable by a task row.
        with zombie_harness.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO job_queue_items "
                    "(job_id, agent_id, agent_dir, message, source, "
                    "project_id, queue_id, priority, admission_state, "
                    "created_at, instance_id, job_type, retry_count, "
                    "metadata) VALUES "
                    "('job-member-itemitem-1', 'explorer', "
                    "'agents/explorer', 'work', 'api', 'test-project', "
                    "NULL, 0, 'active', :now, :iid, 'task', 0, '{}')"
                ),
                {
                    "now": datetime.now(timezone.utc).isoformat(),
                    "iid": INCIDENT_MEMBER,
                },
            )
        # The probe says "live work" ...
        assert (
            zombie_harness.instance_repo.has_live_work(INCIDENT_MEMBER)
            is True
        ), "the JobItem row must register as live work"
        # ... but the member is NOT held on that evidence.
        verdict = await evaluate_mission_live(
            instance_repository=zombie_harness.instance_repo,
            instance_id=ROOT,
            bus_pending_count=None,
            timeout_seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            skip_root=True,
        )
        assert verdict.live is False, verdict
        assert verdict.member_task_hold is False, verdict

    @pytest.mark.asyncio
    async def test_live_work_probe_error_holds_the_tree(
        self, zombie_harness, monkeypatch,
    ):
        """D1a — the ``has_live_work`` probe ERROR yields a fail-MISS
        HOLD (``live=True`` + ``error=True``): "member liveness unknown"
        must never mean "the tree is stale"."""
        from daemon.services.mission_live_guard import (
            evaluate_mission_live,
            MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
        )

        _seed_incident_tree(
            zombie_harness.engine,
            member_status=InstanceStatus.IDLE.value,
            member_task_status="running",
            member_task_created_at=(
                datetime.now(timezone.utc) - timedelta(hours=26)
            ),
        )

        def boom(instance_id):
            raise RuntimeError("live-work probe down")

        monkeypatch.setattr(
            zombie_harness.instance_repo, "has_live_work", boom,
        )
        verdict = await evaluate_mission_live(
            instance_repository=zombie_harness.instance_repo,
            instance_id=ROOT,
            bus_pending_count=None,
            timeout_seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            skip_root=True,
        )
        assert verdict.live is True, verdict
        assert verdict.error is True, verdict

    @pytest.mark.asyncio
    async def test_task_age_probe_error_holds_the_tree(
        self, zombie_harness, monkeypatch,
    ):
        """D1b — the task-AGE probe ERROR yields a fail-MISS HOLD
        (``live=True`` + ``error=True``) — the SECOND probe-error seam,
        pinned separately from the live-work probe."""
        from daemon.services import mission_live_guard as mlg

        _seed_incident_tree(
            zombie_harness.engine,
            member_status=InstanceStatus.IDLE.value,
            member_task_status="running",
            member_task_created_at=(
                datetime.now(timezone.utc) - timedelta(hours=26)
            ),
        )

        def boom(repository, instance_id):
            raise RuntimeError("task-age probe down")

        monkeypatch.setattr(mlg, "_member_live_task_attempt_at", boom)
        verdict = await mlg.evaluate_mission_live(
            instance_repository=zombie_harness.instance_repo,
            instance_id=ROOT,
            bus_pending_count=None,
            timeout_seconds=mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
            skip_root=True,
        )
        assert verdict.live is True, verdict
        assert verdict.error is True, verdict

    @pytest.mark.asyncio
    async def test_row9_stored_watch_hold_rehydrates_instead_of_releasing(
        self, zombie_harness,
    ):
        """P2 — the row-9 consumer (the stored-watch reconcile) HOLDS on
        the member-alive verdict and therefore does NOT release the
        stored watch: it falls through to the LOSS-CASE leg, which
        REHYDRATES a watcher on the ORIGINAL work_id.

        Read from the consumer's source, not assumed: the guard's hold
        makes the ``if not verdict.live`` release block unreachable, so
        the row is CAS'd ``armed -> rehydrated`` and ``add_watch`` is
        called for the ORIGINAL work_id."""
        from daemon.repositories.job_queue.stored_watch_models import (
            StoredWatch,
        )
        from daemon.repositories.job_queue.stored_watch_repository import (
            StoredWatchRepository,
        )
        from daemon.services.job_queue_service import JobQueueService

        # The mission HOLDS: the member owns a YOUNG live task row.
        _seed_incident_tree(
            zombie_harness.engine,
            member_status=InstanceStatus.IDLE.value,
            member_task_status="running",
            member_task_created_at=(
                datetime.now(timezone.utc) - timedelta(minutes=5)
            ),
        )
        # An aged ARMED stored watch (older than the 6h GC window).
        with Session(zombie_harness.engine) as session:
            session.add(
                StoredWatch(
                    stored_watch_id="sw-row9",
                    original_work_id="work-original-row9",
                    retry_work_id="work-retry-row9",
                    watcher_instance_id="watcher-row9",
                    original_watch_events=["mission_terminal"],
                    mission_instance_id=ROOT,
                    work_instance_id=ROOT,
                    status="armed",
                    created_at=now_utc_naive() - timedelta(hours=26),
                )
            )
            session.commit()

        class _Resolver:
            def resolve_work(self, work_id):
                class _Rec:
                    status = "completed"
                return _Rec()

        class _Svc:
            pass

        svc = _Svc()
        svc._stored_watch_repo = StoredWatchRepository(
            zombie_harness.engine,
        )
        svc._watcher_repo = zombie_harness.watcher_repo
        svc._work_resolver = _Resolver()
        svc._instance_manager = zombie_harness.manager

        stats = await JobQueueService.reconcile_stored_watches(
            svc, limit=64,
        )
        assert stats["released"] == 0, stats
        assert stats["rehydrated"] == 1, stats
        with Session(zombie_harness.engine) as session:
            row = session.get(StoredWatch, "sw-row9")
            assert row is not None and row.status == "rehydrated", (
                row.status if row else None
            )
        # The watcher is re-armed on the ORIGINAL work_id.
        rehydrated_watchers = (
            zombie_harness.watcher_repo.get_watchers_for_job(
                "work-original-row9",
            )
        )
        assert rehydrated_watchers, (
            "the loss-case leg must re-arm a watcher on the ORIGINAL "
            "work_id"
        )
