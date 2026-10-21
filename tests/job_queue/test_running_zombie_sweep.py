"""Running-zombie strand — the THIRD member of the strand family.

Commission "running-zombie instances" (2026-10-21). Live shape
(instance 432988b8, 2026-10-19/20): a MISSION ROOT whose instance row
is stuck in ``running`` while every descendant is terminal and the
root's own latest task row is terminal-COMPLETED — the completion
EVENT was lost, so nothing ever terminalized the instance row. The f2
drift finalizer heals only the JobItem (the mission-live guard's
all-terminal short-circuit at ``mission_live_guard.py:509-524``
releases the held row); the INSTANCE row is a different table and
stays ``running`` forever (25h in the live incident).

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
  4. the whole permanent tree is terminal, and the enumeration is
     neither breadth- nor depth-truncated
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

ROOT = "root-zombie-432988b8"
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
) -> None:
    """Insert a ``task`` row (raw SQL — the ``task`` table has several
    non-nullable columns the ORM default would otherwise fill)."""
    created = created_at or datetime.now(timezone.utc)
    created_iso = created.isoformat()
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
                    (:task_type, :instance_id, :status, 0,
                     :created_at, 0, 0, :work_id, 0, 0, :completed_at,
                     :suspension_reason, :resume_target_turn_id)
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
) -> None:
    """Seed the exact live shape.

    root: ``running``, no parent, ``last_activity_at`` FROZEN (the
    predicate correction: the age keys on ``task.completed_at``, never
    on ``last_activity_at``).
    child: terminal (the tree is closed).
    task: terminal-COMPLETED, ``completed_at`` aged by ``task_age``.
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
        last_activity_at=now_utc_naive() - timedelta(hours=26),
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
    async def test_live_descendant_is_skipped(self, zombie_harness):
        """Leg 4: a LIVE descendant means the tree is not closed."""
        _seed_zombie_tree(
            zombie_harness.engine,
            child_status=InstanceStatus.RUNNING.value,
        )
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "live-descendant"
        assert _get_instance_row(
            zombie_harness.engine, ROOT
        ).status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_idle_descendant_is_skipped(self, zombie_harness):
        """Leg 4: an ``idle`` descendant is neither live nor terminal —
        the tree is not provably closed ⇒ SKIP (fail-closed)."""
        _seed_zombie_tree(
            zombie_harness.engine, child_status=InstanceStatus.IDLE.value,
        )
        healer = _make_healer(zombie_harness)
        outcome = await healer.heal_running_zombie(ROOT)
        assert outcome["healed"] is False
        assert outcome["skip_class"] == "non-terminal-descendant"

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
    (``child_reports.py:2600-2645``, ``skip_root=True``).

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
    (``child_reports.py:2554``)."""

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
