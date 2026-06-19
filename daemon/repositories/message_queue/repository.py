"""SQLModel-based MessageQueue Repository implementation."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete as sql_delete, func, and_, or_, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, select, col

from .models import MessageQueue, MessageStatus

# Configuration constants
MESSAGE_TIMEOUT_SECONDS = 3600  # 1 hour


def _coerce_datetime(value: Any) -> datetime | None:
    """Coerce a RETURNING column value to a ``datetime`` (or ``None``).

    - ``None`` passes through.
    - ``datetime`` instances pass through (PostgreSQL native).
    - ISO-8601 strings (SQLite storage format) are parsed; values
      without a timezone offset are assumed UTC because the
      ``MessageQueue`` model writes ``datetime.now(timezone.utc)``
      everywhere.
    """
    if value is None or isinstance(value, datetime):
        return value
    raw = str(value)
    # Python's fromisoformat accepts "+00:00" suffixes in 3.11+ but
    # not "Z"; normalise "Z" -> "+00:00" for cross-version safety.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _coerce_json(value: Any, *, as_dict: bool = False, as_list: bool = False) -> Any:
    """Coerce a RETURNING JSON column value to its expected Python type.

    - ``None`` passes through.
    - ``dict`` / ``list`` (PostgreSQL native) pass through.
    - JSON-encoded strings (SQLite storage format) are parsed. If
      the column is empty text (some SQLite/JSON adapter edge
      cases) we treat it as the default — empty dict for
      ``message_metadata``, ``None`` for ``images`` — matching
      the model's declared defaults.
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    raw = str(value)
    if raw == "":
        return {} if as_dict else (None if as_list else None)
    parsed = json.loads(raw)
    if as_dict and not isinstance(parsed, dict):
        return {}
    if as_list and not isinstance(parsed, list):
        return None
    return parsed


class SQLModelMessageQueueRepository:
    """SQLModel-based MessageQueue repository for queue operations."""
    
    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def enqueue(
        self,
        instance_id: str,
        content: str,
        source: str,
        priority: int = 1,
        max_retries: int = 5,
        message_metadata: dict[str, Any] | None = None,
        message_id: str | None = None,
        images: list[str] | None = None,
    ) -> MessageQueue:
        """Add a message to the queue."""
        message_id = message_id or str(uuid.uuid4())
        
        message = MessageQueue(
            message_id=message_id,
            instance_id=instance_id,
            content=content,
            source=source,
            status=MessageStatus.READY.value,
            priority=priority,
            max_retries=max_retries,
            message_metadata=message_metadata or {},
            images=images,
            enqueued_at=datetime.now(timezone.utc),
        )

        with Session(self.engine) as session:
            session.add(message)
            session.commit()
            session.refresh(message)

        return message

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get(self, message_id: str) -> MessageQueue | None:
        """Get a message by ID."""
        with Session(self.engine) as session:
            return session.get(MessageQueue, message_id)

    def get_by_instance(self, instance_id: str) -> list[MessageQueue]:
        """Get all messages for an instance."""
        with Session(self.engine) as session:
            stmt = select(MessageQueue).where(
                MessageQueue.instance_id == instance_id
            ).order_by(col(MessageQueue.enqueued_at).desc())
            return list(session.exec(stmt))

    def get_by_id(self, message_id: str) -> MessageQueue | None:
        """Get a message by ID (alias for get)."""
        return self.get(message_id)

    # --------------------------------------------------------
    # DEQUEUE (get next ready message)
    # --------------------------------------------------------

    def dequeue(self, instance_id: str | None = None) -> MessageQueue | None:
        """Atomically claim and return the next ready message for processing.

        Selection criteria (a message is eligible when ALL are true):
        - ``status = 'ready'``
        - ``next_retry_at IS NULL OR next_retry_at <= now`` (due)
        - ``instance_id = :instance_id`` when provided

        Ordered by ``priority ASC, enqueued_at ASC``; ``LIMIT 1``.

        Atomic claim: ``UPDATE ... WHERE message_id = (SELECT ...) AND
        status = 'ready' RETURNING *``. The outer ``AND status = 'ready'``
        is the EvalPlanQual guard — under PostgreSQL READ COMMITTED, if
        a concurrent worker has already claimed the candidate row, the
        outer guard re-evaluates against the post-lock row state and the
        UPDATE matches zero rows. SQLite achieves the same end via
        write serialisation. Either way, at most one worker observes a
        non-None result for any given message.

        Returns:
            The claimed message in ``processing`` status, or ``None``
            if no eligible message is currently available.
        """
        now = datetime.now(timezone.utc)

        # Subquery selects the next eligible message. Built with
        # string concatenation rather than SQLAlchemy ORM so the
        # ``ORDER BY priority ASC, enqueued_at ASC LIMIT 1`` semantics
        # are crystal-clear and identical between SQLite and PostgreSQL.
        select_parts = [
            "SELECT message_id FROM message_queue",
            "WHERE status = :status_ready",
            "AND (next_retry_at IS NULL OR next_retry_at <= :now)",
        ]
        params: dict[str, Any] = {
            "status_ready": MessageStatus.READY.value,
            "status_ready_guard": MessageStatus.READY.value,
            "status_processing": MessageStatus.PROCESSING.value,
            "now": now,
            "processing_started_at": now,
            "last_activity_at": now,
        }
        if instance_id is not None:
            select_parts.append("AND instance_id = :instance_id")
            params["instance_id"] = instance_id
        select_parts.append("ORDER BY priority ASC, enqueued_at ASC LIMIT 1")
        select_subquery = " ".join(select_parts)

        update_sql = (
            "UPDATE message_queue "
            "SET status = :status_processing, "
            "    processing_started_at = :processing_started_at, "
            "    last_activity_at = :last_activity_at "
            f"WHERE message_id = ({select_subquery}) "
            "  AND status = :status_ready_guard "
            "RETURNING *"
        )

        with self.engine.begin() as conn:
            row = conn.execute(text(update_sql), params).fetchone()
            if row is None:
                return None
            return self._row_to_message(row)
    
    def find_stuck_messages(self) -> list[MessageQueue]:
        """Find all stuck processing messages.

        A message is stuck when status='processing' AND:
        - ``last_activity_at IS NULL`` (the worker never reported any
          activity since claiming the message), OR
        - ``last_activity_at < timeout_threshold`` (no heartbeat within
          ``MESSAGE_TIMEOUT_SECONDS`` of now)

        Both branches of the OR are evaluated against the SAME
        timestamp column (``last_activity_at``). The previous
        implementation OR'd ``last_activity_at IS NULL`` with
        ``processing_started_at < threshold`` and then AND'd the whole
        expression with ``last_activity_at < threshold``, which made
        the NULL branch unreachable (``NULL < threshold`` evaluates to
        NULL, which fails the AND).

        Args:
            timeout_seconds: Timeout threshold in seconds.

        Returns:
            List of stuck messages.
        """
        timeout_threshold = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS)

        with Session(self.engine) as session:
            stmt = select(MessageQueue).where(
                MessageQueue.status == MessageStatus.PROCESSING.value,
                or_(
                    MessageQueue.last_activity_at.is_(None),
                    MessageQueue.last_activity_at < timeout_threshold,
                ),
            )
            return list(session.exec(stmt))
    
    def find_retry_ready_messages(self) -> list[MessageQueue]:
        """Find all retry-ready messages that can be moved to ready.
        
        Returns:
            List of messages with next_retry_at <= now.
        """
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session:
            stmt = select(MessageQueue).where(
                MessageQueue.status == MessageStatus.RETRYING.value
            ).where(MessageQueue.next_retry_at <= now)
            return list(session.exec(stmt))
    
    def move_retry_ready_to_ready(self, message_ids: list[str]) -> int:
        """Move retry-ready messages back to ready status.
        
        Args:
            message_ids: List of message IDs to update.
            
        Returns:
            Number of messages moved.
        """
        count = 0
        with Session(self.engine) as session:
            for msg_id in message_ids:
                message = session.get(MessageQueue, msg_id)
                if message:
                    message.status = MessageStatus.READY.value
                    message.next_retry_at = None
                    count += 1
            session.commit()
        return count
    
    def fail_stuck_message(self, message_id: str, error_message: str) -> MessageQueue | None:
        """Mark a stuck message as permanently failed.
        
        Args:
            message_id: The message ID to fail.
            error_message: The error message describing the failure.
            
        Returns:
            The updated message or None if not found.
        """
        with Session(self.engine) as session:
            message = session.get(MessageQueue, message_id)
            if message is None:
                return None
            
            message.status = MessageStatus.FAILED.value
            message.error_message = error_message
            message.completed_at = datetime.now(timezone.utc)
            
            session.commit()
            session.refresh(message)
            
            return message
    
    def schedule_retry_for_stuck(self, message_id: str, retry_count: int, error_message: str) -> MessageQueue | None:
        """Schedule a stuck message for retry.
        
        Args:
            message_id: The message ID to retry.
            retry_count: The new retry count.
            error_message: The error message from the stuck condition.
            
        Returns:
            The updated message or None if not found.
        """
        with Session(self.engine) as session:
            message = session.get(MessageQueue, message_id)
            if message is None:
                return None
            
            # Update retry count (retry_count > 0 is the canonical check for retry status)
            message.retry_count = retry_count
            
            # Exponential backoff: 1min, 2min, 4min, 8min, etc.
            delay = min(60 * (2 ** (retry_count - 1)), 3600)  # Max 1 hour
            message.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            message.status = MessageStatus.RETRYING.value
            message.processing_started_at = None
            message.error_message = error_message
            
            session.commit()
            session.refresh(message)
            
            return message

    def dequeue_by_instance(self, instance_id: str) -> MessageQueue | None:
        """Get the next ready message for a specific instance.
        
        This is a convenience wrapper around dequeue() for instance-specific dequeue.
        
        Args:
            instance_id: The instance ID to dequeue from.
            
        Returns:
            The next ready message for the instance, or None if no messages available.
        """
        return self.dequeue(instance_id=instance_id)

    # --------------------------------------------------------
    # ROW MAPPING
    # --------------------------------------------------------

    def _row_to_message(self, row) -> MessageQueue:
        """Convert a database row (UPDATE-RETURNING / SELECT) to a
        ``MessageQueue`` model instance.

        The model declares ``message_metadata`` as a Python attribute
        that maps to the DB column ``metadata`` (via
        ``sa_column=Column("metadata", JSON)``) — we read the value
        from ``row._mapping`` to avoid the ``Row.metadata`` shadow
        attribute that SQLAlchemy reserves on Row objects.

        Type coercion: raw SQL via ``RETURNING`` returns
        ``datetime``/``JSON`` columns as native Python types on
        PostgreSQL, but as raw strings on SQLite (which has no
        native datetime/JSON types and stores them as text). The
        helpers below normalise both shapes into proper ``datetime``
        / ``dict`` / ``list`` objects so callers (and the model
        itself) see a consistent API regardless of dialect.
        """
        mapping = row._mapping
        return MessageQueue(
            message_id=mapping["message_id"],
            instance_id=mapping["instance_id"],
            content=mapping["content"],
            type=mapping["type"],
            source=mapping["source"],
            root_source=mapping["root_source"],
            status=mapping["status"],
            priority=mapping["priority"],
            retry_count=mapping["retry_count"],
            max_retries=mapping["max_retries"],
            error_message=mapping["error_message"],
            last_error=mapping["last_error"],
            message_metadata=_coerce_json(mapping["metadata"], as_dict=True),
            images=_coerce_json(mapping["images"], as_list=True),
            enqueued_at=_coerce_datetime(mapping["enqueued_at"]),
            processing_started_at=_coerce_datetime(mapping["processing_started_at"]),
            last_activity_at=_coerce_datetime(mapping["last_activity_at"]),
            completed_at=_coerce_datetime(mapping["completed_at"]),
            next_retry_at=_coerce_datetime(mapping["next_retry_at"]),
            processing_task_id=mapping["processing_task_id"],
        )

    # --------------------------------------------------------
    # UPDATE STATUS
    # --------------------------------------------------------

    def complete(self, message_id: str) -> MessageQueue | None:
        """Atomically mark message as completed.

        Uses a guarded UPDATE (``status = 'processing'``) so a concurrent
        worker that already completed/failed/retrying the message cannot
        have its terminal status clobbered. Returns ``None`` if the row
        does not exist OR its current status is not ``processing``.
        """
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    UPDATE message_queue
                    SET status = :status_completed,
                        completed_at = :completed_at
                    WHERE message_id = :message_id
                      AND status = :status_processing
                    RETURNING *
                    """
                ),
                {
                    "message_id": message_id,
                    "status_completed": MessageStatus.COMPLETED.value,
                    "status_processing": MessageStatus.PROCESSING.value,
                    "completed_at": now,
                },
            ).fetchone()
            if row is None:
                return None
            return self._row_to_message(row)

    def fail(self, message_id: str, error_message: str) -> MessageQueue | None:
        """Atomically mark message as failed with error message.

        Uses a guarded UPDATE (``status = 'processing'``) so a concurrent
        worker that already completed/failed/retrying the message cannot
        have its terminal status clobbered. Returns ``None`` if the row
        does not exist OR its current status is not ``processing``.
        """
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    UPDATE message_queue
                    SET status = :status_failed,
                        error_message = :error_message,
                        completed_at = :completed_at
                    WHERE message_id = :message_id
                      AND status = :status_processing
                    RETURNING *
                    """
                ),
                {
                    "message_id": message_id,
                    "status_failed": MessageStatus.FAILED.value,
                    "status_processing": MessageStatus.PROCESSING.value,
                    "error_message": error_message,
                    "completed_at": now,
                },
            ).fetchone()
            if row is None:
                return None
            return self._row_to_message(row)

    def retry(self, message_id: str, error_message: str | None = None) -> MessageQueue | None:
        """Atomically transition a failed message to READY for retry.

        Two guarded UPDATEs may be issued (in order):

        1. ``WHERE status='failed' AND retry_count >= max_retries`` —
           marks the message permanently FAILED with a "Max retries (N)
           exceeded" error (mirrors the prior Python-side branch).
        2. ``WHERE status='failed' AND retry_count < max_retries`` —
           increments ``retry_count`` atomically (``retry_count + 1``)
           and schedules the next retry using exponential backoff.

        A follow-up ``SELECT`` reads the current ``retry_count`` and
        ``max_retries`` to compute the backoff delay (and the
        "Max retries (N)" message). If the row was concurrently
        advanced to ``max_retries`` between the SELECT and the UPDATE,
        the WHERE guard rejects our write and the method returns
        ``None`` — the caller can re-invoke ``retry`` and pick up
        branch (1) on the next attempt.

        Returns:
            The updated message, or ``None`` if the message does not
            exist or is not in ``failed`` status.
        """
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            # Branch 1: max retries already exceeded — mark as FAILED.
            # We compute the error message in SQL so the
            # max_retries value from the row is included verbatim
            # (preserves the prior Python-side format).
            row = conn.execute(
                text(
                    """
                    UPDATE message_queue
                    SET status = :status_failed,
                        error_message = 'Max retries (' || max_retries || ') exceeded',
                        completed_at = :completed_at
                    WHERE message_id = :message_id
                      AND status = :status_failed_guard
                      AND retry_count >= max_retries
                    RETURNING *
                    """
                ),
                {
                    "message_id": message_id,
                    "status_failed": MessageStatus.FAILED.value,
                    "status_failed_guard": MessageStatus.FAILED.value,
                    "completed_at": now,
                },
            ).fetchone()
            if row is not None:
                return self._row_to_message(row)

            # Branch 2: read current retry_count to compute backoff delay.
            # If the row was concurrently removed from FAILED status, the
            # UPDATE below will match zero rows and we return None.
            current = conn.execute(
                text(
                    """
                    SELECT retry_count, max_retries
                    FROM message_queue
                    WHERE message_id = :message_id
                      AND status = :status_failed_guard
                    """
                ),
                {
                    "message_id": message_id,
                    "status_failed_guard": MessageStatus.FAILED.value,
                },
            ).fetchone()
            if current is None:
                return None

            current_retry_count = int(current[0])
            # Exponential backoff: 1min, 2min, 4min, 8min, … capped at 1h.
            # Uses the OLD retry_count (before increment) — matches the
            # prior Python implementation's semantics exactly.
            delay = min(60 * (2 ** current_retry_count), 3600)
            next_retry_at = now + timedelta(seconds=delay)

            row = conn.execute(
                text(
                    """
                    UPDATE message_queue
                    SET status = :status_ready,
                        retry_count = retry_count + 1,
                        error_message = :error_message,
                        next_retry_at = :next_retry_at,
                        processing_started_at = NULL
                    WHERE message_id = :message_id
                      AND status = :status_failed_guard
                      AND retry_count < max_retries
                    RETURNING *
                    """
                ),
                {
                    "message_id": message_id,
                    "status_ready": MessageStatus.READY.value,
                    "status_failed_guard": MessageStatus.FAILED.value,
                    "error_message": error_message,
                    "next_retry_at": next_retry_at,
                },
            ).fetchone()
            if row is None:
                return None
            return self._row_to_message(row)

    def update_activity(self, message_id: str) -> MessageQueue | None:
        """Atomically refresh last_activity_at for a processing message.

        Uses a guarded UPDATE (``status = 'processing'``) so a message
        that has been completed/failed/retrying concurrently will not
        have its activity timestamp refreshed. Returns ``None`` if the
        row does not exist OR its current status is not ``processing``.
        """
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    UPDATE message_queue
                    SET last_activity_at = :last_activity_at
                    WHERE message_id = :message_id
                      AND status = :status_processing
                    RETURNING *
                    """
                ),
                {
                    "message_id": message_id,
                    "status_processing": MessageStatus.PROCESSING.value,
                    "last_activity_at": now,
                },
            ).fetchone()
            if row is None:
                return None
            return self._row_to_message(row)
    
    def get_status(self, message_id: str) -> str | None:
        """Get the current status of a message.
        
        Args:
            message_id: The message ID to check.
            
        Returns:
            The status string or None if not found.
        """
        with Session(self.engine) as session:
            message = session.get(MessageQueue, message_id)
            if message is None:
                return None
            return message.status
    
    def is_empty(self, instance_id: str) -> bool:
        """Check if the queue is empty for an instance.
        
        Returns True if there are no ready, processing, or retry-ready messages.
        """
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session:
            stmt = select(func.count()).select_from(MessageQueue).where(
                MessageQueue.instance_id == instance_id
            ).where(
                or_(
                    MessageQueue.status == MessageStatus.READY.value,
                    MessageQueue.status == MessageStatus.PROCESSING.value,
                    and_(
                        MessageQueue.status == MessageStatus.RETRYING.value,
                        MessageQueue.next_retry_at <= now
                    )
                )
            )
            count = session.exec(stmt).one()
            return count == 0

    # --------------------------------------------------------
    # LIST
    # --------------------------------------------------------

    def list(
        self,
        status: str | None = None,
        instance_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MessageQueue]:
        """List messages with optional filters."""
        with Session(self.engine) as session:
            stmt = select(MessageQueue)
            
            if status:
                stmt = stmt.where(MessageQueue.status == status)
            if instance_id:
                stmt = stmt.where(MessageQueue.instance_id == instance_id)

            stmt = stmt.order_by(
                col(MessageQueue.priority).asc(),
                col(MessageQueue.enqueued_at).asc()
            ).offset(offset).limit(limit)
            
            return list(session.exec(stmt))

    def list_ready(self, limit: int = 100) -> list[MessageQueue]:
        """List all ready messages."""
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session:
            stmt = (
                select(MessageQueue)
                .where(MessageQueue.status == MessageStatus.READY.value)
                .where(
                    (MessageQueue.next_retry_at.is_(None))
                    | (MessageQueue.next_retry_at <= now)
                )
                .order_by(
                    col(MessageQueue.priority).asc(),
                    col(MessageQueue.enqueued_at).asc()
                )
                .limit(limit)
            )
            return list(session.exec(stmt))

    def list_pending(self, instance_id: str | None = None, limit: int = 100) -> list[MessageQueue]:
        """List pending (ready or processing) messages."""
        with Session(self.engine) as session:
            stmt = select(MessageQueue).where(
                (MessageQueue.status == MessageStatus.READY.value)
                | (MessageQueue.status == MessageStatus.PROCESSING.value)
            )

            if instance_id:
                stmt = stmt.where(MessageQueue.instance_id == instance_id)

            stmt = stmt.order_by(
                col(MessageQueue.priority).asc(),
                col(MessageQueue.enqueued_at).asc()
            ).limit(limit)

            return list(session.exec(stmt))

    def get_pending_for_instances(
        self, instance_ids: list[str]
    ) -> list[tuple[str, str]]:
        """Get (instance_id, message_id) pairs for pending messages.

        Used by CorrelationManager.rebuild_from_db() to reconstruct
        correlation keys with real message_id UUIDs.

        Args:
            instance_ids: Instance IDs to filter by. Empty list returns
                an empty result.

        Returns:
            List of (instance_id, message_id) tuples for messages whose
            status is READY, PROCESSING, or RETRYING.
        """
        if not instance_ids:
            return []
        with Session(self.engine) as session:
            stmt = (
                select(MessageQueue.instance_id, MessageQueue.message_id)
                .where(MessageQueue.instance_id.in_(instance_ids))
                .where(MessageQueue.status.in_([
                    MessageStatus.READY.value,
                    MessageStatus.PROCESSING.value,
                    MessageStatus.RETRYING.value,
                ]))
            )
            rows = session.exec(stmt).all()
            return [(row[0], row[1]) for row in rows]

    # --------------------------------------------------------
    # CLEANUP
    # --------------------------------------------------------

    def cleanup_old(self, max_age_hours: int = 24) -> int:
        """Remove old completed/failed messages from the queue.
        
        Args:
            max_age_hours: Maximum age of completed/failed messages to keep.
            
        Returns:
            Number of messages deleted.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        
        with Session(self.engine) as session:
            stmt = sql_delete(MessageQueue).where(
                MessageQueue.status.in_([
                    MessageStatus.COMPLETED.value,
                    MessageStatus.FAILED.value
                ])
            ).where(MessageQueue.completed_at < cutoff)
            
            result = session.exec(stmt)
            session.commit()
            
            return result.rowcount

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def delete(self, message_id: str) -> dict[str, Any]:
        """Delete a message from the queue."""
        with Session(self.engine) as session:
            message = session.get(MessageQueue, message_id)
            if message is None:
                return {"deleted": False, "message_id": message_id, "error": "Not found"}

            session.delete(message)
            session.commit()

            return {
                "deleted": True,
                "message_id": message_id,
            }

    def delete_by_instance(self, instance_id: str) -> int:
        """Delete all messages for an instance."""
        with Session(self.engine) as session:
            stmt = sql_delete(MessageQueue).where(MessageQueue.instance_id == instance_id)
            result = session.exec(stmt)
            session.commit()
            
            return result.rowcount

    def clear_all(self) -> int:
        """Delete all messages from the queue.
        
        Useful for development to start with a clean queue on startup.
        
        Returns:
            Number of messages deleted.
        """
        with Session(self.engine) as session:
            stmt = sql_delete(MessageQueue)
            result = session.exec(stmt)
            session.commit()
            
            return result.rowcount

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    def count_by_status(self) -> dict[str, int]:
        """Get count of messages by status."""
        counts = {}
        with Session(self.engine) as session:
            for status in MessageStatus:
                stmt = select(func.count()).select_from(MessageQueue).where(
                    MessageQueue.status == status.value
                )
                counts[status.value] = session.exec(stmt).one()
        return counts

    def count_pending(self) -> int:
        """Get count of pending (ready + processing) messages."""
        with Session(self.engine) as session:
            stmt = select(func.count()).select_from(MessageQueue).where(
                (MessageQueue.status == MessageStatus.READY.value)
                | (MessageQueue.status == MessageStatus.PROCESSING.value)
            )
            return session.exec(stmt).one()
    
    def get_stats(self, instance_id: str) -> dict[str, Any]:
        """Get queue statistics for an instance.
        
        Returns a dict with:
        - pending_count: Number of ready + retry-ready messages
        - processing_count: Number of processing messages
        - oldest_message_age_seconds: Age of oldest message in seconds (or None)
        """
        now = datetime.now(timezone.utc)
        
        with Session(self.engine) as session:
            # Pending count (ready + retrying with next_retry_at <= now)
            pending_stmt = select(func.count()).select_from(MessageQueue).where(
                MessageQueue.instance_id == instance_id
            ).where(
                or_(
                    MessageQueue.status == MessageStatus.READY.value,
                    and_(
                        MessageQueue.status == MessageStatus.RETRYING.value,
                        MessageQueue.next_retry_at <= now
                    )
                )
            )
            pending_count = session.exec(pending_stmt).one()
            
            # Processing count
            processing_stmt = select(func.count()).select_from(MessageQueue).where(
                MessageQueue.instance_id == instance_id
            ).where(
                MessageQueue.status == MessageStatus.PROCESSING.value
            )
            processing_count = session.exec(processing_stmt).one()
            
            # Oldest message age
            oldest_stmt = select(func.min(MessageQueue.enqueued_at)).where(
                MessageQueue.instance_id == instance_id
            ).where(
                MessageQueue.status.in_([
                    MessageStatus.READY.value,
                    MessageStatus.PROCESSING.value,
                    MessageStatus.RETRYING.value
                ])
            )
            oldest = session.exec(oldest_stmt).one()
            oldest_message_age_seconds = None
            if oldest:
                # Make oldest timezone-aware if it's naive (stored without tz in DB)
                if oldest.tzinfo is None:
                    oldest = oldest.replace(tzinfo=timezone.utc)
                oldest_message_age_seconds = (now - oldest).total_seconds()
            
            return {
                "pending_count": pending_count,
                "processing_count": processing_count,
                "oldest_message_age_seconds": oldest_message_age_seconds
            }
