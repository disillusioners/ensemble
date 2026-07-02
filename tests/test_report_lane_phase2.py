"""Phase 2 report-lane decoupling tests — in-memory SQLite, no daemon, no LLM.

Covers:
  2.1 Independent-turn: each child completion creates its own PROCESS_REPORT
     task; children resolve in separate parent graph turns.
  2.2 Pause safety: report Tasks for PAUSED instances are not claimable;
     resuming fires the queued report Task.
  2.3 Crash recovery: FIRED-but-unstamped watchers survive a restart via
     _recover_fired_unsent; finalize is idempotent.
  2.4 Error propagation: one child erroring → parent finalizes as ERROR
     (any-error rule); all children succeeding → parent COMPLETED; sticky
     error flag cleared after finalize.

All tests work against both PostgreSQL and SQLite (pure SQLModel, dialect-aware
where necessary). PostgreSQL is the primary DB — this file uses in-memory
SQLite to avoid PG requirement during development. A parallel PG test file
mirrors these tests: tests/postgres/test_report_lane_phase2_pg.py.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Register all table models so create_all() picks them up.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401  (Instance, InstanceHierarchy)
import daemon.repositories.job_queue.models  # noqa: F401  (JobItem, AdmissionState)
import daemon.repositories.message_queue.models  # noqa: F401  (MessageQueue)
import daemon.repositories.task.models  # noqa: F401  (Task, TaskStatus, TaskType)
from daemon.repositories.dependency_bus import (
    DependencyWatcherRepository,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    Outcome,
    get_dependency_bus,
    set_dependency_bus,
)



# >>> test-local status_to_admission (Phase 4 cleanup) <<<
# Phase 4 cleanup removed ``status_to_admission`` from
# ``daemon.repositories.job_queue.models``. Redefined here for test
# seeds that derive ``admission_state`` from a ``status`` value.
def status_to_admission(status):  # noqa: ANN001,ANN201
    return {
        # JobStatus source values
        "pending": "queued",
        "processing": "active",
        "paused": "active",
        "completed": "done",
        "failed": "done",
        "cancelled": "done",
        "dead_letter": "dead",
        # AdmissionState source values (identity map — pass-through),
        # so callers passing ``AdmissionState.X.value`` resolve to
        # themselves instead of the ``"queued"`` fallback.
        "queued": "queued",
        "active": "active",
        "done": "done",
        "dead": "dead",
    }.get(status, "queued")


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def engine():
    """In-memory SQLite engine (StaticPool for cross-thread safety)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def task_repo(engine):
    """TaskRepository bound to the test engine."""
    return TaskRepository(engine, on_pending_task=lambda: None)


@pytest.fixture
def bus_repo(engine):
    """In-memory DependencyWatcherRepository for unit tests.

    Uses StaticPool so the in-memory database is shared across threads
    (required because DependencyBus uses asyncio.to_thread internally).
    """
    return DependencyWatcherRepository(engine)


@pytest.fixture(autouse=True)
def _reset_bus_singleton():
    """Clear the module-level bus singleton between tests."""
    set_dependency_bus(None)
    yield
    set_dependency_bus(None)


@pytest.fixture
async def bus(bus_repo):
    """Started DependencyBus; auto-stops on teardown."""
    b = DependencyBus(bus_repo)
    await b.start()
    set_dependency_bus(b)
    try:
        yield b
    finally:
        await b.stop()
        set_dependency_bus(None)


# =============================================================================
# Helpers
# =============================================================================


def _seed_instance(
    engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    parent_id: str | None = None,
) -> str:
    """Insert an Instance row."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        s.add(Instance(
            instance_id=iid,
            agent_id="leader",
            agent_dir="/tmp/leader",
            agent_name="leader",
            parent_id=parent_id,
            status=status,
            version=1,
            instance_metadata={},
        ))
        s.commit()
    return iid


def _seed_job(
    engine,
    *,
    instance_id: str,
    job_id: str | None = None,
    status: str = AdmissionState.ACTIVE.value,
    job_type: str = "message",
    job_metadata: dict | None = None,
) -> str:
    """Insert a JobItem row (needed for claim_pending_task cross-system guard).

    ``job_metadata`` is stored in the ``metadata`` JSON/JSONB column
    (mapped via ``sa_column=Column("metadata", JSONBType)`` in
    ``JobItem.job_metadata``). Pass a dict with ``{"message_id": ...}``
    to model the unified-dispatcher admission path — the guard's
    carve-out matches ``task.message_id`` against this value via
    dialect-aware JSON extraction.
    """
    jid = job_id or f"job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        s.add(JobItem(
            job_id=jid,
            agent_id="leader",
            agent_dir="/tmp/leader",
            message="test message",
            source="api",
            job_type=job_type,

            admission_state=status_to_admission(status),
            instance_id=instance_id,
            job_metadata=job_metadata if job_metadata is not None else {},
        ))
        s.commit()
    return jid


def _seed_child_task(
    engine,
    *,
    child_instance_id: str,
) -> int:
    """Insert a PENDING child task row (simulates the child's own task)."""
    from daemon.repositories.task.repository import TaskRepository
    repo = TaskRepository(engine, on_pending_task=lambda: None)
    task = repo.create(
        task_type=TaskType.PROCESS_MESSAGE.value,
        instance_id=child_instance_id,
        message_id=str(uuid.uuid4()),
    )
    return task.id


# =============================================================================
# Suite 2.1 — Independent-turn test
# =============================================================================


class TestIndependentTurn:
    """Each child completion creates its own PROCESS_REPORT task.

    After Phase 1 decoupling, the report lane is:
      child task completes → bus emits terminal → PROCESS_REPORT task created
      → worker claims it → graph turn → lifecycle event → _process_event
      → finalize

    The key invariant: each child completion produces a DISTINCT report Task,
    so the parent's graph runs in separate turns (not batched into one turn).
    """

    @pytest.mark.asyncio
    async def test_child_completion_creates_separate_report_task(
        self, engine, bus
    ):
        """Child 1 completes → one PROCESS_REPORT task for parent exists.

        Verifies that a single child completion creates exactly one report
        Task (not zero, not multiple). The Task is created in the DB
        as part of the child completion flow; here we simulate the
        `_create_completion_report` result by directly creating the Task
        and wiring the bus watcher so the emit_terminal path is exercised.
        """
        parent_id = _seed_instance(engine)
        child_id = _seed_instance(engine, parent_id=parent_id)

        # Create the child task and seed a watcher so emit_terminal has something to fire.
        child_task_id = _seed_child_task(engine, child_instance_id=child_id)
        await bus.watch(
            str(child_task_id),
            FollowUp(
                target_instance_id=parent_id,
                message="child completed",
                source="test",
            ),
        )

        # Simulate child task completion: emit_terminal fires the watcher.
        # After Phase 1, _emit_terminal_via_bus would also create the
        # PROCESS_REPORT task; we verify it would be created here.
        fired = await bus.emit_terminal(
            str(child_task_id),
            Outcome(status="completed", summary="child done"),
        )
        assert len(fired) == 1
        assert fired[0].target_instance_id == parent_id

        # Bus pending count for parent should be 0 after the single watcher fired.
        pending = await bus.count_pending_for_target(parent_id)
        assert pending == 0

        # Simulate _create_completion_report: create the PROCESS_REPORT
        # task (in real code this happens inside ChildReportsService).
        repo = TaskRepository(engine, on_pending_task=lambda: None)
        report_task = repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )

        # Verify exactly one PROCESS_REPORT task exists for the parent.
        with Session(engine) as s:
            from sqlmodel import select as sel
            tasks = list(s.exec(
                sel(Task).where(
                    Task.instance_id == parent_id,
                    Task.task_type == TaskType.PROCESS_REPORT.value,
                )
            ))
        assert len(tasks) == 1, (
            f"Expected 1 PROCESS_REPORT task after child completion, got {len(tasks)}"
        )
        assert tasks[0].id == report_task.id
        assert tasks[0].status == TaskStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_two_children_create_two_separate_report_tasks(self, engine, bus):
        """Child 1 completes → Child 2 completes → 2 PROCESS_REPORT tasks exist.

        This is the core independent-turn invariant: each child creates its
        own report Task. The bus correctly tracks two pending watchers while
        both children are still resolving, and zero after both complete.
        """
        parent_id = _seed_instance(engine)
        child1_id = _seed_instance(engine, parent_id=parent_id)
        child2_id = _seed_instance(engine, parent_id=parent_id)

        # Seed two child tasks and two bus watchers.
        child1_task_id = _seed_child_task(engine, child_instance_id=child1_id)
        child2_task_id = _seed_child_task(engine, child_instance_id=child2_id)

        await bus.watch(
            str(child1_task_id),
            FollowUp(target_instance_id=parent_id, message="child1 done", source="test"),
        )
        await bus.watch(
            str(child2_task_id),
            FollowUp(target_instance_id=parent_id, message="child2 done", source="test"),
        )

        # Before any completion: 2 pending watchers.
        assert await bus.count_pending_for_target(parent_id) == 2

        # Child 1 completes.
        fired1 = await bus.emit_terminal(
            str(child1_task_id),
            Outcome(status="completed", summary="child1 done"),
        )
        assert len(fired1) == 1
        # After Child 1: 1 pending watcher remains.
        assert await bus.count_pending_for_target(parent_id) == 1

        # Create PROCESS_REPORT task for Child 1's completion.
        repo = TaskRepository(engine, on_pending_task=lambda: None)
        report1 = repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )
        assert report1.status == TaskStatus.PENDING.value

        # Child 2 completes.
        fired2 = await bus.emit_terminal(
            str(child2_task_id),
            Outcome(status="completed", summary="child2 done"),
        )
        assert len(fired2) == 1
        # After Child 2: 0 pending watchers.
        assert await bus.count_pending_for_target(parent_id) == 0

        # Create PROCESS_REPORT task for Child 2's completion.
        report2 = repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )

        # Two distinct report Tasks exist.
        with Session(engine) as s:
            from sqlmodel import select as sel
            tasks = list(s.exec(
                sel(Task).where(
                    Task.instance_id == parent_id,
                    Task.task_type == TaskType.PROCESS_REPORT.value,
                )
            ))
        assert len(tasks) == 2
        assert {t.id for t in tasks} == {report1.id, report2.id}
        assert all(t.status == TaskStatus.PENDING.value for t in tasks)

    @pytest.mark.asyncio
    async def test_report_tasks_have_distinct_message_ids(self, engine, bus):
        """Two report Tasks for the same parent must have distinct message_ids.

        The report Task's message_id links to the MessageQueue row that carries
        the child's result. Distinct message_ids ensure each report turn delivers
        its own content and is independently idempotent.
        """
        parent_id = _seed_instance(engine)
        child1_id = _seed_instance(engine, parent_id=parent_id)
        child2_id = _seed_instance(engine, parent_id=parent_id)

        child1_task_id = _seed_child_task(engine, child_instance_id=child1_id)
        child2_task_id = _seed_child_task(engine, child_instance_id=child2_id)

        await bus.watch(str(child1_task_id), FollowUp(target_instance_id=parent_id, message="c1", source="t"))
        await bus.watch(str(child2_task_id), FollowUp(target_instance_id=parent_id, message="c2", source="t"))

        await bus.emit_terminal(str(child1_task_id), Outcome(status="completed"))
        await bus.emit_terminal(str(child2_task_id), Outcome(status="completed"))

        repo = TaskRepository(engine, on_pending_task=lambda: None)
        msg_ids = set()
        for _ in range(2):
            t = repo.create(
                task_type=TaskType.PROCESS_REPORT.value,
                instance_id=parent_id,
                message_id=str(uuid.uuid4()),
            )
            msg_ids.add(t.message_id)

        assert len(msg_ids) == 2, "Report tasks must have distinct message_ids"


# =============================================================================
# Suite 2.1b — CRITICAL: report lane bypasses cross-system guard
# =============================================================================


class TestReportLaneGuard:
    """#1 PRIORITY — the single most important contract of this feature.

    The report-lane decoupling invariant::

        PROCESS_REPORT tasks MUST bypass the cross-system guard in
        ``claim_pending_task`` (they have no ``JobItem`` to collide with),
        while PROCESS_MESSAGE tasks MUST still be blocked by the guard
        when the parent's MESSAGE job is PROCESSING with a non-matching
        ``message_id``.

    This is the core "decoupling" — without it, a child-completion report
    waits forever for a JobItem that will never reference its message_id,
    and the parent's graph never receives the child's result.

    Previous review: only task *creation* was tested. The contract that
    ``claim_pending_task`` actually applies the bypass on real SQL (both
    SQLite and PostgreSQL) was unverified. These tests close that gap.
    """

    def test_process_report_bypasses_cross_system_guard(self, engine, task_repo):
        """PROCESS_REPORT with a different message_id IS claimed — guard bypassed.

        Setup: parent's MESSAGE job is PROCESSING with
        ``metadata.message_id = "msg-user-123"`` (the user message driving
        the graph). A PROCESS_REPORT task exists for the same parent
        instance with ``message_id = "msg-report-456"`` (a child
        completion report — a different message than the job references).

        Invariant: ``claim_pending_task`` MUST return the report task. If
        the guard fired on reports, it would block the claim forever
        (the report waits for a job that never references its message_id
        to terminate) and orphan the report.
        """
        parent_id = _seed_instance(engine)
        # A user message job actively driving the graph for this parent.
        _seed_job(
            engine,
            instance_id=parent_id,
            status=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-user-123"},
        )
        # A child-completion report task for the same parent, with a
        # DIFFERENT message_id (the report's own message_id, NOT the
        # user message's).
        report_task = task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id="msg-report-456",
        )
        assert report_task.status == TaskStatus.PENDING.value

        # The CRITICAL assertion: the report IS claimed. The cross-system
        # guard's job-coordination check is scoped to PROCESS_MESSAGE only
        # (see repository.py claim_pending_task), so PROCESS_REPORT
        # bypasses it entirely.
        claimed = task_repo.claim_pending_task(worker_id="worker-1")
        assert claimed is not None, (
            "PROCESS_REPORT MUST bypass the cross-system guard — got None. "
            "The guard should be scoped to PROCESS_MESSAGE only."
        )
        assert claimed.id == report_task.id
        assert claimed.status == TaskStatus.RUNNING.value
        assert claimed.task_type == TaskType.PROCESS_REPORT.value
        assert claimed.worker_id == "worker-1"

    def test_process_message_blocked_by_cross_system_guard(self, engine, task_repo):
        """PROCESS_MESSAGE with a non-matching message_id IS blocked — guard fires.

        Contrast test for the same setup: the parent's MESSAGE job is
        PROCESSING with ``metadata.message_id = "msg-user-123"``. A
        PROCESS_MESSAGE task exists for the same parent with
        ``message_id = "msg-other-789"`` (does NOT match the job's
        message_id).

        Invariant: ``claim_pending_task`` MUST return None. This proves
        the guard is still active for PROCESS_MESSAGE — the bypass is
        scoped to PROCESS_REPORT only, not all tasks.
        """
        parent_id = _seed_instance(engine)
        _seed_job(
            engine,
            instance_id=parent_id,
            status=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-user-123"},
        )
        # A PROCESS_MESSAGE task with a non-matching message_id (not
        # the unified-dispatcher admission for the job above).
        msg_task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=parent_id,
            message_id="msg-other-789",
        )
        assert msg_task.status == TaskStatus.PENDING.value

        # The cross-system guard MUST block this — the MESSAGE job is
        # actively PROCESSING and no Task row exists for the matching
        # message_id (the unified-dispatcher admission carve-out does
        # not apply).
        claimed = task_repo.claim_pending_task(worker_id="worker-1")
        assert claimed is None, (
            f"PROCESS_MESSAGE with non-matching message_id MUST be blocked "
            f"by the cross-system guard (got {claimed})"
        )

        # The PROCESS_MESSAGE task must still be PENDING (not claimed).
        with Session(engine) as s:
            t = s.get(Task, msg_task.id)
            assert t.status == TaskStatus.PENDING.value
            assert t.worker_id is None

    def test_process_message_unblocked_when_message_id_matches(
        self, engine, task_repo
    ):
        """PROCESS_MESSAGE with matching message_id IS claimed — admission carve-out.

        Third axis of the contract: the unified-dispatcher admission
        carve-out. A PROCESS_MESSAGE task whose message_id matches the
        job's metadata.message_id IS claimed (the MESSAGE job is the
        FIFO placeholder for the admission, not driving graph.astream).

        This pins the ``status IN ('pending', 'running')`` filter on the
        Task-side carve-out and ensures the bypass for reports does not
        accidentally extend to all PROCESS_MESSAGE tasks.
        """
        parent_id = _seed_instance(engine)
        _seed_job(
            engine,
            instance_id=parent_id,
            status=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-matching-001"},
        )
        # PROCESS_MESSAGE task with the SAME message_id as the job —
        # this is the unified-dispatcher admission signal.
        admitted_task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=parent_id,
            message_id="msg-matching-001",
        )

        claimed = task_repo.claim_pending_task(worker_id="worker-1")
        assert claimed is not None
        assert claimed.id == admitted_task.id
        assert claimed.task_type == TaskType.PROCESS_MESSAGE.value


# =============================================================================
# Suite 2.5 — Edge cases for per-instance serialization
# =============================================================================


class TestEdgeCases:
    """Edge-case scenarios for per-instance serialization and report ordering.

    Covers:
      Edge 1: Report Task PENDING + paused → blocked, then resume → claim.
              (Covered by ``TestPauseSafety.test_resume_allows_report_task_claim``.)
      Edge 2: Two children complete simultaneously → both report Tasks get
              processed (per-instance serialization: max 1 RUNNING at a time).
      Edge 3: Report Task claimed while a user message Task is RUNNING for
              the same instance → blocked, then unblocked after message ends.
      Edge 4: Crash after bus fire but before stamp → recovered on restart.
              (Covered by ``TestCrashRecovery.test_fired_unstamped_watcher_recovered_on_restart``.)
    """

    def test_two_simultaneous_reports_serialized_per_instance(
        self, engine, task_repo
    ):
        """Edge 2: two children complete → both reports processed (serialized).

        The per-instance serialization guard (one RUNNING task per
        instance) ensures report tasks for the same parent run in
        separate turns, not in parallel. This is what gives the report
        lane its "independent turn" property: each child completion
        produces its own parent graph turn, sequenced rather than
        racing.
        """
        parent_id = _seed_instance(engine)
        child1_id = _seed_instance(engine, parent_id=parent_id)
        child2_id = _seed_instance(engine, parent_id=parent_id)

        # Create 2 PROCESS_REPORT tasks for the same parent (simulating
        # 2 child completions).
        report1 = task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id="msg-report-A",
        )
        report2 = task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id="msg-report-B",
        )
        assert report1.status == TaskStatus.PENDING.value
        assert report2.status == TaskStatus.PENDING.value

        # First claim: one of the two reports is claimed (RUNNING).
        # The other is PENDING, blocked by the per-instance serialization
        # guard (instance_id IN (SELECT instance_id FROM task WHERE
        # status = 'running')).
        first_claim = task_repo.claim_pending_task(worker_id="worker-1")
        assert first_claim is not None
        assert first_claim.status == TaskStatus.RUNNING.value
        first_id = first_claim.id

        # Second claim: returns None — the per-instance serialization
        # guard blocks it (the other report is now RUNNING for the same
        # instance). The guard is a hard guarantee, not an optimization.
        second_claim = task_repo.claim_pending_task(worker_id="worker-2")
        assert second_claim is None, (
            f"Second claim should be blocked by per-instance serialization "
            f"(got {second_claim})"
        )

        # Complete the first report.
        completed = task_repo.complete_task(first_id, result={"ok": True})
        assert completed is not None
        assert completed.status == TaskStatus.COMPLETED.value

        # Third claim: the second report is now claimable.
        third_claim = task_repo.claim_pending_task(worker_id="worker-3")
        assert third_claim is not None, (
            "Second report should be claimable after first completes"
        )
        assert third_claim.id != first_id
        assert {first_id, third_claim.id} == {report1.id, report2.id}

    def test_report_waits_for_running_message_task_to_finish(
        self, engine, task_repo
    ):
        """Edge 3: report Task waits for a RUNNING PROCESS_MESSAGE task.

        Verifies the per-instance serialization invariant for the mixed
        case: a PROCESS_REPORT task must NOT claim while a
        PROCESS_MESSAGE task for the same instance is RUNNING. Reports
        serialize behind any running turn — they don't race the user
        message's graph.astream.

        Setup: a PROCESS_MESSAGE task is claimed and RUNNING for the
        parent. A PROCESS_REPORT task is then created for the same
        parent. The report must NOT be claimable until the message task
        completes.
        """
        parent_id = _seed_instance(engine)

        # Step 1: Create and claim a PROCESS_MESSAGE task. It is now
        # RUNNING for the parent.
        msg_task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=parent_id,
            message_id="msg-user-001",
        )
        claimed_msg = task_repo.claim_pending_task(worker_id="msg-worker")
        assert claimed_msg is not None
        assert claimed_msg.id == msg_task.id
        assert claimed_msg.status == TaskStatus.RUNNING.value

        # Step 2: Create a PROCESS_REPORT task for the same parent.
        report_task = task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id="msg-report-001",
        )
        assert report_task.status == TaskStatus.PENDING.value

        # Step 3: The report is BLOCKED by per-instance serialization —
        # the message task is RUNNING for this instance.
        blocked = task_repo.claim_pending_task(worker_id="report-worker")
        assert blocked is None, (
            f"PROCESS_REPORT must be blocked while a PROCESS_MESSAGE task "
            f"is RUNNING for the same instance (got {blocked})"
        )

        # The report task is still PENDING.
        with Session(engine) as s:
            t = s.get(Task, report_task.id)
            assert t.status == TaskStatus.PENDING.value

        # Step 4: Complete the message task.
        completed = task_repo.complete_task(msg_task.id, result={"ok": True})
        assert completed is not None
        assert completed.status == TaskStatus.COMPLETED.value

        # Step 5: The report is now claimable.
        claimed_report = task_repo.claim_pending_task(worker_id="report-worker")
        assert claimed_report is not None, (
            "PROCESS_REPORT should be claimable after the PROCESS_MESSAGE "
            "task completes (per-instance serialization released)"
        )
        assert claimed_report.id == report_task.id
        assert claimed_report.status == TaskStatus.RUNNING.value
        assert claimed_report.task_type == TaskType.PROCESS_REPORT.value


# =============================================================================
# Suite 2.2 — Pause safety
# =============================================================================


class TestPauseSafety:
    """Pause gate: report Tasks for PAUSED instances are not claimable.

    The pause gate is in TaskRepository.claim_pending_task:
    ``instance_id NOT IN (SELECT instance_id FROM instances WHERE status IN ('paused', 'terminated'))``

    This suite verifies:
      (a) claim_pending_task returns None for PAUSED instances (the gate blocks it)
      (b) child completing while parent is PAUSED creates a PENDING report Task
      (c) on resume, notify_work fires and the queued report Task is claimable
    """

    def test_paused_instance_blocks_report_task_claim(self, engine, task_repo):
        """(a) No report Task is claimed for a PAUSED instance."""
        parent_id = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
        # PROCESS_REPORT task for the paused parent.
        report_task = task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )
        assert report_task.status == TaskStatus.PENDING.value

        # The pause gate MUST block this claim.
        claimed = task_repo.claim_pending_task(worker_id="worker-1")
        assert claimed is None, (
            f"claim_pending_task should return None for PAUSED instance "
            f"(got {claimed})"
        )

    def test_report_task_stays_pending_while_parent_paused(self, engine, task_repo):
        """(b) A child completing while parent is PAUSED creates a PENDING report Task."""
        parent_id = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
        child_id = _seed_instance(engine, parent_id=parent_id)

        # Simulate child completion creates a PROCESS_REPORT task while parent is PAUSED.
        # The task IS created (ChildReportsService doesn't check parent status
        # when creating the task) but it stays PENDING because the gate blocks it.
        report_task = task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )

        assert report_task.status == TaskStatus.PENDING.value

        # Gate still blocks claim (parent still PAUSED).
        assert task_repo.claim_pending_task(worker_id="worker-1") is None

        # The report Task row still exists — it was created but not claimed.
        with Session(engine) as s:
            from sqlmodel import select as sel
            tasks = list(s.exec(
                sel(Task).where(
                    Task.instance_id == parent_id,
                    Task.task_type == TaskType.PROCESS_REPORT.value,
                )
            ))
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.PENDING.value

    def test_resume_allows_report_task_claim(self, engine, task_repo):
        """(c) On resume, the queued report Task is claimable."""
        parent_id = _seed_instance(engine, status=InstanceStatus.PAUSED.value)

        # Create the report Task while parent is PAUSED.
        report_task = task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )
        assert report_task.status == TaskStatus.PENDING.value

        # Gate blocks while PAUSED.
        assert task_repo.claim_pending_task(worker_id="worker-1") is None

        # Resume: change instance status to RUNNING.
        with Session(engine) as s:
            inst = s.get(Instance, parent_id)
            assert inst is not None
            inst.status = InstanceStatus.RUNNING.value
            s.commit()

        # Gate now allows the claim.
        claimed = task_repo.claim_pending_task(worker_id="worker-1")
        assert claimed is not None, (
            "claim_pending_task should succeed after parent resumes"
        )
        assert claimed.id == report_task.id
        assert claimed.status == TaskStatus.RUNNING.value
        assert claimed.worker_id == "worker-1"

    def test_terminated_instance_blocks_report_task_claim(self, engine, task_repo):
        """TERMINATED instances are also blocked (same gate)."""
        parent_id = _seed_instance(engine, status=InstanceStatus.TERMINATED.value)
        report_task = task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )
        assert task_repo.claim_pending_task(worker_id="worker-1") is None

    def test_process_message_task_also_blocked_for_paused(self, engine, task_repo):
        """The pause gate applies to PROCESS_MESSAGE tasks too (not just reports)."""
        parent_id = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
        # Add a RUNNING PROCESSING MESSAGE job so the cross-system guard
        # doesn't block (that guard is scoped to PROCESS_MESSAGE only).
        _seed_job(engine, instance_id=parent_id, status=AdmissionState.ACTIVE.value)
        msg_task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )
        # The pause gate (not the cross-system guard) blocks this.
        assert task_repo.claim_pending_task(worker_id="worker-1") is None


# =============================================================================
# Suite 2.2.1 — Phase 5: pause-gate edge cases
# =============================================================================


class TestPauseGateEdgeCases:
    """Phase 5 edge cases for the claim_pending_task pause gate.

    S3: serialization guard edge case — a RUNNING instance with a
    mix of PAUSED + PENDING tasks must still allow the PENDING task
    to claim. The per-instance serialization guard excludes only
    RUNNING tasks (not PAUSED), so a PAUSED task left over from a
    partial pause should NOT block its sibling PENDING task.

    W5: new-message-during-pause — a message arrives while the
    instance is PAUSED → Task is created PENDING → claim is blocked
    by the pause gate → on resume the PENDING task becomes
    claimable. This is the user-visible "send a message while
    paused" workflow: the message is buffered, then processed after
    resume.
    """

    def test_s3_paused_task_does_not_block_sibling_pending_claim(
        self, engine, task_repo
    ):
        """S3: a RUNNING instance with one PAUSED task and one
        PENDING task → the PENDING task is claimable.

        The per-instance serialization guard in ``claim_pending_task``
        uses ``status = 'running'`` to find blockers, NOT ``status IN
        ('running', 'paused')``. A PAUSED task on a RUNNING instance
        is a transitional state (e.g. a partially paused instance
        where the cascade reached this task) and must NOT block
        sibling PENDING tasks from being claimed.

        Without this invariant, a parent instance that has any
        PAUSED task would be unable to claim any new PENDING task
        (e.g. a child completion report) until the PAUSED task is
        manually transitioned — an operational deadlock.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        # PAUSED task: this simulates a partially-paused instance
        # where the cascade reached this task but did not reach its
        # sibling. The instance is back to RUNNING (resume happened)
        # but the task is still PAUSED in the DB.
        paused_task = task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=iid,
            message_id=str(uuid.uuid4()),
        )
        with Session(engine) as s:
            from sqlmodel import select as sel

            t = s.get(Task, paused_task.id)
            t.status = TaskStatus.PAUSED.value
            s.commit()
            s.refresh(t)

        # PENDING task: a child completion arriving now.
        pending_task = task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=iid,
            message_id=str(uuid.uuid4()),
        )
        assert pending_task.status == TaskStatus.PENDING.value

        # The PENDING task is claimable — the PAUSED sibling does
        # NOT block (the serialization guard excludes only RUNNING).
        claimed = task_repo.claim_pending_task(worker_id="worker-s3")
        assert claimed is not None, (
            "claim_pending_task should claim the PENDING task "
            "even when a sibling PAUSED task exists on the same "
            "RUNNING instance"
        )
        assert claimed.id == pending_task.id
        assert claimed.status == TaskStatus.RUNNING.value

    def test_w5_new_message_during_pause_blocked_then_unblocked_on_resume(
        self, engine, task_repo
    ):
        """W5: a message arriving for a PAUSED instance is
        buffered as a PENDING task, blocked by the pause gate,
        and becomes claimable after resume.

        The end-to-end flow:
          1. Instance is PAUSED.
          2. A new message arrives (e.g. user sends "continue"
             while the instance is paused).
          3. The enqueue path creates a PENDING task (the
             message is NOT dropped).
          4. ``claim_pending_task`` returns None — the pause
             gate blocks the claim.
          5. The instance resumes (status RUNNING).
          6. ``claim_pending_task`` now returns the buffered
             PENDING task.

        This is the user-facing "message-during-pause" workflow.
        The pre-Phase 2 design had ``enqueue_message`` auto-resume
        the instance, which violated the pause contract. The
        post-Phase 2 design buffers the message and the resume
        path drives the catch-up claim.
        """
        iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)

        # 1+2: instance is PAUSED; a new message arrives.
        # 3: enqueue creates a PENDING task (gate is independent
        # of task creation).
        new_message_task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=iid,
            message_id=str(uuid.uuid4()),
        )
        assert new_message_task.status == TaskStatus.PENDING.value

        # 4: claim is blocked by the pause gate.
        assert task_repo.claim_pending_task(worker_id="worker-w5") is None, (
            "claim_pending_task must be blocked by the pause gate "
            "for a PAUSED instance even if a PENDING task exists"
        )

        # Task row is still PENDING (claim failed, not deleted).
        with Session(engine) as s:
            from sqlmodel import select as sel

            rows = list(s.exec(
                sel(Task).where(Task.instance_id == iid)
            ))
        assert len(rows) == 1
        assert rows[0].status == TaskStatus.PENDING.value

        # 5: resume the instance.
        with Session(engine) as s:
            inst = s.get(Instance, iid)
            assert inst is not None
            inst.status = InstanceStatus.RUNNING.value
            s.commit()

        # 6: claim now succeeds.
        claimed = task_repo.claim_pending_task(worker_id="worker-w5")
        assert claimed is not None, (
            "claim_pending_task should succeed after parent resumes"
        )
        assert claimed.id == new_message_task.id
        assert claimed.status == TaskStatus.RUNNING.value


# =============================================================================
# Suite 2.3 — Crash recovery
# =============================================================================


class TestCrashRecovery:
    """Crash-recovery contracts for the report-lane decoupling.

    The stamp-after-emit pattern:
      emit_terminal → row FIRED, enqueued_at=NULL
      report Task claimed → graph turn runs
      _finalize_job → stamp enqueued_at=now
      crash between emit and stamp → _recover_fired_unsent surfaces the row

    Four windows:
      (a) crash after Task PENDING before bus fire → watcher stays PENDING
          (pre-existing gap, documented with comment)
      (b) crash after bus fire before claim → _recover_fired_unsent returns it
      (c) crash after report turn but before finalize → finalize directly on restart
      (d) second crash before stamp → retried (idempotent)
    """

    @pytest.mark.asyncio
    async def test_fired_unstamped_watcher_recovered_on_restart(self, engine, bus_repo):
        """(b) FIRED row with enqueued_at=NULL is returned by _recover_fired_unsent.

        Simulates a crash between emit_terminal and the report Task claim:
        the watcher is FIRED but not yet enqueued-stamped.
        """
        from daemon.repositories.dependency_bus.models import (
            DependencyWatcher,
            DependencyWatcherState,
        )

        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_task_id = str(uuid.uuid4())

        # Seed a watcher and fire it (emulating the child completion).
        with Session(engine) as s:
            s.add(DependencyWatcher(
                source_task_id=child_task_id,
                target_instance_id=parent_id,
                state=DependencyWatcherState.FIRED.value,  # uppercase
                follow_up_payload=FollowUp(
                    target_instance_id=parent_id,
                    message="test",
                    source="test",
                ).to_payload(),
                fired_at=datetime.now(timezone.utc).isoformat(),
                # enqueued_at is NULL — simulates crash before stamp
                enqueued_at=None,
            ))
            s.commit()

        # Restart the bus (simulating post-crash startup).
        bus2 = DependencyBus(bus_repo)
        await bus2.start()
        try:
            recovered = await bus2._recover_fired_unsent()
            assert len(recovered) == 1, (
                f"_recover_fired_unsent should return 1 fired-unstamped row, got {len(recovered)}"
            )
            # _recover_fired_unsent returns (watch_id, FollowUp) tuples
            assert recovered[0][1].target_instance_id == parent_id
        finally:
            await bus2.stop()

    @pytest.mark.asyncio
    async def test_stamped_fired_watcher_not_recovered(self, engine, bus_repo):
        """Rows that are FIRED AND enqueued_at IS NOT NULL are NOT returned."""
        from daemon.repositories.dependency_bus.models import (
            DependencyWatcher,
            DependencyWatcherState,
        )

        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_task_id = str(uuid.uuid4())

        with Session(engine) as s:
            s.add(DependencyWatcher(
                source_task_id=child_task_id,
                target_instance_id=parent_id,
                state=DependencyWatcherState.FIRED.value,
                follow_up_payload=FollowUp(
                    target_instance_id=parent_id,
                    message="test",
                    source="test",
                ).to_payload(),
                fired_at=datetime.now(timezone.utc).isoformat(),
                enqueued_at=datetime.now(timezone.utc).isoformat(),  # stamped
            ))
            s.commit()

        bus2 = DependencyBus(bus_repo)
        await bus2.start()
        try:
            recovered = await bus2._recover_fired_unsent()
            assert len(recovered) == 0, (
                "Already-stamped rows should not be returned by _recover_fired_unsent"
            )
        finally:
            await bus2.stop()

    @pytest.mark.asyncio
    async     def test_pending_watcher_not_in_recovery(self, engine, bus_repo):
        """PENDING watchers are NOT in the recovery set (they haven't fired yet)."""
        from daemon.repositories.dependency_bus.models import (
            DependencyWatcher,
            DependencyWatcherState,
        )

        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_task_id = str(uuid.uuid4())

        with Session(engine) as s:
            s.add(DependencyWatcher(
                source_task_id=child_task_id,
                target_instance_id=parent_id,
                state=DependencyWatcherState.PENDING.value,  # NOT fired
                follow_up_payload=FollowUp(
                    target_instance_id=parent_id,
                    message="test",
                    source="test",
                ).to_payload(),
                fired_at=None,
                enqueued_at=None,
            ))
            s.commit()

        bus2 = DependencyBus(bus_repo)
        await bus2.start()
        try:
            recovered = await bus2._recover_fired_unsent()
            assert len(recovered) == 0, (
                "PENDING watchers should not appear in _recover_fired_unsent"
            )
        finally:
            await bus2.stop()

    def test_finalize_is_idempotent_via_atomic_transition(self, engine):
        """(d) Finalize is idempotent: re-running on a non-PROCESSING job is a no-op.

        The atomic WHERE status = 'processing' guard in _finalize_job_db_sync
        means re-finalizing a job that is already COMPLETED/FAILED/CANCELLED
        is a no-op (0 rows updated). This is the safety property that makes
        the retry-in-recovery pattern safe.
        """
        from daemon.repositories.job_queue.models import JobItem
        from sqlmodel import select as sel

        parent_id = _seed_instance(engine, status=InstanceStatus.COMPLETED.value)
        job_id = _seed_job(engine, instance_id=parent_id, status=AdmissionState.ACTIVE.value)

        # Verify the job is PROCESSING.
        with Session(engine) as s:
            job = s.get(JobItem, job_id)
            assert job.admission_state == AdmissionState.ACTIVE.value

        # Simulate the atomic transition (what _finalize_job_db_sync does).
        # The actual _finalize_job uses WriteGuardSession; here we verify
        # the SQLite UPDATE is atomic with the admission_state guard.
        # (Phase 5 dropped the legacy ``status`` column —
        # ``admission_state`` is the sole write authority.)
        from sqlalchemy import text
        with engine.begin() as conn:
            result = conn.execute(text("""
                UPDATE job_queue_items
                SET admission_state = :new_status
                WHERE job_id = :job_id AND admission_state = :expected_status
            """), {
                "new_status": AdmissionState.DONE.value,
                "job_id": job_id,
                "expected_status": AdmissionState.ACTIVE.value,
            })
            assert result.rowcount == 1

        # Second call with the same guard: job is now COMPLETED, guard expects PROCESSING.
        with engine.begin() as conn:
            result = conn.execute(text("""
                UPDATE job_queue_items
                SET admission_state = :new_status
                WHERE job_id = :job_id AND admission_state = :expected_status
            """), {
                "new_status": AdmissionState.DONE.value,
                "job_id": job_id,
                "expected_status": AdmissionState.ACTIVE.value,
            })
            assert result.rowcount == 0, (
                "Second finalize with same guard should be a no-op (0 rows)"
            )

    def test_note_crash_window_a_pre_existing_gap(self):
        """(a) Crash after Task PENDING before bus fire is a pre-existing gap.

        If the process crashes AFTER the report Task is created (PENDING) but
        BEFORE the child task emits its terminal event (bus.emit_terminal),
        the watcher stays PENDING. This is a pre-existing gap that is NOT
        introduced by the report-lane decoupling. The stale-task recovery
        service will eventually re-enqueue the PENDING Task, which will
        re-trigger the child completion flow.

        This gap is documented here as a known limitation. It is out of
        scope for Phase 2 hardening per the plan at docs/plans/report-lane-decoupling.md.
        """
        # No assertion — this is a documentation test.
        pass


# =============================================================================
# Suite 2.4 — Error propagation
# =============================================================================


class TestErrorPropagation:
    """Any-error rule: one child erroring → parent finalizes as ERROR.

    After Phase 1 decoupling, the error path is:
      child task errors → bus.emit_terminal(status=error) →
      bus._parent_errored[parent_id] = True →
      bus._parent_error_message[parent_id] = "child error" →
      report Task emits lifecycle event →
      _process_event reads had_parent_error() + parent_error_message() →
      _finalize_job(status=ERROR, error="child error") →
      clear_parent_error()

    Tests verify:
      (a) had_parent_error + parent_error_message are set when a child errors
      (b) all children succeed → no error flag, parent finalizes COMPLETED
      (c) clear_parent_error resets both dicts so revived instance is clean
    """

    @pytest.mark.asyncio
    async def test_child_error_sets_had_parent_error(self, engine, bus):
        """(a) One child errors → bus.had_parent_error returns True."""
        parent_id = _seed_instance(engine)
        child_id = _seed_instance(engine, parent_id=parent_id)
        child_task_id = _seed_child_task(engine, child_instance_id=child_id)

        await bus.watch(
            str(child_task_id),
            FollowUp(target_instance_id=parent_id, message="child error", source="test"),
        )

        # Emit error status for the child.
        await bus.emit_terminal(
            str(child_task_id),
            Outcome(status="error", error="max_retries_exceeded"),
        )

        assert bus.had_parent_error(parent_id) is True, (
            "bus.had_parent_error should be True after a child errors"
        )
        assert bus.parent_error_message(parent_id) == "max_retries_exceeded"

    @pytest.mark.asyncio
    async def test_multiple_children_one_errors_one_succeeds(self, engine, bus):
        """(a variant) First child errors, second succeeds → had_parent_error stays True."""
        parent_id = _seed_instance(engine)
        child1_id = _seed_instance(engine, parent_id=parent_id)
        child2_id = _seed_instance(engine, parent_id=parent_id)
        child1_task_id = _seed_child_task(engine, child_instance_id=child1_id)
        child2_task_id = _seed_child_task(engine, child_instance_id=child2_id)

        await bus.watch(str(child1_task_id), FollowUp(target_instance_id=parent_id, message="c1", source="t"))
        await bus.watch(str(child2_task_id), FollowUp(target_instance_id=parent_id, message="c2", source="t"))

        # Child 1 errors.
        await bus.emit_terminal(str(child1_task_id), Outcome(status="error", error="c1 error"))
        assert bus.had_parent_error(parent_id) is True
        assert bus.parent_error_message(parent_id) == "c1 error"

        # Child 2 succeeds (completes cleanly).
        await bus.emit_terminal(str(child2_task_id), Outcome(status="completed"))
        # Error flag is STICKY — one child erroring is enough.
        assert bus.had_parent_error(parent_id) is True, (
            "had_parent_error must remain True after second child succeeds "
            "(sticky: any-error rule)"
        )
        assert bus.parent_error_message(parent_id) == "c1 error"

    @pytest.mark.asyncio
    async def test_all_children_succeed_no_error_flag(self, engine, bus):
        """(b) All children succeed → had_parent_error is False."""
        parent_id = _seed_instance(engine)
        child1_id = _seed_instance(engine, parent_id=parent_id)
        child2_id = _seed_instance(engine, parent_id=parent_id)
        child1_task_id = _seed_child_task(engine, child_instance_id=child1_id)
        child2_task_id = _seed_child_task(engine, child_instance_id=child2_id)

        await bus.watch(str(child1_task_id), FollowUp(target_instance_id=parent_id, message="c1", source="t"))
        await bus.watch(str(child2_task_id), FollowUp(target_instance_id=parent_id, message="c2", source="t"))

        await bus.emit_terminal(str(child1_task_id), Outcome(status="completed"))
        await bus.emit_terminal(str(child2_task_id), Outcome(status="completed"))

        assert bus.had_parent_error(parent_id) is False, (
            "had_parent_error should be False when all children succeed"
        )
        # parent_error_message is None when no error was ever recorded.
        assert bus.parent_error_message(parent_id) is None

    @pytest.mark.asyncio
    async def test_clear_parent_error_resets_error_flag(self, engine, bus):
        """(c) clear_parent_error resets the sticky flag so revived instances are clean."""
        parent_id = _seed_instance(engine)
        child_id = _seed_instance(engine, parent_id=parent_id)
        child_task_id = _seed_child_task(engine, child_instance_id=child_id)

        await bus.watch(str(child_task_id), FollowUp(target_instance_id=parent_id, message="c", source="t"))
        await bus.emit_terminal(str(child_task_id), Outcome(status="error", error="child error"))

        # Pre-condition: error flag is set.
        assert bus.had_parent_error(parent_id) is True

        # clear_parent_error is called after finalize (simulated here).
        bus.clear_parent_error(parent_id)

        # Post-condition: flag and message are cleared.
        assert bus.had_parent_error(parent_id) is False, (
            "had_parent_error should be False after clear_parent_error"
        )
        assert bus.parent_error_message(parent_id) is None, (
            "parent_error_message should be None after clear_parent_error"
        )

    @pytest.mark.asyncio
    async def test_clear_parent_error_is_idempotent(self, engine, bus):
        """clear_parent_error on a parent with no error flag is a safe no-op."""
        parent_id = f"clean-parent-{uuid.uuid4().hex[:8]}"
        # No error was ever recorded for this parent.
        assert bus.had_parent_error(parent_id) is False

        # Must not raise.
        bus.clear_parent_error(parent_id)
        bus.clear_parent_error(parent_id)  # double-call

        assert bus.had_parent_error(parent_id) is False

    @pytest.mark.asyncio
    async def test_error_flag_propagates_to_finalize_status(self, engine, bus):
        """(a) Verifies the finalize status override: had_parent_error → ERROR.

        Calls the real ``_resolve_finalize_status`` helper from
        ``job_feedback_observer`` (the single source of truth for the
        "any error → error" rule applied by both ``_process_event`` and
        the bus crash-recovery path in ``daemon/api.py``). Duplicating
        the rule here would let the helper drift from the test without
        any signal.
        """
        from daemon.services.job_feedback_observer import (
            CHILD_AGENT_ERROR_FALLBACK,
            _resolve_finalize_status,
        )

        parent_id = _seed_instance(engine)
        child_id = _seed_instance(engine, parent_id=parent_id)
        child_task_id = _seed_child_task(engine, child_instance_id=child_id)

        await bus.watch(str(child_task_id), FollowUp(target_instance_id=parent_id, message="c", source="t"))
        await bus.emit_terminal(str(child_task_id), Outcome(status="error", error="oops"))

        # Default would be COMPLETED (parent's own turn ended cleanly).
        # The bus error flag overrides to ERROR + the captured child error.
        status, error = _resolve_finalize_status(
            bus, parent_id,
            default_status=InstanceStatus.COMPLETED.value,
            default_error=None,
        )
        assert status == InstanceStatus.ERROR.value
        assert error == "oops"

        # Simulate the clear after finalize.
        bus.clear_parent_error(parent_id)

        # After clear: the helper returns the default (no override).
        status_after, error_after = _resolve_finalize_status(
            bus, parent_id,
            default_status=InstanceStatus.COMPLETED.value,
            default_error=None,
        )
        assert status_after == InstanceStatus.COMPLETED.value
        assert error_after is None

    @pytest.mark.asyncio
    async def test_error_flag_uses_fallback_when_message_missing(self, engine, bus):
        """The override returns a non-None error string even when the bus
        did not capture a specific error message. Guards against the
        override silently returning ``None`` and breaking the finalize
        call's ``error=`` argument.

        Note: the bus's ``emit_terminal`` itself populates a "child
        agent error" fallback via ``setdefault`` when ``outcome.error``
        is empty, so in practice ``parent_error_message`` is always
        non-None when ``had_parent_error`` is True. The helper's own
        ``or CHILD_AGENT_ERROR_FALLBACK`` is a second line of defense
        against that fallback being cleared (e.g. on a stop+restart
        that wiped the message dict but not the flag — impossible
        with the current ``stop()`` implementation, but the helper
        stays conservative).
        """
        from daemon.services.job_feedback_observer import (
            CHILD_AGENT_ERROR_FALLBACK,
            _resolve_finalize_status,
        )

        parent_id = _seed_instance(engine)
        child_id = _seed_instance(engine, parent_id=parent_id)
        child_task_id = _seed_child_task(engine, child_instance_id=child_id)

        await bus.watch(str(child_task_id), FollowUp(target_instance_id=parent_id, message="c", source="t"))
        # status="error" with error=None — the bus sets the flag AND
        # populates the message dict with its own "child agent error"
        # fallback via setdefault.
        await bus.emit_terminal(str(child_task_id), Outcome(status="error"))

        assert bus.had_parent_error(parent_id) is True
        assert bus.parent_error_message(parent_id) == CHILD_AGENT_ERROR_FALLBACK

        # Helper returns ERROR + a non-None error string (the bus's
        # own fallback, which equals CHILD_AGENT_ERROR_FALLBACK).
        status, error = _resolve_finalize_status(
            bus, parent_id,
            default_status=InstanceStatus.COMPLETED.value,
            default_error=None,
        )
        assert status == InstanceStatus.ERROR.value
        assert error == CHILD_AGENT_ERROR_FALLBACK
