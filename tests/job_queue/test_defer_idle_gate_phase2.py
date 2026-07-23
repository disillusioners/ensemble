"""Comprehensive tests for Phase 2 job-based idle-gate predicates.

Phase 2 (defer-queue idle gate, 2026-07-23, commits 6e077ddd / 059d1ecc)
introduced two new predicates on ``JobRepository``:

  * ``has_active_non_deferred_work(project_id)`` — counts ``JobItem``
    rows with ``admission_state IN ('queued', 'active')`` whose owning
    queue is non-defer AND whose linked instance is non-terminal. The
    Phase 1 task-granular predicate is "blind" during the inter-turn
    ``waiting_children`` window when no task row exists yet; the job
    predicate closes that gap.
  * ``has_active_non_background_work(project_id)`` — sister method
    that excludes BOTH defer AND background queue types so a background
    queue only fires when every project's normal/defer lanes are idle.

These predicates sit alongside the existing task-granular
``TaskRepository.has_active_non_deferred_work`` /
``has_active_non_background_work`` (Phase 1 / Phase 3) at every gate
call site (Gate A in ``JobProcessor._defer_idle_check`` /
``_background_idle_check``, Gate B in
``JobQueueService._select_next_eligible_job``, plus the
``MaintenanceService._is_idle`` probe). The job predicate is the
FIRST check; the task predicate runs as a follow-up when the job
predicate returns False, because a Task can exist without a backing
JobItem.

The tests in this module cover:

* **TestJobBasedPredicateNonDeferredWork**: the
  ``has_active_non_deferred_work`` predicate across queue type,
  admission state, instance status, project isolation, system-wide
  probe, and queue-less jobs.
* **TestJobBasedPredicateNonBackgroundWork**: the
  ``has_active_non_background_work`` predicate across the same axes,
  with the additional "defer counts, background excluded" semantics.
* **TestIncidentReproduction**: the 2026-07-23 inter-report-gap bug
  scenario where a parent in ``waiting_children`` with an active
  JobItem was invisible to the task-granular predicate.
* **TestGateCompositionBeltAndSuspenders**: the gate call sites use
  OR semantics — "either job predicate True OR task predicate True
  ⇒ block the defer queue". Mocks both predicates and verifies the
  OR truth table at the gate.

Tests use the SQLite in-memory engine via the ``engine`` /
``repository`` fixtures from ``tests/job_queue/conftest.py``. No real
LLM, no daemon — pure unit/integration tests over the repository.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from daemon.repositories.job_queue.models import AdmissionState, QueueType
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.services.job_queue_service import JobQueueService


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (raw-SQL seeding; mirrors patterns in test_seam_invariants.py)
# ─────────────────────────────────────────────────────────────────────────────


def _insert_instance(
    engine,
    instance_id: str,
    project_id: str = "test-project",
    status: str = "running",
    agent_id: str = "developer",
) -> None:
    """Insert an Instance row directly via SQL.

    The Phase 2 predicate joins ``job_queue_items`` against
    ``instances`` to filter out terminal instances, so we need a
    matching Instance row to make the project-scoping + status JOIN
    work. Mirrors the helper in ``test_seam_invariants.py``.
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
                "agent_id": agent_id,
                "agent_dir": f"agents/{agent_id}",
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
    admission_state: str = AdmissionState.QUEUED.value,
    job_metadata: dict | None = None,
) -> None:
    """Insert a JobItem directly via SQL. Mirrors test_seam_invariants."""
    now = datetime.now(timezone.utc).isoformat()
    metadata_json = json.dumps(job_metadata or {})
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
                "message": "hi",
                "source": "api",
                "project_id": project_id,
                "queue_id": queue_id,
                "priority": 0,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
                "job_type": "task",
                "retry_count": 0,
                "metadata": metadata_json,
            },
        )


def _insert_queue(
    engine,
    queue_id: str,
    project_id: str,
    queue_type: str = "parallel",
    queue_name: str | None = None,
    concurrency_limit: int = 1,
) -> None:
    """Insert a JobQueue row directly via SQL.

    Required by every test that needs the predicate's LEFT JOIN on
    ``job_queues`` to actually match a row with a real
    ``queue_type``. The table's NOT-NULL columns (``queue_name``,
    ``queue_name_lower``) are populated; ``is_system`` /
    ``is_paused`` default to False.

    Schema (from ``daemon/repositories/job_queue/models.py``):

      * queue_id (PK)
      * project_id (NOT NULL)
      * queue_name (NOT NULL)
      * queue_name_lower (NOT NULL) — the unique index is
        (project_id, queue_name_lower), so we normalise to its lowercased
        form to match the production ``JobQueueRepository.create`` helper.
      * queue_type (NOT NULL, CHECK IN ('fifo', 'parallel', 'defer', 'background'))
      * concurrency_limit (NOT NULL, >= 1, <= 20; defer/background require 1)
      * is_system / is_paused / description (defaults)
      * created_at / updated_at (timestamps; default to ``now``)
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


def _wire_task_predicates(
    service: JobQueueService,
    *,
    has_active_non_deferred_work: bool = False,
    has_active_non_background_work: bool = False,
) -> tuple[MagicMock, MagicMock]:
    """Wire ``_instance_manager._task_repo.{has_active_non_*}`` on a service.

    Returns the two predicate mocks so tests can assert call args.
    Mirrors the pattern from ``test_select_next_eligible_job.py``,
    extended to wire both predicates.
    """
    non_deferred_pred = MagicMock(return_value=has_active_non_deferred_work)
    non_background_pred = MagicMock(return_value=has_active_non_background_work)
    task_repo = MagicMock()
    task_repo.has_active_non_deferred_work = non_deferred_pred
    task_repo.has_active_non_background_work = non_background_pred
    instance_manager = MagicMock()
    instance_manager._task_repo = task_repo
    service.set_instance_manager(instance_manager)
    return non_deferred_pred, non_background_pred


# ─────────────────────────────────────────────────────────────────────────────
# Test Class 1: has_active_non_deferred_work — exhaustive matrix
# ─────────────────────────────────────────────────────────────────────────────


class TestJobBasedPredicateNonDeferredWork:
    """Phase 2 job-based predicate tests.

    Each test seeds a SINGLE JobItem with the indicated
    (queue_type, instance_status, admission_state) combination and
    asserts the project-scoped probe returns the expected bool. The
    autoincrement ``-postgres`` marker is applied at module level via
    the ``pytest -m "not postgres"`` invocation — these tests target
    SQLite (the in-memory engine); the predicate is portable to
    PostgreSQL but the test data is identical on both.
    """

    def test_waiting_children_instance_with_active_job_returns_true(
        self, engine, repository
    ):
        """A job with ``admission_state='active'`` on a non-defer queue
        whose instance is ``waiting_children`` must be counted as
        active work (the bug scenario).

        This is the EXACT incident reproduction: a parent in
        ``waiting_children`` had an active JobItem that the
        task-granular predicate missed (no Task row existed in the
        gap), and the defer queue wrongly fired.
        """
        _insert_instance(
            engine, "inst-wc-1", project_id="proj-wc", status="waiting_children"
        )
        _insert_queue(
            engine, "queue-nd-1", project_id="proj-wc", queue_type="parallel"
        )
        _insert_job_item(
            engine,
            job_id="job-wc-1",
            instance_id="inst-wc-1",
            project_id="proj-wc",
            queue_id="queue-nd-1",
            admission_state="active",
        )
        assert repository.has_active_non_deferred_work("proj-wc") is True

    def test_running_instance_with_active_job_returns_true(self, engine, repository):
        """``running`` instance + active job ⇒ True."""
        _insert_instance(engine, "inst-run-1", project_id="proj-run", status="running")
        _insert_queue(
            engine, "queue-nd-2", project_id="proj-run", queue_type="parallel"
        )
        _insert_job_item(
            engine,
            job_id="job-run-1",
            instance_id="inst-run-1",
            project_id="proj-run",
            queue_id="queue-nd-2",
            admission_state="active",
        )
        assert repository.has_active_non_deferred_work("proj-run") is True

    def test_completed_instance_returns_false(self, engine, repository):
        """``completed`` instance + active job ⇒ False (terminal)."""
        _insert_instance(
            engine, "inst-done-1", project_id="proj-done", status="completed"
        )
        _insert_queue(
            engine, "queue-nd-3", project_id="proj-done", queue_type="parallel"
        )
        _insert_job_item(
            engine,
            job_id="job-done-1",
            instance_id="inst-done-1",
            project_id="proj-done",
            queue_id="queue-nd-3",
            admission_state="active",
        )
        assert repository.has_active_non_deferred_work("proj-done") is False

    def test_failed_instance_returns_false(self, engine, repository):
        """``failed`` instance ⇒ False (terminal)."""
        _insert_instance(
            engine, "inst-fail-1", project_id="proj-fail", status="failed"
        )
        _insert_queue(
            engine, "queue-nd-4", project_id="proj-fail", queue_type="parallel"
        )
        _insert_job_item(
            engine,
            job_id="job-fail-1",
            instance_id="inst-fail-1",
            project_id="proj-fail",
            queue_id="queue-nd-4",
            admission_state="active",
        )
        assert repository.has_active_non_deferred_work("proj-fail") is False

    def test_defer_queue_job_excluded(self, engine, repository):
        """A job on a defer queue must NOT be counted as non-deferred work.

        The defer predicate must be defer-INVISIBLE: a defer queue's
        own active jobs cannot block the defer gate (otherwise two
        defer queues in the same project would deadlock each other).
        """
        _insert_instance(
            engine, "inst-defer-1", project_id="proj-defer", status="running"
        )
        _insert_queue(
            engine, "queue-defer-1", project_id="proj-defer", queue_type="defer"
        )
        _insert_job_item(
            engine,
            job_id="job-defer-1",
            instance_id="inst-defer-1",
            project_id="proj-defer",
            queue_id="queue-defer-1",
            admission_state="active",
        )
        assert repository.has_active_non_deferred_work("proj-defer") is False

    def test_queued_job_counts_as_active(self, engine, repository):
        """A ``queued`` job (not yet dispatched) must count as active work.

        The predicate covers BOTH ``queued`` and ``active`` admission
        states so a project with only queued (not-yet-dispatched) jobs
        still blocks the defer queue — preserving FIFO priority.
        """
        _insert_instance(
            engine, "inst-q-1", project_id="proj-q", status="idle"
        )
        _insert_queue(
            engine, "queue-nd-5", project_id="proj-q", queue_type="parallel"
        )
        _insert_job_item(
            engine,
            job_id="job-q-1",
            instance_id="inst-q-1",
            project_id="proj-q",
            queue_id="queue-nd-5",
            admission_state="queued",
        )
        assert repository.has_active_non_deferred_work("proj-q") is True

    def test_done_job_excluded(self, engine, repository):
        """A ``done`` job must NOT count as active work."""
        _insert_instance(
            engine, "inst-done-2", project_id="proj-done2", status="running"
        )
        _insert_queue(
            engine, "queue-nd-6", project_id="proj-done2", queue_type="parallel"
        )
        _insert_job_item(
            engine,
            job_id="job-done-2",
            instance_id="inst-done-2",
            project_id="proj-done2",
            queue_id="queue-nd-6",
            admission_state="done",
        )
        assert repository.has_active_non_deferred_work("proj-done2") is False

    def test_project_isolation(self, engine, repository):
        """Jobs in project A must not affect project B."""
        _insert_instance(
            engine, "inst-iso-a", project_id="proj-iso-a", status="running"
        )
        _insert_queue(
            engine, "queue-iso-a", project_id="proj-iso-a", queue_type="parallel"
        )
        _insert_job_item(
            engine,
            job_id="job-iso-a",
            instance_id="inst-iso-a",
            project_id="proj-iso-a",
            queue_id="queue-iso-a",
            admission_state="active",
        )
        assert repository.has_active_non_deferred_work("proj-iso-b") is False

    def test_system_wide_probe(self, engine, repository):
        """``project_id=None`` probes ALL projects.

        This is the maintenance / system-wide probe shape used by
        ``_is_idle`` and the background predicate (which is always
        system-wide).
        """
        _insert_instance(
            engine, "inst-sys-1", project_id="proj-sys-1", status="running"
        )
        _insert_queue(
            engine, "queue-sys-1", project_id="proj-sys-1", queue_type="parallel"
        )
        _insert_job_item(
            engine,
            job_id="job-sys-1",
            instance_id="inst-sys-1",
            project_id="proj-sys-1",
            queue_id="queue-sys-1",
            admission_state="active",
        )
        assert repository.has_active_non_deferred_work(None) is True

    def test_queue_less_job_counted(self, engine, repository):
        """A job with ``NULL queue_id`` must still be counted.

        The LEFT JOIN on ``job_queues`` matches ``queue_type IS NULL``
        (which is in the predicate's exclusion-OR branch), and a
        queue-less job represents a direct-dispatch / legacy row that
        still must block the defer gate.
        """
        _insert_instance(
            engine, "inst-nq-1", project_id="proj-nq", status="running"
        )
        _insert_job_item(
            engine,
            job_id="job-nq-1",
            instance_id="inst-nq-1",
            project_id="proj-nq",
            queue_id=None,
            admission_state="active",
        )
        assert repository.has_active_non_deferred_work("proj-nq") is True

    def test_empty_project_returns_false(self, engine, repository):
        """No jobs at all ⇒ False."""
        _insert_instance(
            engine, "inst-empty-1", project_id="proj-empty", status="idle"
        )
        assert repository.has_active_non_deferred_work("proj-empty") is False

    def test_paused_instance_counts_as_active(self, engine, repository):
        """W2 documentation invariant: a ``paused`` instance's job IS
        counted as active work.

        Pause is an *Instance* concern — the JobItem keeps its lock and
        remains ``admission_state='active'`` during pause, so the
        instance-level ``paused`` status is in the predicate's
        non-terminal set and the job IS counted. A paused instance is
        suspended-but-occupying, NOT idle.
        """
        _insert_instance(
            engine, "inst-paused", project_id="proj-paused", status="paused"
        )
        _insert_queue(
            engine, "queue-paused", project_id="proj-paused", queue_type="parallel"
        )
        _insert_job_item(
            engine,
            job_id="job-paused",
            instance_id="inst-paused",
            project_id="proj-paused",
            queue_id="queue-paused",
            admission_state="active",
        )
        assert repository.has_active_non_deferred_work("proj-paused") is True


# ─────────────────────────────────────────────────────────────────────────────
# Test Class 2: has_active_non_background_work — sister matrix
# ─────────────────────────────────────────────────────────────────────────────


class TestJobBasedPredicateNonBackgroundWork:
    """Phase 2 job-based background-predicate tests.

    Sister method: ``has_active_non_background_work`` excludes BOTH
    defer AND background queue types so a background queue only
    fires when every project's normal/defer lanes are idle. The
    ``project_id`` argument is INTENTIONALLY IGNORED — the background
    predicate is always system-wide (Phase 3 background-seam
    invariant).
    """

    def test_defer_queue_job_excluded_from_non_background(self, engine, repository):
        """A defer queue's job IS excluded from non-background work.

        The predicate counts "non-defer AND non-background" work:
        ``queue_type NOT IN ('defer', 'background')``. Defer queue
        jobs are intentionally invisible to the background gate so a
        defer queue firing in one project does not block a
        background queue in another (background waits for *normal*
        lane work only, not other background-or-defer traffic).
        """
        _insert_instance(
            engine, "inst-bg-d-1", project_id="proj-bg-d", status="running"
        )
        _insert_queue(
            engine, "queue-bg-d-1", project_id="proj-bg-d", queue_type="defer"
        )
        _insert_job_item(
            engine,
            job_id="job-bg-d-1",
            instance_id="inst-bg-d-1",
            project_id="proj-bg-d",
            queue_id="queue-bg-d-1",
            admission_state="active",
        )
        assert repository.has_active_non_background_work() is False

    def test_fifo_queue_job_counts_as_active(self, engine, repository):
        """A FIFO queue's job is non-background and IS counted."""
        _insert_instance(
            engine, "inst-bg-f-1", project_id="proj-bg-f", status="running"
        )
        _insert_queue(
            engine, "queue-bg-f-1", project_id="proj-bg-f", queue_type="fifo"
        )
        _insert_job_item(
            engine,
            job_id="job-bg-f-1",
            instance_id="inst-bg-f-1",
            project_id="proj-bg-f",
            queue_id="queue-bg-f-1",
            admission_state="active",
        )
        assert repository.has_active_non_background_work() is True

    def test_parallel_queue_job_counts_as_active(self, engine, repository):
        """A parallel queue's job is non-background and IS counted."""
        _insert_instance(
            engine, "inst-bg-p-1", project_id="proj-bg-p", status="running"
        )
        _insert_queue(
            engine, "queue-bg-p-1", project_id="proj-bg-p", queue_type="parallel"
        )
        _insert_job_item(
            engine,
            job_id="job-bg-p-1",
            instance_id="inst-bg-p-1",
            project_id="proj-bg-p",
            queue_id="queue-bg-p-1",
            admission_state="active",
        )
        assert repository.has_active_non_background_work() is True

    def test_background_queue_job_excluded(self, engine, repository):
        """A background queue's job must NOT count as non-background work.

        Other background work is *expected* and must not block its
        own lane (otherwise N concurrent background workers would
        starve themselves).
        """
        _insert_instance(
            engine, "inst-bg-b-1", project_id="proj-bg-b", status="running"
        )
        _insert_queue(
            engine, "queue-bg-b-1", project_id="proj-bg-b", queue_type="background"
        )
        _insert_job_item(
            engine,
            job_id="job-bg-b-1",
            instance_id="inst-bg-b-1",
            project_id="proj-bg-b",
            queue_id="queue-bg-b-1",
            admission_state="active",
        )
        assert repository.has_active_non_background_work() is False

    def test_terminal_instance_returns_false(self, engine, repository):
        """Terminal instance (completed) ⇒ False."""
        _insert_instance(
            engine, "inst-bg-t-1", project_id="proj-bg-t", status="completed"
        )
        _insert_queue(
            engine, "queue-bg-t-1", project_id="proj-bg-t", queue_type="parallel"
        )
        _insert_job_item(
            engine,
            job_id="job-bg-t-1",
            instance_id="inst-bg-t-1",
            project_id="proj-bg-t",
            queue_id="queue-bg-t-1",
            admission_state="active",
        )
        assert repository.has_active_non_background_work() is False

    def test_system_wide_scope_ignores_project_arg(
        self, engine, repository
    ):
        """``project_id`` argument is IGNORED for the background predicate.

        The background predicate is always system-wide. Passing any
        value as ``project_id`` should NOT filter the result; this
        test passes ``"some-project"`` and asserts a row inserted
        under a DIFFERENT project is still counted.
        """
        _insert_instance(
            engine,
            "inst-bg-sys",
            project_id="proj-bg-sys-actual",
            status="running",
        )
        _insert_queue(
            engine,
            "queue-bg-sys",
            project_id="proj-bg-sys-actual",
            queue_type="parallel",
        )
        _insert_job_item(
            engine,
            job_id="job-bg-sys",
            instance_id="inst-bg-sys",
            project_id="proj-bg-sys-actual",
            queue_id="queue-bg-sys",
            admission_state="active",
        )
        # The literal sentinel project_id; the predicate ignores it.
        assert repository.has_active_non_background_work("some-other-project") is True


# ─────────────────────────────────────────────────────────────────────────────
# Test Class 3: Reproduce the 2026-07-23 incident
# ─────────────────────────────────────────────────────────────────────────────


class TestIncidentReproduction:
    """Reproduce the 2026-07-23 incident: a parent in
    ``waiting_children`` with an active parallel-queue JobItem was
    invisible to the task-granular predicate, so the defer queue
    fired prematurely.

    The Phase 2 job predicate closes the inter-report-gap blind spot
    that the task predicate has during ``waiting_children``. The
    tests below pin the relevant axes around this scenario.
    """

    def test_waiting_children_parent_blocks_defer_admission(
        self, engine, repository
    ):
        """Parent instance in ``waiting_children`` with an active
        parallel-queue JobItem must block the defer queue admission
        (the exact incident scenario).

        Reproduces 2026-07-23: a parent in ``waiting_children`` had
        a JobItem in ``active`` state on a parallel queue. The
        task-granular predicate found no Task row in the gap, so
        the defer gate returned False and a defer job was wrongly
        admitted.
        """
        _insert_instance(
            engine,
            "inst-incident-parent",
            project_id="proj-incident",
            status="waiting_children",
        )
        _insert_queue(
            engine,
            "queue-parallel",
            project_id="proj-incident",
            queue_type="parallel",
        )
        _insert_job_item(
            engine,
            job_id="job-incident-parent",
            instance_id="inst-incident-parent",
            project_id="proj-incident",
            queue_id="queue-parallel",
            admission_state="active",
        )
        # The Phase 2 predicate MUST see this as active non-defer work.
        assert repository.has_active_non_deferred_work("proj-incident") is True

    def test_idle_parent_allows_defer_admission(self, engine, repository):
        """When the parent reaches an idle/terminal state, the defer
        queue MAY be admitted.

        Duals the test above: once the parent is ``completed``, the
        JobItem is no longer counted (terminal instance excluded by
        the predicate), so the defer queue is released.
        """
        _insert_instance(
            engine,
            "inst-idle-parent",
            project_id="proj-incident2",
            status="completed",
        )
        _insert_queue(
            engine,
            "queue-parallel2",
            project_id="proj-incident2",
            queue_type="parallel",
        )
        _insert_job_item(
            engine,
            job_id="job-idle-parent",
            instance_id="inst-idle-parent",
            project_id="proj-incident2",
            queue_id="queue-parallel2",
            admission_state="done",
        )
        assert repository.has_active_non_deferred_work("proj-incident2") is False

    def test_paused_parent_blocks_defer_admission(self, engine, repository):
        """W2 invariant: a ``paused`` parent still blocks the defer queue.

        A paused parent occupies its slot and may resume at any time;
        treating it as "idle" would re-introduce the 2026-07-17
        "defer job wrongly admitted during pause" bug.
        """
        _insert_instance(
            engine,
            "inst-paused-parent",
            project_id="proj-paused",
            status="paused",
        )
        _insert_queue(
            engine,
            "queue-paused-parent",
            project_id="proj-paused",
            queue_type="parallel",
        )
        _insert_job_item(
            engine,
            job_id="job-paused-parent",
            instance_id="inst-paused-parent",
            project_id="proj-paused",
            queue_id="queue-paused-parent",
            admission_state="active",
        )
        assert repository.has_active_non_deferred_work("proj-paused") is True


# ─────────────────────────────────────────────────────────────────────────────
# Test Class 4: Gate composition — belt-and-suspenders OR semantics
# ─────────────────────────────────────────────────────────────────────────────


class TestGateCompositionBeltAndSuspenders:
    """Pin the OR semantics at the gate call sites.

    Every gate call site consults BOTH the job-granular predicate
    (``JobRepository.has_active_non_deferred_work`` /
    ``has_active_non_background_work``) AND the task-granular
    predicate (``TaskRepository.has_active_non_deferred_work`` /
    ``has_active_non_background_work``). The gate blocks (counts as
    "active") if EITHER predicate says True.

    The four quadrants:

      * job=False, task=False ⇒ idle (admit)
      * job=False, task=True  ⇒ task-only blocks (admit? NO — Phase 1)
      * job=True,  task=False ⇒ job-only blocks (Phase 2 closer)
      * job=True,  task=True  ⇒ both agree (block)

    These tests mock both predicate calls and assert the gate's OR
    truth table using ``_select_next_eligible_job`` (the Gate B call
    site) as the integration point. We exercise the candidate-list
    path with a single defer-queue pending job so the defer branch
    of the gate fires; the next eligible job is None iff the gate
    blocks.
    """

    @staticmethod
    def _build_service_and_pending(defer_queue_id: str = "queue-composition-defer"):
        """Build a JobQueueService with mocks wired for the defer gate.

        Returns ``(service, defer_job)``. The defer job's ``queue_id``
        matches the mocked ``queue_repo.get`` so the branch picks it
        up as a defer candidate. Note the project_id must match
        ``defer_job.project_id`` (we use ``"proj-composition"``).
        """
        defer_job = MagicMock()
        defer_job.job_id = "job-composition-defer"
        defer_job.queue_id = defer_queue_id
        defer_job.project_id = "proj-composition"
        defer_job.priority = 5
        defer_job.created_at = "2025-01-01T00:00:00"

        defer_queue = MagicMock()
        defer_queue.queue_id = defer_queue_id
        defer_queue.project_id = "proj-composition"
        defer_queue.queue_type = QueueType.DEFER.value

        queue_repo = MagicMock()
        queue_repo._queue_map = {defer_queue_id: defer_queue}
        queue_repo.get = lambda qid: queue_repo._queue_map.get(qid)

        repo = MagicMock()
        # No job predicate on this repo mock — both calls return
        # False/MagicMock fallback to task predicate.
        service = JobQueueService(
            repository=repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )
        return service, defer_job

    @pytest.mark.asyncio
    async def test_job_false_task_false_admits_defer_job(self):
        """Both predicates False ⇒ defer job is admitted.

        This is the steady-state idle case: the queue and the task
        tables both confirm the system is idle, so the defer job
        is the next eligible.
        """
        service, defer_job = self._build_service_and_pending()
        non_deferred_pred, _ = _wire_task_predicates(
            service, has_active_non_deferred_work=False
        )
        result = await service._select_next_eligible_job(
            [defer_job], "proj-composition"
        )
        assert result is defer_job, (
            "Defer job must be admitted when both predicates are False "
            "(system is idle)."
        )
        non_deferred_pred.assert_called_once_with("proj-composition")

    @pytest.mark.asyncio
    async def test_job_false_task_true_blocks_defer_job(self):
        """Task-only True ⇒ defer job blocked (legacy Phase 1 invariant).

        A Task exists in the project without a backing JobItem — a
        "virtual job" that holds a worker slot. The gate MUST block
        the defer queue. This is the path the Phase 1 task-granular
        predicate protects.
        """
        service, defer_job = self._build_service_and_pending()
        non_deferred_pred, _ = _wire_task_predicates(
            service, has_active_non_deferred_work=True
        )
        result = await service._select_next_eligible_job(
            [defer_job], "proj-composition"
        )
        assert result is None, (
            "Defer job must be blocked when task predicate is True "
            "even if job predicate is False — Phase 1 belt."
        )
        non_deferred_pred.assert_called_once_with("proj-composition")

    @pytest.mark.asyncio
    async def test_job_true_task_false_blocks_defer_job(self):
        """Job-only True ⇒ defer job blocked (Phase 2 closer).

        The exact Phase 2 scenario: a JobItem in ``active`` state on a
        non-defer queue, but no Task row exists yet (inter-report gap).
        The job predicate returns True, the task predicate returns
        False, and the gate MUST block — the Phase 2 suspenders.
        """
        service, defer_job = self._build_service_and_pending()
        non_deferred_pred, _ = _wire_task_predicates(
            service, has_active_non_deferred_work=False
        )

        # Override the job predicate to return True. _select_next_eligible_job
        # uses ``getattr(self, "_repository", None)`` for the JobRepository.
        job_pred = MagicMock(return_value=True)
        service._repository.has_active_non_deferred_work = job_pred

        result = await service._select_next_eligible_job(
            [defer_job], "proj-composition"
        )
        assert result is None, (
            "Defer job must be blocked when job predicate is True even "
            "if task predicate is False — Phase 2 suspenders."
        )
        # The job predicate was consulted FIRST; the task predicate was
        # NOT consulted because the job predicate short-circuited True.
        job_pred.assert_called_once_with("proj-composition")
        non_deferred_pred.assert_not_called()

    @pytest.mark.asyncio
    async def test_job_true_task_true_blocks_defer_job(self):
        """Both predicates True ⇒ defer job blocked (no disagreement)."""
        service, defer_job = self._build_service_and_pending()
        non_deferred_pred, _ = _wire_task_predicates(
            service, has_active_non_deferred_work=True
        )

        job_pred = MagicMock(return_value=True)
        service._repository.has_active_non_deferred_work = job_pred

        result = await service._select_next_eligible_job(
            [defer_job], "proj-composition"
        )
        assert result is None, (
            "Defer job must be blocked when both predicates are True."
        )
        job_pred.assert_called_once_with("proj-composition")
        # Job predicate short-circuits the OR; task predicate is not
        # consulted.
        non_deferred_pred.assert_not_called()

    @pytest.mark.asyncio
    async def test_job_predicate_exception_fails_closed(self):
        """If the job predicate raises, the defer gate fails closed.

        W3 invariant: a transient DB error in the job predicate must
        NOT silently release the defer queue. The gate must treat the
        exception as "active" — the defer job is blocked. The task
        predicate is then consulted as a follow-up; if THAT also
        raises, the gate stays closed.
        """
        service, defer_job = self._build_service_and_pending()
        non_deferred_pred, _ = _wire_task_predicates(
            service, has_active_non_deferred_work=False
        )

        # Job predicate raises (e.g. transient DB error).
        job_pred = MagicMock(side_effect=RuntimeError("db down"))
        service._repository.has_active_non_deferred_work = job_pred

        result = await service._select_next_eligible_job(
            [defer_job], "proj-composition"
        )
        assert result is None, (
            "W3 invariant: a job-predicate exception must fail CLOSED "
            "(defer job blocked), not OPEN (defer job admitted)."
        )

    @pytest.mark.asyncio
    async def test_neither_predicate_available_fails_closed(self):
        """When neither predicate can be evaluated, fail closed.

        Belt-and-suspenders: if both the job-repo path AND the
        task-repo path are unavailable (older wirings, partial
        init), the defer queue stays held back. The default
        conservative posture prevents premature defer admission
        during partial-init races.
        """
        # Build the service WITHOUT wiring the task predicate path;
        # only the (mocked) job predicate is consulted. The job
        # predicate returns a non-bool (loosely configured Mock) so
        # it does not short-circuit the gate.
        defer_queue = MagicMock()
        defer_queue.queue_id = "queue-composition-defer-2"
        defer_queue.project_id = "proj-composition-2"
        defer_queue.queue_type = QueueType.DEFER.value

        defer_job = MagicMock()
        defer_job.job_id = "job-composition-2"
        defer_job.queue_id = "queue-composition-defer-2"
        defer_job.project_id = "proj-composition-2"

        queue_repo = MagicMock()
        queue_repo._queue_map = {"queue-composition-defer-2": defer_queue}
        queue_repo.get = lambda qid: queue_repo._queue_map.get(qid)

        repo = MagicMock()
        # Job predicate returns a non-bool sentinel (loose Mock).
        repo.has_active_non_deferred_work = MagicMock(return_value="not-a-bool")
        service = JobQueueService(
            repository=repo,
            lock_manager=MagicMock(),
            queue_repo=queue_repo,
        )
        # No instance_manager set: getattr falls through to the
        # branch where ``task_repo is None``. After the non-bool job
        # result is ignored, the conservative fail-closed posture
        # sets ``non_defer_active = True``.

        result = await service._select_next_eligible_job(
            [defer_job], "proj-composition-2"
        )
        assert result is None, (
            "When neither predicate can be evaluated AND the job "
            "predicate returned a non-bool, the gate MUST fail closed "
            "(non_defer_active=True) — defer job blocked."
        )
