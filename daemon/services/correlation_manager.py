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
                except Exception as e:
                    logger.error(
                        f"CM completion_callback failed for parent={parent_id[:8]}: {e}"
                    )
            return True

        # Lock released: do shadow validation outside the per-parent lock
        # to avoid serializing DB reads against register/resolve for the
        # same parent. The CM's pending dict is safe to read here without
        # the lock — the validation is a best-effort, point-in-time check.
        if should_validate:
            await self._validate_shadow_mode(parent_id)
            return False

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

    # -------------------------------------------------------------------------
    # Shadow Validation (Phase 1)
    # -------------------------------------------------------------------------

    async def rebuild_from_db(self) -> None:
        """Reconstruct pending state from database after daemon restart.

        Uses dedicated repository methods:
          * ``get_all_with_waiting_for()`` — only parents with waiting_for > 0
          * ``get_pending_for_instances(child_ids)`` — single batched query
            for pending messages across all children of a parent.

        This collapses the previous 1 + P + P×C×3 query pattern down to
        1 + P + 1 per parent.

        Logs a warning if the rebuilt CM count does not match the DB
        ``waiting_for`` value (mismatches are expected during shadow mode
        and are the data we want to capture).

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
            if pending_pairs:
                async with self._get_lock(parent_id):
                    # W2 fix: _pending was cleared above, so this slot is
                    # always fresh — no need to check for an existing entry.
                    parent_state = ParentCorrelation(parent_id=parent_id)
                    for child_id, message_id in pending_pairs:
                        correlation_key = f"{child_id}:{message_id}"
                        parent_state.pending[correlation_key] = PendingResponse(
                            parent_id=parent_id,
                            child_id=child_id,
                            message_id=message_id,
                            created_at=time.monotonic(),
                            status=STATUS_PENDING,
                        )
                    self._pending[parent_id] = parent_state

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
    pending set. The CM is the single source of truth for parent
    completion (Phase 3) — when the pending set reaches zero, the CM
    synchronously fires ``handle_correlation_complete`` (registered as
    ``completion_callback``), which transitions both the parent JOB
    and the parent INSTANCE to terminal. The chosen ``terminal_status``
    is "completed" if all resolved correlations had status="responded",
    otherwise "error" (conservative rule).

    Must be called from the main asyncio event loop (N3 constraint).

    Wraps in try/except so a CM failure NEVER affects the calling
    control flow (the child_reports / error_reporting paths must
    continue even if CM is broken — the legacy ``waiting_for`` cascade
    is the graceful-degradation fallback).

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
