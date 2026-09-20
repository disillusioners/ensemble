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
from .completion_content import (
    event_created_at_as_utc,
    get_last_assistant_message,
    get_last_assistant_timestamp,
    parse_checkpoint_ts,
)
from .main_loop_bridge import MainLoopBridge

if TYPE_CHECKING:
    from ..config import Config
    from ..persistence import CheckpointSaver
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
    def _checkpointer(self) -> "CheckpointSaver | None":
        """Access checkpointer through manager for test mockability."""
        return self._manager._checkpointer

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

    async def _should_send_completion_report(self, session, instance_id: str, completed_message_id: str | None) -> tuple[bool, None]:
        """Check if completion report should be sent (idempotency checks).
        
        Performs two checks to ensure we do not send duplicate completion reports:
        1. No pending messages (READY, RETRYING) for the instance
        2. No existing completion report for this specific message
        
        The idempotency key includes the message_id so each message completion
        generates a unique report (allowing multiple completions from the same child).
        
        Args:
            session: Database session.
            instance_id: The child instance ID to check.
            completed_message_id: The message ID that just completed (can be None).
            
        Returns:
            Tuple of (should_send, None): True if should proceed with sending report, False to skip.
        """
        # Guard: Can't do idempotency check without message_id
        if completed_message_id is None:
            # Just count all pending messages for the instance
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
            return pending_count > 0, None
        
        # Check for pending/processing messages for this instance
        # Exclude only the completed message by ID (not by status) so that
        # newly sent messages with PROCESSING status are properly counted
        pending_count = session.exec(
            select(func.count())
            .select_from(MessageQueue)
            .where(MessageQueue.instance_id == instance_id)
            .where(MessageQueue.message_id != completed_message_id)
            .where(MessageQueue.status.in_([
                MessageStatus.READY.value,
                MessageStatus.PROCESSING.value,  # Include - excluded by ID instead
                MessageStatus.RETRYING.value,
            ]))
        ).scalar_one()
        
        if pending_count > 0:
            logger.debug(
                f"Instance {instance_id[:8]}... has {pending_count} pending messages, "
                f"skipping completion check"
            )
            return False, None
        
        # Idempotency: Check if completion report already sent for THIS message
        instance = session.get(Instance, instance_id)
        if instance is None or instance.parent_id is None:
            return False, None
            
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
            return False, None
        
        return True, None

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
        
        # Decrement parent's waiting_for counter
        old_waiting = parent.waiting_for or 0
        parent.waiting_for = max(0, old_waiting - 1)
        logger.info(
            f"waiting_for decremented: {old_waiting} -> {parent.waiting_for} "
            f"(parent={parent.instance_id[:8]}..., child={instance.instance_id[:8]}...)"
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
            # DEFECT-2 FIX (Round 1): full completion gate — waiting_for==0 AND pending_count==0
            # AND fresh assistant message after the last child_completed. Suppressed
            # parents are held in WAITING_CHILDREN; the predicate is re-evaluated on
            # the existing message-completed signal (event-driven, no polling).
            #
            # COUNCIL FINDING 1 (Round 2 — cascade-gate reality):
            # In the cascade lane, ``_create_completion_report`` was called upstream
            # and ``session.add(report_message)`` already staged a READY report row
            # for the parent. SQLAlchemy autoflush makes that staged row visible to
            # the pending_count query, so this gate ALWAYS returns False in normal
            # cascade traffic — ``pending_count>=1``. This is INTENTIONALLY
            # conservative: the cascade lane's role is to defer and wait, NOT to
            # be load-bearing for the terminal decision. The load-bearing gates are
            # the emission-time re-check at :~950 (this module) and the mirror at
            # :~765-771 (root lane, added Round 2). The message-completed signal
            # is the event-driven path that re-evaluates the gate and ultimately
            # transitions the instance to COMPLETED via the root lane.
            #
            # Test coverage: TestCascadeCompletionGate in
            # tests/job_queue/test_job_result_summary_and_gate.py exercises the
            # real production sequence without mocking this gate.
            allowed, block_reason = await self._root_completion_gate(
                session, parent.instance_id, waiting_for=parent.waiting_for
            )

            if not allowed:
                # Parent has queued work and/or has not yet responded after the last
                # child report - transition to WAITING_CHILDREN.
                # FIX: Changed from RUNNING to WAITING_CHILDREN. Parent should wait for its own
                # message processing to complete before marking job done. When parent completes
                # its message, the status check will keep it in WAITING_CHILDREN, and the cascade
                # will run again to mark it COMPLETED.
                parent.status = InstanceStatus.WAITING_CHILDREN.value
                logger.info(
                    f"Parent {parent.instance_id[:8]}... held in WAITING_CHILDREN after "
                    f"children done (gate blocked: {block_reason})"
                )
                # Emit status_change SSE event for parent waiting_children
                if self._manager._live_hub:
                    try:
                        await self._manager._live_hub.stream_status_change(parent.instance_id, "waiting_children", agent_id=parent.agent_id)
                    except Exception as e:
                        logger.warning(f"Failed to emit status_change for waiting_children parent: {e}")
                return True, None, None

            # Gate passed - parent is truly complete
            # Publish lifecycle event to mark job as completed
            parent.status = InstanceStatus.COMPLETED.value
            parent.updated_at = datetime.now(timezone.utc).isoformat()
            logger.info(f"Parent {parent.instance_id[:8]}... completed after all children done")
            
            # Capture parent_id for event publishing (instance will be detached after session closes)
            completed_parent_id = parent.instance_id
            completed_parent_parent_id = parent.parent_id
            
            return False, completed_parent_id, completed_parent_parent_id
        
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

        Extraction is delegated to the shared canonical helper in
        completion_content (also used by JobFeedbackObserver for job
        result_summary); this wrapper only adds the report prefix.

        Args:
            instance_id: The instance ID to get message from.
            agent_id: The agent ID (e.g., "coder", "leader").
            
        Returns:
            Formatted string with instance info and last message.
        """
        # Get the report prefix
        prefix = self._get_instance_report_prefix(instance_id, agent_id)

        last_assistant_content, _created_at = await get_last_assistant_message(
            self._checkpointer, instance_id
        )

        if last_assistant_content:
            return f"{prefix}, below is the response:\n{last_assistant_content}"
        return None

    # ── Root-completion predicate (DEFECT-2 fix: premature terminal emission) ──

    async def _assistant_message_fresh(self, session, instance_id: str) -> tuple[bool, str | None]:
        """Check the instance produced an assistant message after its last child_completed.

        The root may only complete a task job once it has responded AFTER every
        child completion report — otherwise its "final" message predates the
        last child's report and the subtree isn't truly done (job 5e197a30).

        Uses ``get_last_assistant_timestamp`` (NOT ``get_last_assistant_message``)
        so empty-content AI turns (e.g. pure tool-call responses) still count as
        a fresh response. This closes the empty-final-turn wedge where the parent
        processed the report but produced no visible content.

        Args:
            session: Open DB session (used for the child_completed lookup).
            instance_id: The instance (root/parent) to check.

        Returns:
            (is_fresh, reason): reason is a short diagnostic when not fresh.
            Fail-open (True) when the instance has no child_completed events
            (nothing to be fresh after — also the fast path for childless
            roots) or when checkpoint timestamps are unreadable: a missing
            timestamp must never wedge the job into eternal PROCESSING
            (see DEADLOCK GUARD in the fix design).
        """
        last_child_completed_at = session.exec(
            select(func.max(Event.created_at))
            .where(Event.instance_id == instance_id)
            .where(Event.kind == EventKind.CHILD_COMPLETED.value)
        ).scalar_one_or_none()

        if last_child_completed_at is None:
            # No child ever reported to this instance — freshness is vacuous.
            return True, None

        try:
            # EMPTY-FINAL-TURN WEDGE CLOSURE: any AI message (even empty content)
            # counts as fresh. A pure tool-call response still indicates the
            # instance responded AFTER the child_completed event.
            last_assistant_ts = await get_last_assistant_timestamp(
                self._checkpointer, instance_id
            )

            last_assistant_dt = parse_checkpoint_ts(last_assistant_ts)
        except Exception as e:
            # Fail-open: an unusable checkpointer must never wedge the job into
            # eternal PROCESSING (DEADLOCK GUARD). The waiting_for/pending legs
            # above remain authoritative; this leg is anti-premature-emission
            # belt-and-suspenders.
            logger.warning(
                "Freshness check failed for instance %s... (%s); treating as "
                "fresh (fail-open)",
                instance_id[:8], e,
            )
            return True, None

        if last_assistant_dt is None:
            # Unreadable/absent checkpoint timestamp — fail open (no deadlock).
            logger.warning(
                "Instance %s... has child_completed events but no readable "
                "assistant timestamp; treating freshness as satisfied",
                instance_id[:8],
            )
            return True, None

        last_child_dt = event_created_at_as_utc(last_child_completed_at)
        if last_assistant_dt >= last_child_dt:
            return True, None

        return False, (
            f"last assistant message ({last_assistant_ts}) predates last "
            f"child_completed ({last_child_dt.isoformat()})"
        )

    async def _gate_wedge_resolver(
        self,
        session,
        instance_id: str,
    ) -> tuple[bool, str | None]:
        """Wedge resolver: close the stale-readable / dead-letter wedge paths.

        Wedge paths occur when ``_assistant_message_fresh`` returns False (the
        parent has a stale assistant timestamp) BUT pending_count is already 0
        — the gate cannot wait for a response that will never come. In that
        situation, if the most recent message for this instance is in a
        TERMINAL state (COMPLETED or FAILED), the parent has done all it can
        and the job must terminate. We allow the gate to pass with a best-
        effort empty result; the result_summary will be None or whatever the
        checkpointer returns.

        Three wedge paths close via this resolver (all event-driven, no polling):

        (a) STALE-READABLE: parent's last assistant timestamp is readable but
            predates the most recent child_completed (no new AI response).
            If the report message that triggered the child_completed has been
            processed to a terminal state, the parent is wedged: allow.

        (b) EMPTY-FINAL-TURN: parent processed the report and produced only
            tool calls (no content). With the empty-content-aware
            ``get_last_assistant_timestamp`` change, the freshness check now
            returns True for this case — the wedge resolver is a belt-and-
            suspenders for the race where the AI message is unparseable.

        (c) DEAD-LETTER: the completion report message transitions to FAILED
            after max retries. The parent never had a chance to respond. If
            the FAILED message is the most recent for this instance, the
            parent is wedged: allow with empty result.

        Trigger: the existing message-completed signal (for paths a/b) and
        the new ``_on_stale_task_permanent_failure`` wedge hook in
        ``manager.py`` (for path c, fires on the FAILED transition).

        Args:
            session: Open DB session with the staged child_completed event.
            instance_id: The instance to evaluate.

        Returns:
            (allowed, reason): True (no reason) when the wedge is closed;
            False + reason when neither freshness nor terminal-state apply.
        """
        is_fresh, reason = await self._assistant_message_fresh(session, instance_id)
        if is_fresh:
            return True, None

        # Wedge detected: freshness failed but pending_count is already 0
        # (gate's pending_count leg already passed). Check the most recent
        # terminal-state message for this instance.
        last_terminal_msg_id = session.exec(
            select(MessageQueue.message_id)
            .where(MessageQueue.instance_id == instance_id)
            .where(MessageQueue.status.in_([
                MessageStatus.COMPLETED.value,
                MessageStatus.FAILED.value,
            ]))
            .order_by(MessageQueue.completed_at.desc())
            .limit(1)
        ).first()

        if last_terminal_msg_id is not None:
            logger.warning(
                "Wedge-resolver: instance %s... freshness failed but most "
                "recent message is terminal (message_id=%s); allowing "
                "completion with best-effort empty result",
                instance_id[:8],
                last_terminal_msg_id[:8],
            )
            return True, None

        return False, reason

    async def _root_completion_gate(
        self,
        session,
        instance_id: str,
        waiting_for: int | None = None,
    ) -> tuple[bool, str | None]:
        """Full emission predicate for marking a root/parent's job terminal.

        A terminal "completed" lifecycle event (which JobFeedbackObserver maps
        to job completion) may only be published when ALL of:
          1. waiting_for == 0 — no outstanding children
          2. pending_count == 0 — no queued/processing messages
          3. fresh assistant message after the last child_completed event —
             the instance has responded to every child report. Wedge-resolved
             by ``_gate_wedge_resolver`` when freshness fails but the most
             recent message is terminal (best-effort empty result).

        Suppressed instances are held in WAITING_CHILDREN; the predicate is
        re-evaluated on the existing message-completed signal (each completed
        message for the instance re-invokes _process_child_completion_and_
        notify_parent via task_processor / message_job_handler). This is an
        event-driven re-check on signal — NOT a poll loop.

        Args:
            session: Open DB session; when None an ephemeral session is opened.
            instance_id: The instance to evaluate.
            waiting_for: In-session waiting_for value; read from DB when None.

        Returns:
            (allowed, block_reason): block_reason is a diagnostic when blocked.
        """
        owns_session = session is None
        if owns_session:
            session = Session(self._manager._engine)

        try:
            if waiting_for is None:
                instance = session.get(Instance, instance_id)
                waiting_for = (instance.waiting_for or 0) if instance else 0

            if waiting_for > 0:
                return False, f"waiting_for={waiting_for}"

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

            if pending_count > 0:
                return False, f"pending_count={pending_count}"

            # DEADLOCK GUARD: route the freshness leg through the wedge resolver
            # so dead-letter / empty-final-turn / stale-readable wedge paths
            # close event-driven (no polling).
            return await self._gate_wedge_resolver(session, instance_id)
        finally:
            if owns_session:
                session.close()

    async def _process_child_completion_and_notify_parent(self, instance_id: str, completed_message_id: str) -> None:
        """Check if child instance is done and send completion report to parent.
        
        CRITICAL FIX C3: Content is fetched BEFORE the transaction to avoid
        leaving the instance in COMPLETED state without a report if the fetch fails.
        
        Args:
            instance_id: The child instance that completed.
            completed_message_id: The message ID that just completed (for idempotency).
        """
        # FIX C3: Fetch content BEFORE transaction — avoid orphaned COMPLETED state
        # Get instance's agent_id for the report
        instance_meta = self._instance_repository.get(instance_id)
        agent_id = instance_meta.agent_id if instance_meta else "agent"
        last_content = await self._get_last_assistant_message(instance_id, agent_id)
        if last_content is None:
            logger.warning(f"No assistant content found for instance {instance_id[:8]}..., using empty content for completion check")
            last_content = "[No response content]"  # Proceed with empty content — state transition must still happen
        
        with Session(self._manager._engine) as session:
            # Get instance metadata
            instance = session.get(Instance, instance_id)
            if instance is None:
                return
            
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
                    # Emit status_change SSE event
                    if self._manager._live_hub:
                        try:
                            await self._manager._live_hub.stream_status_change(instance_id, "waiting_children", agent_id=instance.agent_id)
                        except Exception as e:
                            logger.warning(f"Failed to emit status_change for waiting_children: {e}")
                    return

                # DEFECT-2 FIX (premature terminal emission, job 5e197a30): the old
                # code warned-then-proceeded to COMPLETED whenever waiting_for==0
                # but messages were still queued (e.g. child completion reports),
                # publishing "completed" before the root produced its final
                # response. The full gate now holds the instance in
                # WAITING_CHILDREN until waiting_for==0 AND pending_count==0 AND
                # a fresh assistant message exists after the last child_completed.
                # Suppression is re-evaluated on the existing message-completed
                # signal (this handler re-runs for every completed message) —
                # event-driven, no polling.
                allowed, block_reason = await self._root_completion_gate(
                    session, instance_id, waiting_for=instance.waiting_for
                )
                if not allowed:
                    instance.status = InstanceStatus.WAITING_CHILDREN.value
                    session.commit()
                    logger.info(
                        "Instance %s... held in WAITING_CHILDREN, job not terminal "
                        "(gate blocked: %s)",
                        instance_id[:8], block_reason,
                    )
                    # Emit status_change SSE event
                    if self._manager._live_hub:
                        try:
                            await self._manager._live_hub.stream_status_change(instance_id, "waiting_children", agent_id=instance.agent_id)
                        except Exception as e:
                            logger.warning(f"Failed to emit status_change for waiting_children: {e}")
                    return

                # Gate passed (waiting_for==0 ∧ pending_count==0 ∧ fresh response)
                # - safe to complete. The publish below is covered by this gate:
                # only non-blocking awaits (SSE broadcast) separate it from the
                # gate evaluation, and freshness cannot meaningfully regress
                # across that window.
                logger.info(f"Instance {instance_id[:8]}... completed (no parent, no children), status=COMPLETED")

                # Update instance status to COMPLETED in DB
                instance.status = InstanceStatus.COMPLETED.value
                instance.updated_at = datetime.now(timezone.utc).isoformat()
                instance.last_activity_at = datetime.now(timezone.utc)
                instance.version = (instance.version or 1) + 1

                session.commit()

                # COUNCIL FINDING 5 (Round 2 — root/cascade symmetry):
                # The root lane's gate decision at :~825 and the publish below are
                # separated by a small window (commit + SSE broadcast). A concurrent
                # message enqueue for this instance could regress the predicate
                # (new READY message → pending_count>0). The cascade lane's
                # emission-time gate at :~950 is the mirror; the root lane needs
                # the same protection for symmetry. Fail-open for parity with the
                # adjacent publish: if the mirror raises, log and proceed.
                try:
                    mirror_allowed, mirror_reason = await self._root_completion_gate(
                        None, instance_id
                    )
                except Exception as e:
                    logger.error(
                        "Root emission-time mirror gate raised for %s...: %s; "
                        "fail-open to publish (degraded safety)",
                        instance_id[:8], e,
                    )
                    mirror_allowed = True
                    mirror_reason = None

                if not mirror_allowed:
                    # Gate regressed between decision and commit. Downgrade the
                    # root back to WAITING_CHILDREN; the message-completed signal
                    # (fired by any newly-enqueued message's processing) will
                    # re-evaluate and finish the job properly.
                    logger.warning(
                        "Root %s... emission-time mirror gate regressed (%s); "
                        "downgrading to WAITING_CHILDREN (re-evaluate on next "
                        "message-completed signal)",
                        instance_id[:8], mirror_reason,
                    )
                    try:
                        with Session(self._manager._engine) as downgrade_session:
                            row = downgrade_session.get(Instance, instance_id)
                            if (row is not None
                                    and row.status == InstanceStatus.COMPLETED.value):
                                row.status = InstanceStatus.WAITING_CHILDREN.value
                                row.updated_at = datetime.now(timezone.utc).isoformat()
                                row.version = (row.version or 1) + 1
                                downgrade_session.add(row)
                                downgrade_session.commit()
                    except Exception as e:
                        logger.error(
                            "Failed to downgrade suppressed root %s...: %s",
                            instance_id[:8], e,
                        )
                    # Skip the completed SSE + lifecycle publish; fall through
                    # to the child's own title generation below.
                    self._trigger_title_generation(instance_id, completed_message_id)
                    return

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
            should_send = await self._should_send_completion_report(session, instance_id, completed_message_id)
            if not should_send[0]:
                return

            # Check if this is a tool invocation (explore/experience)
            # If so, skip parent notification but still update status and signal CompletionRegistry
            if instance.instance_metadata and instance.instance_metadata.get("invoked_as_tool", False):
                logger.info(
                    f"Instance {instance_id[:8]}... completed (tool invocation, skipping parent report)"
                )

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
            # DEFECT-2 FIX (Round 1): re-verify the full gate at EMISSION time. The decision
            # in _update_parent_on_child_complete predates several awaits (SSE,
            # child_completed broadcast); a concurrent child completion can land a
            # new CHILD_COMPLETED event / report in that window. If the gate no
            # longer holds, downgrade the parent so the re-evaluation on the next
            # message-completed signal can finish the job properly.
            #
            # COUNCIL FINDING 1 (Round 2 — which gate is load-bearing):
            # The emission-time gate HERE is the load-bearing check for the
            # cascade completion path. It runs in a fresh session (so it sees
            # the committed report row, not a staged one — unlike the cascade
            # decision at :~421 which is intentionally conservative due to
            # autoflush skew). On failure, the parent is downgraded back to
            # WAITING_CHILDREN; the message-completed signal will re-evaluate.
            #
            # FAIL-OPEN wrap: the gate call itself must not crash the emit.
            # If the gate raises (corrupt DB row, etc.), log and proceed to
            # publish — the adjacent publish is already guarded by try/except,
            # this gate must be too for parity.
            try:
                allowed, block_reason = await self._root_completion_gate(
                    None, completed_parent_id
                )
            except Exception as e:
                logger.error(
                    "Emission-time gate raised for parent %s...: %s; "
                    "fail-open to publish (degraded safety)",
                    completed_parent_id[:8], e,
                )
                allowed = True
                block_reason = None

            if not allowed:
                logger.warning(
                    "Parent %s... completion publish suppressed at emission time "
                    "(gate blocked: %s); downgrading to WAITING_CHILDREN",
                    completed_parent_id[:8], block_reason,
                )
                try:
                    with Session(self._manager._engine) as downgrade_session:
                        parent_row = downgrade_session.get(Instance, completed_parent_id)
                        if parent_row is not None and parent_row.status == InstanceStatus.COMPLETED.value:
                            parent_row.status = InstanceStatus.WAITING_CHILDREN.value
                            parent_row.updated_at = datetime.now(timezone.utc).isoformat()
                            parent_row.version = (parent_row.version or 1) + 1
                            downgrade_session.add(parent_row)
                            downgrade_session.commit()
                except Exception as e:
                    logger.error(
                        f"Failed to downgrade suppressed parent {completed_parent_id[:8]}...: {e}"
                    )
                # Skip the completed SSE + lifecycle publish; fall through to the
                # child's own title generation below.
                self._trigger_title_generation(instance_id, completed_message_id)
                return

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
