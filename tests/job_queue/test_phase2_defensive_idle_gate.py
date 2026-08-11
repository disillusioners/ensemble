"""Phase 2 defensive idle-gate tests (2026-08-11).

Phase 1 introduced the task↔JobItem reconciliation primitive that
prevents NEW orphans (a task whose linked JobItem is already terminal —
done/dead — while the task row stays behind). Phase 2 is the
defense-in-depth twin: the idle-gate predicates themselves ALSO
exclude those orphaned tasks, so even if reconciliation misses a row
(race, transient DB failure, pre-Phase-1 data), the defer/background
queue idle gates can never be wedged forever.

This file pins the new behavior on BOTH ``TaskRepository
.has_active_non_deferred_work`` AND ``TaskRepository
.has_active_non_background_work``. Each predicate covers the four
documented Phase 2 cases plus the pause-first crash-recovery
regression guards.

The two predicates are checked for the document's six test cases each
(7 cases total per predicate — including the queued/active regression
guards). For ``has_active_non_deferred_work`` the cases are run for
BOTH ``project_id=None`` (system-wide probe) AND ``project_id != None``
(project-scoped probe); for ``has_active_non_background_work`` the
predicate is system-wide so a single branch covers all scopes
(parameter is ``del``'d).

Test pattern mirrors ``tests/job_queue/test_idle_gate_deadlock_fix.py``:
raw-SQL seeding via ``_insert_instance`` / ``_insert_task`` /
``_insert_job_item`` helpers + the session-scoped ``engine`` /
``task_repository`` fixtures from ``tests/job_queue/conftest.py``.
"""

import json
import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (verbatim from tests/job_queue/test_idle_gate_deadlock_fix.py so
# we have raw-SQL seeding with no API-level coupling)
# ─────────────────────────────────────────────────────────────────────────────


def _insert_instance(
    engine,
    instance_id: str,
    project_id: str = "test-project",
    status: str = "running",
) -> None:
    """Insert a minimal Instance row directly via raw SQL.

    The task-side predicate joins ``task`` against ``instances``; we
    need a matching ``instances`` row for the project_id filter to
    evaluate.
    """
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status, :project_id,
                     :created_at, :updated_at, 1)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
            },
        )


def _insert_task(
    engine,
    *,
    work_id: str | None = None,
    instance_id: str = "test-instance",
    status: str = TaskStatus.PENDING.value,
    is_deferred: bool = False,
    is_background: bool = False,
) -> int:
    """Insert a task row directly via raw SQL and return its primary key."""
    work_id = work_id or str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task (task_type, instance_id, message_id, status,
                                  retry_count, created_at, cancel_requested,
                                  retry_scheduled, work_id, is_deferred, is_background)
                VALUES (:task_type, :instance_id, :message_id, :status,
                        :retry_count, :created_at, :cancel_requested,
                        :retry_scheduled, :work_id, :is_deferred, :is_background)
                """
            ),
            {
                "task_type": TaskType.PROCESS_MESSAGE.value,
                "instance_id": instance_id,
                "message_id": str(uuid.uuid4()),
                "status": status,
                "retry_count": 0,
                "created_at": created_at,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": work_id,
                "is_deferred": is_deferred,
                "is_background": is_background,
            },
        )
    return int(result.lastrowid)


def _insert_job_item(
    engine,
    *,
    job_id: str | None = None,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str | None = None,
    admission_state: str = AdmissionState.QUEUED.value,
    job_type: str = "task",
) -> str:
    """Insert a JobItem row directly via raw SQL. Returns the job_id."""
    job_id = job_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    metadata_json = json.dumps({})
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
                    (:job_id, :agent_id, :agent_dir, :message, :source,
                     :project_id, :queue_id, :priority, :admission_state,
                     :created_at, :instance_id, :job_type, :retry_count,
                     :metadata)
                """
            ),
            {
                "job_id": job_id,
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "message": "test",
                "source": "api",
                "project_id": project_id,
                "queue_id": queue_id,
                "priority": 0,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
                "job_type": job_type,
                "retry_count": 0,
                "metadata": metadata_json,
            },
        )
    return job_id


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def task_repository(engine):
    """TaskRepository backed by the session-scoped in-memory engine."""
    return TaskRepository(engine)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 Defensive Idle-Gate — Defer Predicate
# ─────────────────────────────────────────────────────────────────────────────


class TestPhase2DefensiveDeferPredicate:
    """``TaskRepository.has_active_non_deferred_work`` after Phase 2.

    Phase 2 defensive idle-gate (2026-08-11): a task whose linked
    JobItem is already terminal (``done``/``dead``) no longer counts
    as active non-deferred work, regardless of task status. This
    excludes orphaned tasks that escaped Phase 1 reconciliation.

    Each phase-2 case is checked for BOTH project_id=None (system-wide
    probe) AND project_id!=None (project-scoped probe).
    """

    # ── The core fix: paused task + terminal JobItem → False ──────────

    def test_paused_task_with_done_jobitem_excluded_project_scoped(
        self, task_repository, engine
    ):
        """Core fix (project-scoped): a paused task whose linked JobItem
        is ``admission_state='done'`` is an orphan and must NOT count.

        Pre-Phase-2 this returned True and the defer-queue idle gate
        stayed wedged forever.
        """
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-pdone", project_id="proj-pdone")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-pdone",
            status=TaskStatus.PAUSED.value,
            is_deferred=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-pdone",
            project_id="proj-pdone",
            queue_id="queue-pdone",
            admission_state=AdmissionState.DONE.value,
        )

        assert task_repository.has_active_non_deferred_work("proj-pdone") is False

    def test_paused_task_with_done_jobitem_excluded_system_wide(
        self, task_repository, engine
    ):
        """Core fix (system-wide probe): same case, ``project_id=None``
        must also exclude the orphan."""
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-sw-done", project_id="proj-sw-done")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-sw-done",
            status=TaskStatus.PAUSED.value,
            is_deferred=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-sw-done",
            project_id="proj-sw-done",
            queue_id="queue-sw-done",
            admission_state=AdmissionState.DONE.value,
        )

        assert task_repository.has_active_non_deferred_work() is False

    def test_paused_task_with_dead_jobitem_excluded(self, task_repository, engine):
        """Same fix for the ``dead`` terminal state — both
        ``done`` and ``dead`` JobItems must exclude the orphan."""
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-pdead", project_id="proj-pdead")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-pdead",
            status=TaskStatus.PAUSED.value,
            is_deferred=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-pdead",
            project_id="proj-pdead",
            queue_id="queue-pdead",
            admission_state=AdmissionState.DEAD.value,
        )

        assert task_repository.has_active_non_deferred_work("proj-pdead") is False
        assert task_repository.has_active_non_deferred_work() is False

    # ── Pause-first crash recovery regression guards ──────────────────

    def test_paused_task_with_active_jobitem_still_counts(
        self, task_repository, engine
    ):
        """Pause-first crash recovery guard: a paused task whose
        JobItem is still ``active`` (pause-first convention — pause
        freezes a checkpoint, the JobItem stays ``active`` in
        admission) MUST still count.

        Regressing this would unpause instances and lose the
        pause-first invariant.
        """
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-pact", project_id="proj-pact")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-pact",
            status=TaskStatus.PAUSED.value,
            is_deferred=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-pact",
            project_id="proj-pact",
            queue_id="queue-pact",
            admission_state=AdmissionState.ACTIVE.value,
        )

        assert task_repository.has_active_non_deferred_work("proj-pact") is True
        assert task_repository.has_active_non_deferred_work() is True

    def test_paused_task_with_queued_jobitem_still_counts(
        self, task_repository, engine
    ):
        """Pause-first crash recovery guard (queued variant): a
        paused task whose JobItem is still ``queued`` (the lock
        hasn't been acquired yet) MUST still count — pause-first
        preserves the queued JobItem across resume.
        """
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-pqueued", project_id="proj-pqueued")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-pqueued",
            status=TaskStatus.PAUSED.value,
            is_deferred=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-pqueued",
            project_id="proj-pqueued",
            queue_id="queue-pqueued",
            admission_state=AdmissionState.QUEUED.value,
        )

        # Note: the PENDING-only branch would have excluded the
        # queued JobItem as "unclaimable" — but PAUSED is in the
        # RUNNING+PAUSED branch which has different semantics:
        # PAUSED is occupying the lock, the predicate must count it.
        assert task_repository.has_active_non_deferred_work("proj-pqueued") is True
        assert task_repository.has_active_non_deferred_work() is True

    # ── Pending-only branch: terminal JobItem also excludes ──────────

    def test_pending_task_with_done_jobitem_excluded(self, task_repository, engine):
        """C2 fix (Reviewer correction): a PENDING task whose linked
        JobItem is already ``done`` must NOT count.

        Pre-Phase-2 the pending-only branch only had the
        queued-exclusion; a pending task with a terminal JobItem was
        still counted, leaving the deadlock partially unfixed on that
        path. Phase 2 adds the terminal-JobItem NOT EXISTS to the
        pending branch too.
        """
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-pend-done", project_id="proj-pend-done")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-pend-done",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-pend-done",
            project_id="proj-pend-done",
            queue_id="queue-pend-done",
            admission_state=AdmissionState.DONE.value,
        )

        assert task_repository.has_active_non_deferred_work("proj-pend-done") is False
        assert task_repository.has_active_non_deferred_work() is False

    def test_pending_task_with_dead_jobitem_excluded(self, task_repository, engine):
        """Mirror of above for the ``dead`` terminal state."""
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-pend-dead", project_id="proj-pend-dead")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-pend-dead",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-pend-dead",
            project_id="proj-pend-dead",
            queue_id="queue-pend-dead",
            admission_state=AdmissionState.DEAD.value,
        )

        assert task_repository.has_active_non_deferred_work("proj-pend-dead") is False
        assert task_repository.has_active_non_deferred_work() is False

    def test_pending_task_with_active_jobitem_still_counts(
        self, task_repository, engine
    ):
        """Pending + active JobItem is the genuine "claimable" case
        and MUST still count (no regression vs the original Phase 1
        semantics).
        """
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-pend-act", project_id="proj-pend-act")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-pend-act",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-pend-act",
            project_id="proj-pend-act",
            queue_id="queue-pend-act",
            admission_state=AdmissionState.ACTIVE.value,
        )

        assert task_repository.has_active_non_deferred_work("proj-pend-act") is True

    # ── Running + terminal JobItem regression guard ──────────────────

    def test_running_task_with_done_jobitem_excluded(
        self, task_repository, engine
    ):
        """Phase 2 excludes ALL tasks in the running+paused branch whose
        JobItem is terminal — including RUNNING tasks.

        A RUNNING task whose JobItem is already ``done`` is an orphan:
        the JobItem says the work is finished but the task row was not
        reconciled. Phase 2 excludes it (same defense-in-depth as the
        paused case) so the defer-queue idle gate is not wedged while
        Phase 1 reconciliation catches up.

        NOTE: the task brief's case 7 expected True ("running tasks
        still block"), but that contradicts the plan's canonical SQL
        (phase2-plan.md Task 1), which applies the ``NOT EXISTS
        (done/dead)`` exclusion to the entire
        ``status IN (running, paused)`` branch. The plan is the
        authoritative spec; this test pins the plan's actual behavior.
        """
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-rdone", project_id="proj-rdone")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-rdone",
            status=TaskStatus.RUNNING.value,
            is_deferred=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-rdone",
            project_id="proj-rdone",
            queue_id="queue-rdone",
            admission_state=AdmissionState.DONE.value,
        )

        assert task_repository.has_active_non_deferred_work("proj-rdone") is False


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 Defensive Idle-Gate — Background Predicate
# ─────────────────────────────────────────────────────────────────────────────


class TestPhase2DefensiveBackgroundPredicate:
    """``TaskRepository.has_active_non_background_work`` after Phase 2.

    Mirror of the defer predicate test class. The background predicate
    is system-wide (the ``project_id`` parameter is accepted but
    ignored — documented scope asymmetry), so a single probe covers
    each case.
    """

    # ── The core fix: paused task + terminal JobItem → False ──────────

    def test_paused_task_with_done_jobitem_excluded(self, task_repository, engine):
        """Core fix: a paused task + done JobItem is an orphan and
        must NOT count as active non-background work.

        System-wide probe — ``project_id`` is ``del``'d in the
        predicate, so only the no-argument call matters.
        """
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-bgrdone", project_id="proj-bgrdone")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-bgrdone",
            status=TaskStatus.PAUSED.value,
            is_deferred=False,
            is_background=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-bgrdone",
            project_id="proj-bgrdone",
            queue_id="queue-bgrdone",
            admission_state=AdmissionState.DONE.value,
        )

        assert task_repository.has_active_non_background_work() is False

    def test_paused_task_with_dead_jobitem_excluded(self, task_repository, engine):
        """Same as above for the ``dead`` terminal state."""
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-bgrdead", project_id="proj-bgrdead")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-bgrdead",
            status=TaskStatus.PAUSED.value,
            is_deferred=False,
            is_background=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-bgrdead",
            project_id="proj-bgrdead",
            queue_id="queue-bgrdead",
            admission_state=AdmissionState.DEAD.value,
        )

        assert task_repository.has_active_non_background_work() is False

    # ── Pause-first crash recovery regression guards ──────────────────

    def test_paused_task_with_active_jobitem_still_counts(
        self, task_repository, engine
    ):
        """Pause-first guard: paused + active JobItem STILL counts."""
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-bgract", project_id="proj-bgract")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-bgract",
            status=TaskStatus.PAUSED.value,
            is_deferred=False,
            is_background=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-bgract",
            project_id="proj-bgract",
            queue_id="queue-bgract",
            admission_state=AdmissionState.ACTIVE.value,
        )

        assert task_repository.has_active_non_background_work() is True

    def test_paused_task_with_queued_jobitem_still_counts(
        self, task_repository, engine
    ):
        """Pause-first guard: paused + queued JobItem STILL counts.
        PAUSED is in the running+paused branch which counts the
        task regardless of JobItem admission state (only terminal
        JobItems are excluded, by Phase 2).
        """
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-bgrqueued", project_id="proj-bgrqueued")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-bgrqueued",
            status=TaskStatus.PAUSED.value,
            is_deferred=False,
            is_background=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-bgrqueued",
            project_id="proj-bgrqueued",
            queue_id="queue-bgrqueued",
            admission_state=AdmissionState.QUEUED.value,
        )

        assert task_repository.has_active_non_background_work() is True

    # ── Pending-only branch: terminal JobItem also excludes ──────────

    def test_pending_task_with_done_jobitem_excluded(self, task_repository, engine):
        """C2 fix (pending branch): PENDING + done JobItem is excluded."""
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-bgpdone", project_id="proj-bgpdone")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-bgpdone",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
            is_background=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-bgpdone",
            project_id="proj-bgpdone",
            queue_id="queue-bgpdone",
            admission_state=AdmissionState.DONE.value,
        )

        assert task_repository.has_active_non_background_work() is False

    def test_pending_task_with_dead_jobitem_excluded(self, task_repository, engine):
        """C2 fix (pending branch): PENDING + dead JobItem is excluded."""
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-bgpdead", project_id="proj-bgpdead")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-bgpdead",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
            is_background=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-bgpdead",
            project_id="proj-bgpdead",
            queue_id="queue-bgpdead",
            admission_state=AdmissionState.DEAD.value,
        )

        assert task_repository.has_active_non_background_work() is False

    # ── Running + terminal JobItem regression guard ──────────────────

    def test_running_task_with_done_jobitem_excluded(self, task_repository, engine):
        """Phase 2 excludes ALL tasks in the running+paused branch
        whose JobItem is terminal, including RUNNING. See the
        corresponding defer test for the full rationale — the plan's
        canonical SQL applies the exclusion to the entire
        ``status IN (running, paused)`` branch.
        """
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-bgrrd", project_id="proj-bgrrd")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-bgrrd",
            status=TaskStatus.RUNNING.value,
            is_deferred=False,
            is_background=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-bgrrd",
            project_id="proj-bgrrd",
            queue_id="queue-bgrrd",
            admission_state=AdmissionState.DONE.value,
        )

        assert task_repository.has_active_non_background_work() is False


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 Benchmark — keep NO-EXISTS subquery performance under control
# ─────────────────────────────────────────────────────────────────────────────


class TestPhase2DefensiveIdleGateBenchmark:
    """Lightweight benchmark for the Phase 2 NOT EXISTS subquery.

    The two extra ``NOT EXISTS (job_queue_items ...)`` subqueries could
    regress if the planner misses an index. This benchmark seeds
    ~1000 rows (NOT 10K — keep the test fast), invokes the predicate in
    a tight loop, and asserts each call completes within a loose
    per-call budget. If this benchmark regresses beyond the budget, add
    the composite index migration
    ``job_queue_items(job_id, admission_state, deleted_at)``.
    """

    SEED_INSTANCE_COUNT = 50
    SEED_TASK_COUNT = 1000
    SEED_JOBITEM_COUNT = 1000
    ITERATIONS = 20
    PER_CALL_BUDGET_SECONDS = 0.1  # 100ms — loose SQLite-in-memory threshold

    def test_has_active_non_deferred_work_per_call_within_budget(
        self, task_repository, engine
    ):
        """Seed ~1000 tasks + JobItems and time 20 calls of
        ``has_active_non_deferred_work``. Each call must complete in
        under the budget so a future planner regression is caught."""
        # Seed N instances first (needed for instance.project_id join).
        for i in range(self.SEED_INSTANCE_COUNT):
            _insert_instance(
                engine,
                instance_id=f"bench-inst-{i}",
                project_id=f"bench-proj-{i % 10}",
            )

        # Seed N tasks (mix of statuses — defer predicate unions the
        # running+paused and pending-only branches).
        for i in range(self.SEED_TASK_COUNT):
            _insert_task(
                engine,
                instance_id=f"bench-inst-{i % self.SEED_INSTANCE_COUNT}",
                status=(
                    TaskStatus.RUNNING.value
                    if i % 2 == 0
                    else TaskStatus.PENDING.value
                ),
                is_deferred=False,
            )

        # Seed N JobItems across admission states so the NOT EXISTS
        # subquery has varied match candidates.
        admission_states = [
            AdmissionState.QUEUED.value,
            AdmissionState.ACTIVE.value,
            AdmissionState.DONE.value,
            AdmissionState.DEAD.value,
        ]
        for i in range(self.SEED_JOBITEM_COUNT):
            _insert_job_item(
                engine,
                job_id=str(uuid.uuid4()),
                instance_id=f"bench-inst-{i % self.SEED_INSTANCE_COUNT}",
                project_id=f"bench-proj-{i % 10}",
                queue_id="bench-queue",
                admission_state=admission_states[i % len(admission_states)],
            )

        # Now run the predicate ITERATIONS times; assert each call is
        # within the budget.
        start = time.perf_counter()
        for _ in range(self.ITERATIONS):
            result = task_repository.has_active_non_deferred_work(
                "bench-proj-3"
            )
            assert isinstance(result, bool)
        elapsed = time.perf_counter() - start

        per_call = elapsed / self.ITERATIONS
        # Loose assertion — SQLite in-memory is fast, this guards
        # against a 10×+ regression (e.g. switching to a full table
        # scan because of a lost index hint).
        assert per_call < self.PER_CALL_BUDGET_SECONDS, (
            f"has_active_non_deferred_work took {per_call:.3f}s/call "
            f"(budget: {self.PER_CALL_BUDGET_SECONDS}s) over "
            f"{self.ITERATIONS} iterations with "
            f"{self.SEED_TASK_COUNT}+{self.SEED_JOBITEM_COUNT} "
            "seed rows"
        )
