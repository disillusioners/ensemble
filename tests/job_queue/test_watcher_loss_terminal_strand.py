"""RED harness — watcher-loss terminal strand (commission 2026-10-14).

RCA (adjudicated CONFIRMED, live mission b6e24d78 / receipt 6fc156f3):
a spawned-but-never-dispatched GRANDCHILD worker persisted NON-TERMINAL
(status=idle, last_activity_at=NULL, zero message_queue rows, zero task
rows — the live poison row was 4d41bd67, parent=tester d28b3ce2, depth-2
under the leader root). At the leader's final turn:

  1. The sync stamp path (``_process_child_completion_db_sync`` root
     lane) counts DIRECT children only — all completed → the root is
     stamped COMPLETED (after the FP4 L1 WAITING_CHILDREN → RUNNING
     discharge; the 09:58:10 live event was discharge+stamp, NOT an
     enqueue — zero message_queue rows existed at that instant).
  2. The emission-time ``_root_completion_gate`` runs the RECURSIVE
     tree walk (``evaluate_mission_live``) → finds the idle grandchild
     → any_non_terminal=True → gate BLOCKS (reason=tree_liveness) →
     post-stamp downgrade COMPLETED→WAITING_CHILDREN + suppressed
     publish. The root's own waiting_children status is then itself a
     non-terminal member on every later guard evaluation — the
     self-referential hold.
  3. No publish ⇒ no lifecycle completed event ⇒ the observer's
     ``_finalize_job`` post-commit outbox never runs ⇒ the receipt
     JobItem never terminalizes (admission_state stays ACTIVE) ⇒ the
     watcher never fires.
  4. Terminal-strand gap: after the downgrade there is NO future
     message-completed signal (0 children, 0 queued messages) so the
     event-driven re-check never re-arms. Every 300s the
     WatchReconcileSweepService re-evaluates only; f2 skips with
     "mission-live guard holds JobItem"; the 6h zombie backstop is the
     only natural exit and its anchor re-rolls on any activity touch.

This file pins the RED shape (the wedge), the two GREEN controls that
prove the harness is sound (C1 clean tree publishes; C2 terminalized
straggler publishes), and the f2-lane hold verdict (the adjudication
evidence that job_recovery IS a lane that sees held rows of this shape).

Watcher-row faithfulness (live row f7df7f12): the armed watch carried
the 10 TRANSPORT event kinds (``ALL_WATCHABLE_EVENTS`` — 5 terminal +
in_progress + 4 mid-flight QA) and NO ``mission_terminal`` token; the
assertions ride the actual row contents, never an assumed
mission_terminal registration. ``mission_terminal`` is strictly
opt-in (watcher_models.py) — no display bug.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from daemon.constants import TERMINAL_INSTANCE_STATUSES
from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_models import (
    ALL_WATCHABLE_EVENTS,
    JobWatcher,
)
from daemon.repositories.job_queue.watcher_repository import (
    JobWatcherRepository,
)
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.message_queue.models import (  # noqa: F401
    MessageQueue,
)
from daemon.repositories.task.models import Task as TaskModel  # noqa: F401
from daemon.services import mission_live_guard as _mlg
from daemon.services.child_reports import ChildReportsService
from daemon.services.job_queue_service import JobQueueService
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.timestamps import now_utc_naive
from daemon.services.work_resolver import WorkResolverService

# Timestamps (all UTC). The root's final assistant message is FRESH —
# it is the turn that observed the last child report (live: the final
# child report 7dbd5301 triggered the 09:58:10 discharge+stamp).
T_CHILD_COMPLETED = datetime(2026, 9, 20, 17, 30, 0, tzinfo=timezone.utc)
T_FRESH_ASSISTANT = "2026-09-20T17:40:00+00:00"

ROOT = "root-leader-strand"
TESTER = "child-tester-strand"
GHOST = "grandchild-worker-strand"
RECEIPT_JOB = "job-strand-receipt-1"
WATCHER_INSTANCE = "watcher-inst-strand"


# ─────────────────────────────────────────────────────────────────────────────
# Engine + seed helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def strand_engine():
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
) -> None:
    """Insert an ``instances`` row (raw SQL — mirrors the
    test_mission_live_guard seeder so the ghost row can carry
    ``last_activity_at = NULL`` and ``version = 1`` exactly)."""
    now = datetime.now(timezone.utc).isoformat()
    activity_iso = (
        last_activity_at.isoformat() if last_activity_at is not None else None
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
                "updated_at": now,
                "last_activity_at": activity_iso,
                "version": version,
                "parent_id": parent_id,
            },
        )


def _seed_poison_tree(engine, *, with_ghost: bool = True) -> None:
    """The RCA tree: root=leader → child=tester(COMPLETED) →
    grandchild=worker(idle, never-dispatched ghost).

    The ghost matches ``ChildReportsService._ghost_child_filter`` and
    ``SQLModelInstanceRepository.filter_never_dispatched_ids`` exactly:
    status=idle, version=1, ZERO message_queue rows, ZERO task rows,
    last_activity_at=NULL (the live poison row 4d41bd67 shape).
    """
    _insert_instance(
        engine, ROOT,
        status=InstanceStatus.WAITING_CHILDREN.value,
        last_activity_at=now_utc_naive(),
    )
    _insert_instance(
        engine, TESTER,
        status=InstanceStatus.COMPLETED.value,
        parent_id=ROOT,
        agent_id="tester",
        last_activity_at=now_utc_naive() - timedelta(minutes=2),
    )
    if with_ghost:
        _insert_instance(
            engine, GHOST,
            status=InstanceStatus.IDLE.value,
            parent_id=TESTER,
            agent_id="worker",
            version=1,
            last_activity_at=None,
        )


def _insert_receipt(engine) -> None:
    """Receipt JobItem (ACTIVE) + its COMPLETED work Task + the armed
    watcher row (10 transport event kinds, NO mission_terminal)."""
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
                     'api', 'test-project', NULL, 0, 'active',
                     :created_at, :instance_id, 'task', 0, :metadata)
                """
            ),
            {
                "job_id": RECEIPT_JOB,
                "created_at": now,
                "instance_id": ROOT,
                "metadata": json.dumps({}),
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, status, retry_count,
                     created_at, cancel_requested, retry_scheduled,
                     work_id, is_deferred, is_background, completed_at)
                VALUES
                    ('process_message', :instance_id, 'completed', 0,
                     :created_at, 0, 0, :work_id, 0, 0, :completed_at)
                """
            ),
            {
                "instance_id": ROOT,
                "created_at": now,
                "work_id": RECEIPT_JOB,
                "completed_at": now,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, last_activity_at, version,
                     parent_id)
                VALUES
                    (:instance_id, 'orchestrator', 'agents/orchestrator',
                     'idle', 'test-project', :created_at, :created_at,
                     NULL, 1, NULL)
                """
            ),
            {"instance_id": WATCHER_INSTANCE, "created_at": now},
        )
    with Session(engine) as session:
        session.add(
            JobWatcher(
                job_id=RECEIPT_JOB,
                instance_id=WATCHER_INSTANCE,
                # DELTA 3 (live row f7df7f12): the ACTUAL transport
                # event set — 10 kinds, NO mission_terminal token.
                watch_events=list(ALL_WATCHABLE_EVENTS),
            )
        )
        session.commit()


def _seed_child_completed_event(engine, when: datetime) -> None:
    with Session(engine) as session:
        session.add(
            Event(
                instance_id=ROOT,
                kind=EventKind.CHILD_COMPLETED.value,
                data="{}",
                created_at=when,
            )
        )
        session.commit()


def _fresh_history() -> list[dict]:
    return [
        {
            "role": "user",
            "content": "run the mission",
            "created_at": "2026-09-20T16:00:00+00:00",
        },
        {
            "role": "assistant",
            "content": "mission complete — final report",
            "created_at": T_FRESH_ASSISTANT,
        },
    ]


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


class _EmptyBusStub:
    async def pending_watchers(self, source_task_id):
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Harness: real repos + real notify chain + ChildReportsService (fp4 shape)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def strand_harness(strand_engine):
    instance_repo = SQLModelInstanceRepository(strand_engine)
    task_repo_fq = __import__(
        "daemon.repositories.task.repository", fromlist=["TaskRepository"]
    ).TaskRepository(strand_engine)
    job_repo = JobRepository(strand_engine)
    watcher_repo = JobWatcherRepository(strand_engine)
    from daemon.repositories.event.repository import EventRepository
    event_bus = __import__(
        "daemon.services.event_bus", fromlist=["EventBus"]
    ).EventBus(event_repo=EventRepository(strand_engine))

    manager = MagicMock()
    manager.engine = strand_engine
    manager._engine = strand_engine
    manager._instance_repository = instance_repo
    manager._task_repo = task_repo_fq
    manager._watcher_repo = watcher_repo
    manager._event_bus = event_bus
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._live_hub.stream_question_pack = AsyncMock()
    manager._live_hub.stream_answer_received = AsyncMock()
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-strand")
    )
    manager.config = MagicMock()
    manager.config.report_repair = MagicMock()
    manager.config.report_repair.enabled = False
    manager.config.report_repair.repair_excluded_agents = []
    manager.config.report_repair.size_ratio_threshold = 5.0
    manager.config.report_repair.lookback_messages = 3
    manager.config.report_integrity = MagicMock()
    manager.config.report_integrity.b_terminal_waiting_guard_enabled = True

    # Real notify chain (mirrors test_mission_live_guard
    # _make_real_queue_service): real WorkResolverService +
    # JobQueueService over the SAME engine; only enqueue_message is a
    # transport-boundary mock.
    resolver = WorkResolverService(
        task_repo=task_repo_fq,
        job_repo=job_repo,
        instance_repo=instance_repo,
    )
    queue_service = JobQueueService(
        repository=job_repo,
        lock_manager=MagicMock(),
        queue_repo=MagicMock(),
        instance_manager=manager,
    )
    queue_service.set_watcher_repo(watcher_repo)
    queue_service.set_work_resolver(resolver)
    manager._job_queue_service = queue_service

    events_service = MagicMock()
    events_service._publish_instance_lifecycle_event = AsyncMock()

    service = ChildReportsService(
        manager=manager, events_service=events_service,
    )

    return type(
        "StrandHarness",
        (),
        {
            "engine": strand_engine,
            "manager": manager,
            "instance_repo": instance_repo,
            "task_repo": task_repo_fq,
            "job_repo": job_repo,
            "watcher_repo": watcher_repo,
            "queue_service": queue_service,
            "events_service": events_service,
            "service": service,
        },
    )()


def _drive_root_completion(harness) -> None:
    """Deprecated sync wrapper — tests use :func:`_adrive_root_completion`."""
    raise NotImplementedError("use _adrive_root_completion")


async def _adrive_root_completion(harness) -> None:
    """Async drive — awaited inside the tests."""
    with patch(
        "daemon.services.child_reports.get_instance_messages",
        new_callable=AsyncMock,
        return_value=_fresh_history(),
    ), patch(
        "daemon.services.child_reports.get_last_assistant_timestamp",
        new_callable=AsyncMock,
        return_value=T_FRESH_ASSISTANT,
    ), patch(
        "daemon.services.child_reports.MainLoopBridge.run_async_no_wait"
    ):
        await harness.service._process_child_completion_and_notify_parent(
            ROOT, "root-final-msg-1",
        )


# ─────────────────────────────────────────────────────────────────────────────
# RED→GREEN — the fix contract (FAILS at base = RED evidence; PASSES
# once components 1-3 land = GREEN)
# ─────────────────────────────────────────────────────────────────────────────


class TestWatcherLossStrandRed:
    @pytest.mark.asyncio
    async def test_stranded_grandchild_must_not_hold_mission(
        self, strand_harness,
    ):
        """RED→GREEN fix contract: a never-dispatched idle grandchild
        stranded under a TERMINAL parent must NOT hold the mission.

        At base this FAILS — the observed (bug) behavior, captured as
        the RED evidence: the sync helper stamps the root COMPLETED
        (direct-children check passes — the strand is depth-2), the
        emission gate's recursive walk counts the idle grandchild,
        BLOCKS (reason=tree_liveness), downgrades COMPLETED →
        WAITING_CHILDREN and suppresses the publish. No lifecycle
        completed event; the receipt JobItem stays ACTIVE; the armed
        watcher never fires; no future signal ever re-arms.

        After the fix the strand is excluded from liveness semantics:
        the gate passes, the publish fires, the watcher is
        CAS-claimed."""
        _seed_poison_tree(strand_harness.engine, with_ghost=True)
        _insert_receipt(strand_harness.engine)
        _seed_child_completed_event(
            strand_harness.engine, T_CHILD_COMPLETED,
        )

        await _adrive_root_completion(strand_harness)

        # 1. Root must be COMPLETED — the strand must not force the
        #    stamp→downgrade churn nor hold the root at WC forever.
        row = _get_instance_row(strand_harness.engine, ROOT)
        assert row.status == InstanceStatus.COMPLETED.value, (
            "FIX CONTRACT: the stranded never-dispatched grandchild "
            "must not hold the mission (base behavior: stamp → "
            f"gate downgrade → stuck at {row.status!r})"
        )

        # 2. The lifecycle completed event MUST fire.
        completed = [
            c for c in strand_harness.events_service
            ._publish_instance_lifecycle_event.await_args_list
            if c.kwargs.get("status") == "completed"
            and c.kwargs.get("instance_id") == ROOT
        ]
        assert completed, (
            "FIX CONTRACT: lifecycle completed event must fire for "
            "the stranded-tree mission (base behavior: publish "
            "suppressed by the tree_liveness gate block)"
        )

        # 3. The armed watcher MUST fire (CAS-claimed by the real
        #    notify chain), on its ACTUAL transport event set — the
        #    live row f7df7f12 shape: 10 transport kinds, NO
        #    mission_terminal token.
        assert strand_harness.watcher_repo.get_watchers_for_job(
            RECEIPT_JOB
        ) == [], (
            "FIX CONTRACT: the armed watcher row must be CAS-claimed "
            "(fired) once the mission closes"
        )

        # 4. The strand row itself is left for the healer (component
        #    3) — the read-side exclusion must not silently mutate it.
        ghost = _get_instance_row(strand_harness.engine, GHOST)
        assert ghost.status == InstanceStatus.IDLE.value

    @pytest.mark.asyncio
    async def test_guard_must_not_hold_on_stranded_orphan(
        self, strand_harness,
    ):
        """Guard-level RED→GREEN: ``evaluate_mission_live`` must NOT
        report live for a tree whose only non-terminal member is a
        never-dispatched ghost stranded under a TERMINAL parent.

        Base behavior (RED evidence): live=True, reason="non-terminal
        member present" — the idle grandchild counts live and holds
        every guard consumer (emission gate, f2, boot sweep)."""
        _seed_poison_tree(strand_harness.engine, with_ghost=True)
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=strand_harness.instance_repo,
            instance_id=ROOT,
        )
        assert verdict.live is False, (
            "FIX CONTRACT: the stranded never-dispatched orphan must "
            f"not hold mission liveness (base behavior: {verdict.reason})"
        )
        assert verdict.error is False

    @pytest.mark.asyncio
    async def test_genuinely_running_descendant_still_holds(
        self, strand_harness,
    ):
        """SAFETY BOUND (fail-closed direction — must hold at base AND
        after the fix): a RUNNING descendant is genuinely-live shaped
        (it ran a turn — it is NOT never-dispatched) and MUST keep
        holding the mission even though its direct parent is
        terminal. The strand exclusion is scoped to the
        never-dispatched ghost signature (idle + version 1 + zero
        messages + zero tasks); it must never exclude a member that
        shows execution evidence."""
        _seed_poison_tree(strand_harness.engine, with_ghost=False)
        # Replace the ghost with a RUNNING grandchild (has "run" —
        # give it a message_queue row so it is not ghost-shaped).
        _insert_instance(
            strand_harness.engine, GHOST,
            status=InstanceStatus.RUNNING.value,
            parent_id=TESTER,
            agent_id="worker",
            last_activity_at=now_utc_naive(),
        )
        with Session(strand_harness.engine) as session:
            session.add(MessageQueue(
                message_id="mq-running-proof",
                instance_id=GHOST,
                content="work",
                type="process_message",
                source="internal:test",
                status="completed",
                priority=0,
            ))
            session.commit()

        verdict = await _mlg.evaluate_mission_live(
            instance_repository=strand_harness.instance_repo,
            instance_id=ROOT,
        )
        assert verdict.live is True, (
            "SAFETY BOUND: a RUNNING descendant must keep holding "
            f"mission liveness (got: {verdict.reason})"
        )

        # Full drive: the mission stays held (publish suppressed).
        _insert_receipt(strand_harness.engine)
        _seed_child_completed_event(
            strand_harness.engine, T_CHILD_COMPLETED,
        )
        await _adrive_root_completion(strand_harness)
        row = _get_instance_row(strand_harness.engine, ROOT)
        assert row.status == InstanceStatus.WAITING_CHILDREN.value, (
            "SAFETY BOUND: a genuinely-live descendant must hold the "
            f"root at WAITING_CHILDREN (got: {row.status!r})"
        )
        completed = [
            c for c in strand_harness.events_service
            ._publish_instance_lifecycle_event.await_args_list
            if c.kwargs.get("status") == "completed"
        ]
        assert not completed, "SAFETY BOUND: publish must stay suppressed"
        assert _job_admission_state(
            strand_harness.engine, RECEIPT_JOB
        ) == AdmissionState.ACTIVE.value
        assert strand_harness.watcher_repo.get_watchers_for_job(RECEIPT_JOB)


# ─────────────────────────────────────────────────────────────────────────────
# f2 lane — hold verdict observable (adjudication evidence)
# ─────────────────────────────────────────────────────────────────────────────


class TestF2LaneHoldsPoisonShape:
    @pytest.mark.asyncio
    async def test_f2_skips_with_mission_live_guard_hold(
        self, strand_harness,
    ):
        """The drift-reconciler f2 lane SEES the wedged row of this
        shape and SKIPS with the mission-live guard hold (the
        log-verified 09:59→10:59 skip cadence). Resolves the
        placement adjudication by test: job_recovery is a lane that
        provably evaluates held rows of this shape."""
        from unittest.mock import patch as _patch

        _seed_poison_tree(strand_harness.engine, with_ghost=True)
        _insert_receipt(strand_harness.engine)
        # f2 needs the work Task's completed_at to clear its age
        # floor — backdate it 1h (the shape is hours old when wedged).
        with strand_harness.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET completed_at = :c "
                    "WHERE work_id = :w"
                ),
                {
                    "c": (
                        now_utc_naive() - timedelta(hours=1)
                    ).isoformat(),
                    "w": RECEIPT_JOB,
                },
            )

        recovery = JobRecoveryService(
            job_repository=strand_harness.job_repo,
            lock_repository=MagicMock(),
            instance_repository=strand_harness.instance_repo,
            job_queue_service=strand_harness.queue_service,
            task_repository=strand_harness.task_repo,
            stale_task_recovery=None,
        )
        with _patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            stats = await recovery.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        skips = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_skipped_mission_live"
            and d.get("job_id") == RECEIPT_JOB
        ]
        assert skips, (
            "f2 lane must observably SKIP with the mission-live guard "
            f"hold for the poison shape: {stats['details']}"
        )
        # Receipt stays ACTIVE; watcher survives (nothing fired).
        assert _job_admission_state(
            strand_harness.engine, RECEIPT_JOB
        ) == AdmissionState.ACTIVE.value
        assert strand_harness.watcher_repo.get_watchers_for_job(RECEIPT_JOB)


# ─────────────────────────────────────────────────────────────────────────────
# Controls — the harness itself is sound (must PASS at base)
# ─────────────────────────────────────────────────────────────────────────────


class TestControls:
    @pytest.mark.asyncio
    async def test_c1_clean_tree_publishes_and_fires_watcher(
        self, strand_harness,
    ):
        """C1: the SAME tree WITHOUT the straggler → gate passes →
        lifecycle completed event fires → watcher row CAS-claimed
        (delivered)."""
        _seed_poison_tree(strand_harness.engine, with_ghost=False)
        _insert_receipt(strand_harness.engine)
        _seed_child_completed_event(
            strand_harness.engine, T_CHILD_COMPLETED,
        )

        await _adrive_root_completion(strand_harness)

        row = _get_instance_row(strand_harness.engine, ROOT)
        assert row.status == InstanceStatus.COMPLETED.value
        completed = [
            c for c in strand_harness.events_service
            ._publish_instance_lifecycle_event.await_args_list
            if c.kwargs.get("status") == "completed"
            and c.kwargs.get("instance_id") == ROOT
        ]
        assert completed, "C1: clean tree must publish completed"
        assert strand_harness.watcher_repo.get_watchers_for_job(
            RECEIPT_JOB
        ) == [], "C1: watcher row must be CAS-claimed (fired)"

    @pytest.mark.asyncio
    async def test_c2_terminalized_straggler_unblocks(
        self, strand_harness,
    ):
        """C2: terminalize the straggler → the next gate pass
        publishes. Proves the block is CAUSED by the non-terminal
        straggler and nothing else."""
        _seed_poison_tree(strand_harness.engine, with_ghost=True)
        _insert_receipt(strand_harness.engine)
        _seed_child_completed_event(
            strand_harness.engine, T_CHILD_COMPLETED,
        )

        # Drive 1: wedges (RED shape).
        await _adrive_root_completion(strand_harness)
        assert _get_instance_row(
            strand_harness.engine, ROOT
        ).status == InstanceStatus.WAITING_CHILDREN.value

        # Terminalize the straggler (operator-style cleanup).
        with strand_harness.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET status = 'terminated' "
                    "WHERE instance_id = :g"
                ),
                {"g": GHOST},
            )

        # Drive 2: gate passes now → publish fires.
        await _adrive_root_completion(strand_harness)
        row = _get_instance_row(strand_harness.engine, ROOT)
        assert row.status == InstanceStatus.COMPLETED.value
        completed = [
            c for c in strand_harness.events_service
            ._publish_instance_lifecycle_event.await_args_list
            if c.kwargs.get("status") == "completed"
            and c.kwargs.get("instance_id") == ROOT
        ]
        assert completed, "C2: terminalized straggler must unblock"
        assert strand_harness.watcher_repo.get_watchers_for_job(
            RECEIPT_JOB
        ) == []


# ─────────────────────────────────────────────────────────────────────────────
# Guard unit evidence — captured in the RED run, superseded above by the
# flipped fix-contract assertions (test_guard_must_not_hold_on_stranded_orphan).
# ─────────────────────────────────────────────────────────────────────────────
