"""Task repository for worker pool tasks."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import delete as sql_delete, func, text
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select, col

from daemon.services.job_state_machine import InvalidTransitionError
from daemon.services.feature_flags import TURN_RECONCILER_DIRECT_WRITE_PARITY
from daemon.services.turn_transitions import (
    _TransitionContext,
    AbortTurn,
    CompleteTurn,
    RetryTurn,
)

from ..instance.models import Instance, InstanceStatus
from ..job_queue.models import AdmissionState, JobItem, active_admission_states_sql
from .models import Task, TaskStatus, TaskType

logger = logging.getLogger(__name__)


class DirectWriteError(RuntimeError):
    """Raised when a direct ``UPDATE task SET status=`` is attempted
    outside a named-transition context (Phase 4c, increment3-plan.md §6a).

    The chokepoint wrappers in this module (D8) close the
    ``complete_task`` / ``cancel_task`` / ``fail_task`` half of the
    "cascade forgot a mirror" bug class. The C7 write guard is the
    static defense against FUTURE direct writes bypassing the wrappers
    or any of the named transitions. When the guard is permanently
    enabled (Phase 4c), an UPDATE on ``task.status`` outside a
    ``TaskRepository._transition_context()`` block raises this error
    so the regression surfaces loudly in CI rather than silently
    regressing mirror consistency.

    Initial Phase 4a state: the guard is DISABLED — every direct
    UPDATE in this repo continues to work. The guard mechanism
    (thread-local via ``_TransitionContext``) is wired up but only
    enforced when ``TURN_RECONCILER_DIRECT_WRITE_PARITY`` is True.
    See increment3-plan.md §6b for the safe-rollout path.
    """


class TaskRepository:
    """Repository for Task CRUD operations with atomic claiming."""

    def __init__(self, engine: Engine, on_pending_task: Callable[[], None] | None = None):
        """Initialize repository with a database engine.

        Args:
            engine: SQLAlchemy database engine.
            on_pending_task: Optional callback to notify workers of new pending tasks.
        """
        self.engine = engine
        self._on_pending_task = on_pending_task

    # --------------------------------------------------------
    # D8 chokepoint helpers (Phase 4a, increment3-plan §6a/6b/6.5)
    # --------------------------------------------------------

    @classmethod
    def _transition_context(cls) -> _TransitionContext:
        """Return a thread-local context manager that opens a transition slot.

        The named transitions (``CompleteTurn``, ``AbortTurn``, etc.)
        call this internally so direct ``UPDATE task SET status=``
        statements executed DURING a transition's ``run(session)`` are
        permitted. Outside of such a context, direct status writes are
        FORBIDDEN by the C7 write guard (increment3-plan §6a). The guard
        is currently DISABLED by default — see
        ``TURN_RECONCILER_DIRECT_WRITE_PARITY`` (Phase 4c will enable it
        permanently).
        """
        return _TransitionContext()

    def _resolve_work_id(self, task_id: int) -> str | None:
        """Resolve a Task primary key to its ``work_id`` UUID.

        Returns ``None`` if the task does not exist. The named
        transitions are keyed on ``work_id`` (the cross-system
        correlation identifier) rather than the integer primary key —
        a non-existent task_id must short-circuit to ``None`` so the
        chokepoint wrappers (D8) preserve their existing
        "return None when task not found" semantic.

        Uses ``engine.connect()`` (NOT ``SQLModelSession``) so this
        SELECT does NOT acquire a write lock: SQLite's file-backed
        engines serialise writes aggressively across connections, and
        the prior ``SQLModelSession`` path held a connection while the
        wrapper's later ``engine.begin()`` opened a SECOND connection
        for the transition's UPDATE (regressed
        ``test_heartbeat_rejects_completed_task`` with "database is
        locked"). ``connect()`` releases the read-only connection at
        exit; PostgreSQL treats it as an auto-commit SELECT.
        """
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT work_id FROM task WHERE id = :id"),
                {"id": task_id},
            ).first()
        if row is None:
            return None
        # SQLAlchemy row; column access by key for portability across
        # SQLite and PostgreSQL.
        return row[0]

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def create(
        self,
        task_type: str,
        instance_id: str,
        message_id: str | None = None,
    ) -> Task:
        """Create a new task.

        Args:
            task_type: Type of the task (e.g., 'process_message').
            instance_id: Associated instance ID.
            message_id: Optional associated message ID.

        Returns:
            Created Task object.
        """
        with SQLModelSession(self.engine) as db_session:
            task = Task(
                task_type=task_type,
                instance_id=instance_id,
                message_id=message_id,
                status=TaskStatus.PENDING.value,
                created_at=datetime.now(timezone.utc),
            )
            db_session.add(task)
            db_session.commit()
            db_session.refresh(task)
            return task

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get(self, task_id: int) -> Task | None:
        """Get a task by ID.

        Args:
            task_id: Task ID.

        Returns:
            Task object or None if not found.
        """
        with SQLModelSession(self.engine) as db_session:
            return db_session.get(Task, task_id)

    def get_by_instance(self, instance_id: str) -> list[Task]:
        """Get all tasks for an instance.

        Args:
            instance_id: Instance ID.

        Returns:
            List of Task objects, newest first.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(Task)
                .where(Task.instance_id == instance_id)
                .order_by(col(Task.created_at).desc())
            )
            return list(db_session.exec(stmt))

    def get_by_message(self, message_id: str) -> Task | None:
        """Get task by associated message ID.

        Args:
            message_id: Message ID.

        Returns:
            Task object or None if not found.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(Task).where(Task.message_id == message_id)
            return db_session.exec(stmt).first()

    def get_by_work_id(self, work_id: str) -> Task | None:
        """Get a task by its stable cross-system work identifier.

        Phase 1 (Batch 2, 2026-06-27) of
        feature/virtual-job-management-surface. The ``work_id``
        column (UUID4 string, declared ``unique=True`` on the Task
        SQLModel) is the virtual job resolver's correlation key
        between a Task row and its corresponding JobItem row (or a
        logical work unit spanning both). Lookups against the unique
        index are O(log n) on both SQLite and PostgreSQL.

        Mirrors :meth:`get_by_message` (same SQLModelSession
        handling, same query style) so the data access layer stays
        uniform across all the by-foreign-key lookup helpers.

        Args:
            work_id: The UUID4 work identifier assigned at Task
                creation by the model's ``default_factory``.

        Returns:
            Task object or None if no task with that ``work_id``
            exists.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(Task).where(Task.work_id == work_id)
            return db_session.exec(stmt).first()

    def find_running_by_instance(self, instance_id: str) -> Task | None:
        """Return the first RUNNING ``task`` row for ``instance_id``,
        or None.

        Used by the cross-dispatcher pre-flight in
        ``MessageJobHandler.handle``: if a WorkerPool task is
        currently driving ``graph.astream`` for an instance, an
        incoming MESSAGE job for the same instance should back off
        rather than risk a concurrent stream. The Execution Gate's
        ``try_acquire`` is the authoritative safety net, so a
        non-None return is an optimisation, not a hard guarantee.

        Lifted from the handler so the data access pattern stays on
        the repository (consistent with the rest of the task
        access code).

        Args:
            instance_id: The langgraph thread_id / instance_id.

        Returns:
            The first RUNNING Task for the instance, or None.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(Task)
                .where(Task.instance_id == instance_id)
                .where(Task.status == TaskStatus.RUNNING.value)
            )
            return db_session.exec(stmt).first()

    def find_paused_or_running_by_instance(
        self, instance_id: str
    ) -> Task | None:
        """Return the first PAUSED or RUNNING ``PROCESS_MESSAGE`` ``task`` for ``instance_id``.

        Phase 2.5 (2026-06-27, D13 consumption-site rewrite). The
        root-vs-child routing primitive for
        ``InstanceManager.resume_processing_job``. Pre-D13, the same
        decision was made by looking up a PROCESSING ``JobItem`` via
        ``JobRepository.find_processing_message_jobs_by_instance`` —
        after D13, messages no longer create ``JobItem`` rows, so the
        routing decision moves onto the ``task`` table.

        Widened sister query to :meth:`find_running_by_instance` (the
        happy-path RUNNING-only lookup) and :meth:`has_inflight_task`
        (PENDING-or-RUNNING EXISTS check):

          * PAUSED tasks are included because a paused root instance is
            exactly the case where checkpoint resume must fire
            (``task.status`` was transitioned ``RUNNING → PAUSED`` by
            ``_pause_cascade_db_sync`` and will be transitioned back to
            ``PENDING`` by ``_resume_cascade_db_sync`` — the resume
            path needs to recognise that state and re-attach the
            graph driver).
          * PAUSED is intentionally NOT included by
            :meth:`has_inflight_task` — that helper is the
            ``job_continue`` concurrency gate, which must let PAUSED
            through (paused tasks are not actively driving the
            graph). The two semantics are deliberately different.

        Filters on ``task_type = PROCESS_MESSAGE`` so report tasks
        (``PROCESS_REPORT``) do not collide with the resume routing
        decision — a paused report task is irrelevant to whether the
        root instance has an in-flight graph turn to resume.

        Pattern (parameterised ``IN`` clause) matches the dual-driver
        approach in :meth:`has_inflight_task` — works on both SQLite
        and PostgreSQL without dialect branching.

        Args:
            instance_id: The langgraph thread_id / instance_id.

        Returns:
            The first PAUSED or RUNNING ``PROCESS_MESSAGE`` Task for
            the instance, or ``None``.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(Task)
                .where(Task.instance_id == instance_id)
                .where(
                    Task.status.in_([
                        TaskStatus.PAUSED.value,
                        TaskStatus.RUNNING.value,
                        # CANCELLED is included because ``_resume_cascade_db_sync``
                        # transitions PAUSED tasks to CANCELLED atomically with
                        # the instance resume (Phase 3 W2 fix). A CANCELLED task
                        # is the marker that this instance was paused-and-resumed
                        # and the resume driver ``resume_processing_job`` must run
                        # the root cleanup path (stale message → COMPLETED, then
                        # ``_resume_processing_background``). Without CANCELLED
                        # in the IN clause, ``resume_processing_job`` finds no
                        # task, misroutes the instance to the WorkerPool child
                        # path, and the stale PENDING/PROCESSING message from the
                        # paused turn wedges the parent at waiting_children after
                        # the final LLM turn (E2E test_pause_after_spawn_then_resume
                        # regression).
                        TaskStatus.CANCELLED.value,
                    ])
                )
                .where(Task.task_type == TaskType.PROCESS_MESSAGE.value)
                .order_by(col(Task.created_at).desc())
            )
            return db_session.exec(stmt).first()

    def find_resume_root_candidate_by_active_job(
        self, instance_id: str
    ) -> Task | None:
        """Detect the report-turn-pause orphan state and return the
        terminal backing ``PROCESS_MESSAGE`` Task (Bug A fallback).

        Phase 1 / Step B (Revision 2, 2026-08-01). The complementary
        primitive to :meth:`find_paused_or_running_by_instance`. When
        that method returns ``None`` AND there is still an ``active``
        JobItem on the instance, the most likely explanation is the
        Bug A incident state: a ``process_report`` turn is in flight,
        the original ``PROCESS_MESSAGE`` Task has reached a terminal
        state, and the ``active`` JobItem mirrors a work_id that
        ``find_paused_or_running_by_instance`` does not recognise.

        Returns the most-recent ``PROCESS_MESSAGE`` Task on the
        instance ONLY when ALL of these conditions hold:

          * No PAUSED / RUNNING / CANCELLED ``PROCESS_MESSAGE`` Task
            exists on the instance (i.e.
            :meth:`find_paused_or_running_by_instance` returns
            ``None``). A live or paused Task means the existing root
            route owns the instance and the fallback must NOT steal
            the routing decision.
          * The most-recent ``PROCESS_MESSAGE`` Task on the instance
            is in a TERMINAL state (``COMPLETED``, ``CANCELLED``, or
            ``FAILED``).
          * A non-deleted ``active`` JobItem exists on the instance
            whose ``job_id`` equals the terminal Task's ``work_id``
            (direct column-to-column join — NO JSON extraction). The
            JobItem is the orphaned mirror: the Task ended but the
            JobItem still holds a lock.

        Correlation axis: ``job_queue_items.job_id = task.work_id``
        (direct column join). The retry-path rationale for
        work_id-keyed correlation (instead of message_id-keyed)
        applies here too — see
        the shared in-flight JobItem predicate for the
        full discussion of the ``message_id`` → ``work_id``
        re-keying.

        Returns ``None`` when:

          * An ordinary child instance with no active JobItem
            (``find_paused_or_running_by_instance`` already handles
            the child branch via ``enqueue_message``).
          * The instance has a resumable Task (PAUSED / RUNNING /
            CANCELLED) — :meth:`find_paused_or_running_by_instance`
            owns the routing.
          * The most-recent PROCESS_MESSAGE Task is non-terminal
            (PENDING, RUNNING, PAUSED) — but those are excluded by
            the first condition above; the method short-circuits.
          * No PROCESS_MESSAGE Task exists at all.
          * No active JobItem correlates via ``work_id == job_id``
            (orphaned Task without an orphaned JobItem, or queued/
            done/deleted JobItem).
          * A ``PROCESS_REPORT`` Task is the most-recent row but a
            newer PROCESS_MESSAGE terminal Task also exists — the
            query orders by ``created_at DESC`` and filters on
            ``task_type = PROCESS_MESSAGE`` so report tasks never
            appear in the candidate set.

        Args:
            instance_id: The langgraph thread_id / instance_id.

        Returns:
            The terminal backing ``PROCESS_MESSAGE`` Task (with its
            stable ``work_id``), or ``None`` if no such candidate
            exists.

        Dual-driver: the SQL uses direct column-to-column joins
        (``task.work_id = job_queue_items.job_id``) without JSON
        extraction, ``rowid``, or other backend-only operators. Works
        identically on SQLite and PostgreSQL.
        """
        # First, defer to the existing root-route primitive. If a
        # resumable Task exists, the regular route owns the instance
        # and the fallback MUST return None.
        if self.find_paused_or_running_by_instance(instance_id) is not None:
            return None

        # No PAUSED / RUNNING / CANCELLED PROCESS_MESSAGE Task.
        # Look for the most-recent PROCESS_MESSAGE Task on the
        # instance (any status — we'll filter below) plus a
        # correlated non-deleted active JobItem via work_id ==
        # job_id. The terminal-status filter applies AFTER the
        # correlation — a non-terminal row with no correlated
        # JobItem is also a non-match.
        #
        # CANCELLED is intentionally EXCLUDED from the candidate
        # set. The existing ``find_paused_or_running_by_instance``
        # treats CANCELLED as a "resumable marker" (the
        # pause-cascade transitions PAUSED → CANCELLED), and the
        # ``_resume_cascade_db_sync`` flow expects the existing
        # route to own CANCELLED Tasks. Including CANCELLED in
        # the fallback would steal routing from the cascade-
        # resume path. CANCELLED can also come from ``schedule_retry``
        # (Phase 3 retry path) — in that case the active JobItem
        # correlates via ``work_id`` and the retry child Task
        # (PENDING) drives the actual work. The fallback does NOT
        # service the retry path; retry children are claimed by
        # the regular ``claim_pending_task`` path (FIFO recovery).
        terminal_statuses = [
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
        ]
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(Task)
                .where(Task.instance_id == instance_id)
                .where(Task.task_type == TaskType.PROCESS_MESSAGE.value)
                .where(Task.status.in_(terminal_statuses))
                .where(
                    # EXISTS a non-deleted active JobItem whose
                    # job_id matches this Task's work_id (direct
                    # column-to-column join — NO JSON extraction).
                    select(JobItem.job_id)
                    .where(JobItem.job_id == Task.work_id)
                    .where(JobItem.admission_state == AdmissionState.ACTIVE.value)
                    .where(JobItem.deleted_at.is_(None))
                    .exists()
                )
                .order_by(col(Task.created_at).desc())
            )
            return db_session.exec(stmt).first()

    def has_inflight_task(self, instance_id: str) -> bool:
        """Return True if any PENDING or RUNNING ``task`` row exists for ``instance_id``.

        Phase 1 (2026-06-24, report-lane decoupling). Used by
        ``daemon/api.py`` crash-recovery to decide whether to
        finalize a parent directly (no in-flight task will drive
        the natural path) or defer to the natural path (a report
        Task is still PENDING or RUNNING and will finalize the
        parent itself when it ends).

        Phase 2.5 (2026-06-27, D13 consumption-site rewrite). Also
        used by ``tools/job_queue.py:job_continue`` as the
        DB-level concurrency gate (replaces the pre-D13
        ``find_processing_message_jobs_by_instance`` check, which
        became a no-op after MESSAGE ``JobItem`` creation was
        eliminated in Phase 2).

        Sister query to :meth:`find_running_by_instance` —
        widened to include PENDING so a not-yet-claimed report
        Task is also treated as in-flight. One indexed EXISTS
        against ``ix_task_instance_id``.

        PAUSED is intentionally NOT included: paused tasks are not
        actively driving the graph — the per-instance guard should
        not block a ``job_continue`` call against a paused instance
        (the user has explicitly paused it and is now choosing to
        unpause via a separate flow). This is the inverse of
        :meth:`find_paused_or_running_by_instance`, which DOES
        include PAUSED for the resume-routing primitive.

        Dual-driver SQL: pure SQLModel via ``session.scalar`` —
        the parameterized ``IN (:pending, :running)`` works on
        both SQLite and PostgreSQL (matches the pattern in
        ``has_pending_tasks_blocked_by_busy_instance`` /
        ``find_cancellable_tasks``).

        Args:
            instance_id: The instance ID to check.

        Returns:
            True if any PENDING or RUNNING task exists for the
            instance; False otherwise (only terminal tasks, or
            none at all).
        """
        with self.engine.begin() as conn:
            stmt = text("""
                SELECT 1 FROM task
                WHERE instance_id = :instance_id
                AND status IN (:status_pending, :status_running)
                LIMIT 1
            """)
            row = conn.execute(stmt, {
                "instance_id": instance_id,
                "status_pending": TaskStatus.PENDING.value,
                "status_running": TaskStatus.RUNNING.value,
            }).fetchone()
            return row is not None

    def list_running_tasks(self) -> list[Task]:
        """Return every RUNNING ``task`` row.

        Used by the periodic drift reconciler (F5/F10, Phase 3 of
        defer-seam bugfix) to find RUNNING tasks whose backing JobItem
        already terminated (F10 zombie) or whose heartbeat is NULL (P1
        pattern deadlock candidate). Unlike ``find_stale_running_tasks``,
        this query is NOT filtered by a heartbeat age threshold — the
        reconciler needs the full set so it can apply its own age
        predicates (``min_pending_age_seconds`` etc.) per case.

        Ordered by ``created_at`` ascending so the oldest RUNNING
        tasks are reconciled first — they are most likely to be the
        drift victims.

        Returns:
            List of RUNNING ``Task`` rows.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(Task)
                .where(Task.status == TaskStatus.RUNNING.value)
                .order_by(col(Task.created_at).asc())
            )
            return list(db_session.exec(stmt))

    def list_pending_tasks_older_than(self, age_seconds: int) -> list[Task]:
        """Return PENDING ``task`` rows whose ``created_at`` is older than
        ``age_seconds`` ago AND whose ``last_heartbeat_at`` IS NULL.

        Used by the periodic drift reconciler (F5, P1-pattern deadlock
        detection) to find tasks that have been PENDING without a
        heartbeat for a long time — the canonical signature of a
        task that was never claimed by a worker (P1 self-deadlock).
        The NULL-heartbeat filter ensures freshly-enqueued tasks that
        haven't been picked up yet are NOT considered drift
        (heartbeat for PENDING tasks is NULL by design — only RUNNING
        tasks heart-beat; a PENDING+NULL-heartbeat is only stale if
        it's been waiting longer than the threshold).

        Args:
            age_seconds: Minimum age in seconds for a task to be
                considered drift-eligible.

        Returns:
            List of PENDING ``Task`` rows older than ``age_seconds``
            with a NULL ``last_heartbeat_at``.
        """
        threshold = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(Task)
                .where(Task.status == TaskStatus.PENDING.value)
                .where(Task.last_heartbeat_at.is_(None))
                .where(Task.created_at < threshold)
                .order_by(col(Task.created_at).asc())
            )
            return list(db_session.exec(stmt))

    def reconcile_turn_mirror(self, work_id: str) -> dict[str, Any]:
        """Reconcile one Task's cross-system turn mirrors atomically.

        The Task row is authoritative.  Its status and secondary link keys are
        read once, and every mutation is guarded by that snapshot so a racing
        lifecycle transition turns the reconciliation writes into no-ops.
        """
        terminal_statuses = (
            TaskStatus.COMPLETED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.FAILED.value,
        )
        inflight_statuses = (
            TaskStatus.PENDING.value,
            TaskStatus.RUNNING.value,
            TaskStatus.PAUSED.value,
        )
        updated_counts: dict[str, int] = {}
        drift_flags: dict[str, Any] = {}

        with self.engine.begin() as conn:
            task_sql = """
                SELECT id, status, message_id, instance_id
                FROM task
                WHERE work_id = :work_id
            """
            if self.engine.dialect.name == "postgresql":
                task_sql += " FOR UPDATE"
            snapshot = conn.execute(text(task_sql), {"work_id": work_id}).mappings().first()

            found = snapshot is not None
            snapshot_status = snapshot["status"] if snapshot else None
            result = {
                "work_id": work_id,
                "found": found,
                "snapshot_status": snapshot_status,
                "updated_counts": updated_counts,
                "drift_flags": drift_flags,
            }

            terminal = not found or snapshot_status in terminal_statuses
            if found and snapshot_status not in terminal_statuses + inflight_statuses:
                raise ValueError(
                    f"Unknown Task status for turn mirror reconciliation: {snapshot_status}"
                )
            terminal_reason = snapshot_status if found else "orphaned_no_task"
            params = {
                "work_id": work_id,
                "task_exists": found,
                "snapshot_status": snapshot_status,
                "task_status": snapshot_status,
                "terminal": terminal,
                "terminal_reason": terminal_reason,
                "task_id": snapshot["id"] if snapshot else None,
                "task_message_id": snapshot["message_id"] if snapshot else None,
                "task_instance_id": snapshot["instance_id"] if snapshot else None,
                "status_pending": TaskStatus.PENDING.value,
                "status_running": TaskStatus.RUNNING.value,
                "status_paused": TaskStatus.PAUSED.value,
                "status_waiting_children": InstanceStatus.WAITING_CHILDREN.value,
            }
            snapshot_guard = """
                (:task_exists = false OR EXISTS (
                    SELECT 1 FROM task
                    WHERE work_id = :work_id AND status = :snapshot_status
                ))
            """

            updated_counts["job_queue_items"] = conn.execute(
                text(f"""
                    UPDATE job_queue_items
                    SET admission_state = CASE
                            WHEN :terminal AND NOT EXISTS (
                                SELECT 1 FROM instances i
                                WHERE i.instance_id = :task_instance_id
                                  AND i.status = :status_waiting_children
                            ) THEN 'done'
                            ELSE admission_state
                        END,
                        terminal_reason = CASE
                            WHEN :terminal AND NOT EXISTS (
                                SELECT 1 FROM instances i
                                WHERE i.instance_id = :task_instance_id
                                  AND i.status = :status_waiting_children
                            ) THEN :terminal_reason
                            ELSE terminal_reason
                        END,
                        failed_at = CASE
                            WHEN :task_status IN ('failed', 'cancelled')
                                 AND NOT EXISTS (
                                     SELECT 1 FROM instances i
                                     WHERE i.instance_id = :task_instance_id
                                       AND i.status = :status_waiting_children
                                 )
                            THEN COALESCE(failed_at, CAST(CURRENT_TIMESTAMP AS TEXT))
                            ELSE failed_at
                        END
                    WHERE job_id = :work_id AND {snapshot_guard}
                """),
                params,
            ).rowcount

            updated_counts["job_locks"] = conn.execute(
                text(f"""
                    DELETE FROM job_locks
                    WHERE job_id = :work_id
                      AND :terminal
                      AND {snapshot_guard}
                      AND NOT EXISTS (
                          SELECT 1 FROM instances i
                          WHERE i.instance_id = :task_instance_id
                            AND i.status = :status_waiting_children
                      )
                """),
                params,
            ).rowcount

            updated_counts["message_queue"] = conn.execute(
                text(f"""
                    UPDATE message_queue
                    SET status = 'completed',
                        processing_task_id = NULL,
                        completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)
                    WHERE message_id = :task_message_id
                      AND status IN ('pending', 'ready', 'processing', 'retrying')
                      AND :terminal
                      AND {snapshot_guard}
                """),
                params,
            ).rowcount

            updated_counts["dependency_watchers"] = conn.execute(
                text(f"""
                    UPDATE dependency_watchers
                    SET state = 'CANCELLED'
                    WHERE source_task_id = CAST(:task_id AS TEXT)
                      AND state = 'PENDING'
                      AND :terminal
                      AND {snapshot_guard}
                      AND NOT EXISTS (
                          SELECT 1 FROM task AS target_task
                          WHERE target_task.instance_id = dependency_watchers.target_instance_id
                            AND target_task.status IN (
                                :status_pending, :status_running, :status_paused
                            )
                      )
                """),
                params,
            ).rowcount

            updated_counts["report_injections"] = conn.execute(
                text(f"""
                    UPDATE report_injections
                    SET state = 'TASK_DELIVERED',
                        delivered_at = COALESCE(
                            delivered_at, CAST(CURRENT_TIMESTAMP AS TEXT)
                        )
                    WHERE report_message_id = :task_message_id
                      AND state = 'PENDING'
                      AND :terminal
                      AND {snapshot_guard}
                """),
                params,
            ).rowcount

            if found:
                instance_row = conn.execute(
                    text("""
                        SELECT i.status,
                               EXISTS (
                                   SELECT 1 FROM task t
                                   WHERE t.instance_id = :task_instance_id
                                     AND t.status IN (
                                         :status_pending, :status_running, :status_paused
                                     )
                               ) AS has_inflight
                        FROM instances i
                        WHERE i.instance_id = :task_instance_id
                    """),
                    params,
                ).mappings().first()
                if (
                    instance_row
                    and instance_row["status"] == InstanceStatus.RUNNING.value
                    and not bool(instance_row["has_inflight"])
                ):
                    drift_flags["instance_running_without_inflight_task"] = {
                        "instance_id": params["task_instance_id"],
                        "status": instance_row["status"],
                    }
                    logger.warning(
                        "Turn mirror drift: instance %s is running without in-flight tasks "
                        "(work_id=%s)",
                        params["task_instance_id"],
                        work_id,
                    )

            updated_counts["job_watchers"] = conn.execute(
                text("""
                    DELETE FROM job_watchers
                    WHERE job_id = :work_id
                      AND NOT EXISTS (
                          SELECT 1 FROM task WHERE work_id = :work_id
                      )
                """),
                params,
            ).rowcount

            invariant = conn.execute(
                text("""
                    SELECT admission_state,
                           (admission_state = 'active') AS is_active,
                           EXISTS (
                               SELECT 1 FROM job_locks jl
                               WHERE jl.job_id = job_queue_items.job_id
                           ) AS has_lock
                    FROM job_queue_items
                    WHERE job_id = :work_id
                """),
                {"work_id": work_id},
            ).mappings().first()
            if invariant and bool(invariant["is_active"]) != bool(invariant["has_lock"]):
                error = InvalidTransitionError(
                    work_id,
                    invariant["admission_state"],
                    "active_with_lock",
                )
                error.args = (
                    f"Turn mirror invariant failed for work_id={work_id}: "
                    f"admission_state={invariant['admission_state']}, "
                    f"has_lock={bool(invariant['has_lock'])}",
                )
                raise error

            return result

    # --------------------------------------------------------
    # CLAIM (Atomic)
    # --------------------------------------------------------

    def claim_pending_task(
        self,
        worker_id: str,
    ) -> Task | None:
        """Atomically claim the next eligible pending task.

        Only claims tasks that are ready (no backoff delay remaining).
        Uses UPDATE-RETURNING pattern for SQLite compatibility.
        Only one worker can claim a task at a time.

        Per-instance guard: a pending task is only claimable if no other task
        for the same ``instance_id`` is currently ``RUNNING`` AND no MESSAGE
        job for the same instance is *actively* ``PROCESSING`` (i.e. the
        job is still driving ``graph.astream`` for this instance, not just
        sitting in PROCESSING because the instance is WAITING_CHILDREN).
        This prevents two workers from concurrently processing tasks for the
        same langgraph thread_id, which would race on ``graph.astream`` and
        shadow channel writes in the Postgres checkpointer.

        Cross-system coordination uses
        :meth:`_active_jobitem_with_inflight_task_sql`: an active JobItem
        blocks only while its backing Task is PENDING, RUNNING, or PAUSED.
        Orphan cleanup belongs to ``reconcile_turn_mirror``. The retained
        WAITING_CHILDREN exception prevents child-report deadlock.

        ``deleted_at IS NULL`` matches the canonical job-side query
        (a soft-deleted PROCESSING job never auto-completes and would
        otherwise permanently block the instance).

        Heartbeat init: ``last_heartbeat_at`` is set to the same value as
        ``started_at`` on claim, so the recovery service can distinguish
        a freshly-claimed task (heartbeat fresh) from a crashed one
        (heartbeat stale). The worker's heartbeat thread keeps updating
        ``last_heartbeat_at`` every ``task_heartbeat_interval_seconds``
        while the task is in flight; the recovery predicate compares
        ``last_heartbeat_at`` to the threshold.

        Defer queue idle gate (Phase 3 Part B2, 2026-06-27):
        deferred tasks (``is_deferred=True``) are gated on the
        project having no active non-deferred work, mirroring the job
        defer queue's ``count_active_jobs_in_non_defer_queues == 0``
        semantics. The gate is folded INTO the atomic claim's inner
        SELECT (NOT a separate Python pre-check) so it shares the
        same WHERE-clause evaluation as the pause gate, per-instance
        guard, and cross-system guard — closing the deterministic
        starvation window that the prior pre-check had when the
        oldest PENDING task was deferred AND its instance was paused
        or terminated. Non-deferred tasks bypass the gate entirely;
        only deferred candidates are held back while non-deferred
        work is active in the same project.

        Args:
            worker_id: ID of the worker claiming the task.

        Returns:
            Claimed Task object or None if no pending tasks ready.
        """
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S.%f") + now.strftime("%z")

        # Cross-system guard scope: the report lane (PROCESS_REPORT)
        # bypasses the job-coordination exclusion entirely — reports
        # have no JobItem to collide with, so the original guard is
        # irrelevant for them. We pass the literal directly (a fixed
        # enum string, not user input) so the predicate is identical
        # on both backends.

        with self.engine.begin() as conn:
            # The defer queue idle gate is folded INTO the atomic
            # claim's inner SELECT (not a separate Python pre-check).
            # Folding the gate into the same SQL statement closes the
            # deterministic starvation window that the prior
            # pre-check had: when the oldest PENDING task was deferred
            # AND its instance was paused/terminated, the pre-check
            # (which did NOT apply the pause gate) would still pick the
            # deferred task as the candidate, see ``is_deferred=True``,
            # find the project's active non-deferred count > 0, and
            # return None for the entire method — starving a younger,
            # non-deferred, eligible task. With the gate in the same
            # SQL, the pause gate and the defer gate evaluate together
            # for every candidate: a paused deferred task is filtered
            # out by the pause gate, and the next eligible non-deferred
            # task is selected.
            #
            # The defer predicate uses a correlated subquery on
            # ``task.instance_id`` (the candidate row's instance) to
            # resolve the candidate's project_id via ``instances``.
            # This preserves the original gate's project-scoping and
            # NULL-fallback semantics:
            #   * A non-deferred candidate (``is_deferred = false``)
            #     bypasses the gate entirely.
            #   * A deferred candidate whose instance has no
            #     ``instances`` row resolves to project_id = NULL;
            #     ``i2.project_id = NULL`` is UNKNOWN in SQL, so
            #     EXISTS returns FALSE and the candidate is allowed —
            #     the original "no project context → allow" fallback.
            stmt = text(f"""
                UPDATE task
                SET status = :status_running,
                    worker_id = :worker_id,
                    started_at = :started_at,
                    last_heartbeat_at = :started_at
                WHERE id = (
                    SELECT id FROM task
                    WHERE status = :status_pending
                    AND (next_retry_at IS NULL OR next_retry_at <= :now_str)
                    -- Defer queue idle gate (Phase 3 Part B2
                    -- starvation-fix revision, 2026-06-27). Held back
                    -- when this candidate is deferred AND there is
                    -- active non-deferred work in the same project.
                    -- Evaluated in the same SQL as the pause gate,
                    -- per-instance guard, and cross-system guard so a
                    -- paused deferred task can never starve a younger
                    -- non-deferred task.
                    AND NOT (
                        task.is_deferred = :is_deferred_true
                        AND EXISTS (
                            SELECT 1 FROM task t2
                            JOIN instances i2
                                ON t2.instance_id = i2.instance_id
                            WHERE t2.status IN (:status_pending, :status_running, :status_paused)
                              AND t2.is_deferred = :is_deferred_false
                              AND i2.project_id = (
                                  SELECT i_cand.project_id
                                  FROM instances i_cand
                                  WHERE i_cand.instance_id = task.instance_id
                              )
                        )
                    )
                    -- Background queue idle gate (Phase 3 background
                    -- seam, 2026-07-14). Held back when this candidate
                    -- is a background task AND there is active
                    -- non-background work ANYWHERE in the system. Scope
                    -- is system-wide (NO project_id filter) — this is
                    -- the documented asymmetry from the defer gate:
                    -- background work waits for ALL projects to be idle
                    -- on their non-background lanes, not just the
                    -- candidate's project. Mirrors
                    -- :meth:`has_active_non_background_work` so the
                    -- claim path and the admission probe cannot
                    -- disagree.
                    --
                    -- Defer-leak bug fix (2026-07-23): the predicate
                    -- fix (Phase 2) removed ``is_deferred = false``
                    -- from the standalone
                    -- ``has_active_non_background_work`` so defer
                    -- work IS counted as non-background work. This
                    -- inline copy of the gate had the same defect
                    -- (the ``t3.is_deferred = false`` clause was
                    -- removed from the inner EXISTS below) which
                    -- made defer tasks invisible to the background
                    -- gate via the atomic claim path — admitting
                    -- background work while defer tasks were active.
                    -- Removed here for parity with the predicate;
                    -- the ``is_deferred_false`` bind stays because
                    -- the DEFER gate above still uses it.
                    AND NOT (
                        task.is_background = :is_background_true
                        AND EXISTS (
                            SELECT 1 FROM task t3
                            JOIN instances i3
                                ON t3.instance_id = i3.instance_id
                            WHERE t3.status IN (:status_pending, :status_running, :status_paused)
                              AND t3.is_background = :is_background_false
                            -- Deliberately NO project_id scoping — the
                            -- background gate is system-wide by
                            -- design. A background candidate only
                            -- claims when every project is idle on
                            -- its non-background lanes.
                        )
                    )
                    -- Queue-awareness guard (FIFO concurrency fix,
                    -- 2026-07-26). The Task-claim path is
                    -- queue-concurrency-blind by default: a Task's
                    -- PENDING status is independent of whether its
                    -- linked JobItem holds a ``concurrency_limit``
                    -- slot. Without this guard, a FIFO-denied
                    -- JobItem (admission_state='queued', slot
                    -- denied by ``start_job_atomic_with_lock``)
                    -- would let the Task race the slot and bypass
                    -- the queue's concurrency enforcement.
                    --
                    -- Contract: a Task is claimable only when its
                    -- linked JobItem (matched via
                    -- ``job_queue_items.job_id == task.work_id``)
                    -- is ``admission_state='active'`` (slot held)
                    -- or there is no linked JobItem at all (the
                    -- legacy / report-task path). A queued linked
                    -- JobItem means the slot is denied — the Task
                    -- MUST wait for the slot to free up (the
                    -- sibling job to complete, which triggers
                    -- ``notify_work`` and re-evaluates the claim).
                    --
                    -- A queued linked JobItem still enforces FIFO
                    -- admission. Cross-system orphan cleanup is handled by
                    -- reconcile_turn_mirror rather than this task-scoped gate.
                    AND NOT EXISTS (
                        SELECT 1 FROM job_queue_items _qi
                        WHERE _qi.job_id = task.work_id
                          AND _qi.admission_state = '{AdmissionState.QUEUED.value}'
                          AND _qi.deleted_at IS NULL
                    )
                    AND instance_id NOT IN (
                        SELECT instance_id FROM task
                        WHERE status = :status_running_guard
                    )
                    AND instance_id NOT IN (
                        -- Phase 1 (2026-06-24, report-lane decoupling):
                        -- Pause gate. Excludes instances whose status is
                        -- PAUSED or TERMINATED for ALL task types. Before
                        -- this change, pause protection for report tasks
                        -- was accidental — it fell out of the cross-system
                        -- job guard (the instance's status was checked
                        -- inside that guard against ``waiting_children``,
                        -- which is orthogonal to pause). Now that the
                        -- cross-system guard is scoped to PROCESS_MESSAGE
                        -- only (see below), pause protection for reports
                        -- would have been lost. This explicit gate
                        -- restores it uniformly for every task type —
                        -- user messages and reports alike — and mirrors
                        -- the existing recovery exclusions in
                        -- ``find_stale_running_tasks`` /
                        -- ``find_cancellable_tasks`` (parameterized
                        -- ``IN (status_paused, status_terminated)`` works
                        -- on both SQLite and PostgreSQL).
                        SELECT instance_id FROM instances
                        WHERE status IN (:status_paused, :status_terminated)
                    )
                    AND (
                        -- Cross-system guard: JOB COORDINATION ONLY —
                        -- scoped to ``process_message`` tasks. Report
                        -- tasks (``process_report`` and any future
                        -- non-message types) bypass the guard entirely.
                        -- A report's ``message_id`` matches no
                        -- ``JobItem``, so the guard would (a) block the
                        -- report's claim forever (the report Task waits
                        -- for the job that never references its
                        -- ``message_id`` to terminate) and (b) orphan
                        -- the report — the report is the only thing
                        -- that would deliver the child's result to the
                        -- parent. Bypassing the guard restores
                        -- independent-turn delivery: each child
                        -- completion becomes its own parent graph turn.
                        --
                        -- The per-instance serialization guard above
                        -- (one RUNNING task per instance) still applies
                        -- — that is the only invariant reports need.
                        --
                        -- D13: the previous ``j.job_type = 'message'``
                        -- filter inside this subquery is removed —
                        -- messages no longer create ``JobItem`` rows
                        -- (see InstanceMessagingService.enqueue_message).
                        -- The subquery now checks for ANY processing
                        -- ``JobItem`` for the instance; after D13 these
                        -- are exclusively TASK-type dispatch-queue jobs,
                        -- so blocking on them is correct (they drive
                        -- instance spawn + message enqueue and must
                        -- complete before a second message can be
                        -- processed for the same instance).
                        task_type != :process_message_type
                        OR instance_id NOT IN (
                            SELECT j.instance_id FROM job_queue_items j
                            LEFT JOIN instances i ON j.instance_id = i.instance_id
                            -- Phase 3 admission-decision migration: filter on
                            -- admission_state IN ('queued', 'active') instead of
                            -- ``status = 'processing'``. The legacy predicate
                            -- excluded PAUSED jobs even though they still hold
                            -- the lock (admission_state='active' under the new
                            -- model — see Plan §8.1 / ``paused`` admission handling).
                            -- The IN-list also covers the B1 single-transaction
                            -- window where a job briefly sits in
                            -- admission_state='queued' while its lock is held
                            -- (mirrors ``_ACTIVE_JOB_IDS_SUBQUERY`` in
                            -- lock_repository.py).
                            WHERE j.admission_state IN {active_admission_states_sql()}
                              AND j.instance_id IS NOT NULL
                              AND j.deleted_at IS NULL
                              AND {self._active_jobitem_with_inflight_task_sql("j")}
                        )
                    )
                  ORDER BY created_at ASC LIMIT 1
                )
                AND status = :status_pending
                RETURNING *
            """)
            row = conn.execute(stmt, {
                "status_running": TaskStatus.RUNNING.value,
                "worker_id": worker_id,
                "started_at": now,
                "status_pending": TaskStatus.PENDING.value,
                "status_running_guard": TaskStatus.RUNNING.value,
                "status_waiting_children": InstanceStatus.WAITING_CHILDREN.value,
                "status_paused": TaskStatus.PAUSED.value,
                "status_terminated": InstanceStatus.TERMINATED.value,
                "process_message_type": TaskType.PROCESS_MESSAGE.value,
                "now_str": now_str,
                # Defer queue idle gate (Phase 3 Part B2). Python
                # booleans so the comparison works on both SQLite
                # (INTEGER 0/1) and PostgreSQL (BOOLEAN false/true) —
                # matches the dual-driver pattern used elsewhere in
                # this repository (e.g. schedule_retry).
                "is_deferred_true": True,
                "is_deferred_false": False,
                # Background queue idle gate (Phase 3 background seam,
                # 2026-07-14). Same Python-boolean dual-driver pattern
                # as the defer gate above. The background predicate is
                # folded into the SAME atomic claim's inner SELECT (not
                # a Python pre-check) so it shares the pause gate,
                # per-instance guard, cross-system guard, and defer
                # gate evaluation — closing the deterministic starvation
                # window that the prior pre-check pattern had for
                # defer. The same anti-starvation guarantee applies to
                # background: a paused background task is filtered out
                # by the pause gate, and the next eligible non-
                # background task is selected instead.
                "is_background_true": True,
                "is_background_false": False,
            }).fetchone()

            if row is None:
                return None

            claimed_task = self._row_to_task(row)

        # Additive reconciler pass (Site 1: claim). Runs AFTER the claim
        # commits so the Task snapshot the reconciler reads reflects the
        # newly-claimed row. CATCH per increment1-plan §5.1 — blocking the
        # claim on a mirror desync would deadlock the worker pool, because
        # the claim path is the only path that can eventually retry the
        # reconciliation. The reconciler's writes already ran (or
        # correctly no-op'd); the exception is diagnostic. Log at WARNING
        # with ``work_id`` so desyncs are diagnosable in production
        # telemetry.
        # Claim-time reconciliation is defense-in-depth; the primary orphan
        # prevention is via cascade + periodic sweep (reconcile_drift_states).
        # If an orphaned JobItem blocks the claim query, the reconciler never
        # fires here — but the periodic sweep catches any orphans the
        # event-driven paths miss.
        try:
            self.reconcile_turn_mirror(claimed_task.work_id)
        except InvalidTransitionError as e:
            logger.warning(
                "Reconciler invariant violation after claim for work_id=%s: %s",
                claimed_task.work_id,
                e,
            )

        return claimed_task

    def _active_jobitem_with_inflight_task_sql(self, job_alias: str) -> str:
        """Single-source-of-truth simplified cross-system predicate.

        Post-Increment 2, orphan reconciliation belongs to
        ``reconcile_turn_mirror``. Active JobItems block only while their
        backing Task is in flight. The WAITING_CHILDREN exception is retained
        because those JobItems intentionally semaphore child-completion reports.
        Both claim and busy-probe gates call this helper (P1/F11 invariant).
        """
        return (
            f"EXISTS (\n"
            f"    SELECT 1 FROM task t\n"
            f"    WHERE t.work_id = {job_alias}.job_id\n"
            f"      AND t.status IN (:status_pending, :status_running, :status_paused)\n"
            f")\n"
            f"AND (i.status IS NULL OR i.status != :status_waiting_children)"
        )

    def requeue_task_with_backoff(
        self, task_id: int, min_delay_seconds: float = 0.5, max_delay_seconds: float = 2.0
    ) -> Task | None:
        """Re-queue a RUNNING task back to PENDING with a jittered backoff.

        Like ``requeue_task`` but sets ``next_retry_at`` to
        ``now + uniform(min, max)`` so the task is not eligible for
        re-claim until that time. ``claim_pending_task`` already
        filters on ``next_retry_at <= now_str``, so the worker poll
        will see the task as "not yet ready" and move on to a
        different pending task. This prevents a tight CPU spin when
        a sibling MESSAGE job holds the lease for minutes at a
        time and the worker would otherwise re-claim the same task
        immediately, re-run, hit contention again, and re-queue —
        looping at the speed of the DB.

        The delay is stored as a string in the same format
        ``claim_pending_task`` parses (ISO-8601 with fractional
        seconds and timezone offset).

        Atomicity: same as ``requeue_task`` — the UPDATE is
        conditional on ``status='running'``. A task that was
        concurrently completed/failed/cancelled is left alone.

        Args:
            task_id: The Task ID to re-queue.
            min_delay_seconds: Lower bound for the jittered delay.
            max_delay_seconds: Upper bound for the jittered delay.

        Returns:
            The updated Task in PENDING status, or None if the task
            was not in RUNNING status when we tried to re-queue.
        """
        import random
        delay = random.uniform(min_delay_seconds, max_delay_seconds)
        next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        next_retry_at_str = (
            next_retry_at.strftime("%Y-%m-%dT%H:%M:%S.%f")
            + next_retry_at.strftime("%z")
        )
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    UPDATE task
                    SET status = :status_pending,
                        worker_id = NULL,
                        started_at = NULL,
                        last_heartbeat_at = NULL,
                        next_retry_at = :next_retry_at
                    WHERE id = :task_id
                      AND status = :status_running
                    RETURNING id
                    """
                ),
                {
                    "task_id": task_id,
                    "status_pending": TaskStatus.PENDING.value,
                    "status_running": TaskStatus.RUNNING.value,
                    "next_retry_at": next_retry_at_str,
                },
            )
            row = result.first()
            if row is None:
                return None
            # Don't notify workers — the task is intentionally not
            # claimable yet, and a wake-up would just be ignored
            # (claim_pending_task will skip it on next_retry_at).
            # Notify the next time something else makes the task
            # claimable (e.g. a manual clear, a sibling completion).
            return self.get(task_id)

    def update_heartbeat(self, task_id: int) -> bool:
        """Update a task's heartbeat timestamp.

        Called by the worker's heartbeat thread every
        ``task_heartbeat_interval_seconds`` while the task is being
        processed. The recovery service reads this column to distinguish
        a live task (heartbeat fresh) from a crashed one (heartbeat stale).

        The UPDATE is atomic and conditional on status='running' — a task
        that has been CANCELLED or COMPLETED by recovery while the
        heartbeat thread was racing will not have its heartbeat
        refreshed, and the worker will see the cancellation on its
        next read of the task.

        Args:
            task_id: ID of the task to heartbeat.

        Returns:
            True if the heartbeat was applied, False if the task no
            longer exists or is no longer RUNNING.
        """
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE task
                    SET last_heartbeat_at = :now
                    WHERE id = :id
                    AND status = :status_running
                """),
                {
                    "now": now,
                    "id": task_id,
                    "status_running": TaskStatus.RUNNING.value,
                },
            )
            return result.rowcount > 0

    def backfill_heartbeats(self) -> int:
        """Backfill last_heartbeat_at = started_at for tasks that lack it.

        Run on startup so that tasks inserted by older code paths
        (before last_heartbeat_at existed) aren't immediately flagged as
        stale by the recovery service. Also covers the rare case of a
        daemon restart while tasks are in flight.

        Returns:
            Number of rows backfilled.
        """
        with self.engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE task
                    SET last_heartbeat_at = COALESCE(started_at, created_at)
                    WHERE last_heartbeat_at IS NULL
                    AND status = :status_running
                """),
                {"status_running": TaskStatus.RUNNING.value},
            )
            return result.rowcount

    def _row_to_task(self, row) -> Task:
        """Convert a database row to a Task object.

        Direct attribute access: every column referenced here MUST be
        present on ``row``. The prior ``hasattr`` fallbacks for
        ``work_id`` (random UUID) and ``is_deferred`` (False) silently
        masked migration gaps — a row from a missing-column DDL would
        yield a Task with a random work_id, breaking the
        unique-indexed virtual-job correlation. Letting ``AttributeError``
        surface makes migration gaps immediately debuggable. The
        ``bool()`` coercion on ``cancel_requested`` / ``retry_scheduled``
        is kept because INTEGER 0/1 (SQLite) and BOOLEAN false/true
        (PostgreSQL) both coerce to proper Python bools inside
        ``Task()``'s Pydantic validator; tests rely on ``is True`` /
        ``is False`` for those flags.

        Args:
            row: Raw database row from UPDATE-RETURNING query.

        Returns:
            Task object.
        """
        return Task(
            id=row.id,
            task_type=row.task_type,
            instance_id=row.instance_id,
            message_id=row.message_id,
            work_id=row.work_id,
            status=row.status,
            worker_id=row.worker_id,
            retry_count=row.retry_count if hasattr(row, 'retry_count') else 0,
            next_retry_at=row.next_retry_at if hasattr(row, 'next_retry_at') else None,
            cancel_requested=bool(row.cancel_requested) if hasattr(row, 'cancel_requested') else False,
            cancel_requested_at=row.cancel_requested_at if hasattr(row, 'cancel_requested_at') else None,
            retry_scheduled=bool(row.retry_scheduled) if hasattr(row, 'retry_scheduled') else False,
            is_deferred=row.is_deferred,
            is_background=row.is_background,
            result=row.result,
            error=row.error,
            created_at=row.created_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
            last_heartbeat_at=row.last_heartbeat_at if hasattr(row, 'last_heartbeat_at') else None,
        )

    # --------------------------------------------------------
    # UPDATE STATUS
    # --------------------------------------------------------

    def complete_task(self, task_id: int, result: dict[str, Any]) -> Task | None:
        """Mark task as completed with result.

        Phase 4a (D8 chokepoint, increment3-plan §6.5): this method is
        a THIN WRAPPER around the ``CompleteTurn`` named transition.
        The transition owns the atomic UPDATE on the task row; the
        chokepoint then delegates to ``reconcile_turn_mirror`` to
        update the 7 mirror tables in a SEPARATE post-commit
        transaction (matching the production invocation pattern in
        ``daemon/services/job_feedback_observer.py:3410`` — the
        reconciler opens its own ``engine.begin()``, it does NOT share
        the caller's transaction).

        Behavioral notes for Phase 4a (zero behavior change, C6 fix):

          * ``_resolve_work_id`` (no-op read) uses
            ``engine.connect()`` (not a session) so it doesn't acquire
            a SQLite write lock — file-backed test engines can
            serialise aggressively across connections
            (regression in ``test_heartbeat_rejects_completed_task``
            if a heavyweight session was opened).
          * The transition's atomic UPDATE on ``task`` is followed by
            a follow-up UPDATE for the auxiliary columns (``result``,
            ``completed_at``) — both inside the SAME
            ``engine.begin()`` so a crash mid-wrapper leaves no
            half-written terminal row.
          * Mirror reconciliation (``reconcile_turn_mirror``) runs in
            a SEPARATE ``engine.begin()`` AFTER the task UPDATE
            commits. This matches the established pattern in the
            cascade helpers.

        Returns ``None`` if the task was not found OR was already in
        a terminal status (the transition's ``WHERE status='running'``
        guard makes the UPDATE no-op for terminal tasks). Callers
        handle ``None`` as "already transitioned by another worker".

        Args:
            task_id: Task ID.
            result: Result dictionary to store.

        Returns:
            Updated Task object or None if not found or already transitioned.
        """
        work_id = self._resolve_work_id(task_id)
        if work_id is None:
            return None

        now = datetime.now(timezone.utc)
        result_json = json.dumps(result)

        with self.engine.begin() as session:
            # Read prior status INSIDE the same transaction so the
            # transition's ``WHERE status='running'`` guard is honored
            # exactly when it actually fires (idempotency for
            # already-terminal tasks returns None).
            prior_row = session.execute(
                text("SELECT status FROM task WHERE id = :id"),
                {"id": task_id},
            ).first()
            if prior_row is None:
                return None
            if prior_row[0] != TaskStatus.RUNNING.value:
                # Already terminal (or pending/paused); the transition
                # would no-op, so return None to preserve the original
                # semantic.
                return None

            transition = CompleteTurn(
                work_id=work_id,
                result=result,
                task_repo=self,
            )
            # Invoke the transition's atomic UPDATE directly without
            # its built-in ``_reconcile()`` — the reconciler opens its
            # OWN ``engine.begin()``, which on file-based SQLite
            # serialises against the outer transaction's held write
            # lock ("database is locked" regressions in
            # ``test_heartbeat_rejects_completed_task``). We instead
            # run ``reconcile_turn_mirror`` ourselves AFTER the outer
            # transaction commits — same shape as the post-commit
            # pattern in ``daemon/services/job_feedback_observer.py:3410``.
            transition._write(session, "completed", "running")
            # Auxiliary writes (must run inside the same transaction as
            # the transition's UPDATE so a crash mid-wrapper leaves no
            # half-written terminal row). The reconciler-owned mirror
            # updates (mirrors 2-8) run AFTER this transaction commits,
            # in their own engine.begin() — see below.
            session.execute(
                text(
                    "UPDATE task "
                    "SET result = :result, "
                    "    completed_at = :completed_at "
                    "WHERE id = :task_id AND status = :status_completed"
                ),
                {
                    "task_id": task_id,
                    "status_completed": TaskStatus.COMPLETED.value,
                    "result": result_json,
                    "completed_at": now,
                },
            )
            row = session.execute(
                text("SELECT * FROM task WHERE id = :id"),
                {"id": task_id},
            ).fetchone()
            if row is None:
                return None
            updated = self._row_to_task(row)
        # Transition committed above. Now reconcile all 7 mirror
        # tables in a separate post-commit transaction (matches the
        # pattern in daemon/services/job_feedback_observer.py:3410).
        # NOTE: reconcile_turn_mirror opens its own engine.begin();
        # calling it INSIDE the transition transaction above would
        # double-up connections and trip SQLite file-based locking.
        try:
            self.reconcile_turn_mirror(work_id)
        except InvalidTransitionError as e:  # pragma: no cover - invariant trip
            logger.warning(
                "Reconciler invariant violation after complete_task "
                "for task_id=%s work_id=%s: %s",
                task_id, work_id, e,
            )

        # Notify workers that a pending task may now be claimable.
        self._notify_pending_task()

        return updated

    def fail_task(self, task_id: int, error: str) -> Task | None:
        """Mark task as failed with error message.

        Phase 4a (D8 chokepoint, increment3-plan §6.5): this method is
        a THIN WRAPPER around ``AbortTurn(reason='failed')``. The
        transition owns the atomic UPDATE + mirror reconciliation
        (delegated to ``reconcile_turn_mirror``).

        **B1 critical**: ``fail_task`` MUST route through
        ``AbortTurn(reason='failed')`` — NOT ``reason='cancelled'`` —
        so the task discriminator survives into the JobItem
        ``terminal_reason`` mirror. A regression that routed this
        through ``reason='cancelled'`` would lose the failure
        discriminator and corrupt observability (jobs that genuinely
        failed would appear as cancellations).

        Atomic UPDATE with WHERE status=running guard + mirror
        reconciliation: the named transition handles both. Returns
        ``None`` if the task was not found OR was already in a
        terminal status. Callers handle ``None`` as "already
        transitioned by another worker".

        Args:
            task_id: Task ID.
            error: Error message.

        Returns:
            Updated Task object or None if not found or already transitioned.
        """
        work_id = self._resolve_work_id(task_id)
        if work_id is None:
            return None

        now = datetime.now(timezone.utc)

        with self.engine.begin() as session:
            # Read prior status INSIDE the same transaction so we know
            # whether the transition will actually flip status='running'
            # → 'failed'. The transition's guard
            # ``WHERE work_id=:work_id AND status='running'`` no-ops
            # for non-running tasks; matching the original semantic,
            # we return ``None`` in that case (idempotent re-fail is
            # a no-op).
            prior_row = session.execute(
                text("SELECT status FROM task WHERE id = :id"),
                {"id": task_id},
            ).first()
            if prior_row is None:
                return None
            if prior_row[0] != TaskStatus.RUNNING.value:
                # Already terminal (or pending/paused); the transition
                # would no-op, so we return None to match original
                # semantics. Idempotency: a second fail_task on an
                # already-failed task returns None.
                return None

            transition = AbortTurn(
                work_id=work_id,
                reason="failed",
                task_repo=self,
                error=error,
            )
            # See ``complete_task`` — call the transition's atomic
            # UPDATE directly so the reconciler's nested ``engine.begin()``
            # doesn't serialise against the outer transaction on
            # file-based SQLite. Mirror reconciliation happens
            # post-commit below.
            transition._write(session, "failed", "running")
            # Auxiliary writes inside the same transaction as the
            # transition (error / completed_at). Preserves the
            # pre-refactor single-UPDATE behavior.
            session.execute(
                text(
                    "UPDATE task "
                    "SET error = :error, "
                    "    completed_at = :completed_at "
                    "WHERE id = :task_id AND status = :status_failed"
                ),
                {
                    "task_id": task_id,
                    "status_failed": TaskStatus.FAILED.value,
                    "error": error,
                    "completed_at": now,
                },
            )
            row = session.execute(
                text("SELECT * FROM task WHERE id = :id"),
                {"id": task_id},
            ).fetchone()
            if row is None:
                return None
            updated = self._row_to_task(row)
        # Post-commit mirror reconciliation (matches the pattern in
        # daemon/services/job_feedback_observer.py:3410). The
        # reconciler opens its own engine.begin() — calling it inside
        # the transition transaction above would double-up connections
        # and trip SQLite file-based locking.
        try:
            self.reconcile_turn_mirror(work_id)
        except InvalidTransitionError as e:  # pragma: no cover - invariant trip
            logger.warning(
                "Reconciler invariant violation after fail_task "
                "for task_id=%s work_id=%s: %s",
                task_id, work_id, e,
            )

        # Notify workers (see complete_task for rationale).
        self._notify_pending_task()

        return updated

    def cancel_pending_tasks_for_instance(self, instance_id: str) -> int:
        """Cancel every PENDING ``task`` row for ``instance_id``.

        F12 fix (Phase 3, 2026-07-01): on retry, ``atomic_retry`` flips the
        JobItem back to ``queued`` and the orchestrator will call
        ``start_job`` to spawn a fresh instance/Task. A leftover PENDING
        retry child on the same ``instance_id`` from a previous
        failed-start path would otherwise survive — ``claim_pending_task``'s
        per-instance guard blocks only RUNNING tasks, not PENDING ones, so
        the stale PENDING and the fresh retry Task can both become
        claimable and contest the same LangGraph checkpoint (the
        ``graph.astream`` call drives writes to the Postgres checkpointer
        keyed on ``thread_id`` which is the ``instance_id``; two
        concurrent streams shadow each other's channel writes).

        The cancellation here is **best-effort** — it transitions stale
        PENDING tasks to CANCELLED so the next ``claim_pending_task`` will
        ignore them, but does NOT touch RUNNING tasks (those are protected
        by ``claim_pending_task``'s per-instance guard already, and a
        concurrent RUNNING task is by definition a sibling that's
        legitimately executing — killing it would be a regression). The
        RUNNING case is handled separately by F10 / recovery paths.

        Atomic SQL UPDATE with a row-count return: the WHERE clause
        matches ``status = 'pending'`` only, so a RUNNING task on the
        same instance (which would be a sibling still executing) is
        never touched. The UPDATE is a single-statement transaction so
        the transition is race-free against concurrent
        ``claim_pending_task`` calls — the row-level write lock on the
        candidate row (acquired by the candidate's UPDATE … SET
        status='running') serialises with this bulk UPDATE.

        Args:
            instance_id: The instance whose stale PENDING tasks should
                be cancelled before retry re-admission.

        Returns:
            Number of PENDING task rows transitioned to CANCELLED. Zero
            is a valid no-op (instance had no PENDING tasks, or they
            were already claimed/cancelled by a concurrent caller).
        """
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    UPDATE task
                    SET status = :status_cancelled,
                        cancel_requested = :cancel_requested_true,
                        cancel_requested_at = :now,
                        completed_at = :now
                    WHERE instance_id = :instance_id
                      AND status = :status_pending
                    """
                ),
                {
                    "instance_id": instance_id,
                    "status_cancelled": TaskStatus.CANCELLED.value,
                    "status_pending": TaskStatus.PENDING.value,
                    "cancel_requested_true": True,
                    "now": now,
                },
            )
            return result.rowcount

    # --------------------------------------------------------
    # RECOVERY
    # --------------------------------------------------------

    def find_stale_running_tasks(self, threshold_minutes: int = 15) -> list[Task]:
        """Find tasks that have been running too long.

        Used for crash recovery to detect tasks that may have been
        abandoned by crashed workers.

        Liveness signal: the predicate is on ``last_heartbeat_at`` rather
        than ``started_at``, with a fallback to ``started_at`` for rows
        that have ``last_heartbeat_at IS NULL`` (e.g. legacy rows predating
        the heartbeat column, or rows that the startup backfill has not
        yet touched). A live task's heartbeat is updated every
        ``task_heartbeat_interval_seconds`` by the worker; a crashed task's
        heartbeat stops being updated, so the recovery service can
        distinguish them within the configured threshold (default 5 min)
        without false-positively flagging long-running live tasks.

        Args:
            threshold_minutes: Minutes after which a running task is
                considered stale. Sized for *time since last heartbeat*,
                not *time since started*.

        Returns:
            List of stale running tasks.
        """
        threshold = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)

        with SQLModelSession(self.engine) as db_session:
            stmt = select(Task).where(
                Task.status == TaskStatus.RUNNING.value,
                # COALESCE falls back to started_at for legacy rows.
                func.coalesce(Task.last_heartbeat_at, Task.started_at) < threshold,
                # Skip tasks belonging to paused/terminated instances: pause
                # intentionally leaves the task RUNNING so resume can continue
                # from the same row. Recovery must not auto-resume such tasks.
                Task.instance_id.notin_(
                    select(Instance.instance_id).where(
                        Instance.status.in_([
                            InstanceStatus.PAUSED.value,
                            InstanceStatus.TERMINATED.value,
                        ])
                    )
                ),
            )
            return list(db_session.exec(stmt))

    def reset_stale_tasks(self, threshold_minutes: int = 15) -> int:
        """Reset stale running tasks to pending status.

        Used for crash recovery to make abandoned tasks available again.

        Liveness signal: predicate is on ``COALESCE(last_heartbeat_at,
        started_at)`` so live long-running tasks aren't reset.
        """
        threshold = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
        count = 0

        with SQLModelSession(self.engine) as db_session:
            stmt = text("""
                UPDATE task
                SET status = :status_pending,
                    worker_id = NULL,
                    started_at = NULL
                WHERE status = :status_running
                AND COALESCE(last_heartbeat_at, started_at) < :threshold
            """)

            result = db_session.exec(stmt, params={
                "status_pending": TaskStatus.PENDING.value,
                "status_running": TaskStatus.RUNNING.value,
                "threshold": threshold,
            })

            count = result.rowcount
            db_session.commit()
            return count

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    def get_pending_count(self) -> int:
        """Count pending tasks.

        Returns:
            Number of pending tasks.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(func.count()).select_from(Task).where(
                Task.status == TaskStatus.PENDING.value
            )
            return db_session.exec(stmt).one()

    def has_pending_tasks_blocked_by_busy_instance(self) -> bool:
        """Return whether pending work is held by an in-flight sibling.

        The task-level RUNNING guard and shared JobItem predicate mirror
        ``claim_pending_task``. JobItems block only when their backing Task is
        PENDING, RUNNING, or PAUSED; the reconciler owns orphan cleanup. The
        WAITING_CHILDREN exception remains part of the shared predicate.
        """
        with self.engine.begin() as conn:
            stmt = text(f"""
                SELECT 1
                WHERE EXISTS (
                    SELECT 1 FROM task t_pending
                    WHERE t_pending.status = :status_pending
                    AND (
                        EXISTS (
                            SELECT 1 FROM task t_running
                            WHERE t_running.status = :status_running
                            AND t_running.instance_id = t_pending.instance_id
                        )
                        OR EXISTS (
                            SELECT 1 FROM job_queue_items j_running
                            LEFT JOIN instances i ON j_running.instance_id = i.instance_id
                            -- Phase 3 admission-decision migration: mirrors
                            -- ``claim_pending_task`` — MUST use the SAME
                            -- predicate (see docstring above). The legacy
                            -- ``status = 'processing'`` excluded PAUSED
                            -- jobs that hold the lock
                            -- (admission_state='active'); the IN-list also
                            -- covers the B1 single-transaction window.
                            -- See ``_ACTIVE_JOB_IDS_SUBQUERY`` in
                            -- lock_repository.py for the canonical form.
                            WHERE j_running.admission_state IN {active_admission_states_sql()}
                              AND j_running.instance_id = t_pending.instance_id
                              AND j_running.deleted_at IS NULL
                              AND {self._active_jobitem_with_inflight_task_sql("j_running")}
                        )
                    )
                )
                LIMIT 1
            """)
            row = conn.execute(stmt, {
                "status_pending": TaskStatus.PENDING.value,
                "status_running": TaskStatus.RUNNING.value,
                "status_paused": TaskStatus.PAUSED.value,
                "status_waiting_children": InstanceStatus.WAITING_CHILDREN.value,
            }).fetchone()
            return row is not None

    def has_active_non_deferred_work(self, project_id: str | None = None) -> bool:
        """Return True if there is any non-deferred in-flight task.

        Phase 3 Part B2 (2026-06-30, defer-seam bugfix Phase 1). The
        shared predicate backing the defer-queue idle gate: returns
        True iff at least one row exists in ``task`` with status
        ``pending``, ``running``, OR ``paused`` and ``is_deferred =
        false``. ``paused`` counts as non-idle (the pause-fix,
        2026-07-01): a paused instance is suspended-but-occupying, so
        the defer queue and maintenance must treat it as still-active
        work, not as a free admission slot. The gate is shared by:

        * :meth:`claim_pending_task` — folds the predicate into the
          atomic claim's inner SELECT so deferred candidates are held
          back while non-deferred work is active in the same project.
        * Job-queue call sites that admit deferred jobs into the
          dispatch queue (e.g. the project-scoped probe used by the
          defer-queue idle gate in
          ``daemon/services/job_queue_mgmt_service.py``) — they use
          this helper instead of re-implementing the same EXISTS
          query so the claim path and the admission probe never
          disagree.

        Args:
            project_id: Optional project scope.

              * ``project_id=None`` (default): system-wide probe — the
                project filter is omitted entirely. Any non-deferred
                in-flight task in any project triggers a True return.
                This is the conservative gate for the worker pool's
                claim path (it must never starve a non-deferred task
                in any project).
              * ``project_id="abc"``: only count tasks whose
                ``instance_id`` belongs to an instance in that project.
                Tasks whose instance has no ``instances`` row do not
                match the INNER JOIN and are excluded — consistent with
                the defer-queue idle gate's project resolution in
                :meth:`claim_pending_task` (which resolves a deferred
                candidate's project via ``instances`` and falls back
                to NULL when no row exists).

        Returns:
            True if at least one non-deferred PENDING, RUNNING, or PAUSED
            task exists (scoped per ``project_id``); False otherwise.

        Dialect notes:
            * ``t.is_deferred = :is_deferred_false`` with a Python
              ``False`` bound parameter — matches the existing
              dual-driver pattern in :meth:`claim_pending_task` and
              :meth:`schedule_retry` so the boolean comparison works
              on both SQLite (INTEGER 0/1) and PostgreSQL (BOOLEAN
              false/true).
            * ``SELECT EXISTS(...)`` returns a single boolean column
              (0/1 on both backends) and is wrapped in ``bool()`` so
              the return type is invariant across drivers.
            * The status ``IN (:status_pending, :status_running, :status_paused)``
              parameterised list includes ``paused`` so a paused
              instance counts as non-idle for the defer gate and
              maintenance (pause-fix, 2026-07-01). Note this is
              deliberately NARROWER than
              :meth:`has_pending_tasks_blocked_by_busy_instance`, which
              answers a different question (is a pending task blocked
              by a *running* task) and must stay two-state.
        """
        with self.engine.begin() as conn:
            if project_id is None:
                stmt = text("""
                    SELECT EXISTS(
                        SELECT 1 FROM task t
                        JOIN instances i ON t.instance_id = i.instance_id
                        WHERE t.status IN (:status_pending, :status_running, :status_paused)
                          AND t.is_deferred = :is_deferred_false
                    )
                """)
                row = conn.execute(stmt, {
                    "status_pending": TaskStatus.PENDING.value,
                    "status_running": TaskStatus.RUNNING.value,
                    "status_paused": TaskStatus.PAUSED.value,
                    "is_deferred_false": False,
                }).fetchone()
            else:
                stmt = text("""
                    SELECT EXISTS(
                        SELECT 1 FROM task t
                        JOIN instances i ON t.instance_id = i.instance_id
                        WHERE t.status IN (:status_pending, :status_running, :status_paused)
                          AND t.is_deferred = :is_deferred_false
                          AND i.project_id = :project_id
                    )
                """)
                row = conn.execute(stmt, {
                    "status_pending": TaskStatus.PENDING.value,
                    "status_running": TaskStatus.RUNNING.value,
                    "status_paused": TaskStatus.PAUSED.value,
                    "is_deferred_false": False,
                    "project_id": project_id,
                }).fetchone()
            return bool(row[0])

    def has_active_non_background_work(self, project_id: str | None = None) -> bool:
        """Return True if there is any non-background in-flight task.

        Phase 3 background-seam (2026-07-14): the shared predicate backing
        the background-queue idle gate. Sister query to
        :meth:`has_active_non_deferred_work` — the two gates share the
        SAME atomic claim SQL pattern (see
        :meth:`claim_pending_task` for the defer predicate this mirrors)
        so the claim path and the admission probe can never disagree.

        Defer-work inclusion (defer-leak bug fix, 2026-07-23):
        ``is_deferred`` is INTENTIONALLY NOT filtered out — a defer
        task MUST count as non-background work so it blocks the
        background gate. The defer predicate
        :meth:`has_active_non_deferred_work` excludes defer work
        (defer queues should not block themselves), but the
        background predicate excludes ONLY background work. They are
        not symmetric. ``TaskRepository.has_active_non_background_work``
        and :meth:`JobRepository.has_active_non_background_work` agree:
        defer work is non-background work and blocks the background
        gate.

        Scope difference from the defer predicate:

        * DEFER is **project-scoped** — a project's defer queue only
          waits for non-deferred work in the SAME project.
        * BACKGROUND is **system-wide** — a background task waits for
          non-background work across ALL projects. This is the
          documented scope asymmetry from
          ``feature/virtual-job-management-surface`` Phase 3 background
          seam (2026-07-14).

        The ``project_id`` parameter is accepted for signature symmetry
        with :meth:`has_active_non_deferred_work` but is **ignored** —
        the predicate is always system-wide. The parameter is preserved
        in the signature so callers that pass ``queue.project_id`` for
        symmetry with the defer path can do so without raising, and so
        ``getattr(task_repo, "has_active_non_background_work", None)``
        lookups in callers stay uniform.

        Returns:
            True if at least one ``is_background=false`` task exists
            with status ``pending``, ``running``, or ``paused`` across
            ANY project (defer tasks included); False otherwise.

        Dialect notes mirror :meth:`has_active_non_deferred_work`
        exactly — Python ``False`` booleans as bound parameters so the
        comparison works on both SQLite (INTEGER 0/1) and PostgreSQL
        (BOOLEAN false/true), ``SELECT EXISTS(...)`` wrapped in
        ``bool()`` for a backend-invariant return type, and the
        ``IN (pending, running, paused)`` parameterised status list so a
        paused instance counts as non-idle (matches the pause-fix
        semantics from 2026-07-01).
        """
        with self.engine.begin() as conn:
            # Background gate is ALWAYS system-wide (Phase 3 background
            # seam, 2026-07-14). Mark the parameter as intentionally
            # unused so a future reader does not silently add a project
            # filter without understanding the documented scope
            # asymmetry — background work waits across projects, not
            # within them.
            del project_id
            stmt = text("""
                SELECT EXISTS(
                    SELECT 1 FROM task t
                    JOIN instances i ON t.instance_id = i.instance_id
                    WHERE t.status IN (:status_pending, :status_running, :status_paused)
                      AND t.is_background = :is_background_false
                )
            """)
            row = conn.execute(stmt, {
                "status_pending": TaskStatus.PENDING.value,
                "status_running": TaskStatus.RUNNING.value,
                "status_paused": TaskStatus.PAUSED.value,
                "is_background_false": False,
            }).fetchone()
            return bool(row[0])

    def count_by_status(self) -> dict[str, int]:
        """Get count of tasks by status.

        Returns:
            Dictionary mapping status to count.
        """
        counts = {}
        with SQLModelSession(self.engine) as db_session:
            for status in TaskStatus:
                stmt = select(func.count()).select_from(Task).where(
                    Task.status == status.value
                )
                counts[status.value] = db_session.exec(stmt).one()
        return counts

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def delete(self, task_id: int) -> bool:
        """Delete a task.

        Args:
            task_id: Task ID.

        Returns:
            True if deleted, False if not found.
        """
        with SQLModelSession(self.engine) as db_session:
            task = db_session.get(Task, task_id)
            if task is None:
                return False

            db_session.delete(task)
            db_session.commit()
            return True

    def delete_by_instance(self, instance_id: str) -> int:
        """Delete all tasks for an instance.

        Args:
            instance_id: Instance ID.

        Returns:
            Number of tasks deleted.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = sql_delete(Task).where(Task.instance_id == instance_id)
            result = db_session.exec(stmt)
            db_session.commit()
            return result.rowcount

    def clear_all(self, preserve_in_flight: bool = False) -> int:
        """Delete tasks, optionally preserving resumable / in-flight work.

        Args:
            preserve_in_flight: When ``False`` (default) every task row is
                deleted — the legacy "nuclear wipe" used outside the
                startup path. When ``True``, only **backlog** work is
                discarded: tasks whose status is neither ``running`` nor
                ``paused``. RUNNING tasks (in-flight, recovered by
                StaleTaskRecovery after restart) and PAUSED tasks
                (suspended-but-resumable) are kept so that:

                  * the defer-queue idle gate
                    (``has_active_non_deferred_work``) still sees a
                    paused instance's task and blocks ``system_defer_queue``;
                  * a paused instance can still be resumed after restart.

                This is the mode the ``discard_on_startup`` startup hook
                uses: a clean backlog slate without orphaning resumable
                state.

        Returns:
            Number of tasks deleted.
        """
        with SQLModelSession(self.engine) as db_session:
            if preserve_in_flight:
                stmt = sql_delete(Task).where(
                    Task.status.notin_([
                        TaskStatus.RUNNING.value,
                        TaskStatus.PAUSED.value,
                    ])
                )
            else:
                stmt = sql_delete(Task)
            result = db_session.exec(stmt)
            db_session.commit()
            return result.rowcount

    # --------------------------------------------------------
    # RETRY & CANCELLATION
    # --------------------------------------------------------

    def schedule_retry(
        self,
        task_id: int,
        max_retries: int,
        backoff_base: int = 60,
        backoff_max: int = 3600,
    ) -> Task | None:
        """Phase 5 (DQ increment3-plan §5.7 / §6.5 / §9 Phase 5):
        thin wrapper around the ``RetryTurn`` named transition.

        Marks the parent task as CANCELLED with retry_scheduled=True and
        creates a new PENDING task with incremented retry_count and a
        calculated next_retry_at. All operations are in a single
        transaction — crash-safe.

        The wrapper owns the conditional UPDATE-with-guard (the race
        gate that prevents duplicate retry children) and the backoff
        calculation. ``RetryTurn`` owns the child INSERT + mirror
        reconciliation + watcher migration (F6 fix).

        Atomicity / concurrency: the parent UPDATE carries the full
        guard (``retry_scheduled = false``, ``retry_count < max_retries``,
        and ``status IN ('running','failed','cancelled')``) directly in
        the SQL WHERE clause. The ``RetryTurn.run(session)`` call is
        gated on ``UPDATE.rowcount == 1`` inside the same
        ``engine.begin()`` transaction, so two concurrent callers cannot
        both pass the check and create duplicate retry children — only
        the first UPDATE will match the row and produce a rowcount of 1,
        and only that caller will then construct the child. If the UPDATE
        returns 0 rows (already retried, max retries exceeded, status
        not eligible, or task not found), this method returns None and
        does not construct the child.

        ``cancelled`` is included in the eligible set on purpose: an
        orphaned CANCELLED task (``status=cancelled, retry_scheduled=false``,
        no child) is exactly the crash-recovery case
        ``find_orphaned_cancelled_tasks()`` detects on startup. The
        double-retry guard (``retry_scheduled = false``) and the
        ``retry_count < max_retries`` guard together still prevent
        duplicate retry creation. The terminal states ``completed`` and
        ``failed`` (when the worker already reported outcome) are
        excluded — a terminal task must never get a retry child.

        Returns the new retry task, or None if no retry was scheduled.
        """
        retry_task = None

        with self.engine.begin() as conn:
            # Atomic UPDATE-with-guard: the race condition gate. The
            # status guard (``IN ('running','failed','cancelled')``)
            # replaces the prior Python-side check and prevents
            # clobbering a concurrent terminal-state write (e.g. a
            # parallel `complete_task` that set status to 'completed').
            # Use Python booleans as bound values so the comparison
            # works on both SQLite (INTEGER 0/1) and PostgreSQL (BOOLEAN
            # false/true). `cancelled` is included so the orphan-recovery
            # path (CANCELLED + retry_scheduled=false) can schedule a
            # retry child; see method docstring.
            parent_row = conn.execute(
                text("""
                    UPDATE task
                    SET status = :status_cancelled,
                        cancel_requested = :cancel_requested_true,
                        cancel_requested_at = :now,
                        completed_at = :now,
                        retry_scheduled = :retry_scheduled_true
                    WHERE id = :task_id
                      AND retry_scheduled = :retry_scheduled_false
                      AND retry_count < :max_retries
                      AND status IN (:status_running, :status_failed, :status_cancelled)
                    RETURNING *
                """),
                {
                    "task_id": task_id,
                    "status_cancelled": TaskStatus.CANCELLED.value,
                    "cancel_requested_true": True,
                    "retry_scheduled_true": True,
                    "retry_scheduled_false": False,
                    "max_retries": max_retries,
                    "status_running": TaskStatus.RUNNING.value,
                    "status_failed": TaskStatus.FAILED.value,
                    "status_cancelled": TaskStatus.CANCELLED.value,
                    "now": datetime.now(timezone.utc),
                },
            ).fetchone()

            if parent_row is None:
                # Either task not found, already retried, max retries
                # exceeded, or status not in ('running','failed','cancelled').
                # In all cases the safe action is the same: do nothing,
                # return None.
                return None

            # The UPDATE didn't modify retry_count, so the RETURNING row
            # still has the parent's current retry_count.
            current_retry_count = parent_row.retry_count
            new_retry_count = current_retry_count + 1

            # Calculate exponential backoff. The ``2 ** current_retry_count``
            # delay is capped at ``backoff_max`` so a long-running retry
            # chain doesn't produce multi-day delays.
            now = datetime.now(timezone.utc)
            delay_seconds = min(
                backoff_base * (2 ** current_retry_count),
                backoff_max,
            )
            next_retry_at = now + timedelta(seconds=delay_seconds)
            next_retry_at_str = (
                next_retry_at.strftime("%Y-%m-%dT%H:%M:%S.%f")
                + next_retry_at.strftime("%z")
            )

            # Mint fresh child work_id. Reusing the parent's work_id
            # would violate the UNIQUE constraint on ``task.work_id``
            # (the parent row is only cancelled, not deleted) — the
            # retry is a fresh logical work attempt and gets its own
            # virtual-job identifier.
            child_work_id = str(uuid.uuid4())

            # Build child INSERT kwargs (all NOT NULL columns except
            # work_id, status, instance_id — those are method-level
            # defaults on ``RetryTurn``). ``message_id`` is inherited
            # from the parent (the retry rides the same message lane);
            # ``is_deferred`` and ``is_background`` are inherited so the
            # retry child stays in the same defer/background queue lane
            # (Phase 3 Part B1 2026-06-27 / Part B2 2026-07-14).
            child_kwargs = {
                "task_type": parent_row.task_type,
                "message_id": parent_row.message_id,
                "retry_count": new_retry_count,
                "next_retry_at": next_retry_at_str,
                "created_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "is_deferred": bool(parent_row.is_deferred)
                if hasattr(parent_row, "is_deferred")
                else False,
                "is_background": bool(parent_row.is_background)
                if hasattr(parent_row, "is_background")
                else False,
            }

            # Run the RetryTurn transition. This performs:
            #   1. Defensive parent UPDATE (status='cancelled').
            #   2. INSERT child Task with all configured columns.
            #   3. Reconcile parent mirrors via reconcile_turn_mirror.
            #   4. Reconcile child mirrors via reconcile_turn_mirror.
            #   5. Migrate job_watchers from parent to child work_id (F6).
            # All inside the same ``engine.begin()`` transaction as the
            # gate UPDATE above, so the gate-pass + child-insert commit
            # atomically or both roll back.
            transition = RetryTurn(
                parent_work_id=parent_row.work_id,
                child_work_id=child_work_id,
                task_repo=self,
                instance_id=parent_row.instance_id,
                child_kwargs=child_kwargs,
            )
            transition.run(conn)

            # Re-read the child Task row to return as a Task object
            # (matches the prior implementation's contract — callers
            # receive the row that was INSERTed, not a TransitionResult).
            child_row = conn.execute(
                text("SELECT * FROM task WHERE work_id = :work_id"),
                {"work_id": child_work_id},
            ).fetchone()
            retry_task = self._row_to_task(child_row)

        # AFTER commit — safe to notify workers (see complete_task for
        # rationale). The notification is fired only when a retry child
        # was actually inserted; an empty-result gate UPDATE returns
        # None above and skips the notify.
        if retry_task is not None:
            self._notify_pending_task()

        return retry_task

    def _notify_pending_task(self) -> None:
        """Notify workers that a pending task was created."""
        if self._on_pending_task:
            try:
                self._on_pending_task()
            except Exception:
                logger.warning("Failed to notify workers of pending task", exc_info=True)

    def request_cancel(self, task_id: int) -> bool:
        """Atomically request cancellation of a running task.

        Sets cancel_requested=True on the task. The worker thread
        checks this flag periodically and will stop gracefully.

        Returns True if the flag was set, False if task not found,
        already cancelled, or retry already scheduled.
        """
        now = datetime.now(timezone.utc)

        with self.engine.begin() as conn:
            # Use bound parameters with Python booleans so the boolean
            # comparisons work on both SQLite (INTEGER 0/1) and PostgreSQL
            # (BOOLEAN false/true).
            result = conn.execute(
                text("""
                    UPDATE task
                    SET cancel_requested = :cancel_requested_true,
                        cancel_requested_at = :cancelled_at
                    WHERE id = :id
                    AND status = :status_running
                    AND cancel_requested = :cancel_requested_false
                    AND retry_scheduled = :retry_scheduled_false
                """),
                {
                    "cancel_requested_true": True,
                    "cancelled_at": now,
                    "id": task_id,
                    "status_running": TaskStatus.RUNNING.value,
                    "cancel_requested_false": False,
                    "retry_scheduled_false": False,
                }
            )
            return result.rowcount > 0

    def find_cancellable_tasks(self, threshold_minutes: int) -> list[Task]:
        """Find running tasks that have exceeded the timeout threshold
        and haven't been marked for cancellation yet.

        Liveness signal: predicate is on ``COALESCE(last_heartbeat_at,
        started_at)`` so live long-running tasks aren't flagged.
        """
        threshold = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)

        with self.engine.begin() as conn:
            # Use bound parameter with Python False so the boolean
            # comparison works on both SQLite (INTEGER 0) and PostgreSQL
            # (BOOLEAN false).
            # Exclude tasks whose instance is PAUSED/TERMINATED: pause
            # intentionally leaves the task RUNNING so resume can continue
            # from the same row. Recovery must not auto-resume such tasks.
            stmt = text("""
                SELECT t.* FROM task t
                LEFT JOIN instances i ON i.instance_id = t.instance_id
                WHERE t.status = :status_running
                AND COALESCE(t.last_heartbeat_at, t.started_at) < :threshold
                AND t.cancel_requested = :cancel_requested
                AND (i.status IS NULL OR i.status NOT IN (:paused, :terminated))
            """)
            rows = conn.execute(stmt, {
                "status_running": TaskStatus.RUNNING.value,
                "threshold": threshold,
                "cancel_requested": False,
                "paused": InstanceStatus.PAUSED.value,
                "terminated": InstanceStatus.TERMINATED.value,
            }).fetchall()
            return [self._row_to_task(row) for row in rows]

    def cancel_task(self, task_id: int, reason: str = "") -> Task | None:
        """Directly cancel a task (mark as CANCELLED).

        Phase 4a (D8 chokepoint, increment3-plan §6.5): this method is
        a THIN WRAPPER around ``AbortTurn(reason='cancelled')``. The
        transition owns the atomic UPDATE on the task row; the
        chokepoint then delegates to ``reconcile_turn_mirror`` to
        update the 7 mirror tables in a SEPARATE post-commit
        transaction (matches the production invocation pattern in
        ``daemon/services/job_feedback_observer.py:3410``).

        The transition's ``AbortTurn._write`` gates on
        ``status='running'`` (the common case — a task being cancelled
        mid-execution). For cancellation of PENDING or PAUSED tasks
        (race condition where the task hasn't started yet or was just
        paused by the cascade), this wrapper falls back to an inline
        atomic UPDATE with the broader ``WHERE status IN (running,
        pending, paused)`` guard, mirroring the pre-refactor
        behavior. Mirror reconciliation runs in a SEPARATE post-commit
        ``engine.begin()`` for both branches.

        Used by StaleTaskRecovery when worker doesn't respond to
        cancel_requested flag within grace period. Also called by the
        rest of the codebase (worker_pool.py is the production caller)
        — preserving the public signature here is the whole point of
        the D8 chokepoint wrapper.

        Args:
            task_id: Task ID.
            reason: Free-form cancellation reason (recorded on the
                task's ``error`` column for observability).

        Returns:
            Updated Task object or None if not found or already terminal.
        """
        work_id = self._resolve_work_id(task_id)
        if work_id is None:
            return None

        now = datetime.now(timezone.utc)
        error_text = f"Task cancelled: {reason}" if reason else None

        with self.engine.begin() as session:
            # Phase 4a behavior preservation: read the prior status to
            # pick the right UPDATE branch. The running→cancelled
            # path goes through the named transition (mirror reconcile
            # happens in a separate post-commit transaction — see
            # below). The pending/paused→cancelled path goes through a
            # legacy-shape UPDATE for backward compatibility; the
            # wrapper then explicitly calls reconcile_turn_mirror.
            prior_row = session.execute(
                text("SELECT status FROM task WHERE work_id = :wid"),
                {"wid": work_id},
            ).first()
            if prior_row is None:
                return None
            prior_status = prior_row[0]

            if prior_status == TaskStatus.RUNNING.value:
                # Hot path: route through the named transition's
                # atomic UPDATE. We bypass the transition's
                # built-in ``_reconcile()`` (same SQLite-locking
                # reason as in ``complete_task`` / ``fail_task``:
                # the reconciler opens its own ``engine.begin()``
                # which serialises against the outer transaction on
                # file-based engines). Mirror reconciliation runs
                # post-commit, below.
                transition = AbortTurn(
                    work_id=work_id,
                    reason="cancelled",
                    task_repo=self,
                    error=error_text,
                )
                transition._write(session, "cancelled", "running")
                # Auxiliary writes (cancel_requested bookkeeping,
                # completed_at) — kept inside the same transaction as
                # the transition's UPDATE for atomicity.
                session.execute(
                    text(
                        "UPDATE task "
                        "SET cancel_requested = :cancel_requested_true, "
                        "    cancel_requested_at = :cancelled_at, "
                        "    completed_at = :completed_at, "
                        "    error = :error "
                        "WHERE id = :task_id AND status = :status_cancelled"
                    ),
                    {
                        "task_id": task_id,
                        "status_cancelled": TaskStatus.CANCELLED.value,
                        "cancel_requested_true": True,
                        "cancelled_at": now,
                        "completed_at": now,
                        "error": error_text,
                    },
                )
            elif prior_status in (
                TaskStatus.PENDING.value,
                TaskStatus.PAUSED.value,
            ):
                # Cold path: legacy-shape atomic UPDATE for
                # pending/paused cancellation.
                row = session.execute(
                    text(
                        "UPDATE task SET "
                        "status = :status_cancelled, "
                        "cancel_requested = :cancel_requested_true, "
                        "cancel_requested_at = :cancelled_at, "
                        "completed_at = :completed_at, "
                        "error = :error "
                        "WHERE id = :id "
                        "  AND status IN (:status_running, :status_pending, :status_paused) "
                        "RETURNING *"
                    ),
                    {
                        "status_cancelled": TaskStatus.CANCELLED.value,
                        "cancel_requested_true": True,
                        "cancelled_at": now,
                        "completed_at": now,
                        "error": error_text,
                        "id": task_id,
                        "status_running": TaskStatus.RUNNING.value,
                        "status_pending": TaskStatus.PENDING.value,
                        "status_paused": TaskStatus.PAUSED.value,
                    },
                ).fetchone()
                if row is None:
                    return None
            else:
                # Already terminal (completed/failed/cancelled) or
                # unknown status — match original behavior: return
                # None (no-op).
                return None

            row = session.execute(
                text("SELECT * FROM task WHERE id = :id"),
                {"id": task_id},
            ).fetchone()
            if row is None:
                return None
            result = self._row_to_task(row)
        # Post-commit mirror reconciliation. The reconciler opens
        # its own engine.begin(); calling it inside the transaction
        # above would double-up connections and trip SQLite
        # file-based locking.
        try:
            self.reconcile_turn_mirror(work_id)
        except InvalidTransitionError as e:  # pragma: no cover - invariant trip
            logger.warning(
                "Reconciler invariant violation after cancel_task "
                "for task_id=%s work_id=%s: %s",
                task_id, work_id, e,
            )

        # Notify workers (see complete_task for rationale). Notification
        # is safe after the commit; the worst case is a spurious wakeup
        # that finds nothing to claim.
        self._notify_pending_task()

        return result

    def force_cancel_and_schedule_retry(
        self,
        task_id: int,
        max_retries: int,
        reason: str,
        backoff_base: int = 60,
        backoff_max: int = 3600,
    ) -> Task | None:
        """Phase 4b.14 (DQ increment3-plan §5.7 / §6.5 / §9 Phase 4b):
        thin wrapper around ``RetryTurn`` (with the parent ``error``
        column set to the force-cancel reason).

        Atomically cancels a task and schedules a retry in a single
        transaction. Combines ``cancel_task()`` + ``schedule_retry()``
        to prevent the window where a crash would leave an orphaned
        CANCELLED task with no retry child.

        The wrapper owns the conditional UPDATE-with-guard (the race
        gate that prevents duplicate retry children) and the backoff
        calculation. ``RetryTurn`` owns the child INSERT + mirror
        reconciliation + watcher migration (F6 fix). The parent cancel
        and ``error`` recording are achieved by the gate UPDATE; the
        ``RetryTurn`` parent UPDATE is a defensive idempotent
        operation that re-records the error (same value) and the
        cancelled status (no-op).

        Differences from ``schedule_retry``:

          * The eligible parent status set is smaller
            (``('running','failed')`` only) — ``cancelled`` is NOT
            eligible because the force-cancel path is reached after
            ``request_cancel`` was already set, and a cancelled parent
            is not a force-cancel candidate.
          * The parent's ``error`` column is set to
            ``f"Force cancelled: {reason}"`` for observability
            (caller-supplied ``reason`` string).
          * Both ``cancel_requested`` and ``cancel_requested_at`` are
            set on the parent (the same as ``schedule_retry``).

        Atomicity / concurrency: same atomic-UPDATE-with-guard pattern
        as ``schedule_retry``. The parent UPDATE is conditional on
        ``retry_scheduled = False AND retry_count < max_retries AND
        status IN ('running','failed')`` so concurrent callers can only
        create one retry child. The ``RetryTurn.run(session)`` call is
        gated on ``UPDATE.rowcount == 1`` inside the same
        ``engine.begin()`` transaction.

        Returns the new retry task, or None if the parent is missing,
        already has ``retry_scheduled=True``, has
        ``retry_count >= max_retries``, or is not in
        ``('running', 'failed')`` status.
        """
        retry_task = None
        now = datetime.now(timezone.utc)
        parent_error = f"Force cancelled: {reason}"

        with self.engine.begin() as conn:
            # Force-cancel parent and set retry_scheduled guard in a
            # single atomic UPDATE. Only one concurrent caller wins the
            # row update; the rest see rowcount=0 and return None.
            parent_row = conn.execute(
                text("""
                    UPDATE task
                    SET status = :status_cancelled,
                        cancel_requested = :cancel_requested_true,
                        cancel_requested_at = :now,
                        completed_at = :now,
                        error = :error,
                        retry_scheduled = :retry_scheduled_true
                    WHERE id = :task_id
                      AND retry_scheduled = :retry_scheduled_false
                      AND retry_count < :max_retries
                      AND status IN (:status_running, :status_failed)
                    RETURNING *
                """),
                {
                    "task_id": task_id,
                    "status_cancelled": TaskStatus.CANCELLED.value,
                    "cancel_requested_true": True,
                    "now": now,
                    "error": parent_error,
                    "retry_scheduled_true": True,
                    "retry_scheduled_false": False,
                    "max_retries": max_retries,
                    "status_running": TaskStatus.RUNNING.value,
                    "status_failed": TaskStatus.FAILED.value,
                },
            ).fetchone()

            if parent_row is None:
                # Parent missing, already has retry_scheduled, retry
                # budget exhausted, or status outside (running, failed).
                # No child inserted; transaction commits empty.
                return None

            # The UPDATE didn't modify retry_count, so the RETURNING row
            # still has the parent's current retry_count.
            current_retry_count = parent_row.retry_count
            new_retry_count = current_retry_count + 1

            # Calculate backoff (same exponential formula as
            # ``schedule_retry``).
            delay_seconds = min(
                backoff_base * (2 ** current_retry_count),
                backoff_max,
            )
            next_retry_at = now + timedelta(seconds=delay_seconds)
            next_retry_at_str = (
                next_retry_at.strftime("%Y-%m-%dT%H:%M:%S.%f")
                + next_retry_at.strftime("%z")
            )

            # Mint fresh child work_id.
            child_work_id = str(uuid.uuid4())

            # Build child INSERT kwargs (same as ``schedule_retry``).
            child_kwargs = {
                "task_type": parent_row.task_type,
                "message_id": parent_row.message_id,
                "retry_count": new_retry_count,
                "next_retry_at": next_retry_at_str,
                "created_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "is_deferred": bool(parent_row.is_deferred)
                if hasattr(parent_row, "is_deferred")
                else False,
                "is_background": bool(parent_row.is_background)
                if hasattr(parent_row, "is_background")
                else False,
            }

            # Run the RetryTurn transition with parent_error to
            # record the force-cancel reason on the parent task. The
            # gate UPDATE above already set the same error value;
            # ``RetryTurn`` re-records it (idempotent — same value).
            transition = RetryTurn(
                parent_work_id=parent_row.work_id,
                child_work_id=child_work_id,
                task_repo=self,
                instance_id=parent_row.instance_id,
                child_kwargs=child_kwargs,
                parent_error=parent_error,
            )
            transition.run(conn)

            # Re-read the child Task row to return as a Task object.
            child_row = conn.execute(
                text("SELECT * FROM task WHERE work_id = :work_id"),
                {"work_id": child_work_id},
            ).fetchone()
            retry_task = self._row_to_task(child_row)

        # AFTER commit — safe to notify workers (see ``complete_task``
        # for rationale). The notification is fired only when a retry
        # child was actually inserted; an empty-result gate UPDATE
        # returns None above and skips the notify.
        if retry_task is not None:
            self._notify_pending_task()

        return retry_task

    def find_orphaned_cancelled_tasks(self) -> list[Task]:
        """Find CANCELLED tasks that never got a retry child.

        These are tasks where:
        - status = 'cancelled'
        - retry_scheduled = False (or the retry_scheduled flag was set but child doesn't exist)
        - retry_count < max_retries (retry should have been scheduled)
        - message_id IS NOT NULL (tasks with NULL message_id don't have associated messages)

        Used by startup recovery to detect crash-before-retry scenarios.
        """
        with self.engine.begin() as conn:
            # Use bound parameter with Python False so the boolean
            # comparison works on both SQLite (INTEGER 0) and PostgreSQL
            # (BOOLEAN false). The previous hard-coded `= 0` raised
            # `psycopg.errors.UndefinedFunction: operator does not exist:
            # boolean = integer` on PostgreSQL.
            stmt = text("""
                SELECT t1.* FROM task t1
                WHERE t1.status = :status_cancelled
                AND t1.retry_scheduled = :retry_scheduled
                AND t1.message_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM task t2
                    WHERE t2.instance_id = t1.instance_id
                    AND t2.message_id = t1.message_id
                    AND t2.retry_count > t1.retry_count
                )
            """)
            rows = conn.execute(stmt, {
                "status_cancelled": TaskStatus.CANCELLED.value,
                "retry_scheduled": False,
            }).fetchall()
            return [self._row_to_task(row) for row in rows]

    def get_retry_chain(self, instance_id: str, message_id: str) -> list[Task]:
        """Get all tasks in a retry chain for debugging."""
        with self.engine.begin() as conn:
            stmt = text("""
                SELECT * FROM task
                WHERE instance_id = :instance_id
                AND message_id = :message_id
                ORDER BY retry_count ASC
            """)
            rows = conn.execute(stmt, {
                "instance_id": instance_id,
                "message_id": message_id,
            }).fetchall()
            return [self._row_to_task(row) for row in rows]

    # --------------------------------------------------------
    # C7 — _status_write_guard (Phase 4c, increment3-plan §6a)
    # --------------------------------------------------------
    # The chokepoint wrappers (D8) close the *accidental* bypass — but
    # a determined future developer writing a one-off UPDATE on
    # ``task.status`` could still reintroduce the bug class. The
    # repository-level write guard is the static defense: it RAISES
    # ``DirectWriteError`` if ``UPDATE task SET status=`` is executed
    # outside a transition context (the thread-local flag in
    # ``turn_transitions._TransitionContext``).
    #
    # Initial Phase 4a state: the guard is DISABLED by default — every
    # direct UPDATE in this repo continues to work. The
    # ``_status_write_guard`` method exists so tests and the eventual
    # Phase 4c rollout can opt in / verify. The flag that controls the
    # global on/off is the module-level ``TURN_RECONCILER_DIRECT_WRITE_PARITY``
    # boolean (see ``daemon.services.feature_flags``).
    #
    # Tests (planned, ships with Phase 4c):
    #   * ``tests/unit/test_write_guard.py::test_direct_status_update_raises``
    #   * ``tests/unit/test_write_guard.py::test_in_transition_context_allows_write``
    #
    # ``_status_write_guard(op)`` is intended to be CALLED from the
    # atomic status-UPDATE helper inside this repository
    # (``complete_task``, ``fail_task``, ``cancel_task``, the various
    # claim / heartbeat / state-change methods). The current Phase 4a
    # implementation does NOT call it from inside the named
    # transitions themselves — the named transitions set the thread-
    # local flag during their ``run()`` execution and the auxiliary
    # column writes inside the chokepoint wrappers naturally inherit
    # that flag.

    @staticmethod
    def _status_write_guard(op: str) -> None:
        """Raise ``DirectWriteError`` if a direct status write is
        attempted outside a transition context AND the global feature
        flag is enabled.

        Phase 4a behavior: NO-OP. The flag is OFF, so the only way to
        trigger this raise is to (a) enable ``TURN_RECONCILER_DIRECT_WRITE_PARITY``
        AND (b) be outside a ``_TransitionContext``. Both are required
        — the flag opt-in keeps the rollout safe (see increment3-plan
        §6a / §6b).

        Reads the flag via ``daemon.services.feature_flags`` module
        reference (NOT a copied module-level binding) so a runtime
        ``monkeypatch.setattr(feature_flags,
        'TURN_RECONCILER_DIRECT_WRITE_PARITY', True)`` flips behavior
        without re-importing the repository module. The same indirection
        handles the thread-local guard read in ``turn_transitions``.

        Args:
            op: Short description of the attempted operation, included
                in the error message (e.g. ``"complete_task direct UPDATE"``).
        """
        # Runtime lookup of the flag (allows tests / deployment
        # scripts to flip the flag without reloading the module).
        try:
            from daemon.services import feature_flags as _ff
        except ImportError:  # pragma: no cover - import path is fixed
            return
        if not getattr(_ff, "TURN_RECONCILER_DIRECT_WRITE_PARITY", False):
            return
        # When the flag is on, check the thread-local guard from
        # ``turn_transitions._TransitionContext``.
        try:
            from daemon.services import turn_transitions as _tt_module
        except ImportError:  # pragma: no cover - import path is fixed
            return
        enabled = getattr(_tt_module._guard, "enabled", False)
        if not enabled:
            raise DirectWriteError(
                f"Direct {op} on task.status is forbidden (C7 write guard). "
                f"Use CompleteTurn / AbortTurn / RetryTurn / BeginTurn / "
                f"ClaimTurn / SuspendTurn / ResumeTurn instead. "
                f"See increment3-plan.md §6a."
            )
