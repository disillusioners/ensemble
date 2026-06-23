"""Task repository for worker pool tasks."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import delete as sql_delete, func, text
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select, col

from ..job_queue.models import JobStatus
from ..instance.models import Instance, InstanceStatus
from .models import Task, TaskStatus, TaskType

# Job type string for MESSAGE jobs. job_type is a free-form string column
# (see JobItem.job_type), not an enum — the canonical job-side query
# (find_processing_message_jobs_by_instance) uses the same literal.
JOB_TYPE_MESSAGE = "message"


logger = logging.getLogger(__name__)


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

        The MESSAGE-job check is critical because the child-completion
        handler enqueues completion reports as tasks, but the parent's
        original user message is typically being processed by the job-queue
        path. Without the cross-system guard, the task forks the checkpoint
        from a stale state and the "Done! 👋" AIMessage produced by the
        original job is shadowed/lost.

        WAITING_CHILDREN carve-out: when a job is mid-flight and the
        instance transitions to ``WAITING_CHILDREN`` (the parent spawned
        children and is awaiting their reports), ``MessageJobHandler``
        defers job completion — the job stays ``PROCESSING`` until the
        instance lifecycle resolves. But the job is *not* driving
        ``graph.astream`` in this window; ``JobFeedbackObserver`` will
        complete the job when the instance finally completes. If we
        blocked on the PROCESSING status alone, the child-completion
        report task would be unable to claim and deliver the child
        result to the parent — a deadlock where the job waits for the
        child report and the child report waits for the job. We therefore
        also check the instance's ``waiting_for > 0`` / ``WAITING_CHILDREN``
        status and only treat the job as a blocker when the instance is
        NOT in that deferred state.

        Unified-dispatcher admission carve-out: in the Phase C/D unified
        dispatcher, the ``JobFeedbackObserver._admit_via_worker_pool``
        admits a MESSAGE job to a Task row BEFORE the worker claims it. The JobItem is
        in ``PROCESSING`` status (set by ``JobProcessor.start_job``) and
        is intended to stay there until the instance lifecycle resolves
        — the Task drives ``graph.astream``, the JobItem is just a FIFO
        placeholder. The cross-system guard above would deadlock: the
        Task can't claim because the MESSAGE job is ``PROCESSING``,
        but the JobItem can't reach its terminal transition because the
        Task never claimed. We therefore also exclude MESSAGE jobs that
        have a corresponding ``task`` row for the same ``message_id``
        with status ``pending`` or ``running`` — the dispatcher has
        already taken ownership, the worker is allowed to claim. The
        ``status IN ('pending', 'running')`` filter (rather than any
        status) is deliberate: a Task in ``COMPLETED`` / ``FAILED`` /
        ``CANCELLED`` is no longer driving ``graph.astream`` for the
        instance, so a new admission would not race. The legacy
        dual-path (where ``MessageJobHandler.handle`` directly drives
        ``graph.astream`` for the parent) does NOT create a Task row
        for the parent message, so the carve-out is inert there.

        ``deleted_at IS NULL`` matches the canonical job-side query
        (``find_processing_message_jobs_by_instance``): a soft-deleted
        PROCESSING job never auto-completes and would otherwise
        permanently block the instance.

        Heartbeat init: ``last_heartbeat_at`` is set to the same value as
        ``started_at`` on claim, so the recovery service can distinguish
        a freshly-claimed task (heartbeat fresh) from a crashed one
        (heartbeat stale). The worker's heartbeat thread keeps updating
        ``last_heartbeat_at`` every ``task_heartbeat_interval_seconds``
        while the task is in flight; the recovery predicate compares
        ``last_heartbeat_at`` to the threshold.

        Args:
            worker_id: ID of the worker claiming the task.

        Returns:
            Claimed Task object or None if no pending tasks ready.
        """
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S.%f") + now.strftime("%z")

        # Build the JSON-extract fragment for the unified-dispatcher
        # admission carve-out. The MESSAGE job's message_id lives in
        # ``job_metadata`` (a JSONBType column mapped to the DB column
        # ``metadata`` — see JobItem.job_metadata's sa_column override),
        # and we need to match it against ``task.message_id`` (a VARCHAR
        # column). The two backends use different syntax:
        #
        #   * PostgreSQL JSONB: ``column->>'key'`` returns TEXT directly.
        #   * SQLite:          ``json_extract(column, '$.key')`` returns
        #                       JSON; we cast to TEXT inline.
        #
        # A missing/NULL ``job_metadata`` produces NULL on both backends
        # and ``NOT EXISTS`` correctly defaults to TRUE (blocker fires).
        json_extract_message_id = self._json_extract_text_sql(
            column="j.metadata", key="message_id"
        )

        with self.engine.begin() as conn:
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
                    AND instance_id NOT IN (
                        SELECT instance_id FROM task
                        WHERE status = :status_running_guard
                    )
                    AND instance_id NOT IN (
                        -- Cross-system guard: a MESSAGE job only blocks the
                        -- task when it is *actively* driving graph.astream.
                        -- When the instance has transitioned to
                        -- WAITING_CHILDREN, the job is just a FIFO placeholder
                        -- (JobFeedbackObserver will complete it when the
                        -- instance lifecycle resolves) — it is NOT holding
                        -- the langgraph thread, so the child-completion
                        -- report task must be allowed to claim and deliver
                        -- the child result. Without this carve-out, the
                        -- job waits for the child report and the child
                        -- report waits for the job: deadlock.
                        --
                        -- ``deleted_at IS NULL`` matches the canonical
                        -- job-side query
                        -- (``find_processing_message_jobs_by_instance``):
                        -- a soft-deleted PROCESSING job never auto-completes
                        -- and would otherwise permanently block the instance.
                        --
                        -- Unified-dispatcher admission carve-out: when the
                        -- unified dispatcher has already admitted the
                        -- MESSAGE job to a Task row (same message_id,
                        -- status pending or running), the job is the
                        -- FIFO placeholder for that admission — it is NOT
                        -- driving graph.astream — so the worker is
                        -- allowed to claim the Task. Without this
                        -- carve-out, the Task can't claim (the job is
                        -- PROCESSING) and the job can't reach its
                        -- terminal transition (the Task never claimed).
                        -- Phase 4: ``waiting_for`` column dropped; the
                        -- ``i.status != waiting_children`` guard is the
                        -- only FIFO carve-out left.
                        SELECT j.instance_id FROM job_queue_items j
                        LEFT JOIN instances i ON j.instance_id = i.instance_id
                        WHERE j.status = :status_processing
                        AND j.job_type = :job_type_message
                        AND j.instance_id IS NOT NULL
                        AND j.deleted_at IS NULL
                        AND (i.status IS NULL OR i.status != :status_waiting_children)
                        AND NOT EXISTS (
                            SELECT 1 FROM task t
                            WHERE t.message_id = {json_extract_message_id}
                            AND t.status IN (:status_pending, :status_running)
                        )
                    )
                    ORDER BY created_at ASC
                    LIMIT 1
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
                "status_processing": JobStatus.PROCESSING.value,
                "job_type_message": JOB_TYPE_MESSAGE,
                "status_waiting_children": InstanceStatus.WAITING_CHILDREN.value,
                "now_str": now_str,
            }).fetchone()

            if row is None:
                return None

            return self._row_to_task(row)

    def _json_extract_text_sql(self, column: str, key: str) -> str:
        """Return a dialect-aware SQL fragment that extracts ``key`` from
        a JSON/JSONB ``column`` as TEXT.

        The two supported backends use different syntax for TEXT
        extraction of a JSON value:

        * PostgreSQL JSONB: ``column->>'key'`` returns TEXT directly
          (no cast needed).
        * SQLite: ``json_extract(column, '$.key')`` returns the JSON
          value; we wrap it in ``CAST(... AS TEXT)`` so the comparison
          against a VARCHAR column (e.g. ``task.message_id``) is
          string-based, matching the ``->>`` semantics on PG.

        Unlike ``_json_path_text`` in ``daemon.repositories.infra.repository``,
        this helper returns a *raw SQL string fragment* (not a
        SQLAlchemy expression) because the call sites here use
        ``text("...")`` and cannot compose with SQLAlchemy expression
        objects without a full query rewrite. The ``key`` value is
        interpolated as a constant — callers MUST pass a static string
        and never a user-supplied value (this method is not
        user-input-safe by design).

        Args:
            column: Bare column reference (e.g. ``"j.job_metadata"``).
                Callers are responsible for aliasing.
            key: Static JSON key to extract.

        Returns:
            SQL fragment suitable for direct interpolation into a
            ``text()`` statement.
        """
        if self.engine.dialect.name == "postgresql":
            return f"{column}->>'{key}'"
        return f"CAST(json_extract({column}, '$.{key}') AS TEXT)"

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

        Args:
            row: Raw database row from UPDATE-RETURNING query.

        Returns:
            Task object.
        """
        # Coerce boolean columns to Python bool explicitly. Raw SQL via
        # RETURNING returns INTEGER 0/1 on SQLite (and the value can be
        # a Python int even on PostgreSQL depending on the driver's
        # type adapter). The Task model declares these fields as
        # ``bool``, and downstream assertions like ``is True`` /
        # ``is False`` rely on actual bool singletons, not ints. The
        # Pydantic coercion path inside Task() is not always invoked
        # here because we construct Task with keyword args directly.
        return Task(
            id=row.id,
            task_type=row.task_type,
            instance_id=row.instance_id,
            message_id=row.message_id,
            status=row.status,
            worker_id=row.worker_id,
            retry_count=row.retry_count if hasattr(row, 'retry_count') else 0,
            next_retry_at=row.next_retry_at if hasattr(row, 'next_retry_at') else None,
            cancel_requested=bool(row.cancel_requested) if hasattr(row, 'cancel_requested') else False,
            cancel_requested_at=row.cancel_requested_at if hasattr(row, 'cancel_requested_at') else None,
            retry_scheduled=bool(row.retry_scheduled) if hasattr(row, 'retry_scheduled') else False,
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

        Atomic SQL UPDATE with WHERE status=running guard — uses
        PostgreSQL EvalPlanQual recheck under READ COMMITTED to prevent
        concurrent transition races (e.g. recovery cancelling the task
        while the worker thread was committing its completion). Returns
        None if the task was not found OR was already in a terminal
        status (COMPLETED/FAILED/CANCELLED) when we tried to update;
        callers handle None as "already transitioned by another worker".

        Args:
            task_id: Task ID.
            result: Result dictionary to store.

        Returns:
            Updated Task object or None if not found or already transitioned.
        """
        now = datetime.now(timezone.utc)
        result_json = json.dumps(result)

        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    UPDATE task
                    SET status = :status_completed,
                        result = :result,
                        completed_at = :completed_at
                    WHERE id = :task_id
                      AND status = :status_running
                    RETURNING *
                    """
                ),
                {
                    "task_id": task_id,
                    "status_completed": TaskStatus.COMPLETED.value,
                    "status_running": TaskStatus.RUNNING.value,
                    "result": result_json,
                    "completed_at": now,
                },
            ).fetchone()

            if row is None:
                return None

            updated = self._row_to_task(row)

        # Notify workers that a pending task may now be claimable.
        # (Sibling tasks for the same instance are unblocked by this terminal
        # transition; without notification they'd wait up to 3s for the next poll.)
        self._notify_pending_task()

        return updated

    def fail_task(self, task_id: int, error: str) -> Task | None:
        """Mark task as failed with error message.

        Atomic SQL UPDATE with WHERE status=running guard — uses
        PostgreSQL EvalPlanQual recheck under READ COMMITTED to prevent
        concurrent transition races. Returns None if the task was not
        found OR was already in a terminal status; callers handle None
        as "already transitioned by another worker".

        Args:
            task_id: Task ID.
            error: Error message.

        Returns:
            Updated Task object or None if not found or already transitioned.
        """
        now = datetime.now(timezone.utc)

        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    UPDATE task
                    SET status = :status_failed,
                        error = :error,
                        completed_at = :completed_at
                    WHERE id = :task_id
                      AND status = :status_running
                    RETURNING *
                    """
                ),
                {
                    "task_id": task_id,
                    "status_failed": TaskStatus.FAILED.value,
                    "status_running": TaskStatus.RUNNING.value,
                    "error": error,
                    "completed_at": now,
                },
            ).fetchone()

            if row is None:
                return None

            updated = self._row_to_task(row)

        # Notify workers (see complete_task for rationale).
        self._notify_pending_task()

        return updated

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
        """Check whether any pending task is blocked by a per-instance guard.

        Returns True if there is at least one PENDING task whose ``instance_id``
        also has a RUNNING task OR an *actively* PROCESSING MESSAGE job. A
        MESSAGE job is only "actively" blocking when the instance is NOT in
        ``WAITING_CHILDREN`` — in that state the job is just a FIFO
        placeholder waiting for the instance lifecycle to resolve, not
        holding the langgraph thread, so a child-completion report task is
        not actually blocked. Used by the worker pool to distinguish "no
        work" from "work exists but instance is busy" in the empty-claim
        path. The job-queue probe joins ``instances`` (via
        ``idx_instances_status``) and the ``job_queue_items.instance_id``
        index, so it stays cheap.

        Phase 5: ``waiting_for`` column was dropped in Phase 4; the
        ``i.status != waiting_children`` guard is the only FIFO carve-out
        left (mirrors ``claim_pending_task``).

        Mirrors the unified-dispatcher admission carve-out in
        :meth:`claim_pending_task`: a PROCESSING MESSAGE job is also NOT
        actively blocking when a corresponding Task row exists for the
        same ``message_id`` with status ``pending`` or ``running`` (the
        unified dispatcher has already taken ownership — see
        ``claim_pending_task`` for full rationale). The two methods MUST
        use the same predicate, otherwise the worker pool makes
        inconsistent idle/busy decisions (spurious wakeups or workers
        sleeping through admissible work).

        Returns:
            True if any pending task is blocked by Fix B's per-instance guard
            (task-level or job-queue-level).
        """
        json_extract_message_id = self._json_extract_text_sql(
            column="j_running.metadata", key="message_id"
        )
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
                            WHERE j_running.status = :status_processing
                            AND j_running.job_type = :job_type_message
                            AND j_running.instance_id = t_pending.instance_id
                            AND j_running.deleted_at IS NULL
                            -- Phase 4/5: ``waiting_for`` column dropped.
                            -- The ``i.status != waiting_children`` guard
                            -- below is the only FIFO carve-out left
                            -- (mirrors ``claim_pending_task``).
                            AND (i.status IS NULL OR i.status != :status_waiting_children)
                            -- Unified-dispatcher admission carve-out
                            -- (mirror of claim_pending_task). A MESSAGE
                            -- job with a corresponding pending/running
                            -- Task row is the FIFO placeholder for an
                            -- admitted dispatch — NOT driving astream —
                            -- so it is NOT actively blocking the
                            -- instance.
                            AND NOT EXISTS (
                                SELECT 1 FROM task t_admitted
                                WHERE t_admitted.message_id = {json_extract_message_id}
                                AND t_admitted.status IN (:status_pending, :status_running)
                            )
                        )
                    )
                )
                LIMIT 1
            """)
            row = conn.execute(stmt, {
                "status_pending": TaskStatus.PENDING.value,
                "status_running": TaskStatus.RUNNING.value,
                "status_processing": JobStatus.PROCESSING.value,
                "job_type_message": JOB_TYPE_MESSAGE,
                "status_waiting_children": InstanceStatus.WAITING_CHILDREN.value,
            }).fetchone()
            return row is not None

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

    def clear_all(self) -> int:
        """Delete all tasks.

        Useful for development to start with a clean task queue on startup.

        Returns:
            Number of tasks deleted.
        """
        with SQLModelSession(self.engine) as db_session:
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
        """Create a new Task for retry with exponential backoff.

        Marks the parent task as CANCELLED with retry_scheduled=True and creates
        a new PENDING task with incremented retry_count and calculated next_retry_at.

        All operations are in a single transaction — crash-safe.

        Atomicity / concurrency: the parent UPDATE carries the full guard
        (``retry_scheduled = false``, ``retry_count < max_retries``, and
        ``status IN ('running','failed')``) directly in the SQL WHERE clause.
        The child INSERT is gated on ``UPDATE.rowcount == 1`` inside the same
        ``engine.begin()`` transaction, so two concurrent callers cannot
        both pass the check and create duplicate retry children — only the
        first UPDATE will match the row and produce a rowcount of 1, and
        only that caller will then INSERT the child. If the UPDATE returns
        0 rows (already retried, max retries exceeded, status not eligible,
        or task not found), this method returns None and does not INSERT.

        Returns the new retry task, or None if no retry was scheduled.
        """
        retry_task = None  # Will be set inside transaction if successful
        now = datetime.now(timezone.utc)

        with self.engine.begin() as conn:
            # Atomic UPDATE: gate the WHOLE operation on the row still
            # matching the preconditions. The status guard
            # (``IN ('running','failed')``) replaces the prior Python-side
            # check and also prevents clobbering a concurrent terminal-state
            # write (e.g. a parallel `complete_task` that set status to
            # 'completed'). Use Python booleans as bound values so the
            # comparison works on both SQLite (INTEGER 0/1) and PostgreSQL
            # (BOOLEAN false/true).
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
                      AND status IN (:status_running, :status_failed)
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
                    "now": now,
                },
            ).fetchone()

            if parent_row is None:
                # Either task not found, already retried, max retries
                # exceeded, or status not in ('running','failed'). In all
                # cases the safe action is the same: do nothing, return None.
                return None

            # The UPDATE didn't modify retry_count, so the RETURNING row
            # still has the parent's current retry_count.
            current_retry_count = parent_row.retry_count
            new_retry_count = current_retry_count + 1

            # Calculate exponential backoff
            delay_seconds = min(
                backoff_base * (2 ** current_retry_count),
                backoff_max
            )
            next_retry_at = now + timedelta(seconds=delay_seconds)
            next_retry_at_str = next_retry_at.strftime("%Y-%m-%dT%H:%M:%S.%f") + next_retry_at.strftime("%z")

            # Create new retry task (column is task_type, not type).
            # Pass Python booleans so the bound parameters are typed
            # correctly for both SQLite and PostgreSQL. This INSERT is
            # in the same transaction as the parent UPDATE above, so
            # both succeed atomically or both roll back.
            result = conn.execute(
                text("""
                    INSERT INTO task (task_type, instance_id, message_id, status,
                                      retry_count, next_retry_at, created_at,
                                      cancel_requested, retry_scheduled)
                    VALUES (:task_type, :instance_id, :message_id, :status_pending,
                            :retry_count, :next_retry_at_str, :created_at,
                            :cancel_requested, :retry_scheduled)
                    RETURNING *
                """),
                {
                    "task_type": parent_row.task_type,
                    "instance_id": parent_row.instance_id,
                    "message_id": parent_row.message_id,
                    "status_pending": TaskStatus.PENDING.value,
                    "retry_count": new_retry_count,
                    "next_retry_at_str": next_retry_at_str,
                    "created_at": now,
                    "cancel_requested": False,
                    "retry_scheduled": False,
                }
            ).fetchone()

            retry_task = self._row_to_task(result)

        # AFTER commit — safe to notify workers
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

        Atomic SQL UPDATE with WHERE status IN (running, pending) guard
        — uses PostgreSQL EvalPlanQual recheck under READ COMMITTED to
        prevent concurrent transition races (e.g. the worker thread
        committing its own completion while recovery is force-cancelling
        the task). Returns None if the task was not found OR was
        already in a terminal status (COMPLETED/FAILED/CANCELLED);
        callers handle None as "already transitioned by another worker".

        Replaces the prior read-then-write pattern (SELECT for current
        status, Python-side check, then blind UPDATE) which was a
        TOCTOU race under PostgreSQL READ COMMITTED — both writers
        could observe status='running' and both commits could succeed,
        producing duplicate / clobbered terminal writes. The single
        guarded UPDATE is race-free on both SQLite and PostgreSQL.

        Used by StaleTaskRecovery when worker doesn't respond to
        cancel_requested flag within grace period.
        """
        now = datetime.now(timezone.utc)

        with self.engine.begin() as conn:
            # Single atomic UPDATE — the WHERE status IN (...) guard
            # makes the conditional read-modify-write a single SQL
            # statement. Use bound parameter with Python True so the
            # boolean column write works on both SQLite (INTEGER 0/1)
            # and PostgreSQL (BOOLEAN false/true).
            row = conn.execute(
                text(
                    """
                    UPDATE task SET
                        status = :status_cancelled,
                        cancel_requested = :cancel_requested,
                        cancel_requested_at = :cancelled_at,
                        completed_at = :completed_at,
                        error = :error
                    WHERE id = :id
                      AND status IN (:status_running, :status_pending)
                    RETURNING *
                    """
                ),
                {
                    "status_cancelled": TaskStatus.CANCELLED.value,
                    "cancel_requested": True,
                    "cancelled_at": now,
                    "completed_at": now,
                    "error": f"Task cancelled: {reason}",
                    "id": task_id,
                    "status_running": TaskStatus.RUNNING.value,
                    "status_pending": TaskStatus.PENDING.value,
                },
            ).fetchone()

            if row is None:
                return None

            result = self._row_to_task(row)

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
        """Atomically cancel a task and schedule a retry in a single transaction.

        Combines cancel_task() + schedule_retry() to prevent the window where
        a crash would leave an orphaned CANCELLED task with no retry child.

        Atomicity / concurrency: same atomic-UPDATE-with-guard pattern as
        ``schedule_retry``. The parent UPDATE is conditional on
        ``retry_scheduled = False AND retry_count < max_retries AND
        status IN ('running','failed')`` so concurrent callers can only
        create one retry child. The child INSERT is gated on
        ``UPDATE.rowcount == 1`` inside the same ``engine.begin()``
        transaction. Use Python booleans as bound values so the boolean
        column writes work on both SQLite (INTEGER 0/1) and PostgreSQL
        (BOOLEAN false/true).

        Returns the new retry task, or None if the parent is missing,
        already has ``retry_scheduled=True``, has
        ``retry_count >= max_retries``, or is not in
        ``('running', 'failed')`` status.
        """
        retry_task = None  # Will be set inside transaction if successful
        now = datetime.now(timezone.utc)

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
                    "error": f"Force cancelled: {reason}",
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

            # Calculate backoff
            delay_seconds = min(
                backoff_base * (2 ** current_retry_count),
                backoff_max,
            )
            next_retry_at = now + timedelta(seconds=delay_seconds)
            next_retry_at_str = (
                next_retry_at.strftime("%Y-%m-%dT%H:%M:%S.%f")
                + next_retry_at.strftime("%z")
            )

            # Create retry child. Same transaction as the parent UPDATE.
            result = conn.execute(
                text("""
                    INSERT INTO task (task_type, instance_id, message_id, status,
                                      retry_count, next_retry_at, created_at,
                                      cancel_requested, retry_scheduled)
                    VALUES (:task_type, :instance_id, :message_id, :status_pending,
                            :retry_count, :next_retry_at_str, :created_at,
                            :cancel_requested, :retry_scheduled)
                    RETURNING *
                """),
                {
                    "task_type": parent_row.task_type,
                    "instance_id": parent_row.instance_id,
                    "message_id": parent_row.message_id,
                    "status_pending": TaskStatus.PENDING.value,
                    "retry_count": new_retry_count,
                    "next_retry_at_str": next_retry_at_str,
                    "created_at": now,
                    "cancel_requested": False,
                    "retry_scheduled": False,
                },
            ).fetchone()

            retry_task = self._row_to_task(result)

        # AFTER commit — safe to notify workers (see complete_task for rationale).
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
