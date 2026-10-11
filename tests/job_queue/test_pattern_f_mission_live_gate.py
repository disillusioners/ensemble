"""Fix e8a61da3 (Option E) — Pattern (f) liveness-aware finalize gate
(E1) + durable JOB_COMPLETED witness row (E2).

The 043d64a8 compounding failures from the RCA:

* **(i) Pattern (f) premature finalize** — the drift reconciler's
  f1 lane (active JobItem + FAILED/CANCELLED backing Task) had ONLY
  the task-scoped retry-child-live conjunct. A usage-limit deferral
  cancelled the parent task, the retry task quiesced between
  attempts, and the reconciler finalized the JobItem — firing
  ``notify_watchers`` — while the MISSION was still alive. The
  premature notify spent the mission's watch rows at the wrong
  moment. E1 wires the canonical ``evaluate_mission_live`` guard
  into the f1 lane (read-only; the retry-child conjunct STAYS).

* **(ii) Witness no-publish** — the recovery finalize boundary
  published NOTHING durable; durable ``job_completed`` rows were
  minted only by the observer producer on the natural path, so the
  recovery terminal left zero job-side trace. E2 publishes the
  ``JOB_COMPLETED`` witness row via the EventBus durable lane at the
  Pattern (f) finalize boundaries (no watcher fan-out).

Covers:

* AC1 — mission tree LIVE (the exact 043d64a8 posture: parent task
  CANCELLED by the deferral, retry quiesced, instances non-terminal)
  → the reconciler REFUSES to finalize (skip detail
  ``orphan_active_skipped_mission_live_f1``), the boundary is never
  called, the JobItem stays ACTIVE.
* AC2 — retry-child-live conjunct still load-bearing: a PENDING
  retry child on the instance skips via the W1 detail (BEFORE the
  mission gate).
* AC3 — all-terminal tree → finalize proceeds through the boundary
  AND the E2 witness row is published (kind ``job_completed``,
  payload carries job_id + status; no watcher fan-out from it).
* AC4 — E2 soft-fail: no events service wired → finalize still
  succeeds.

Fixture shape mirrors ``test_w1_retry_child_lineage_conjunct.py``
(raw-SQL seeders; job_queue/conftest engine fixtures) so the two
files read as one family.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.stale_task_recovery import StaleTaskRecovery


def _insert_instance(
    engine,
    instance_id: str,
    project_id: str = "test-project",
    status: str = InstanceStatus.RUNNING.value,
    created_at: datetime | None = None,
) -> None:
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version)
                VALUES
                    (:instance_id, 'developer', 'agents/developer',
                     :status, :project_id, :created_at, :updated_at, 1)
                """
            ),
            {
                "instance_id": instance_id,
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
            },
        )


def _insert_job_item(
    engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str | None = None,
    admission_state: str = AdmissionState.ACTIVE.value,
    created_at: datetime | None = None,
) -> None:
    now = (created_at or datetime.now(timezone.utc)).isoformat()
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
                    (:job_id, 'developer', 'agents/developer', 'hi', 'api',
                     :project_id, :queue_id, 0, :admission_state,
                     :created_at, :instance_id, 'task', 0, '{}')
                """
            ),
            {
                "job_id": job_id,
                "project_id": project_id,
                "queue_id": queue_id,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
            },
        )


def _insert_task(
    engine,
    *,
    work_id: str,
    instance_id: str,
    status: str = TaskStatus.PENDING.value,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
    retry_count: int = 0,
) -> int:
    now = created_at or datetime.now(timezone.utc)
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred, is_background,
                     completed_at)
                VALUES
                    ('process_message', :instance_id, NULL, :status,
                     :retry_count, :created_at, 0, 0, :work_id, 0, 0,
                     :completed_at)
                """
            ),
            {
                "instance_id": instance_id,
                "status": status,
                "retry_count": retry_count,
                "created_at": now,
                "work_id": work_id,
                "completed_at": (
                    completed_at.isoformat() if completed_at else None
                ),
            },
        )
        return result.lastrowid


@pytest.fixture
def task_repository(engine) -> TaskRepository:
    return TaskRepository(engine)


class _WitnessBus:
    """Capture ``create_event`` calls (the E2 durable lane)."""

    def __init__(self):
        self.calls: list[dict] = []

    async def create_event(self, *, instance_id, kind, data=None, **kw):
        self.calls.append(
            {"instance_id": instance_id, "kind": kind, "data": data}
        )


def _witness_manager(instance_repo) -> MagicMock:
    """Instance-manager double carrying the real instance repository
    (the mission-live guard walks it) + a capture bus for E2."""
    manager = MagicMock()
    manager._instance_repository = instance_repo
    bus = _WitnessBus()
    events_service = MagicMock()
    events_service._event_bus = bus
    manager._events_service = events_service
    manager._bus = bus
    return manager


def _make_service(
    *,
    engine,
    repository,
    lock_repo,
    instance_repo,
    task_repository,
    job_queue_service_mock,
    witness_manager,
) -> JobRecoveryService:
    stale_recovery = StaleTaskRecovery(
        task_repository=task_repository,
        message_repository=None,
        event_repository=None,
    )
    return JobRecoveryService(
        job_repository=repository,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
        job_queue_service=job_queue_service_mock,
        task_repository=task_repository,
        stale_task_recovery=stale_recovery,
        instance_manager=witness_manager,
    )


def _boundary_mock(repository) -> MagicMock:
    """JobQueueService mock whose ``_finalize_terminal`` performs the
    real atomic_transition (the W1-test convention) so a stray
    finalization actually persists."""
    mock = MagicMock()
    mock.notify_watchers = AsyncMock(return_value=None)

    async def _boundary_side_effect(
        *, instance_id, decision, job_id, error_message, target_status,
    ):
        now = datetime.now(timezone.utc).isoformat()
        repository.atomic_transition(
            job_id,
            from_status="active",
            to_status=target_status,
            completed_at=now,
            error_message=error_message,
        )
        return (job_id, None)

    mock._finalize_terminal = AsyncMock(side_effect=_boundary_side_effect)
    return mock


def _seed_043d64a8_shape(
    engine,
    *,
    mission_live: bool,
    retry_child_live: bool = False,
):
    """Seed the exact incident posture.

    leader (mission root) → worker (deferred instance). The worker's
    parent task is CANCELLED (the usage-limit deferral supersede); a
    retry task existed and either quiesced (terminal — the reconciler
    sees no in-flight work) or is still live (the W1 conjunct case).
    ``mission_live`` controls the instance-tree posture.
    """
    worker_status = (
        InstanceStatus.RUNNING.value if mission_live
        else InstanceStatus.COMPLETED.value
    )
    _insert_instance(engine, "inst-e1-leader")
    _insert_instance(engine, "inst-e1-worker", status=worker_status)
    _insert_job_item(
        engine,
        job_id="job-e1-043d",
        instance_id="inst-e1-worker",
        queue_id="queue-e1",
        created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
    )
    _insert_task(
        engine,
        work_id="job-e1-043d",
        instance_id="inst-e1-worker",
        status=TaskStatus.CANCELLED.value,  # the deferral supersede
        created_at=datetime.now(timezone.utc) - timedelta(seconds=3600),
        completed_at=datetime.now(timezone.utc) - timedelta(seconds=1800),
    )
    # The retry task: fresh work_id (schedule_retry mints one),
    # same instance. Quiesced = terminal unless retry_child_live.
    _insert_task(
        engine,
        work_id="job-e1-043d-retry",
        instance_id="inst-e1-worker",
        status=(
            TaskStatus.PENDING.value
            if retry_child_live
            else TaskStatus.CANCELLED.value
        ),
        retry_count=1,
    )


class TestE1MissionLiveFinalizeGate:
    @pytest.mark.asyncio
    async def test_mission_alive_refuses_finalize_043d64a8(
        self,
        engine,
        repository,
        lock_repo,
        task_repository,
    ):
        """AC1 — the exact 043d64a8 scenario. Parent task CANCELLED,
        retry quiesced (W1 conjunct passes), but the mission tree is
        STILL LIVE (worker instance running under the leader). Pre-E1
        the reconciler finalized + notified; post-E1 it holds."""
        instance_repo = SQLModelInstanceRepository(engine=engine)
        _seed_043d64a8_shape(engine, mission_live=True)
        jq = _boundary_mock(repository)
        service = _make_service(
            engine=engine,
            repository=repository,
            lock_repo=lock_repo,
            instance_repo=instance_repo,
            task_repository=task_repository,
            job_queue_service_mock=jq,
            witness_manager=_witness_manager(instance_repo),
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )
        details = stats.get("details", []) if isinstance(stats, dict) else []

        # The skip is observable through the drift details.
        assert any(
            d.get("pattern") == "orphan_active_skipped_mission_live_f1"
            for d in details
        ), details
        # The boundary was NEVER reached — no finalize, no notify.
        jq._finalize_terminal.assert_not_called()
        jq.notify_watchers.assert_not_called()
        # The JobItem stays ACTIVE.
        with engine.begin() as conn:
            state = conn.execute(
                text(
                    "SELECT admission_state FROM job_queue_items "
                    "WHERE job_id = 'job-e1-043d'"
                )
            ).fetchone()[0]
        assert state == AdmissionState.ACTIVE.value

    @pytest.mark.asyncio
    async def test_retry_child_live_conjunct_fires_before_mission_gate(
        self,
        engine,
        repository,
        lock_repo,
        task_repository,
    ):
        """AC2 — the W1 conjunct is STILL load-bearing: a live
        (PENDING) retry child skips via the retry-child detail, not
        the mission gate."""
        instance_repo = SQLModelInstanceRepository(engine=engine)
        _seed_043d64a8_shape(engine, mission_live=True, retry_child_live=True)
        jq = _boundary_mock(repository)
        service = _make_service(
            engine=engine,
            repository=repository,
            lock_repo=lock_repo,
            instance_repo=instance_repo,
            task_repository=task_repository,
            job_queue_service_mock=jq,
            witness_manager=_witness_manager(instance_repo),
        )

        stats = await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )
        details = stats.get("details", []) if isinstance(stats, dict) else []
        assert any(
            d.get("pattern") == "orphan_active_skipped_retry_child_live"
            for d in details
        ), details
        jq._finalize_terminal.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_terminal_tree_finalizes_and_publishes_witness(
        self,
        engine,
        repository,
        lock_repo,
        task_repository,
    ):
        """AC3 — mission genuinely over (all instances terminal): the
        finalize proceeds through the boundary AND the E2 witness row
        is published on the EventBus durable lane (kind
        ``job_completed``, job_id-addressed, watcher fan-out NEVER
        called from it)."""
        instance_repo = SQLModelInstanceRepository(engine=engine)
        _seed_043d64a8_shape(engine, mission_live=False)
        witness_manager = _witness_manager(instance_repo)
        jq = _boundary_mock(repository)
        service = _make_service(
            engine=engine,
            repository=repository,
            lock_repo=lock_repo,
            instance_repo=instance_repo,
            task_repository=task_repository,
            job_queue_service_mock=jq,
            witness_manager=witness_manager,
        )

        await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        # Finalize went through the boundary (cancelled target — the
        # Task's terminal status is authoritative).
        jq._finalize_terminal.assert_awaited_once()
        # E2: the durable witness row WAS published.
        bus = witness_manager._bus
        assert len(bus.calls) == 1
        call = bus.calls[0]
        assert call["kind"] == "job_completed"
        assert call["data"]["job_id"] == "job-e1-043d"
        assert call["data"]["status"] == "cancelled"
        assert call["data"]["source"] == "job_recovery_service.pattern_f"
        # The JobItem left ACTIVE.
        with engine.begin() as conn:
            state = conn.execute(
                text(
                    "SELECT admission_state FROM job_queue_items "
                    "WHERE job_id = 'job-e1-043d'"
                )
            ).fetchone()[0]
        assert state != AdmissionState.ACTIVE.value

    @pytest.mark.asyncio
    async def test_witness_publish_is_soft_fail(
        self,
        engine,
        repository,
        lock_repo,
        task_repository,
    ):
        """AC4 — no events service wired (legacy test double shape):
        the witness publish skips quietly and the finalize STILL
        succeeds."""
        instance_repo = SQLModelInstanceRepository(engine=engine)
        _seed_043d64a8_shape(engine, mission_live=False)
        jq = _boundary_mock(repository)
        bare = MagicMock()  # no usable events service
        bare._instance_repository = instance_repo
        # Explicit None — a bare MagicMock would auto-vivify the
        # attribute and exercise the exception path instead of the
        # intended quiet-skip path.
        bare._events_service = None
        service = _make_service(
            engine=engine,
            repository=repository,
            lock_repo=lock_repo,
            instance_repo=instance_repo,
            task_repository=task_repository,
            job_queue_service_mock=jq,
            witness_manager=bare,
        )

        await service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )

        jq._finalize_terminal.assert_awaited_once()
        with engine.begin() as conn:
            state = conn.execute(
                text(
                    "SELECT admission_state FROM job_queue_items "
                    "WHERE job_id = 'job-e1-043d'"
                )
            ).fetchone()[0]
        assert state != AdmissionState.ACTIVE.value
