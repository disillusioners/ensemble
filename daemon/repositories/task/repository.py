"""Task repository for worker pool tasks."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import delete as sql_delete, func, text
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select, col

from .models import Task, TaskStatus, TaskType


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
        for the same ``instance_id`` is currently ``RUNNING``. This prevents
        two workers from concurrently processing tasks for the same langgraph
        thread_id, which would race on ``graph.astream`` and shadow channel
        writes in the Postgres checkpointer.

        Args:
            worker_id: ID of the worker claiming the task.

        Returns:
            Claimed Task object or None if no pending tasks ready.
        """
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S.%f") + now.strftime("%z")

        with self.engine.begin() as conn:
            stmt = text("""
                UPDATE task
                SET status = :status_running,
                    worker_id = :worker_id,
                    started_at = :started_at
                WHERE id = (
                    SELECT id FROM task
                    WHERE status = :status_pending
                    AND (next_retry_at IS NULL OR next_retry_at <= :now_str)
                    AND instance_id NOT IN (
                        SELECT instance_id FROM task
                        WHERE status = :status_running_guard
                    )
                    ORDER BY created_at ASC
                    LIMIT 1
                )
                RETURNING *
            """)
            row = conn.execute(stmt, {
                "status_running": TaskStatus.RUNNING.value,
                "worker_id": worker_id,
                "started_at": now,
                "status_pending": TaskStatus.PENDING.value,
                "status_running_guard": TaskStatus.RUNNING.value,
                "now_str": now_str,
            }).fetchone()

            if row is None:
                return None

            return self._row_to_task(row)

    def _row_to_task(self, row) -> Task:
        """Convert a database row to a Task object.

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
            status=row.status,
            worker_id=row.worker_id,
            retry_count=row.retry_count if hasattr(row, 'retry_count') else 0,
            next_retry_at=row.next_retry_at if hasattr(row, 'next_retry_at') else None,
            cancel_requested=row.cancel_requested if hasattr(row, 'cancel_requested') else False,
            cancel_requested_at=row.cancel_requested_at if hasattr(row, 'cancel_requested_at') else None,
            retry_scheduled=row.retry_scheduled if hasattr(row, 'retry_scheduled') else False,
            result=row.result,
            error=row.error,
            created_at=row.created_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
        )

    # --------------------------------------------------------
    # UPDATE STATUS
    # --------------------------------------------------------

    def complete_task(self, task_id: int, result: dict[str, Any]) -> Task | None:
        """Mark task as completed with result.

        Args:
            task_id: Task ID.
            result: Result dictionary to store.

        Returns:
            Updated Task object or None if not found.
        """
        now = datetime.now(timezone.utc)

        with SQLModelSession(self.engine) as db_session:
            task = db_session.get(Task, task_id)
            if task is None:
                return None

            task.status = TaskStatus.COMPLETED.value
            task.result = json.dumps(result)
            task.completed_at = now

            db_session.commit()
            db_session.refresh(task)

        # Notify workers that a pending task may now be claimable.
        # (Sibling tasks for the same instance are unblocked by this terminal
        # transition; without notification they'd wait up to 3s for the next poll.)
        self._notify_pending_task()

        return task

    def fail_task(self, task_id: int, error: str) -> Task | None:
        """Mark task as failed with error message.

        Args:
            task_id: Task ID.
            error: Error message.

        Returns:
            Updated Task object or None if not found.
        """
        now = datetime.now(timezone.utc)

        with SQLModelSession(self.engine) as db_session:
            task = db_session.get(Task, task_id)
            if task is None:
                return None

            task.status = TaskStatus.FAILED.value
            task.error = error
            task.completed_at = now

            db_session.commit()
            db_session.refresh(task)

        # Notify workers (see complete_task for rationale).
        self._notify_pending_task()

        return task

    # --------------------------------------------------------
    # RECOVERY
    # --------------------------------------------------------

    def find_stale_running_tasks(self, threshold_minutes: int = 15) -> list[Task]:
        """Find tasks that have been running too long.

        Used for crash recovery to detect tasks that may have been
        abandoned by crashed workers.

        Args:
            threshold_minutes: Minutes after which a running task is considered stale.

        Returns:
            List of stale running tasks.
        """
        threshold = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)

        with SQLModelSession(self.engine) as db_session:
            stmt = select(Task).where(
                Task.status == TaskStatus.RUNNING.value,
                Task.started_at < threshold,
            )
            return list(db_session.exec(stmt))

    def reset_stale_tasks(self, threshold_minutes: int = 15) -> int:
        """Reset stale running tasks to pending status.

        Used for crash recovery to make abandoned tasks available again.

        Args:
            threshold_minutes: Minutes after which a running task is considered stale.

        Returns:
            Number of tasks reset.
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
                AND started_at < :threshold
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
        also has a RUNNING task. Used by the worker pool to distinguish
        "no work" from "work exists but instance is busy" in the empty-claim
        path. Cheap: two index lookups.

        Returns:
            True if any pending task is blocked by Fix B's per-instance guard.
        """
        with self.engine.begin() as conn:
            stmt = text("""
                SELECT 1
                WHERE EXISTS (
                    SELECT 1 FROM task t_pending
                    WHERE t_pending.status = :status_pending
                    AND EXISTS (
                        SELECT 1 FROM task t_running
                        WHERE t_running.status = :status_running
                        AND t_running.instance_id = t_pending.instance_id
                    )
                )
                LIMIT 1
            """)
            row = conn.execute(stmt, {
                "status_pending": TaskStatus.PENDING.value,
                "status_running": TaskStatus.RUNNING.value,
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

        Returns the new retry task, or None if max retries exceeded or parent
        already has retry_scheduled=True (double-retry guard).
        """
        retry_task = None  # Will be set inside transaction if successful

        with self.engine.begin() as conn:
            # Get parent task
            parent_row = conn.execute(
                text("SELECT * FROM task WHERE id = :id"),
                {"id": task_id}
            ).fetchone()

            if parent_row is None:
                pass  # Let transaction finish, retry_task stays None
            else:
                parent = dict(parent_row._mapping)

                # Check retry_scheduled guard to prevent double-retry.
                # Use `False` (not `0`) as the dict.get() default so the
                # truthiness check is unambiguous across dialects — the value
                # itself is read as a Python bool from both SQLite and
                # PostgreSQL.
                if parent.get("retry_scheduled", False):
                    pass  # Retry already scheduled by another process
                elif parent.get("retry_count", 0) >= max_retries:
                    pass  # Max retries exceeded
                else:
                    current_retry_count = parent.get("retry_count", 0)
                    new_retry_count = current_retry_count + 1

                    # Calculate exponential backoff
                    delay_seconds = min(
                        backoff_base * (2 ** current_retry_count),
                        backoff_max
                    )
                    next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
                    next_retry_at_str = next_retry_at.strftime("%Y-%m-%dT%H:%M:%S.%f") + next_retry_at.strftime("%z")
                    now = datetime.now(timezone.utc)

                    # Mark parent as CANCELLED and set retry_scheduled guard.
                    # Use bound parameters (`:cancel_requested`, `:retry_scheduled`)
                    # with Python booleans so the comparison works on both
                    # SQLite (INTEGER 0/1) and PostgreSQL (BOOLEAN false/true).
                    conn.execute(
                        text("""
                            UPDATE task SET
                                status = :status_cancelled,
                                cancel_requested = :cancel_requested,
                                cancel_requested_at = :cancelled_at,
                                completed_at = :completed_at,
                                retry_scheduled = :retry_scheduled
                            WHERE id = :id
                        """),
                        {
                            "status_cancelled": TaskStatus.CANCELLED.value,
                            "cancel_requested": True,
                            "cancelled_at": now,
                            "completed_at": now,
                            "retry_scheduled": True,
                            "id": task_id,
                        }
                    )

                    # Create new retry task (column is task_type, not type).
                    # Pass Python booleans so the bound parameters are typed
                    # correctly for both SQLite and PostgreSQL.
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
                            "task_type": parent["task_type"],
                            "instance_id": parent["instance_id"],
                            "message_id": parent.get("message_id"),
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
        and haven't been marked for cancellation yet."""
        threshold = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)

        with self.engine.begin() as conn:
            # Use bound parameter with Python False so the boolean
            # comparison works on both SQLite (INTEGER 0) and PostgreSQL
            # (BOOLEAN false).
            stmt = text("""
                SELECT * FROM task
                WHERE status = :status_running
                AND started_at < :threshold
                AND cancel_requested = :cancel_requested
            """)
            rows = conn.execute(stmt, {
                "status_running": TaskStatus.RUNNING.value,
                "threshold": threshold,
                "cancel_requested": False,
            }).fetchall()
            return [self._row_to_task(row) for row in rows]

    def cancel_task(self, task_id: int, reason: str = "") -> Task | None:
        """Directly cancel a task (mark as CANCELLED).

        Used by StaleTaskRecovery when worker doesn't respond to
        cancel_requested flag within grace period.
        """
        now = datetime.now(timezone.utc)
        result = None

        with self.engine.begin() as conn:
            # Check current status
            row = conn.execute(
                text("SELECT * FROM task WHERE id = :id"),
                {"id": task_id}
            ).fetchone()

            if row is None:
                return None

            current = self._row_to_task(row)
            if current.status not in (TaskStatus.RUNNING.value, TaskStatus.PENDING.value):
                return None

            conn.execute(
                text("""
                    UPDATE task SET
                        status = :status_cancelled,
                        cancel_requested = :cancel_requested,
                        cancel_requested_at = :cancelled_at,
                        completed_at = :completed_at,
                        error = :error
                    WHERE id = :id
                """),
                {
                    "status_cancelled": TaskStatus.CANCELLED.value,
                    "cancel_requested": True,
                    "cancelled_at": now,
                    "completed_at": now,
                    "error": f"Task cancelled: {reason}",
                    "id": task_id,
                }
            )

            # Re-fetch to return updated task
            updated_row = conn.execute(
                text("SELECT * FROM task WHERE id = :id"),
                {"id": task_id}
            ).fetchone()
            result = self._row_to_task(updated_row) if updated_row else None

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

        Returns the new retry task, or None if max retries exceeded.
        """
        retry_task = None  # Will be set inside transaction if successful
        now = datetime.now(timezone.utc)

        with self.engine.begin() as conn:
            # Get parent task
            parent_row = conn.execute(
                text("SELECT * FROM task WHERE id = :id"),
                {"id": task_id}
            ).fetchone()

            if parent_row is None:
                pass  # Let transaction finish, retry_task stays None
            else:
                parent = dict(parent_row._mapping)

                # Check guards. Use `False` (not `0`) as the dict.get()
                # default so the truthiness check is unambiguous across
                # dialects — the value itself is read as a Python bool from
                # both SQLite and PostgreSQL.
                if parent.get("retry_scheduled", False):
                    pass  # Already has retry scheduled
                elif parent.get("retry_count", 0) >= max_retries:
                    pass  # Max retries exceeded
                else:
                    current_retry_count = parent.get("retry_count", 0)
                    new_retry_count = current_retry_count + 1

                    # Calculate backoff
                    delay_seconds = min(
                        backoff_base * (2 ** current_retry_count),
                        backoff_max
                    )
                    next_retry_at = now + timedelta(seconds=delay_seconds)
                    next_retry_at_str = next_retry_at.strftime("%Y-%m-%dT%H:%M:%S.%f") + next_retry_at.strftime("%z")

                    # Force-cancel parent and set retry_scheduled guard.
                    # Use bound parameters with Python booleans so the
                    # boolean column writes work on both SQLite
                    # (INTEGER 0/1) and PostgreSQL (BOOLEAN false/true).
                    conn.execute(
                        text("""
                            UPDATE task SET
                                status = :status_cancelled,
                                cancel_requested = :cancel_requested,
                                cancel_requested_at = :now,
                                completed_at = :now,
                                error = :error,
                                retry_scheduled = :retry_scheduled
                            WHERE id = :id
                        """),
                        {
                            "status_cancelled": TaskStatus.CANCELLED.value,
                            "cancel_requested": True,
                            "now": now,
                            "error": f"Force cancelled: {reason}",
                            "retry_scheduled": True,
                            "id": task_id,
                        }
                    )

                    # Create retry child. Pass Python booleans so the
                    # bound parameters are typed correctly for both
                    # SQLite and PostgreSQL.
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
                            "task_type": parent["task_type"],
                            "instance_id": parent["instance_id"],
                            "message_id": parent.get("message_id"),
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
