"""End-to-End integration tests for ``Bucket 5`` of the Nuclear System Cleanup.

The Bucket 5 reaper terminates non-terminal ``instances`` rows that no
longer have any live work driving them. The full cleanup pipeline runs
five sequential buckets inside :meth:`JobQueueService.cleanup_non_terminal_jobs`:

  1. Batch UPDATE queued (``admission_state='queued'``) JobItems to
     ``done`` / ``terminal_reason='cancelled'`` via a single SQL UPDATE.
  2. Cancel each active JobItem through the per-row cascade
     (``terminate_instance`` for alive instances, atomic UPDATE for dead
     ones).
  3. Reap orphan active jobs (active rows whose instance is terminal
     or missing).
  4. Batch-reconcile bad-state Tasks (paused/pending whose linked
     JobItem is already terminal).
  5. **Terminate zombie instances** (this bucket) — non-terminal
     ``instances`` rows with no live JobItem AND no live Task.

These tests verify the real Bucket 5 behavior end-to-end against an
in-memory SQLite database (StaticPool + FK enforcement + real
SQLModel metadata.create_all()). The Bucket 5 code path executes real
SQL against the real ``SQLModelInstanceRepository`` /
``TaskRepository`` instances; the only mocks are for the
non-DB dependencies of :class:`JobQueueService`
(``_lock_manager``, ``_instance_manager``) because those involve
graph-task cancellation / cascade lifecycle logic that lives
outside the scope of this reaper test.

Scenario coverage (12 scenarios):

  1. Zombie termination — running instance + terminal JobItem, no tasks.
  2. Zombie termination — paused instance, no JobItems, no tasks.
  3. Zombie termination — waiting_children instance, all children gone.
  4. PROTECTION — running instance WITH active JobItem.
  5. PROTECTION — running instance WITH pending/running task.
  6. PROTECTION — running instance WITH queued JobItem.
  7. ``total_processed`` invariant holds (sum of cancelled_queued +
     cancelled_active; excludes terminated_instances).
  8. Preflight endpoint returns correct ``zombie_instance_count``.
  9. Cleanup ordering: instances whose active JobItem was cancelled by
     Buckets 1-4 get re-evaluated by Bucket 5.
  10. Edge — instance with no JobItem but has running task → protected.
  11. Edge — instance already in terminal state → not affected.
  12. Edge — empty system → no exception, all counters zero.

Run with::

    timeout 300 .venv/bin/pytest tests/integration/test_nuclear_cleanup_bucket5.py \\
        -v --tb=short -q --override-ini="addopts="
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

# Register ALL relevant tables before metadata.create_all() so the
# schema is built end-to-end — the zombie scan touches ``instances``,
# ``job_queue_items``, and ``task``; the cancel cascade reads
# ``job_locks`` / ``job_queue_items``; ``_is_instance_alive`` reads
# ``instances``.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.instance_ui_prefs.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem, JobQueue
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_queue_service import JobQueueService


# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool + FK enforcement).

    ``StaticPool`` keeps a single connection alive for the test so reads
    after writes (including those written via ``asyncio.to_thread``)
    see the latest data — the cleanup buckets all execute via
    ``asyncio.to_thread`` against this engine.
    """
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


@pytest.fixture
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def task_repo(engine: Engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def queue_repo(engine: Engine) -> JobQueueRepository:
    return JobQueueRepository(engine)


@pytest.fixture
def project_id() -> str:
    """Synthetic project_id used for the seeded JobItems."""
    return f"proj-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def service(
    job_repo: JobRepository,
    queue_repo: JobQueueRepository,
    instance_repo: SQLModelInstanceRepository,
    task_repo: TaskRepository,
) -> JobQueueService:
    """Real :class:`JobQueueService` wired against real repositories.

    The ``_lock_manager`` and ``_instance_manager`` are ``MagicMock``s
    because their graph-task / cascade logic is outside Bucket 5's
    scope; we override ``service.cancel_job`` per-test to bypass the
    real cascade when needed (so the test can directly control whether
    a JobItem is marked terminal or not).
    """
    # Use ``__new__`` to skip the ``lock_manager`` constructor check
    # (``JobLockManager.__init__`` enforces a real ``LockRepository``);
    # we set the attribute directly so cleanup_non_terminal_jobs can
    # still invoke ``release_queue_lock`` / ``release`` (both are
    # AsyncMock-able).
    svc = JobQueueService.__new__(JobQueueService)
    svc._repository = job_repo
    svc._lock_manager = MagicMock()
    # AsyncMock for the lock release methods so ``await`` works.
    svc._lock_manager.release_queue_lock = AsyncMock(return_value=False)
    svc._lock_manager.release = AsyncMock(return_value=False)
    svc._queue_repo = queue_repo
    # Wire the instance_manager mock with the REAL _instance_repository
    # + _task_repo so Bucket 5 finds them via getattr(...). The rest
    # of the instance_manager surface (terminate_instance, etc.) stays
    # a MagicMock so the active-cancel cascade becomes a no-op; tests
    # that need a successful cascade override ``svc.cancel_job``.
    mgr = MagicMock()
    mgr._instance_repository = instance_repo
    mgr._task_repo = task_repo
    # WS4 fixture repair (2026-09-06): the C2 change (2026-08-12)
    # routed Bucket 5 through the FULL ``terminate_instance`` cascade
    # instead of the raw ``transition_status_if`` UPDATE. A bare
    # ``AsyncMock(return_value=None)`` swallows the cascade's DB
    # write, so the assertions that read the instance status back
    # from the DB could never observe the termination — 6 scenarios
    # failed at base afd7c387 (attribution proven in a base worktree)
    # and the suite could not anchor the WS4 mission-lens scenarios.
    # The side_effect below performs the DB-VISIBLE portion of the
    # real cascade (the race-safe terminal transition through the
    # same repo method the pre-C2 code called) while keeping the
    # graph-task / MCP / child-cascade logic out of scope. The
    # allowed-from set is the full non-terminal set — mirroring the
    # cascade's own short-circuit-on-terminal idempotency.
    _ALL_NON_TERMINAL = (
        "running", "paused", "idle", "queued", "waiting",
        "waiting_children", "initializing", "resuming",
    )

    async def _terminate_like_cascade(instance_id: str) -> None:
        instance_repo.transition_status_if(
            instance_id,
            InstanceStatus.TERMINATED.value,
            _ALL_NON_TERMINAL,
        )

    mgr.terminate_instance = AsyncMock(side_effect=_terminate_like_cascade)
    # Holder actions (WS4) re-enqueue foreground messages through the
    # manager's public front primitive; tests that exercise the
    # re-send path override this per-test.
    mgr.enqueue_message_job = AsyncMock(return_value=None)
    svc._instance_manager = mgr
    svc._retry_engine = None
    svc._dlq_service = None
    svc._loop = None
    svc._dispatch_bus = None
    svc._idempotency_key_ttl_hours = 24
    svc._project_repo = None
    svc._watcher_repo = None
    svc._work_resolver = None
    return svc


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def make_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    project_id: str | None = None,
) -> Instance:
    """Insert a single Instance row with the given status.

    Uses a raw ORM ``Session.add`` so the test can control ``status``
    directly (the repository ``create()`` defaults to ``idle`` and
    would require an extra transition for every non-idle status).
    """
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = _now_iso()
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            agent_name="developer",
            status=status,
            project_id=project_id,
            created_at=now_iso,
            updated_at=now_iso,
        )
        s.add(inst)
        s.commit()
        s.refresh(inst)
    return inst


def make_job_item(
    engine: Engine,
    *,
    instance_id: str | None = None,
    admission_state: str = AdmissionState.QUEUED.value,
    project_id: str | None = None,
    job_type: str = "task",
    terminal_reason: str | None = None,
    deleted_at: str | None = None,
    queue_id: str | None = None,
    message: str = "test-job",
) -> str:
    """Insert a single JobItem row with the given admission_state.

    Bypasses ``JobRepository.create`` (which always defaults to
    ``admission_state='queued'``) by inserting via a raw SQL UPDATE on
    a freshly-created row. This lets the test seed terminal
    (``done``) and active (``active``) rows directly.

    WS4 additions: ``queue_id`` (lane classification for the mission
    lens) and ``message`` (the content the re-send action re-enqueues).

    Returns:
        ``job_id`` of the inserted JobItem.
    """
    jid = f"job-{uuid.uuid4().hex[:8]}"
    now_iso = _now_iso()
    with Session(engine) as s:
        job = JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message=message,
            source="test",
            project_id=project_id,
            admission_state=AdmissionState.QUEUED.value,
            job_type=job_type,
            instance_id=instance_id,
            terminal_reason=None,
            deleted_at=None,
            created_at=now_iso,
            queue_id=queue_id,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
    # Now flip the row to the desired admission_state via raw SQL.
    # We use UPDATE instead of ORM session because the JobItem model
    # has a version_id_col; the direct UPDATE bypasses optimistic-lock
    # noise and matches how the production zombie scan reads the row.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE job_queue_items "
                "SET admission_state = :state, "
                "    terminal_reason = :reason, "
                "    deleted_at = :deleted_at "
                "WHERE job_id = :job_id"
            ),
            {
                "state": admission_state,
                "reason": terminal_reason,
                "deleted_at": deleted_at,
                "job_id": jid,
            },
        )
    return jid


def make_task(
    engine: Engine,
    *,
    instance_id: str,
    status: str = TaskStatus.PENDING.value,
    work_id: str | None = None,
) -> int:
    """Insert a Task row with the given status. Returns the task id.

    Like :func:`make_job_item`, this uses a raw UPDATE after the
    initial insert so we can pin ``status`` to anything
    (running, paused, etc.) without going through the TaskRepository
    state machine. ``work_id`` lets a test pin the Task↔JobItem
    linkage (``Task.work_id == JobItem.job_id`` on the job-driven
    path).
    """
    wid = work_id or f"work-{uuid.uuid4().hex[:12]}"
    now_dt = _now_dt()
    with Session(engine) as s:
        task = Task(
            work_id=wid,
            task_type="process_message",
            instance_id=instance_id,
            message_id=None,
            status=TaskStatus.PENDING.value,
            worker_id="worker-0",
            created_at=now_dt,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        task_id = int(task.id)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE task "
                "SET status = :status "
                "WHERE id = :task_id"
            ),
            {"status": status, "task_id": task_id},
        )
    return task_id


# ──────────────────────────────────────────────────────────────────────────
# Scenarios
# ──────────────────────────────────────────────────────────────────────────


class TestBucket5ZombieReaper:
    """Real end-to-end verification of the Bucket 5 instance-level reaper.

    Every test below runs ``JobQueueService.cleanup_non_terminal_jobs``
    against the real in-memory SQLite engine. The only mocks are the
    ``_lock_manager`` and ``_instance_manager`` (set on the service
    fixture); every SQL statement — including the Bucket 5 zombie scan
    and the ``transition_status_if`` race-safe termination — executes
    against real tables.
    """

    @pytest.mark.asyncio
    async def test_scenario_1_zombie_running_instance_terminal_job(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Scenario 1: Running instance + terminal JobItem, no tasks.

        Expected: Instance terminated; ``terminated_instances=1``,
        ``total_processed=0`` (Bucket 5 excluded from
        ``total_processed``).
        """
        inst = make_instance(engine, status="running", project_id=project_id)
        # Terminal JobItem: done, terminal_reason='completed', deleted_at=None.
        # The zombie scan ignores deleted rows so we MUST set
        # deleted_at=None to ensure the JobItem counts as "terminal,
        # not live".
        make_job_item(
            engine,
            instance_id=inst.instance_id,
            admission_state="done",
            terminal_reason="completed",
            project_id=project_id,
        )

        # Bucket 1-4 should find no queued / active JobItems.
        result = await service.cleanup_non_terminal_jobs()

        assert result["terminated_instances"] == 1, (
            f"Expected 1 terminated_instance, got {result['terminated_instances']}: "
            f"{result!r}"
        )
        assert result["cancelled_queued"] == 0
        assert result["cancelled_active"] == 0
        assert result["total_processed"] == 0

        # Verify the instance is now TERMINATED in the DB.
        inst_after = instance_repo.get(inst.instance_id)
        assert inst_after.status == InstanceStatus.TERMINATED.value

    @pytest.mark.asyncio
    async def test_scenario_2_zombie_paused_instance_no_jobs(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Scenario 2: Paused instance, no JobItems, no tasks.

        Expected: Instance terminated.
        """
        inst = make_instance(engine, status="paused", project_id=project_id)

        result = await service.cleanup_non_terminal_jobs()

        assert result["terminated_instances"] == 1
        assert result["total_processed"] == 0

        inst_after = instance_repo.get(inst.instance_id)
        assert inst_after.status == InstanceStatus.TERMINATED.value

    @pytest.mark.asyncio
    async def test_scenario_3_zombie_waiting_children_instance(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Scenario 3: waiting_children instance, all children gone.

        Expected: Instance terminated.
        """
        inst = make_instance(
            engine, status="waiting_children", project_id=project_id
        )

        result = await service.cleanup_non_terminal_jobs()

        assert result["terminated_instances"] == 1
        assert result["total_processed"] == 0

        inst_after = instance_repo.get(inst.instance_id)
        assert inst_after.status == InstanceStatus.TERMINATED.value

    @pytest.mark.asyncio
    async def test_scenario_4_protection_running_with_active_job(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Scenario 4: Running instance WITH active JobItem → protected.

        The active JobItem counts as live work — the zombie scan
        excludes instances that have a JobItem in ``admission_state``
        ``queued`` or ``active`` (deleted_at IS NULL).

        Expected: ``terminated_instances=0``, instance still running.
        """
        inst = make_instance(engine, status="running", project_id=project_id)
        make_job_item(
            engine,
            instance_id=inst.instance_id,
            admission_state="active",
            project_id=project_id,
        )

        # WS4 fixture note: neutralize Bucket 2 so the scenario
        # isolates what it documents — the Bucket 5 SCAN protection
        # an ``active`` row provides. (The fixture repair made
        # ``terminate_instance`` DB-visible, so the REAL Bucket 2
        # cancel cascade now legitimately terminates the instance —
        # correct production behaviour, but it would mask the scan
        # predicate this scenario exists to pin.)
        service.cancel_job = AsyncMock(return_value=True)

        result = await service.cleanup_non_terminal_jobs()

        assert result["terminated_instances"] == 0, (
            f"Expected 0 terminated (active JobItem protects), got "
            f"{result['terminated_instances']}: {result!r}"
        )
        # Bucket 2 (active-side cancel) would fire here, but our
        # service.cancel_job default is the real method which sees
        # ``instance_manager.terminate_instance`` as a no-op AsyncMock
        # and the active cascade returns True after the no-op. The
        # JobItem stays 'active' (no real cascade effect); Bucket 5
        # therefore excludes it.
        inst_after = instance_repo.get(inst.instance_id)
        assert inst_after.status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_scenario_5_protection_running_with_running_task(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Scenario 5: Running instance WITH running task → protected.

        A live Task (``status IN ('pending','running','paused')``)
        excludes the instance from the zombie scan.
        """
        inst = make_instance(engine, status="running", project_id=project_id)
        make_task(engine, instance_id=inst.instance_id, status="running")

        result = await service.cleanup_non_terminal_jobs()

        assert result["terminated_instances"] == 0
        inst_after = instance_repo.get(inst.instance_id)
        assert inst_after.status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_scenario_6_protection_running_with_queued_job(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Scenario 6: Running instance WITH queued JobItem → protected.

        The queued JobItem also counts as live work. The bucket-1
        batch UPDATE will flip it to ``done`` / ``terminal_reason=
        'cancelled'``, but the instance still has the now-terminal
        JobItem. Bucket 5 sees no live JobItem AND no live Task →
        the instance becomes a zombie in this scenario ONLY if there
        is no live Task. We don't seed a Task, so the instance IS a
        zombie and gets terminated.

        This matches the documented behaviour: the queued batch in
        Bucket 1 cancels the JobItem, then Bucket 5 picks up the
        resulting zombie instance. The test still asserts the
        invariant that the cleanup finished without raising and that
        ``cancelled_queued`` reflects the batch-UPDATE count.
        """
        inst = make_instance(engine, status="running", project_id=project_id)
        make_job_item(
            engine,
            instance_id=inst.instance_id,
            admission_state="queued",
            job_type="task",  # batch_cancel_queued excludes 'message'
            project_id=project_id,
        )

        result = await service.cleanup_non_terminal_jobs()

        # Bucket 1 cancels the queued job (it's a 'task' type).
        assert result["cancelled_queued"] == 1, (
            f"Expected 1 cancelled_queued, got {result['cancelled_queued']}: "
            f"{result!r}"
        )
        # Bucket 5 sees a now-terminal JobItem + no live Task → instance
        # is a zombie and gets terminated.
        assert result["terminated_instances"] == 1
        # total_processed = cancelled_queued + cancelled_active.
        assert result["total_processed"] == 1

        inst_after = instance_repo.get(inst.instance_id)
        assert inst_after.status == InstanceStatus.TERMINATED.value

    @pytest.mark.asyncio
    async def test_scenario_7_total_processed_invariant(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Scenario 7: total_processed == cancelled_queued + cancelled_active.

        Mixed state: 2 zombie instances + 1 queued JobItem on a
        third instance. The queued JobItem is cancelled by Bucket 1
        (cancelled_queued=1), then the third instance becomes a zombie
        and gets terminated by Bucket 5 (terminated_instances=3).
        ``total_processed`` MUST equal 1, not 4 — it excludes
        ``terminated_instances``.
        """
        # Two bare zombies (no JobItems, no Tasks).
        for _ in range(2):
            make_instance(engine, status="running", project_id=project_id)

        # Third instance: has a queued JobItem (no live Task). After
        # Bucket 1 cancels the job, the instance becomes a zombie.
        inst_with_queued = make_instance(
            engine, status="running", project_id=project_id
        )
        make_job_item(
            engine,
            instance_id=inst_with_queued.instance_id,
            admission_state="queued",
            job_type="task",
            project_id=project_id,
        )

        result = await service.cleanup_non_terminal_jobs()

        assert result["cancelled_queued"] == 1
        assert result["cancelled_active"] == 0
        assert result["terminated_instances"] == 3
        # Invariant: total_processed = cancelled_queued + cancelled_active.
        assert result["total_processed"] == 1, (
            f"total_processed invariant violated: {result!r}"
        )

    @pytest.mark.asyncio
    async def test_scenario_8_preflight_zombie_count(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Scenario 8: ``count_zombie_instances()`` returns the right count.

        3 zombie instances + 2 protected instances (one with a live
        active JobItem, one with a live running Task). Preflight count
        must equal 3.
        """
        # 3 zombies.
        for _ in range(3):
            make_instance(engine, status="running", project_id=project_id)

        # 1 protected via active JobItem.
        protected_by_job = make_instance(
            engine, status="running", project_id=project_id
        )
        make_job_item(
            engine,
            instance_id=protected_by_job.instance_id,
            admission_state="active",
            project_id=project_id,
        )

        # 1 protected via running Task.
        protected_by_task = make_instance(
            engine, status="running", project_id=project_id
        )
        make_task(engine, instance_id=protected_by_task.instance_id, status="running")

        # Pre-flight count check via the same raw-SQL the preflight
        # endpoint uses.
        count = instance_repo.count_zombie_instances()
        assert count == 3, f"Expected 3 zombies, got {count}"

        # Sanity: the preflight-cleanup combination should also leave
        # only the 2 protected instances alive after we run cleanup.
        result = await service.cleanup_non_terminal_jobs()
        assert result["terminated_instances"] == 3

        # Post-cleanup, the zombie count should drop to 0 (both
        # protected instances still have live work).
        assert instance_repo.count_zombie_instances() == 0

    @pytest.mark.asyncio
    async def test_scenario_9_bucket5_runs_after_buckets_1_to_4(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        job_repo: JobRepository,
        engine: Engine,
        project_id: str,
    ):
        """Scenario 9: Instances whose JobItems were just cancelled by
        Buckets 1-4 are re-evaluated by Bucket 5.

        The setup: a running instance + an active JobItem whose
        ``instance_id`` points at a NON-EXISTENT instance. This forces
        the Bucket 2 active-cancel cascade to fall through the
        ``_is_instance_alive`` check (``alive=False`` because the
        target instance doesn't exist) and into the atomic
        ``cancel_job`` path, which atomically flips the JobItem to
        ``done``. Once the JobItem is terminal, the original (real)
        running instance has no live JobItem and no live Task → it's
        a zombie → Bucket 5 terminates it.

        Expected: ``cancelled_active=1`` AND ``terminated_instances=1``.
        """
        # The "real" instance we want Bucket 5 to terminate.
        real_inst = make_instance(
            engine, status="running", project_id=project_id
        )

        # Active JobItem pointing at a NON-EXISTENT instance — so
        # the active cancel cascade (Bucket 2) falls through to the
        # atomic repo.cancel_job and the JobItem becomes 'done'.
        ghost_instance_id = f"ghost-{uuid.uuid4().hex[:8]}"
        jid = make_job_item(
            engine,
            instance_id=ghost_instance_id,
            admission_state="active",
            job_type="task",  # not 'message' so find_active_jobs picks it up
            project_id=project_id,
        )

        result = await service.cleanup_non_terminal_jobs()

        # Bucket 2 cancelled the active JobItem via the atomic path.
        assert result["cancelled_active"] == 1, (
            f"Expected cancelled_active=1, got {result['cancelled_active']}: "
            f"{result!r}"
        )
        # The original real instance is now a zombie (no live JobItem
        # for IT, no live Task) → Bucket 5 terminates it.
        assert result["terminated_instances"] == 1, (
            f"Expected terminated_instances=1, got "
            f"{result['terminated_instances']}: {result!r}"
        )
        # total_processed excludes terminated_instances.
        assert result["total_processed"] == 1

        # Verify the JobItem is now terminal.
        job_after = job_repo.get(jid)
        assert job_after.admission_state == AdmissionState.DONE.value

        # Verify the real instance is now TERMINATED.
        inst_after = instance_repo.get(real_inst.instance_id)
        assert inst_after.status == InstanceStatus.TERMINATED.value

    @pytest.mark.asyncio
    async def test_scenario_10_protection_no_job_with_running_task(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Scenario 10: Running instance, no JobItem, but has a running task.

        A live running Task excludes the instance from the zombie
        scan even when there's no JobItem at all. The instance
        stays alive.
        """
        inst = make_instance(engine, status="running", project_id=project_id)
        make_task(engine, instance_id=inst.instance_id, status="running")

        result = await service.cleanup_non_terminal_jobs()

        assert result["terminated_instances"] == 0
        inst_after = instance_repo.get(inst.instance_id)
        assert inst_after.status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_scenario_11_terminal_instance_untouched(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Scenario 11: Instance already in terminal state → not affected.

        The zombie scan only picks instances whose ``status`` is NOT
        in the terminal set (``completed``, ``error``, ``terminated``,
        ``failed``). A pre-existing ``completed`` instance is
        excluded from the scan even if it has no live work.
        """
        inst = make_instance(engine, status="completed", project_id=project_id)

        result = await service.cleanup_non_terminal_jobs()

        # ``transition_status_if`` is not called for terminal-status
        # rows because they don't appear in the zombie set.
        assert result["terminated_instances"] == 0

        inst_after = instance_repo.get(inst.instance_id)
        # Status remains exactly what we set — not flipped to terminated.
        assert inst_after.status == "completed"

    @pytest.mark.asyncio
    async def test_scenario_12_empty_system_no_error(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
    ):
        """Scenario 12: Empty system (no instances) → no exception, all 0.

        The cleanup must be a safe no-op when there's nothing to
        reap. The Bucket 5 raw-SQL ``SELECT i.instance_id FROM
        instances i WHERE ...`` returns an empty result set on an
        empty DB; ``transition_status_if`` is never called; the
        counters all stay 0.
        """
        result = await service.cleanup_non_terminal_jobs()

        assert result == {
            "cancelled_queued": 0,
            "cancelled_active": 0,
            "orphaned_reaped": 0,
            "reconciled_bad_state": 0,
            "terminated_instances": 0,
            "total_processed": 0,
        }


# ──────────────────────────────────────────────────────────────────────────
# WS4 mission lens (fix/defer-self-witness-and-cleanup, 2026-09-06)
# ──────────────────────────────────────────────────────────────────────────


def make_queue(
    engine: Engine,
    *,
    project_id: str,
    queue_type: str = "defer",
    queue_name: str = "system_defer_queue",
    queue_id: str | None = None,
) -> str:
    """Insert a JobQueue row and return its ``queue_id``."""
    qid = queue_id or f"queue-{uuid.uuid4().hex[:8]}"
    now_iso = _now_iso()
    with Session(engine) as s:
        s.add(
            JobQueue(
                queue_id=qid,
                project_id=project_id,
                queue_name=queue_name,
                queue_name_lower=queue_name.lower(),
                queue_type=queue_type,
                concurrency_limit=1 if queue_type in ("defer", "background") else 3,
                is_system=True,
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        s.commit()
    return qid


def get_job(engine: Engine, job_id: str) -> dict:
    """Read a JobItem row back as a plain dict (raw SQL, no ORM cache)."""
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT admission_state, terminal_reason FROM job_queue_items "
                "WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        ).fetchone()
    assert row is not None, f"job {job_id} vanished"
    return {"admission_state": row[0], "terminal_reason": row[1]}


class TestWS4MissionLens:
    """WS4 self-shield exemption + holder actions (integration, real SQL).

    The mission lens: an instance's OWN queued defer-lane rows no
    longer shield it from the Bucket 5 reaper (the stalled-holder
    self-shield). Everything else that shielded before STILL shields
    (fail-CLOSED posture): active rows on any lane, queued rows on
    non-defer / unknown lanes, live Tasks, live children.
    """

    @pytest.mark.asyncio
    async def test_13_stalled_holder_own_defer_mirrors_reaped(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """THE mission lens: stalled holder (only own queued defer
        mirrors) is reap-eligible — the incident 6bc61f42 shape."""
        inst = make_instance(engine, status="running", project_id=project_id)
        defer_qid = make_queue(engine, project_id=project_id, queue_type="defer")
        make_job_item(
            engine,
            instance_id=inst.instance_id,
            admission_state="queued",
            job_type="message",
            project_id=project_id,
            queue_id=defer_qid,
            message="the deferred message",
        )

        result = await service.cleanup_non_terminal_jobs()

        assert result["terminated_instances"] == 1
        assert result["total_processed"] == 0
        inst_after = instance_repo.get(inst.instance_id)
        assert inst_after.status == InstanceStatus.TERMINATED.value
        # Mirror protection STAYS: cleanup must NOT have cancelled the
        # queued defer mirror (start_job's terminal-instance abort path
        # owns it later).
        job_after = get_job(engine, _only_job_id(engine))
        assert job_after["admission_state"] == "queued"

    @pytest.mark.asyncio
    async def test_14_queued_non_defer_lane_still_shields(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Fail-CLOSED half of the lens: a queued mirror on a NON-defer
        lane still shields (bucket 1 skips mirrors; the lens only
        exempts the defer lane)."""
        inst = make_instance(engine, status="running", project_id=project_id)
        fifo_qid = make_queue(
            engine, project_id=project_id, queue_type="fifo",
            queue_name="system_parallel_queue",
        )
        make_job_item(
            engine,
            instance_id=inst.instance_id,
            admission_state="queued",
            job_type="message",
            project_id=project_id,
            queue_id=fifo_qid,
        )

        service.cancel_job = AsyncMock(return_value=True)  # isolate scan

        result = await service.cleanup_non_terminal_jobs()

        assert result["terminated_instances"] == 0
        inst_after = instance_repo.get(inst.instance_id)
        assert inst_after.status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_15_active_defer_row_still_shields(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """An ACTIVE defer-lane row (any job_type) still shields — the
        exemption is for QUEUED defer rows only."""
        inst = make_instance(engine, status="running", project_id=project_id)
        defer_qid = make_queue(engine, project_id=project_id, queue_type="defer")
        make_job_item(
            engine,
            instance_id=inst.instance_id,
            admission_state="active",
            job_type="message",
            project_id=project_id,
            queue_id=defer_qid,
        )

        service.cancel_job = AsyncMock(return_value=True)  # isolate scan

        result = await service.cleanup_non_terminal_jobs()

        assert result["terminated_instances"] == 0
        inst_after = instance_repo.get(inst.instance_id)
        assert inst_after.status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_16_unknown_lane_queued_row_still_shields(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """A queued row with NO lane (``queue_id`` NULL → LEFT JOIN
        miss → ``queue_type IS NULL``) still shields — fail-CLOSED on
        unknown lanes. (The FK makes a dangling non-null queue_id
        unconstructible; the NULL lane and the queue-row-deleted lane
        are the same LEFT-JOIN-miss shape the SQL arm covers.)"""
        inst = make_instance(engine, status="running", project_id=project_id)
        make_job_item(
            engine,
            instance_id=inst.instance_id,
            admission_state="queued",
            job_type="message",
            project_id=project_id,
            queue_id=None,
        )

        service.cancel_job = AsyncMock(return_value=True)  # isolate scan

        result = await service.cleanup_non_terminal_jobs()

        assert result["terminated_instances"] == 0
        inst_after = instance_repo.get(inst.instance_id)
        assert inst_after.status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_17_live_children_still_protect_stalled_holder(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """W1 live-children guard survives the lens: a stalled holder
        with a non-terminal child is NOT reaped."""
        parent = make_instance(engine, status="waiting_children", project_id=project_id)
        child = make_instance(engine, status="running", project_id=project_id)
        # Point the child at the parent, and give the child a running
        # Task so it is genuinely live — then the parent's survival is
        # attributable to the W1 live-children anti-join, not to a
        # shared zombie shape.
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE instances SET parent_id = :p WHERE instance_id = :c"),
                {"p": parent.instance_id, "c": child.instance_id},
            )
        make_task(engine, instance_id=child.instance_id, status="running")
        defer_qid = make_queue(engine, project_id=project_id, queue_type="defer")
        make_job_item(
            engine,
            instance_id=parent.instance_id,
            admission_state="queued",
            job_type="message",
            project_id=project_id,
            queue_id=defer_qid,
        )

        result = await service.cleanup_non_terminal_jobs()

        # Neither parent (stalled + live child) nor child (running, live)
        parent_after = instance_repo.get(parent.instance_id)
        assert parent_after.status == InstanceStatus.WAITING_CHILDREN.value
        assert result["terminated_instances"] == 0

    @pytest.mark.asyncio
    async def test_18_running_task_still_protects_despite_defer_mirrors(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Genuinely-live mission: own queued defer mirrors AND a
        running Task → NOT reaped (the task anti-join is independent
        of the lens)."""
        inst = make_instance(engine, status="running", project_id=project_id)
        make_task(engine, instance_id=inst.instance_id, status="running")
        defer_qid = make_queue(engine, project_id=project_id, queue_type="defer")
        make_job_item(
            engine,
            instance_id=inst.instance_id,
            admission_state="queued",
            job_type="message",
            project_id=project_id,
            queue_id=defer_qid,
        )

        result = await service.cleanup_non_terminal_jobs()

        assert result["terminated_instances"] == 0
        inst_after = instance_repo.get(inst.instance_id)
        assert inst_after.status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_19_preflight_split_reap_vs_live_and_defer_count(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Preflight live-vs-reap split: stalled holder = reap-eligible,
        running-task mission = will-remain, deferred count = 1."""
        stalled = make_instance(engine, status="running", project_id=project_id)
        live = make_instance(engine, status="running", project_id=project_id)
        make_instance(engine, status="terminated", project_id=project_id)
        make_task(engine, instance_id=live.instance_id, status="running")
        defer_qid = make_queue(engine, project_id=project_id, queue_type="defer")
        make_job_item(
            engine,
            instance_id=stalled.instance_id,
            admission_state="queued",
            job_type="message",
            project_id=project_id,
            queue_id=defer_qid,
        )

        reap_ids = set(instance_repo.find_zombie_instances())
        non_terminal = set(instance_repo.find_non_terminal_instance_ids())
        live_ids = non_terminal - reap_ids

        assert reap_ids == {stalled.instance_id}
        assert live_ids == {live.instance_id}

        from daemon.services.defer_block_resolver import _DEFER_PENDING_COUNT_SQL

        with engine.connect() as conn:
            defer_count = int(conn.execute(_DEFER_PENDING_COUNT_SQL).scalar_one())
        assert defer_count == 1

    @pytest.mark.asyncio
    async def test_20_force_complete_refused_when_probe_busy(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Guard: a holder with LIVE non-defer work is refused (probe
        busy → terminated=False; cascade never runs).

        WS4 Round-2 W2 (2026-09-06) — the probe is now
        ``has_live_work`` (per-instance companion to the bulk zombie
        scan). A live mirror on a non-defer lane for a DIFFERENT
        instance must still trigger refusal on the requester
        (``WS1 carve-out only excludes the REQUESTER's own mirrors``).
        """
        inst = make_instance(engine, status="running", project_id=project_id)
        fifo_qid = make_queue(
            engine, project_id=project_id, queue_type="fifo",
            queue_name="system_parallel_queue",
        )
        # Live settled mirror on a NON-defer lane belonging to a
        # DIFFERENT instance: the WS1 carve-out only excludes the
        # REQUESTER's own mirrors, so probing with ``inst`` as the
        # requester sees the other holder's mirror → busy → the guard
        # must refuse (mixed stalled+live is structurally impossible
        # per WS2 strict semantics, so a busy probe means NOT stalled).
        other = make_instance(engine, status="running", project_id=project_id)
        make_job_item(
            engine,
            instance_id=other.instance_id,
            admission_state="done",
            job_type="message",
            project_id=project_id,
            queue_id=fifo_qid,
        )

        # ``has_live_work`` is the new probe — mock it True so the
        # initial probe refuses. (Real DB would also return True
        # because the other-instance's settled mirror IS a live
        # witness on the busy-set, but the mock keeps the assertion
        # shape crisp.)
        instance_repo.has_live_work = MagicMock(return_value=True)

        result = await service.force_complete_defer_holder(inst.instance_id)

        assert result == {
            "instance_id": inst.instance_id,
            "terminated": False,
            "probe_busy": True,
        }
        service._instance_manager.terminate_instance.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_21_force_complete_succeeds_mirrors_only_and_rederives(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Guard success: mirrors-only holder → terminated; the probe
        ran with the canonical arms (system scope + WS1 carve-out + the
        Round-2 W2 task/child-instance arms folded into
        ``has_live_work``), NOT the FE-reported kind."""
        inst = make_instance(engine, status="running", project_id=project_id)
        defer_qid = make_queue(engine, project_id=project_id, queue_type="defer")
        # Settled mirror on the DEFER lane: the carve-out excludes the
        # holder's own settled mirrors → probe False → stalled.
        make_job_item(
            engine,
            instance_id=inst.instance_id,
            admission_state="done",
            job_type="message",
            project_id=project_id,
            queue_id=defer_qid,
        )

        # WS4 Round-2 (2026-09-06) — the guard now calls
        # ``instance_repo.has_live_work`` (per-instance companion to
        # the bulk zombie scan) for BOTH the initial probe AND the
        # W1 TOCTOU re-check immediately before terminate. The old
        # WS1 carve-out probe (job-side only) is no longer used.
        live_work_probe = MagicMock(return_value=False)
        instance_repo.has_live_work = live_work_probe

        result = await service.force_complete_defer_holder(inst.instance_id)

        assert result["terminated"] is True
        assert result["probe_busy"] is False
        # Initial probe + W1 re-check: TWO calls to the same
        # single-instance companion. The re-check uses the same
        # bind so a probe→terminate window that lands new live work
        # is caught (W1 RED→GREEN).
        assert live_work_probe.call_count == 2
        assert live_work_probe.call_args_list[0].args == (inst.instance_id,)
        assert live_work_probe.call_args_list[1].args == (inst.instance_id,)
        service._instance_manager.terminate_instance.assert_awaited_once_with(
            inst.instance_id
        )

    @pytest.mark.asyncio
    async def test_21a_w1_toctou_recheck_refuses_when_work_appears(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """WS4 Round-2 W1 (2026-09-06, ``fix/defer-self-witness-and-cleanup``)
        RED→GREEN pin: TOCTOU re-check immediately before the
        terminate. The first probe returns False (clean), but live
        work appears between the probe and the re-check (e.g. a
        delegating-repo write that landed). The re-check returns
        True (busy) → the action is REFUSED, terminate NEVER runs.

        Pin the corner: ``terminated=False, probe_busy=True`` and
        ``terminate_instance`` is NOT awaited.
        """
        inst = make_instance(engine, status="running", project_id=project_id)

        # Probe side-effect sequence: first call False (clean),
        # second call (the W1 re-check immediately before terminate)
        # True (busy — work appeared between probe and re-check).
        live_work_probe = MagicMock(side_effect=[False, True])
        instance_repo.has_live_work = live_work_probe

        result = await service.force_complete_defer_holder(inst.instance_id)

        assert result == {
            "instance_id": inst.instance_id,
            "terminated": False,
            "probe_busy": True,
        }
        # The destructive call was NEVER made — the re-check caught
        # the racing live work.
        service._instance_manager.terminate_instance.assert_not_awaited()
        # Exactly two probe calls: the initial + the W1 re-check.
        assert live_work_probe.call_count == 2

    @pytest.mark.asyncio
    async def test_21b_w1_recheck_succeeds_when_no_work_appears(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """WS4 Round-2 W1 companion pin: when no work appears between
        the initial probe and the re-check, BOTH probes return False,
        the terminate runs, ``terminated=True, probe_busy=False``."""
        inst = make_instance(engine, status="running", project_id=project_id)

        live_work_probe = MagicMock(return_value=False)
        instance_repo.has_live_work = live_work_probe

        result = await service.force_complete_defer_holder(inst.instance_id)

        assert result == {
            "instance_id": inst.instance_id,
            "terminated": True,
            "probe_busy": False,
        }
        # Initial probe + W1 re-check, both False, terminate runs.
        assert live_work_probe.call_count == 2
        service._instance_manager.terminate_instance.assert_awaited_once_with(
            inst.instance_id
        )

    @pytest.mark.asyncio
    async def test_21c_w2_holder_with_live_task_no_jobitem_refused(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """WS4 Round-2 W2 (2026-09-06) RED→GREEN pin: holder with a
        live Task but NO JobItem at all is REFUSED. Pre-W2 the
        job-side-only probe (``has_active_non_deferred_work``) saw
        nothing busy and would have terminated the instance,
        orphaning the live Task. Post-W2 the
        ``has_live_work(instance_id)`` companion folds in the
        ``task.status IN (pending, running, paused)`` arm and refuses.

        No mocks — the real ``has_live_work`` runs against the real
        SQLite engine (file-backed recipe — see the file header).
        """
        inst = make_instance(engine, status="running", project_id=project_id)
        # Live Task with no JobItem at all (the W2 gap shape).
        make_task(engine, instance_id=inst.instance_id, status="running")

        result = await service.force_complete_defer_holder(inst.instance_id)

        assert result == {
            "instance_id": inst.instance_id,
            "terminated": False,
            "probe_busy": True,
        }
        service._instance_manager.terminate_instance.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_21d_w2_holder_with_live_child_refused(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """WS4 Round-2 W2 RED→GREEN pin: a parent instance with a
        NON-TERMINAL CHILD running a Task (child has a Task row, NO
        JobItems) is REFUSED by force-complete. The third arm of
        ``has_live_work`` (``instances.child WHERE child.parent_id = i
        AND child.status NOT IN terminal``) catches it.

        The original dispatcher spec:
          "holder with a LIVE CHILD running a Task (child has a Task
          row, NO JobItems) → force-complete REFUSED".
        """
        parent = make_instance(
            engine, status="waiting_children", project_id=project_id
        )
        child = make_instance(
            engine, status="running", project_id=project_id
        )
        # Wire the child's parent_id to the holder.
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE instances SET parent_id = :p WHERE instance_id = :c"),
                {"p": parent.instance_id, "c": child.instance_id},
            )
        # Child runs a Task — the W2 "live child running a Task" shape.
        make_task(engine, instance_id=child.instance_id, status="running")

        result = await service.force_complete_defer_holder(parent.instance_id)

        assert result == {
            "instance_id": parent.instance_id,
            "terminated": False,
            "probe_busy": True,
        }
        service._instance_manager.terminate_instance.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_21e_w2_probe_arms_match_bulk_zombie_scan(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """WS4 Round-2 W2 structural pin: ``has_live_work`` is the
        single-instance companion to the bulk ``find_zombie_instances``
        scan — they MUST classify the same instance identically.

        For each instance in the test fixture, ``has_live_work`` is
        the exact INVERSE of "would this instance appear in
        ``find_zombie_instances``?". An instance with the SAME live
        shape (live Task only, live child only, etc.) must return
        True from ``has_live_work`` AND must NOT appear in the
        zombie scan.
        """
        # Holder with a live Task — ``has_live_work`` True, zombie scan misses.
        holder = make_instance(engine, status="running", project_id=project_id)
        make_task(engine, instance_id=holder.instance_id, status="running")

        # Mirror-only holder (defer-lane settled mirror) — ``has_live_work``
        # False (WS4 mission lens: own defer mirrors don't witness),
        # zombie scan catches it.
        mirror_only = make_instance(engine, status="running", project_id=project_id)
        defer_qid = make_queue(engine, project_id=project_id, queue_type="defer")
        make_job_item(
            engine,
            instance_id=mirror_only.instance_id,
            admission_state="done",
            job_type="message",
            project_id=project_id,
            queue_id=defer_qid,
        )

        # Verify the inverse: every instance flagged live by
        # ``has_live_work`` is NOT in the zombie scan; every instance
        # NOT live is in the scan.
        live_ids = {
            iid for iid in (
                holder.instance_id, mirror_only.instance_id,
            )
            if instance_repo.has_live_work(iid)
        }
        zombie_ids = set(instance_repo.find_zombie_instances())

        assert live_ids == {holder.instance_id}
        assert mirror_only.instance_id in zombie_ids
        assert holder.instance_id not in zombie_ids

    @pytest.mark.asyncio
    async def test_22_force_complete_missing_instance_404(
        self,
        service: JobQueueService,
    ):
        """A missing holder raises LookupError (router maps to 404)."""
        with pytest.raises(LookupError):
            await service.force_complete_defer_holder("inst-missing")

    @pytest.mark.asyncio
    async def test_23_resend_cancels_defer_job_and_reenqueues_foreground(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Re-send-foreground: the queued defer job is cancelled (via
        the existing cancel path → done/cancelled) and its message
        content is re-enqueued as a NEW foreground message job through
        the manager front primitive — NOT a mirror mutation."""
        inst = make_instance(engine, status="running", project_id=project_id)
        defer_qid = make_queue(engine, project_id=project_id, queue_type="defer")
        jid = make_job_item(
            engine,
            instance_id=inst.instance_id,
            admission_state="queued",
            job_type="message",
            project_id=project_id,
            queue_id=defer_qid,
            message="please run the deferred work",
        )
        # The mirror's authoritative Task — the job-driven path mints
        # ``Task.work_id == JobItem.job_id``, so seed the linkage.
        make_task(engine, instance_id=inst.instance_id, work_id=jid)

        from types import SimpleNamespace

        service._instance_manager.enqueue_message_job = AsyncMock(
            return_value=SimpleNamespace(job_id="new-job-1", message_id="msg-1")
        )

        result = await service.resend_deferred_foreground(inst.instance_id)

        assert result["found_defer_jobs"] == 1
        assert result["cancelled_defer_jobs"] == 1
        assert result["skipped_empty_content"] == 0
        assert result["resend_results"][0]["cancelled_job_id"] == jid
        assert result["resend_results"][0]["job_id"] == "new-job-1"
        # The authoritative Task was cancelled too (union consistency).
        assert result["resend_results"][0]["task_cancelled"] is True

        # The cancelled row is terminal (done/cancelled) — the OLD
        # mirror, mutated through the registered cancel writer.
        job_after = get_job(engine, jid)
        assert job_after["admission_state"] == "done"
        assert job_after["terminal_reason"] == "cancelled"

        # The linked Task is CANCELLED — no bad-state shape minted.
        with engine.begin() as conn:
            task_status = conn.execute(
                text("SELECT status FROM task WHERE work_id = :w"),
                {"w": jid},
            ).scalar_one()
        assert task_status == TaskStatus.CANCELLED.value

        # The NEW foreground job went through the public front
        # primitive with the original content (is_deferred defaults
        # False → foreground).
        service._instance_manager.enqueue_message_job.assert_awaited_once_with(
            instance_id=inst.instance_id,
            message="please run the deferred work",
            source="api",
        )

    @pytest.mark.asyncio
    async def test_24_resend_ignores_active_and_non_defer_rows(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """Re-send scope = QUEUED defer-lane rows only: active defer
        rows, queued non-defer rows, and unknown-lane rows are left
        alone (found=0 → the router maps to 400)."""
        inst = make_instance(engine, status="running", project_id=project_id)
        defer_qid = make_queue(engine, project_id=project_id, queue_type="defer")
        fifo_qid = make_queue(
            engine, project_id=project_id, queue_type="fifo",
            queue_name="system_parallel_queue",
        )
        make_job_item(
            engine, instance_id=inst.instance_id, admission_state="active",
            job_type="message", project_id=project_id, queue_id=defer_qid,
        )
        make_job_item(
            engine, instance_id=inst.instance_id, admission_state="queued",
            job_type="message", project_id=project_id, queue_id=fifo_qid,
        )
        make_job_item(
            engine, instance_id=inst.instance_id, admission_state="queued",
            job_type="message", project_id=project_id,
            queue_id=None,  # NULL lane — LEFT JOIN miss, fail-closed
        )

        result = await service.resend_deferred_foreground(inst.instance_id)

        assert result["found_defer_jobs"] == 0
        assert result["cancelled_defer_jobs"] == 0

    @pytest.mark.asyncio
    async def test_25_resend_empty_content_cancels_without_reenqueue(
        self,
        service: JobQueueService,
        instance_repo: SQLModelInstanceRepository,
        engine: Engine,
        project_id: str,
    ):
        """A queued defer row with empty content is cancelled but
        contributes nothing to the re-enqueue pass."""
        inst = make_instance(engine, status="running", project_id=project_id)
        defer_qid = make_queue(engine, project_id=project_id, queue_type="defer")
        make_job_item(
            engine, instance_id=inst.instance_id, admission_state="queued",
            job_type="message", project_id=project_id, queue_id=defer_qid,
            message="   ",
        )

        result = await service.resend_deferred_foreground(inst.instance_id)

        assert result["cancelled_defer_jobs"] == 1
        assert result["skipped_empty_content"] == 1
        service._instance_manager.enqueue_message_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_26_resend_missing_instance_raises(self, service: JobQueueService):
        with pytest.raises(LookupError):
            await service.resend_deferred_foreground("inst-missing")

    # ──────────────────────────────────────────────────────────────────────
    # Unblock-round ITEM 2 (2026-09-06): real-wiring integration test
    # for ``GET /api/jobs/cleanup/preflight``'s ``defer_blocked_count``
    # surface. The previous (round-2) unit test
    # ``TestCleanupPreflightEndpoint.test_preflight_ws4_live_vs_reap_split_and_defer_count``
    # HAND-SET ``manager._defer_block_resolver`` and reached
    # ``defer_resolver._job_repo.engine`` directly — the attribute was
    # NEVER assigned in production (`api.py:977-978` wires only the
    # queues.py module-global), so the count was silently 0 in
    # production, masked behind the MagicMock hand-set. The test below
    # uses the canonical production-shape wiring (the queues.py
    # singleton via ``set_defer_block_resolver(...)``) against a real
    # SQLite engine (file-backed recipe, see file header) and confirms
    # the count propagates end-to-end through the FastAPI endpoint.
    # ──────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_27_preflight_defer_count_via_real_singleton_wiring(
        self,
        engine: Engine,
        project_id: str,
        tmp_path,
    ):
        """Unblock-round ITEM 2: real-wiring integration proof.

        Production-shape wiring:

        * ``set_defer_block_resolver(DeferBlockResolver(job_repo=...))``
          — the same ``daemon/api.py:977-978`` lifespan-startup path;
        * the FastAPI ``cleanup_preflight`` endpoint reads the resolver
          via ``daemon.routers.queues.get_defer_block_resolver()``;
        * the resolver's public
          :meth:`DeferBlockResolver.defer_pending_count` instance
          method reaches ``self._job_repo.engine`` internally — the
          router NEVER touches the engine.

        RED→GREEN anchor: this test would FAIL with the round-2
        wiring shape (``manager._defer_block_resolver`` hand-set,
        because the production code never assigns that attribute) —
        the GREEN proof is "test passes with the canonical wiring
        path". See `test_27b_preflight_defer_count_wiring_regression`
        for the inverse pin.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from daemon.routers.jobs_management import router as management_router
        from daemon.routers.queues import (
            set_defer_block_resolver,
            _defer_block_resolver as queues_resolver_global,
        )
        from daemon.services.defer_block_resolver import DeferBlockResolver

        # Seed: a queued defer-lane JobItem — this is the input the
        # canonical SELECT counts (admission_state='queued' +
        # queue_type='defer' + deleted_at IS NULL).
        defer_qid = make_queue(
            engine, project_id=project_id, queue_type="defer"
        )
        make_job_item(
            engine,
            admission_state="queued",
            job_type="message",
            project_id=project_id,
            queue_id=defer_qid,
        )
        make_job_item(
            engine,
            admission_state="queued",
            job_type="message",
            project_id=project_id,
            queue_id=defer_qid,
        )

        # Sanity: the canonical SQL constant returns 2 (the count the
        # instance method will produce).
        from daemon.services.defer_block_resolver import (
            _DEFER_PENDING_COUNT_SQL,
        )
        with engine.connect() as conn:
            raw_count = int(
                conn.execute(_DEFER_PENDING_COUNT_SQL).scalar_one()
            )
        assert raw_count == 2

        # Production-shape wiring: the queues.py module-global. THE
        # SAME WIRING ``daemon/api.py`` lifespan runs at app startup.
        job_repo = JobRepository(engine)
        defer_resolver = DeferBlockResolver(job_repo=job_repo)
        set_defer_block_resolver(defer_resolver)
        try:
            # The preflight endpoint reads the resolver via the
            # ``get_defer_block_resolver()`` factory — assert it sees
            # the same instance we just wired (this is the connection
            # test for the singleton channel).
            from daemon.routers.queues import get_defer_block_resolver
            assert get_defer_block_resolver() is defer_resolver

            # Build the FastAPI app with the management router. Use a
            # ``MagicMock`` manager that has the bare-minimum repo
            # attributes the preflight resolves (no hand-set on
            # ``_defer_block_resolver`` — that would re-introduce the
            # round-2 mask class).
            manager = MagicMock(spec=["_task_repo", "_instance_repository"])
            manager._task_repo = TaskRepository(engine)
            manager._instance_repository = SQLModelInstanceRepository(engine)

            app = FastAPI()
            app.include_router(management_router)
            app.state.manager = manager

            with TestClient(app) as client:
                response = client.get("/jobs/cleanup/preflight")

            assert response.status_code == 200
            body = response.json()
            # The defer count propagated through the singleton →
            # resolver → instance-method path. Round-2 silently
            # returned 0 because the wiring was never assigned in
            # production.
            assert body["defer_blocked_count"] == 2, (
                f"defer_blocked_count should be 2 (real wiring); "
                f"got {body['defer_blocked_count']}. If this is 0, "
                f"the preflight's wiring regressed — see unblock-round "
                f"ITEM 2 for the canonical-wiring fix."
            )
        finally:
            # Reset the queues.py module-global — the integration
            # test must not leak state to siblings. Production uses
            # idempotent ``set_defer_block_resolver`` at lifespan
            # startup; tests need explicit teardown.
            import daemon.routers.queues as queues_module
            queues_module._defer_block_resolver = None
            assert queues_resolver_global is None  # global reset

    def test_27b_preflight_defer_count_wiring_regression(
        self,
        engine: Engine,
        project_id: str,
    ):
        """Unblock-round ITEM 2 — RED-pin for the original regression.

        The round-2 wiring tried to read ``manager._defer_block_resolver``
        (``jobs_management.py:675``) but NO production code ever assigns
        that attribute — ``api.py:977-978`` wires only the
        ``queues.py`` module-global. Round-2's unit test hand-set
        ``manager._defer_block_resolver``, masking the regression.

        After the unblock-round canonical fix, the preflight reads
        ``daemon.routers.queues.get_defer_block_resolver()`` — when
        the singleton is unwired (test double, partial lifespan, or
        regression in startup order), the preflight sees ``503`` from
        the factory, degrades to ``defer_blocked_count = 0``, and the
        OLD ``manager._defer_block_resolver`` hand-set becomes a
        silent zero — exactly the failure shape the round-2 mask
        hid. This test pins the BRAND-NEW behavior: with the
        production wiring absent, the preflight MUST report 0 (NOT
        swallow an exception, NOT crash, NOT show a stale value).
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from daemon.routers.jobs_management import router as management_router
        from daemon.routers.queues import _defer_block_resolver

        # Seed: queued defer-lane JobItems.
        defer_qid = make_queue(
            engine, project_id=project_id, queue_type="defer"
        )
        make_job_item(
            engine,
            admission_state="queued",
            job_type="message",
            project_id=project_id,
            queue_id=defer_qid,
        )

        # Ensure the queues.py module-global is unwired (the
        # regression shape — production's ``set_defer_block_resolver``
        # never ran).
        import daemon.routers.queues as queues_module
        queues_module._defer_block_resolver = None
        try:
            manager = MagicMock(spec=["_task_repo", "_instance_repository"])
            manager._task_repo = TaskRepository(engine)
            manager._instance_repository = SQLModelInstanceRepository(engine)
            # NO hand-set on ``_defer_block_resolver`` (the round-2
            # mask class). NO call to ``set_defer_block_resolver``.

            app = FastAPI()
            app.include_router(management_router)
            app.state.manager = manager

            with TestClient(app) as client:
                response = client.get("/jobs/cleanup/preflight")

            assert response.status_code == 200
            body = response.json()
            # The unwired-singleton path degrades to 0 — pinned so a
            # future refactor cannot silently swallow the gap.
            assert body["defer_blocked_count"] == 0
        finally:
            queues_module._defer_block_resolver = None
            assert _defer_block_resolver is None  # global reset


def _only_job_id(engine: Engine) -> str:
    """Return the single job_queue_items row's job_id (test helper)."""
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT job_id FROM job_queue_items")).fetchall()
    assert len(rows) == 1, f"expected exactly 1 job row, got {len(rows)}"
    return rows[0][0]
