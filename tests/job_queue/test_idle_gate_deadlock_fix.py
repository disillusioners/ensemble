"""Regression tests for the idle-gate deadlock fix (2026-08-10).

Two coupled bugs caused jobs on defer/background queues to deadlock
permanently. The full incident is in the commit message of
``c0306b86`` (Fix 1), ``158d97cf`` (Fix 2), and ``939dceb8`` (Fix 3).
This test file pins the new behavior in place so future refactors
cannot silently re-introduce either half of the deadlock cycle.

Four axes are covered:

* **TestFlagDerivationHelper** — ``_derive_task_flags_from_queue_type``
  unit tests against the module-level helper that backs Fix 1.

* **TestTaskSideDeferPredicateExclusion** — ``TaskRepository
  .has_active_non_deferred_work`` correctly excludes a PENDING task
  whose linked JobItem is still ``admission_state='queued'`` (the
  unclaimable case); preserves "claimable PENDING counts" semantics
  for direct-queue tasks (no JobItem) and for PENDING tasks whose
  JobItem is already ``active``.

* **TestTaskSideBackgroundPredicateExclusion** — mirror of the defer
  case for ``TaskRepository.has_active_non_background_work``.

* **TestJobSideBackgroundPredicateExclusion** — ``JobRepository
  .has_active_non_background_work`` LEFT JOINs ``task`` and excludes
  ``('queued' AND task.status='pending')`` JobItems (the deadlock
  case). Preserves the "queued Item with non-pending task still
  counts" semantics for the legitimate-waiting case.

Tests use the SQLite in-memory engine via the ``engine`` /
``task_repository`` fixtures from ``tests/job_queue/conftest.py``.
No real LLM, no daemon — pure unit/integration tests over the
repositories. The same SQL is portable to PostgreSQL (the
migration/column-ensure paths handle dialect differences).
"""

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from daemon.repositories.job_queue.models import AdmissionState, QueueType
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.instance_messaging import _derive_task_flags_from_queue_type


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (raw-SQL seeding; mirrors patterns in test_background_queue.py
# and test_defer_idle_gate_phase2.py)
# ─────────────────────────────────────────────────────────────────────────────


def _insert_instance(
    engine,
    instance_id: str,
    project_id: str = "test-project",
    status: str = "running",
) -> None:
    """Insert a minimal Instance row directly via raw SQL.

    The task-side predicate joins ``task`` against ``instances``; the
    job-side predicate joins ``job_queue_items`` against ``instances``
    via the ``status`` check. We need a matching ``instances`` row
    for both predicates to evaluate the project/status filters.
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
    """Insert a task row directly via raw SQL and return its primary key.

    The repository's public ``create`` API does not accept
    ``is_deferred`` / ``is_background``, so we insert directly. Returns
    the auto-incremented ``id`` so the test can correlate the task with
    other rows (e.g. JobItem rows via ``work_id``).
    """
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
                # Python bool so the bind works on both SQLite
                # (INTEGER 0/1) and PostgreSQL (BOOLEAN false/true).
                "is_deferred": is_deferred,
                "is_background": is_background,
            },
        )
        return int(result.lastrowid)


def _insert_queue(
    engine,
    queue_id: str,
    project_id: str,
    queue_type: str = "parallel",
    queue_name: str | None = None,
    concurrency_limit: int = 1,
) -> None:
    """Insert a JobQueue row directly via raw SQL.

    Mirrors the helper in ``test_defer_idle_gate_phase2.py`` and
    ``test_background_queue.py``. Required for any test that needs
    the predicate's LEFT JOIN on ``job_queues`` to match a row with a
    real ``queue_type``.
    """
    name = queue_name or queue_id
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_queues
                    (queue_id, project_id, queue_name, queue_name_lower,
                     queue_type, concurrency_limit, is_system, is_paused,
                     description, created_at, updated_at)
                VALUES
                    (:queue_id, :project_id, :queue_name, :queue_name_lower,
                     :queue_type, :concurrency_limit, 0, 0,
                     NULL, :created_at, :updated_at)
                """
            ),
            {
                "queue_id": queue_id,
                "project_id": project_id,
                "queue_name": name,
                "queue_name_lower": name.lower(),
                "queue_type": queue_type,
                "concurrency_limit": concurrency_limit,
                "created_at": now,
                "updated_at": now,
            },
        )


def _insert_job_item(
    engine,
    *,
    job_id: str | None = None,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str | None = None,
    admission_state: str = AdmissionState.QUEUED.value,
    job_type: str = "task",
) -> None:
    """Insert a JobItem row directly via raw SQL.

    The ``job_id`` here is the JobItem's UUID4 — it matches the
    corresponding Task's ``work_id`` when the task is enqueued via
    the JobProcessor path (the linkage contract).
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def task_repository(engine):
    """TaskRepository backed by the session-scoped in-memory engine."""
    return TaskRepository(engine)


@pytest.fixture
def queue_repository(engine):
    """JobQueueRepository backed by the session-scoped engine."""
    return JobQueueRepository(engine)


@pytest.fixture
def job_repository(engine):
    """JobRepository backed by the session-scoped engine."""
    return JobRepository(engine)


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Flag-derivation helper (Fix 1)
# ─────────────────────────────────────────────────────────────────────────────


class TestFlagDerivationHelper:
    """Unit tests for ``_derive_task_flags_from_queue_type``.

    The helper is the single source of truth for the flag-derivation
    rule that backs Fix 1 in ``enqueue_message_job``. The four
    documented queue types and the ``None``-unresolved case are
    exhaustively covered. Tests assert the True/False pair is
    backend-invariant (no SQL involved — just pure Python).
    """

    def test_defer_queue_forces_is_deferred_true(self):
        """``queue_type='defer'`` ⇒ ``is_deferred=True`` regardless of caller."""
        # default
        assert _derive_task_flags_from_queue_type("defer") == (True, False)
        # caller wanted is_background=True too — preserved
        assert _derive_task_flags_from_queue_type(
            "defer", is_background=True
        ) == (True, True)
        # caller wanted is_deferred=True — already correct, no-op
        assert _derive_task_flags_from_queue_type(
            "defer", is_deferred=True, is_background=True
        ) == (True, True)

    def test_background_queue_forces_is_background_true(self):
        """``queue_type='background'`` ⇒ ``is_background=True`` regardless of caller."""
        # default
        assert _derive_task_flags_from_queue_type("background") == (False, True)
        # caller wanted is_deferred=True too — preserved
        assert _derive_task_flags_from_queue_type(
            "background", is_deferred=True
        ) == (True, True)

    def test_fifo_queue_preserves_caller_flags(self):
        """``queue_type='fifo'`` ⇒ caller flags pass through unchanged.

        The defer/background lanes are the only ones that mandate a
        flag. ``fifo`` (and ``parallel``) just pass through whatever
        the caller asked for so the helper is a no-op for normal
        queues.
        """
        assert _derive_task_flags_from_queue_type("fifo") == (False, False)
        assert _derive_task_flags_from_queue_type(
            "fifo", is_deferred=True
        ) == (True, False)
        assert _derive_task_flags_from_queue_type(
            "fifo", is_background=True
        ) == (False, True)

    def test_parallel_queue_preserves_caller_flags(self):
        """``queue_type='parallel'`` ⇒ caller flags pass through (no-op)."""
        assert _derive_task_flags_from_queue_type("parallel") == (False, False)
        assert _derive_task_flags_from_queue_type(
            "parallel", is_deferred=True, is_background=True
        ) == (True, True)

    def test_none_queue_type_falls_through(self):
        """``queue_type=None`` (unresolved) ⇒ caller flags pass through.

        ``enqueue_message_job`` sets ``resolved_queue_type = None``
        when the queue lookup fails or the project has no
        ``JobQueueService._queue_repo``. The helper must NOT
        force a flag in that case — the caller's intent is the
        only signal we have.
        """
        assert _derive_task_flags_from_queue_type(None) == (False, False)
        assert _derive_task_flags_from_queue_type(
            None, is_deferred=True
        ) == (True, False)
        assert _derive_task_flags_from_queue_type(
            None, is_background=True
        ) == (False, True)
        assert _derive_task_flags_from_queue_type(
            None, is_deferred=True, is_background=True
        ) == (True, True)

    def test_unknown_queue_type_preserves_caller_flags(self):
        """Defensive: an unknown ``queue_type`` is treated as a normal
        queue (caller flags pass through).

        The CHECK constraint on ``job_queues.queue_type`` enforces
        the four valid values, so this branch is purely defensive
        against future schema drift. It documents the contract for
        future maintainers.
        """
        assert _derive_task_flags_from_queue_type(
            "unknown", is_deferred=True
        ) == (True, False)


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Task-side defer predicate (Fix 2A)
# ─────────────────────────────────────────────────────────────────────────────


class TestTaskSideDeferPredicateExclusion:
    """``TaskRepository.has_active_non_deferred_work`` after Fix 2.

    The four documented cases:

      1. RUNNING non-deferred task → True (existing behavior preserved).
      2. PENDING-only non-deferred task with a queued JobItem → False
         (the deadlock case — the bug).
      3. PENDING non-deferred task with NO linked JobItem → True
         (direct-queue task, genuinely claimable).
      4. PENDING non-deferred task with an active JobItem → True
         (admitted, claimable).

    The test is the regression guard for the deadlock fix. Pre-fix
    case 2 returned True and wedged the defer-queue idle gate forever.
    """

    def test_running_non_deferred_task_returns_true(
        self, task_repository, queue_repository, engine
    ):
        """Case 1: a RUNNING non-deferred task contributes to the count."""
        _insert_instance(engine, "inst-1", project_id="proj-d-1")
        _insert_task(
            engine,
            instance_id="inst-1",
            status=TaskStatus.RUNNING.value,
            is_deferred=False,
        )
        assert task_repository.has_active_non_deferred_work("proj-d-1") is True
        # System-wide probe also returns True.
        assert task_repository.has_active_non_deferred_work() is True

    def test_pending_with_queued_jobitem_returns_false(
        self, task_repository, engine
    ):
        """Case 2: the deadlock case. A PENDING non-deferred task whose
        linked JobItem is still ``admission_state='queued'`` does NOT
        count as active non-deferred work.

        Pre-fix the predicate returned True for this case, the
        defer-queue idle gate stayed wedged, and the queue never
        made progress.
        """
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-pd", project_id="proj-pd")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-pd",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        # The linked JobItem is still queued — the task is unclaimable
        # (the queue-awareness guard in claim_pending_task blocks it).
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-pd",
            project_id="proj-pd",
            queue_id="queue-regular",
            admission_state=AdmissionState.QUEUED.value,
        )

        # Project-scoped probe: the predicate should ignore the
        # unclaimable pending task.
        assert task_repository.has_active_non_deferred_work("proj-pd") is False
        # System-wide probe: same result.
        assert task_repository.has_active_non_deferred_work() is False

    def test_pending_without_jobitem_returns_true(
        self, task_repository, engine
    ):
        """Case 3: a PENDING non-deferred task with NO linked JobItem
        is claimable (direct-queue task) and MUST count.

        The bug fix's exclusion only applies when the linked JobItem
        is still queued. A direct-queue task has no JobItem at all →
        the NOT EXISTS subquery is false → the task still counts.
        Otherwise the predicate would falsely report "idle" while
        a direct-queue task is still waiting.
        """
        _insert_instance(engine, "inst-direct", project_id="proj-direct")
        _insert_task(
            engine,
            instance_id="inst-direct",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        # No JobItem inserted — direct-queue task.
        assert task_repository.has_active_non_deferred_work("proj-direct") is True

    def test_pending_with_active_jobitem_returns_true(
        self, task_repository, engine
    ):
        """Case 4: a PENDING non-deferred task whose linked JobItem is
        already ``admission_state='active'`` is claimable and MUST count.

        The lock has been acquired and the task is next in line. The
        exclusion only targets the queued-bucket block.
        """
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-active", project_id="proj-active")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-active",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-active",
            project_id="proj-active",
            queue_id="queue-active",
            admission_state=AdmissionState.ACTIVE.value,
        )
        assert task_repository.has_active_non_deferred_work("proj-active") is True

    def test_deferred_task_excluded_unchanged(
        self, task_repository, engine
    ):
        """Sanity: a deferred task is still excluded from the defer
        predicate (the defer→defer lane is invisible to the defer
        gate, which is the original contract).

        This is the pre-fix behavior that the deadlock bug was
        about to corrupt by counting UNCLAIMABLE PENDING tasks as
        active non-deferred work — the inverse case below.
        """
        _insert_instance(engine, "inst-deferred", project_id="proj-deferred")
        _insert_task(
            engine,
            instance_id="inst-deferred",
            status=TaskStatus.RUNNING.value,
            is_deferred=True,
        )
        # The defer gate must NOT count an `is_deferred=True` task
        # (a defer task must pass through the defer gate, not block it).
        assert task_repository.has_active_non_deferred_work("proj-deferred") is False

    def test_paused_non_deferred_task_returns_true(
        self, task_repository, engine
    ):
        """A PAUSED non-deferred task still counts (pause-fix semantics).

        Pause = suspended-but-occupying, not idle. The exclusion
        applies to PENDING tasks only; RUNNING/PAUSED always count.
        """
        _insert_instance(engine, "inst-paused", project_id="proj-paused")
        _insert_task(
            engine,
            instance_id="inst-paused",
            status=TaskStatus.PAUSED.value,
            is_deferred=False,
        )
        assert task_repository.has_active_non_deferred_work("proj-paused") is True

    def test_mixed_pending_running_only_running_counts(
        self, task_repository, engine
    ):
        """Two tasks in the same project: one PENDING-with-queued-JobItem
        (deadlock case, excluded) and one RUNNING (counts). The predicate
        must return True (the RUNNING task alone is enough).

        Regression for the "does the exclusion leak into the
        RUNNING branch" case — the two branches are unions, not
        intersections, so the RUNNING task must still drive the
        result True.
        """
        # PENDING with queued JobItem (deadlock case, excluded).
        wd_queued = str(uuid.uuid4())
        _insert_instance(engine, "inst-mixed-1", project_id="proj-mixed")
        _insert_task(
            engine,
            work_id=wd_queued,
            instance_id="inst-mixed-1",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
        )
        _insert_job_item(
            engine,
            job_id=wd_queued,
            instance_id="inst-mixed-1",
            project_id="proj-mixed",
            queue_id="queue-mixed-1",
            admission_state=AdmissionState.QUEUED.value,
        )
        # RUNNING task in the same project (counts).
        _insert_instance(engine, "inst-mixed-2", project_id="proj-mixed")
        _insert_task(
            engine,
            instance_id="inst-mixed-2",
            status=TaskStatus.RUNNING.value,
            is_deferred=False,
        )
        assert task_repository.has_active_non_deferred_work("proj-mixed") is True


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Task-side background predicate (Fix 2A)
# ─────────────────────────────────────────────────────────────────────────────


class TestTaskSideBackgroundPredicateExclusion:
    """Mirror of the defer predicate test for
    ``TaskRepository.has_active_non_background_work``.

    Same four cases, same expectations. The background predicate is
    system-wide (the ``project_id`` parameter is accepted for
    signature symmetry but intentionally ignored — documented scope
    asymmetry with the defer predicate).
    """

    def test_running_non_background_task_returns_true(
        self, task_repository, engine
    ):
        """Case 1: a RUNNING non-background task contributes to the count."""
        _insert_instance(engine, "inst-bgr-1", project_id="proj-bgr-1")
        _insert_task(
            engine,
            instance_id="inst-bgr-1",
            status=TaskStatus.RUNNING.value,
            is_background=False,
        )
        assert task_repository.has_active_non_background_work() is True

    def test_pending_with_queued_jobitem_returns_false(
        self, task_repository, engine
    ):
        """Case 2: deadlock case. A PENDING non-background task whose
        linked JobItem is still queued does NOT count.

        Pre-fix the predicate counted it as active non-background
        work and the background-queue idle gate stayed wedged.
        """
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-bgr-pd", project_id="proj-bgr-pd")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-bgr-pd",
            status=TaskStatus.PENDING.value,
            is_background=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-bgr-pd",
            project_id="proj-bgr-pd",
            queue_id="queue-bgr-1",
            admission_state=AdmissionState.QUEUED.value,
        )
        assert task_repository.has_active_non_background_work() is False

    def test_pending_without_jobitem_returns_true(
        self, task_repository, engine
    ):
        """Case 3: direct-queue task (no JobItem) still counts."""
        _insert_instance(engine, "inst-bgr-direct", project_id="proj-bgr-direct")
        _insert_task(
            engine,
            instance_id="inst-bgr-direct",
            status=TaskStatus.PENDING.value,
            is_background=False,
        )
        assert task_repository.has_active_non_background_work() is True

    def test_pending_with_active_jobitem_returns_true(
        self, task_repository, engine
    ):
        """Case 4: PENDING task with an active JobItem still counts."""
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-bgr-active", project_id="proj-bgr-active")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-bgr-active",
            status=TaskStatus.PENDING.value,
            is_background=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-bgr-active",
            project_id="proj-bgr-active",
            queue_id="queue-bgr-active",
            admission_state=AdmissionState.ACTIVE.value,
        )
        assert task_repository.has_active_non_background_work() is True

    def test_background_task_excluded_unchanged(
        self, task_repository, engine
    ):
        """Sanity: a background task is still excluded from the
        background predicate (a background task must pass through
        the background gate, not block it).
        """
        _insert_instance(engine, "inst-bg-flag", project_id="proj-bg-flag")
        _insert_task(
            engine,
            instance_id="inst-bg-flag",
            status=TaskStatus.RUNNING.value,
            is_background=True,
        )
        assert task_repository.has_active_non_background_work() is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Job-side background predicate (Fix 2B)
# ─────────────────────────────────────────────────────────────────────────────


class TestJobSideBackgroundPredicateExclusion:
    """``JobRepository.has_active_non_background_work`` after Fix 2.

    The LEFT JOIN to ``task`` and the
    ``NOT (j.admission_state = 'queued' AND t.status = 'pending')``
    exclusion. The two cases that matter:

      * Case A: a queued JobItem linked to a pending Task → False
        (deadlock case — the predicate must NOT count it).
      * Case B: a queued JobItem with NO linked Task (direct-queue
        enqueue) or with a non-pending Task → True (still represents
        legitimate work waiting for the lock).
    """

    def test_queued_jobitem_with_pending_task_returns_false(
        self, job_repository, engine
    ):
        """Case A: the deadlock case. A ``queued`` JobItem whose
        linked Task is still ``pending`` is unclaimable and must
        NOT count as active non-background work.
        """
        # Seed an instance (terminal-status filter would otherwise
        # exclude it).
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-jbg-pd", project_id="proj-jbg-pd")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-jbg-pd",
            status=TaskStatus.PENDING.value,
            is_deferred=False,
            is_background=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-jbg-pd",
            project_id="proj-jbg-pd",
            queue_id="queue-jbg-pd",
            admission_state=AdmissionState.QUEUED.value,
        )
        # The predicate must NOT count this row.
        assert job_repository.has_active_non_background_work() is False

    def test_queued_jobitem_without_task_returns_true(
        self, job_repository, engine
    ):
        """Case B: a ``queued`` JobItem with NO linked Task (direct-queue
        enqueue — no shared work_id) still counts.

        The ``LEFT JOIN`` keeps ``t.status`` NULL for the no-task
        case, so the ``NOT (...)`` exclusion short-circuits to TRUE
        and the row is correctly counted. Regression for the
        "does the LEFT JOIN break direct-queue counting" case.
        """
        _insert_instance(engine, "inst-jbg-direct", project_id="proj-jbg-direct")
        # JobItem with no matching task row (job_id is a fresh UUID,
        # not shared with any task).
        _insert_job_item(
            engine,
            job_id=str(uuid.uuid4()),
            instance_id="inst-jbg-direct",
            project_id="proj-jbg-direct",
            queue_id="queue-jbg-1",
            admission_state=AdmissionState.QUEUED.value,
        )
        assert job_repository.has_active_non_background_work() is True

    def test_queued_jobitem_with_active_task_returns_true(
        self, job_repository, engine
    ):
        """Case B (extended): a ``queued`` JobItem whose linked Task is
        ``active`` STILL counts. The exclusion only targets the
        ``('queued' AND pending)`` combination; a
        ``('queued' AND active)`` row is a transient state during
        claim and must NOT block the gate (the work is happening).
        """
        work_id = str(uuid.uuid4())
        _insert_instance(engine, "inst-jbg-acts", project_id="proj-jbg-acts")
        _insert_task(
            engine,
            work_id=work_id,
            instance_id="inst-jbg-acts",
            status=TaskStatus.RUNNING.value,
            is_deferred=False,
            is_background=False,
        )
        _insert_job_item(
            engine,
            job_id=work_id,
            instance_id="inst-jbg-acts",
            project_id="proj-jbg-acts",
            queue_id="queue-jbg-acts",
            admission_state=AdmissionState.QUEUED.value,
        )
        assert job_repository.has_active_non_background_work() is True

    def test_active_jobitem_always_counts(
        self, job_repository, engine
    ):
        """Sanity: an ``active`` JobItem always counts regardless of
        the linked Task's status. ``active`` is NOT in the exclusion
        condition, so the row is correctly counted.
        """
        _insert_instance(engine, "inst-jbg-active", project_id="proj-jbg-active")
        _insert_job_item(
            engine,
            job_id=str(uuid.uuid4()),
            instance_id="inst-jbg-active",
            project_id="proj-jbg-active",
            queue_id="queue-jbg-active",
            admission_state=AdmissionState.ACTIVE.value,
        )
        assert job_repository.has_active_non_background_work() is True

    def test_background_queue_item_excluded_unchanged(
        self, job_repository, engine
    ):
        """Sanity: a JobItem on a background queue is still excluded
        (the existing ``q.queue_type != 'background'`` filter).
        """
        _insert_instance(engine, "inst-jbg-bg", project_id="proj-jbg-bg")
        _insert_queue(
            engine, "queue-bg", project_id="proj-jbg-bg", queue_type="background"
        )
        _insert_job_item(
            engine,
            job_id=str(uuid.uuid4()),
            instance_id="inst-jbg-bg",
            project_id="proj-jbg-bg",
            queue_id="queue-bg",
            admission_state=AdmissionState.ACTIVE.value,
        )
        assert job_repository.has_active_non_background_work() is False
