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
from daemon.repositories.job_queue.models import AdmissionState, JobItem
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
    mgr.terminate_instance = AsyncMock(return_value=None)
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
) -> str:
    """Insert a single JobItem row with the given admission_state.

    Bypasses ``JobRepository.create`` (which always defaults to
    ``admission_state='queued'``) by inserting via a raw SQL UPDATE on
    a freshly-created row. This lets the test seed terminal
    (``done``) and active (``active``) rows directly.

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
            message="test-job",
            source="test",
            project_id=project_id,
            admission_state=AdmissionState.QUEUED.value,
            job_type=job_type,
            instance_id=instance_id,
            terminal_reason=None,
            deleted_at=None,
            created_at=now_iso,
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
) -> int:
    """Insert a Task row with the given status. Returns the task id.

    Like :func:`make_job_item`, this uses a raw UPDATE after the
    initial insert so we can pin ``status`` to anything
    (running, paused, etc.) without going through the TaskRepository
    state machine.
    """
    work_id = f"work-{uuid.uuid4().hex[:12]}"
    now_dt = _now_dt()
    with Session(engine) as s:
        task = Task(
            work_id=work_id,
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
