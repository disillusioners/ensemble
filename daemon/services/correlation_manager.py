"""CorrelationManager: Tracks send_message → response correlations for parent instances.

Phase 1 (Shadow Mode): The CM observes and validates the existing waiting_for counter
without modifying control flow. It compares its own pending count against DB waiting_for
and logs mismatches for investigation.

Architecture:
- Per-parent asyncio.Lock serializes all register/resolve calls for each parent.
- All lock-protected methods run on the main asyncio event loop (N3 constraint).
- EventBus subscription uses a large buffer (5000) to mitigate silent-drop risk.
- completion_callback is invoked AFTER the per-parent Lock is released (W1).
  This allows Phase 2 callbacks to perform cascade work (e.g. parent status
  transitions) that may re-enter CM for the same parent_id without deadlocking.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Awaitable

if TYPE_CHECKING:
    from daemon.services.event_bus import EventBus

logger = logging.getLogger(__name__)

# Status values for PendingResponse entries
STATUS_PENDING = "pending"
STATUS_RESPONDED = "responded"
STATUS_ERROR = "error"

# EventBus queue buffer size — large to mitigate silent-drop risk (N1).
# Default EventBus global_queue_size is 1000. A completion event storm
# (many children completing simultaneously) could overflow a small buffer,
# causing events to be silently dropped and shadow validation to miss updates.
_EVENT_BUS_QUEUE_SIZE = 5000

# Rate-limiting constants
_RATE_LIMIT_WINDOW_SEC = 60.0
_RATE_LIMIT_CAP = 100
_RATE_LIMIT_SUMMARY_INTERVAL_SEC = 300.0


@dataclass
class PendingResponse:
    """Tracks a single outstanding send_message → response correlation."""

    parent_id: str
    child_id: str
    message_id: str
    created_at: float
    status: str = STATUS_PENDING  # "pending" | "responded" | "error"


@dataclass
class ParentCorrelation:
    """All outstanding message-response correlations for one parent."""

    parent_id: str
    pending: dict[str, PendingResponse] = field(default_factory=dict)
    # correlation_key = f"{child_id}:{message_id}"
    had_error: bool = False  # Set True when any response resolves with error

    @property
    def is_complete(self) -> bool:
        return len(self.pending) == 0

    @property
    def pending_count(self) -> int:
        return len(self.pending)


class CorrelationManager:
    """Tracks message-response correlations for parent instances.

    Phase 1 runs in shadow mode: it observes and validates the existing
    waiting_for counter without affecting any control flow.

    Usage (Phase 1 — shadow mode):
        cm = CorrelationManager(
            instance_repository=...,
            message_queue_repository=...,
            completion_callback=None,  # Not called in shadow mode
            event_bus=...,
        )
        await cm.start()
        # CM runs in background, logging shadow validations
        await cm.stop()
    """

    def __init__(
        self,
        instance_repository,  # SQLModelInstanceRepository
        message_queue_repository,  # SQLModelMessageQueueRepository
        completion_callback: Callable[[str, str], Awaitable[None]] | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        """Initialize the CorrelationManager.

        Args:
            instance_repository: The instance repository (for DB queries).
            message_queue_repository: The message queue repository (for DB queries).
            completion_callback: Async callback called when parent completes.
                Signature: async def callback(parent_id: str, terminal_status: str) -> None
                Invoked AFTER the per-parent lock is released (W1 fix), so it
                is safe for the callback to re-enter CM for the same parent_id
                (e.g. during Phase 2 cascade work) without deadlocking.
            event_bus: Optional EventBus instance for inbound lifecycle subscription.
                If provided, Phase 1 uses it for shadow validation logging.
        """
        self._instance_repo = instance_repository
        self._msg_repo = message_queue_repository
        self._completion_callback = completion_callback
        self._event_bus = event_bus

        # In-memory state: parent_id → ParentCorrelation
        self._pending: dict[str, ParentCorrelation] = {}

        # Per-parent asyncio.Lock for serialized register/resolve.
        # Must be created on the main event loop — all methods using these
        # locks MUST run on the main event loop (N3 constraint).
        self._locks: dict[str, asyncio.Lock] = {}

        # Background task for processing EventBus events
        self._event_task: asyncio.Task[None] | None = None
        self._running = False

        # EventBus subscription
        self._subscriber_id: str | None = None
        self._event_queue: asyncio.Queue | None = None

        # Rate-limiting state for shadow validation logs
        self._mismatch_count = 0
        self._match_count = 0
        self._last_mismatch_summary = 0.0
        self._last_match_summary = 0.0
        # Per-counter window timestamps so a burst of mismatches does not
        # reset the match window (and vice versa).
        self._mismatch_window_start: float = 0.0
        self._match_window_start: float = 0.0

        # Cache for the DEBUG_COMPLETION_INVARIANT flag. None = not yet
        # read. The flag is a runtime operator toggle (config-level) and
        # is not expected to change without a daemon restart, so we read
        # it once on first use and cache the value. Tests that need to
        # flip the flag dynamically set this attribute directly.
        self._debug_invariant_enabled: bool | None = None

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    def _get_lock(self, parent_id: str) -> asyncio.Lock:
        """Get or create the per-parent asyncio.Lock.

        Must be called from the main event loop (N3 constraint).
        Lock is lazily created bound to the current event loop.

        Args:
            parent_id: The parent instance ID.

        Returns:
            asyncio.Lock for this parent.
        """
        if parent_id not in self._locks:
            self._locks[parent_id] = asyncio.Lock()
        return self._locks[parent_id]

    def _determine_terminal_status(self, parent_state: ParentCorrelation) -> str:
        """Determine the terminal status for a parent.

        Conservative: any error response → parent "error".

        Args:
            parent_state: The parent's correlation state.

        Returns:
            "error" if had_error else "completed".
        """
        return "error" if parent_state.had_error else "completed"

    # -------------------------------------------------------------------------
    # Public API (shadow mode — logging only, no control flow)
    # -------------------------------------------------------------------------

    async def register_message_send(
        self,
        parent_id: str,
        child_id: str,
        message_id: str,
    ) -> None:
        """Register a pending message send (mirrors waiting_for++).

        Must be called from the main event loop (N3 constraint).

        Args:
            parent_id: The parent instance ID.
            child_id: The child instance ID.
            message_id: The message ID.
        """
        correlation_key = f"{child_id}:{message_id}"
        async with self._get_lock(parent_id):
            if parent_id not in self._pending:
                self._pending[parent_id] = ParentCorrelation(parent_id=parent_id)

            parent_state = self._pending[parent_id]
            entry = PendingResponse(
                parent_id=parent_id,
                child_id=child_id,
                message_id=message_id,
                created_at=time.monotonic(),
                status=STATUS_PENDING,
            )
            parent_state.pending[correlation_key] = entry

            logger.debug(
                f"CM register: parent={parent_id[:8]}, child={child_id[:8]}, "
                f"msg={message_id[:8]}, pending={parent_state.pending_count}"
            )

        # Lock released. Run the DEBUG_COMPLETION_INVARIANT check OUTSIDE
        # the per-parent lock so the DB read does not serialize register
        # operations for the same parent. The check is observability only
        # and must not affect the caller's control flow.
        await self._check_invariant(parent_id)

    async def resolve_response(
        self,
        parent_id: str,
        child_id: str,
        message_id: str,
        status: str = STATUS_RESPONDED,
    ) -> bool:
        """Resolve a pending message response (mirrors waiting_for--).

        Must be called from the main event loop (N3 constraint).

        CRITICAL (Fix N2): Set had_error BEFORE popping entry to ensure
        the error flag is set even if the entry is later referenced.

        Args:
            parent_id: The parent instance ID.
            child_id: The child instance ID.
            message_id: The message ID.
            status: Response status — "responded", "error", or "failed".

        Returns:
            True if this resolved the last pending correlation for the parent
            (i.e., triggered correlation.complete), False otherwise.
        """
        correlation_key = f"{child_id}:{message_id}"
        # Capture state under the lock, then release BEFORE doing the
        # shadow-mode DB read. _validate_shadow_mode is a best-effort
        # telemetry check; serializing it under the per-parent lock would
        # block other register/resolve operations on the same parent.
        should_validate = False
        should_complete = False  # W1 fix: defer completion_callback past lock release
        terminal_status: str | None = None
        # H7 fix: capture the ParentCorrelation reference before deletion so
        # the completion_callback path can restore _pending[parent_id] if the
        # callback raises. The dict entry is removed below under the lock,
        # but the local reference (and therefore the object) survives until
        # restored on the failure path. None when correlation is not yet
        # complete — in which case no restoration is needed.
        parent_state_to_restore: ParentCorrelation | None = None
        async with self._get_lock(parent_id):
            if parent_id not in self._pending:
                logger.debug(
                    f"CM resolve: parent={parent_id[:8]} not tracked, "
                    f"child={child_id[:8]}, msg={message_id[:8]}"
                )
                return False

            parent_state = self._pending[parent_id]
            entry = parent_state.pending.get(correlation_key)
            if entry is None:
                logger.debug(
                    f"CM resolve: entry not found for parent={parent_id[:8]}, "
                    f"child={child_id[:8]}, msg={message_id[:8]}"
                )
                return False

            # Fix N2: Set had_error BEFORE popping entry.
            # Conservative: any error/failed response → parent is "error".
            if status in (STATUS_ERROR, "failed"):
                parent_state.had_error = True

            # Mark entry and remove from pending
            entry.status = status
            parent_state.pending.pop(correlation_key, None)

            logger.debug(
                f"CM resolve: parent={parent_id[:8]}, child={child_id[:8]}, "
                f"msg={message_id[:8]}, status={status}, "
                f"remaining={parent_state.pending_count}, had_error={parent_state.had_error}"
            )

            if parent_state.is_complete:
                # Correlation complete for this parent
                terminal_status = self._determine_terminal_status(parent_state)
                logger.info(
                    f"CM correlation complete: parent={parent_id[:8]}, "
                    f"status={terminal_status}, had_error={parent_state.had_error}"
                )
                # Clean up in-memory state while still holding the lock
                # H7 fix: capture the reference for potential restoration on
                # callback exception. Without this, a callback that throws
                # would leave the parent permanently wedged (orphan job in
                # PROCESSING forever) because _pending[parent_id] is already
                # gone before the callback fires.
                parent_state_to_restore = parent_state
                del self._pending[parent_id]
                # S3 fix: also drop the per-parent lock entry to prevent
                # unbounded growth of the _locks dict across many sessions.
                # Safe with None default if the lock was never created.
                self._locks.pop(parent_id, None)
                # W1 fix: defer the completion_callback invocation until AFTER
                # the per-parent lock is released. In Phase 2 the callback
                # performs cascade work (parent status transition) that may
                # call back into CM for the same parent_id — calling it under
                # the lock would deadlock. The lock is no longer needed once
                # _pending has been mutated; the callback is safe to await
                # without it.
                should_complete = True
            else:
                # Not complete yet — schedule shadow validation AFTER releasing lock.
                should_validate = True

        # Lock released. Fire the completion_callback OUTSIDE the per-parent
        # lock (W1 fix) so Phase 2 cascade work (e.g. status transitions that
        # re-enter CM) cannot deadlock on _get_lock(parent_id). State mutation
        # (del _pending[parent_id]) already happened above under the lock.
        if should_complete:
            if self._completion_callback is not None:
                try:
                    await self._completion_callback(parent_id, terminal_status)
                except Exception:
                    # H7 fix: state was cleared under the lock above, so an
                    # exception here would normally leave the parent
                    # permanently wedged (orphan job in PROCESSING forever).
                    # Restore _pending[parent_id] so external retry (or a
                    # subsequent register_message_send) can recover the
                    # completion. The original lock was popped from _locks by
                    # the S3 fix above; _get_lock() lazily recreates it, so
                    # the restoration lock is a fresh per-parent asyncio.Lock
                    # bound to the current event loop (N3 constraint preserved).
                    #
                    # Conditional restore: only restore if no concurrent
                    # register_message_send has already populated
                    # _pending[parent_id] — clobbering would lose new
                    # pending state and leave the parent in an even worse
                    # inconsistency than the original orphan.
                    logger.exception(
                        f"CM completion_callback failed for parent={parent_id[:8]}; "
                        f"attempting to restore _pending for retry"
                    )
                    async with self._get_lock(parent_id):
                        if parent_id not in self._pending:
                            self._pending[parent_id] = parent_state_to_restore
                            logger.warning(
                                f"CM restored _pending[parent={parent_id[:8]}] "
                                f"(had_error={parent_state_to_restore.had_error}, "
                                f"terminal_status={terminal_status}) — "
                                f"callback failure, state preserved for retry"
                            )
                        else:
                            logger.info(
                                f"CM restore skipped for parent={parent_id[:8]} — "
                                f"concurrent register_message_send already "
                                f"populated _pending, leaving as-is"
                            )
            # Observability: run the DEBUG_COMPLETION_INVARIANT check
            # on the completion path too. CM is now 0 for this parent
            # (entry cleared under the lock above); a divergence means
            # the DB is still tracking a non-zero waiting_for, which is
            # exactly the symptom we're watching for. See
            # _check_invariant docstring for the triage decision tree.
            await self._check_invariant(parent_id)
            return True

        # Lock released: do shadow validation outside the per-parent lock
        # to avoid serializing DB reads against register/resolve for the
        # same parent. The CM's pending dict is safe to read here without
        # the lock — the validation is a best-effort, point-in-time check.
        if should_validate:
            await self._validate_shadow_mode(parent_id)
            # Observability: also run the DEBUG_COMPLETION_INVARIANT check
            # (no-op when the flag is OFF). See _check_invariant docstring
            # for the divergence triage decision tree.
            await self._check_invariant(parent_id)
            return False

        # Defensive fallback: neither should_complete nor should_validate
        # was set. This should not be reachable (the lock-protected block
        # above sets one of them when the pending set is non-empty), but
        # if it ever happens, run the invariant check anyway and fall
        # through to the implicit return None.
        await self._check_invariant(parent_id)

    def get_pending_count(self, parent_id: str) -> int:
        """Get the pending correlation count for a parent.

        Args:
            parent_id: The parent instance ID.

        Returns:
            Number of pending correlations, or 0 if parent not tracked.
        """
        if parent_id not in self._pending:
            return 0
        return self._pending[parent_id].pending_count

    def is_complete(self, parent_id: str) -> bool:
        """Check if all correlations are resolved for a parent.

        True if all resolved, or True if parent not tracked (no pending work).

        Args:
            parent_id: The parent instance ID.

        Returns:
            True if no pending correlations.
        """
        if parent_id not in self._pending:
            return True
        return self._pending[parent_id].is_complete

    async def clear_for_instance(self, parent_id: str) -> None:
        """Clear all pending correlations and locks for a terminated instance.

        Called from instance_lifecycle.terminate_instance() to evict stale
        in-memory state when an instance is terminated. Without this,
        a terminated-and-revived instance would inherit its previous
        ``_pending[parent_id]`` entry — ``is_complete()`` would never return
        True again until daemon restart, wedging the parent permanently
        (S3 leak: both dicts grow unbounded across terminate/revive cycles).

        Safe to call even if there are no entries for ``parent_id``: both
        ``.pop(key, None)`` calls are no-ops on missing keys. Mirrors the
        cleanup pattern in ``resolve_response`` (when correlation completes)
        and uses the same per-parent lock as ``register_message_send`` /
        ``resolve_response`` for serialized access.

        Must be called from the main event loop (N3 constraint).

        Args:
            parent_id: The parent instance ID whose CM state should be cleared.
        """
        async with self._get_lock(parent_id):
            self._pending.pop(parent_id, None)
            self._locks.pop(parent_id, None)
            logger.debug(f"CM clear_for_instance: parent={parent_id[:8]}")

    async def rearm_parent(self, parent_id: str) -> bool:
        """Recreate a minimal ``_pending[parent_id]`` slot after deferred finalization.

        C2-PartA fix (premature-completion bug). When
        :meth:`CorrelationManager.resolve_response` brings the pending set to
        zero, it deletes ``_pending[parent_id]`` BEFORE firing the
        ``completion_callback`` (so the H7 exception-restore path can recover
        it). The ``completion_callback`` in turn calls
        :meth:`JobFeedbackObserver._finalize_job`, which runs the
        :meth:`JobFeedbackObserver._finalize_job_db_sync` terminal transition.
        If the ``waiting_for`` gate inside that helper defers (``skip=True`` —
        wave 2 has been spawned), ``_pending[parent_id]`` has already been
        cleared and the per-message-batch callback will NEVER fire again.

        Without this method, multi-wave scenarios where wave 2 is spawned via
        ``job_continue`` or ``watch_job`` (paths that register new CM
        correlations but may not use the ``send_message`` register sequence)
        can wedge the job in PROCESSING forever: wave 2 children complete,
        :func:`notify_corr_resolve` calls land on a parent with no entry,
        ``resolve_response`` returns ``False`` silently, and no callback fires
        when the last wave 2 child resolves.

        This method recreates an empty ``_pending[parent_id]`` slot so
        subsequent :meth:`register_message_send` and
        :meth:`resolve_response` calls find the parent and track the next
        wave. When all wave 2 correlations resolve, the CM callback fires
        again and ``_finalize_job`` runs the terminal transition (with
        ``waiting_for == 0`` this time).

        Safe to call when an entry already exists: returns ``False`` without
        clobbering existing pending state (a concurrent
        ``register_message_send`` may have populated it). The lock is acquired
        to serialize against concurrent ``register_message_send`` for the same
        parent — same pattern as :meth:`clear_for_instance`.

        Must be called from the main event loop (N3 constraint). Callers
        that run inside the ``completion_callback`` (i.e.
        :meth:`JobFeedbackObserver.handle_correlation_complete` → ``_finalize_job``)
        must schedule this via ``asyncio.create_task()`` to respect the N4
        constraint (re-entering CM for the same ``parent_id`` from the
        callback would deadlock).

        Args:
            parent_id: The parent instance ID whose CM state should be re-armed.

        Returns:
            ``True`` if a fresh empty entry was created, ``False`` if an
            entry already existed (no-op).
        """
        async with self._get_lock(parent_id):
            if parent_id in self._pending:
                existing_count = self._pending[parent_id].pending_count
                logger.debug(
                    f"CM rearm_parent: parent={parent_id[:8]} already tracked "
                    f"(pending_count={existing_count}), skipping re-arm"
                )
                return False
            self._pending[parent_id] = ParentCorrelation(parent_id=parent_id)
            logger.info(
                f"CM rearm_parent: recreated empty _pending[parent="
                f"{parent_id[:8]}] after deferred finalization (waiting_for>0)"
            )
            return True

    # -------------------------------------------------------------------------
    # Shadow Validation (Phase 1)
    # -------------------------------------------------------------------------

    def _is_debug_invariant_enabled(self) -> bool:
        """Check if the ``DEBUG_COMPLETION_INVARIANT`` config flag is ON.

        Reads the flag from ``daemon.config.load_config()`` on first call
        and caches the result on the instance. The flag is a runtime
        operator toggle (config-level) and is not expected to change
        without a daemon restart, so reading it once is safe and avoids
        the cost of re-parsing ``config.yaml`` on every register/resolve.

        Tests that need to flip the flag dynamically should set
        ``self._debug_invariant_enabled`` directly on the CM instance —
        this is the supported override path (see
        ``tests/test_correlation_manager.py`` for examples).

        Returns:
            ``True`` if the flag is ON, ``False`` otherwise (including
            when config loading fails — fail closed to avoid spurious
            DB reads in environments where config is unavailable).
        """
        if self._debug_invariant_enabled is not None:
            return self._debug_invariant_enabled
        try:
            from ..config import load_config

            config = load_config()
            self._debug_invariant_enabled = bool(
                config.job_system.debug_completion_invariant
            )
        except Exception as e:
            # Config load failure — fail closed. The check is
            # observability only and a config issue should not
            # cause every register/resolve to error.
            logger.debug(
                f"CM invariant: failed to load config for "
                f"debug_completion_invariant check: {e}"
            )
            self._debug_invariant_enabled = False
        return self._debug_invariant_enabled

    async def _check_invariant(self, parent_id: str) -> None:
        """Check CM pending count vs DB ``waiting_for`` and log divergence.

        Phase A observability check, gated on the ``DEBUG_COMPLETION_INVARIANT``
        config flag (``daemon.config.JobSystemConfig.debug_completion_invariant``).
        When the flag is OFF, this method is a no-op. When ON, it reads the
        current ``waiting_for`` from the DB and compares with the CM's
        in-memory pending count. Disagreement is logged at WARNING with the
        structured prefix ``event=CM_WAITING_FOR_DIVERGENCE`` so log
        aggregators can alert on the key directly.

        Triage decision tree (operational runbook)
        ------------------------------------------

        * **<10 divergences/hour** → investigate in next sprint.
          Background noise from timing of the register/resolve pair
          relative to the DB write — accept this as long-running
          operational reality.
        * **10–100 divergences/hour** → page on-call, check for new
          ``waiting_for`` mutation sites. The CM is the SOLE completion
          authority in Phase A; any site that mutates ``waiting_for``
          outside the CM hook chain is a bug.
        * **>100 divergences/hour** → flip the kill switch
          (``USE_LEGACY_WAITING_FOR_CASCADE=ON``) IMMEDIATELY and
          investigate. Indicates a serious drift between CM and DB —
          better to fall back to the legacy cascade than risk
          premature completions.

        **Acceptable divergence rate**: <10/hour (background noise).
        **Zero is the goal** — investigate any divergence eventually;
        <10/hour is the operational threshold, not a target.

        Implementation contract
        -----------------------

        * **Non-blocking**: the DB read is wrapped in
          ``asyncio.to_thread`` to avoid blocking the main event loop.
        * **Never raises**: any exception (config load failure, DB read
          failure, instance missing ``waiting_for``) is swallowed and
          logged at DEBUG. The check is observability only and MUST NOT
          affect the caller's control flow.
        * **No decisions made on the comparison**: the divergence is
          logged, nothing else. The check is a tripwire, not a guard.

        Must be called from the main event loop (N3 constraint).

        Args:
            parent_id: The parent instance ID to validate.
        """
        if not self._is_debug_invariant_enabled():
            return

        try:
            # Read DB waiting_for (sync, wrap in to_thread) — same
            # pattern as _validate_shadow_mode. Single-row PK lookup,
            # cheap.
            instance = await asyncio.to_thread(
                self._instance_repo.get, parent_id
            )
        except Exception as e:
            # DB read failure — log at DEBUG and continue. The check
            # is observability only and must not affect the calling
            # control flow.
            logger.debug(
                f"CM invariant: DB read failed for parent={parent_id[:8]}: {e}"
            )
            return

        if instance is None:
            # Parent not in DB — can't compare. Could be a transient
            # state during instance creation/termination. Log at DEBUG.
            logger.debug(
                f"CM invariant: parent={parent_id[:8]} not in DB, "
                f"skipping divergence check"
            )
            return

        db_waiting = getattr(instance, "waiting_for", None)
        if db_waiting is None:
            # Defensive: instance object doesn't expose waiting_for.
            # This should never happen with a real Instance model, but
            # the mock-friendly signature is robust to it.
            logger.debug(
                f"CM invariant: parent={parent_id[:8]} instance has no "
                f"waiting_for attribute, skipping divergence check"
            )
            return

        cm_count = self.get_pending_count(parent_id)

        if cm_count != db_waiting:
            # Structured WARNING — operators alert on the event= key.
            # Field order in the message matches the structured fields
            # so log aggregators can extract them consistently.
            logger.warning(
                f"event=CM_WAITING_FOR_DIVERGENCE parent={parent_id[:8]} "
                f"cm_pending_count={cm_count} db_waiting_for={db_waiting}"
            )

    async def rebuild_from_db(self) -> None:
        """Reconstruct pending state from database after daemon restart.

        Crash-Recovery Contract
        -----------------------
        Rebuild is the SOLE mechanism for recovering ``_pending`` state after
        a daemon restart. The CM is becoming the authoritative completion
        tracker (Phase 3+), so the correctness of this method directly
        determines whether the system can survive a crash without wedging
        jobs in PROCESSING forever.

        **What rebuild does** (per-parent, for every ``parent`` in the DB
        with ``waiting_for > 0``):

          (a) Reads ``waiting_for > 0`` parents via
              ``get_all_with_waiting_for()`` (single query).
          (b) For each parent, reads its children via ``get_children()``
              (one query per parent).
          (c) For each parent, reads pending messages across all of its
              children via ``get_pending_for_instances(child_ids)`` (single
              batched query — collapses the old ``1 + P + P*C*3`` pattern
              down to ``1 + P + 1`` per parent).
          (d) Populates ``_pending[parent_id]`` from the
              ``(child_id, message_id)`` tuples returned. The
              ``correlation_key`` is ``f"{child_id}:{message_id}"`` —
              identical to what :meth:`register_message_send` produces, so
              a subsequent :meth:`resolve_response` with the real UUID
              will find the rebuilt entry.

        **OVERWRITE vs MERGE semantics**:

          * **Top-level (``self._pending = {}``) is OVERWRITE.** Stale
            entries from before the crash are wiped before the rebuild loop
            starts. This is the W2 fix: previously the method merged new
            entries into the existing dict, which could re-add stale or
            duplicate entries if a ``register_message_send`` landed
            between ``start()`` and ``rebuild_from_db()``.
          * **Per-parent write is MERGE.** When the rebuild loop reaches a
            parent, it acquires the per-parent ``asyncio.Lock`` and
            checks ``self._pending[parent_id]``. If a concurrent
            ``register_message_send`` already populated the slot (after
            the top-level clear but before the rebuild loop reached this
            parent), the rebuild MERGES the DB-backed ``(child, msg)``
            pairs into the existing ``ParentCorrelation.pending`` instead
            of replacing the whole object. This preserves the
            concurrent register's entries. If the slot is empty (the
            common case), a fresh ``ParentCorrelation`` is created.

        **Concurrency safety during rebuild**:

          * Concurrent ``register_message_send`` for a parent that
            arrives AFTER the top-level clear is preserved by the merge
            semantics above. The per-parent ``asyncio.Lock`` serializes
            the concurrent register against the rebuild's per-parent
            write.
          * Concurrent ``register_message_send`` that arrives BEFORE the
            top-level clear (i.e. before rebuild starts) will have its
            ``_pending[parent_id]`` entry wiped by the clear. This is
            acceptable: the CM hook contract is "DB write first, then
            register" (see :func:`notify_corr_register`), so any
            completed register will be reflected in the DB and picked up
            by the rebuild query. A register whose DB write was still
            in-flight when the clear ran is effectively dropped — this
            is a known limitation of the current architecture and is
            out of scope for this method.

        **Orphan count (zero children, ``waiting_for > 0``)**:

          If a parent has ``waiting_for > 0`` but no children (or no
          pending messages), the CM tracks nothing for that parent. The
          rebuild logs a mismatch warning (``DB waiting_for=N, CM
          found=0``). This is the correct behaviour for an inconsistent
          DB state — the CM cannot fabricate pending entries that
          aren't there. External recovery code is responsible for
          reconciling the orphan count (e.g. by clearing the stale
          ``waiting_for`` or by terminating the wedged parent).

        **Race windows outside rebuild's control**:

          * The top-level clear (``self._pending = {}``) is NOT held
            under any lock. This is intentional: the per-parent locks
            protect ``_pending[parent_id]`` writes, and the clear
            itself is an idempotent dict reassignment. A concurrent
            ``register_message_send`` that is mid-write when the clear
            runs will land its entry in the new dict (or the old dict,
            which is then dropped — see the "before clear" case above).
          * Rebuild is NOT re-entrant. Do not call it from inside a
            :meth:`register_message_send` or :meth:`resolve_response`
            callback. ``start()`` is the only production caller and is
            invoked exactly once during daemon startup, before any
            EventBus traffic.

        **Logging**:

          * INFO: "rebuild_from_db: starting..." at start,
            "rebuild_from_db complete: tracked_parents=X,
            mismatch_warnings=Y" at end.
          * WARNING: per-parent mismatch when CM count ≠ DB
            ``waiting_for``. These are expected in shadow mode and are
            the data we want to capture.
          * DEBUG: per-parent match.

        Must be called from the main event loop (N3 constraint).
        """
        logger.info("CM rebuild_from_db: starting...")

        # W2 fix: Clear _pending before rebuilding. Previously this method
        # merged new entries into the existing dict, which could re-add
        # stale or duplicate entries if a register_message_send landed
        # between start() and rebuild_from_db(). start() is the only
        # caller in production and is called before any EventBus traffic,
        # so replacing the dict is safe; subsequent register/resolve
        # calls must go through the per-parent lock and will see the
        # rebuilt state.
        #
        # The clear is intentionally NOT held under any lock — it is an
        # idempotent dict reassignment, and the per-parent locks below
        # protect per-parent writes. See the crash-recovery contract in
        # the docstring above for the full concurrency model.
        self._pending = {}

        # Step 1: Single query for all parents with waiting_for > 0.
        parents = await asyncio.to_thread(self._instance_repo.get_all_with_waiting_for)

        rebuild_mismatch_warnings = 0

        for parent in parents:
            parent_id = parent.instance_id
            db_waiting = parent.waiting_for

            # Step 2: Get children of this parent (one query per parent).
            children = await asyncio.to_thread(
                self._instance_repo.get_children, parent_id
            )
            child_ids = [c.instance_id for c in children]

            # Step 3: Single batched query for pending messages across all
            # children. Returns list[(child_instance_id, message_id)].
            pending_pairs: list[tuple[str, str]] = []
            if child_ids:
                pending_pairs = await asyncio.to_thread(
                    self._msg_repo.get_pending_for_instances,
                    child_ids,
                )

            # Step 4: Build correlation entries from (child_id, message_id) tuples.
            #
            # Crash-recovery contract: MERGE into the existing slot if a
            # concurrent register_message_send populated it after the
            # top-level clear above. This is the additive complement to
            # the W2 OVERWRITE-at-the-top: stale pre-clear entries are
            # wiped by the clear, but a register that landed after the
            # clear is preserved. If the slot is empty, create fresh.
            if pending_pairs:
                async with self._get_lock(parent_id):
                    # MERGE semantics: respect any ParentCorrelation that
                    # a concurrent register_message_send already created.
                    if parent_id not in self._pending:
                        self._pending[parent_id] = ParentCorrelation(
                            parent_id=parent_id
                        )
                    parent_state = self._pending[parent_id]
                    for child_id, message_id in pending_pairs:
                        correlation_key = f"{child_id}:{message_id}"
                        # Only add the DB-backed entry if no entry for
                        # this (child, msg) pair exists yet. A concurrent
                        # register may have added the same pair (or a
                        # pair whose DB write hasn't been read back yet);
                        # in either case we must not clobber the
                        # existing PendingResponse, which represents a
                        # live correlation that the caller expects to
                        # resolve later.
                        if correlation_key not in parent_state.pending:
                            parent_state.pending[correlation_key] = PendingResponse(
                                parent_id=parent_id,
                                child_id=child_id,
                                message_id=message_id,
                                created_at=time.monotonic(),
                                status=STATUS_PENDING,
                            )
                    # No `self._pending[parent_id] = parent_state` here:
                    # we either created a fresh object (assigned above)
                    # or we mutated the existing object in place. The
                    # reference in self._pending[parent_id] is already
                    # correct.

            # W2 fix: Compare found count with DB waiting_for UNDER the
            # per-parent lock. Reading self._pending outside the lock was a
            # TOCTOU hazard (concurrent register_message_send could mutate
            # pending between rebuild and the count check).
            async with self._get_lock(parent_id):
                cm_count = self.get_pending_count(parent_id)
            if cm_count != db_waiting:
                logger.warning(
                    f"CM rebuild mismatch: parent={parent_id[:8]}, "
                    f"DB waiting_for={db_waiting}, CM found={cm_count}"
                )
                rebuild_mismatch_warnings += 1
            else:
                logger.debug(
                    f"CM rebuild match: parent={parent_id[:8]}, count={cm_count}"
                )

        logger.info(
            f"CM rebuild_from_db complete: "
            f"tracked_parents={len(self._pending)}, "
            f"mismatch_warnings={rebuild_mismatch_warnings}"
        )

    async def _validate_shadow_mode(self, parent_id: str) -> None:
        """Compare CM pending count with DB waiting_for and log the result.

        Uses rate-limited logging: first 100 mismatches/minute logged individually,
        then summary every 5 minutes. Matches are similarly rate-limited.

        Must be called from the main event loop (N3 constraint).

        Args:
            parent_id: The parent instance ID to validate.
        """
        # Read DB waiting_for (sync, wrap in to_thread)
        instance = await asyncio.to_thread(self._instance_repo.get, parent_id)
        if instance is None:
            logger.debug(f"CM shadow: parent={parent_id[:8]} not in DB")
            return

        db_waiting = instance.waiting_for
        cm_count = self.get_pending_count(parent_id)

        if cm_count != db_waiting:
            if self._should_log_mismatch():
                logger.warning(
                    f"CM shadow mismatch: parent={parent_id[:8]}, "
                    f"DB waiting_for={db_waiting}, CM pending={cm_count}"
                )
            else:
                # Count but don't log individually
                pass
        else:
            if self._should_log_match():
                logger.debug(
                    f"CM shadow match: parent={parent_id[:8]}, count={cm_count}"
                )

    def _should_log_mismatch(self) -> bool:
        """Rate-limit mismatch logs: 100/minute, then summary every 5 min.

        Returns:
            True if this mismatch should be logged now.
        """
        now = time.monotonic()

        # Reset window every minute. Mismatch window is independent of
        # the match window — a mismatch burst must not starve match logs.
        if now - self._mismatch_window_start >= _RATE_LIMIT_WINDOW_SEC:
            self._mismatch_window_start = now
            self._mismatch_count = 0

        if self._mismatch_count < _RATE_LIMIT_CAP:
            self._mismatch_count += 1
            return True

        # Over 100/min — log summary every 5 min
        if now - self._last_mismatch_summary >= _RATE_LIMIT_SUMMARY_INTERVAL_SEC:
            self._last_mismatch_summary = now
            logger.warning(
                f"CM shadow: {self._mismatch_count} mismatches in last "
                f"{_RATE_LIMIT_SUMMARY_INTERVAL_SEC / 60:.0f} min "
                f"(rate limit active, showing summary only)"
            )
            return True

        return False

    def _should_log_match(self) -> bool:
        """Rate-limit match logs: 100/minute, then summary every 5 min.

        Returns:
            True if this match should be logged now.
        """
        now = time.monotonic()

        # Reset window every minute. Match window is independent of
        # the mismatch window — a match burst must not starve mismatch logs.
        if now - self._match_window_start >= _RATE_LIMIT_WINDOW_SEC:
            self._match_window_start = now
            self._match_count = 0

        if self._match_count < _RATE_LIMIT_CAP:
            self._match_count += 1
            return True

        # Over 100/min — log summary every 5 min
        if now - self._last_match_summary >= _RATE_LIMIT_SUMMARY_INTERVAL_SEC:
            self._last_match_summary = now
            logger.info(
                f"CM shadow: {self._match_count} matches in last "
                f"{_RATE_LIMIT_SUMMARY_INTERVAL_SEC / 60:.0f} min "
                f"(rate limit active, showing summary only)"
            )
            return True

        return False

    # -------------------------------------------------------------------------
    # Lifecycle (start/stop)
    # -------------------------------------------------------------------------

    async def start(self) -> None:
        """Start the CorrelationManager.

        Subscribes to EventBus (if provided), rebuilds state from DB,
        and starts the background event processing task.

        Must be called from the main event loop (N3 constraint).
        """
        if self._running:
            logger.warning("CM already running")
            return

        logger.info("CM starting...")
        self._running = True
        self._mismatch_window_start = time.monotonic()
        self._match_window_start = time.monotonic()

        # Step 1: Subscribe to EventBus for inbound lifecycle events (shadow validation).
        # Use large queue size (5000) to mitigate silent-drop risk (N1).
        # The default global_queue_size is 1000 — a completion event storm
        # could overflow it and silently drop events.
        if self._event_bus is not None:
            self._subscriber_id = f"cm_{id(self)}"
            self._event_queue = self._event_bus.subscribe_all(
                subscriber_id=self._subscriber_id,
                maxsize=_EVENT_BUS_QUEUE_SIZE,
            )
            logger.info(
                f"CM subscribed to EventBus as '{self._subscriber_id}' "
                f"(queue_size={_EVENT_BUS_QUEUE_SIZE})"
            )

        # Step 2: Rebuild from DB on startup
        try:
            await self.rebuild_from_db()
        except Exception as e:
            logger.error(f"CM rebuild_from_db failed: {e}")

        # Step 3: Start background event processing task
        self._event_task = asyncio.create_task(self._event_loop())
        logger.info(f"CM started, tracking {len(self._pending)} parent(s)")

    async def stop(self) -> None:
        """Stop the CorrelationManager gracefully.

        Cancels the event processing task and cleans up EventBus subscription.

        Must be called from the main event loop (N3 constraint).
        """
        if not self._running:
            return

        logger.info("CM stopping...")
        self._running = False

        # Cancel event task
        if self._event_task is not None:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
            self._event_task = None

        # Unsubscribe from EventBus
        if self._event_bus is not None and self._subscriber_id is not None:
            self._event_bus.unsubscribe_all(self._subscriber_id)
            self._subscriber_id = None
            self._event_queue = None

        logger.info("CM stopped")

    async def _event_loop(self) -> None:
        """Process inbound lifecycle events from EventBus.

        Phase 1 (shadow mode): logs received events for shadow validation.
        This is a background task that runs until stop() is called.

        Runs on the main event loop (N3 constraint).

        NOTE: Events may be silently dropped if the EventBus queue fills up
        (see _EVENT_BUS_QUEUE_SIZE and the N1 risk documented in start()).
        """
        logger.info("CM event_loop started")
        while self._running:
            try:
                if self._event_queue is None:
                    await asyncio.sleep(1.0)
                    continue

                try:
                    event = await asyncio.wait_for(
                        self._event_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.1)
                    continue

                # Extract event details
                instance_id = event.get("instance_id", "")
                event_type = event.get("event_type", "")

                logger.debug(
                    f"CM event received: instance={instance_id[:8] if instance_id else '?'}, "
                    f"type={event_type}"
                )

                # Phase 1: Log received events for shadow validation awareness.
                # In Phase 2+, this would trigger resolve_response calls.

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"CM event_loop error: {e}")
                await asyncio.sleep(1.0)

        logger.info("CM event_loop exited")


# -------------------------------------------------------------------------
# Module-level singleton for dependency injection
# -------------------------------------------------------------------------

_correlation_manager: CorrelationManager | None = None


def get_correlation_manager() -> CorrelationManager | None:
    """Get the module-level CorrelationManager instance.

    Returns:
        The singleton CorrelationManager instance, or None if not initialized.
        Callers should treat None as "CM not wired up — skip hooks".
    """
    return _correlation_manager


def set_correlation_manager(cm: CorrelationManager | None) -> None:
    """Set the module-level CorrelationManager instance.

    Args:
        cm: The CorrelationManager instance, or None to clear.
    """
    global _correlation_manager
    _correlation_manager = cm
    if cm is not None:
        logger.info("CorrelationManager registered (shadow mode)")
    else:
        logger.info("CorrelationManager unregistered")


# -------------------------------------------------------------------------
# Shadow-mode safe hook helpers
# -------------------------------------------------------------------------
#
# These wrappers exist so the call sites in send_message, child_reports, and
# error_reporting can fire-and-forget correlation updates without ever
# affecting control flow. They are intentionally tolerant of:
#   * CM not initialized (returns None from get_correlation_manager)
#   * CM raising any exception (logged at WARNING, swallowed)
#
# In shadow mode the CM observes and validates waiting_for; it never
# participates in cascade decisions or parent status transitions.

async def notify_corr_register(
    parent_id: str,
    child_id: str,
    message_id: str,
) -> None:
    """Authoritative resolution hook: register a message send in the CM.

    Adds the (child_id, message_id) pair to the CM's per-parent pending
    set. The CM is the single source of truth for parent completion
    (Phase 3) — when the matching ``notify_corr_resolve`` brings the
    pending set to zero, the CM synchronously fires
    ``handle_correlation_complete`` (registered as
    ``completion_callback``), which transitions both the parent JOB
    and the parent INSTANCE to terminal.

    Wraps in try/except so a CM failure NEVER affects the calling
    control flow (the send_message path must continue even if CM is
    broken — the legacy ``waiting_for`` cascade in the inline code
    below is the graceful-degradation fallback).

    Args:
        parent_id: The parent instance ID that is waiting for the
            child response.
        child_id: The child instance ID the message is being sent to.
        message_id: The message ID used to correlate the eventual
            response with this send.
    """
    cm = get_correlation_manager()
    if cm is None:
        return
    try:
        await cm.register_message_send(parent_id, child_id, message_id)
    except Exception as e:
        logger.warning(
            f"CM hook: register_message_send failed "
            f"(parent={parent_id[:8]}, child={child_id[:8]}, msg={message_id[:8]}): {e}"
        )


async def notify_corr_resolve(
    parent_id: str,
    child_id: str,
    message_id: str,
    status: str = STATUS_RESPONDED,
) -> None:
    """Authoritative resolution hook: resolve a message response in the CM.

    Removes the (child_id, message_id) entry from the CM's per-parent
    pending set. The CM is the single source of truth for parent completion
    (Phase 3) — when the pending set reaches zero, the CM synchronously fires
    ``handle_correlation_complete`` (registered as ``completion_callback``),
    which transitions both the parent JOB and the parent INSTANCE to
    terminal. The chosen ``terminal_status`` is "completed" if all resolved
    correlations had status="responded", otherwise "error" (conservative rule).

    Must be called from the main asyncio event loop (N3 constraint).

    Wraps in try/except so a CM failure NEVER affects the calling
    control flow (the child_reports / error_reporting paths must continue
    even if CM is broken — the legacy ``waiting_for`` cascade is the
    graceful-degradation fallback).

    Args:
        parent_id: The parent instance ID (waiting for the response).
        child_id: The child instance ID that just responded/failed.
        message_id: The message ID whose response this resolves.
        status: "responded" for normal completion, "error" for failures.
    """
    cm = get_correlation_manager()
    if cm is None:
        return
    try:
        await cm.resolve_response(parent_id, child_id, message_id, status=status)
    except Exception as e:
        logger.warning(
            f"CM hook: resolve_response failed "
            f"(parent={parent_id[:8]}, child={child_id[:8]}, msg={message_id[:8]}, "
            f"status={status}): {e}"
        )


async def notify_corr_rearm(parent_id: str) -> None:
    """Authoritative resolution hook: re-arm CM ``_pending[parent_id]``.

    C2-PartA fix (premature-completion bug). After
    :func:`_finalize_job_db_sync` defers finalization (``skip=True`` due to
    ``waiting_for > 0``), the CM's ``_pending[parent_id]`` has been cleared
    by the prior :func:`resolve_response` (which ``del``'s the entry inside
    the per-parent lock before firing the completion_callback). Call this
    hook to recreate an empty slot so wave 2 children's
    :func:`notify_corr_resolve` calls find the parent and track the next
    wave. Without this, jobs in multi-wave scenarios can wedge in PROCESSING
    forever — particularly when wave 2 is spawned via ``job_continue`` or
    ``watch_job`` (paths that register via CM independently of
    ``send_message``).

    Follows the same fire-and-forget pattern as :func:`notify_corr_register`
    and :func:`notify_corr_resolve`: no-op if the CM is not initialized, and
    any exception is logged at WARNING and swallowed so the calling control
    flow is never affected.

    Must be called from the main asyncio event loop (N3 constraint). The
    intended call site (:meth:`JobFeedbackObserver._finalize_job` on
    ``skip=True``) schedules this via ``asyncio.create_task()`` to respect
    the N4 constraint — re-entering CM for the same ``parent_id`` from
    inside the completion_callback would deadlock.

    Args:
        parent_id: The parent instance ID to re-arm.
    """
    cm = get_correlation_manager()
    if cm is None:
        return
    try:
        await cm.rearm_parent(parent_id)
    except Exception as e:
        logger.warning(
            f"CM hook: rearm_parent failed (parent={parent_id[:8]}): {e}"
        )


# -------------------------------------------------------------------------
# FastAPI lifespan helpers
# -------------------------------------------------------------------------
#
# These helpers encapsulate the CorrelationManager startup/shutdown logic so
# that daemon/api.py does not need to inline the wiring. A CM failure must
# NEVER block daemon startup — it is logged at WARNING and the daemon
# continues without it. Phase 1 runs in shadow mode (logging only).


async def init_correlation_manager(
    app, manager, completion_callback=None
) -> None:
    """Initialize and start the CorrelationManager.

    Phase 1: shadow mode (validation only).
    Phase 2: the CM fires ``completion_callback`` when all message responses
    for a parent are resolved. The callback is the authoritative terminal
    transition path for the ``JobFeedbackObserver`` — it replaces the legacy
    ``waiting_for``-based check in ``_process_event`` and eliminates Race #1
    (the TOCTOU window between reading ``waiting_for`` and acting on it).

    If ``completion_callback`` is ``None`` (legacy callers), a shadow logger
    is used that just records the event. Production must pass the observer's
    ``handle_correlation_complete`` method to wire Phase 2 end-to-end.

    The CM observes and validates the existing waiting_for counter
    without affecting any control flow. A CM failure is logged at WARNING
    and the daemon continues without it.

    Args:
        app: The FastAPI app (used to stash the instance on app.state for
            later shutdown).
        manager: The InstanceManager (provides repositories and EventBus).
        completion_callback: Optional async callback
            ``async def callback(parent_id, terminal_status) -> None``.
            When provided, called by CM when a parent's correlations fully
            resolve. When ``None``, falls back to a shadow logger.
    """
    async def _shadow_completion_callback(parent_id: str, terminal_status: str) -> None:
        logger.info(
            f"CM shadow: correlation.complete(parent={parent_id[:8]}..., "
            f"status={terminal_status})"
        )

    # Use the caller-supplied callback (Phase 2) or fall back to the shadow
    # logger (legacy / tests). Both are safe: the CM swallows callback
    # exceptions and logs them.
    cb = completion_callback if completion_callback is not None else _shadow_completion_callback

    try:
        correlation_manager = CorrelationManager(
            instance_repository=manager._instance_repository,
            message_queue_repository=manager._queue_repository,
            completion_callback=cb,
            event_bus=manager._event_bus,
        )
        set_correlation_manager(correlation_manager)
        await correlation_manager.start()
        app.state._correlation_manager = correlation_manager
        logger.info("CorrelationManager started")
    except Exception as e:
        logger.warning(
            f"Failed to start CorrelationManager (continuing without it): {e}"
        )
        set_correlation_manager(None)


async def shutdown_correlation_manager(app) -> None:
    """Stop the CorrelationManager (shadow observer).

    Must run BEFORE manager.shutdown() — manager.shutdown() tears down the
    EventBus, and CM holds a subscription on it. A stop failure is logged at
    WARNING and swallowed so the rest of the shutdown sequence proceeds.
    """
    if hasattr(app.state, '_correlation_manager') and app.state._correlation_manager:
        try:
            await app.state._correlation_manager.stop()
        except Exception as e:
            logger.warning(f"Error stopping CorrelationManager: {e}")
        set_correlation_manager(None)
