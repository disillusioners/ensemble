"""Error reporting service for handling and reporting errors to parent instances."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, NamedTuple

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


class _ErrorReportDbResult(NamedTuple):
    """Result of the sync DB half of :meth:`ErrorReportingService._send_error_report`.

    Carries the values the async caller needs after the ``WriteGuardSession``
    block has run on a worker thread (via ``asyncio.to_thread``):

    * ``skip`` — when True, the child instance row was not found in the DB.
      The caller should return early without firing the post-commit side
      effects (CompletionRegistry / SSE / lifecycle event / enqueue).
    * ``child_instance_id`` / ``child_agent_id`` — captured from the child
      instance row before the session closes (the instance is detached
      after commit). Used by the caller for the child-error SSE.
    * ``cascade_status`` — ``"completed"`` or ``"waiting_children"`` when
      the CM-disabled inline cascade ran and committed a parent status
      transition; ``None`` when no cascade happened (CM-active path, or
      the parent still has pending children, or parent is already
      terminal). The caller uses this to fire the appropriate
      post-commit SSE + lifecycle event on the parent.
    * ``cascade_parent_id`` / ``cascade_parent_agent_id`` /
      ``cascade_parent_parent_id`` — the parent instance IDs captured
      before the cascade commit. Only meaningful when ``cascade_status``
      is not ``None``.
    """

    skip: bool
    child_instance_id: str | None
    child_agent_id: str | None
    cascade_status: str | None
    cascade_parent_id: str | None
    cascade_parent_agent_id: str | None
    cascade_parent_parent_id: str | None


def _is_dependency_bus_enabled(manager: "InstanceManager") -> bool:
    """Read the ``use_dependency_bus`` flag from the manager config.

    Module-level helper (not a method) because the gated call site in
    ``_send_error_report`` is a long function and the helper is also
    used by other call sites that receive ``manager`` as an argument.
    Mirrors the defensive ``getattr`` chain used in
    ``JobProcessor._is_legacy_jobqueue_dispatch_enabled``,
    ``ChildReportsService._is_dependency_bus_enabled``, and the sibling
    helper in ``daemon/tools/instance.py`` so test mocks that bypass
    ``InstanceManager.__init__`` (e.g. ``MagicMock()`` without explicit
    ``config``) don't crash. Default is False (Phase D feature flag
    OFF = legacy CM path is active), matching the config field's
    default.

    Args:
        manager: The InstanceManager (or test mock).

    Returns:
        True if the operator has enabled the DB-backed DependencyBus
        completion-delivery path; False otherwise.
    """
    _config = getattr(manager, "config", None)
    _job_system = getattr(_config, "job_system", None)
    return bool(
        getattr(_job_system, "use_dependency_bus", False)
    )


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

    def _send_error_report_db_sync(
        self,
        instance_id: str,
        parent_id: str,
        message_id: str | None,
    ) -> _ErrorReportDbResult:
        """Sync DB half of :meth:`_send_error_report`.

        Opens a ``WriteGuardSession`` and performs ALL the DB operations
        that were previously inlined in the async caller:

          a) Set child instance status to ERROR + bookkeeping.
          b) Fail the associated message (if ``message_id`` is provided).
          c) Parent bookkeeping: ``last_activity_at``, ``version`` bump.
          d) Delete from ``instance_hierarchy``.
          e) Inline cascade (A9: hard error when bus is None): bus is
             the SOLE completion authority.
             or WAITING_CHILDREN, then commit.

        When the CM is active, the inline cascade is SKIPPED (the CM
        callback owns completion). The CM hook itself
        (``notify_corr_resolve``) is an in-memory notification that must
        run on the asyncio event loop — it is NOT a DB operation and
        therefore NOT in this helper. The async caller invokes it
        AFTER this helper returns.

        Post-commit async side effects (SSE ``status_change``,
        lifecycle event publish) are also NOT in this helper — they
        remain in the async caller so they can use ``await``. This
        method returns a :class:`_ErrorReportDbResult` that tells the
        caller which post-commit SSE/lifecycle event to fire (if any).

        Runs on a worker thread via ``asyncio.to_thread`` from
        :meth:`_send_error_report`. This keeps ``session.commit()`` off
        the event loop so SQLite WAL write contention cannot deadlock
        the daemon (see the deadlock analysis in the experience docs).

        Idempotency: if the child instance row is missing, returns
        ``skip=True`` and the caller short-circuits without firing
        the post-commit side effects.

        Args:
            instance_id: The child instance ID that has failed.
            parent_id: The parent instance ID (already resolved by the
                caller from a pre-session read; passed in so the helper
                doesn't re-read the instance row just to find the
                parent).
            message_id: Optional message ID that triggered the error.

        Returns:
            :class:`_ErrorReportDbResult` carrying ``skip=True`` when
            the child row is missing, or ``skip=False`` with the
            captured child IDs and (if the inline cascade ran) the
            parent status transition info.
        """
        from sqlalchemy import func, select

        with WriteGuardSession(
            Session(self._manager.engine), self._manager.write_guard
        ) as session:
            # a) Get child instance
            instance = session.get(Instance, instance_id)
            if not instance:
                return _ErrorReportDbResult(
                    skip=True,
                    child_instance_id=None,
                    child_agent_id=None,
                    cascade_status=None,
                    cascade_parent_id=None,
                    cascade_parent_agent_id=None,
                    cascade_parent_parent_id=None,
                )

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

            # d) Capture child instance_id; the completion cascade below
            # uses ``bus.count_pending_for_target_sync`` as the SOLE
            # completion authority (A9: hard error when bus is None).
            parent = session.get(Instance, parent_id)
            if parent:

                # NOTE: The CM hook (``await notify_corr_resolve``) that
                # used to live here has been moved out of this sync helper
                # to the async caller. It is an in-memory CM notification
                # that acquires the CM's per-parent asyncio.Lock (N3
                # constraint: must run on the main event loop) — it
                # cannot be called from a worker thread. The hook is
                # safe to run AFTER this helper returns because:
                #   - When CM is active, the inline cascade below is
                #     SKIPPED anyway (CM callback owns completion), so
                #     ordering the hook before or after the cascade check
                #     does not change the outcome.
                #   - When CM is None (disabled), the hook is a no-op.

                session.expire(parent)
                parent = session.get(Instance, parent_id)
                parent.last_activity_at = datetime.now(timezone.utc)
                parent.version = (parent.version or 1) + 1

                # NOTE: We no longer mutate ``parent.children`` (JSON cache) here.
                # The ``instance_hierarchy`` junction table is the canonical
                # source of parent-child relationships. See Phase 4 migration
                # 20260621_000002.

                # f) Delete from instance_hierarchy
                session.execute(
                    text("DELETE FROM instance_hierarchy WHERE child_id = :child_id"),
                    {"child_id": instance_id},
                )

                # g) Cascade: check if parent can complete after all children done/error.
                # A9: bus is the SOLE completion authority; bus=None is hard error.
                from .dependency_bus import get_dependency_bus
                bus = get_dependency_bus()
                if bus is not None:
                    # Bus is wired — it is the SOLE completion authority.
                    all_children_done = (
                        bus.count_pending_for_target_sync(
                            parent.instance_id
                        )
                        == 0
                    )
                else:
                    raise RuntimeError(
                        "DependencyBus is None — invalid state. "
                        "The bus must be initialized (see ADR-011)."
                    )
                if (
                    all_children_done
                    and parent.status != InstanceStatus.COMPLETED.value
                    and parent.status != InstanceStatus.ERROR.value
                ):
                    # Phase 3 (Cascade Unification) — preserved through
                    # Phase 5 (CM removal): when the completion authority
                    # is wired, the inline cascade + SELECT COUNT(*)
                    # fallback + inline status transition are all
                    # SKIPPED. The DependencyBus (Phase D, the SOLE
                    # completion authority after CM removal in Phase 5)
                    # fires ``_retrigger_parent_finalize`` from the
                    # async caller in ``_send_error_report``, which
                    # transitions the parent JOB to terminal via
                    # ``_finalize_job``. No DB query, no TOCTOU window
                    # (Race #3 eliminated).
                    #
                    # ``bus`` is already in scope from the
                    # ``get_dependency_bus()`` call at the top of
                    # this `if` block (Phase 5 dedup). The
                    # ``all_children_done`` check above guarantees
                    # the bus will succeed when its emit fires from
                    # the async caller.
                    if bus is not None:
                        # Bus is wired — bus callback owns completion.
                        logger.info(
                            f"Bus-active: skipping inline cascade for parent "
                            f"{parent.instance_id[:8]}... (error path) — "
                            f"bus callback owns completion"
                        )
                    else:
                        # Graceful degradation: keep the original inline
                        # logic with SELECT COUNT(*) fallback. This path
                        # is also the one exercised by tests that do not
                        # wire a CM fixture.
                        # Check if parent has any pending messages
                        parent_pending = session.exec(
                            select(func.count())
                            .select_from(MessageQueue)
                            .where(MessageQueue.instance_id == parent.instance_id)
                            .where(
                                MessageQueue.status.in_(
                                    [
                                        MessageStatus.READY.value,
                                        MessageStatus.PROCESSING.value,
                                        MessageStatus.RETRYING.value,
                                    ]
                                )
                            )
                        ).scalar_one()

                        if parent_pending == 0:
                            # No pending messages, parent is truly complete
                            parent.status = InstanceStatus.COMPLETED.value
                            parent.updated_at = datetime.now(
                                timezone.utc
                            ).isoformat()
                            logger.info(
                                f"Parent {parent.instance_id[:8]}... completed after child error"
                            )

                            # Capture parent_id and agent_id for event publishing (outside transaction)
                            completed_parent_id = parent.instance_id
                            completed_parent_agent_id = parent.agent_id
                            completed_parent_parent_id = parent.parent_id

                            session.commit()

                            # Return cascade info so the async caller can
                            # fire the post-commit SSE + lifecycle event
                            # AFTER ``await asyncio.to_thread`` returns.
                            return _ErrorReportDbResult(
                                skip=False,
                                child_instance_id=error_instance_id,
                                child_agent_id=child_agent_id,
                                cascade_status="completed",
                                cascade_parent_id=completed_parent_id,
                                cascade_parent_agent_id=completed_parent_agent_id,
                                cascade_parent_parent_id=completed_parent_parent_id,
                            )
                        else:
                            # Has pending messages - transition to WAITING_CHILDREN
                            # Parent should wait for its message processing to complete
                            parent.status = InstanceStatus.WAITING_CHILDREN.value
                            parent.updated_at = datetime.now(
                                timezone.utc
                            ).isoformat()
                            session.commit()  # Commit the WAITING_CHILDREN status change
                            logger.info(
                                f"Parent {parent.instance_id[:8]}... all children done but has {parent_pending} "
                                f"pending messages, status=WAITING_CHILDREN after child error"
                            )
                            # Return cascade info so the async caller can
                            # fire the post-commit SSE AFTER the helper.
                            return _ErrorReportDbResult(
                                skip=False,
                                child_instance_id=error_instance_id,
                                child_agent_id=child_agent_id,
                                cascade_status="waiting_children",
                                cascade_parent_id=parent.instance_id,
                                cascade_parent_agent_id=parent.agent_id,
                                cascade_parent_parent_id=parent.parent_id,
                            )

        # Session exited (CM-active path, or cascade didn't trigger).
        # Return the captured child IDs so the async caller can fire
        # the post-commit CompletionRegistry / child-error SSE. No
        # cascade_status set — the CM callback or the next caller's
        # processing owns any parent-side transition.
        return _ErrorReportDbResult(
            skip=False,
            child_instance_id=error_instance_id,
            child_agent_id=child_agent_id,
            cascade_status=None,
            cascade_parent_id=None,
            cascade_parent_agent_id=None,
            cascade_parent_parent_id=None,
        )

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
            
            # Step 3: Atomic DB transaction — runs on a worker thread via
            # ``asyncio.to_thread`` so the sync ``session.commit()`` cannot
            # block the event loop. SQLite WAL + 30s busy_timeout would
            # otherwise wedge the loop completely on write contention
            # (the documented deadlock chain). The helper returns a
            # ``_ErrorReportDbResult`` carrying everything the async caller
            # needs to fire the post-commit side effects.
            db_result = await asyncio.to_thread(
                self._send_error_report_db_sync,
                instance_id,
                parent_id,
                message_id,
            )
            if db_result.skip:
                # Child instance row missing — return without firing
                # any post-commit side effects (CompletionRegistry / SSE
                # / lifecycle event / enqueue). The helper already
                # logged at debug-equivalent; nothing to do here.
                return

            # CM hook — runs on the event loop AFTER the sync helper
            # returns. This is an in-memory CM notification (acquires the
            # CM's per-parent ``asyncio.Lock``; N3 constraint: must run
            # on the main event loop) and is therefore NOT in the sync
            # helper. Safe to run after the helper because:
            #   - When CM is active, the inline cascade inside the helper
            #     is SKIPPED anyway (CM callback owns completion), so
            #     ordering the hook before or after the cascade check
            #     does not change the outcome.
            #   - When CM is None (disabled / not wired), the hook is a
            #     no-op (``notify_corr_resolve`` checks for CM and
            #     returns early when absent).
            #
            # Skip the hook when message_id is missing/empty:
            # the CM keys correlations on (child_id, message_id) and
            # cannot resolve a None/empty message_id against any
            # registered entry. Calling with message_id="" would
            # silently no-op and the pending entry would stay forever.
            #
            # Phase D (DependencyBus): when ``use_dependency_bus=ON``, the
            # CM ``notify_corr_resolve`` call is SKIPPED and replaced by
            # ``bus.emit_terminal(...)`` keyed on the child task id,
            # with ``status="error"`` and the error message forwarded as
            # the Outcome's ``error`` field. The two authorities are
            # mutually exclusive — never called in parallel — to prevent
            # double-fire (Phase A lesson).
            # The bus is a pure state machine — it does NOT deliver
            # messages to the LLM. The re-trigger of ``_finalize_job``
            # is the actual finalization path (see
            # ``_emit_terminal_via_bus`` docstring).
            if message_id:
                if _is_dependency_bus_enabled(self._manager):
                    # ─── Phase D: DependencyBus path (replaces CM) ───────
                    _child_task_err = None
                    _task_repo_err = getattr(self._manager, "_task_repo", None)
                    if _task_repo_err is not None:
                        _child_task_err = await asyncio.to_thread(
                            _task_repo_err.get_by_message, message_id
                        )
                    # Import here to avoid module-level cycle with
                    # dependency_bus importing from this module transitively.
                    from .dependency_bus import (
                        Outcome,
                        get_dependency_bus,
                    )
                    _bus = get_dependency_bus()
                    if _bus is None:
                        logger.warning(
                            "use_dependency_bus=ON but bus singleton is "
                            "None; falling back to legacy CM resolve "
                            "(error) path"
                        )
                    else:
                        # ─── Phase D re-trigger (inverse-regression fix) ──
                        # Delegate the bus emission + FollowUp enqueue
                        # + re-trigger loop to
                        # ``child_reports._emit_terminal_via_bus`` so the
                        # finalize re-trigger fires uniformly for BOTH
                        # completion and error paths. Previously this
                        # code called ``_bus.emit_terminal`` directly
                        # and enqueued FollowUps, but never invoked
                        # ``_retrigger_parent_finalize`` — meaning a
                        # child error could leave the parent stuck in
                        # PROCESSING forever (the inverse-regression
                        # bug the re-trigger was added to prevent on
                        # the completion path). See
                        # ``child_reports._emit_terminal_via_bus``
                        # docstring for the full rationale and the
                        # ``_retriggered`` set guard that prevents
                        # redundant observer work.
                        _child_reports_svc = getattr(
                            self._manager, "_child_reports_service", None
                        )
                        if _child_reports_svc is not None:
                            try:
                                await _child_reports_svc._emit_terminal_via_bus(
                                    task_id=getattr(_child_task_err, "id", None),
                                    status="error",
                                    error=error,
                                    summary=f"child errored: {error_type}",
                                )
                            except Exception as hook_err:
                                logger.warning(
                                    f"bus hook: _emit_terminal_via_bus "
                                    f"(error) failed "
                                    f"(parent={parent_id[:8]}..., "
                                    f"child={instance_id[:8]}...): {hook_err}"
                                )
                        else:
                            # Defensive fallback: ``child_reports``
                            # service is not wired (unit tests with a
                            # bare MagicMock manager, or partial init
                            # during early daemon startup). Replicate
                            # the bus emit so the FIRED transition
                            # happens on the bus DB, but DO NOT
                            # enqueue a FollowUp message — the bus
                            # path is internal plumbing, not a
                            # message source the LLM consumes. The
                            # re-trigger finalization is also lost in
                            # this branch (the helper lives on
                            # ``ChildReportsService``), so the parent
                            # may stay PROCESSING in this extremely
                            # rare fallback path. In production this
                            # branch should never trigger; the
                            # ``_child_reports_service`` attribute is
                            # set in
                            # ``InstanceManager.__init__`` before the
                            # error-reporting service is wired.
                            logger.warning(
                                f"bus hook (error): child_reports "
                                f"service not wired; falling back to "
                                f"direct _bus.emit_terminal (no "
                                f"FollowUp enqueue, no re-trigger — "
                                f"parent may be stuck in PROCESSING). "
                                f"parent={parent_id[:8]}..., "
                                f"child={instance_id[:8]}..."
                            )
                            try:
                                _outcome = Outcome(
                                    status="error",
                                    error=error,
                                    summary=f"child errored: {error_type}",
                                )
                                _fired = await _bus.emit_terminal(
                                    task_id=str(
                                        getattr(_child_task_err, "id", None) or ""
                                    ),
                                    outcome=_outcome,
                                )
                                # No FollowUp enqueue — the bus does
                                # not deliver messages. The FollowUp
                                # payload is logged for observability
                                # but discarded.
                                for _fu in _fired:
                                    logger.debug(
                                        f"bus error: FIRED watcher "
                                        f"target={_fu.target_instance_id[:8]}..., "
                                        f"outcome=error (no FollowUp "
                                        f"enqueue — re-trigger lost in "
                                        f"this fallback path)",
                                        extra={"completion_delivery_path": "bus"},
                                    )
                            except Exception as hook_err:
                                logger.warning(
                                    f"bus hook: emit_terminal (error) failed "
                                    f"(parent={parent_id[:8]}..., "
                                    f"child={instance_id[:8]}...): {hook_err}"
                                )
                else:
                    # Phase 5 (2026-06-23): the legacy CM path is
                    # GONE — the CorrelationManager class was removed.
                    # The bus is the SOLE completion authority; when
                    # ``use_dependency_bus=OFF``, the daemon is in an
                    # invalid state per ADR-011 (A9 hard error). Log
                    # the failure at WARNING and continue — the
                    # inline cascade in the sync helper above has
                    # already committed the DB state, and the rest
                    # of the error-reporting path (CompletionRegistry,
                    # SSE, error message enqueue) proceeds normally.
                    # The parent may stay PROCESSING in this OFF
                    # state — that is the documented trade-off for
                    # disabling the bus.
                    logger.warning(
                        f"Bus-off (use_dependency_bus=False): cannot "
                        f"resolve parent={parent_id[:8]}... for child "
                        f"error on instance={instance_id[:8]}... — "
                        f"the bus is the SOLE completion authority "
                        f"after Phase 5 CM removal; parent may stay "
                        f"in PROCESSING until manual intervention. "
                        f"Enable ``use_dependency_bus: true`` to "
                        f"restore automatic finalization."
                    )
            else:
                logger.debug(
                    f"CM hook: skipping resolve (error) for parent="
                    f"{parent_id[:8]}..., child={instance_id[:8]}... "
                    f"(no message_id — error reported without a tracked send)"
                )

            # Post-commit side effects for the inline cascade
            # (CM-disabled / graceful-degradation path only). The sync
            # helper committed the parent status transition; we now fire
            # the corresponding SSE + lifecycle event on the event loop.
            if db_result.cascade_status == "completed":
                # Emit status_change SSE event for parent completed
                if self._manager._live_hub:
                    try:
                        await self._manager._live_hub.stream_status_change(
                            db_result.cascade_parent_id,
                            "completed",
                            agent_id=db_result.cascade_parent_agent_id,
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to emit status_change for completed parent: {e}"
                        )

                # FIX: Publish lifecycle event so JobFeedbackObserver completes the job
                if self._events_service:
                    await self._events_service._publish_instance_lifecycle_event(
                        instance_id=db_result.cascade_parent_id,
                        status="completed",
                        error=None,
                        parent_id=db_result.cascade_parent_parent_id,
                    )
            elif db_result.cascade_status == "waiting_children":
                # Emit status_change SSE event for parent waiting_children
                if self._manager._live_hub:
                    try:
                        await self._manager._live_hub.stream_status_change(
                            db_result.cascade_parent_id,
                            "waiting_children",
                            agent_id=db_result.cascade_parent_agent_id,
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to emit status_change for waiting_children parent: {e}"
                        )
            
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
                    await self._manager._live_hub.stream_status_change(
                        db_result.child_instance_id,
                        "error",
                        agent_id=db_result.child_agent_id,
                    )
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
