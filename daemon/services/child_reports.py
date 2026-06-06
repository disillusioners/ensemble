"""Child reports service for handling child instance completion reports."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import func, select, text
from sqlmodel import Session

from ..persistence import get_instance_messages
from ..repositories.instance.models import Instance, InstanceStatus
from ..repositories.message_queue.models import MessageQueue, MessageStatus, MessageType
from ..repositories.task.models import Task, TaskType, TaskStatus
from ..repositories.event.models import Event, EventKind
from ..registry import get_registry
from ..write_pause_guard import WriteGuardSession
from .main_loop_bridge import MainLoopBridge

if TYPE_CHECKING:
    from ..config import Config
    from ..repositories.instance.repository import SQLModelInstanceRepository
    from .event_publisher import EventPublisherService
    from .error_reporting import ErrorReportingService


logger = logging.getLogger(__name__)


class ChildReportsService:
    """Service for handling child instance completion reports.
    
    Handles:
    - Idempotency per-message (won't send duplicate reports for same message)
    - Parent's waiting_for counter decrement
    - Parent's children[] cache update (FIX: W6)
    - Cascade: if parent's waiting_for reaches 0, transition parent to RUNNING
    """

    def __init__(
        self,
        manager: "InstanceManager",
        events_service: "EventPublisherService | None" = None,
    ):
        """Initialize the child reports service.
        
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
    def _checkpointer(self) -> "Any | None":
        """Access the underlying LangGraph checkpointer (saver) through manager.

        Phase 2 migration: the manager now stores a ``CheckpointerAdapter``;
        services that need the raw saver (passed to ``get_instance_messages``)
        reach it via ``raw_saver``. ``maintenance.py`` uses the adapter
        interface directly.

        Returns ``None`` if the checkpointer has not been initialized yet.
        """
        adapter = self._manager._checkpointer
        return adapter.raw_saver if adapter is not None else None

    def _trigger_title_generation(self, instance_id: str, completed_message_id: str) -> None:
        """Trigger title generation for an instance after message completion.
        
        This is fire-and-forget - runs asynchronously without blocking the caller.
        Title generation checks if title already exists before generating.
        
        Args:
            instance_id: The instance ID that completed.
            completed_message_id: The message ID that completed (to get user content).
        """
        # Get the original user message content for title generation
        message = self._manager._queue_repository.get(completed_message_id)
        if message is None:
            logger.warning(
                f"Cannot trigger title generation for {instance_id[:8]}...: "
                f"message {completed_message_id[:8]}... not found"
            )
            return
        
        message_content = message.content or ""
        
        # Use MainLoopBridge for fire-and-forget async execution
        MainLoopBridge.run_async_no_wait(
            self._manager._generate_and_broadcast_title(instance_id, message_content)
        )
        logger.debug(f"Title generation triggered for instance {instance_id[:8]}...")

    def _get_instance_report_prefix(self, instance_id: str, agent_id: str) -> str:
        """Get formatted prefix for instance completion reports.
        
        Args:
            instance_id: The instance ID.
            agent_id: The agent ID.
        
        Returns:
            Formatted prefix like "Coder agent (id=xxx) has done" or
            "Coder agent (name=create-feature-a, id=xxx) has done"
        """
        # Get agent display name from meta.json
        agent_name = agent_id.capitalize()
        
        try:
            registry = get_registry()
            metadata = registry.get(agent_id)
            if metadata and metadata.name:
                agent_name = metadata.name
        except Exception:
            pass
        
        # Get instance_name from metadata
        instance_meta = self._instance_repository.get(instance_id)
        instance_name = None
        if instance_meta and instance_meta.instance_metadata:
            instance_name = instance_meta.instance_metadata.get("instance_name")
        
        # Format based on whether instance_name is set
        if instance_name:
            return f"{agent_name} agent (name={instance_name}, id={instance_id}) has done"
        else:
            return f"{agent_name} agent (id={instance_id}) has done"

    async def _summarize_instance(self, instance_id: str, agent_id: str) -> str:
        """Summarize instance messages using LLM.
        
        Args:
            instance_id: The instance ID to summarize.
            agent_id: The agent ID (e.g., "coder", "leader").
            
        Returns:
            Formatted summary string with instance info.
        """
        from langchain_core.messages import HumanMessage, SystemMessage
        
        # Get the report prefix
        prefix = self._get_instance_report_prefix(instance_id, agent_id)
        
        # Get instance messages
        if self._checkpointer:
            messages = await get_instance_messages(self._checkpointer, instance_id)
        else:
            messages = []
        
        if not messages:
            return f"{prefix}, below is the response: No activity recorded."
        
        # Build conversation summary for the LLM
        conversation_text = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if content:
                # Truncate very long messages
                if len(content) > 500:
                    content = content[:500] + "..."
                conversation_text.append(f"{role}: {content}")
        
        if not conversation_text:
            return f"{prefix}, below is the response: No messages to summarize."
        
        conversation = "\n".join(conversation_text)
        
        # Create LLM client for summarization using the same config pattern
        # Filter model_vision from config to avoid noisy LangChain warnings
        llm_config = {
            "base_url": self._config.llm.base_url,
            "api_key": self._config.llm.api_key,
            "model": self._config.llm.model,
            "temperature": 0.3,  # Lower temperature for more focused summaries
            "default_headers": {"x-proxy-app": "ensemble"},
        }
        # Remove model_vision if present (summarization doesn't need vision)
        llm_config = {k: v for k, v in llm_config.items() if k != "model_vision"}
        
        # Import here to use the same pattern as graph.py
        from ..graph import ThinkingChatOpenAI
        llm = ThinkingChatOpenAI(**llm_config)
        
        summarization_prompt = f"""Summarize what this agent accomplished in 2-3 sentences. Focus on the outcomes and key actions taken, not the process.

Agent conversation:
{conversation}

Provide a concise summary:"""

        try:
            response = await asyncio.to_thread(
                llm.invoke,
                [SystemMessage(content="You are a helpful assistant that summarizes agent conversations concisely."),
                 HumanMessage(content=summarization_prompt)]
            )
            # Handle both string and list content types
            content = response.content
            if isinstance(content, list):
                # Extract text from list of content blocks
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        text_parts.append(block.get("text", ""))
                    else:
                        text_parts.append(str(block))
                summary = " ".join(text_parts)
            else:
                summary = str(content) if content else ""
            return f"{prefix}, below is the response: {summary}"
        except Exception as e:
            logger.warning(f"Failed to summarize instance {instance_id}: {e}")
            # Fallback: count messages and provide basic summary
            return f"{prefix}, below is the response: Completed {len(messages)} message(s)."

    async def _should_send_completion_report(self, session, instance_id: str, completed_message_id: str | None) -> tuple[bool, str | None]:
        """Check if completion report should be sent (idempotency checks).
        
        Performs two checks to ensure we do not send duplicate completion reports:
        1. No pending messages (PROCESSING, RETRYING) for the instance
        2. No existing completion report for this specific message
        
        The idempotency key includes the message_id so each message completion
        generates a unique report (allowing multiple completions from the same child).
        
        Args:
            session: Database session.
            instance_id: The child instance ID to check.
            completed_message_id: The message ID that just completed (can be None).
            
        Returns:
            Tuple of (should_send, stale_report_reason): 
            - should_send: True if should proceed with sending report, False to skip.
            - stale_report_reason: None if should_send=True, or reason string if skipped.
        """
        logger.debug(
            f"_should_send_completion_report called: instance_id={instance_id[:8] if instance_id else None}, "
            f"completed_message_id={completed_message_id[:8] if completed_message_id else None}"
        )
        
        # Guard: Can't do idempotency check without message_id
        if completed_message_id is None:
            # Just count all pending messages for the instance
            pending_count = session.exec(
                select(func.count())
                .select_from(MessageQueue)
                .where(MessageQueue.instance_id == instance_id)
                .where(MessageQueue.status.in_([
                    MessageStatus.PROCESSING.value,
                    MessageStatus.RETRYING.value,
                ]))
            ).scalar_one()
            return pending_count > 0, "no_completed_message_id"
        
        # Check for pending/processing messages for this instance
        # Exclude only the completed message by ID (not by status) so that
        # newly sent messages with PROCESSING status are properly counted
        pending_count = session.exec(
            select(func.count())
            .select_from(MessageQueue)
            .where(MessageQueue.instance_id == instance_id)
            .where(MessageQueue.message_id != completed_message_id)
            .where(MessageQueue.status.in_([
                MessageStatus.PROCESSING.value,  # Include - excluded by ID instead
                MessageStatus.RETRYING.value,
            ]))
        ).scalar_one()
        
        if pending_count > 0:
            logger.info(
                f"Instance {instance_id[:8]}... has {pending_count} pending messages "
                f"(PROCESSING/RETRYING), skipping completion report"
            )
            return False, "pending_messages_exist"
        
        # Idempotency: Check if completion report already sent for THIS message
        instance = session.get(Instance, instance_id)
        if instance is None:
            logger.info(f"Instance {instance_id[:8]}... not found, skipping completion report")
            return False, "instance_not_found"
        
        if instance.parent_id is None:
            logger.info(f"Instance {instance_id[:8]}... has no parent_id, skipping completion report")
            return False, "no_parent_id"
            
        # Use message_id in source so each completion generates a unique report
        existing_report = session.exec(
            select(MessageQueue)
            .where(MessageQueue.instance_id == instance.parent_id)
            .where(MessageQueue.source == f"internal_report:{instance_id}:{completed_message_id}")
            .where(MessageQueue.status.in_([
                MessageStatus.READY.value,
                MessageStatus.PROCESSING.value,
                MessageStatus.COMPLETED.value,
            ]))
        ).first()
        
        if existing_report is not None:
            logger.debug(
                f"Completion report already queued for child {instance_id[:8]}... "
                f"message {completed_message_id[:8]}..., skipping duplicate"
            )
            return False, "idempotency_skip"
        
        logger.info(
            f"Idempotency check PASSED: child {instance_id[:8]}..., "
            f"message {completed_message_id[:8]}..., no existing report found"
        )
        return True, "all_checks_passed"

    async def _create_completion_report(
        self,
        session,
        instance,
        last_content: str,
        completed_message_id: str,
    ) -> tuple[MessageQueue, Task, str]:
        """Create the completion report message and task for the parent.
        
        Updates the child instance status to COMPLETED and creates:
        - COMPLETION_REPORT message for parent
        - PROCESS_MESSAGE task
        
        Args:
            session: Database session.
            instance: The child Instance object.
            last_content: The content to include in the report (fetched before transaction).
            completed_message_id: The message ID that completed (for unique report source).
            
        Returns:
            Tuple of (report_message, report_task, report_message_id).
        """
        # Update child instance status to COMPLETED
        instance.status = InstanceStatus.COMPLETED.value
        instance.updated_at = datetime.now(timezone.utc).isoformat()
        instance.last_activity_at = datetime.now(timezone.utc)
        instance.version = (instance.version or 1) + 1
        
        # Create completion report message for parent
        # Include message_id in source for per-message idempotency
        report_message_id = str(uuid.uuid4())
        report_message = MessageQueue(
            message_id=report_message_id,
            instance_id=instance.parent_id,
            content=last_content,  # Already fetched before transaction
            source=f"internal_report:{instance.instance_id}:{completed_message_id}",
            type=MessageType.COMPLETION_REPORT.value,
            status=MessageStatus.READY.value,
            priority=0,  # System priority
            enqueued_at=datetime.now(timezone.utc),
        )
        session.add(report_message)
        
        # Create task for parent to process the report
        report_task = Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=instance.parent_id,
            message_id=report_message_id,
            status=TaskStatus.PENDING.value,
            created_at=datetime.now(timezone.utc),
        )
        session.add(report_task)
        
        return report_message, report_task, report_message_id

    async def _update_parent_on_child_complete(self, session, instance) -> tuple[bool, str | None, str | None]:
        """Update parent state when child completes.
        
        Handles:
        - Decrement parent's waiting_for counter
        - Update parent's children cache (FIX: W6)
        - Delete from instance_hierarchy table
        - Cascade: transition parent based on waiting_for and status
        
        Args:
            session: Database session.
            instance: The child Instance object.
            
        Returns:
            Tuple of (transitioned_to_running, completed_parent_id, completed_parent_parent_id):
            - transitioned_to_running: True if parent transitioned to RUNNING (has more work)
            - completed_parent_id: Instance ID if parent completed (for event publishing), None otherwise
            - completed_parent_parent_id: Parent's parent_id if parent completed, None otherwise
        """
        parent = session.get(Instance, instance.parent_id)
        if not parent:
            return False, None, None

        # Decrement parent's waiting_for counter atomically.
        # Fix C: a non-atomic read-modify-write here races with concurrent
        # child completions (two decrements can both read the same starting
        # value, both write N-1, leaving the counter stuck at N-1 instead of
        # N-2). The SQL UPDATE is atomic in both SQLite and Postgres; COALESCE
        # guards against NULL and the CASE clamps at 0.
        #
        # Dialect note: SQLite's scalar MAX(a, b) is multi-arg, but PostgreSQL
        # only exposes MAX as an aggregate, so it errors with
        # ``function max(integer, integer) does not exist``. GREATEST looks
        # like the obvious fix but is a SQLite *extension* function, not a
        # core builtin, so the stdlib ``sqlite3`` driver raises
        # ``no such function: GREATEST``. CASE is the portable form — same
        # shape in both dialects, no dialect branch needed.
        #
        # RETURNING gives us the post-UPDATE value as observed by THIS
        # statement, so the log line is honest about what THIS decrement
        # actually saw. We do NOT log a from-value: under concurrent
        # decrements, the pre-value would be a stale session-cache
        # read, and the inferred "from" via ``new + 1`` is wrong when
        # the clamp kept it at 0 (0-1 stays at 0, so +1 misleads). Log
        # just the new value; chains of decrements reconstruct the
        # sequence from successive log lines.
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
            {"pid": parent.instance_id},
        )
        new_waiting_row = result.first()
        new_waiting = int(new_waiting_row[0]) if new_waiting_row is not None else 0

        # Force the session to re-read for the cascade check below.
        # SQLAlchemy would otherwise return the stale cached value on
        # subsequent attribute access.
        session.expire(parent)
        parent = session.get(Instance, instance.parent_id)
        logger.info(
            f"waiting_for decremented -> {new_waiting} "
            f"(parent={parent.instance_id[:8] if parent else '?'}..., "
            f"child={instance.instance_id[:8]}...)"
        )
        parent.last_activity_at = datetime.now(timezone.utc)
        parent.version = (parent.version or 1) + 1
        
        # FIX W6: Update parent's children[] denormalized cache
        # Note: instance_hierarchy is the canonical source; we update the cache here
        if parent.children:
            try:
                children_list = json.loads(parent.children) if isinstance(parent.children, str) else parent.children
                if instance.instance_id in children_list:
                    children_list = [c for c in children_list if c != instance.instance_id]
                    parent.children = json.dumps(children_list)
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Failed to parse children JSON for parent {instance.parent_id[:8]}...")
        
        # Remove from instance_hierarchy junction table
        # NOTE: Do NOT delete the instance from instances table - terminate means stop tasks, not delete
        session.execute(
            text("DELETE FROM instance_hierarchy WHERE child_id = :child_id"),
            {"child_id": instance.instance_id}
        )
        
        # Cascade check: if waiting_for is 0, check if parent can complete
        # FIX: Removed status restriction - cascade should run whenever waiting_for == 0,
        # regardless of current status (e.g., RUNNING from previous cascade). This ensures
        # parent waits for ALL children before completing, not just the first batch.
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
                # Publish lifecycle event to mark job as completed
                parent.status = InstanceStatus.COMPLETED.value
                parent.updated_at = datetime.now(timezone.utc).isoformat()
                logger.info(f"Parent {parent.instance_id[:8]}... completed after all children done")
                
                # Capture parent_id for event publishing (instance will be detached after session closes)
                completed_parent_id = parent.instance_id
                completed_parent_parent_id = parent.parent_id
                
                return False, completed_parent_id, completed_parent_parent_id
            else:
                # Has pending messages but all children done - transition to WAITING_CHILDREN
                # FIX: Changed from RUNNING to WAITING_CHILDREN. Parent should wait for its own
                # message processing to complete before marking job done. When parent completes
                # its message, the status check will keep it in WAITING_CHILDREN, and the cascade
                # will run again to mark it COMPLETED.
                parent.status = InstanceStatus.WAITING_CHILDREN.value
                logger.info(
                    f"Parent {parent.instance_id[:8]}... all children done but has {parent_pending} "
                    f"pending messages, status=WAITING_CHILDREN"
                )
                # Emit status_change SSE event for parent waiting_children
                if self._manager._live_hub:
                    try:
                        await self._manager._live_hub.stream_status_change(parent.instance_id, "waiting_children", agent_id=parent.agent_id)
                    except Exception as e:
                        logger.warning(f"Failed to emit status_change for waiting_children parent: {e}")
                return True, None, None
        
        return False, None, None
        
    async def _create_completion_events(
        self,
        session,
        instance_id: str,
        parent_id: str,
        report_message_id: str,
        waiting_for_remaining: int,
    ) -> tuple[Event, Event]:
        """Create completion events for child and parent.
        
        Creates:
        - INSTANCE_COMPLETED event for the child
        - CHILD_COMPLETED event for the parent
        
        Args:
            session: Database session.
            instance_id: The child instance ID.
            parent_id: The parent instance ID.
            report_message_id: The report message ID for the parent event.
            waiting_for_remaining: The remaining waiting_for count after decrement.
            
        Returns:
            Tuple of (completion_event, parent_event).
        """
        # Create completion event for child
        completion_event = Event(
            instance_id=instance_id,
            kind=EventKind.INSTANCE_COMPLETED.value,
            data=json.dumps({
                "parent_id": parent_id,
                "report_message_id": report_message_id,
            }),
            created_at=datetime.now(timezone.utc),
        )
        session.add(completion_event)
        
        # Also create event for parent about child completion
        parent_event = Event(
            instance_id=parent_id,
            message_id=report_message_id,
            kind=EventKind.CHILD_COMPLETED.value,
            data=json.dumps({
                "child_instance_id": instance_id,
                "waiting_for_remaining": waiting_for_remaining,
            }),
            created_at=datetime.now(timezone.utc),
        )
        session.add(parent_event)
        
        return completion_event, parent_event

    async def _get_last_assistant_message(self, instance_id: str, agent_id: str) -> str | None:
        """Get the last assistant message from instance history.
        
        This is the default/simple approach for completion reports - just
        pass the agent's last response to the parent.
        
        Args:
            instance_id: The instance ID to get message from.
            agent_id: The agent ID (e.g., "coder", "leader").
            
        Returns:
            Formatted string with instance info and last message.
        """
        # Get the report prefix
        prefix = self._get_instance_report_prefix(instance_id, agent_id)
        
        raw_content = await self._get_last_assistant_message_raw(instance_id)
        
        if raw_content:
            return f"{prefix}, below is the response:\n{raw_content}"
        return None

    async def _get_last_assistant_message_raw(self, instance_id: str) -> str | None:
        """Get the raw last assistant message content (no formatting).
        
        Returns just the actual agent response content, matching the format
        used by MessageJobHandler when setting result_summary=result.content.
        
        Args:
            instance_id: The instance ID to get message from.
            
        Returns:
            The raw assistant message content, or None if not found.
        """
        if self._checkpointer:
            messages = await get_instance_messages(self._checkpointer, instance_id)
        else:
            messages = []
        
        # Find the last assistant message with actual content
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if content and content.strip():
                    return content.strip()
        
        return None

    async def _process_child_completion_and_notify_parent(self, instance_id: str, completed_message_id: str) -> None:
        """Check if child instance is done and send completion report to parent.
        
        CRITICAL FIX C3: Content is fetched BEFORE the transaction to avoid
        leaving the instance in COMPLETED state without a report if the fetch fails.
        
        Args:
            instance_id: The child instance that completed.
            completed_message_id: The message ID that just completed (for idempotency).
        """
        logger.info(f"_process_child_completion_and_notify_parent called: instance={instance_id[:8]}..., message_id={completed_message_id[:8] if completed_message_id else None}")
        
        # FIX C3: Fetch content BEFORE transaction — avoid orphaned COMPLETED state
        # Get instance's agent_id for the report
        instance_meta = self._instance_repository.get(instance_id)
        agent_id = instance_meta.agent_id if instance_meta else "agent"
        last_content = await self._get_last_assistant_message(instance_id, agent_id)
        if last_content is None:
            logger.warning(f"No assistant content found for instance {instance_id[:8]}..., using empty content for completion check")
            last_content = "[No response content]"  # Proceed with empty content — state transition must still happen
        
        with WriteGuardSession(Session(self._manager.engine), self._manager.write_guard) as session:
            # Get instance metadata
            instance = session.get(Instance, instance_id)
            if instance is None:
                logger.info(f"Instance {instance_id[:8]}... not found in DB, skipping")
                return
            
            logger.info(f"Instance {instance_id[:8]}... parent_id={instance.parent_id}, waiting_for={instance.waiting_for}, status={instance.status}")
            
            # Not a child? Instance completed (no parent to send report to)
            # Check if we have active children - if so, wait for them before completing
            if instance.parent_id is None:
                if instance.waiting_for > 0:
                    # Has children still running - transition to WAITING_CHILDREN
                    # Job will complete when last child finishes
                    instance.status = InstanceStatus.WAITING_CHILDREN.value
                    session.commit()
                    logger.info(
                        f"Instance {instance_id[:8]}... completed message but waiting for "
                        f"{instance.waiting_for} children, status=WAITING_CHILDREN"
                    )
                    logger.info(f"Instance {instance_id[:8]}... has children (waiting_for>0), deferring completion")
                    # Emit status_change SSE event
                    if self._manager._live_hub:
                        try:
                            await self._manager._live_hub.stream_status_change(instance_id, "waiting_children", agent_id=instance.agent_id)
                        except Exception as e:
                            logger.warning(f"Failed to emit status_change for waiting_children: {e}")
                    return
                
                # waiting_for == 0, but check for pending messages before completing.
                # This handles the case where child completion reports are still queued
                # but waiting_for was already decremented by a previous cascade.
                pending_count = session.exec(
                    select(func.count())
                    .select_from(MessageQueue)
                    .where(MessageQueue.instance_id == instance_id)
                    .where(MessageQueue.status.in_([
                        MessageStatus.READY.value,
                        MessageStatus.PROCESSING.value,
                        MessageStatus.RETRYING.value,
                    ]))
                ).scalar_one()
                
                if instance.waiting_for > 0 and pending_count > 0:
                    # Has explicit children to wait for
                    instance.status = InstanceStatus.WAITING_CHILDREN.value
                    session.commit()
                    logger.info(
                        f"Instance {instance_id[:8]}... waiting_for={instance.waiting_for}, pending={pending_count}, "
                        f"status=WAITING_CHILDREN"
                    )
                    logger.info(f"Instance {instance_id[:8]}... has pending messages, deferring notification")
                    # Emit status_change SSE event
                    if self._manager._live_hub:
                        try:
                            await self._manager._live_hub.stream_status_change(instance_id, "waiting_children", agent_id=instance.agent_id)
                        except Exception as e:
                            logger.warning(f"Failed to emit status_change for waiting_children: {e}")
                    return
                elif pending_count > 0 and instance.waiting_for == 0:
                    logger.warning(
                        "Instance %s has pending_count=%d but waiting_for=0 — "
                        "proceeding to COMPLETED (not waiting_children)",
                        instance_id[:8], pending_count
                    )
                
                # No children, no pending messages - safe to complete
                logger.info(f"Instance {instance_id[:8]}... no parent, skipping notification")
                
                # No children, no pending messages - safe to complete
                logger.info(f"Instance {instance_id[:8]}... completed (no parent, no children), status=COMPLETED")

                # Update instance status to COMPLETED in DB
                instance.status = InstanceStatus.COMPLETED.value
                instance.updated_at = datetime.now(timezone.utc).isoformat()
                instance.last_activity_at = datetime.now(timezone.utc)
                instance.version = (instance.version or 1) + 1

                session.commit()

                # Emit status_change SSE event for root instance completed
                if self._manager._live_hub:
                    try:
                        await self._manager._live_hub.stream_status_change(instance_id, "completed", agent_id=instance.agent_id)
                    except Exception as e:
                        logger.warning(f"Failed to emit status_change for completed root instance: {e}")

                # Signal CompletionRegistry for invoke_agent_and_wait() callers
                from .completion_registry import get_completion_registry
                get_completion_registry().complete(instance_id, result=last_content)

                if self._events_service:
                    await self._events_service._publish_instance_lifecycle_event(
                        instance_id=instance_id,
                        status="completed",
                        error=None,
                        parent_id=None,
                    )
                
                # Trigger title generation (fire-and-forget)
                self._trigger_title_generation(instance_id, completed_message_id)
                return
            
            # Idempotency checks
            should_send, skip_reason = await self._should_send_completion_report(session, instance_id, completed_message_id)
            if not should_send:
                logger.info(f"Instance {instance_id[:8]}... completion report skipped: reason={skip_reason}")
                return

            # Check if this is a tool invocation (explore/experience)
            # If so, skip parent notification but still update status and signal CompletionRegistry
            if instance.instance_metadata and instance.instance_metadata.get("invoked_as_tool", False):
                logger.info(
                    f"Instance {instance_id[:8]}... completed (tool invocation, skipping parent report)"
                )
                logger.info(f"Instance {instance_id[:8]}... is tool invocation, skipping parent notification")

                # Update child status to COMPLETED
                instance.status = InstanceStatus.COMPLETED.value
                instance.updated_at = datetime.now(timezone.utc).isoformat()
                instance.last_activity_at = datetime.now(timezone.utc)
                instance.version = (instance.version or 1) + 1

                # Capture parent_id before session closes
                parent_id = instance.parent_id

                session.commit()
                
                # Emit status_change SSE event for tool invocation completed
                if self._manager._live_hub:
                    try:
                        await self._manager._live_hub.stream_status_change(instance_id, "completed", agent_id=instance.agent_id)
                    except Exception as e:
                        logger.warning(f"Failed to emit status_change for completed tool invocation: {e}")

                # Signal CompletionRegistry for explore() callers
                from .completion_registry import get_completion_registry
                get_completion_registry().complete(instance_id, result=last_content)

                # Optionally publish lifecycle event
                if self._events_service:
                    try:
                        await self._events_service._publish_instance_lifecycle_event(
                            instance_id=instance_id,
                            status="completed",
                            error=None,
                            parent_id=parent_id,
                        )
                    except Exception as e:
                        logger.warning(f"Failed to publish lifecycle event: {e}")

                # Trigger title generation (fire-and-forget)
                self._trigger_title_generation(instance_id, completed_message_id)

                return

            # ATOMIC: Instance completed — create completion report for parent
            logger.info(f"Instance {instance_id[:8]}... completed, sending report to parent {instance.parent_id[:8]}...")
            
            # Create completion report
            report_message, report_task, report_message_id = await self._create_completion_report(
                session, instance, last_content, completed_message_id
            )
            
            # Update parent state
            parent_transitioned_to_running, completed_parent_id, completed_parent_parent_id = await self._update_parent_on_child_complete(session, instance)
            
            # Calculate waiting_for remaining for event
            waiting_for_remaining = max(0, (instance.parent_id and session.get(Instance, instance.parent_id).waiting_for) or 0)
            
            # Create events
            await self._create_completion_events(
                session,
                instance_id,
                instance.parent_id,
                report_message_id,
                waiting_for_remaining,
            )
            
            # Capture parent_id and agent_id before session closes (instance will be detached)
            parent_id = instance.parent_id
            child_agent_id = instance.agent_id
            
            # Capture parent's agent_id for status_change event
            parent_agent_id = None
            if completed_parent_id:
                parent = session.get(Instance, completed_parent_id)
                if parent:
                    parent_agent_id = parent.agent_id
            
            session.commit()

        # Signal CompletionRegistry for invoke_agent_and_wait() callers
        # After commit (DB consistent), before SSE broadcast (non-critical)
        from .completion_registry import get_completion_registry
        get_completion_registry().complete(instance_id, result=last_content)
        
        # Emit status_change SSE event for child completed
        if self._manager._live_hub:
            try:
                await self._manager._live_hub.stream_status_change(instance_id, "completed", agent_id=child_agent_id)
            except Exception as e:
                logger.warning(f"Failed to emit status_change for completed instance: {e}")
        
        # Broadcast child completion event asynchronously (using captured parent_id)
        try:
            await self._manager._live_hub.stream_lifecycle(
                instance_id=parent_id,
                event_type="child_completed",
                data={
                    "child_instance_id": instance_id,
                    "report_message_id": report_message_id,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to broadcast child completion event: {e}")
        
        # If parent completed (all children done), publish lifecycle event to mark job as completed
        if completed_parent_id:
            try:
                # Emit status_change SSE event for parent completed
                if self._manager._live_hub:
                    await self._manager._live_hub.stream_status_change(completed_parent_id, "completed", agent_id=parent_agent_id)
            except Exception as e:
                logger.warning(f"Failed to emit status_change for completed parent: {e}")
            
            try:
                if self._events_service:
                    await self._events_service._publish_instance_lifecycle_event(
                        instance_id=completed_parent_id,
                        status="completed",
                        error=None,
                        parent_id=completed_parent_parent_id,
                    )
            except Exception as e:
                logger.warning(f"Failed to publish lifecycle event for completed parent {completed_parent_id[:8]}...: {e}")

        # Trigger title generation for child instance (fire-and-forget)
        self._trigger_title_generation(instance_id, completed_message_id)
