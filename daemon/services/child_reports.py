"""Child reports service for handling child instance completion reports."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import exists, func, select, text, update as sa_update
from sqlmodel import Session

from ..graph import ThinkingChatOpenAI, clean_llm_config
from ..persistence import get_instance_messages
from ..repositories.instance.models import Instance, InstanceStatus
from ..repositories.message_queue.models import MessageQueue, MessageStatus, MessageType
from ..repositories.job_queue.models import JobItem
from ..repositories.task.models import Task, TaskType, TaskStatus
from ..repositories.event.models import Event, EventKind
from ..repositories.dependency_bus.models import DependencyWatcher, DependencyWatcherState
from ..repositories.report_injection.models import ReportInjection, ReportInjectionState
from ..registry import get_registry
from ..write_pause_guard import WriteGuardSession
from .job_queue_service import TERMINAL_STATUSES
from .main_loop_bridge import MainLoopBridge

if TYPE_CHECKING:
    from ..config import Config, ReportRepairConfig
    from ..repositories.instance.repository import SQLModelInstanceRepository
    from .event_publisher import EventPublisherService
    from .error_reporting import ErrorReportingService


logger = logging.getLogger(__name__)


REPORT_REPAIR_PROMPT = """\
The following are the last {count} assistant messages from a child agent that has \
just completed its task. The LAST message (message {n}) may be truncated, incomplete, \
or a short sign-off, while the substantive report content may be in an earlier message.

Your job: produce a SINGLE coherent report message that captures the real completion \
content. If the last message is a valid summary, return it. If the real content is \
in an earlier message, extract and compose the correct report. Be concise — your \
output REPLACES the report content sent to the parent agent.

--- Message {n_minus_2} (word count: {wc_n_minus_2}) ---
{msg_n_minus_2}

--- Message {n_minus_1} (word count: {wc_n_minus_1}) ---
{msg_n_minus_1}

--- Message {n} — LAST (word count: {wc_n}) ---
{msg_n}

Return ONLY the report text, no preamble."""


# W3: Hard cap on combined-report output to keep the parent context window
# bounded. Large tool output or stack traces otherwise blow up the parent's
# next prompt. Mirrors the 500-char cap at child_reports.py:563 but uses a
# larger ceiling because combined text replaces the last assistant message
# rather than sitting inline in a conversation.
MAX_COMBINED_REPORT_CHARS = 10_000


class _ChildCompletionDbResult(NamedTuple):
    """Result of the sync DB half of ``_process_child_completion_and_notify_parent``.

    Carries the outcome + data the async caller needs to fire
    post-commit side effects (SSE / CompletionRegistry / lifecycle event /
    CM resolve hook) AFTER ``asyncio.to_thread`` returns to the event loop.

    The sync helper that produces this runs the entire ``WriteGuardSession``
    block on a worker thread so ``session.commit()`` cannot wedge the event
    loop under SQLite WAL write contention (the same deadlock chain fixed
    in ``job_feedback_observer._finalize_instance``).

    Outcomes:
        ``"instance_not_found"`` — no instance row, nothing to do.
        ``"deferred_waiting_children"`` — root has pending children (bus),
            SSE ``waiting_children`` only, no commit.
        ``"root_waiting_children"`` — root carve-out: own-queue pending,
            commit + SSE ``waiting_children``.
        ``"root_completed"`` — root completed cleanly, commit + SSE
            ``completed`` + CompletionRegistry + lifecycle event + title gen.
        ``"idempotency_skip"`` — already reported, nothing to do.
        ``"tool_invocation_completed"`` — tool-invocation child, commit
            + SSE ``completed`` + CompletionRegistry + lifecycle event +
            title gen.
        ``"regular_child_completed"`` — regular child, commit + SSE
            ``completed`` + CompletionRegistry + lifecycle event + title
            gen + child_completed lifecycle broadcast + (optional) parent
            completion event when all children resolved.
        ``"child_still_running_defer"`` — non-root instance has
            non-terminal children (active in ``instances.parent_id``
            working set), defer; no commit, no report, no event. Mirrors
            ``deferred_waiting_children`` for the non-root parent case.
    """

    outcome: str
    instance_id: str
    agent_id: str | None
    parent_id: str | None
    # For regular_child_completed
    child_agent_id: str | None = None
    report_message_id: str | None = None
    completed_parent_id: str | None = None
    completed_parent_parent_id: str | None = None
    parent_agent_id: str | None = None
    # True if the parent cascade set status to WAITING_CHILDREN (CM-disabled
    # legacy path with pending own-queue messages). The async caller emits
    # the ``waiting_children`` SSE for the parent after the commit.
    parent_waiting_children_sse: bool = False
    waiting_children_parent_agent_id: str | None = None


class ChildReportsService:
    """Service for handling child instance completion reports.

    Handles:
    - Idempotency per-message (won't send duplicate reports for same message)
    - Parent's children[] cache update (FIX: W6)
    - Cascade: bus authoritative for parent completion
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

    def _bus_count_pending_for_target_sync(
        self, target_instance_id: str
    ) -> int:
        """Sync helper: count PENDING watchers targeting ``target_instance_id`` in the bus.

        **Fail-OPEN semantics (warning)**: this helper catches all
        exceptions and returns ``0`` (treated as "no pending
        watchers"). This is fail-OPEN — a transient DB failure
        passes the gate and may allow premature completion.

        **Caller contract**: only use this helper for **defense-in-
        depth checks** that have a separate safety net (the in-
        session bus gate inside ``WriteGuardSession``, a parent bus-pending
        check, etc.). The **authoritative bus
        gate** at the completion decision point must use the inline
        COUNT query directly on the ``WriteGuardSession``'s session
        object (see ``_process_child_completion_db_sync``), so the
        COUNT and UPDATE share one transaction. The inline query
        lets exceptions propagate to the caller's fail-safe path
        instead of silently returning ``0``.

        Kept for: (a) tests that exercise the bus counter path
        directly without going through the completion gate, (b)
        the early defense-in-depth check at line 1718 in
        ``job_feedback_observer.py`` (which has its own in-session
        gate as the authoritative decision point).

        Used by the sync completion gate in
        :meth:`_process_child_completion_db_sync` (which runs inside
        ``WriteGuardSession`` on a worker thread — an ``await`` is
        impossible there) to consult the bus. The bus is the SOLE
        pending-children source — its DB-backed ``dependency_watchers``
        table is authoritative. Without this check, the root-instance
        completion gate falls through to COMPLETED prematurely while
        children are still running — the exact bug the bus was
        designed to prevent.

        Bus singleton missing → returns 0. This treats the gate as a
        no-op when the bus is not wired (bus singleton missing is a
        hard error), so the caller falls through to its own safe
        default rather than blocking on an unavailable authority.

        The implementation delegates to
        :meth:`DependencyBus.count_pending_for_target_sync` which
        wraps the sync repository's ``count_pending_for_target``
        COUNT(*) query (dialect-portable — works on both SQLite
        and PostgreSQL).

        Args:
            target_instance_id: The parent instance ID whose
                PENDING watcher count is being queried.

        Returns:
            Non-negative integer count of PENDING watchers for
            the given target. Returns 0 when the bus singleton is
            not wired or the DB query fails.
        """
        from .dependency_bus import get_dependency_bus

        bus = get_dependency_bus()
        if bus is None:
            return 0

        try:
            return bus.count_pending_for_target_sync(target_instance_id)
        except Exception as e:
            # FAIL-OPEN (see method docstring): a DB failure here
            # returns 0 and PASSES the gate. Callers that use this
            # helper at the completion decision point MUST have a
            # separate safety net (typically the in-session bus
            # gate inline query that shares a transaction with the
            # UPDATE). Logged at warning level so persistent
            # failures surface in observability without taking
            # down the completion path.
            logger.warning(
                f"bus.count_pending_for_target_sync failed for "
                f"{target_instance_id[:8]}...: {e} — treating as 0 "
                f"(FAIL-OPEN: bus pending-children check skipped, "
                f"may cause premature completion if persistent)"
            )
            return 0

    async def _emit_terminal_via_bus(
        self,
        task_id: int | None,
        status: str,
        summary: str | None = None,
        error: str | None = None,
    ) -> list:
        """Emit a terminal event via the DependencyBus and re-trigger parent finalization.

        This helper atomically transitions PENDING watchers for
        ``task_id`` to FIRED via the bus, then **directly** re-triggers
        ``JobFeedbackObserver._finalize_job`` on any parent whose
        watchers are all FIRED.

        The bus is a state machine — it does NOT enqueue messages.
        The leader's LLM path is NEVER injected with a synthetic
        ``[dependency_bus] child XXX completed`` message; the parent
        learns about child completion through the normal
        ``child_reports`` mechanism (the ``internal_report:`` message
        enqueued by ``_process_child_completion_and_notify_parent``)
        and the user's own prompts.

        Args:
            task_id: The child task id whose terminal event is firing.
                ``None`` short-circuits — the bus is keyed on a string
                task id and a missing id means we can't match any
                watchers (this can happen if the task row was already
                GC'd; we log and return an empty list).
            status: One of ``"completed"``, ``"error"``,
                ``"terminated"``. Currently the bus only uses this for
                structured logging — all PENDING watchers fire
                regardless of outcome (the parent needs to know about
                both success and failure).
            summary: Optional human-readable summary of the terminal
                event. Used for structured logging only.
            error: Optional error message when ``status == "error"``.

        Returns:
            List of FollowUps that were atomically transitioned from
            PENDING to FIRED. The list is returned for backward
            compatibility and observability (callers can log how many
            watchers fired), but the FollowUps are NOT enqueued as
            messages — finalization flows through the report Task
            (``PROCESS_REPORT``) → ``_process_event`` path. Empty
            list when no watchers existed.
        """
        from .dependency_bus import (
            Outcome,
            get_dependency_bus,
        )

        bus = get_dependency_bus()
        if bus is None:
            # Bus singleton missing — genuine wiring failure. Return
            # empty list (no fallback exists; the bus is the sole
            # authority).
            logger.warning(
                "_emit_terminal_via_bus: bus singleton is None — "
                "wiring failure, returning empty FollowUp list "
                "(no fallback)"
            )
            return []

        if task_id is None:
            logger.warning(
                "_emit_terminal_via_bus: task_id is None — cannot fire "
                "watchers (no key to match); returning empty FollowUp list"
            )
            return []

        outcome = Outcome(status=status, error=error, summary=summary)
        fired = await bus.emit_terminal(task_id=str(task_id), outcome=outcome)

        # Phase 1 (2026-06-24, report-lane decoupling): the bus no
        # longer re-triggers ``_finalize_job`` directly. The old
        # loop below used to iterate the fired FollowUps, check
        # ``count_pending_for_target == 0`` and call the deleted
        # ``_retrigger_parent_finalize`` shortcut (which itself
        # called ``_finalize_job`` on the JobFeedbackObserver) to
        # short-circuit the natural finalize path.
        #
        # That shortcut was the source of the orphan-Task bug: a
        # finalize fired by the bus would terminate the parent's job
        # while its report Task was still PENDING, leaving the Task
        # with no JobItem to drive it.
        #
        # The natural path is now the only path: the report Task
        # runs (Phase 1.1: ``notify_work()`` wakes the worker; Phase
        # 1.2: ``PROCESS_REPORT`` TaskType uses the same
        # ProcessMessageProcessor delivery), emits a lifecycle
        # event, and ``JobFeedbackObserver._process_event`` checks
        # the bus pending count and finalizes the job when 0.
        # Per-child error status is threaded into the finalize path
        # from the bus's ``had_parent_error`` + new
        # ``parent_error_message`` (Step 1.7).

        # ─── Crash-recovery dedup stamp (C1, 2026-06-22 reorder) ───
        # Stamp ``enqueued_at`` on every fired FollowUp AFTER the
        # finalization loop. Once stamped, a future restart's
        # :meth:`DependencyBus._recover_fired_unsent` will NOT
        # re-deliver this row — the dedup invariant the crash-
        # recovery path relies on. The async wrapper
        # ``bus.mark_enqueued_by_source_target`` runs the repo's
        # sync method on a worker thread so the event loop is not
        # blocked.
        #
        # Why AFTER finalization (not before, as the pre-fix code
        # did): stamping before finalization would leave a
        # ``enqueued_at IS NOT NULL`` row in the DB if the process
        # crashed between stamp and retrigger — recovery would skip
        # the row and the parent would stay PROCESSING forever.
        # Stamping after finalization means a crash before
        # finalization leaves the row un-stamped → next restart
        # retries the finalization (idempotent). See the comment
        # block above for the full invariant.
        for fu in fired:
            # Best-effort stamp; failure here must not abort the
            # loop (the finalization has already run for this target
            # above — leaving the row un-stamped means a future
            # restart will safely retry the finalization, which is
            # idempotent).
            try:
                # ``enqueued_at`` is the C1 dedup marker — once
                # stamped, ``_recover_fired_unsent`` won't return
                # this row on the next restart.
                await bus.mark_enqueued_by_source_target(
                    str(task_id), fu.target_instance_id
                )
            except Exception as stamp_err:
                logger.debug(
                    f"bus follow-up stamp failed (non-fatal): "
                    f"source_task={str(task_id)[:8]}..., "
                    f"target={fu.target_instance_id[:8]}...: {stamp_err}"
                )

        return fired

    async def _emit_terminal_for_child_instance_via_bus(
        self,
        parent_instance_id: str | None,
        child_instance_id: str,
        status: str,
        summary: str | None = None,
        error: str | None = None,
    ) -> list:
        """Fire PENDING watchers for a (parent, child) instance pair.

        Corrective companion to :meth:`_emit_terminal_via_bus` for
        multi-turn non-root children (Wanderer-class). The
        task-keyed helper above matches watchers on the child task
        id of the *current* graph turn — which is correct for
        single-turn children but misses the parent's watcher when
        the child registers the watcher on its FIRST
        ``process_message`` task and reaches its terminal graph
        turn on a LATER ``PROCESS_REPORT`` task. See the call site
        in ``_dispatch_post_commit_side_effects`` (``regular_child_completed``
        outcome) for the full timeline.

        This helper routes the corrective emit through the bus's
        instance-pair matcher (:meth:`DependencyBus.emit_terminal_for_child_instance`).
        Exactly-once is preserved by the bus's underlying
        ``transition_state`` guarded UPDATE — when the task-keyed
        emit already fired the watcher, this call is a no-op.

        Mirrors :meth:`_emit_terminal_via_bus`'s fail-safe shape:
        ``bus is None`` / ``parent_instance_id is None`` short-
        circuits with an empty list (logged at WARNING / DEBUG so
        the wiring failure is observable without aborting the
        parent's finalization).

        Args:
            parent_instance_id: The parent instance id (the watcher's
                ``target_instance_id``). ``None`` short-circuits
                (root instances have no parent to fire watchers for).
            child_instance_id: The just-completed child instance id.
            status: Terminal status string (``"completed"`` /
                ``"error"`` / ``"terminated"``) — logging only; all
                PENDING watchers fire regardless of outcome.
            summary: Optional human-readable summary for structured
                logging.
            error: Optional error message when ``status == "error"``.

        Returns:
            List of FollowUps atomically transitioned from PENDING
            to FIRED by this call (informational — finalization flows
            through the report ``Task`` the DB-sync helper already
            committed). Empty list when bus is None, parent is None,
            no watchers exist, or the task-keyed emit already fired.
        """
        if parent_instance_id is None:
            # Root instances have no parent → no watcher to fire.
            logger.debug(
                "_emit_terminal_for_child_instance_via_bus: "
                "parent_instance_id is None — no watcher to fire "
                "(root instance); returning empty FollowUp list"
            )
            return []

        from .dependency_bus import (
            Outcome,
            get_dependency_bus,
        )

        bus = get_dependency_bus()
        if bus is None:
            logger.warning(
                "_emit_terminal_for_child_instance_via_bus: bus "
                "singleton is None — wiring failure, returning "
                "empty FollowUp list (no fallback)"
            )
            return []

        outcome = Outcome(status=status, error=error, summary=summary)
        fired = await bus.emit_terminal_for_child_instance(
            parent_instance_id=parent_instance_id,
            child_instance_id=child_instance_id,
            outcome=outcome,
        )
        # The bus's ``emit_terminal_for_child_instance`` stamps the
        # ``enqueued_at`` dedup marker itself (per-row, by watch_id);
        # no stamp loop is needed in this caller. The marker keeps a
        # future restart's ``_recover_fired_unsent`` from re-delivering
        # rows this method already fired — same invariant the
        # task-keyed helper's stamp loop in ``_emit_terminal_via_bus``
        # preserves.
        return fired

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
            Formatted prefix like "Developer agent (id=xxx) has done" or
            "Developer agent (name=create-feature-a, id=xxx) has done"
        """
        # Get agent display name from meta.json
        registry = get_registry()
        agent_name = agent_id.capitalize()

        try:
            metadata = registry.get_resolved(agent_id)
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
            agent_id: The agent ID (e.g., "developer", "leader").
            
        Returns:
            Formatted summary string with instance info.
        """
        from langchain_core.messages import HumanMessage, SystemMessage
        
        # Get the report prefix
        prefix = self._get_instance_report_prefix(instance_id, agent_id)
        
        # Get instance messages
        if self._checkpointer:
            messages = await get_instance_messages(self._checkpointer, instance_id)
            # Exclude both synthetic system messages (is_synthetic) and real checkpointed
            # context messages (context_kind: project/shared/skills) so they do not leak
            # into report summaries.
            messages = [m for m in messages if not (m.get("is_synthetic") or m.get("context_kind"))]
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
        llm_config = clean_llm_config(llm_config)
        
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

    async def _update_parent_on_child_complete(self, session, instance, completed_message_id: str | None = None) -> tuple[bool, str | None, str | None]:
        """Update parent state when child completes.

        Handles:
        - Update parent's children cache (FIX: W6)
        - Delete from instance_hierarchy table
        - Cascade: bus authoritative for parent completion

        Args:
            session: Database session.
            instance: The child Instance object.
            completed_message_id: The message ID that just completed (for bus hook).

        Returns:
            Tuple of (transitioned_to_running, completed_parent_id, completed_parent_parent_id):
            - transitioned_to_running: True if parent transitioned to RUNNING (has more work)
            - completed_parent_id: Instance ID if parent completed (for event publishing), None otherwise
            - completed_parent_parent_id: Parent's parent_id if parent completed, None otherwise
        """
        parent = session.get(Instance, instance.parent_id)
        if not parent:
            return False, None, None

        # Phase 5: the bus is the SOLE completion authority. CM is
        # removed. The hook below (``_emit_terminal_via_bus``) asks
        # the bus to atomically transition PENDING watchers for the
        # child task id to FIRED, then directly re-triggers
        # ``_finalize_job`` on any parent whose watchers are all
        # FIRED. The bus DB is authoritative — there is no
        # ``SELECT COUNT(*)`` fallback, no TOCTOU window (Race #1 /
        # #3 eliminated).
        #
        # MUST NOT affect control flow — the bus helper is fail-safe
        # (returns ``[]`` on bus=None or missing task) so a wiring
        # failure cannot break the child-completion path. The inline
        # cascade below this hook is only reached when the bus is
        # None — bus singleton missing is a hard error.
        #
        # Calling context: this method is called from
        # _process_child_completion_and_notify_parent, which is invoked
        # by MessageJobHandler.process via TaskProcessor.run_task using
        # MainLoopBridge.run_async — so we are on the main asyncio event
        # loop. A direct await is safe here.
        #
        # Skip the hook when message_id is missing/empty: the bus keys
        # correlations on the child task id (looked up from
        # message_id) and cannot fire watchers with a None/empty
        # message_id. ``_emit_terminal_via_bus`` itself logs and
        # returns ``[]`` on a missing task — so this guard is
        # only a fast-path skip.
        #
        # The bus is the SOLE completion authority. At completion
        # time we always route through the bus; the helper handles
        # the no-watchers case as a no-op.
        if completed_message_id:
            # Look up the child task id from the message_id — the
            # bus is keyed on task id, not message_id. The lookup
            # runs on a worker thread (sync DB call) so it doesn't
            # block the event loop. When the task row is missing
            # (e.g. cleared by a stale-task sweep before this
            # completion was reported), ``_emit_terminal_via_bus``
            # logs and returns an empty list — no FollowUps to
            # enqueue, no harm done.
            _child_task = None
            _task_repo = getattr(self._manager, "_task_repo", None)
            if _task_repo is not None:
                _child_task = await asyncio.to_thread(
                    _task_repo.get_by_message, completed_message_id
                )
            await self._emit_terminal_via_bus(
                task_id=getattr(_child_task, "id", None),
                status="completed",
                summary="child completed",
            )
        else:
            logger.debug(
                f"Bus hook: skipping terminal emit for parent="
                f"{instance.parent_id[:8] if instance.parent_id else '?'}..., "
                f"child={instance.instance_id[:8]}... "
                f"(no message_id — child completed without a tracked send)"
            )

        parent.last_activity_at = datetime.now(timezone.utc)
        parent.version = (parent.version or 1) + 1

        # NOTE: parent.children cache column was dropped in Phase 4.

        # Remove from instance_hierarchy junction table
        # NOTE: Do NOT delete the instance from instances table - terminate means stop tasks, not delete
        session.execute(
            text("DELETE FROM instance_hierarchy WHERE child_id = :child_id"),
            {"child_id": instance.instance_id}
        )
        
        # Cascade check: if bus says complete, check if parent can complete
        # FIX: Removed status restriction - cascade should run whenever all children done,
        # regardless of current status (e.g., RUNNING from previous cascade). This ensures
        # parent waits for ALL children before completing, not just the first batch.
        # W1 FIX: Also preserve ERROR status during cascade — a parent whose last child
        # completed successfully should still report as ERROR (it errored first, and that
        # state is more useful for diagnostics than overwriting it with COMPLETED).
        #
        from .dependency_bus import get_dependency_bus
        bus = get_dependency_bus()
        if bus is not None:
            # Bus is wired up — it is the SOLE completion authority. The
            # pending count is read from the bus's DB (PENDING watchers
            # — no in-memory CM set, no TOCTOU window — Race #1 / #3
            # eliminated). The ``_sync`` variant runs the COUNT(*)
            # directly on the helper's session, so the bus state and
            # the in-memory decision share a consistent read at this
            # call site (the bus helper also fails-closed on DB
            # errors, but the bus singleton being non-None means the
            # DB is reachable).
            is_parent_complete = bus.count_pending_for_target_sync(parent.instance_id) == 0
        else:
            # ─── A8: HARD ERROR (not graceful degradation) ─────────────
            # Bus is None is an INVALID state. The ``SELECT COUNT(*)``
            # fallback (Race #3) is the exact bug we are fixing — it MUST
            # NOT be reachable. The bus must be initialized for the new
            # architecture to work; we raise rather than silently degrade
            # into the TOCTOU fallback.
            #
            # Honest propagation note: this RuntimeError is caught by the
            # W3 fail-safe (``except Exception``) in
            # ``_finalize_job`` and results in a per-job FAILED transition.
            # It is NOT a process-level crash — the daemon stays alive and
            # only the affected job fails. For production, the bus must be
            # initialized before any traffic (the startup invariant
            # enforced in ``daemon/main.py`` via
            # ``init_dependency_bus``).
            raise RuntimeError(
                f"DependencyBus is not initialized for parent="
                f"{parent.instance_id[:8]}...; bus must be initialized. "
                f"This is a hard error — the SELECT COUNT(*) TOCTOU fallback "
                f"(Race #3) is disabled by design."
            )
        # PAUSED excluded too: a paused question() parent must not be
        # auto-completed by a child's completion (resume() owns its
        # terminal transition). This is a skip guard, not a legitimate
        # transition write.
        if (
            is_parent_complete
            and parent.status != InstanceStatus.COMPLETED.value
            and parent.status != InstanceStatus.ERROR.value
            and parent.status != InstanceStatus.PAUSED.value
        ):
            # Phase 3 (Cascade Unification): when the bus is active,
            # the inline cascade + SELECT COUNT(*) + inline status
            # transition are SKIPPED. The bus's ``emit_terminal`` (called
            # via the hook above) already atomically transitioned matching
            # PENDING watchers to FIRED, and if that was the last
            # watcher the bus re-triggers
            # ``_finalize_job`` directly. The bus DB is the source
            # of truth (no TOCTOU window — Race #3 eliminated).
            #
            # When bus is None (bus singleton missing — hard error),
            # keep the existing logic with the SELECT COUNT(*)
            # fallback. This path is also the one exercised by every
            # test that does not wire a bus fixture (e.g.
            # tests/job_queue/test_in_progress_guard.py).
            if bus is not None:
                # Bus is active — bus callback handles completion.
                # No count_pending query, no inline status transition,
                # no inline lifecycle event (the caller at line ~914-931
                # is also skipped because we return completed_parent_id=None).
                logger.info(
                    f"Bus-active: skipping inline cascade for parent "
                    f"{parent.instance_id[:8]}... — bus callback owns completion"
                )
                return False, None, None

            # (dead-code fallback — bus-active path bypasses) Phase 2
            # hardening: the ``parent_pending`` count uses the shared
            # positive-polarity predicate
            # ``message_queue_counts_as_pending`` so that any
            # ``processing``/``retrying`` row whose backing work is
            # terminal (e.g. cancelled by the resume cascade) does not
            # block the parent from completing. The reachable production
            # site at ``child_reports.py:1459`` uses the same helper —
            # the production bus path bypasses this branch entirely
            # (return above), so this is future-proofing.
            from ..repositories.message_queue.predicates import (
                message_queue_counts_as_pending,
            )
            _fallback_candidates_1 = session.exec(
                select(MessageQueue)
                .where(MessageQueue.instance_id == parent.instance_id)
                .where(MessageQueue.status.in_([
                    MessageStatus.READY.value,
                    MessageStatus.PROCESSING.value,
                    MessageStatus.RETRYING.value,
                ]))
            ).scalars().all()
            parent_pending = sum(
                1
                for _r in _fallback_candidates_1
                if message_queue_counts_as_pending(_r, self._manager.engine)
            )
            del _fallback_candidates_1

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
                #
                # Phase 4: WAITING_CHILDREN is DEPRECATED as a control-flow
                # signal — the DependencyBus is the authoritative source
                # of correlation state. The status set is RETAINED for
                # graceful-degradation (bus singleton None) and for the
                # FIFO carve-out SQL compatibility
                # (daemon/repositories/task/repository.py). The
                # ``transitioned_to_running`` return value remains ``True``
                # because the parent is still alive (will process more
                # messages) — contract required by
                # ``tests/test_cascade_integration.py``.
                #
                # Defense-in-depth (F8): was a ``_has_no_active_message_job``
                # carve-out check here. Removed in Phase 5: after D13 there
                # are no MESSAGE ``JobItem`` rows, so the guard was a
                # permanent no-op. The Task-level coordination guard in
                # ``claim_pending_task`` (one RUNNING task per instance)
                # is the post-D13 invariant that replaces it.
                parent.status = InstanceStatus.WAITING_CHILDREN.value
                logger.info(
                    f"Parent {parent.instance_id[:8]}... all children done but has {parent_pending} "
                    f"pending messages, status=WAITING_CHILDREN (deprecated; bus is authoritative)"
                )
                # Emit status_change SSE event for parent waiting_children
                # (display only — status field is being phased out).
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
        pending_for_parent: int,
    ) -> tuple[Event, Event]:
        """Create completion events for child and parent.

        Args:
            session: Database session.
            instance_id: The child instance ID.
            parent_id: The parent instance ID.
            report_message_id: The report message ID for the parent event.
            pending_for_parent: PENDING-watcher count for the parent
                (from the DependencyBus).

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
                "pending_for_parent": pending_for_parent,
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
            agent_id: The agent ID (e.g., "developer", "leader").
            
        Returns:
            Formatted string with instance info and last message.
        """
        # Get the report prefix
        prefix = self._get_instance_report_prefix(instance_id, agent_id)
        
        raw_content = await self._get_last_assistant_message_raw(instance_id)
        
        if raw_content:
            return f"{prefix}, below is the response:\n{raw_content}"
        return None

    @staticmethod
    def _is_likely_truncated_report(messages: list[dict], *, ratio: float = 3.0) -> bool:
        """Check if the last assistant message is likely truncated.

        Returns True if any of the penultimate messages (n-1 or n-2) has a
        word count > ``ratio`` × the last message's word count, indicating
        the real content may be in an earlier message.

        W5 tuning: skip repair when the last message has >=5 words (a 5+
        word message is unlikely to be a truncation) AND require the earlier
        message to have >=20 words (don't trigger on tiny earlier messages).

        Args:
            messages: List of message dicts (already filtered to real
                assistant messages — no synthetic/context_kind). Must be
                in chronological order.
            ratio: Size ratio threshold (default 3.0).

        Returns:
            True if the report looks truncated and should trigger repair.
        """
        if len(messages) < 2:
            return False
        last_content = messages[-1].get("content", "")
        if not last_content or not last_content.strip():
            return True  # empty last message → definitely truncated
        last_wc = len(last_content.split())
        # W5: floor — a 5+ word last message is unlikely to be a truncation.
        if last_wc >= 5:
            return False
        for msg in messages[-3:-1]:  # n-1 and n-2 (if present)
            earlier_wc = len((msg.get("content", "") or "").split())
            # W5: also require the earlier message to have >=20 words so
            # tiny earlier messages don't false-positive the heuristic.
            if earlier_wc >= 20 and earlier_wc > ratio * last_wc:
                return True
        return False

    async def _repair_report_with_llm(
        self, messages: list[dict], config: "ReportRepairConfig", *, instance_id: str
    ) -> str | None:
        """Call the LLM to compose a repaired report from the last messages.

        Mirrors ``LoopRepairer._summarize_loop`` (graph.py:1455) and the
        existing ``_generate_completion_report`` LLM-call pattern
        (child_reports.py:549). Returns the repaired text, or None on
        failure/timeout (caller falls back to combining messages).

        W1 fix: indexing uses NEGATIVE offsets so n=2 lands the earlier
        message in ``msg_n_minus_1`` correctly (previously ``_get(1)`` and
        ``_get(n-1)`` both resolved to the same last message when n=2).

        Args:
            messages: Last N assistant message dicts (chronological).
            config: Report repair config for timeout/threshold.
            instance_id: Instance ID for log correlation (W2).

        Returns:
            Repaired report text, or None if LLM failed/timed out.
        """
        # S5: lazy import — mirrors the convention at graph.py:1011-1017.
        # Keeps the module-level import surface small and avoids the cycle
        # risk if compaction ever imports child_reports.
        from ..compaction import _extract_text_from_content

        from langchain_core.messages import HumanMessage, SystemMessage

        n = len(messages)

        # Build the prompt — use NEGATIVE indexing so n=2 correctly maps
        # msg_n_minus_1 → the earlier (longer) message and msg_n → the last.
        def _get(idx: int) -> tuple[str, int]:
            c = messages[idx].get("content", "") or ""
            return c, len(c.split())

        msg_n_minus_2, wc_n_minus_2 = _get(-3) if n >= 3 else ("", 0)
        msg_n_minus_1, wc_n_minus_1 = _get(-2) if n >= 2 else ("", 0)
        msg_n, wc_n = _get(-1) if n >= 1 else ("", 0)

        prompt = REPORT_REPAIR_PROMPT.format(
            count=n,
            n=n,
            n_minus_1=n - 1,
            n_minus_2=n - 2,
            wc_n_minus_2=wc_n_minus_2,
            wc_n_minus_1=wc_n_minus_1,
            wc_n=wc_n,
            msg_n_minus_2=msg_n_minus_2,
            msg_n_minus_1=msg_n_minus_1,
            msg_n=msg_n,
        )

        timeout = config.timeout_seconds
        try:
            # Mirror the existing LLM-call pattern in this module
            # (child_reports.py:549-561): build a manual dict, clean it,
            # and construct via ThinkingChatOpenAI.
            llm_config = {
                "base_url": self._config.llm.base_url,
                "api_key": self._config.llm.api_key,
                "model": self._config.llm.model,
                "temperature": 0.3,
                "default_headers": {"x-proxy-app": "ensemble"},
            }
            cleaned = clean_llm_config(llm_config)
            llm = ThinkingChatOpenAI(**cleaned)

            response = await asyncio.wait_for(
                asyncio.to_thread(
                    llm.invoke,
                    [
                        SystemMessage(
                            content="You are a report summarizer. Compose the single best report message from the given child agent messages."
                        ),
                        HumanMessage(content=prompt),
                    ],
                ),
                timeout=timeout,
            )
            return _extract_text_from_content(response.content)

        except asyncio.TimeoutError:
            logger.warning(
                f"[ReportRepairer] LLM timed out after {timeout}s for instance "
                f"{instance_id[:8]}..., using combine-fallback"
            )
            return None
        except Exception as e:
            # S7: include instance_id in the generic exception log too.
            logger.warning(
                f"[ReportRepairer] LLM failed for instance {instance_id[:8]}...: "
                f"{type(e).__name__}: {e}"
            )
            return None

    @staticmethod
    def _combine_messages(messages: list[dict]) -> str:
        """Combine multiple messages into a single report text (fallback).

        Chronological order: earliest first, last message last. Output is
        capped at :data:`MAX_COMBINED_REPORT_CHARS` (10_000) to keep the
        parent context window from blowing up on large tool output or stack
        traces — the LLM-side truncation pattern at child_reports.py:563
        (``_summarize_instance_conversation``) caps at 500 chars; this
        fallback cap is much higher because the combined text replaces the
        last assistant message rather than sitting inline in a conversation.

        When truncation kicks in, a warning is logged and the suffix
        ``…[truncated]…`` is appended so the parent can see the report
        was cut short.
        """
        combined = "\n\n---\n\n".join(
            (m.get("content", "") or "").strip()
            for m in messages
            if (m.get("content", "") or "").strip()
        )
        if len(combined) > MAX_COMBINED_REPORT_CHARS:
            logger.warning(
                f"[ReportRepairer] Combined report truncated: "
                f"{len(combined)} -> {MAX_COMBINED_REPORT_CHARS} chars"
            )
            combined = combined[:MAX_COMBINED_REPORT_CHARS] + "…[truncated]…"
        return combined

    async def _get_last_assistant_message_raw(self, instance_id: str) -> str | None:
        """Get the raw last assistant message content (no formatting).

        Returns just the actual agent response content, matching the format
        used by MessageJobHandler when setting result_summary=result.content.

        When unhappy-path report repair is enabled (``report_repair.enabled``),
        checks whether the last message looks truncated (an earlier message
        is much larger). If so, attempts LLM repair; on LLM failure/timeout,
        combines the last messages into one report.

        Args:
            instance_id: The instance ID to get message from.

        Returns:
            The raw assistant message content, or None if not found.
        """
        # --- Fetch and filter messages (existing logic) ---
        if self._checkpointer:
            messages = await get_instance_messages(self._checkpointer, instance_id)
            # Exclude both synthetic system messages (is_synthetic) and real checkpointed
            # context messages (context_kind: project/shared/skills) so they do not leak
            # into report content.
            messages = [m for m in messages if not (m.get("is_synthetic") or m.get("context_kind"))]
        else:
            messages = []

        # Collect real assistant messages with content (chronological)
        assistant_msgs = [
            m for m in messages
            if m.get("role") == "assistant" and (m.get("content", "") or "").strip()
        ]
        if not assistant_msgs:
            return None

        last = assistant_msgs[-1]
        last_content = (last.get("content", "") or "").strip()

        # --- Repair disable short-circuit ---
        # S4: Config has ``default_factory`` for ``report_repair`` — always
        # present, no need for ``getattr`` defensive guard.
        report_repair_cfg = self._config.report_repair
        if not report_repair_cfg.enabled:
            return last_content  # happy path, skip repair entirely

        # --- Truncation check ---
        # NOTE: lookback_messages currently affects only the combine fallback
        # slice (recent = assistant_msgs[-lookback:]). The heuristic/repair
        # slices use [-3:] internally.
        lookback = report_repair_cfg.lookback_messages
        recent = assistant_msgs[-lookback:] if len(assistant_msgs) >= lookback else assistant_msgs
        if not self._is_likely_truncated_report(recent, ratio=report_repair_cfg.size_ratio_threshold):
            return last_content  # happy path — sizes are similar

        logger.info(
            f"[ReportRepairer] Unhappy path triggered for instance {instance_id[:8]}... "
            f"({len(recent)} recent messages, ratio>{report_repair_cfg.size_ratio_threshold})"
        )

        # --- LLM repair ---
        repaired = await self._repair_report_with_llm(
            recent, report_repair_cfg, instance_id=instance_id
        )
        if repaired and repaired.strip():
            logger.info(f"[ReportRepairer] LLM repair succeeded for instance {instance_id[:8]}...")
            return repaired.strip()

        # --- Fallback: combine messages ---
        logger.info(f"[ReportRepairer] Using combine-fallback for instance {instance_id[:8]}...")
        combined = self._combine_messages(recent)
        return combined if combined.strip() else last_content

    async def _process_child_completion_and_notify_parent(self, instance_id: str, completed_message_id: str) -> None:
        """Check if child instance is done and send completion report to parent.
        
        CRITICAL FIX C3: Content is fetched BEFORE the transaction to avoid
        leaving the instance in COMPLETED state without a report if the fetch fails.
        
        The entire ``WriteGuardSession`` block (DB reads, writes, commits) now
        runs on a worker thread via ``asyncio.to_thread`` — mirrors the
        ``_finalize_instance_db_sync`` fix in ``job_feedback_observer.py``.
        Under SQLite WAL write contention (busy_timeout=30s), a sync
        ``session.commit()`` on the event loop thread wedges the loop
        completely — Ctrl+C ignored, all APIs frozen. Moving it to a
        worker thread keeps the loop responsive.
        
        Post-commit side effects (SSE, CompletionRegistry, lifecycle events,
        CM resolve hook) remain on the event loop — they fire AFTER
        ``asyncio.to_thread`` returns.
        
        Args:
            instance_id: The child instance that completed.
            completed_message_id: The message ID that just completed (for idempotency).
        """
        logger.info(f"_process_child_completion_and_notify_parent called: instance={instance_id[:8]}..., message_id={completed_message_id[:8] if completed_message_id else None}")
        
        # FIX C3: Fetch content BEFORE transaction — avoid orphaned COMPLETED state
        # Get instance's agent_id for the report (sync DB read → wrap in to_thread)
        instance_meta = await asyncio.to_thread(
            self._instance_repository.get, instance_id
        )
        agent_id = instance_meta.agent_id if instance_meta else "agent"
        last_content = await self._get_last_assistant_message(instance_id, agent_id)
        if last_content is None:
            logger.warning(f"No assistant content found for instance {instance_id[:8]}..., using empty content for completion check")
            last_content = "[No response content]"  # Proceed with empty content — state transition must still happen

        # MAJOR A fix (re-arm safety net, 2026-06-22; Phase 1 bus
        # migration 2026-06-23): wrap the ``asyncio.to_thread`` call in
        # the per-parent ``asyncio.Lock`` when the bus is wired. After
        # Phase 1 the lock lives on the bus (see
        # ``DependencyBus._get_parent_lock``); CM's ``_get_lock`` is a
        # deprecated passthrough and will be removed in Phase 5. The
        # lock is held on the EVENT LOOP for the entire duration of the
        # worker-thread sync helper, blocking ``bus.watch()`` (which
        # also acquires ``bus._get_parent_lock(parent_id)``) from
        # running on the loop and committing a new watcher row between
        # the in-session bus gate inside the sync helper and the
        # terminal status UPDATE that follows it.
        #
        # Why this works (no deadlock risk):
        #   * ``asyncio.Lock`` is event-loop-bound — the worker
        #     thread that ``asyncio.to_thread`` runs in NEVER
        #     acquires it; it only runs the sync SQLAlchemy code
        #     while the GIL is released during I/O.
        #   * The lock serializes coroutines on the loop, not
        #     threads.
        #   * ``bus.watch()`` acquires per-parent lock → bus task
        #     lock (sequential, never nested). We acquire the
        #     per-parent lock only here. No cycle exists.
        #   * WriteGuardSession is a Python-level counter, not a DB
        #     lock — no interaction.
        #
        # When the bus is None (legacy path / not initialized), no
        # lock is acquired — no concurrent writer to race against.
        from .dependency_bus import get_dependency_bus
        bus = get_dependency_bus()
        if bus is not None:
            async with await bus._get_parent_lock(instance_id):
                # Run the ENTIRE WriteGuardSession block on a
                # worker thread so session.commit() cannot deadlock
                # the event loop. The per-parent lock is held on the
                # event loop for the duration — see the comment
                # above.
                result = await asyncio.to_thread(
                    self._process_child_completion_db_sync,
                    instance_id,
                    completed_message_id,
                    last_content,
                )
        else:
            result = await asyncio.to_thread(
                self._process_child_completion_db_sync,
                instance_id,
                completed_message_id,
                last_content,
            )

        # Dispatch post-commit side effects on the event loop.
        await self._dispatch_post_commit_side_effects(
            result, last_content, completed_message_id
        )

    @staticmethod
    def _ghost_child_filter():
        """Boolean SQL expr: True when an ``Instance`` child row is a ghost.

        A ghost (Inc 2026-08-03 ``tester-stuck-waiting-children-orphaned-idle-worker``)
        is a child that was spawned but whose dispatch FAILED — it sits at
        ``status='idle'``, ``version=1``, and has NEVER received a
        ``message_queue`` or ``task`` row (no work was ever queued, no turn
        ever ran). Because ``idle`` is not in ``TERMINAL_STATUSES``, both
        completion guards count such a child as "active" forever and
        permanently wedge its parent at ``waiting_children`` — the child
        can never produce a completion report on its own (its dispatch
        already failed), so the parent must not wait for it.

        Excluding ghosts via this filter unblocks the wedge regardless of
        which spawn/send failure mode produced the orphan (cold-load
        ``None``-read, unattributed cache eviction, etc. — all left the row
        idle/v1/empty). The filter is intentionally EMPTY-state-based so a
        genuinely-dispatched ``idle`` child (has queued work but has not
        started its first turn yet) is NOT treated as a ghost — its
        ``message_queue``/``task`` rows keep it blocking as intended.

        Returns the ``Instance``-scoped boolean expression; callers wrap it
        with ``~`` (NOT) to exclude ghosts from an active-children count.
        """
        no_messages = ~exists(
            select(MessageQueue.message_id).where(
                MessageQueue.instance_id == Instance.instance_id
            )
        )
        no_tasks = ~exists(
            select(Task.id).where(Task.instance_id == Instance.instance_id)
        )
        return (
            (Instance.status == InstanceStatus.IDLE.value)
            & (Instance.version == 1)
            & no_messages
            & no_tasks
        )

    def _count_actionable_pending_tasks(self, session: Session, instance_id: str) -> int:
        """Count PENDING tasks for an instance that will actually run a turn.
        Used by both completion guards (root + non-root) to decide whether
        to defer completion. A PENDING ``process_report`` task whose report
        was ALREADY delivered via the report-injection hot path
        (``state = INJECTED``) is EXCLUDED: the task processor skips such
        tasks (no graph turn runs), so they are no-ops that must NOT defer
        completion — otherwise the parent stalls in ``waiting_children``
        forever, because the skipped task never produces a turn to
        re-evaluate completion. Reports still awaiting delivery
        (``PENDING`` / ``TASK_DELIVERED``) ARE counted, since their
        ``process_report`` turn will run and may spawn more children.

        Correlation: ``Task.message_id`` of a ``process_report`` task IS
        the ``completion_report`` message id, which is also
        ``ReportInjection.report_message_id``. For non-report tasks
        (``process_message``) there is no matching ``report_injections``
        row, so the EXISTS is false and the task is counted.

        Args:
            session: An open (WriteGuard)Session.
            instance_id: The instance whose pending tasks to count.

        Returns:
            Count of PENDING tasks that will run a real graph turn.
        """
        injected_report_exists = (
            select(ReportInjection.injection_id)
            .where(ReportInjection.report_message_id == Task.message_id)
            .where(ReportInjection.state == ReportInjectionState.INJECTED.value)
            .exists()
        )
        return session.exec(
            select(func.count())
            .select_from(Task)
            .where(Task.instance_id == instance_id)
            .where(Task.status == TaskStatus.PENDING.value)
            .where(~injected_report_exists)
        ).scalar_one()

    def _process_child_completion_db_sync(
        self,
        instance_id: str,
        completed_message_id: str | None = None,
        last_content: str = "",
    ) -> _ChildCompletionDbResult:
        """Sync DB half of ``_process_child_completion_and_notify_parent``.
        
        Opens a ``WriteGuardSession`` and performs ALL DB reads/writes/commits
        for the four child-completion branches (root, idempotency-skip,
        tool-invocation, regular-child). Returns a ``_ChildCompletionDbResult``
        describing the outcome so the async caller can fire the post-commit
        side effects (SSE / CompletionRegistry / lifecycle event / CM resolve
        hook) on the event loop.
        
        Runs on a worker thread via ``asyncio.to_thread`` from
        ``_process_child_completion_and_notify_parent``. This keeps
        ``session.commit()`` off the event loop so SQLite WAL write contention
        cannot deadlock the daemon (see the deadlock analysis in the
        experience docs — same chain as ``_finalize_instance``).
        
        The DB operations are EXACTLY the same as the pre-refactor inline
        block — same order, same conditions, same commits. Only the call
        site changed (inline → extracted helper + ``to_thread``).
        
        Args:
            instance_id: The child instance that completed.
            completed_message_id: The message ID that just completed.
            last_content: The assistant message content (pre-fetched to avoid
                orphaned COMPLETED state — see Fix C3).
            
        Returns:
            ``_ChildCompletionDbResult`` with the outcome + data the async
            caller needs for side effects.
        """
        with WriteGuardSession(Session(self._manager.engine), self._manager.write_guard) as session:
            # Get instance metadata
            instance = session.get(Instance, instance_id)
            if instance is None:
                logger.info(f"Instance {instance_id[:8]}... not found in DB, skipping")
                return _ChildCompletionDbResult(
                    outcome="instance_not_found",
                    instance_id=instance_id,
                    agent_id=None,
                    parent_id=None,
                )

            logger.info(f"Instance {instance_id[:8]}... parent_id={instance.parent_id}, status={instance.status}")

            # ─── Fix 2 idempotency short-circuit ─────────────────────────
            # If the instance is already in a terminal state (COMPLETED
            # or ERROR), short-circuit immediately. Re-running this
            # function on a terminal instance would otherwise re-write
            # status, re-emit a completion_report, re-emit an
            # INSTANCE_COMPLETED event, and re-trigger the bus terminal
            # hook — duplicating every observable side effect on each
            # re-entry (the Wanderer per-graph-turn pattern).
            #
            # This is a status-level idempotency guard: the existing
            # in-branch idempotency check (existing_report query)
            # catches the duplicate-message case via the report message
            # source, but it does NOT catch re-entries with a different
            # ``completed_message_id`` (or with None) where the row has
            # already been finalized. The status check is the
            # coarser-grained safety net that supersedes both.
            # PAUSED is also excluded: a paused question() instance must not
            # be overwritten by a stale completion report while the user is
            # editing/inspecting it (resume() owns the terminal transition).
            if instance.status in (
                InstanceStatus.COMPLETED.value,
                InstanceStatus.ERROR.value,
                InstanceStatus.PAUSED.value,
            ):
                logger.info(
                    f"Instance {instance_id[:8]}... already in terminal "
                    f"or paused state ({instance.status}), skipping "
                    f"_process_child_completion_db_sync (idempotency)"
                )
                return _ChildCompletionDbResult(
                    outcome="idempotency_skip",
                    instance_id=instance_id,
                    agent_id=instance.agent_id,
                    parent_id=instance.parent_id,
                )

            # Not a child? Instance completed (no parent to send report to)
            # Check if we have active children - if so, wait for them before completing.
            #
            # Phase 5: bus is the SOLE completion authority.
            if instance.parent_id is None:
                from .dependency_bus import get_dependency_bus
                bus = get_dependency_bus()
                if bus is not None:
                    # Phase 5: bus is the SOLE completion authority. The
                    # ``_sync`` variant runs the COUNT(*) directly on
                    # the helper's session (no event-loop hop, safe to
                    # call from this worker-thread sync function).
                    pending_children = bus.count_pending_for_target_sync(instance_id)
                else:
                    # ─── A8: HARD ERROR (not graceful degradation) ─────────
                    # Bus is None is an INVALID state. Mirrors the
                    # A8 hard error at site 2.
                    raise RuntimeError(
                        f"DependencyBus is not initialized for instance="
                        f"{instance_id[:8]}...; bus must be initialized "
                        f"(Phase 5)."
                    )
                if pending_children > 0:
                    # Has children still running — defer completion.
                    # Phase 4: do NOT transition status to WAITING_CHILDREN;
                    # the bus is the authoritative source of pending
                    # children and instances stay PROCESSING while
                    # children run. The ``waiting_children`` SSE event
                    # is kept for watcher compatibility (display only).
                    logger.info(
                        f"Instance {instance_id[:8]}... completed message but waiting for "
                        f"{pending_children} children (bus={bus is not None}), deferring completion"
                    )
                    # SSE side effect is dispatched by the async caller.
                    return _ChildCompletionDbResult(
                        outcome="deferred_waiting_children",
                        instance_id=instance_id,
                        agent_id=instance.agent_id,
                        parent_id=None,
                    )

                # Pending-task guard (TOCTOU fix, 2026-07-22) — symmetric to
                # the non-root guard below. ``pending_children == 0`` is
                # necessary but not sufficient: a root instance can end a
                # turn with zero pending children while it still has a
                # PENDING task queued (e.g. a ``PROCESS_REPORT`` turn from
                # a child that just reported). Marking it COMPLETED now
                # would be premature and the idempotency guard would later
                # drop the real final completion. Defer until the queued
                # turn drains. See the non-root guard for the full rationale.
                pending_tasks = self._count_actionable_pending_tasks(session, instance_id)
                if pending_tasks > 0:
                    logger.info(
                        f"Instance {instance_id[:8]}... completed message "
                        f"with 0 pending children but {pending_tasks} "
                        f"pending task(s), deferring completion "
                        f"(pending-tasks guard)"
                    )
                    return _ChildCompletionDbResult(
                        outcome="deferred_waiting_children",
                        instance_id=instance_id,
                        agent_id=instance.agent_id,
                        parent_id=None,
                    )

                # Phase 5 (Cascade Unification — Fix A2): root completion
                # is NOT a child response, so we MUST NOT call any
                # ``resolve_response`` hook here (a self-referential
                # key would never match any registered watcher and
                # would silently no-op). Instead, we use
                # ``bus.count_pending_for_target_sync(...) == 0`` as
                # a read-only check (Condition 1): are all child
                # responses received?
                # The bus DB is the authoritative source.
                #
                # Note: ``bus`` is reused from the earlier lookup above
                # (it remains in scope after the ``return`` guard at
                # the end of the previous block).
                if bus is not None:
                    all_children_done = bus.count_pending_for_target_sync(instance_id) == 0
                    if not all_children_done:
                        # Bus still has pending watchers for this root.
                        #
                        # Phase 4: do NOT set status to WAITING_CHILDREN
                        # — instances stay PROCESSING while children run.
                        # The ``waiting_children`` SSE event is kept below
                        # for watcher compatibility (display only).
                        logger.info(
                            f"Instance {instance_id[:8]}... bus has "
                            f"unresolved child responses, status=PROCESSING (bus tracks pending)"
                        )
                        # SSE side effect is dispatched by the async caller.
                        return _ChildCompletionDbResult(
                            outcome="deferred_waiting_children",
                            instance_id=instance_id,
                            agent_id=instance.agent_id,
                            parent_id=None,
                        )

                # ─── Bus gate (premature-completion fix) ───────────
                # The bus DB is the authoritative source of pending-
                # children truth — we MUST consult it here, before
                # falling through to COMPLETED, or the root instance
                # will be marked COMPLETED while a child is still
                # working (the exact premature-completion bug the bus
                # was designed to prevent).
                #
                # C2 fix (TOCTOU hardening, 2026-06-22): inline the
                # COUNT query directly on the WriteGuardSession's
                # ``session`` object so the COUNT and the in-session
                # status UPDATE below share the SAME transaction.
                # The previous helper
                # (``_bus_count_pending_for_target_sync``) opened its
                # own short-lived Session via the bus repository,
                # creating transaction A — while the UPDATE below
                # commits in transaction B (the WriteGuardSession).
                # A concurrent ``bus.watch()`` INSERT on a different
                # connection could commit between A and B,
                # re-opening the premature-completion window this
                # gate exists to close. With the inline query, the
                # COUNT and UPDATE are atomic at the DB level (on
                # SQLite full write lock; on PostgreSQL READ
                # COMMITTED within a single transaction).
                #
                # The query is the same dialect-portable COUNT(*)
                # the helper uses, but executed on the existing
                # ``session`` so it joins this transaction. No try/
                # except — exceptions propagate to the existing
                # W3 fail-safe path in the async caller
                # (``_process_child_completion_and_notify_parent``).
                # This is fail-CLOSED (a transient DB failure aborts
                # the transition, not silently passes the gate).
                #
                # B fix (2026-06-22): the inline query must NOT
                # catch exceptions — see MEDIUM B in the review.
                # Catching and returning 0 would silently pass the
                # gate, reintroducing the premature-completion bug
                # on transient DB errors.
                #
                # The per-parent CM lock from Fix A (above the
                # ``asyncio.to_thread`` call) serializes this gate
                # against ``bus.watch()`` — a watch INSERT that
                # commits inside the lock is guaranteed visible to
                # the COUNT here, regardless of who wins the race.
                #
                # Defensive wiring check: only consult the bus when
                # the bus singleton is wired. When the singleton is
                # None (testing, missing init, config drift), the
                # gate is dormant — same semantics as the original
                # ``_bus_count_pending_for_target_sync`` helper
                # before the C2 inline refactor. Without this guard,
                # a test or config that doesn't wire the bus
                # singleton would still execute the inline COUNT
                # against an empty table — usually harmless (returns
                # 0), but in degraded states (mock MagicMock
                # truthiness, partial migrations) it could defer a
                # completion that should proceed.
                from .dependency_bus import get_dependency_bus as _get_bus
                if _get_bus() is not None:
                    _bus_pending_stmt = (
                        select(func.count())
                        .select_from(DependencyWatcher)
                        .where(DependencyWatcher.target_instance_id == instance_id)
                        .where(
                            DependencyWatcher.state
                            == DependencyWatcherState.PENDING.value
                        )
                    )
                    bus_pending = int(session.scalar(_bus_pending_stmt) or 0)
                    if bus_pending > 0:
                        # Bug fix (2026-06-22): transition
                        # ``instance.status`` to ``WAITING_CHILDREN``
                        # so the frontend reflects the "leader is
                        # waiting for children" state on the bus
                        # path. Previously the status stayed at
                        # whatever it was (typically ``running``)
                        # so the UI showed ``running`` even though
                        # no LLM call was in flight. The
                        # bus_pending count is the authoritative
                        # signal that real pending children exist;
                        # the legacy ``_has_no_active_message_job``
                        # carve-out was removed in Phase 5 (no
                        # MESSAGE ``JobItem`` rows exist post-D13).
                        instance.status = InstanceStatus.WAITING_CHILDREN.value
                        instance.updated_at = datetime.now(timezone.utc).isoformat()
                        instance.version = (instance.version or 1) + 1
                        session.commit()
                        logger.info(
                            f"Instance {instance_id[:8]}... CM says "
                            f"complete but bus has {bus_pending} "
                            f"PENDING watchers, "
                            f"deferring completion, "
                            f"status=WAITING_CHILDREN"
                        )
                        # SSE side effect is dispatched by the
                        # async caller.
                        return _ChildCompletionDbResult(
                            outcome="deferred_waiting_children",
                            instance_id=instance_id,
                            agent_id=instance.agent_id,
                            parent_id=None,
                        )

                # Defense-in-depth: live-children cross-check (Inc
                # 2026-08-02 "leader completed while tester child still
                # running"). The bus gate above trusts
                # ``count_pending_for_target_sync == 0`` as the
                # authoritative "all children done" signal. But the bus
                # ``dependency_watchers`` rows can be moved out of PENDING
                # by silent raw-SQL writers (e.g. the ``reconcile_turn_mirror``
                # cancel guard in ``daemon/repositories/task/repository.py``),
                # which would zero the bus count while a child instance is
                # genuinely still working. This second gate consults the
                # ``instances`` table directly: a root with any
                # non-terminal child must NEVER reach COMPLETED, regardless
                # of what the bus reports.
                #
                # Same-transaction guarantee: this COUNT runs on the
                # existing ``WriteGuardSession`` / ``session`` so it shares
                # the SAME transaction as the bus COUNT above and the
                # ``Instance`` status UPDATE below — closing the TOCTOU
                # window between the read and the write (mirrors the C2
                # inline-bus-COUNT pattern at :1417-1428). A transient
                # failure propagates to the existing W3 fail-safe path
                # (fail-CLOSED) rather than silently passing the gate.
                live_children_stmt = (
                    select(func.count())
                    .select_from(Instance)
                    .where(Instance.parent_id == instance_id)
                    .where(
                        Instance.status.notin_([
                            InstanceStatus.COMPLETED.value,
                            InstanceStatus.ERROR.value,
                            InstanceStatus.TERMINATED.value,
                            InstanceStatus.FAILED.value,
                        ])
                    )
                    .where(~ChildReportsService._ghost_child_filter())
                )
                live_children = int(session.scalar(live_children_stmt) or 0)
                if live_children > 0:
                    instance.status = InstanceStatus.WAITING_CHILDREN.value
                    instance.updated_at = datetime.now(timezone.utc).isoformat()
                    instance.version = (instance.version or 1) + 1
                    session.commit()
                    logger.info(
                        f"Instance {instance_id[:8]}... bus reports 0 "
                        f"pending but {live_children} live child instance(s) "
                        f"found, deferring completion, "
                        f"status=WAITING_CHILDREN"
                    )
                    return _ChildCompletionDbResult(
                        outcome="deferred_waiting_children",
                        instance_id=instance_id,
                        agent_id=instance.agent_id,
                        parent_id=None,
                    )

                # Check pending messages before completing.
                # Exclude the just-completed message by ID (mirrors
                # _should_send_completion_report at line 270-279) to avoid the
                # double-count hazard when message_queue.complete() has not
                # committed yet.
                #
                # Phase 3: this ``SELECT COUNT(*)`` is RETAINED. It checks
                # a different concern (root's OWN queue pending work —
                # messages from external sources like HTTP, scheduler),
                # not child-response correlation. Per ADR-012 and the plan,
                # this is NOT subject to Race #3.
                #
                # Phase 4: WAITING_CHILDREN is DEPRECATED — display/log only.
                #
                # Phase 2 (Bug B): this site is the **1 reachable
                # production parent-completion guard** (the 3 dead-code
                # fallbacks at ``child_reports.py:863`` / ``:2058`` /
                # ``error_reporting.py:270`` are gated behind bus-active
                # early-returns and are dead code in production). The
                # ``pending_count`` now uses the shared positive-polarity
                # predicate ``message_queue_counts_as_pending`` — see
                # ``daemon/repositories/message_queue/predicates.py``.
                # The base status filter (READY/PROCESSING/RETRYING) is
                # unchanged; the predicate handles the terminal/live
                # decision per row using ``work_id`` as the identity
                # key. This closes the production incident where two
                # ``completion_report`` rows were orphaned at
                # ``processing`` with terminal backing Tasks and the
                # parent stayed stuck at ``WAITING_CHILDREN`` forever.
                from ..repositories.message_queue.predicates import (
                    message_queue_counts_as_pending,
                )
                # ``.scalars().all()`` ensures we get MessageQueue
                # instances (not ``Row`` objects) on every dialect —
                # ``select(MessageQueue).all()`` is documented but the
                # ``Row``-object fallback was observed in tests under
                # ``WriteGuardSession`` (the proxy's ``__getattr__`` on
                # ``exec`` may bypass SQLModel's row-mapping path on
                # some versions). ``scalars()`` is the supported API.
                _candidate_rows = session.exec(
                    select(MessageQueue)
                    .where(MessageQueue.instance_id == instance_id)
                    .where(MessageQueue.message_id != completed_message_id)
                    .where(MessageQueue.status.in_([
                        MessageStatus.READY.value,
                        MessageStatus.PROCESSING.value,
                        MessageStatus.RETRYING.value,
                    ]))
                ).scalars().all()
                pending_count = sum(
                    1
                    for _row in _candidate_rows
                    if message_queue_counts_as_pending(_row, self._manager.engine)
                )
                del _candidate_rows  # scope hygiene

                # WHY ROOT GETS WAITING_CHILDREN BUT NON-ROOT DOES NOT:
                # This branch is reached only when ``instance.parent_id is None``
                # (a root instance). The semantic concern here is the root's OWN
                # message queue — messages from external sources (HTTP, scheduler,
                # direct user input) that are not child-response correlations.
                # The CM tracks parent→child correlation; it does NOT track a
                # root's own-queue work. So we still set WAITING_CHILDREN to
                # signal "this root has queued work to process" even though
                # there is no child correlation pending.
                #
                # Non-root parents (handled in ``_update_parent_on_child_complete``
                # at line ~565) are gated by the bus pending-count check — they
                # stay PROCESSING because the bus tracks their pending children.
                # They never reach the SELECT COUNT branch
                # for their own queue because the bus path keeps the parent in
                # PROCESSING until the bus reports no pending children.
                #
                # The root carve-out is intentional and aligned with Site 1B
                # (ADR-012) two-condition check: root completion requires BOTH
                # no pending children AND no own-queue messages.
                if pending_count > 0:
                    # Single pending_count guard is sufficient. Do NOT
                    # transition to COMPLETED — there is queued work that
                    # the worker must still process.
                    #
                    # Phase 4: WAITING_CHILDREN status set is retained
                    # for graceful-degradation watchers and FIFO
                    # carve-out SQL compatibility (display only).
                    #
                    # Phase 5: the legacy
                    # ``_has_no_active_message_job`` carve-out check
                    # was removed. After D13 there are no MESSAGE
                    # ``JobItem`` rows, so the guard was a permanent
                    # no-op. The own-queue ``pending_count`` is the
                    # authoritative signal that real queued work
                    # exists.
                    instance.status = InstanceStatus.WAITING_CHILDREN.value
                    session.commit()
                    logger.info(
                        f"Instance {instance_id[:8]}... has {pending_count} pending "
                        f"messages, status=WAITING_CHILDREN (deprecated)"
                    )
                    # SSE side effect is dispatched by the async caller.
                    return _ChildCompletionDbResult(
                        outcome="root_waiting_children",
                        instance_id=instance_id,
                        agent_id=instance.agent_id,
                        parent_id=None,
                    )

                # No children, no pending messages - safe to complete
                logger.info(f"Instance {instance_id[:8]}... no parent, skipping notification")

                # No children, no pending messages - safe to complete
                logger.info(f"Instance {instance_id[:8]}... completed (no parent, no children), status=COMPLETED")

                # Defense-in-depth: if this instance became PAUSED during processing
                # (e.g. question() tool), do NOT overwrite with COMPLETED.
                # The primary cross-session race protection is the pipeline's
                # _is_instance_paused() fresh-DB check before _check_child_completion.
                if instance.status == InstanceStatus.PAUSED.value:
                    logger.info(
                        f"Instance {instance_id[:8]}... is PAUSED at root-completion "
                        f"write, skipping COMPLETED transition (idempotency)"
                    )
                    return _ChildCompletionDbResult(
                        outcome="idempotency_skip",
                        instance_id=instance_id,
                        agent_id=instance.agent_id,
                        parent_id=None,
                    )

                # Defense-in-depth atomic guard: use SQLAlchemy Core
                # ``UPDATE ... WHERE status NOT IN (...)`` so a pause
                # cascade that commits PAUSED between the ``session.get``
                # above and this write cannot be silently overwritten.
                # The where-not-in set is the canonical {PAUSED, COMPLETED,
                # ERROR} terminal-and-paused set; rowcount == 0 means
                # another writer (e.g. question() pause cascade) already
                # finalized the row and we must skip the rest of the
                # completion logic for this path.
                now_iso = datetime.now(timezone.utc).isoformat()
                now_dt = datetime.now(timezone.utc)
                update_result = session.execute(
                    sa_update(Instance)
                    .where(Instance.instance_id == instance_id)
                    .where(
                        Instance.status.notin_([
                            InstanceStatus.PAUSED.value,
                            InstanceStatus.COMPLETED.value,
                            InstanceStatus.ERROR.value,
                        ])
                    )
                    .values(
                        status=InstanceStatus.COMPLETED.value,
                        updated_at=now_iso,
                        last_activity_at=now_dt,
                        version=Instance.version + 1,
                    )
                )
                session.commit()
                if update_result.rowcount == 0:
                    logger.warning(
                        f"Instance {instance_id[:8]}... root-completion "
                        f"UPDATE matched 0 rows (status already in "
                        f"{{paused,completed,error}}); skipping "
                        f"completion side effects (TOCTOU defense)"
                    )
                    return _ChildCompletionDbResult(
                        outcome="idempotency_skip",
                        instance_id=instance_id,
                        agent_id=instance.agent_id,
                        parent_id=None,
                    )

                # Post-commit side effects (SSE, CompletionRegistry, lifecycle, title)
                # are dispatched by the async caller.
                return _ChildCompletionDbResult(
                    outcome="root_completed",
                    instance_id=instance_id,
                    agent_id=instance.agent_id,
                    parent_id=None,
                )
            
            # Idempotency checks — inlined from _should_send_completion_report
            # (no await needed; runs on worker thread inside WriteGuardSession).
            if completed_message_id is None:
                pending_count = session.exec(
                    select(func.count())
                    .select_from(MessageQueue)
                    .where(MessageQueue.instance_id == instance_id)
                    .where(MessageQueue.status.in_([
                        MessageStatus.PROCESSING.value,
                        MessageStatus.RETRYING.value,
                    ]))
                ).scalar_one()
                should_send = pending_count > 0
                skip_reason = "no_completed_message_id"
            else:
                pending_count = session.exec(
                    select(func.count())
                    .select_from(MessageQueue)
                    .where(MessageQueue.instance_id == instance_id)
                    .where(MessageQueue.message_id != completed_message_id)
                    .where(MessageQueue.status.in_([
                        MessageStatus.PROCESSING.value,
                        MessageStatus.RETRYING.value,
                    ]))
                ).scalar_one()
                if pending_count > 0:
                    should_send = False
                    skip_reason = "pending_messages_exist"
                else:
                    inst_check = session.get(Instance, instance_id)
                    if inst_check is None:
                        should_send = False
                        skip_reason = "instance_not_found"
                    elif inst_check.parent_id is None:
                        should_send = False
                        skip_reason = "no_parent_id"
                    else:
                        existing_report = session.exec(
                            select(MessageQueue)
                            .where(MessageQueue.instance_id == inst_check.parent_id)
                            .where(MessageQueue.source == f"internal_report:{instance_id}:{completed_message_id}")
                            .where(MessageQueue.status.in_([
                                MessageStatus.READY.value,
                                MessageStatus.PROCESSING.value,
                                MessageStatus.COMPLETED.value,
                            ]))
                        ).first()
                        if existing_report is not None:
                            should_send = False
                            skip_reason = "idempotency_skip"
                        else:
                            should_send = True
                            skip_reason = "all_checks_passed"
            
            if not should_send:
                logger.info(f"Instance {instance_id[:8]}... completion report skipped: reason={skip_reason}")
                return _ChildCompletionDbResult(
                    outcome="idempotency_skip",
                    instance_id=instance_id,
                    agent_id=instance.agent_id,
                    parent_id=instance.parent_id,
                )

            # Check if this is a tool invocation (explore/experience)
            # If so, skip parent notification but still update status and signal CompletionRegistry
            if instance.instance_metadata and instance.instance_metadata.get("invoked_as_tool", False):
                logger.info(
                    f"Instance {instance_id[:8]}... completed (tool invocation, skipping parent report)"
                )
                logger.info(f"Instance {instance_id[:8]}... is tool invocation, skipping parent notification")

                # Capture parent_id before any further writes; the ORM
                # ``Instance`` reference stays valid through commit
                # because the WriteGuardSession owns this session.
                parent_id = instance.parent_id

                # Defense-in-depth atomic guard: use SQLAlchemy Core
                # ``UPDATE ... WHERE status NOT IN (...)`` so a pause
                # cascade that commits PAUSED between the ``session.get``
                # above and this write cannot be silently overwritten.
                # rowcount == 0 means another writer already finalized
                # the row and we must skip the rest of the completion
                # logic for this path.
                now_iso = datetime.now(timezone.utc).isoformat()
                now_dt = datetime.now(timezone.utc)
                update_result = session.execute(
                    sa_update(Instance)
                    .where(Instance.instance_id == instance_id)
                    .where(
                        Instance.status.notin_([
                            InstanceStatus.PAUSED.value,
                            InstanceStatus.COMPLETED.value,
                            InstanceStatus.ERROR.value,
                        ])
                    )
                    .values(
                        status=InstanceStatus.COMPLETED.value,
                        updated_at=now_iso,
                        last_activity_at=now_dt,
                        version=Instance.version + 1,
                    )
                )
                session.commit()
                if update_result.rowcount == 0:
                    logger.warning(
                        f"Instance {instance_id[:8]}... tool-invocation "
                        f"UPDATE matched 0 rows (status already in "
                        f"{{paused,completed,error}}); skipping "
                        f"completion side effects (TOCTOU defense)"
                    )
                    return _ChildCompletionDbResult(
                        outcome="idempotency_skip",
                        instance_id=instance_id,
                        agent_id=instance.agent_id,
                        parent_id=parent_id,
                    )

                # Post-commit side effects are dispatched by the async caller.
                return _ChildCompletionDbResult(
                    outcome="tool_invocation_completed",
                    instance_id=instance_id,
                    agent_id=instance.agent_id,
                    parent_id=parent_id,
                )

            # ─── Wanderer active-children guard ────────────────────────────
            # Symmetric to the root-instance ``deferred_waiting_children``
            # branch above (lines ~1146-1163): if this non-root instance
            # still has children with non-terminal status, defer the
            # completion_report emission. Without this guard, a non-root
            # parent like Wanderer emits a completion_report to its
            # parent (leader) on every graph turn while its own spawned
            # children are still running — corrupting the leader's
            # understanding of child completion and risking premature
            # finalization.
            #
            # Source of truth: ``instances.parent_id`` (the permanent
            # record), NOT the bus ``DependencyWatcher`` counter. The bus
            # under-counts because ``spawn_instance`` does not register
            # bus watchers for every spawn — using it here would let
            # the defer gate leak. ``instances.parent_id`` is the
            # source-of-truth working set the cascade code already
            # maintains (insert on spawn, status transitions on
            # completion/error).
            #
            # The guard checks whether the just-completed instance
            # ITSELF has non-terminal children (rows whose
            # ``parent_id == instance_id``). When Wanderer emits its own
            # completion_report to leader, Wanderer's spawned coders
            # are rows with ``parent_id == wanderer_id``. Without this
            # check, Wanderer would emit a completion_report on every
            # graph turn even while its coders are still running.
            #
            # Status filter: ``status NOT IN (COMPLETED, ERROR,
            # TERMINATED, FAILED)`` — the canonical terminal set
            # (``daemon.services.job_queue_service.TERMINAL_STATUSES``).
            # Without TERMINATED / FAILED here, a single TERMINATED or
            # FAILED sibling would be counted as active and permanently
            # wedge the parent (it would never emit a completion_report).
            # These states are terminal — the child will not resume
            # normal work, so the parent must not wait for it. Mirrors
            # the cascade completion check (Fix 1).
            #
            # Note: this is the ONLY Fix-1 site we expand. Fix 2's
            # idempotency set (``child_reports.py:1144-1147``) and the
            # parent-cascade check (``child_reports.py:1682-1684``) have
            # the same COMPLETED/ERROR-only omission but that is
            # PRE-EXISTING and out of scope for this PR — leaving them
            # untouched keeps the diff focused.
            active_children = session.exec(
                select(func.count())
                .select_from(Instance)
                .where(Instance.parent_id == instance_id)
                .where(Instance.instance_id != instance_id)
                .where(
                    Instance.status.not_in(TERMINAL_STATUSES)
                )
                .where(~self._ghost_child_filter())
            ).scalar_one()
            # ─── Pending-task guard (TOCTOU fix, 2026-07-22) ──────────────
            # ``active_children == 0`` is necessary but NOT sufficient for
            # true completion: a non-root parent can end a turn with zero
            # active children while it STILL has queued work — a PENDING
            # task (e.g. a ``PROCESS_REPORT`` turn from a child that just
            # reported) that will run next and spawn more children. Without
            # this check, the parent is prematurely marked COMPLETED and
            # reports to its own parent; when the queued task then runs and
            # the parent truly finishes, the idempotency guard
            # ("already in terminal state") silently drops the real final
            # report. Reproduced with the tester (777eff96): turn 9287 ended
            # at 07:28:50 with 0 children while task 9288 was still PENDING
            # (it later spawned worker 78b04db6); the premature completion
            # at 07:28:50 then suppressed the real completion at 07:32:58.
            #
            # Count PENDING tasks for this instance only. The CURRENT turn's
            # task is RUNNING (the task processor claims pending→running
            # before driving the graph, and only marks it COMPLETED after
            # this helper returns), so it is excluded by the status filter.
            # The per-instance serialization guard (one RUNNING task per
            # instance) guarantees no second RUNNING task exists, so every
            # non-current queued task is PENDING and is counted here.
            #
            # CRITICAL: a pending ``process_report`` whose report was
            # already delivered via the report-injection hot path
            # (INJECTED) is a NO-OP (the task processor skips it) and is
            # EXCLUDED by ``_count_actionable_pending_tasks`` — counting
            # it would defer completion forever because the skipped task
            # never produces a turn to re-evaluate completion.
            pending_tasks = self._count_actionable_pending_tasks(session, instance_id)
            if active_children > 0 or pending_tasks > 0:
                # The just-completed instance (e.g., Wanderer / a tester)
                # still has non-terminal children OR queued turns of its
                # own. Mirror the root branch's defer outcome — no status
                # transition, no report, no events. The child's status stays
                # in its pre-call value (typically RUNNING) until the parent
                # actually finalizes the subtree.
                logger.info(
                    f"Non-root instance {instance_id[:8]}... has "
                    f"{active_children} active children / {pending_tasks} "
                    f"pending task(s), deferring completion_report to "
                    f"parent (parent={instance.parent_id[:8]}..., "
                    f"active-children/pending-tasks guard)"
                )
                return _ChildCompletionDbResult(
                    outcome="child_still_running_defer",
                    instance_id=instance_id,
                    agent_id=instance.agent_id,
                    parent_id=instance.parent_id,
                )

            # ATOMIC: Instance completed — create completion report for parent
            logger.info(f"Instance {instance_id[:8]}... completed, sending report to parent {instance.parent_id[:8]}...")

            # --- Inline: _create_completion_report (no await needed) ---
            # Defense-in-depth atomic guard: use SQLAlchemy Core
            # ``UPDATE ... WHERE status NOT IN (...)`` so a pause
            # cascade that commits PAUSED between the ``session.get``
            # above and this write cannot be silently overwritten by
            # a child completion. rowcount == 0 means another writer
            # (e.g. question() pause cascade) already finalized the
            # row and we must skip the entire completion_report
            # emission (message + task + report_injection + parent
            # cascade) for this path.
            now_iso = datetime.now(timezone.utc).isoformat()
            now_dt = datetime.now(timezone.utc)
            update_result = session.execute(
                sa_update(Instance)
                .where(Instance.instance_id == instance_id)
                .where(
                    Instance.status.notin_([
                        InstanceStatus.PAUSED.value,
                        InstanceStatus.COMPLETED.value,
                        InstanceStatus.ERROR.value,
                    ])
                )
                .values(
                    status=InstanceStatus.COMPLETED.value,
                    updated_at=now_iso,
                    last_activity_at=now_dt,
                    version=Instance.version + 1,
                )
            )
            if update_result.rowcount == 0:
                # Roll back any implicit session state from the failed
                # UPDATE attempt so the idempotency_skip return is
                # clean. (No session.add() calls have happened yet at
                # this point in the function.)
                session.rollback()
                logger.warning(
                    f"Instance {instance_id[:8]}... inlined "
                    f"cascade UPDATE matched 0 rows (status already "
                    f"in {{paused,completed,error}}); skipping "
                    f"completion_report + parent cascade "
                    f"(TOCTOU defense)"
                )
                return _ChildCompletionDbResult(
                    outcome="idempotency_skip",
                    instance_id=instance_id,
                    agent_id=instance.agent_id,
                    parent_id=instance.parent_id,
                )

            # Create completion report message for parent
            # Include message_id in source for per-message idempotency
            report_message_id = str(uuid.uuid4())
            report_message = MessageQueue(
                message_id=report_message_id,
                instance_id=instance.parent_id,
                content=last_content,
                source=f"internal_report:{instance.instance_id}:{completed_message_id}",
                type=MessageType.COMPLETION_REPORT.value,
                status=MessageStatus.READY.value,
                priority=0,
                enqueued_at=datetime.now(timezone.utc),
            )
            session.add(report_message)
            
            # Create task for parent to process the report.
            #
            # C2 torn-state guard: the deferred-pause pattern exists because
            # a graph task cannot call the pause cascade itself (the cascade
            # would cancel the task while its transaction is still active).
            # ``question_pause_node`` therefore calls
            # ``set_deferred_question_pause`` before the post-graph callback
            # starts the cascade. The DB commit in ``_pause_cascade_db_sync``
            # happens on a worker thread, leaving a short race window where
            # the marker is set but the parent's status still reads RUNNING.
            # We need both checks: the marker catches that window, while the
            # DB check catches the Path 4 user-click-stop cascade, which has
            # no marker.
            #
            # **Marker lifetime (C1 fix, 2026-07)**: the marker is set in
            # ``question_pause_node``, **peeked** in the post-graph completion
            # path via ``has_deferred_question_pause`` BEFORE awaiting
            # ``pause_instance_cascade``, and **popped** in the inner
            # ``finally`` block AFTER the cascade's DB commit completes. The
            # old "pop before cascade" ordering left the marker empty during
            # the cascade's DB-commit window — both this guard (Phase 1) and
            # the ``_prepare_enqueued_message`` guard (Phase 2) would see
            # ``marker=False, db=RUNNING`` and CREATE a spurious Task.
            # Extending the marker lifetime past the cascade closes that race.
            #
            # MessageQueue and ReportInjection are intentionally created
            # regardless of this decision.  ReportInjection is the durable
            # fallback, drained before EVERY LLM call (not only on resume) by
            # claim_for_injection (graph.py:2566-2590).  A terminated parent
            # can still leave an orphaned PENDING row; cleanup is deferred to
            # the follow-up reconcile_terminal_report_injections work.
            # Phase 2 adds the companion guard in
            # instance_messaging.py:_prepare_enqueued_message.
            parent = session.get(Instance, instance.parent_id)
            marker_paused = (
                instance.parent_id in self._manager._deferred_question_pause
            )
            db_paused = (
                parent is not None
                and parent.status == InstanceStatus.PAUSED.value
            )
            if marker_paused or db_paused:
                skip_reason = "marker" if marker_paused else "db_status"
                logger.info(
                    f"child_reports: skipping PROCESS_REPORT Task creation for parent "
                    f"{instance.parent_id[:8]}... — reason={skip_reason}; "
                    f"report_injection row will deliver on resume via "
                    f"claim_for_injection (graph.py:2566-2590)"
                )
            else:
                # Phase 1 (2026-06-24): PROCESS_REPORT — see TaskType docstring.
                report_task = Task(
                    task_type=TaskType.PROCESS_REPORT.value,
                    instance_id=instance.parent_id,
                    message_id=report_message_id,
                    status=TaskStatus.PENDING.value,
                    created_at=datetime.now(timezone.utc),
                )
                session.add(report_task)

            # ─── Report-injection queue (deadlock fix) ───────────────────
            # Enqueue a row in ``report_injections`` in the SAME
            # transaction as the completion_report message + the
            # PROCESS_REPORT task, so the three are crash-consistent.
            # The parent's LIVE agent-node drains PENDING rows from
            # this queue (via the ``ReportInjectionSlot`` factory
            # closure) right before each LLM call and injects them as
            # ``HumanMessage``\s — delivering the report ASAP, WITHOUT
            # waiting for the parent's turn to end.
            #
            # This fixes the deadlock where a parent that held its
            # graph turn open (polling / sleeping for child reports)
            # blocked the PROCESS_REPORT task via the per-instance
            # serialization guard (one RUNNING task per instance) —
            # the report sat in ``pending`` forever. With this queue,
            # the live turn pulls the report on its next LLM call;
            # the PROCESS_REPORT task remains as the fallback for when
            # no turn is live (and as the crash-recovery path).
            #
            # Exactly-once between the two paths is enforced by the
            # atomic ``state = PENDING → terminal`` claim in
            # ``ReportInjectionRepository`` (drain marks ``INJECTED``;
            # the fallback task claims ``TASK_DELIVERED``; the loser
            # sees no PENDING row and skips). See the package docstring
            # in ``daemon/repositories/report_injection/``.
            report_injection_row = ReportInjection(
                parent_instance_id=instance.parent_id,
                child_instance_id=instance.instance_id,
                child_message_id=completed_message_id,
                report_message_id=report_message_id,
                content=last_content,
            )
            session.add(report_injection_row)
            
            # --- Inline: _update_parent_on_child_complete (no await needed) ---
            # The bus is the SOLE completion authority.
            if parent is None:
                raise RuntimeError(
                    f"Parent {instance.parent_id} disappeared during child completion"
                )
            parent.last_activity_at = datetime.now(timezone.utc)
            parent.version = (parent.version or 1) + 1

            # NOTE: parent.children cache column was dropped in Phase 4.

            # Remove from instance_hierarchy junction table
            # NOTE: Do NOT delete the instance from instances table - terminate means stop tasks, not delete
            session.execute(
                text("DELETE FROM instance_hierarchy WHERE child_id = :child_id"),
                {"child_id": instance.instance_id}
            )

            # Cascade check — DependencyBus is the SOLE completion
            # authority (Phase 5). The inlined copy here exists because
            # ``_process_child_completion_db_sync`` runs on a worker
            # thread via ``asyncio.to_thread`` and cannot ``await`` the
            # async helper. Both call sites enforce the same A8
            # invariant.
            from .dependency_bus import get_dependency_bus
            bus = get_dependency_bus()
            if bus is not None:
                is_parent_complete = bus.count_pending_for_target_sync(parent.instance_id) == 0
            else:
                # ─── A8: HARD ERROR (not graceful degradation) ─────────────
                # Bus is None is an INVALID state. The ``SELECT COUNT(*)``
                # fallback (Race #3) is the exact bug we are fixing — it
                # MUST NOT be reachable. Mirrors the hard error at the
                # ``_update_parent_on_child_complete`` call site.
                #
                # Honest propagation note: this RuntimeError is caught by
                # the W3 fail-safe (``except Exception``) in
                # ``_finalize_job`` and results in a per-job FAILED
                # transition. It is NOT a process-level crash. For
                # production, the bus must be initialized before any
                # traffic.
                raise RuntimeError(
                    f"DependencyBus is not initialized for parent="
                    f"{instance.parent_id[:8] if instance.parent_id else '?'}...; "
                    f"bus must be initialized. "
                    f"This is a hard error — the SELECT COUNT(*) TOCTOU fallback "
                    f"(Race #3) is disabled by design."
                )
            
            completed_parent_id: str | None = None
            completed_parent_parent_id: str | None = None
            parent_waiting_children_sse: bool = False
            waiting_children_parent_agent_id: str | None = None
            
            # PAUSED excluded too: a paused question() parent must not be
            # auto-completed by a child's completion (resume() owns its
            # terminal transition). This is a skip guard, not a legitimate
            # transition write.
            if (
                is_parent_complete
                and parent.status != InstanceStatus.COMPLETED.value
                and parent.status != InstanceStatus.ERROR.value
                and parent.status != InstanceStatus.PAUSED.value
            ):
                if bus is not None:
                    # Bus is active — bus callback handles completion.
                    # No count_pending query, no inline status transition.
                    logger.info(
                        f"Bus-active: skipping inline cascade for parent "
                        f"{parent.instance_id[:8]}... — bus callback owns completion"
                    )
                else:
                    # (dead-code fallback — bus-active path bypasses)
                    # Phase 2 hardening: ``parent_pending`` uses the
                    # shared positive-polarity predicate so terminal
                    # backing work does not block parent completion.
                    # The reachable production site is
                    # ``child_reports.py:1459``; this is future-proofing
                    # for any code path that bypasses the bus.
                    from ..repositories.message_queue.predicates import (
                        message_queue_counts_as_pending,
                    )
                    _fallback_candidates_2 = session.exec(
                        select(MessageQueue)
                        .where(MessageQueue.instance_id == parent.instance_id)
                        .where(MessageQueue.status.in_([
                            MessageStatus.READY.value,
                            MessageStatus.PROCESSING.value,
                            MessageStatus.RETRYING.value,
                        ]))
                    ).scalars().all()
                    parent_pending = sum(
                        1
                        for _r in _fallback_candidates_2
                        if message_queue_counts_as_pending(_r, self._manager.engine)
                    )
                    del _fallback_candidates_2

                    if parent_pending == 0:
                        # No pending messages, parent is truly complete
                        parent.status = InstanceStatus.COMPLETED.value
                        parent.updated_at = datetime.now(timezone.utc).isoformat()
                        logger.info(f"Parent {parent.instance_id[:8]}... completed after all children done")
                        completed_parent_id = parent.instance_id
                        completed_parent_parent_id = parent.parent_id
                    else:
                        # Has pending messages but all children done - transition to WAITING_CHILDREN
                        #
                        # Phase 5: the previous ``_has_no_active_message_job``
                        # carve-out (defense-in-depth against stale/duplicate
                        # ``message_queue`` rows from a task-claim race) was
                        # REMOVED. After D13 there are no MESSAGE
                        # ``JobItem`` rows, so the guard was a permanent
                        # no-op. The bus ``count_pending_for_target_sync``
                        # already gates the inline cascade above; once we
                        # cross into the pending-messages branch the
                        # ``message_queue`` own-queue count is the
                        # authoritative signal. The child-completion work
                        # above (report message, task, hierarchy delete)
                        # is still real and we let the function proceed to
                        # commit + emit events — just write the parent
                        # status here.
                        parent.status = InstanceStatus.WAITING_CHILDREN.value
                        logger.info(
                            f"Parent {parent.instance_id[:8]}... all children done but has {parent_pending} "
                            f"pending messages, status=WAITING_CHILDREN (deprecated; bus is authoritative)"
                        )
                        # Flag SSE emission for the async caller (was at
                        # line 624-627 in the pre-refactor inline block).
                        parent_waiting_children_sse = True
                        waiting_children_parent_agent_id = parent.agent_id
            
            # --- Inline: _create_completion_events (no await needed) ---
            # Source of ``pending_for_parent``: DependencyBus (SOLE completion
            # authority). When bus is None the error-reporting path raises
            # A9 hard error above, so we only ever reach here with a bus.
            #
            # Fix 3 (off-by-1 correction): the snapshot below is taken
            # INSIDE the WriteGuardSession transaction, BEFORE the
            # post-commit bus terminal hook fires (called from
            # ``_dispatch_post_commit_side_effects`` for the
            # ``regular_child_completed`` outcome — see ~lines 1880-1900).
            # That hook atomically transitions the just-completed child's
            # PENDING watcher to FIRED, decrementing the parent's
            # pending count by exactly 1. The CHILD_COMPLETED event
            # emitted here is intended for the parent's UI / observers —
            # it should reflect the parent's pending-children count
            # AFTER the watcher fires (the post-commit reality), not
            # the pre-commit snapshot.
            #
            # The minimal, least-invasive fix: subtract 1 with a
            # ``max(0, ...)`` clamp. We know exactly one watcher was
            # just fired (the current child's task), so the corrected
            # post-fire count is ``current_count - 1``. ``max(0, ...)``
            # is defensive — should never underflow, but a transient
            # race that drops the count below 0 must not produce a
            # negative value in the event payload.
            _raw_pending = bus.count_pending_for_target_sync(instance.parent_id)
            pending_for_parent = max(0, int(_raw_pending or 0) - 1)
            completion_event = Event(
                instance_id=instance_id,
                kind=EventKind.INSTANCE_COMPLETED.value,
                data=json.dumps({
                    "parent_id": instance.parent_id,
                    "report_message_id": report_message_id,
                }),
                created_at=datetime.now(timezone.utc),
            )
            session.add(completion_event)
            
            # Also create event for parent about child completion
            parent_event = Event(
                instance_id=instance.parent_id,
                message_id=report_message_id,
                kind=EventKind.CHILD_COMPLETED.value,
                data=json.dumps({
                    "child_instance_id": instance_id,
                    "pending_for_parent": pending_for_parent,
                }),
                created_at=datetime.now(timezone.utc),
            )
            session.add(parent_event)
            
            # Capture IDs before session closes (instance will be detached)
            parent_id = instance.parent_id
            child_agent_id = instance.agent_id
            
            # Capture parent's agent_id for status_change event
            parent_agent_id: str | None = None
            if completed_parent_id:
                parent_for_event = session.get(Instance, completed_parent_id)
                if parent_for_event:
                    parent_agent_id = parent_for_event.agent_id
            
            session.commit()
            # Post-commit side effects are dispatched by the async caller.
            return _ChildCompletionDbResult(
                outcome="regular_child_completed",
                instance_id=instance_id,
                agent_id=instance.agent_id,
                parent_id=parent_id,
                child_agent_id=child_agent_id,
                report_message_id=report_message_id,
                completed_parent_id=completed_parent_id,
                completed_parent_parent_id=completed_parent_parent_id,
                parent_agent_id=parent_agent_id,
                parent_waiting_children_sse=parent_waiting_children_sse,
                waiting_children_parent_agent_id=waiting_children_parent_agent_id,
            )

    async def _dispatch_post_commit_side_effects(
        self,
        result: _ChildCompletionDbResult,
        last_content: str,
        completed_message_id: str,
    ) -> None:
        """Fire post-commit side effects for ``_process_child_completion_and_notify_parent``.

        Called on the event loop AFTER ``asyncio.to_thread`` returns from
        ``_process_child_completion_db_sync``. Dispatches SSE, CompletionRegistry,
        lifecycle events, and the bus terminal hook based on the DB outcome.

        The bus terminal hook (``bus.emit_terminal``) fires AFTER the
        commit so that the bus's DB-backed watcher state is updated
        after the DB state is consistent. Finalization then flows
        through the report ``Task`` (PROCESS_REPORT) →
        ``JobFeedbackObserver._process_event`` path — the bus is a
        pure state machine, the observer handles terminal
        transitions. There is no longer a separate bus-callback DB
        transaction.

        Args:
            result: The outcome from the sync DB helper.
            last_content: The assistant message content (used for CompletionRegistry).
            completed_message_id: The completed message ID (used for bus hook).
        """
        outcome = result.outcome
        instance_id = result.instance_id
        agent_id = result.agent_id
        parent_id = result.parent_id

        # Emit SSE for deferred waiting_children (no commit happened)
        if outcome in ("deferred_waiting_children",):
            if self._manager._live_hub:
                try:
                    await self._manager._live_hub.stream_status_change(
                        instance_id, "waiting_children", agent_id=agent_id
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to emit status_change for waiting_children: {e}"
                    )
            return

        # Root waiting_children: commit + SSE
        if outcome == "root_waiting_children":
            if self._manager._live_hub:
                try:
                    await self._manager._live_hub.stream_status_change(
                        instance_id, "waiting_children", agent_id=agent_id
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to emit status_change for waiting_children: {e}"
                    )
            return

        # Child still running defer (Fix 1, Wanderer active-children guard):
        # non-root instance has non-terminal children → no commit, no
        # report, no events fired. Same dispatch shape as
        # ``deferred_waiting_children`` (SSE only) so the UI can reflect
        # the wait state without emitting a duplicate
        # ``child_completed`` lifecycle event to the parent.
        if outcome == "child_still_running_defer":
            if self._manager._live_hub:
                try:
                    await self._manager._live_hub.stream_status_change(
                        instance_id, "waiting_children", agent_id=agent_id
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to emit status_change for "
                        f"child_still_running_defer: {e}"
                    )
            return

        # Phase 5: "root_skipped_terminal_job" outcome removed — guard is gone

        # Root completed: commit + SSE + CompletionRegistry + lifecycle + title
        if outcome == "root_completed":
            if self._manager._live_hub:
                try:
                    await self._manager._live_hub.stream_status_change(
                        instance_id, "completed", agent_id=agent_id
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to emit status_change for completed root instance: {e}"
                    )
            from .completion_registry import get_completion_registry
            get_completion_registry().complete(instance_id, result=last_content)
            if self._events_service:
                try:
                    await self._events_service._publish_instance_lifecycle_event(
                        instance_id=instance_id,
                        status="completed",
                        error=None,
                        parent_id=None,
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to publish lifecycle event for root {instance_id[:8]}...: {e}"
                    )
            self._trigger_title_generation(instance_id, completed_message_id)
            return

        # Idempotency skip: nothing to do
        if outcome == "idempotency_skip":
            return

        # Tool invocation completed: commit + SSE + CompletionRegistry + lifecycle + title
        if outcome == "tool_invocation_completed":
            if self._manager._live_hub:
                try:
                    await self._manager._live_hub.stream_status_change(
                        instance_id, "completed", agent_id=agent_id
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to emit status_change for completed tool invocation: {e}"
                    )
            from .completion_registry import get_completion_registry
            get_completion_registry().complete(instance_id, result=last_content)
            if self._events_service:
                try:
                    await self._events_service._publish_instance_lifecycle_event(
                        instance_id=instance_id,
                        status="completed",
                        error=None,
                        parent_id=parent_id,
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to publish lifecycle event for tool invocation {instance_id[:8]}...: {e}"
                    )
            self._trigger_title_generation(instance_id, completed_message_id)
            return

        # Regular child completed: commit + bus hook + SSE + CompletionRegistry + lifecycle + title
        if outcome == "regular_child_completed":
            # Bus terminal hook: fires AFTER the commit so the DB state
            # is consistent before the bus's ``emit_terminal`` runs its
            # own transaction. Mirrors the inline branch above
            # (``_update_parent_on_child_complete``) — both call sites
            # converge on ``_emit_terminal_via_bus`` to keep the bus
            # wiring in one place.
            #
            # Phase 5: CM is removed. Bus is the SOLE completion
            # authority. We always route through the bus at completion
            # time; the helper handles the no-watchers case as a
            # no-op.
            if completed_message_id:
                # Look up the child task id from the message_id — the
                # bus is keyed on task id, not message_id. The lookup
                # runs on a worker thread (sync DB call) so it doesn't
                # block the event loop. When the task row is missing
                # (e.g. cleared by a stale-task sweep before this
                # completion was reported), ``_emit_terminal_via_bus``
                # logs and returns an empty list — no FollowUps to
                # enqueue, no harm done.
                _child_task_post = None
                _task_repo_post = getattr(self._manager, "_task_repo", None)
                if _task_repo_post is not None:
                    _child_task_post = await asyncio.to_thread(
                        _task_repo_post.get_by_message, completed_message_id
                    )
                await self._emit_terminal_via_bus(
                    task_id=getattr(_child_task_post, "id", None),
                    status="completed",
                    summary="regular child completed",
                )

            # ─── Corrective emit for multi-turn children ───────────────
            # ``_emit_terminal_via_bus`` keys watchers on the CHILD
            # TASK id of the current graph turn. That matches
            # watchers whose source_task_id is the just-completed
            # task — the single-turn case. It MISSES watchers whose
            # source_task_id is the child's FIRST ``process_message``
            # task (registered by ``send_message`` when the parent
            # first sent the child a message), but the child reached
            # its terminal graph turn on a LATER ``PROCESS_REPORT``
            # task. This happens to multi-turn non-root children
            # like Wanderer: parent → wanderer (task T0 registers
            # the watcher), then wanderer → spawns coders → coder
            # → wanderer re-runs on PROCESS_REPORT tasks T1, T2, …
            # until finally wanderer emits its own completion_report
            # to its parent from task TN.
            #
            # Without this corrective emit the parent's PENDING
            # watcher keyed on T0 never fires — the parent stays
            # PENDING forever and wedges in ``waiting_children``.
            # The corrective emit matches the watcher on the
            # (parent, child) instance pair (via the
            # ``follow_up_payload.metadata.child_id`` field that
            # ``send_message`` stamps on every watcher), so it
            # fires regardless of which task id was the terminal
            # one. ``transition_state``'s guarded ``WHERE state =
            # 'PENDING'`` Core UPDATE enforces exactly-once: when
            # the task-keyed emit above already fired the watcher
            # (single-turn case), this is a safe no-op.
            await self._emit_terminal_for_child_instance_via_bus(
                parent_instance_id=parent_id,
                child_instance_id=instance_id,
                status="completed",
                summary="regular child completed (corrective multi-turn emit)",
            )

            # Phase 1 (2026-06-24, report-lane decoupling): Wake the
            # worker pool after the report Task is committed. Before
            # this, the report Task sat PENDING until the worker pool's
            # 3-second poll noticed it — that delay meant the parent's
            # next graph turn could lag noticeably behind the child's
            # completion. The worker pool's ``notify_work()`` is
            # thread-safe and best-effort: a missing pool is tolerated
            # (tests may build a bare InstanceManager without one).
            worker_pool = getattr(self._manager, "_worker_pool", None)
            if worker_pool is not None:
                try:
                    worker_pool.notify_work()
                except Exception as notify_err:
                    logger.warning(
                        f"child_reports: worker_pool.notify_work() "
                        f"failed for report Task (non-fatal): {notify_err}"
                    )

            # Fast-path hint for the report-injection drain: mark this
            # parent as having a pending report so the parent's next
            # live LLM call knows to hit the DB drain. Post-commit (the
            # report_injections row is already committed in the same tx
            # as the message+task above). Best-effort — runs on the
            # event loop, serialized against the drain's set check, so
            # the worst case of a missed bump is a one-LLM-call delivery
            # delay, never a lost report.
            pending_set = getattr(self._manager, "_report_injection_pending", None)
            if pending_set is not None and parent_id:
                pending_set.add(parent_id)

            # CompletionRegistry
            from .completion_registry import get_completion_registry
            get_completion_registry().complete(instance_id, result=last_content)
            
            # SSE for child completed
            if self._manager._live_hub:
                try:
                    await self._manager._live_hub.stream_status_change(
                        instance_id, "completed", agent_id=result.child_agent_id
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to emit status_change for completed instance: {e}"
                    )
            
            # Broadcast child completion event
            try:
                await self._manager._live_hub.stream_lifecycle(
                    instance_id=parent_id,
                    event_type="child_completed",
                    data={
                        "child_instance_id": instance_id,
                        "report_message_id": result.report_message_id,
                    },
                )
            except Exception as e:
                logger.warning(
                    f"Failed to broadcast child completion event: {e}"
                )
            
            # If parent completed (all children done)
            if result.completed_parent_id:
                if self._manager._live_hub:
                    try:
                        await self._manager._live_hub.stream_status_change(
                            result.completed_parent_id, "completed", agent_id=result.parent_agent_id
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to emit status_change for completed parent: {e}"
                        )
                if self._events_service:
                    try:
                        await self._events_service._publish_instance_lifecycle_event(
                            instance_id=result.completed_parent_id,
                            status="completed",
                            error=None,
                            parent_id=result.completed_parent_parent_id,
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to publish lifecycle event for completed parent "
                            f"{result.completed_parent_id[:8]}...: {e}"
                        )

            # Emit SSE for parent WAITING_CHILDREN (CM-disabled legacy path
            # with pending own-queue messages — was inside the WriteGuardSession
            # at line 624-627 in the pre-refactor inline block).
            if result.parent_waiting_children_sse and result.parent_id:
                if self._manager._live_hub:
                    try:
                        await self._manager._live_hub.stream_status_change(
                            result.parent_id, "waiting_children",
                            agent_id=result.waiting_children_parent_agent_id,
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to emit status_change for waiting_children parent: {e}"
                        )
            
            # Title generation
            self._trigger_title_generation(instance_id, completed_message_id)
            return

        # instance_not_found or unknown outcome: nothing to do
        if outcome in ("instance_not_found",):
            return

        logger.warning(
            f"Unknown child completion outcome '{outcome}' for "
            f"instance {instance_id[:8]}..."
        )
