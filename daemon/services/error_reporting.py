"""Error reporting service for handling and reporting errors to parent instances."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlmodel import Session

from ..repositories.instance.models import Instance, InstanceStatus
from ..repositories.message_queue.models import MessageQueue, MessageStatus
from ..write_pause_guard import WriteGuardSession

if TYPE_CHECKING:
    from ..config import Config
    from ..persistence import MessageQueueRepository
    from ..repositories.instance.repository import SQLModelInstanceRepository
    from .event_publisher import EventPublisherService


logger = logging.getLogger(__name__)

# Error report severity classification
CRITICAL_ERROR_TYPES = frozenset({"max_retries_exceeded", "circuit_breaker_open"})
RECOVERABLE_ERROR_TYPES = frozenset({"watchdog_timeout", "circuit_breaker_open"})


class ErrorReportingService:
    """Service for handling and reporting errors to parent instances.
    
    Called when a child instance encounters an unrecoverable error:
    - Max retries exceeded
    - Stale task recovery failure
    - Cancellation (shutdown, user request)
    - Circuit breaker opened (via CircuitOpenError)
    - Unhandled exception
    
    This service:
    - Checks for duplicate error reports (idempotency)
    - Fetches metadata outside transaction
    - Performs atomic DB update: child status, message status, parent counter/cache,
      hierarchy deletion, and parent cascade
    - Enqueues error report message to parent
    - Broadcasts child_failed SSE event
    """

    def __init__(
        self,
        manager: "InstanceManager",
        events_service: "EventPublisherService | None" = None,
    ):
        """Initialize the error reporting service.
        
        Args:
            manager: The InstanceManager facade.
            events_service: Optional event publisher service for lifecycle events.
        """
        self._manager = manager
        self._events_service = events_service

    @property
    def _config(self) -> "Config":
        """Access config through manager for test mockability."""
        return self._manager.config

    @property
    def _instance_repository(self) -> "SQLModelInstanceRepository":
        """Access instance repository through manager for test mockability."""
        return self._manager._instance_repository

    @property
    def _queue_repository(self) -> "MessageQueueRepository":
        """Access queue repository through manager for test mockability."""
        return self._manager._queue_repository

    async def _send_error_report(
        self, 
        instance_id: str, 
        error: str,
        error_type: str = "execution_error",
        message_id: str | None = None
    ) -> None:
        """Send error report to parent instance when child fails permanently.
        
        Args:
            instance_id: The child instance ID that has failed.
            error: The error message describing what went wrong.
            error_type: Category of error (e.g., "max_retries", "timeout", "circuit_breaker").
            message_id: Optional message ID that triggered the error.
        """
        from sqlalchemy import func, select
        from ..repositories.instance.repository import get_agent_name
        
        try:
            # Step 1: Dedup check - prevent duplicate error reports
            # First try message_id-based dedup (most precise)
            dedup_key = f"internal_error_report:{instance_id}"
            dedup_source_filter = message_id  # Use None if no message_id

            if message_id:
                meta_check = await asyncio.to_thread(self._instance_repository.get, instance_id)
                if meta_check and meta_check.parent_id:
                    # Check for existing error report in parent's queue
                    existing = await asyncio.to_thread(
                        self._queue_repository.list,
                        instance_id=meta_check.parent_id,
                        status="ready",
                        limit=10
                    )
                    for existing_msg in existing:
                        if existing_msg.source == dedup_key:
                            logger.debug(f"Error report already queued for instance {instance_id[:8]}..., skipping duplicate")
                            return
            else:
                # Fallback: dedup by instance_id + error_type when no message_id
                # This prevents duplicate reports when the same instance fails multiple times
                # without an associated message
                meta_check = await asyncio.to_thread(self._instance_repository.get, instance_id)
                if meta_check and meta_check.parent_id:
                    existing = await asyncio.to_thread(
                        self._queue_repository.list,
                        instance_id=meta_check.parent_id,
                        status="ready",
                        limit=10
                    )
                    for existing_msg in existing:
                        # Match: same instance + same error_type
                        msg_metadata = existing_msg.message_metadata or {}
                        if (existing_msg.source == dedup_key and
                                msg_metadata.get("error_type") == error_type):
                            logger.debug(
                                f"Error report already queued for instance {instance_id[:8]}... "
                                f"(type={error_type}), skipping duplicate"
                            )
                            return
            
            # Step 2: Fetch metadata outside transaction
            meta = await asyncio.to_thread(self._instance_repository.get, instance_id)
            if not meta:
                logger.warning(f"Cannot send error report: instance {instance_id} not found")
                return
            
            parent_id = meta.parent_id
            if not parent_id:
                logger.debug(f"Instance {instance_id} has no parent, skipping error report")
                return
            
            agent_name = meta.agent_name or get_agent_name(meta.agent_dir)
            
            logger.info(f"Instance {instance_id[:8]}... failed, sending error report to parent {parent_id[:8]}...")
            
            # Compute these before transaction to avoid issues if computation fails
            truncated_error = error[:2000] if len(error) > 2000 else error
            severity = "critical" if error_type in CRITICAL_ERROR_TYPES else "warning"
            
            # Step 3: Atomic DB transaction
            with WriteGuardSession(Session(self._manager.engine), self._manager.write_guard) as session:
                # a) Get child instance
                instance = session.get(Instance, instance_id)
                if not instance:
                    return
                
                # Capture child agent_id before session closes
                child_agent_id = instance.agent_id
                
                # b) Set child instance status to ERROR
                instance.status = InstanceStatus.ERROR.value
                instance.updated_at = datetime.now(timezone.utc).isoformat()
                
                # Capture instance_id before session closes
                error_instance_id = instance.instance_id
                
                # c) Fail associated message if provided
                if message_id:
                    message = session.get(MessageQueue, message_id)
                    if message:
                        message.status = MessageStatus.FAILED.value
                        message.completed_at = datetime.now(timezone.utc)
                
                # d) Decrement parent's waiting_for counter atomically.
                # Fix C: symmetric to the decrement in child_reports.py. A
                # non-atomic read-modify-write races with concurrent
                # child-completion decrements. SQL UPDATE is atomic in both
                # SQLite and Postgres. Use CASE (not MAX, not GREATEST) for
                # the clamp-at-zero: PostgreSQL's MAX is aggregate-only and
                # errors on multi-arg scalar ``MAX(0, ...)``; GREATEST is a
                # SQLite *extension* function the stdlib sqlite3 driver
                # doesn't load. CASE is portable SQL — same shape in both
                # dialects, no dialect branch needed. RETURNING gives us the
                # post-UPDATE value for accurate logging (see child_reports.py
                # for the rationale on why we don't log a from-value).
                parent = session.get(Instance, parent_id)
                if parent:
                    result = session.execute(
                        text(
                            "UPDATE instances "
                            "SET waiting_for = CASE "
                            "    WHEN COALESCE(waiting_for, 0) - 1 > 0 "
                            "        THEN COALESCE(waiting_for, 0) - 1 "
                            "    ELSE 0 "
                            "END "
                            "WHERE instance_id = :pid "
                            "RETURNING waiting_for"
                        ),
                        {"pid": parent_id},
                    )
                    new_waiting_row = result.first()
                    new_waiting = int(new_waiting_row[0]) if new_waiting_row is not None else 0
                    logger.info(
                        f"waiting_for decremented (error path) -> {new_waiting} "
                        f"(parent={parent_id[:8]}..., child={instance_id[:8]}...)"
                    )
                    session.expire(parent)
                    parent = session.get(Instance, parent_id)
                    parent.last_activity_at = datetime.now(timezone.utc)
                    parent.version = (parent.version or 1) + 1
                    
                    # e) Update parent's children[] cache
                    if parent.children:
                        try:
                            children_list = json.loads(parent.children) if isinstance(parent.children, str) else parent.children
                            if instance_id in children_list:
                                children_list = [c for c in children_list if c != instance_id]
                                parent.children = json.dumps(children_list)
                        except (json.JSONDecodeError, TypeError):
                            logger.warning(f"Failed to parse children JSON for parent {parent_id[:8]}...")
                    
                    # f) Delete from instance_hierarchy
                    session.execute(
                        text("DELETE FROM instance_hierarchy WHERE child_id = :child_id"),
                        {"child_id": instance_id}
                    )
                    
                    # g) Cascade: check if parent can complete after all children done/error
                    # FIX: Removed status restriction - cascade should run whenever waiting_for == 0,
                    # regardless of current status. Mirrors the fix in _update_parent_on_child_complete.
                    if parent.waiting_for == 0 and parent.status != InstanceStatus.COMPLETED.value:
                        # Check if parent has any pending messages
                        parent_pending = session.exec(
                            select(func.count())
                            .select_from(MessageQueue)
                            .where(MessageQueue.instance_id == parent.instance_id)
                            .where(MessageQueue.status.in_([
                                MessageStatus.READY.value,
                                MessageStatus.PROCESSING.value,
                                MessageStatus.RETRYING.value,
                            ]))
                        ).scalar_one()
                        
                        if parent_pending == 0:
                            # No pending messages, parent is truly complete
                            parent.status = InstanceStatus.COMPLETED.value
                            parent.updated_at = datetime.now(timezone.utc).isoformat()
                            logger.info(f"Parent {parent.instance_id[:8]}... completed after child error")
                            
                            # Capture parent_id and agent_id for event publishing (outside transaction)
                            completed_parent_id = parent.instance_id
                            completed_parent_agent_id = parent.agent_id
                            completed_parent_parent_id = parent.parent_id
                            
                            session.commit()
                            
                            # Emit status_change SSE event for parent completed
                            if self._manager._live_hub:
                                try:
                                    await self._manager._live_hub.stream_status_change(completed_parent_id, "completed", agent_id=completed_parent_agent_id)
                                except Exception as e:
                                    logger.warning(f"Failed to emit status_change for completed parent: {e}")
                            
                            # FIX: Publish lifecycle event so JobFeedbackObserver completes the job
                            if self._events_service:
                                await self._events_service._publish_instance_lifecycle_event(
                                    instance_id=completed_parent_id,
                                    status="completed",
                                    error=None,
                                    parent_id=completed_parent_parent_id,
                                )
                        else:
                            # Has pending messages - transition to WAITING_CHILDREN
                            # Parent should wait for its message processing to complete
                            parent.status = InstanceStatus.WAITING_CHILDREN.value
                            parent.updated_at = datetime.now(timezone.utc).isoformat()
                            session.commit()  # Commit the WAITING_CHILDREN status change
                            logger.info(
                                f"Parent {parent.instance_id[:8]}... all children done but has {parent_pending} "
                                f"pending messages, status=WAITING_CHILDREN after child error"
                            )
                            # Emit status_change SSE event for parent waiting_children
                            if self._manager._live_hub:
                                try:
                                    await self._manager._live_hub.stream_status_change(parent.instance_id, "waiting_children", agent_id=parent.agent_id)
                                except Exception as e:
                                    logger.warning(f"Failed to emit status_change for waiting_children parent: {e}")
            
            # Signal CompletionRegistry for invoke_agent_and_wait() callers
            # After session commit — instance is in ERROR state in DB
            from .completion_registry import get_completion_registry
            get_completion_registry().complete(
                instance_id,
                result=f"Agent error: {truncated_error}",
                is_error=True,
            )
            
            # Emit status_change SSE event for child error
            if self._manager._live_hub:
                try:
                    await self._manager._live_hub.stream_status_change(error_instance_id, "error", agent_id=child_agent_id)
                except Exception as e:
                    logger.warning(f"Failed to emit status_change for error instance: {e}")
            
            # Step 4: Enqueue error report message to parent (outside transaction)
            error_report = f"⚠️ {agent_name} encountered an error:\n\n**Error Type:** {error_type}\n**Severity:** {severity}\n**Details:** {truncated_error}"
            
            msg = await asyncio.to_thread(
                self._queue_repository.enqueue,
                instance_id=parent_id,
                content=error_report,
                source=f"internal_error_report:{instance_id}",
                priority=1,  # Normal priority
                message_metadata={
                    "type": "error_report", 
                    "child_instance_id": instance_id,
                    "error_type": error_type,
                    "error": truncated_error,
                    "original_message_id": message_id,
                    "severity": severity,
                    "recoverable": error_type in RECOVERABLE_ERROR_TYPES,
                }
            )
            report_message_id = msg.message_id
            
            # Step 5: Broadcast child_failed SSE event with null guard
            if self._manager._live_hub:
                try:
                    await self._manager._live_hub.stream_lifecycle(
                        instance_id=parent_id,
                        event_type="child_failed",
                        data={
                            "type": "error_report",
                            "child_instance_id": instance_id,
                            "agent_name": agent_name,
                            "error_type": error_type,
                            "error": truncated_error,
                            "original_message_id": message_id,
                            "severity": severity,
                            "report_message_id": report_message_id,
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to broadcast child_failed event: {e}")
            
            logger.info(f"Sent error report from {agent_name} ({instance_id[:8]}...) to parent ({parent_id[:8]}...)")
            
        except Exception as e:
            logger.error(
                f"Failed to send error report for instance {instance_id[:8]}...: {e}. "
                f"Original error was: {error_type}: {error[:200]}"
            )
