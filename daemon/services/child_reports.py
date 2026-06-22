"""Child reports service for handling child instance completion reports."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import func, select, text
from sqlmodel import Session

from ..graph import ThinkingChatOpenAI, clean_llm_config
from ..persistence import get_instance_messages
from ..repositories.instance.models import Instance, InstanceStatus
from ..repositories.message_queue.models import MessageQueue, MessageStatus, MessageType
from ..repositories.job_queue.models import JobItem, JobStatus
from ..repositories.task.models import Task, TaskType, TaskStatus
from ..repositories.event.models import Event, EventKind
from ..repositories.dependency_bus.models import DependencyWatcher, DependencyWatcherState
from ..registry import get_registry
from ..write_pause_guard import WriteGuardSession
from .main_loop_bridge import MainLoopBridge

if TYPE_CHECKING:
    from ..config import Config
    from ..repositories.instance.repository import SQLModelInstanceRepository
    from .event_publisher import EventPublisherService
    from .error_reporting import ErrorReportingService


logger = logging.getLogger(__name__)


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
        ``"deferred_waiting_children"`` — root has pending children (CM or
            ``waiting_for``), SSE ``waiting_children`` only, no commit.
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

    def _is_dependency_bus_enabled(self) -> bool:
        """Read the ``use_dependency_bus`` flag from the manager config.

        Defensive ``getattr`` chain mirrors
        ``JobProcessor._is_legacy_jobqueue_dispatch_enabled`` and the
        sibling helper in ``daemon/tools/instance.py`` so test mocks
        that bypass ``InstanceManager.__init__`` (e.g. ``MagicMock()``
        without explicit ``config``) don't crash. The default is False
        (Phase D feature flag OFF = legacy CM path is active), matching
        the config field's default.

        Returns:
            True if the operator has enabled the DB-backed DependencyBus
            completion-delivery path; False otherwise.
        """
        _config = getattr(self._manager, "config", None)
        _job_system = getattr(_config, "job_system", None)
        return bool(
            getattr(_job_system, "use_dependency_bus", False)
        )

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
        session bus gate inside ``WriteGuardSession``, a parent
        ``cm.is_complete`` check, etc.). The **authoritative bus
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
        impossible there) to consult the bus under
        ``use_dependency_bus=ON``. When the bus flag is ON, the CM's
        in-memory pending set is starved (send_message skips
        ``cm.register_message_send``), so the bus DB is the
        authoritative source of pending-children truth. Without this
        check, the root-instance completion gate falls through to
        COMPLETED prematurely while children are still running —
        the exact bug Phase D was designed to prevent.

        Graceful-degradation semantics (matches the
        ``get_correlation_manager() is not None`` guards elsewhere):
        bus singleton missing or flag OFF → returns 0.

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
            not wired, the flag is OFF, or the DB query fails.
        """
        from .dependency_bus import get_dependency_bus

        bus = get_dependency_bus()
        if bus is None:
            return 0

        # Mirror the W1-fix defensive flag check from the call
        # sites in ``_emit_terminal_via_bus`` callers: the flag
        # must be ON for the bus to be the source of truth. If the
        # operator flipped the flag OFF mid-flight, treat the bus
        # as inert (the CM path is the fallback).
        if not self._is_dependency_bus_enabled():
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

        When the ``use_dependency_bus`` flag is ON, this helper replaces
        the ``notify_corr_resolve`` call: it asks the bus to atomically
        transition PENDING watchers for ``task_id`` to FIRED, then
        **directly** re-triggers ``JobFeedbackObserver._finalize_job``
        on any parent whose watchers are all FIRED.

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
            messages — see ``_retrigger_parent_finalize`` below for
            the actual finalization path. Empty list when no watchers
            existed or when the flag is OFF (this helper is a no-op in
            the OFF case — the caller falls through to the CM path).
        """
        from .dependency_bus import (
            Outcome,
            get_dependency_bus,
        )

        bus = get_dependency_bus()
        if bus is None:
            # Bus singleton missing despite flag being ON — treat as a
            # wiring failure and return an empty list (the caller's CM
            # path will still run because we never replace it; the CM
            # is the safety net).
            logger.warning(
                "_emit_terminal_via_bus: bus singleton is None despite "
                "flag=ON — returning empty FollowUp list "
                "(caller should fall back to CM path)"
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

        # ─── Phase D re-trigger (direct finalization) ────────────────
        # After the bus fires watchers, check if each target (parent)
        # has ALL watchers resolved (0 PENDING). If so, directly call
        # ``_finalize_job`` on the JobFeedbackObserver — there is NO
        # message-queue follow-up step. The bus is a state machine
        # that drives an internal finalization transition, not an
        # event source that the LLM consumes.
        #
        # Why this is needed: on the bus path (use_dependency_bus=ON),
        # the CM callback is starved — ``send_message`` skips
        # ``cm.register_message_send``, so the CM never invokes
        # ``handle_correlation_complete`` for wave-2 children. Without
        # this direct re-trigger, the job stays PROCESSING forever
        # (the inverse of the premature-completion bug the bus gate
        # was added to prevent).
        #
        # Guarded by a ``_retriggered`` set so a parent that received
        # multiple FollowUps from one terminal event is re-triggered
        # at most once per call (avoids redundant observer work when
        # N fired FollowUps all target the same parent).
        #
        # **Stamp ordering (crash-recovery invariant, 2026-06-22)**:
        # the ``mark_enqueued_by_source_target`` stamp MUST happen
        # AFTER the finalization attempt (not before). If we stamped
        # first, a crash between stamp and retrigger would leave
        # ``enqueued_at IS NOT NULL`` and a future restart's
        # :meth:`DependencyBus._recover_fired_unsent` would skip the
        # row — the parent would stay PROCESSING forever. Stamping
        # AFTER the finalization attempt is correct: a crash before
        # finalization leaves the row un-stamped → next restart
        # retries finalization → safe because ``_finalize_job`` is
        # idempotent (atomic ``WHERE status = PROCESSING`` UPDATE).
        # This matches the recovery pattern in ``api.py`` which
        # correctly orders retrigger → stamp.
        _retriggered: set[str] = set()
        for fu in fired:
            target_id = fu.target_instance_id
            if target_id in _retriggered:
                continue
            _retriggered.add(target_id)
            try:
                remaining = await bus.count_pending_for_target(target_id)
            except Exception as e:
                logger.warning(
                    f"Bus re-trigger check failed for {target_id[:8]}...: {e}"
                )
                continue
            if remaining == 0:
                logger.info(
                    f"Bus: all watchers fired for {target_id[:8]}... "
                    f"(remaining PENDING=0), re-triggering finalize"
                )
                # Loop-level exception guard: even though
                # ``_retrigger_parent_finalize`` now has internal
                # try/except around its two DB calls, a defensive
                # outer guard here ensures a bug or unexpected
                # error path inside the helper cannot abort the
                # iteration and skip re-triggering the remaining
                # targets.
                try:
                    await self._retrigger_parent_finalize(target_id)
                except Exception as retrigger_err:
                    logger.warning(
                        f"Bus re-trigger: unexpected error for "
                        f"{target_id[:8]}...: {retrigger_err}"
                    )

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

    async def _retrigger_parent_finalize(self, instance_id: str) -> None:
        """Re-trigger job finalization for a parent after all bus watchers fired.

        On the bus path (``use_dependency_bus=ON``), the CM callback never
        re-fires because ``send_message`` skips ``cm.register_message_send``.
        After the bus fires the last watcher, we must explicitly re-attempt
        job finalization via the :class:`JobFeedbackObserver` so the job
        transitions PROCESSING → COMPLETED and locks are released.

        Safety properties:
          * **No deadlock**: ``_emit_terminal_via_bus`` runs in post-commit
            dispatch (after ``WriteGuardSession`` closed). The observer's
            ``_finalize_job`` acquires the CM per-parent lock and opens its
            own ``WriteGuardSession``. No shared transaction, no nested locks.
          * **Idempotent**: ``_finalize_job_db_sync`` uses an atomic
            ``WHERE status = PROCESSING`` guard. If the job was already
            finalized by another path, ``rowcount=0`` and the helper
            returns ``skip=True`` — no duplicate side effects.
          * **Defense-in-depth**: ``getattr(manager, "_job_feedback_observer",
            None)`` lets the helper degrade gracefully when the observer
            has not been wired (unit tests, partial init). A missing
            observer is logged at DEBUG, not WARN, because it is the
            expected state for tests.

        Args:
            instance_id: The parent instance whose job may need to be
                finalized. Resolved to its PROCESSING ``JobItem`` via
                ``JobFeedbackObserver._get_processing_job_for_instance``.
        """
        observer = getattr(self._manager, "_job_feedback_observer", None)
        if observer is None:
            # Expected in unit tests where ChildReportsService is built
            # against a bare MagicMock manager; the bus re-trigger is a
            # no-op there. In production the observer is wired by
            # ``daemon/api.py`` via ``InstanceManager.set_job_feedback_observer``.
            logger.debug(
                f"Bus re-trigger: no observer available for "
                f"{instance_id[:8]}... (skipping — not wired in test?)"
            )
            return

        # Find the PROCESSING job for this instance. Uses the same
        # helper as ``handle_correlation_complete`` (job_feedback_observer
        # line 551) so the stale-job defense-in-depth re-query is applied
        # uniformly across both re-trigger paths.
        #
        # Exception-safety: the helper opens its own ``WriteGuardSession``
        # and runs sync DB queries on a worker thread. A transient DB
        # failure here would propagate out of ``_retrigger_parent_finalize``
        # and break the retrigger loop in ``_emit_terminal_via_bus`` for
        # all subsequent targets. Wrap so a single failure logs and
        # returns cleanly — the next child completion (or a manual
        # recovery sweep) can retry the finalization.
        try:
            job = await observer._get_processing_job_for_instance(instance_id)
        except Exception as e:
            logger.warning(
                f"Bus re-trigger: job lookup failed for "
                f"{instance_id[:8]}...: {e}"
            )
            return
        if job is None:
            # The job is already terminal or never existed — finalization
            # is either already complete or irrelevant. Silent skip
            # matches the behaviour of the CM callback when no
            # PROCESSING job exists (see ``handle_correlation_complete``
            # line 552-557).
            logger.debug(
                f"Bus re-trigger: no PROCESSING job for "
                f"{instance_id[:8]}..., may already be finalized"
            )
            return

        logger.info(
            f"Bus re-trigger: calling _finalize_job for {instance_id[:8]}... "
            f"(job={job.job_id[:8]}...)"
        )
        try:
            # _finalize_job re-checks ALL gates (bus=0, CM empty,
            # waiting_for=0) before transitioning. On the bus path the
            # bus gate passes (0 PENDING), the CM gate passes (empty
            # slot, is_complete=True — bus-path children never
            # registered), and waiting_for passes (either decremented
            # to 0 by ``_update_parent_on_child_complete`` or already 0
            # when the bus flag is ON). Result: PROCESSING → COMPLETED,
            # instance → COMPLETED, locks released.
            await observer._finalize_job(
                job,
                instance_id,
                InstanceStatus.COMPLETED.value,
                error=None,
            )
        except Exception as e:
            # Defensive guard: a single re-trigger failure must not
            # crash the bus finalization path. The bus no longer
            # enqueues FollowUp messages onto the parent's queue —
            # the bus is a pure state machine that drives a direct
            # ``_finalize_job`` call (see ``_emit_terminal_via_bus``
            # docstring). The next child completion (or a manual
            # recovery sweep) can retry the finalization; the
            # ``_finalize_job`` itself is idempotent.
            logger.warning(
                f"Bus re-trigger: _finalize_job failed for "
                f"{instance_id[:8]}...: {e}"
            )

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

    def _has_no_active_message_job(self, session, instance_id: str) -> bool:
        """Check if there is NO active (PENDING/PROCESSING) MESSAGE job for an instance.

        Used as a defense-in-depth guard before writing WAITING_CHILDREN status.
        If no active job exists, pending messages are likely stale/duplicate
        (from a task-claim race) and writing WAITING_CHILDREN would permanently
        strand the instance — there is no code path that transitions out of
        WAITING_CHILDREN other than a new message arriving on a fresh
        MESSAGE job, which is exactly what is missing here.

        NOTE: MESSAGE jobs are NOT 1:1-per-instance — each user message creates
        a new job (see ``instance_messaging.enqueue_message_job``). We check for
        ACTIVE jobs (PENDING/PROCESSING), not terminal jobs, to avoid false
        positives when a completed old job coexists with an active new job.

        Args:
            session: Active SQLModel session (DB read happens here, not committed).
            instance_id: The instance to check.

        Returns:
            True iff there is no active MESSAGE job (PENDING or PROCESSING) for
            this instance. False when at least one active job exists.
        """
        _no_active = session.exec(
            select(func.count())
            .select_from(JobItem)
            .where(JobItem.instance_id == instance_id)
            .where(JobItem.job_type == "message")
            .where(JobItem.deleted_at.is_(None))
            .where(JobItem.status.in_([
                JobStatus.PENDING.value,
                JobStatus.PROCESSING.value,
            ]))
        ).scalar_one() == 0
        return _no_active

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

    async def _update_parent_on_child_complete(self, session, instance, completed_message_id: str | None = None) -> tuple[bool, str | None, str | None]:
        """Update parent state when child completes.
        
        Handles:
        - Decrement parent's waiting_for counter
        - Update parent's children cache (FIX: W6)
        - Delete from instance_hierarchy table
        - Cascade: transition parent based on waiting_for and status
        
        Args:
            session: Database session.
            instance: The child Instance object.
            completed_message_id: The message ID that just completed (for CM hook).
            
        Returns:
            Tuple of (transitioned_to_running, completed_parent_id, completed_parent_parent_id):
            - transitioned_to_running: True if parent transitioned to RUNNING (has more work)
            - completed_parent_id: Instance ID if parent completed (for event publishing), None otherwise
            - completed_parent_parent_id: Parent's parent_id if parent completed, None otherwise
        """
        parent = session.get(Instance, instance.parent_id)
        if not parent:
            return False, None, None

        # ─── Phase A: USE_LEGACY_WAITING_FOR_CASCADE kill switch ──────────
        # When the flag is OFF (default), the CorrelationManager is the SOLE
        # completion authority and ``waiting_for`` is no longer decremented
        # (the CM tracks the in-memory pending set). When the flag is ON, we
        # run the legacy M0 path unchanged — the SQL decrement + cascade
        # decision + ``WAITING_CHILDREN`` write. The flag exists for the
        # rollback path documented in ``docs/configuration/completion-flags.md``
        # and tested by the A14 kill-switch pack; it is NOT a safe revert.
        # See ADR-011 and the execution plan (decouple-execution-plan.md §A4).
        use_legacy_cascade = bool(
            self._config.job_system.use_legacy_waiting_for_cascade
        )

        if use_legacy_cascade:
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
        else:
            # Flag OFF: do NOT decrement the DB ``waiting_for`` column.
            # CorrelationManager tracks the in-memory pending set; the
            # ``waiting_for`` column is the rebuild cache (ADR-011) and
            # must stay in sync with the CM. Decrementing it here would
            # create divergence between the two authorities — which is
            # exactly the bug class the flag is designed to prevent.
            logger.debug(
                f"USE_LEGACY_WAITING_FOR_CASCADE=OFF: skipping waiting_for SQL "
                f"decrement for parent={parent.instance_id[:8] if parent else '?'}..., "
                f"child={instance.instance_id[:8]}... (CM is authoritative)"
            )

        # AUTHORITATIVE RESOLUTION HOOK (CorrelationManager Phase 3):
        # the CM is the single source of truth for parent completion. This
        # ``notify_corr_resolve`` call decrements the CM's per-parent pending
        # set; if that drops to zero, the CM synchronously fires
        # ``handle_correlation_complete`` (registered as
        # ``completion_callback``), which transitions the parent JOB to
        # terminal and (Phase 3) the parent INSTANCE to terminal. The
        # CM's in-memory pending set is authoritative — there is no
        # ``SELECT COUNT(*)`` fallback, no TOCTOU window (Race #1 / #3
        # eliminated).
        #
        # MUST NOT affect control flow — wrapped in try/except inside
        # ``notify_corr_resolve``. The inline cascade below this hook is
        # only reached when ``get_correlation_manager()`` returns ``None``
        # (graceful degradation / CM disabled).
        #
        # Calling context: this method is called from
        # _process_child_completion_and_notify_parent, which is invoked
        # by MessageJobHandler.process via TaskProcessor.run_task using
        # MainLoopBridge.run_async — so we are on the main asyncio event
        # loop. The CM's per-parent lock is bound to the main loop (N3
        # constraint); a direct await is safe here.
        #
        # Skip the hook when message_id is missing/empty: the CM keys
        # correlations on (child_id, message_id) and cannot resolve a
        # None/empty message_id against any registered entry. Calling
        # with message_id="" would silently no-op and the pending entry
        # would stay forever.
        #
        # Phase D (DependencyBus): when ``use_dependency_bus=ON``, the
        # CM ``notify_corr_resolve`` call is SKIPPED and replaced by
        # ``bus.emit_terminal(...)`` keyed on the child task id. The
        # two authorities are mutually exclusive — never called in
        # parallel — to prevent double-fire (Phase A lesson: the
        # double-decrement bug class).
        if completed_message_id:
            # W1 fix (2026-06-21): check the flag AND the bus singleton.
            # When the flag is ON but the bus singleton is None (wiring
            # failure, lazy init race, or unit-test mock), falling
            # through to the CM path is the safe default — the bus path
            # would otherwise silently drop the FollowUp because
            # ``_emit_terminal_via_bus`` returns ``[]`` on bus=None.
            # Checking both at the call site makes the CM the
            # graceful-degradation fallback, matching the project's
            # pattern for ``get_correlation_manager() is not None``
            # guards elsewhere.
            from .dependency_bus import get_dependency_bus
            use_bus_path = (
                self._is_dependency_bus_enabled()
                and get_dependency_bus() is not None
            )
            if use_bus_path:
                # ─── Phase D: DependencyBus path (replaces CM) ───────
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
                try:
                    from .correlation_manager import notify_corr_resolve
                    await notify_corr_resolve(
                        parent_id=instance.parent_id,
                        child_id=instance.instance_id,
                        message_id=completed_message_id,
                        status="responded",
                    )
                except Exception as hook_err:
                    # Defensive outer guard — the helper already swallows CM
                    # errors, but keep this so a failure in the import path or
                    # argument binding can never break the child-completion path.
                    logger.warning(
                        f"CM hook: resolve path failed "
                        f"(parent={instance.parent_id[:8] if instance.parent_id else '?'}..., "
                        f"child={instance.instance_id[:8]}...): {hook_err}"
                    )
        else:
            logger.debug(
                f"CM hook: skipping resolve for parent="
                f"{instance.parent_id[:8] if instance.parent_id else '?'}..., "
                f"child={instance.instance_id[:8]}... "
                f"(no message_id — child completed without a tracked send)"
            )

        parent.last_activity_at = datetime.now(timezone.utc)
        parent.version = (parent.version or 1) + 1
        
        # NOTE: We no longer mutate ``parent.children`` (JSON cache) here.
        # The ``instance_hierarchy`` junction table is the canonical
        # source of parent-child relationships — _enrich_instance() in
        # daemon/repositories/instance/repository.py loads children
        # from it on every read. Writes to the JSON cache were doubly
        # broken (RMW races + overridden on read) and persistently
        # useless (no code ever reads the corrupted value). See C10.

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
        # W1 FIX: Also preserve ERROR status during cascade — a parent whose last child
        # completed successfully should still report as ERROR (it errored first, and that
        # state is more useful for diagnostics than overwriting it with COMPLETED).
        #
        # Phase 4: the ``parent.waiting_for == 0`` control-flow READ is
        # replaced by ``cm.is_complete(parent_id)`` when the CM is wired up.
        # ``waiting_for`` is retained as the rebuild cache (ADR-011) and the
        # graceful-degradation fallback. The WRITE SQL above is gated by
        # ``USE_LEGACY_WAITING_FOR_CASCADE`` (Phase A) — when OFF, the
        # decrement is skipped and the CM is the only completion authority.
        from .correlation_manager import get_correlation_manager
        cm = get_correlation_manager()
        if cm is not None:
            # CM is wired up — it is the SOLE completion authority. The
            # pending count is read from CM's in-memory set (no DB query,
            # no TOCTOU window — Race #1 / #3 eliminated).
            is_parent_complete = cm.is_complete(parent.instance_id)
        elif use_legacy_cascade:
            # Legacy M0 fallback (kill switch ON): use the ``waiting_for``
            # column directly. Race-prone (Phase 4 docstring) but the
            # behaviour Phase A is rolling back FROM.
            is_parent_complete = (getattr(parent, "waiting_for", None) or 0) == 0
        else:
            # ─── A8: HARD ERROR (not graceful degradation) ─────────────
            # CM is None AND ``USE_LEGACY_WAITING_FOR_CASCADE=OFF`` is an
            # INVALID state. The ``SELECT COUNT(*)`` fallback (Race #3) is
            # the exact bug we are fixing — it MUST NOT be reachable when
            # the kill switch is OFF. CM must be initialized for the new
            # architecture to work; we raise rather than silently degrade
            # into the TOCTOU fallback. See
            # ``docs/configuration/completion-flags.md`` §"Required
            # external precondition" and the decouple execution plan §A8.
            # Flag ON (kill switch) keeps the legacy ``SELECT COUNT(*)``
            # fallback above so the rollback path still works.
            #
            # Honest propagation note: this RuntimeError is caught by the
            # W3 fail-safe (``except Exception``) in
            # ``_finalize_job`` and results in a per-job FAILED transition.
            # It is NOT a process-level crash — the daemon stays alive and
            # only the affected job fails. For production, CM must be
            # initialized before any traffic (the startup invariant
            # enforced in ``daemon/main.py`` via ``cm.start()``).
            raise RuntimeError(
                f"USE_LEGACY_WAITING_FOR_CASCADE=OFF but CorrelationManager "
                f"is not initialized for parent={parent.instance_id[:8]}...; "
                f"CM must be initialized when the legacy cascade is disabled. "
                f"This is a hard error — the SELECT COUNT(*) TOCTOU fallback "
                f"(Race #3) is disabled by design."
            )
        if (
            is_parent_complete
            and parent.status != InstanceStatus.COMPLETED.value
            and parent.status != InstanceStatus.ERROR.value
        ):
            # Phase 3 (Cascade Unification): when CM is active, the inline
            # cascade + SELECT COUNT(*) + inline status transition are
            # SKIPPED. The CM's resolve_response (called via the shadow
            # hook above) already removed the entry from its in-memory
            # pending set, and if that was the last correlation the CM
            # callback ``handle_correlation_complete`` is fired
            # synchronously — which transitions the parent JOB to terminal
            # via ``_finalize_job``. The CM's in-memory set is the source
            # of truth (no DB query, no TOCTOU window — Race #3 eliminated).
            #
            # When CM is None (graceful degradation), keep the existing
            # logic with the SELECT COUNT(*) fallback. This path is also
            # the one exercised by every test that does not wire a CM
            # fixture (e.g. tests/job_queue/test_in_progress_guard.py).
            if cm is not None:
                # CM is active — CM callback handles completion.
                # No count_pending query, no inline status transition,
                # no inline lifecycle event (the caller at line ~914-931
                # is also skipped because we return completed_parent_id=None).
                logger.info(
                    f"CM-active: skipping inline cascade for parent "
                    f"{parent.instance_id[:8]}... — CM callback owns completion"
                )
                return False, None, None

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
                #
                # Phase 4: WAITING_CHILDREN is DEPRECATED as a control-flow
                # signal — CM is the authoritative source of correlation
                # state. The status set is RETAINED for graceful-degradation
                # (CM is None) and for the FIFO carve-out SQL compatibility
                # (daemon/repositories/task/repository.py). The
                # ``transitioned_to_running`` return value remains ``True``
                # because the parent is still alive (will process more
                # messages) — contract required by
                # ``tests/test_cascade_integration.py``.
                #
                # Defense-in-depth (F8): if there is NO active MESSAGE job
                # for this parent, the parent_pending count is a false
                # positive (stale/duplicate from task-claim race). Skip
                # the WAITING_CHILDREN write to avoid permanently
                # stranding the parent — fall through to ``return False,
                # None, None`` so no transition is recorded.
                if self._has_no_active_message_job(session, parent.instance_id):
                    logger.warning(
                        f"Parent {parent.instance_id[:8]}... has {parent_pending} pending "
                        f"messages but no active MESSAGE job — skipping WAITING_CHILDREN "
                        f"write (stale/duplicate messages from task-claim race)"
                    )
                    return False, None, None
                parent.status = InstanceStatus.WAITING_CHILDREN.value
                logger.info(
                    f"Parent {parent.instance_id[:8]}... all children done but has {parent_pending} "
                    f"pending messages, status=WAITING_CHILDREN (deprecated; CM is authoritative)"
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

        # MAJOR A fix (re-arm safety net, 2026-06-22): wrap the
        # ``asyncio.to_thread`` call in the CorrelationManager's
        # per-parent ``asyncio.Lock`` when CM is wired. The lock is
        # held on the EVENT LOOP for the entire duration of the
        # worker-thread sync helper, blocking ``bus.watch()`` (which
        # also acquires ``cm._get_lock(parent_id)`` after the C1 fix
        # in ``dependency_bus.py``) from running on the loop and
        # committing a new watcher row between the in-session bus
        # gate inside the sync helper and the terminal status
        # UPDATE that follows it.
        #
        # Why this works (no deadlock risk):
        #   * ``asyncio.Lock`` is event-loop-bound — the worker
        #     thread that ``asyncio.to_thread`` runs in NEVER
        #     acquires it; it only runs the sync SQLAlchemy code
        #     while the GIL is released during I/O.
        #   * The lock serializes coroutines on the loop, not
        #     threads.
        #   * ``bus.watch()`` acquires CM lock (parent) → bus task
        #     lock. We acquire CM lock only here. No cycle exists.
        #   * WriteGuardSession is a Python-level counter, not a DB
        #     lock — no interaction.
        #
        # When CM is None (legacy path / not initialized), no lock
        # is acquired — the legacy ``waiting_for`` cascade is the
        # only authority and there is no concurrent writer to race
        # against on the in-memory CM pending set.
        from .correlation_manager import get_correlation_manager as _get_cm_for_a_fix
        cm = _get_cm_for_a_fix()
        if cm is not None:
            async with cm._get_lock(instance_id):
                # Run the ENTIRE WriteGuardSession block on a
                # worker thread so session.commit() cannot deadlock
                # the event loop. The CM lock is held on the
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

    def _process_child_completion_db_sync(
        self,
        instance_id: str,
        completed_message_id: str,
        last_content: str,
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
            
            logger.info(f"Instance {instance_id[:8]}... parent_id={instance.parent_id}, waiting_for={instance.waiting_for}, status={instance.status}")
            
            # Not a child? Instance completed (no parent to send report to)
            # Check if we have active children - if so, wait for them before completing.
            #
            # Phase 4: the ``waiting_for > 0`` READ for the deferral decision
            # is replaced by ``cm.get_pending_count()`` when the
            # CorrelationManager is wired up. ``waiting_for`` is retained as
            # the rebuild cache (ADR-011) and as the graceful-degradation
            # fallback.
            if instance.parent_id is None:
                from .correlation_manager import get_correlation_manager
                cm = get_correlation_manager()
                if cm is not None:
                    pending_children = cm.get_pending_count(instance_id)
                else:
                    # Legacy fallback — ``waiting_for`` column.
                    pending_children = getattr(instance, "waiting_for", None) or 0
                if pending_children > 0:
                    # Has children still running — defer completion.
                    # Phase 4: do NOT transition status to WAITING_CHILDREN;
                    # the CM is the authoritative source of pending children
                    # and instances stay PROCESSING while children run.
                    # The ``waiting_children`` SSE event is kept for watcher
                    # compatibility (display only).
                    logger.info(
                        f"Instance {instance_id[:8]}... completed message but waiting for "
                        f"{pending_children} children (CM={cm is not None}), deferring completion"
                    )
                    # SSE side effect is dispatched by the async caller.
                    return _ChildCompletionDbResult(
                        outcome="deferred_waiting_children",
                        instance_id=instance_id,
                        agent_id=instance.agent_id,
                        parent_id=None,
                    )

                # Phase 3 (Cascade Unification — Fix A2): root completion
                # is NOT a child response, so we MUST NOT call
                # ``cm.resolve_response`` here (a self-referential
                # ``(instance_id, message_id)`` key would never match any
                # registered correlation and would silently no-op).
                # Instead, we use ``cm.is_complete`` as a read-only check
                # (Condition 1): are all child responses received?
                # The CM's in-memory pending set is the authoritative
                # source — if CM says children are still pending but
                # ``waiting_for`` happened to read 0 (e.g. a
                # register/resolve interleaving with our session
                # snapshot), trust CM. When CM is None (graceful
                # degradation), skip Condition 1 and fall through to
                # Condition 2 (the existing pending_count query).
                #
                # Note: ``cm`` is reused from the earlier lookup above
                # (it remains in scope after the ``return`` guard at
                # the end of the previous block).
                if cm is not None:
                    all_children_done = cm.is_complete(instance_id)
                    if not all_children_done:
                        # CM still has pending correlations for this
                        # root. Trust CM (more accurate than the DB
                        # ``waiting_for`` snapshot — Race #3 adjacent
                        # window).
                        #
                        # Phase 4: do NOT set status to WAITING_CHILDREN
                        # — instances stay PROCESSING while children run.
                        # The ``waiting_children`` SSE event is kept below
                        # for watcher compatibility (display only).
                        logger.info(
                            f"Instance {instance_id[:8]}... waiting_for=0 but CM has "
                            f"unresolved child responses, status=PROCESSING (CM tracks pending)"
                        )
                        # SSE side effect is dispatched by the async caller.
                        return _ChildCompletionDbResult(
                            outcome="deferred_waiting_children",
                            instance_id=instance_id,
                            agent_id=instance.agent_id,
                            parent_id=None,
                        )

                # ─── Phase D bus gate (premature-completion fix) ───────────
                # When ``use_dependency_bus=ON``, the CM is starved
                # (``send_message`` skips ``cm.register_message_send``),
                # so the two checks above (CM pending count + CM
                # is_complete) both pass with "complete" even while
                # children tracked via the bus are still running. The
                # bus DB is the authoritative source of pending-
                # children truth on the bus path — we MUST consult it
                # here, before falling through to COMPLETED, or the
                # root instance will be marked COMPLETED while a child
                # is still working (the exact premature-completion
                # bug Phase D was designed to prevent).
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
                # BOTH the flag is ON AND the bus singleton is
                # wired. When the singleton is None (testing, missing
                # init, config drift), the gate is dormant — same
                # semantics as the original
                # ``_bus_count_pending_for_target_sync`` helper
                # before the C2 inline refactor. Without this guard,
                # a test or config that leaves the flag ON without
                # wiring the bus singleton would still execute the
                # inline COUNT against an empty table — usually
                # harmless (returns 0), but in degraded states
                # (mock MagicMock truthiness, partial migrations) it
                # could defer a completion that should proceed.
                if self._is_dependency_bus_enabled():
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
                            # F8 carve-out (2026-06-22): if there is
                            # NO active MESSAGE job (PENDING/PROCESSING)
                            # for this instance, the pending-children
                            # signal is likely stale/duplicate from a
                            # task-claim race. Writing WAITING_CHILDREN
                            # here would permanently strand the
                            # instance — no code path transitions out
                            # of WAITING_CHILDREN other than a new
                            # message arriving on a fresh MESSAGE job,
                            # which is exactly what is missing. In
                            # that case, return a
                            # ``root_skipped_terminal_job`` result
                            # (preserves status, signals
                            # CompletionRegistry) instead of writing
                            # the status. Mirrors the same guard in
                            # the regular ``root_waiting_children``
                            # path below — bus path must be
                            # consistent with the non-bus path.
                            if self._has_no_active_message_job(session, instance_id):
                                logger.warning(
                                    f"Instance {instance_id[:8]}... has "
                                    f"{bus_pending} bus PENDING watchers "
                                    f"(use_dependency_bus=ON) but no active "
                                    f"MESSAGE job — skipping WAITING_CHILDREN "
                                    f"write (stale/duplicate from task-claim "
                                    f"race). Status preserved as "
                                    f"{instance.status}."
                                )
                                return _ChildCompletionDbResult(
                                    outcome="root_skipped_terminal_job",
                                    instance_id=instance_id,
                                    agent_id=instance.agent_id,
                                    parent_id=None,
                                )

                            # Bug fix (2026-06-22): transition
                            # ``instance.status`` to ``WAITING_CHILDREN``
                            # so the frontend reflects the "leader is
                            # waiting for children" state on the bus
                            # path. Previously the status stayed at
                            # whatever it was (typically ``running``)
                            # so the UI showed ``running`` even though
                            # no LLM call was in flight. The
                            # F8 carve-out above protects against
                            # stale/duplicate signals from a
                            # task-claim race.
                            instance.status = InstanceStatus.WAITING_CHILDREN.value
                            instance.updated_at = datetime.now(timezone.utc).isoformat()
                            instance.version = (instance.version or 1) + 1
                            session.commit()
                            logger.info(
                                f"Instance {instance_id[:8]}... CM says "
                                f"complete but bus has {bus_pending} "
                                f"PENDING watchers "
                                f"(use_dependency_bus=ON), "
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

                # waiting_for == 0, but check for pending messages before completing.
                # This handles the case where child completion reports are still queued
                # but waiting_for was already decremented by a previous cascade.
                # Exclude the just-completed message by ID (mirrors
                # _should_send_completion_report at line 270-279) to avoid the
                # double-count hazard when message_queue.complete() has not
                # committed yet.
                #
                # Phase 3: this ``SELECT COUNT(*)`` is RETAINED. It checks
                # a different concern (root's OWN queue pending work —
                # messages from external sources like HTTP, scheduler),
                # not child-response correlation. The CM set does not
                # track these. Per ADR-012 and the plan, this is NOT
                # subject to Race #3.
                #
                # Phase 4: the ``WAITING_CHILDREN`` set here is DEPRECATED
                # and retained only for graceful degradation. CM is the
                # authoritative source of correlation state. Display/log.
                pending_count = session.exec(
                    select(func.count())
                    .select_from(MessageQueue)
                    .where(MessageQueue.instance_id == instance_id)
                    .where(MessageQueue.message_id != completed_message_id)
                    .where(MessageQueue.status.in_([
                        MessageStatus.READY.value,
                        MessageStatus.PROCESSING.value,
                        MessageStatus.RETRYING.value,
                    ]))
                ).scalar_one()

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
                # at line ~565) are gated by ``cm.is_complete()`` — when CM is
                # active, they stay PROCESSING because the CM tracks their
                # pending children. They never reach the SELECT COUNT branch
                # for their own queue because the CM-active bypass returns early
                # at line ~574.
                #
                # The root carve-out is intentional and aligned with Site 1B
                # (ADR-012) two-condition check: root completion requires BOTH
                # no pending children AND no own-queue messages.
                if pending_count > 0:
                    # By this point waiting_for is guaranteed 0 (the early
                    # return above handled all waiting_for > 0 cases), so the
                    # single pending_count guard is sufficient. Do NOT
                    # transition to COMPLETED — there is queued work that
                    # the worker must still process.
                    #
                    # Display/log: ``waiting_for=0`` is a snapshot read
                    # of the rebuild cache. CM is authoritative. The
                    # WAITING_CHILDREN status set is retained for
                    # graceful-degradation watchers and FIFO carve-out
                    # SQL compatibility.

                    # Defense-in-depth: if there is NO active MESSAGE job
                    # (PENDING/PROCESSING) for this instance, the
                    # pending_count is a false positive — stale/duplicate
                    # messages from a task-claim race (see
                    # daemon/repositories/task/repository.py claim race).
                    # Do NOT write WAITING_CHILDREN or the instance gets
                    # permanently stuck (no code path clears it back).
                    #
                    # NOTE: MESSAGE jobs are NOT 1:1-per-instance — each
                    # user message creates a new job. We check for ACTIVE
                    # jobs, not terminal jobs, to avoid false positives
                    # when a completed old job coexists with an active
                    # new job.
                    if self._has_no_active_message_job(session, instance_id):
                        logger.warning(
                            f"Instance {instance_id[:8]}... has pending_count={pending_count} "
                            f"but no active MESSAGE job — skipping WAITING_CHILDREN "
                            f"write (stale/duplicate messages from task-claim race)"
                        )
                        # Do NOT set status to WAITING_CHILDREN. Leave the
                        # instance status as-is (current status preserved).
                        return _ChildCompletionDbResult(
                            outcome="root_skipped_terminal_job",
                            instance_id=instance_id,
                            agent_id=instance.agent_id,
                            parent_id=None,
                        )

                    instance.status = InstanceStatus.WAITING_CHILDREN.value
                    session.commit()
                    logger.info(
                        f"Instance {instance_id[:8]}... waiting_for=0 (rebuild-cache snapshot) "
                        f"but has {pending_count} pending messages, status=WAITING_CHILDREN (deprecated)"
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

                # Update instance status to COMPLETED in DB
                instance.status = InstanceStatus.COMPLETED.value
                instance.updated_at = datetime.now(timezone.utc).isoformat()
                instance.last_activity_at = datetime.now(timezone.utc)
                instance.version = (instance.version or 1) + 1

                session.commit()
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

                # Update child status to COMPLETED
                instance.status = InstanceStatus.COMPLETED.value
                instance.updated_at = datetime.now(timezone.utc).isoformat()
                instance.last_activity_at = datetime.now(timezone.utc)
                instance.version = (instance.version or 1) + 1

                # Capture parent_id before session closes
                parent_id = instance.parent_id

                session.commit()
                # Post-commit side effects are dispatched by the async caller.
                return _ChildCompletionDbResult(
                    outcome="tool_invocation_completed",
                    instance_id=instance_id,
                    agent_id=instance.agent_id,
                    parent_id=parent_id,
                )

            # ATOMIC: Instance completed — create completion report for parent
            logger.info(f"Instance {instance_id[:8]}... completed, sending report to parent {instance.parent_id[:8]}...")
            
            # --- Inline: _create_completion_report (no await needed) ---
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
                content=last_content,
                source=f"internal_report:{instance.instance_id}:{completed_message_id}",
                type=MessageType.COMPLETION_REPORT.value,
                status=MessageStatus.READY.value,
                priority=0,
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
            
            # --- Inline: _update_parent_on_child_complete (no await needed) ---
            # ─── Phase A: USE_LEGACY_WAITING_FOR_CASCADE kill switch ─────
            # When the flag is OFF (default), skip the legacy
            # ``waiting_for`` SQL decrement — the CorrelationManager is
            # the SOLE completion authority and tracks the in-memory
            # pending set. The ``waiting_for`` column is the rebuild
            # cache (ADR-011) and stays in sync via the CM. When the
            # flag is ON (kill switch), run the legacy M0 path
            # unchanged. See ``docs/configuration/completion-flags.md``
            # and the execution plan (decouple-execution-plan.md §A4).
            use_legacy_cascade = bool(
                self._config.job_system.use_legacy_waiting_for_cascade
            )

            if use_legacy_cascade:
                # Decrement parent's waiting_for counter atomically.
                result_update = session.execute(
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
                    {"pid": instance.parent_id},
                )
                new_waiting_row = result_update.first()
                new_waiting = int(new_waiting_row[0]) if new_waiting_row is not None else 0

                # Force the session to re-read for the cascade check below.
                parent = session.get(Instance, instance.parent_id)
                session.expire(parent)
                parent = session.get(Instance, instance.parent_id)
                logger.info(
                    f"waiting_for decremented -> {new_waiting} "
                    f"(parent={parent.instance_id[:8] if parent else '?'}..., "
                    f"child={instance.instance_id[:8]}...)"
                )
            else:
                # Flag OFF: do NOT decrement the DB ``waiting_for``
                # column. CM is the authority; decrementing here would
                # cause divergence between CM and the rebuild cache.
                # Just look up the parent for the cascade check below
                # (CM.is_complete reads from its in-memory set, not the
                # DB column).
                parent = session.get(Instance, instance.parent_id)
                logger.debug(
                    f"USE_LEGACY_WAITING_FOR_CASCADE=OFF: skipping waiting_for SQL "
                    f"decrement for parent={instance.parent_id[:8] if instance.parent_id else '?'}..., "
                    f"child={instance.instance_id[:8]}... (CM is authoritative)"
                )

            parent.last_activity_at = datetime.now(timezone.utc)
            parent.version = (parent.version or 1) + 1

            # NOTE: We no longer mutate ``parent.children`` (JSON cache) here.
            # The ``instance_hierarchy`` junction table is the canonical
            # source of parent-child relationships — _enrich_instance() in
            # daemon/repositories/instance/repository.py loads children
            # from it on every read. Writes to the JSON cache were doubly
            # broken (RMW races + overridden on read) and persistently
            # useless (no code ever reads the corrupted value). See C10.

            # Remove from instance_hierarchy junction table
            session.execute(
                text("DELETE FROM instance_hierarchy WHERE child_id = :child_id"),
                {"child_id": instance.instance_id}
            )

            # Cascade check — gated by USE_LEGACY_WAITING_FOR_CASCADE
            # (Phase A). CM is the SOLE completion authority when the
            # flag is OFF; the legacy ``waiting_for == 0`` READ is
            # only consulted when the kill switch is ON.
            from .correlation_manager import get_correlation_manager as get_cm_for_cascade
            cm = get_cm_for_cascade()
            if cm is not None:
                is_parent_complete = cm.is_complete(parent.instance_id)
            elif use_legacy_cascade:
                is_parent_complete = (getattr(parent, "waiting_for", None) or 0) == 0
            else:
                # ─── A8: HARD ERROR (not graceful degradation) ─────────────
                # CM is None AND ``USE_LEGACY_WAITING_FOR_CASCADE=OFF`` is an
                # INVALID state. The ``SELECT COUNT(*)`` fallback (Race #3) is
                # the exact bug we are fixing — it MUST NOT be reachable when
                # the kill switch is OFF. Mirrors the hard error at the
                # ``_update_parent_on_child_complete`` call site (line ~660)
                # — the inlined copy here exists because
                # ``_process_child_completion_db_sync`` runs on a worker
                # thread via ``asyncio.to_thread`` and cannot ``await`` the
                # async helper. Both call sites must enforce the same A8
                # invariant.
                #
                # Honest propagation note: this RuntimeError is caught by
                # the W3 fail-safe (``except Exception``) in
                # ``_finalize_job`` and results in a per-job FAILED
                # transition. It is NOT a process-level crash. For
                # production, CM must be initialized before any traffic.
                raise RuntimeError(
                    f"USE_LEGACY_WAITING_FOR_CASCADE=OFF but CorrelationManager "
                    f"is not initialized for parent={instance.parent_id[:8] if instance.parent_id else '?'}...; "
                    f"CM must be initialized when the legacy cascade is disabled. "
                    f"This is a hard error — the SELECT COUNT(*) TOCTOU fallback "
                    f"(Race #3) is disabled by design."
                )
            
            completed_parent_id: str | None = None
            completed_parent_parent_id: str | None = None
            parent_waiting_children_sse: bool = False
            waiting_children_parent_agent_id: str | None = None
            
            if (
                is_parent_complete
                and parent.status != InstanceStatus.COMPLETED.value
                and parent.status != InstanceStatus.ERROR.value
            ):
                if cm is not None:
                    # CM is active — CM callback handles completion.
                    # No count_pending query, no inline status transition.
                    logger.info(
                        f"CM-active: skipping inline cascade for parent "
                        f"{parent.instance_id[:8]}... — CM callback owns completion"
                    )
                else:
                    # Check if parent has any pending messages (legacy path)
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
                        logger.info(f"Parent {parent.instance_id[:8]}... completed after all children done")
                        completed_parent_id = parent.instance_id
                        completed_parent_parent_id = parent.parent_id
                    else:
                        # Has pending messages but all children done - transition to WAITING_CHILDREN
                        #
                        # Defense-in-depth (F8): if there is NO active
                        # MESSAGE job for this parent, the parent_pending
                        # count is a false positive (stale/duplicate
                        # from task-claim race). Skip the
                        # WAITING_CHILDREN write to avoid permanently
                        # stranding the parent. The child-completion
                        # work above (report message, task,
                        # waiting_for decrement, children cache,
                        # hierarchy delete) is still real and we let
                        # the function proceed to commit + emit
                        # events — just leave the parent status as-is.
                        if self._has_no_active_message_job(session, parent.instance_id):
                            logger.warning(
                                f"Parent {parent.instance_id[:8]}... has {parent_pending} pending "
                                f"messages but no active MESSAGE job — skipping WAITING_CHILDREN "
                                f"write (stale/duplicate messages from task-claim race)"
                            )
                        else:
                            parent.status = InstanceStatus.WAITING_CHILDREN.value
                            logger.info(
                                f"Parent {parent.instance_id[:8]}... all children done but has {parent_pending} "
                                f"pending messages, status=WAITING_CHILDREN (deprecated; CM is authoritative)"
                            )
                            # Flag SSE emission for the async caller (was at
                            # line 624-627 in the pre-refactor inline block).
                            parent_waiting_children_sse = True
                            waiting_children_parent_agent_id = parent.agent_id
            
            # --- Inline: _create_completion_events (no await needed) ---
            # Phase 4: derive from the CorrelationManager when available
            # (in-memory pending set — authoritative for runtime). Falls
            # back to the ``waiting_for`` DB column (rebuild cache) when
            # CM is None / disabled (graceful degradation).
            if instance.parent_id is not None and cm is not None:
                waiting_for_remaining = max(0, int(cm.get_pending_count(instance.parent_id) or 0))
            else:
                # Legacy fallback — read from the DB column.
                waiting_for_remaining = max(0, (instance.parent_id and session.get(Instance, instance.parent_id).waiting_for) or 0)
            
            # Create completion event for child
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
                    "waiting_for_remaining": waiting_for_remaining,
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
        lifecycle events, and the CM resolve hook based on the DB outcome.

        The CM resolve hook (``notify_corr_resolve``) fires AFTER the commit so
        that the CM's in-memory pending set is updated after the DB state is
        consistent. The callback ``handle_correlation_complete`` does its own
        separate DB transaction (via ``asyncio.to_thread``), so there is no
        dependency between the commit orderings.

        Args:
            result: The outcome from the sync DB helper.
            last_content: The assistant message content (used for CompletionRegistry).
            completed_message_id: The completed message ID (used for CM hook).
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

        # Root carve-out (F5/F6): the WAITING_CHILDREN write was
        # suppressed because there is no active MESSAGE job
        # (stale/duplicate from task-claim race). The instance stays in
        # its current (non-terminal) status — we do NOT emit a
        # "completed" SSE, do NOT publish a lifecycle "completed"
        # event, and do NOT trigger title generation. We DO signal
        # CompletionRegistry so any ``invoke_agent_and_wait()``
        # callers do not hang. ``complete()`` is idempotent (see
        # ``CompletionRegistry.complete`` — duplicate calls return
        # False but are safe) and buffers the result if no event
        # exists yet.
        if outcome == "root_skipped_terminal_job":
            try:
                from .completion_registry import get_completion_registry
                get_completion_registry().complete(instance_id, result=last_content)
                logger.info(
                    f"Root {instance_id[:8]}... carve-out fired (no active job), "
                    f"CompletionRegistry signaled"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to signal CompletionRegistry for {instance_id[:8]}...: {e}"
                )
            return

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

        # Regular child completed: commit + CM hook + SSE + CompletionRegistry + lifecycle + title
        if outcome == "regular_child_completed":
            # CM resolve hook: fires AFTER the commit so the DB state is
            # consistent before the CM's callback runs its own transaction.
            #
            # Phase D (DependencyBus): when ``use_dependency_bus=ON``, the
            # CM hook is SKIPPED and replaced by ``bus.emit_terminal``,
            # exactly as in the inline branch above
            # (``_update_parent_on_child_complete``). The bus path here
            # handles the post-commit side-effect variant — both call
            # sites converge on ``_emit_terminal_via_bus`` to keep the
            # bus wiring in one place.
            if completed_message_id:
                # W1 fix (2026-06-21): same guard as the inline branch
                # above — flag AND bus singleton. Mirrors the comment
                # block at line ~725; the rationale (bus=None →
                # FollowUp silently dropped) is identical. See that
                # site for the full explanation.
                from .dependency_bus import get_dependency_bus
                use_bus_path = (
                    self._is_dependency_bus_enabled()
                    and get_dependency_bus() is not None
                )
                if use_bus_path:
                    # ─── Phase D: DependencyBus path (replaces CM) ───
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
                else:
                    try:
                        from .correlation_manager import notify_corr_resolve
                        await notify_corr_resolve(
                            parent_id=parent_id,
                            child_id=instance_id,
                            message_id=completed_message_id,
                            status="responded",
                        )
                    except Exception as hook_err:
                        logger.warning(
                            f"CM hook: resolve path failed "
                            f"(parent={parent_id[:8] if parent_id else '?'}..., "
                            f"child={instance_id[:8]}...): {hook_err}"
                        )

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
