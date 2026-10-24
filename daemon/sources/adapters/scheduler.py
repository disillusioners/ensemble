"""Scheduler adapter for triggering agents on schedule.

## DST semantics (ADR-008, pinned by phase-1 §Task 8)

The scheduler delegates DST handling to ``croniter>=3.0.0`` (pinned in
``pyproject.toml:25``) for the **cron path**. For the **one-shot path**,
naive local times are anchored via the canonical
:func:`daemon.util.tz.anchor_local_to_utc` helper (architecture §4.2;
phase-1 §Task 9) — aware→trust, fold→0 (first occurrence), gap→shift
forward + warning. One DST rule for the whole feature: croniter's
documented default (skip-the-gap on spring-forward, first-occurrence on
fall-back ambiguity).

For daily/weekly cron schedules, the schedule key is interpreted in the
schedule's ``timezone`` (existing config key, read at ``__init__``
lines ~121–126); croniter returns the next fire as an aware datetime.

**Spring-forward gap** (e.g. America/New_York 02:30 on the day DST
starts): croniter skips the gap and fires at the next valid local time
(e.g. 03:30 EDT). Naive one-shot anchor: shifts forward by the gap and
emits a loud ``shifted-forward from nonexistent local time ... (gap)``
warning at the adapter boundary.

**Fall-back ambiguity** (e.g. America/New_York 01:30 on the day DST
ends): croniter picks the first occurrence (EDT, UTC-4). Naive one-shot
anchor: uses ``fold=0`` — the pre-DST offset, matching croniter's
default; one semantic across both paths.

Phase-5 tests ``TestDstSemantics`` parameterize over BOTH paths (cron
+ one-shot anchor) and assert this documented behavior. No
``pytest.skip``.

See ``.agents/shared/planning/scheduled-tasks/decisions.md`` ADR-008 and
``architecture-recommendation.md`` §4 for the full rationale.

## Phase-1 D4 idempotency contract (ADR-004)

One-time schedules emit ``idempotency_key=f"scheduler:{source_id}:{self._run_at.isoformat()}"``
to ``enqueue_message_job`` (gated to ``SCHEDULE_TYPE_ONE_TIME`` so cron
fires keep minting fresh JobItems). The key MUST be derived from
``self._run_at`` (the configured run_at parsed once in ``__init__``),
NOT from ``next_trigger`` — the latter returns ``now`` for past-due
one-shots and would mutate the key on every cycle.

## Phase-1 D3 lateness cap (ADR-003)

When a one-shot's ``run_at`` is past-due beyond
``SchedulingConfig.one_shot_max_lateness_seconds``, the adapter writes a
``schedule_executions`` row with ``status='skipped'`` and
``error_message='past_lateness_cap: lateness={X}s, cap={Y}s'`` and does
NOT dispatch a JobItem. The schedule remains armed (NOT disabled) so the
operator can update ``run_at`` and re-fire.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable, Awaitable
from zoneinfo import ZoneInfo
from croniter import croniter
from croniter import CroniterBadCronError

from ..base import (
    IncomingMessage,
    MessageSourceAdapter,
    OutgoingMessage,
    SourceConfig,
    SourceStatus,
)
from daemon.models import SchedulerInstanceMode
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.message_queue.models import MessageStatus
from daemon.repositories.source.models import ExecutionStatus
from daemon.repositories.task.models import TaskStatus
from daemon.registry import get_registry
from daemon.sources.mapper import BoundCallerRefusal
from daemon.constants import (
    SCHEDULER_SEMAPHORE_TIMEOUT_S,
    SCHEDULER_MANUAL_SEMAPHORE_TIMEOUT_S,
    SCHEDULER_GRACE_PERIOD_S,
    SCHEDULER_ERROR_RETRY_S,
    SCHEDULER_DRAIN_CHECK_S,
    SCHEDULER_DEFAULT_MAX_CONCURRENT,
    SCHEDULER_DEFAULT_PRIORITY,
)

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    from daemon.services.job_queue_service import JobQueueService
    from daemon.repositories.source.repository import SourceRepository as SourceRepositoryType

logger = logging.getLogger(__name__)

#: Sentinel: a Task lookup that RAISED (the DB did not answer).  It is
#: NOT the same as "no Task exists" (W3) — the breach handler must not
#: read an unknown as "nothing is in flight" (that would write a FALSE
#: failed row for a possibly-live delivery).
_TASK_LOOKUP_FAILED = object()

#: Sentinel: a LIVENESS read that RAISED (the DB did not answer).  Same
#: discipline as ``_TASK_LOOKUP_FAILED`` and the SAME consequence: an
#: unknown liveness is NOT "not live" — treating it as a confident
#: negative would skip the coalescing and open a FRESH GENERATION while
#: the pending delivery may still be live (a second live delivery).
_LIVENESS_UNKNOWN = object()

#: Prefix of every minted DEFERRED execution id — the substitution marker
#: distinguishing a deferred audit row from a direct dispatch's id (the
#: ``_deferred_metadata`` W4 contract: the metadata carries THIS minted id,
#: never the busy fire's own).
_DEFERRED_EXECUTION_ID_PREFIX = "deferred-"

#: Outcome vocabulary of ``_coalesce_into`` (the in-place coalescing
#: write): the delivery was rewritten / the guard matched no row (the
#: caller rolls over to a fresh generation) / the scrub failed and the
#: content write was skipped.
_COALESCE_COALESCED = "coalesced"
_COALESCE_ROLLOVER = "rollover"
_COALESCE_SKIPPED = "skipped"
#: The coalesce write's outcome is UNKNOWN (the guarded UPDATE raised — we
#: have NO rowcount proof that the row is gone).  An unknown write outcome
#: is NOT proof of no-row: the pending delivery may still be live, so this
#: token must never be treated as ``_COALESCE_ROLLOVER`` (which authorises
#: a fresh enqueue).  INTERNAL coalesce-path token only — it is mapped to
#: a non-consuming retry outcome, never added to the R8-pinned five-value
#: ``_fire_outcome`` vocabulary.
_COALESCE_WRITE_UNKNOWN = "write_unknown"

#: The age-cap TERMINAL reason token (decisions.md DEC-9's cap block).  The
#: terminal row's ``error_message`` carries this token PLUS the episode key
#: (``since=<iso>``) so the terminal-disposition guard can tell THIS
#: episode's terminal row from an EARLIER episode's (W-A).
_AGE_CAP_REASON_TOKEN = "nondelivery_age_cap"

#: Separator between the age-cap reason token and the episode key it
#: carries (``"nondelivery_age_cap since=2026-10-21T12:00:00+00:00"``).
_AGE_CAP_EPISODE_KEY_SEP = " since="

#: How many recent execution rows the episode-scoped terminal guard scans
#: for a previous terminal disposition.  The guard is about the IMMEDIATE
#: repetition of a disposition (an unrelated row landing between two
#: breaches must not defeat it), not about history.
_AGE_CAP_GUARD_SCAN_LIMIT = 50


def _age_cap_reason(episode_key: str | None) -> str:
    """The age-cap terminal row's reason: the token PLUS the episode key.

    The episode key is the carrier's ``_defer_since`` (the episode's start
    marker — set-if-absent at the FIRST non-delivery outcome).  Carrying it
    in the row is what lets the terminal-disposition guard answer "is THIS
    episode's terminal already on record?" (W-A) instead of "is the LATEST
    row a terminal?" (which both misses a duplicate when an unrelated row
    interleaves and suppresses a LATER episode's legitimate terminal).
    """
    if not episode_key:
        return _AGE_CAP_REASON_TOKEN
    return f"{_AGE_CAP_REASON_TOKEN}{_AGE_CAP_EPISODE_KEY_SEP}{episode_key}"


def _episode_key_from_reason(message: str | None) -> str | None:
    """The episode key a terminal row's reason carries (``None`` if absent).

    A row WITHOUT a key cannot be proven to belong to this episode, so it
    never suppresses a write (the conservative direction: a duplicated row
    is better than a missing terminal record).
    """
    if not message:
        return None
    idx = message.find(_AGE_CAP_EPISODE_KEY_SEP)
    if idx < 0:
        return None
    return message[idx + len(_AGE_CAP_EPISODE_KEY_SEP):].strip() or None


class SchedulerAdapter(MessageSourceAdapter):
    """Adapter that triggers messages on a schedule.
    
    Supports:
    - Cron expressions (schedule: "0 9 * * 1-5")
    - Interval in seconds (interval_seconds: 300)
    - One-time triggers (run_at: "2025-03-15T10:00:00Z")
    """
    
    # Schedule type constants
    SCHEDULE_TYPE_CRON = "cron"
    SCHEDULE_TYPE_INTERVAL = "interval"
    SCHEDULE_TYPE_ONE_TIME = "one_time"

    # Sentinel return value from ``_get_next_trigger_time`` when a one-shot
    # schedule's ``run_at`` is past-due beyond ``SchedulingConfig.one_shot_max_lateness_seconds``
    # (D3 lateness cap, ADR-003). The ``_run_schedule`` loop branches on
    # this sentinel to call ``_record_skipped_execution`` and NOT dispatch
    # a JobItem. Chosen as a module-level constant (NOT a per-instance
    # singleton) so a plain ``is`` identity check is correct.
    PAST_LATENESS_CAP = "__PAST_LATENESS_CAP__"

    # ------------------------------------------------------------------
    # Fire-outcome vocabulary (decisions.md DEC-9 CRIT-1) — FIVE values.
    # ------------------------------------------------------------------
    # The per-fire outcome gates the ONE-SHOT CONSUMPTION.  The two
    # consuming grounds are DIFFERENT and must never be "unified"
    # (decisions.md DEC-9 CRIT-1 part 4):
    #   * ``deferred_durable`` consumes because a DURABLE delivery exists;
    #   * ``refused_permanent`` consumes because the condition is
    #     PERMANENT (keeping the intent alive would be a 5s spin against a
    #     dead target, not a safety property).
    FIRE_OUTCOME_DIRECT = "direct"
    FIRE_OUTCOME_DEFERRED_DURABLE = "deferred_durable"
    FIRE_OUTCOME_REARMED = "rearmed"
    FIRE_OUTCOME_REFUSED_PERMANENT = "refused_permanent"
    FIRE_OUTCOME_FAILED = "failed"

    #: The outcomes that CONSUME a one-shot.  ``rearmed`` / ``failed``
    #: never consume — the intent survives and the schedule re-arms on the
    #: 5s cadence (decisions.md DEC-9's consumption-gating table).
    _CONSUMING_FIRE_OUTCOMES = frozenset(
        {
            FIRE_OUTCOME_DIRECT,
            FIRE_OUTCOME_DEFERRED_DURABLE,
            FIRE_OUTCOME_REFUSED_PERMANENT,
        }
    )

    #: Terminal Task statuses — a Task in one of these is NOT a live
    #: delivery (the coalescing gate's Task condition is NON-TERMINAL:
    #: a Task flips to RUNNING at claim BEFORE the message_queue row
    #: leaves 'ready' — task/repository.py:2801-2804).
    _TERMINAL_TASK_STATUSES = frozenset(
        {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
            "expired",  # no TaskStatus member — legacy literal, kept
        }
    )

    #: A CLAIMED Task — the delivery is IN FLIGHT and WILL land (C2: the
    #: claim window has BOTH a 'ready' message_queue row AND a running
    #: Task, so the age-cap breach must not abandon it).
    _CLAIMED_TASK_STATUSES = frozenset(
        {TaskStatus.RUNNING.value, TaskStatus.PAUSED.value}
    )

    #: The message_queue statuses that mean "a live delivery exists"
    #: (the W9 guard — ``retrying`` is the live-but-not-yet-claimed case).
    _LIVE_MQ_STATUSES = frozenset(
        {MessageStatus.READY.value, MessageStatus.RETRYING.value}
    )
    
    def __init__(
        self,
        config: SourceConfig,
        on_message: Callable[[IncomingMessage], Awaitable[None]],
        execution_callback: Callable | None = None,
        on_complete_callback: Callable[[str, bool], None] | None = None,
        job_queue_service: "JobQueueService" | None = None,
        source_repo: "SourceRepositoryType" | None = None,
        instance_repo: "SQLModelInstanceRepository" | None = None,
        manager: "InstanceManager | None" = None,
    ):
        """Initialize the scheduler adapter.

        Args:
            config: Source configuration containing schedule parameters
            on_message: Callback for incoming messages
            execution_callback: Optional callback for execution status updates.
                Called with: (execution_id, schedule_id, status, instance_id, error_message)
            on_complete_callback: Optional callback to notify adapter completion.
                Called with: (source_id, completed=True) when one-time schedule finishes.
            job_queue_service: Optional JobQueueService for routing jobs through queue.
                If provided and project_id is configured, jobs will be queued instead of
                immediate execution.
            source_repo: Optional SourceRepository for instance mode run counter tracking.
            instance_repo: Optional InstanceRepository for checking instance status in reuse_instance mode.
            manager: Optional InstanceManager (Phase 5). Required for the
                inline message-Job dispatch path
                (:meth:`_route_via_job_queue`). When None, the inline
                path raises immediately — the legacy TASK-job poll-loop
                path through :meth:`JobQueueService.enqueue` was removed
                in Phase 5; messages must always be JobItems now.
        """
        super().__init__(config, on_message)
        self._execution_callback = execution_callback
        self._on_complete_callback = on_complete_callback  # NEW
        self._manager = manager
        self._job_queue_service = job_queue_service
        self._source_repo = source_repo
        self._instance_repo = instance_repo
        
        # Extract scheduler-specific config
        scheduler_config = config.config
        
        # Instance mode configuration (Task 6)
        instance_mode_str = scheduler_config.get("instance_mode", "new_instance")
        # One-time schedules are forced to new_instance — UNLESS the stored
        # row EXPLICITLY asks for reuse_instance.
        #
        # HISTORY: the force was UNCONDITIONAL. It encoded the "a one-shot
        # is a fresh task, never a continuation" semantics — the exact
        # framing the user ruling of 2026-10-20 (Discord, direct)
        # RETRACTS: a self-alarm must wake the CALLING instance, and the
        # user's own worked example is a ONE-SHOT (arm at 13:00 → fire at
        # 14:00). The stored ``instance_mode`` is the operator's stated
        # intent, so an explicit ``reuse_instance`` is honoured and the
        # engine's existing reuse-instance delivery path (an ordinary user
        # message injected into the mapped instance) does the rest.
        #
        # The DEFAULT is unchanged: a one-time row that says nothing (or
        # says new_instance) still fires into a fresh instance. Measured
        # blast radius at the ruling: 0 live schedule rows carry
        # ``reuse_instance`` — 11 scheduler rows (10 stopped + 1
        # cancelled), all ``new_instance``; the 14 total source_configs
        # also include 3 chat-source rows — so no existing schedule
        # changes behaviour.
        if (
            scheduler_config.get("run_at")
            and instance_mode_str != "reuse_instance"
        ):
            self._instance_mode = SchedulerInstanceMode.NEW_INSTANCE
            logger.debug(f"Force new_instance for one-time schedule: {self.source_id}")
        else:
            self._instance_mode = SchedulerInstanceMode(instance_mode_str)
        
        # Schedule configuration
        self._schedule_type: str | None = None
        self._cron_expression: str | None = None
        self._interval_seconds: int | None = None
        self._run_at: datetime | None = None
        
        # Message configuration
        self._agent: str | None = scheduler_config.get("agent")
        self._message_content: str = scheduler_config.get("message", "")
        
        # Timezone configuration (Phase 1 / Task 2.4 / ADR-002): wire the
        # canonical D2 resolution chain — explicit ``config.timezone`` (when
        # present) wins over ``SchedulingConfig.default_timezone`` over
        # host-local detection over ``datetime.timezone.utc`` (the resolver
        # implements the four-step chain via the ``explicit`` vs ``default``
        # args). Per-config precedence is preserved: an operator who sets
        # ``timezone`` on a schedule row keeps that tz; only schedules
        # WITHOUT an explicit ``timezone`` pick up the daemon-wide default
        # → host-local → UTC chain.
        #
        # NOTE: do NOT default ``scheduler_config.get("timezone", "UTC")``
        # the way the legacy block did — that would inject "UTC" as
        # ``explicit`` and short-circuit step 1 of the resolver chain
        # (ZoneInfo("UTC") resolves cleanly), so the
        # ``SchedulingConfig.default_timezone`` (step 2) and host-local
        # detection (step 3) would never run. ``None`` is the correct
        # sentinel that lets the resolver fall through to step 2 / 3 / 4.
        # The previous default of "UTC" was a legacy safety net that the
        # resolver chain obsoletes.
        timezone_str = scheduler_config.get("timezone")
        try:
            from daemon.config import SchedulingConfig
            _default_tz = SchedulingConfig().default_timezone
        except Exception as cfg_exc:  # noqa: BLE001 — bootstrap-safety
            # Config not yet importable (boot ordering); default to None so
            # the resolver's chain falls through to host-local → UTC.
            logger.debug(
                "SchedulerAdapter.__init__: SchedulingConfig unavailable (%s); "
                "tz chain falls through to host-local detection",
                cfg_exc,
            )
            _default_tz = None
        from daemon.util.tz import resolve_timezone
        self._timezone, _tz_warning = resolve_timezone(
            timezone_str,
            default=_default_tz,
            for_tool=False,
        )
        if _tz_warning:
            # for_tool=False normally returns "" on clean resolution; this
            # branch is reachable only if a future caller switches the
            # adapter to for_tool=True. Logged at WARNING because tz
            # resolution failure is operator-actionable (host tzdb gap or
            # bad ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE).
            logger.warning(
                f"SchedulerAdapter tz resolution invalid for {self.source_id}: {_tz_warning}"
            )
        
        # Concurrency control
        self._max_concurrent: int = scheduler_config.get("max_concurrent", SCHEDULER_DEFAULT_MAX_CONCURRENT)
        self._running_executions: int = 0
        self._execution_semaphore: asyncio.Semaphore | None = None
        
        # Task queue routing configuration (Tasks 5.1 & 5.3)
        self._project_id: str | None = scheduler_config.get("project_id")
        priority_raw = scheduler_config.get("priority", SCHEDULER_DEFAULT_PRIORITY)
        self._priority: int = self._validate_priority(priority_raw)
        
        if self._project_id:
            logger.info(
                f"SchedulerAdapter queue routing enabled: project_id={self._project_id}, "
                f"priority={self._priority}"
            )
        
        # Parse and validate schedule configuration
        self._parse_schedule_config(scheduler_config)
        
        # Internal state
        self._scheduler_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event = asyncio.Event()
        self._is_one_time_executed: bool = False
        # D3 lateness cap (ADR-003): stashed by ``_get_next_trigger_time``
        # when it returns the ``PAST_LATENESS_CAP`` sentinel, consumed by
        # the ``_run_schedule`` loop's skip branch.
        self._pending_skip_lateness: float | None = None
        self._pending_skip_cap: int | None = None
        # W1: once-per-run_at skip guard. Tracks the run_at value for
        # which the SKIPPED row was last written so we don't emit a new
        # SKIPPED row on every loop iteration while the operator hasn't
        # updated run_at. Cleared on operator update (when ``_run_at``
        # changes) so a fresh future run_at produces its own SKIPPED
        # audit row.
        self._last_skipped_run_at: datetime | None = None
        # The per-fire outcome (decisions.md DEC-9 CRIT-1) — the ONE-SHOT
        # CONSUMPTION gate.  Reset at the start of every fire.
        self._fire_outcome: str = self.FIRE_OUTCOME_DIRECT
        # Set when the RE-ARM branch already took the mandated 5s sleep so
        # the loop does not double-sleep (the re-arm cadence stays 5s).
        self._rearm_cadence_slept: bool = False
        # H3(i) — the IN-MEMORY episode clock.  When the carrier write
        # FAILS the episode is untracked in the DB (the 24h age cap cannot
        # fire on it) — this fallback keeps the cap able to terminate the
        # episode for the adapter's lifetime, so a broken carrier write
        # cannot produce an unbounded 5s re-arm spin.  It is SET-IF-ABSENT
        # (B1): the clock measures the age of the FIRST failed carrier
        # write and ACCUMULATES from there — a plain overwrite would
        # re-stamp it on every re-arm tick, so the visible age would stay
        # below the cap forever and the bound would be ZERO.  It is retired
        # as soon as a carrier write succeeds.
        self._defer_since_in_memory: str | None = None
        
        logger.info(
            f"SchedulerAdapter initialized: type={self._schedule_type}, "
            f"source_id={self.source_id}, timezone={timezone_str}, "
            f"instance_mode={self._instance_mode.value}"
        )
    
    def _validate_priority(self, priority: int) -> int:
        """Validate and clamp priority to valid range.
        
        Args:
            priority: Priority value to validate.
            
        Returns:
            Priority clamped to 1-10 range.
        """
        if not isinstance(priority, int):
            try:
                priority = int(priority)
            except (ValueError, TypeError):
                logger.warning(f"Invalid priority type: {type(priority)}, defaulting to 5")
                return 5
        
        if priority < 1:
            logger.warning(f"Priority {priority} below minimum, clamping to 1")
            return 1
        if priority > 10:
            logger.warning(f"Priority {priority} above maximum, clamping to 10")
            return 10
        
        return priority
    
    def _parse_schedule_config(self, scheduler_config: dict) -> None:
        """Parse and validate schedule configuration.
        
        Args:
            scheduler_config: The configuration dict from SourceConfig
            
        Raises:
            ValueError: If no valid schedule is configured
        """
        # Check for conflicting schedule types
        has_schedule = "schedule" in scheduler_config and scheduler_config["schedule"]
        has_interval = "interval_seconds" in scheduler_config
        has_run_at = "run_at" in scheduler_config and scheduler_config["run_at"]
        
        if has_schedule and has_interval:
            logger.warning(
                f"Both cron and interval specified for {self.source_id}. "
                f"Using cron, ignoring interval_seconds={scheduler_config['interval_seconds']}"
            )
        
        # Check for cron expression
        if has_schedule:
            self._schedule_type = self.SCHEDULE_TYPE_CRON
            self._cron_expression = scheduler_config["schedule"]
            
            # Validate cron expression
            try:
                now = datetime.now(self._timezone)
                cron = croniter(self._cron_expression, now)
                # Try to get next run to validate
                cron.get_next(datetime)
                logger.info(f"Valid cron expression: {self._cron_expression}")
            except CroniterBadCronError as e:
                raise ValueError(f"Invalid cron expression '{self._cron_expression}': {e}")
        
        # Check for interval
        elif "interval_seconds" in scheduler_config:
            interval = scheduler_config["interval_seconds"]
            if not isinstance(interval, int) or interval <= 0:
                raise ValueError(f"interval_seconds must be a positive integer, got: {interval}")
            self._schedule_type = self.SCHEDULE_TYPE_INTERVAL
            self._interval_seconds = interval
            logger.info(f"Interval schedule: every {interval} seconds")
            
        # Check for one-time execution
        elif "run_at" in scheduler_config and scheduler_config["run_at"]:
            run_at_str = scheduler_config["run_at"]
            try:
                # Try parsing ISO format
                self._run_at = datetime.fromisoformat(run_at_str.replace("Z", "+00:00"))
                # If no timezone info, assume UTC
                if self._run_at.tzinfo is None:
                    self._run_at = self._run_at.replace(tzinfo=timezone.utc)
            except ValueError as e:
                raise ValueError(f"Invalid run_at format '{run_at_str}': {e}")
            
            self._schedule_type = self.SCHEDULE_TYPE_ONE_TIME
            logger.info(f"One-time schedule: {self._run_at}")
            
        else:
            raise ValueError(
                "No valid schedule configured. Provide one of: "
                "'schedule' (cron), 'interval_seconds', or 'run_at'"
            )
        
        # Validate agent and message
        if not self._agent:
            logger.warning("No 'agent' specified in scheduler config")
        if not self._message_content:
            logger.warning("No 'message' specified in scheduler config")
    
    async def start(self) -> None:
        """Start the scheduler loop."""
        if self._status == SourceStatus.RUNNING:
            logger.warning(f"Scheduler already running: {self.source_id}")
            return
        
        self._status = SourceStatus.STARTING
        self._error = None
        
        try:
            # Initialize semaphore for concurrency control
            self._execution_semaphore = asyncio.Semaphore(self._max_concurrent)
            
            # Reset stop event
            self._stop_event.clear()
            
            # Start the scheduler loop
            self._scheduler_task = asyncio.create_task(self._run_schedule())
            
            self._status = SourceStatus.RUNNING
            logger.info(f"Scheduler started: {self.source_id}, type={self._schedule_type}")
            
        except Exception as e:
            self._status = SourceStatus.ERROR
            self._error = str(e)
            logger.error(f"Failed to start scheduler: {e}")
            raise
    
    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        logger.info(f"Stopping scheduler: {self.source_id}")
        
        # Signal stop
        self._stop_event.set()
        
        # Cancel scheduler task
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None
        
        # Wait for running executions to complete (with timeout)
        if self._running_executions > 0:
            logger.info(
                f"Waiting for {self._running_executions} running execution(s) to complete..."
            )
            # Give a grace period for running executions
            try:
                await asyncio.wait_for(
                    self._wait_for_executions(),
                    timeout=SCHEDULER_GRACE_PERIOD_S
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"Timeout waiting for executions to complete, "
                    f"{self._running_executions} still running"
                )
        
        self._status = SourceStatus.STOPPED
        logger.info(f"Scheduler stopped: {self.source_id}")
    
    async def _wait_for_executions(self) -> None:
        """Wait for all running executions to complete."""
        while self._running_executions > 0:
            await asyncio.sleep(SCHEDULER_DRAIN_CHECK_S)
    
    async def send(self, message: OutgoingMessage) -> bool:
        """Handle responses from scheduled tasks (e.g., child agent outputs).
        
        Since scheduler is a one-way source, responses are logged for monitoring
        rather than sent to an external destination. This method exists to satisfy
        the MessageSourceAdapter interface and handle responses from child sessions
        that inherit the scheduler's root_source.
        
        Args:
            message: Outgoing message containing agent response
            
        Returns:
            True - responses are logged/monitored, always success
        """
        # Log the response for monitoring/debugging scheduled tasks
        content_preview = message.content[:200] + "..." if len(message.content) > 200 else message.content
        logger.info(
            f"Scheduled task response for {message.external_user_id}: {content_preview}"
        )
        
        # Response is logged for monitoring - _store_response was removed (dead code)
        
        return True
    
    async def health_check(self) -> bool:
        """Check if scheduler is healthy.
        
        Returns:
            True if scheduler is running
        """
        if self._status != SourceStatus.RUNNING:
            return False
        
        # Check if scheduler task is still running
        if self._scheduler_task and self._scheduler_task.done():
            # Task completed unexpectedly
            try:
                self._scheduler_task.result()
            except Exception as e:
                self._error = str(e)
                self._status = SourceStatus.ERROR
                return False
        
        return True
    
    async def manual_trigger(self) -> str:
        """Manually trigger the schedule immediately.
        
        Returns:
            execution_id: Unique ID for this manual execution
        """
        if self._status != SourceStatus.RUNNING:
            raise RuntimeError(f"Scheduler not running: {self._status}")
        
        execution_id = str(uuid.uuid4())
        logger.info(f"Manual trigger for {self.source_id}: execution_id={execution_id}")
        
        # Run the trigger asynchronously without waiting
        asyncio.create_task(self._execute_trigger(execution_id))
        
        return execution_id
    
    async def _run_schedule(self) -> None:
        """Main scheduler loop that triggers messages at scheduled times."""
        logger.info(f"Starting scheduler loop: {self.source_id}")

        while not self._stop_event.is_set():
            try:
                # Check if we should trigger now
                next_trigger = self._get_next_trigger_time()

                # D3 lateness cap (ADR-003): if the past-due one-shot is
                # beyond ``one_shot_max_lateness_seconds``, write a SKIPPED
                # schedule_executions row and DO NOT dispatch a JobItem.
                # Schedule remains armed so the operator can re-schedule.
                if next_trigger is self.PAST_LATENESS_CAP:
                    lateness = self._pending_skip_lateness
                    cap = self._pending_skip_cap
                    # Clear pending state so the next iteration (e.g. after
                    # the operator updates run_at) starts fresh.
                    self._pending_skip_lateness = None
                    self._pending_skip_cap = None
                    # W1 once-per-run_at guard: skip the audit row +
                    # callback when this run_at has already produced a
                    # SKIPPED row in this adapter lifetime. The adapter
                    # is reconstructed on schedule update, so a fresh
                    # future run_at produces its own SKIPPED row.
                    if self._last_skipped_run_at == self._run_at:
                        logger.debug(
                            "Skipping repeated SKIPPED for unchanged "
                            f"run_at={self._run_at}; last skip was for "
                            "this same run_at — no new audit row."
                        )
                        await asyncio.sleep(SCHEDULER_ERROR_RETRY_S)
                        continue
                    skip_exec_id = self._record_skipped_execution(
                        lateness=lateness, cap=cap
                    )
                    self._last_skipped_run_at = self._run_at
                    # Notify the execution_callback path with status=SKIPPED
                    # so external observers see the skip event. Reuse the
                    # SAME execution_id the audit row carries so observers
                    # can correlate; on row-write failure ``skip_exec_id``
                    # is ``None`` and we mint a phantom id so the callback
                    # still fires (no skip event would otherwise surface).
                    if skip_exec_id is None:
                        skip_exec_id = f"skipped-phantom-{uuid.uuid4()}"
                    if self._execution_callback:
                        try:
                            self._execution_callback(
                                execution_id=skip_exec_id,
                                schedule_id=self.source_id,
                                status=ExecutionStatus.SKIPPED.value,
                                instance_id=None,
                                error_message=(
                                    f"past_lateness_cap: lateness={lateness}s, cap={cap}s"
                                ),
                            )
                        except Exception as cb_exc:
                            logger.warning(f"Execution callback error (skip): {cb_exc}")
                    # Brief pause to avoid a tight loop if the cap is hit
                    # repeatedly with the same run_at (operator hasn't
                    # updated). Matches the existing SCHEDULER_ERROR_RETRY_S
                    # retry-loop convention.
                    await asyncio.sleep(SCHEDULER_ERROR_RETRY_S)
                    continue

                if next_trigger is None:
                    # One-time trigger already executed
                    if self._schedule_type == self.SCHEDULE_TYPE_ONE_TIME:
                        logger.info(f"One-time schedule completed: {self.source_id}")
                        break
                    logger.error(f"Could not determine next trigger time: {self.source_id}")
                    break

                # Calculate wait time
                now = datetime.now(self._timezone)
                if next_trigger.tzinfo is None:
                    next_trigger = next_trigger.replace(tzinfo=self._timezone)

                wait_seconds = (next_trigger - now).total_seconds()

                if wait_seconds > 0:
                    # Wait until next trigger time
                    logger.debug(
                        f"Next trigger for {self.source_id} in {wait_seconds:.1f}s "
                        f"(at {next_trigger.isoformat()})"
                    )

                    # Use wait_for with stop event to allow graceful shutdown
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=wait_seconds
                        )
                        # Stop event was set, exit gracefully
                        if self._stop_event.is_set():
                            break
                    except asyncio.TimeoutError:
                        # Timeout means we reached the trigger time
                        pass

                # Trigger the scheduled message
                await self._emit_scheduled_message()

                # For one-time schedules, exit after execution
                if self._schedule_type == self.SCHEDULE_TYPE_ONE_TIME:
                    # CONSUMPTION GATE (R8 / DEC-9 CRIT-1): a one-shot is
                    # consumed ONLY on a durable outcome.  ``direct`` /
                    # ``deferred_durable`` / ``refused_permanent`` consume;
                    # ``rearmed`` / ``failed`` NEVER do — the intent
                    # survives and the schedule re-arms on the 5s cadence.
                    if self._fire_outcome in self._CONSUMING_FIRE_OUTCOMES:
                        self._is_one_time_executed = True
                        logger.info(f"One-time schedule executed: {self.source_id}")
                        # NEW: Notify completion so adapter can disable itself
                        if self._on_complete_callback:
                            try:
                                self._on_complete_callback(self.source_id, completed=True)
                            except Exception as e:
                                logger.warning(f"on_complete_callback failed: {e}")
                        break
                    # NOT consumed — re-arm on the mandated cadence (the
                    # branch that already took the sleep does not sleep
                    # twice).
                    logger.warning(
                        "Scheduler %s: the one-time fire was NOT consumed "
                        "(outcome=%s) — the schedule re-arms",
                        self.source_id,
                        self._fire_outcome,
                    )
                    if not self._rearm_cadence_slept:
                        await asyncio.sleep(SCHEDULER_ERROR_RETRY_S)
                    continue

            except asyncio.CancelledError:
                # Scheduler was cancelled
                break
            except Exception as e:
                logger.error(f"Scheduler error for {self.source_id}: {e}", exc_info=True)
                # Brief pause before retry to avoid tight loop on errors
                await asyncio.sleep(SCHEDULER_ERROR_RETRY_S)

        logger.info(f"Scheduler loop ended: {self.source_id}")
    
    def _get_next_trigger_time(self):
        """Calculate next trigger time based on schedule type.

        Returns:
            - ``datetime`` of next trigger (for cron / interval / valid one-shot)
            - ``None`` if no more triggers (one-shot already executed, or
              cron / interval has no future time)
            - ``PAST_LATENESS_CAP`` sentinel (one-shot past-due beyond
              ``SchedulingConfig.one_shot_max_lateness_seconds``) — the
              ``_run_schedule`` loop branches on this to write a SKIPPED
              ``schedule_executions`` row and NOT dispatch a JobItem.
        """
        now = datetime.now(self._timezone)

        if self._schedule_type == self.SCHEDULE_TYPE_CRON:
            if not self._cron_expression:
                return None
            # F2 / D8 (DST fix, 2026-10-02): croniter 6.0.0 has a defect
            # family on the recurring-cron path that emits phantom
            # fires (or silently drops real fires) around DST
            # transitions. ``daemon.util.tz.compute_next_cron_fire``
            # wraps croniter with re-anchoring + roundtrip phantom
            # detection + cron-expression-literal recovery; the
            # canonical DST rule (architecture §4.2 / ADR-008) lives
            # in ``daemon.util.tz.anchor_local_to_utc`` and is shared
            # by one-shot + cron paths. See the helper's docstring
            # for the empirical croniter-6.0.0 trace.
            try:
                from daemon.util.tz import compute_next_cron_fire as _next_cron_fire
                cron_fire = _next_cron_fire(
                    self._cron_expression, now, self._timezone,
                )
                if cron_fire is not None:
                    return cron_fire
            except Exception as cron_exc:  # noqa: BLE001 — fallback
                # Fall back to direct croniter on any unexpected
                # failure (e.g. tz helper import error, downstream
                # regression). Croniter's broken DST behavior is the
                # known-bad shape — we'd rather emit a degraded fire
                # than no fire at all (legacy behavior).
                logger.warning(
                    "scheduler._get_next_trigger_time: cron-path DST "
                    "fix failed (expr=%r, tz=%s): %s; falling back "
                    "to direct croniter",
                    self._cron_expression, self._timezone, cron_exc,
                )
            try:
                cron = croniter(self._cron_expression, now)
                return cron.get_next(datetime)
            except CroniterBadCronError as e:
                logger.error(f"Cron parsing error: {e}")
                return None

        elif self._schedule_type == self.SCHEDULE_TYPE_INTERVAL:
            if not self._interval_seconds:
                return None
            # For interval, next trigger is now + interval
            return now + timedelta(seconds=self._interval_seconds)

        elif self._schedule_type == self.SCHEDULE_TYPE_ONE_TIME:
            if self._is_one_time_executed:
                return None
            # If run_at is in the past, trigger now.
            #
            # ``run_at`` is parsed once in ``_parse_schedule_config`` where
            # naive ISO inputs are anchored to ``self._timezone`` (or
            # assumed-UTC if tzinfo is None and timezone is ``UTC``).
            # After that point, ``run_at`` is ALWAYS tz-aware when
            # ``__init__`` was the construction path (the only production
            # path — adapter is constructed via
            # ``SourceRegistry._create_adapter_from_config``); no
            # ``as tz-aware`` re-anchor is reachable here. The helper
            # ``daemon.util.tz.anchor_local_to_utc`` (architecture §4.2)
            # is the canonical anchor for any NEW one-shot surface
            # (phase-2 tools/REST/service).
            run_at = self._run_at

            if run_at <= now:
                # Past-due. Check the D3 lateness cap (ADR-003) before
                # firing. Cap is opt-in via SchedulingConfig; ``None`` means
                # unlimited (legacy behavior preserved).
                try:
                    from daemon.config import SchedulingConfig
                    cap = SchedulingConfig().one_shot_max_lateness_seconds
                except Exception as cfg_exc:  # noqa: BLE001 — bootstrap-safety
                    logger.debug(
                        "scheduler: SchedulingConfig unavailable (%s); "
                        "treating cap as None (unlimited)",
                        cfg_exc,
                    )
                    cap = None
                if cap is not None:
                    lateness = (now - run_at).total_seconds()
                    if lateness > cap:
                        # Beyond cap — write a SKIPPED row, do NOT dispatch.
                        # Signal the loop via the PAST_LATENESS_CAP sentinel.
                        logger.info(
                            f"Scheduler {self.source_id}: past-due one-shot "
                            f"lateness={lateness:.0f}s exceeds cap={cap}s; "
                            f"recording SKIPPED, no dispatch"
                        )
                        # Stash the lateness/cap values for the loop to pick
                        # up via ``_pending_skip_*`` (no return-value tuple
                        # because the signature must stay compatible with
                        # callers that use ``is None`` / ``is PAST_LATENESS_CAP``
                        # identity checks).
                        self._pending_skip_lateness = lateness
                        self._pending_skip_cap = cap
                        return self.PAST_LATENESS_CAP
                return now  # Trigger now (legacy behavior preserved when cap is None)

            return run_at

        return None
    
    def _record_skipped_execution(self, *, lateness: float | None, cap: int | None) -> str | None:
        """Write a SKIPPED ``schedule_executions`` row for a past-lateness-cap one-shot (D3).

        ADR-003 — schedule remains armed (NOT disabled); operator can update
        ``run_at`` to a future time and re-fire. The execution_id prefix
        ``skipped-`` distinguishes cap-skips from real runs in list output.

        Uses the 2-call pattern (correction (a), architecture §8):
        ``record_execution_start(...)`` THEN ``record_execution_complete(...)``.
        ``record_execution_complete`` does NOT accept a ``schedule_id`` kwarg
        — verified by grep against ``daemon/repositories/source/repository.py:570``.
        We do NOT invent one (would diverge from the substrate).

        Synchronous helper — called from the async ``_run_schedule`` loop; the
        ``source_repo`` is the SQLModelSourceRepository (SQLite/Postgres) and
        both methods open their own sessions internally, so this is safe
        to call from the async loop (it blocks the loop briefly; mirrors the
        existing fire-and-forget ``execution_callback`` pattern at
        ``registry.py:490-496``).

        Returns the ``execution_id`` written to the row (or ``None`` when
        no row was written). Callers use this to echo the SAME id into
        ``_execution_callback`` so observers can correlate the SKIPPED
        event with the audit row (previously a separate ``uuid.uuid4()``
        was minted for the callback, leaving observers looking at a
        phantom id that never appears in the row).
        """
        if self._source_repo is None:
            logger.warning(
                f"Scheduler {self.source_id}: cannot record SKIPPED execution "
                f"(no source_repo); cap_skipped_row missing for lateness={lateness}s"
            )
            return None

        execution_id = f"skipped-{uuid.uuid4()}"
        error_message = f"past_lateness_cap: lateness={lateness}s, cap={cap}s"
        try:
            # Call 1: open the row in TRIGGERED state (status default for record_execution_start).
            self._source_repo.record_execution_start(
                schedule_id=self.source_id,
                instance_id=None,
                execution_id=execution_id,
            )
            # Call 2: immediately transition to SKIPPED with the error message.
            self._source_repo.record_execution_complete(
                execution_id=execution_id,
                status=ExecutionStatus.SKIPPED.value,
                error_message=error_message,
            )
            logger.info(
                f"Scheduler {self.source_id}: recorded SKIPPED execution "
                f"{execution_id[:16]}... ({error_message})"
            )
            return execution_id
        except Exception as exc:
            # Logging-only — a failed audit row is preferable to silently
            # dispatching the JobItem the cap was meant to suppress.
            logger.error(
                f"Scheduler {self.source_id}: failed to record SKIPPED "
                f"execution (lateness={lateness}s, cap={cap}s): {exc}",
                exc_info=True,
            )
            return None

    def _format_continuation_message(self, original_message: str, run_number: int) -> str:
        """Format a continuation message with #N prefix for reuse_instance mode.
        
        Args:
            original_message: The original scheduled message content.
            run_number: The current run number for this session.
            
        Returns:
            Formatted message with continuation prefix and instructions.
        """
        continuation_template = f"""#{run_number}

[CONTINUATION - Run #{run_number}]

This is scheduled execution #{run_number} of a multi-run session.
The previous runs have been completed. Continue the work incrementally:

1. Review the context and progress from prior runs (if any)
2. Build upon previous work, do not repeat what was already done
3. Focus on advancing the task rather than restarting from scratch
4. Provide incremental progress reports

Original scheduled task:
{original_message}
"""
        return continuation_template
    
    def _is_instance_active(self) -> tuple[bool, str | None, str | None]:
        """Check if the mapped instance is currently active (running or waiting).
        
        For reuse_instance mode, checks if the mapped instance exists and is active.
        If the instance is running or waiting, execution should be skipped.
        
        Returns:
            Tuple of (is_active, instance_id, instance_status).
            - is_active: True if instance exists and status is running/waiting.
            - instance_id: The instance ID if mapping exists, None otherwise.
            - instance_status: The instance status string if mapping exists, None otherwise.
        """
        # Only applicable for reuse_instance mode
        if self._instance_mode != SchedulerInstanceMode.REUSE_INSTANCE:
            return False, None, None
        
        # Check if we have the required dependencies
        if not self._source_repo or not self._instance_repo:
            logger.debug(
                f"Cannot check instance status: source_repo={self._source_repo is not None}, "
                f"instance_repo={self._instance_repo is not None}"
            )
            return False, None, None
        
        # Get instance mapping (source_id is used as external_user_id for scheduler)
        try:
            mapping = self._source_repo.get_instance_mapping(
                self.source_id, 
                self.source_id
            )
        except Exception as e:
            logger.warning(f"Failed to get instance mapping: {e}")
            return False, None, None
        
        if not mapping:
            logger.debug(f"No instance mapping found for {self.source_id}")
            return False, None, None
        
        # Get instance status
        try:
            instance = self._instance_repo.get(mapping.agent_instance_id)
        except Exception as e:
            logger.warning(f"Failed to get instance: {e}")
            return False, None, None
        
        if not instance:
            logger.debug(f"Instance not found: {mapping.agent_instance_id}")
            return False, None, None
        
        # Check if instance is active (running or waiting)
        # Note: instance.status is a string, compare with enum values
        is_active = instance.status in (
            InstanceStatus.RUNNING.value,
            InstanceStatus.WAITING.value,
            InstanceStatus.WAITING_CHILDREN.value,
        )
        
        return is_active, mapping.agent_instance_id, instance.status

    # ------------------------------------------------------------------
    # Scheduler deferred delivery (commission
    # ``scheduler-deferred-alarm-delivery``).
    #
    # The DEFECT this fixes: a scheduled fire at a BUSY caller was
    # SILENTLY DROPPED (no JobItem / Task / MessageQueue row / audit row)
    # and a one-shot was still CONSUMED — the alarm was permanently lost
    # with zero trace (plan §(a)).
    #
    # The fix: the fire becomes a DURABLE PENDING delivery to the PINNED
    # caller (a PENDING Task + a MessageQueue row + a JobItem), the fire is
    # recorded in ``schedule_executions`` as ``deferred``, and repeated
    # fires COALESCE into that ONE pending delivery (never a second live
    # delivery — at-most-once is the invariant this commission restores).
    # ------------------------------------------------------------------

    def _defer_bounds(self) -> tuple[int, int]:
        """``(count_cap, max_age_seconds)`` from ``SchedulingConfig``.

        The two bounds are pydantic-settings knobs, NOT DDL (decisions.md
        DEC-19) — the migration delta stays EMPTY.
        """
        try:
            from daemon.config import SchedulingConfig

            cfg = SchedulingConfig()
            return int(cfg.defer_max_count), int(cfg.defer_max_age_seconds)
        except Exception as cfg_exc:  # noqa: BLE001 — bootstrap-safety
            logger.debug(
                "Scheduler %s: SchedulingConfig unavailable (%s); using the "
                "documented deferral defaults",
                self.source_id,
                cfg_exc,
            )
            return 16, 86400

    def _defer_state(self) -> dict[str, Any]:
        """The episode carrier, read from the schedule row's ``config`` JSON.

        Keys (plan §(c) C0 / decisions.md DEC-12): ``_defer_gen`` ·
        ``_defer_message_id`` · ``_defer_count`` · ``_defer_since``.
        """
        if self._source_repo is None:
            return {}
        try:
            row = self._source_repo.get_source_config(self.source_id)
        except Exception as exc:  # noqa: BLE001 — a read failure is not fatal
            logger.warning(
                "Scheduler %s: defer-state read failed: %s", self.source_id, exc
            )
            return {}
        if row is None:
            return {}
        return dict(getattr(row, "config", None) or {})

    async def _apply_defer_state(self, **kwargs: Any) -> bool:
        """Atomic carrier write — NEVER a Python read-modify-write.

        ``update_source_config`` replaces the whole ``config`` dict (its
        docstring forbids per-key mutation), so every per-key carrier
        mutation goes through ``apply_scheduler_defer_state`` (the
        dialect-aware ``jsonb_set`` / ``json_set`` + ``RETURNING`` pattern).

        FAMILY CLOSURE (H3): this is the FIFTH member of the
        ``_record_execution_start`` family — the carrier write is no
        longer swallowed.  It returns success/failure and EVERY call site
        gates it (a failed carrier write means the episode is UNTRACKED —
        the 24h age cap cannot fire on it — so the caller must be loud).

        F-N1: the repository's return value is HONOURED.  A missing source
        row makes ``apply_scheduler_defer_state`` return ``None`` (a warn,
        no raise) — the write did NOT happen, so a ``None`` result is a
        FAILED write.  Reporting it as a success retired the in-memory
        episode clock while the carrier stayed untracked, and the age cap
        could never fire on that path (the same false-success class this
        commission closed everywhere else).

        Returns:
            ``True`` when the carrier was written, ``False`` on a write
            failure (the token ``carrier_write_failed`` is logged).
        """
        if self._source_repo is None:
            logger.error(
                "Scheduler %s: carrier_write_failed — the carrier write was "
                "SKIPPED (%s, no source_repo); the episode is UNTRACKED",
                self.source_id,
                sorted(kwargs),
            )
            return False
        try:
            result = await asyncio.to_thread(
                self._source_repo.apply_scheduler_defer_state, self.source_id, **kwargs
            )
        except Exception as exc:  # noqa: BLE001 — the failure is surfaced, not swallowed
            logger.error(
                "Scheduler %s: carrier_write_failed — the carrier write FAILED "
                "(%s): %s; the episode is UNTRACKED (the 24h age cap cannot "
                "fire on it)",
                self.source_id,
                sorted(kwargs),
                exc,
            )
            return False
        if result is None:
            # F-N1: the repository found NO source row (it warns and
            # returns None) — the write did NOT happen.  A missing row is
            # a FAILED write, never a silent success.
            logger.error(
                "Scheduler %s: carrier_write_failed — the carrier write found "
                "NO source row (%s); the episode is UNTRACKED (the 24h age "
                "cap cannot fire on it)",
                self.source_id,
                sorted(kwargs),
            )
            return False
        return True

    def _queue_repository(self) -> Any:
        """The message-queue repository (the coalescing write target)."""
        return getattr(self._manager, "_queue_repository", None)

    def _task_repository(self) -> Any:
        """The task repository (the per-delivery durable-evidence check)."""
        return getattr(self._manager, "_task_repo", None)

    def _scrub_body(self, body: str, actor: str) -> str | None:
        """Re-scrub a body before it is (re)written to the queue.

        The scrub seam is a FUNCTION, not a location (architect §3,
        ADR-1): the coalescing in-place update calls the very same helper
        ``instance_messaging`` uses before the INSERT.

        FAIL CLOSED on a scrub failure: ``None`` is returned and the caller
        MUST NOT write the body at all.  Writing the RAW (unscrubbed) body
        would breach the house scrub doctrine (the persisted queue-row body
        is a scrub seam) — so the write is SKIPPED entirely instead.
        """
        try:
            from daemon.services.secret_persistence_scrub import scrub_enqueued_body

            return scrub_enqueued_body(body, actor=actor)
        except Exception as exc:  # noqa: BLE001 — the body is NOT written raw
            logger.error(
                "Scheduler %s: enqueue-body scrub failed — the body write is "
                "SKIPPED (the raw body is never persisted): %s",
                self.source_id,
                exc,
            )
            return None

    def _format_deferred_message(
        self, count: int, cap: int, instance_status: str | None, run_number: int
    ) -> str:
        """The deferred body — carries the LIVE missed-occurrence count.

        The saturation display is ``'<cap>+'`` when the count is pinned at
        the cap (NOT ``min(count+1, cap)``, which would write '16' —
        decisions.md DEC-19 / plan §(e) pin #3's Blocking-4 leg).
        """
        display = f"{cap}+" if count >= cap else str(count)
        plural = "occurrence" if count == 1 else "occurrences"
        status = instance_status or "busy"
        continuation = self._format_continuation_message(
            self._message_content, run_number
        )
        return (
            f"{continuation}\n"
            f"[DEFERRED — {display} {plural} coalesced]\n"
            f"The scheduled alarm could not be delivered at the fire instant: "
            f"the target instance was {status}. It is queued as the target's "
            f"NEXT turn and carries the live count of missed occurrences "
            f"({display})."
        )

    def _deferred_metadata(
        self, deferred_execution_id: str, count: int, instance_status: str | None
    ) -> dict[str, Any]:
        """The metadata a deferred delivery carries.

        W4 (decisions.md DEC-12): the metadata carries the SUBSTITUTED
        ``deferred-`` execution id — the id of the DEFERRED row this
        commission writes — and NEVER the busy fire's own ``execution_id``
        (that id belongs to a row that was never INSERTed).
        """
        return {
            "scheduler": {
                "execution_id": deferred_execution_id,
                "trigger_type": "scheduled",
                "trigger_time": datetime.now(self._timezone).isoformat(),
                "instance_mode": self._instance_mode.value,
                "deferred": True,
                "deferred_count": count,
                "target_status": instance_status,
            },
            "agent": self._agent,
            "force_new_instance": False,
            "deferred_execution_id": deferred_execution_id,
        }

    def _deferred_idempotency_key(self, key_gen: int) -> str:
        """The idempotency key of a deferred delivery.

        ADR-2 (decisions.md DEC-7): a ONE-SHOT keeps the EXISTING
        ``run_at`` key; a RECURRING fire uses the generation key
        ``scheduler:{source_id}:deferred:v{gen}``.  The key is the dedup
        latch ONLY — the authoritative coalescing state is the
        forward-carried ``_defer_message_id`` (never a key lookup).
        """
        if (
            self._schedule_type == self.SCHEDULE_TYPE_ONE_TIME
            and self._run_at is not None
        ):
            return f"scheduler:{self.source_id}:{self._run_at.isoformat()}"
        return f"scheduler:{self.source_id}:deferred:v{key_gen}"

    def _task_for_message(self, message_id: str) -> Any:
        """The Task row for a delivery id — the PER-DELIVERY predicate.

        Blocking 1 (decisions.md DEC-9): the predicate is per-DELIVERY —
        the Task's ``message_id`` must EQUAL the delivery's id.  A
        per-INSTANCE form would be satisfied by a DECOY task (a busy
        caller routinely holds unrelated PENDING tasks).

        RAISE-vs-ABSENT (W3): a lookup EXCEPTION is NOT the same as "no
        Task".  The exception returns the ``_TASK_LOOKUP_FAILED`` sentinel
        so the caller can tell "the Task is absent" (a real answer) from
        "the DB did not answer" (an unknown) — at a partial-DB-failure
        breach tick an unknown must NOT be read as "nothing is in flight"
        (that would write a FALSE failed row for a possibly-live
        delivery).
        """
        task_repo = self._task_repository()
        if task_repo is None:
            return None
        try:
            return task_repo.get_by_message(message_id)
        except Exception as exc:  # noqa: BLE001 — a read failure is not "absent"
            logger.warning(
                "Scheduler %s: task lookup failed for %s: %s",
                self.source_id,
                message_id,
                exc,
            )
            return _TASK_LOOKUP_FAILED

    def _delivery_task_is_live(self, message_id: str) -> bool | object:
        """Is the delivery named by ``message_id`` still live?

        The conjunction, explicitly: live ⇔ the Task is NON-terminal AND
        the message_queue row status is 'ready'/'retrying'; the
        terminal-Task test short-circuits first (a terminal Task returns
        False before the MQ row is consulted).

        [LEADER-RULED — iter-003 B3] The coalescing LIVENESS test is the
        MQ status guard (``status IN ('ready','retrying')`` — W9); the
        Task condition is the STRANDED-DELIVERY test and it is
        **NON-TERMINAL** here (a Task flips to RUNNING at claim —
        ``task/repository.py:2801-2804`` — BEFORE the message_queue row
        leaves 'ready', so a PENDING-only gate would fail inside that
        code-verified window and open a SECOND live delivery).

        TRI-STATE (H2): a read that RAISES is UNKNOWN, never a confident
        negative.  ``False`` means "definitively not live"; the
        ``_LIVENESS_UNKNOWN`` sentinel means "the DB did not answer" —
        the caller must NOT open a fresh generation on an unknown (the
        pending delivery may still be live).
        """
        task = self._task_for_message(message_id)
        if task is _TASK_LOOKUP_FAILED:
            # The Task read did not answer -> UNKNOWN (not "not live").
            return _LIVENESS_UNKNOWN
        if task is None:
            return False
        status = getattr(task, "status", None)
        if status in self._TERMINAL_TASK_STATUSES:
            return False
        mq_repo = self._queue_repository()
        if mq_repo is None:
            return False
        try:
            row = mq_repo.get(message_id)
        except Exception as exc:  # noqa: BLE001 — UNKNOWN, not a confident negative
            logger.error(
                "Scheduler %s: liveness_read_unknown — the liveness read for "
                "%s FAILED (%s); the delivery's liveness is UNKNOWN (it is "
                "NOT treated as 'not live')",
                self.source_id,
                message_id,
                exc,
            )
            return _LIVENESS_UNKNOWN
        if row is None:
            return False
        return getattr(row, "status", None) in self._LIVE_MQ_STATUSES

    def _record_execution_start(self, execution_id: str, instance_id: str | None) -> bool:
        """Open the audit row (the 2-call start->complete pattern).

        Returns ``True`` when the row was written.

        FAIL CLOSED (L-1 / architect §15.8): the audit-row write failure
        must NOT be treated as "the audit trail is fine".  On the dispatch
        leg (the deferred enqueue) a write failure means the dispatch does
        NOT happen at all — the caller checks this return value and
        refuses to enqueue.
        """
        if self._source_repo is None:
            logger.error(
                "Scheduler %s: cannot open the execution row %s (no "
                "source_repo) — the audit write FAILED",
                self.source_id,
                execution_id,
            )
            return False
        try:
            self._source_repo.record_execution_start(
                schedule_id=self.source_id,
                instance_id=instance_id,
                execution_id=execution_id,
            )
            return True
        except Exception as exc:  # noqa: BLE001 — the failure is fail-closed, not swallowed
            logger.error(
                "Scheduler %s: could not open the execution row %s (audit "
                "write FAILED): %s",
                self.source_id,
                execution_id,
                exc,
            )
            return False

    def _record_execution_complete(
        self, execution_id: str, status: str, error_message: str | None
    ) -> None:
        """Close the audit row."""
        if self._source_repo is None:
            return
        try:
            self._source_repo.record_execution_complete(
                execution_id=execution_id,
                status=status,
                error_message=error_message,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Scheduler %s: could not close the execution row %s: %s",
                self.source_id,
                execution_id,
                exc,
            )

    def _display_run_number(self, state: dict[str, Any]) -> int:
        """The occurrence number shown in the deferred body.

        ADR-8 (decisions.md DEC-20.2): the deferral is BLIND to the
        dispatch counter — ``_run_counter`` counts DIRECT dispatches only.
        The displayed number is therefore the counter + 1 (the occurrence
        this delivery carries) WITHOUT advancing the counter.
        """
        try:
            counter = int(state.get("_run_counter") or 0)
        except (TypeError, ValueError):
            counter = 0
        return counter + 1

    def _terminal_cap_disposition_already_recorded(
        self, episode_key: str | None
    ) -> bool:
        """Has THIS EPISODE's age-cap terminal disposition been recorded?

        H3(ii) idempotency guard, EPISODE-SCOPED (W-A): when the carrier
        CLEAR fails the episode lingers, and every recurring fire would
        otherwise write ANOTHER terminal ``failed`` row (a duplicated
        terminal disposition).

        The guard asks about the EPISODE, not about the latest row: it
        scans the recent execution rows for the newest TERMINAL row that
        carries the age-cap reason and suppresses the write ONLY when that
        row belongs to THIS episode (its ``since=`` episode key matches
        this episode's ``_defer_since``).  The latest-row-any predicate
        this replaces had two defects, both pinned:

        * an UNRELATED execution row landing between two breaches of the
          SAME episode hid the earlier terminal row, so the duplicate was
          written (the suppression missed);
        * a terminal row of an EARLIER episode suppressed a LATER
          episode's legitimate terminal (a false disposition: the new
          episode terminated SILENTLY, with no terminal row of its own).

        Conservative: a read failure answers ``False`` (the disposition is
        written — a duplicated row is better than a missing terminal
        record), and a terminal row WITHOUT an episode key never
        suppresses (it cannot be proven to be this episode's).
        """
        if self._source_repo is None:
            return False
        if not episode_key:
            # Without an episode key there is nothing to match against —
            # never suppress (the conservative direction).
            return False
        try:
            rows = self._source_repo.list_schedule_executions(
                self.source_id, limit=_AGE_CAP_GUARD_SCAN_LIMIT
            )
        except Exception as exc:  # noqa: BLE001 — fail toward WRITING the row
            logger.warning(
                "Scheduler %s: the execution-history lookup failed (%s) — the "
                "terminal age-cap row will be written (the guard cannot prove "
                "it is already recorded)",
                self.source_id,
                exc,
            )
            return False
        for row in rows or []:
            status = getattr(row, "status", None)
            message = getattr(row, "error_message", None) or ""
            if status != ExecutionStatus.FAILED.value:
                continue
            if _AGE_CAP_REASON_TOKEN not in message:
                continue
            # The newest TERMINAL age-cap row decides: it suppresses only
            # when it belongs to THIS episode.
            return _episode_key_from_reason(message) == episode_key
        return False

    async def _terminate_age_cap_episode(
        self,
        *,
        instance_id: str | None,
        max_age: int,
        age: float,
        what: str,
        episode_key: str | None = None,
    ) -> str:
        """Terminate the non-delivery episode VISIBLY (the age-cap legs).

        Consumes the occurrence + writes a terminal ``failed`` row (reason
        ``nondelivery_age_cap``) + a WARNING + CLEARS the carrier.  Shared
        by the two legs that share that disposition: case 1 (the pending
        delivery was abandoned) and case 3b (nothing will ever land).

        DO NOT UNIFY (decisions.md DEC-9 CRIT-1 part 4 — the two
        consumption grounds are DIFFERENT and must never be collapsed):
        ``refused_permanent`` normally means "a PERMANENT typed refusal at
        fire time" (the E1 confirmed-absence refusal) — it consumes because
        the CONDITION is permanent (keeping the intent alive would be a 5s
        spin against a dead target).  Here the SAME vocabulary value is
        overloaded for the age-cap TERMINAL disposition (the episode ends
        visibly; the reason token names the age cap, not a refusal).  The
        two grounds stay distinct in the CODE and in the REASON TOKEN:
        the E1 refusal carries the refusal reason, the age cap carries
        ``nondelivery_age_cap``.  (Promote-disclosure: the overload of
        the vocabulary value — not of the reason token — is disclosed at
        promote time.)

        The carrier fields the breach touches: ``_defer_since`` (cleared) ·
        ``_defer_message_id`` (cleared) · ``_defer_count`` (reset to 0) ·
        ``_defer_gen`` (NOT bumped — the breach never opens a fresh
        generation).

        ``episode_key`` is the episode's ``_defer_since`` — it is carried
        in the terminal row's reason so the episode-scoped idempotency
        guard (W-A) can tell THIS episode's terminal row from an earlier
        episode's.
        """
        cap_row_id = f"{_DEFERRED_EXECUTION_ID_PREFIX}{uuid.uuid4()}"
        # H3(ii) idempotency guard — evaluated BEFORE the row is opened, so
        # no half-written ('triggered') row is left behind when the
        # disposition is already on record.  W-A: the guard is scoped to
        # THIS EPISODE (its ``since=`` key), so an earlier episode's
        # terminal row can no longer suppress this episode's legitimate
        # terminal disposition.
        already_recorded = self._terminal_cap_disposition_already_recorded(
            episode_key
        )
        if not already_recorded and not self._record_execution_start(
            cap_row_id, instance_id
        ):
            # ASYMMETRY (R1 / DEC-4 row traceability): the DIRECT-DELIVERY
            # path fails CLOSED on an audit-write failure ONLY where R1's
            # fail-closed rule demands NO-DISPATCH (a dispatch without its
            # audit row is invisible — the original defect class).  The
            # age-cap TERMINATION has NOTHING to dispatch: its failure mode
            # is "RETRY THE TERMINATION", never "consume silently".  So: do
            # NOT consume, RETAIN the carrier, ERROR log, and let the 5s
            # loop retry — a transient DB failure recovers, a persistent one
            # re-attempts the VISIBLE termination on the next tick.
            logger.error(
                "Scheduler %s: the age-cap termination's audit row could not be "
                "written (audit_write_failed) — the occurrence is NOT consumed, "
                "the carrier is RETAINED and the termination is retried on the "
                "next tick (execution_id=%s)",
                self.source_id,
                cap_row_id,
            )
            self._fire_outcome = self.FIRE_OUTCOME_REARMED
            return self._fire_outcome
        if already_recorded:
            # H3(ii) idempotency guard: the terminal disposition is ALREADY
            # on record (the carrier clear failed and the episode lingers) —
            # the row is NOT written again (the consumption still happens:
            # the episode terminates either way).
            logger.warning(
                "Scheduler %s: the age-cap terminal disposition is already on "
                "record — no duplicate terminal row is written (the episode "
                "still terminates; reason=nondelivery_age_cap)",
                self.source_id,
            )
        else:
            self._record_execution_complete(
                cap_row_id,
                ExecutionStatus.FAILED.value,
                _age_cap_reason(episode_key),
            )
        # H3(ii): the carrier clear is GATED — on failure the episode
        # lingers, and the idempotency guard above keeps the terminal
        # disposition from being re-written on every recurring fire.
        await self._clear_episode()
        logger.warning(
            "Scheduler %s: non-delivery episode exceeded %ss (age=%.0fs) — %s; "
            "the occurrence is consumed and the carrier is CLEARED "
            "(reason=nondelivery_age_cap)",
            self.source_id,
            max_age,
            age,
            what,
        )
        # DO NOT UNIFY (decisions.md DEC-9 CRIT-1 part 4): this is the
        # vocabulary value the age-cap TERMINAL disposition borrows — the
        # E1 refusal's ground ("the condition is PERMANENT") and the age
        # cap's ground ("the episode ends visibly") are DIFFERENT and stay
        # distinct in the CODE (the reason token names the age cap:
        # ``nondelivery_age_cap``) — but the 5-value vocabulary is
        # R8-pinned, so the age cap shares the only consuming terminal
        # value.  The overload is disclosed at promote time.
        self._fire_outcome = self.FIRE_OUTCOME_REFUSED_PERMANENT
        return self._fire_outcome

    async def _handle_age_cap_breach(
        self, state: dict[str, Any], max_age: int, instance_id: str | None
    ) -> str | None:
        """The 24h non-delivery age cap — ONE rule, ONE token, THREE cases.

        [LEADER-RULED — Q1, the refined three-sub-case breach rule]

        ``nondelivery_age_cap`` (decisions.md DEC-9's cap block), refined:

        1. A PENDING delivery exists (``status IN ('ready','retrying')``) ->
           the guarded abandon -> CONSUME the occurrence + a terminal
           ``failed`` row (reason ``nondelivery_age_cap``) + a WARNING + the
           carrier CLEARED.  (The delivery was killed — it will never land.)
        2. NO pending delivery, but one is IN FLIGHT (a NON-TERMINAL Task
           whose ``message_id`` EQUALS this episode's ``_defer_message_id``
           — the SAME per-delivery stranded-delivery predicate) -> consume
           NOTHING, write NO failed row, RETAIN the carrier (ADJ-4: it WILL
           land).
        3. NO pending delivery AND nothing will land:
           3a. the marker's Task is terminal-COMPLETED -> the occurrence WAS
               DELIVERED -> CLEAR the carrier (this is the delivery-detection
               clear W8 had no home for), write NO failed row (a false failed
               row would be a lie) and apply the normal consumption
               semantics;
           3b. the marker's Task is terminal-FAILED or ABSENT, or there is
               NO marker at all (the delivery-less legs) -> TERMINATE
               VISIBLY: consume + a terminal ``failed`` row (reason
               ``nondelivery_age_cap``) + a WARNING + the carrier CLEARED.

        SUBSUMPTION (leader's note): case 3 subsumes "the delivery already
        landed" (3a — terminal completed), "the delivery died" (3b — terminal
        failed) and "there never was one" (3b — no marker).  All three
        terminate VISIBLY; none may spin.

        INVARIANT: never spin, AND never write a FALSE failed row — a breach
        disposition must be truthful about whether the alarm actually
        reached the caller.

        Returns the fire outcome when the breach was handled, ``None`` when
        the episode is still young.
        """
        since = state.get("_defer_since") or self._defer_since_in_memory
        if not since:
            return None
        try:
            since_dt = datetime.fromisoformat(str(since))
        except (TypeError, ValueError):
            return None
        if since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - since_dt).total_seconds()
        if age < max_age:
            return None

        marker = state.get("_defer_message_id") or None
        task = self._task_for_message(marker) if marker else None

        # ---- W3: the lookup RAISED — the state is UNKNOWN ----------------
        if task is _TASK_LOOKUP_FAILED:
            # The DB did not answer: "nothing is in flight" is NOT a
            # conclusion we may draw (it would write a FALSE failed row
            # for a possibly-live delivery).  Retain the carrier, warn and
            # retry on the next fire tick (the DB may recover).
            logger.warning(
                "Scheduler %s: non-delivery episode exceeded %ss (age=%.0fs) "
                "but the delivery state could not be read (task lookup "
                "failed) — the carrier is RETAINED, nothing is consumed and "
                "no failed row is written.",
                self.source_id,
                max_age,
                age,
            )
            self._fire_outcome = self.FIRE_OUTCOME_REARMED
            return self._fire_outcome

        task_status = getattr(task, "status", None)

        # ---- C2: the CLAIM-WINDOW cross-check, BEFORE any abandon -------
        # The claim window has BOTH a 'ready' message_queue row AND a
        # RUNNING Task: the delivery IS in flight and WILL land.  The
        # case-1-first ordering would abandon it, consume the occurrence
        # and write a terminal failed row for a delivery that lands — a
        # FALSE failed disposition — and clearing the carrier would orphan
        # the episode (ADJ-4's forbidden shape: a post-claim death would
        # then escape the cap entirely).  The cross-check uses the SAME
        # per-delivery Task predicate used at enqueue time.
        if task is not None and task_status in self._CLAIMED_TASK_STATUSES:
            logger.warning(
                "Scheduler %s: non-delivery episode exceeded %ss (age=%.0fs) "
                "but the delivery is CLAIMED (task=%s) — the carrier is "
                "RETAINED, nothing is consumed and no failed row is written.",
                self.source_id,
                max_age,
                age,
                task_status,
            )
            self._fire_outcome = self.FIRE_OUTCOME_REARMED
            return self._fire_outcome

        # ---- CASE 1 — a PENDING delivery exists -------------------------
        abandoned = False
        if marker:
            mq_repo = self._queue_repository()
            if mq_repo is not None:
                try:
                    abandoned = await asyncio.to_thread(
                        mq_repo.abandon_pending, marker
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Scheduler %s: age-cap abandon failed for %s: %s",
                        self.source_id,
                        marker,
                        exc,
                    )
        if abandoned:
            return await self._terminate_age_cap_episode(
                instance_id=instance_id,
                max_age=max_age,
                age=age,
                what=(
                    "the pending delivery was abandoned and the occurrence "
                    "consumed"
                ),
                episode_key=since,
            )

        # ---- CASE 2 — a delivery is IN FLIGHT (it WILL land) -------------
        if marker and task is not None and task_status not in self._TERMINAL_TASK_STATUSES:
            logger.warning(
                "Scheduler %s: non-delivery episode exceeded %ss (age=%.0fs) "
                "but a delivery is IN FLIGHT (task=%s) — the carrier is "
                "RETAINED, nothing is consumed and no failed row is written.",
                self.source_id,
                max_age,
                age,
                task_status,
            )
            self._fire_outcome = self.FIRE_OUTCOME_REARMED
            return self._fire_outcome

        # ---- CASE 3a — the occurrence WAS DELIVERED ----------------------
        if marker and task_status == "completed":
            # The delivery landed: the episode is CLOSED.  Clear the
            # carrier (the delivery-detection clear W8 had no home for),
            # write NO failed row (it would be a lie — the alarm DID reach
            # the caller) and apply the normal consumption semantics.
            # H3: the clear is GATED — on failure the episode lingers and
            # the ERROR is logged (``episode_clear_failed``).
            await self._clear_episode()
            logger.warning(
                "Scheduler %s: non-delivery episode exceeded %ss (age=%.0fs) "
                "but the deferred delivery LANDED — the carrier is CLEARED, "
                "no failed row is written and the occurrence is consumed.",
                self.source_id,
                max_age,
                age,
            )
            self._fire_outcome = self.FIRE_OUTCOME_DEFERRED_DURABLE
            return self._fire_outcome

        # ---- CASE 3b — nothing will ever land ----------------------------
        return await self._terminate_age_cap_episode(
            instance_id=instance_id,
            max_age=max_age,
            age=age,
            what=(
                "nothing will ever land (the delivery died, or there never "
                "was one) — the episode terminates visibly"
            ),
            episode_key=since,
        )

    async def _coalesce_into(
        self,
        message_id: str,
        count: int,
        cap: int,
        instance_id: str,
        instance_status: str | None,
        reason: str,
        run_number: int,
    ) -> str:
        """Coalesce this fire into the LIVE pending delivery.

        ADR-1 / DEC-18: the coalescing path NEVER tears the pending
        delivery down and re-enqueues it (that check-then-act race can
        deliver TWICE).  It rewrites the still-pending row's body with a
        SINGLE-STATEMENT guarded UPDATE (``status IN ('ready','retrying')``
        — the W9 guard).

        Returns:
            ``"coalesced"`` — the delivery was rewritten (the fire is
            audited as a coalesced DEFERRED row);
            ``"rollover"`` — the guard matched no row (the delivery is gone
            -> the caller may roll over to a fresh generation);
            ``"skipped"`` — the body could NOT be written (the scrub
            failed).  The RAW body is never persisted; this fire is NOT
            dispatched again (the pending delivery is still live — a fresh
            enqueue would open a SECOND live delivery, an at-most-once
            breach) and the next fire retries.  NOTE: a skipped coalesce
            applies NO ``count_delta``, so the delivered body's occurrence
            count can under-report by one per scrub failure — the count
            CAP bounds the DISPLAY (the saturation marker), NOT the
            under-report (W-B).
        """
        mq_repo = self._queue_repository()
        if mq_repo is None:
            # W-C: there is no coalesce substrate at all, so there is NO
            # proof that the pending delivery is gone — the same epistemic
            # shape H1 removed.  Returning the PROOF token ("rollover")
            # here would authorise a fresh enqueue on a guess.
            return _COALESCE_WRITE_UNKNOWN
        new_count = min(count + 1, cap)
        body = self._format_deferred_message(new_count, cap, instance_status, run_number)
        # The scrub is a HARD gate: on a scrub failure the write is SKIPPED
        # entirely (the raw body is never persisted — the house scrub
        # doctrine).
        scrubbed = self._scrub_body(body, instance_id)
        if scrubbed is None:
            logger.error(
                "Scheduler %s: the coalesced body could not be scrubbed — the "
                "content write is SKIPPED (no raw body is persisted) and this "
                "fire is not re-dispatched (the pending delivery is still "
                "live); the next fire retries.",
                self.source_id,
            )
            return _COALESCE_SKIPPED
        # W4: the metadata carries the SUBSTITUTED ``deferred-`` execution
        # id of the row THIS fire writes.
        deferred_exec_id = f"{_DEFERRED_EXECUTION_ID_PREFIX}{uuid.uuid4()}"
        metadata = self._deferred_metadata(deferred_exec_id, new_count, instance_status)
        try:
            ok = await asyncio.to_thread(
                mq_repo.update_ready_content,
                message_id,
                scrubbed,
                metadata,
            )
        except Exception as exc:  # noqa: BLE001
            # H1: the write outcome is UNKNOWN — we have NO rowcount proof
            # that the row is gone.  An unknown write outcome is NOT proof
            # of no-row: the pending delivery may still be live, so this
            # must NOT be returned as _COALESCE_ROLLOVER (which authorises
            # a fresh enqueue = a SECOND live delivery).  The caller skips
            # the fire (the pending delivery is left untouched) and retries
            # on the next tick — the age cap bounds it.
            logger.error(
                "Scheduler %s: coalesce_write_unknown — the coalescing write for "
                "%s FAILED (%s); the write outcome is UNKNOWN (not proof that "
                "the row is gone) — no fresh enqueue is opened, the pending "
                "delivery is left untouched and the next fire retries",
                self.source_id,
                message_id,
                exc,
            )
            return _COALESCE_WRITE_UNKNOWN
        if not ok:
            return _COALESCE_ROLLOVER
        # The count is committed to the carrier (the authoritative counter).
        if not await self._apply_defer_state(count_delta=1, count_cap=cap):
            # H3 (the unnamed call sites are gated too): the delivery was
            # ALREADY rewritten (the body stands) — the carrier write only
            # lost the COUNT, so the delivered body may under-report the
            # occurrence count.  W-B: the count FREEZES and under-reports
            # by one per failed commit; the COUNT CAP bounds the DISPLAY
            # (the saturation marker), NOT the under-report.  Conservative
            # and non-destructive: the delivery is not touched.
            logger.error(
                "Scheduler %s: carrier_count_update_failed — the coalesced "
                "occurrence count could not be committed to the carrier; the "
                "delivery was rewritten (the delivered body may under-report "
                "the count — the count freezes and under-reports by one per "
                "failed commit; the count cap bounds the DISPLAY, not the "
                "under-report)",
                self.source_id,
            )
        # One DEFERRED audit row per coalesced fire (the row cost is
        # disclosed — plan §(f) under-disclosure 2).
        # W1: the audit-row write failure must not make the fire INVISIBLE
        # (DEC-4 row traceability) — it is recorded as a gap.
        if not self._record_execution_start(deferred_exec_id, instance_id):
            logger.error(
                "Scheduler %s: the coalesced fire's audit row could not be "
                "written — the fire is AUDIT-GAP (DEC-4 row traceability); "
                "the delivery itself was coalesced (execution_id=%s).",
                self.source_id,
                deferred_exec_id,
            )
        self._record_execution_complete(
            deferred_exec_id, ExecutionStatus.DEFERRED.value, reason
        )
        logger.warning(
            "Scheduler %s: deferred fire coalesced into the pending delivery "
            "%s (count=%s, target=%s)",
            self.source_id,
            message_id,
            new_count,
            instance_id,
        )
        return _COALESCE_COALESCED

    async def _defer_delivery(
        self,
        execution_id: str,
        instance_id: str,
        instance_status: str | None,
        reason: str,
    ) -> str:
        """Defer the scheduled alarm to the PINNED caller.

        The target is resolved by the busy check (``_is_instance_active``)
        — or, on the semaphore-timeout path, by the read-only
        ``_resolve_deferrable_target()`` — the mapper is NEVER re-entered
        at defer time (architect §3.1: the mapper's mapping-miss branch is
        the only stranger-spawn surface).

        The busy fire's ``execution_id`` parameter is deliberately unused:
        the deferred audit row mints its own ``deferred-`` id (see the
        ``_deferred_metadata`` contract).

        Returns the ``_fire_outcome`` for this fire.
        """
        if self._source_repo is None or self._manager is None:
            # No substrate for a durable delivery (the adapter is used in
            # isolation in some unit tests) — the caller keeps the legacy
            # skip behaviour.
            self._fire_outcome = self.FIRE_OUTCOME_FAILED
            return self._fire_outcome

        cap, _max_age = self._defer_bounds()
        state = self._defer_state()
        gen = int(state.get("_defer_gen") or 0)
        count = int(state.get("_defer_count") or 0)
        marker = state.get("_defer_message_id") or None
        run_number = self._display_run_number(state)

        # NOTE (C1 — SINGLE EVALUATION SITE): the age-cap breach is NOT
        # evaluated here.  It is evaluated EXACTLY ONCE per fire, at the top
        # of ``_emit_scheduled_message`` — the carrier opens at FIVE sites
        # (the deferral, the audit-write failure, the enqueue exception, the
        # taskless enqueue and the semaphore re-arm) and only ONE of them is
        # on this path, so a second evaluation site would both duplicate
        # the work and risk a double consumption.

        # (B) COALESCE into the live delivery.
        if marker:
            live = self._delivery_task_is_live(marker)
            if live is _LIVENESS_UNKNOWN:
                # H2: the liveness read did not answer — UNKNOWN, not "not
                # live".  Opening a fresh generation here would mint a
                # SECOND live delivery while the pending one may still be
                # live.  Skip the fire (the pending delivery is left
                # untouched) and retry on the next tick — the age cap
                # bounds it (the token ``liveness_read_unknown`` was
                # logged by the read itself).
                await self._open_episode()
                self._fire_outcome = self.FIRE_OUTCOME_REARMED
                return self._fire_outcome
            if live:
                coalesce_result = await self._coalesce_into(
                    marker, count, cap, instance_id, instance_status, reason, run_number
                )
                if coalesce_result == _COALESCE_COALESCED:
                    self._fire_outcome = self.FIRE_OUTCOME_DEFERRED_DURABLE
                    return self._fire_outcome
                if coalesce_result == _COALESCE_SKIPPED:
                    # The body could not be scrubbed -> the content write was
                    # SKIPPED (the raw body is never persisted).  This fire is
                    # NOT re-dispatched: the pending delivery is still live and
                    # a fresh enqueue would open a SECOND live delivery (an
                    # at-most-once breach).  The outcome is non-consuming; the
                    # next fire retries.
                    await self._open_episode()
                    self._fire_outcome = self.FIRE_OUTCOME_FAILED
                    return self._fire_outcome
                if coalesce_result == _COALESCE_WRITE_UNKNOWN:
                    # H1: the coalescing write's outcome is UNKNOWN (not
                    # proof that the row is gone).  NO fresh enqueue is
                    # opened (the pending delivery may still be live), the
                    # pending delivery is left untouched and the next fire
                    # retries — the age cap bounds it.
                    await self._open_episode()
                    self._fire_outcome = self.FIRE_OUTCOME_REARMED
                    return self._fire_outcome
                # ROLLOVER (rowcount 0 — the row was claimed / consumed / gone).
                logger.info(
                    "Scheduler %s: the deferred delivery %s is gone — rolling over "
                    "to a fresh generation",
                    self.source_id,
                    marker,
                )

        # (C) ENQUEUE — the first fire of a generation, a rollover, or the
        # marker-miss fallback.
        if marker:
            # ROLLOVER: the marker points at a delivery that is gone -> a
            # NEW generation (new key, the count resets to 1).
            key_gen = gen + 1
            bump_gen = True
            reset_count = True
        elif gen >= 1:
            # MARKER-MISS FALLBACK (architect R3 / DEC-18 item 4): the
            # marker write was lost (the crash window) but a live delivery
            # may still exist.  Enqueueing under the CURRENT generation key
            # produces a DEDUP HIT that returns the KEPT ``message_id``
            # (``instance_messaging`` returns the original id, never the
            # phantom's) -> persist it -> the next coalesce corrects the
            # kept body.  NO loss, NO duplicate.
            key_gen = gen
            bump_gen = False
            reset_count = False
        else:
            key_gen = 1
            bump_gen = True
            reset_count = True

        deferred_exec_id = f"{_DEFERRED_EXECUTION_ID_PREFIX}{uuid.uuid4()}"
        key = self._deferred_idempotency_key(key_gen)
        body = self._format_deferred_message(1, cap, instance_status, run_number)
        metadata = self._deferred_metadata(deferred_exec_id, 1, instance_status)

        # ROW FIRST (L-1 / DEC-18): the audit row is INSERTed BEFORE the
        # dispatch — the fail-closed ordering.
        if not self._record_execution_start(deferred_exec_id, instance_id):
            # §15.8 (L-1): an audit-row write failure FAILS CLOSED — the
            # dispatch does NOT happen (NO enqueue), the outcome is
            # NON-consuming (``failed``), the episode carrier opens (as on
            # every other non-delivery leg) and the schedule re-arms on the
            # existing 5s cadence.  The alarm is never silently dispatched
            # without its audit row.
            await self._open_episode()
            self._fire_outcome = self.FIRE_OUTCOME_FAILED
            logger.error(
                "Scheduler %s: the DEFERRED audit row could not be written "
                "(audit_write_failed) — the dispatch is REFUSED (no enqueue); "
                "the alarm is NOT consumed and the schedule re-arms",
                self.source_id,
            )
            return self._fire_outcome
        # The scrub is a HARD gate here too: the RAW body is never
        # persisted (the house scrub doctrine).  On a scrub failure the
        # dispatch does NOT happen.
        scrubbed = self._scrub_body(body, instance_id)
        if scrubbed is None:
            self._record_execution_complete(
                deferred_exec_id,
                ExecutionStatus.FAILED.value,
                "scrub_failed: the body could not be scrubbed; no dispatch",
            )
            await self._open_episode()
            self._fire_outcome = self.FIRE_OUTCOME_FAILED
            logger.error(
                "Scheduler %s: the deferred body could not be scrubbed — the "
                "dispatch is REFUSED (no enqueue, no raw body persisted); the "
                "alarm is NOT consumed and the schedule re-arms",
                self.source_id,
            )
            return self._fire_outcome
        try:
            result = await self._manager.enqueue_message_job(
                instance_id=instance_id,
                message=scrubbed,
                source="scheduler",
                priority=self._priority,
                metadata=metadata,
                idempotency_key=key,
            )
        except Exception as exc:  # noqa: BLE001 — the failure leg is terminal
            # L-1 consequence (a): the row completes FAILED, the one-shot is
            # NOT consumed, and the intent survives (re-arm).
            self._record_execution_complete(
                deferred_exec_id,
                ExecutionStatus.FAILED.value,
                f"defer_enqueue_failed: {exc}",
            )
            await self._open_episode()
            self._fire_outcome = self.FIRE_OUTCOME_FAILED
            logger.error(
                "Scheduler %s: the deferred enqueue failed (%s) — the alarm "
                "was NOT consumed and the schedule re-arms",
                self.source_id,
                exc,
            )
            return self._fire_outcome

        # DURABLE EVIDENCE (R2 / Blocking 1): a PENDING Task whose
        # ``message_id`` EQUALS the id THIS enqueue returned.  A real
        # ``message_id`` alone is NOT evidence — the question-pause marker
        # window produces a real id with NO Task.
        message_id = getattr(result, "message_id", None)
        task = None
        task_repo = self._task_repository()
        if task_repo is not None and message_id:
            try:
                task = await asyncio.to_thread(task_repo.get_by_message, message_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Scheduler %s: durable-evidence lookup failed for %s: %s",
                    self.source_id,
                    message_id,
                    exc,
                )
                task = None
        task_status = getattr(task, "status", None)
        if task is None or task_status != "pending":
            # TASK-LESS ENQUEUE (R7 / Blocking 1): the enqueue returned a
            # real id but left NO PENDING Task -> the delivery is STRANDED
            # (a 'ready' row with no Task never drains).  Classify it
            # ``failed`` / ``taskless_enqueue``, clean the row up, consume
            # NOTHING and re-arm.
            residue = ""
            mq_repo = self._queue_repository()
            if mq_repo is not None and message_id:
                try:
                    await asyncio.to_thread(mq_repo.delete, message_id)
                except Exception as exc:  # noqa: BLE001
                    residue = (
                        f" (the stranded message_queue row {message_id} could "
                        f"not be deleted: {exc})"
                    )
                    logger.warning(
                        "Scheduler %s: taskless enqueue cleanup failed for %s: %s",
                        self.source_id,
                        message_id,
                        exc,
                    )
            self._record_execution_complete(
                deferred_exec_id,
                ExecutionStatus.FAILED.value,
                "taskless_enqueue",
            )
            await self._open_episode()
            self._fire_outcome = self.FIRE_OUTCOME_FAILED
            logger.warning(
                "Scheduler %s: the deferred enqueue left NO pending Task "
                "(taskless_enqueue) — the delivery was not created%s; the "
                "alarm is NOT consumed and the schedule re-arms",
                self.source_id,
                residue,
            )
            return self._fire_outcome

        # DURABLE — persist the marker, the generation and the count.
        # H3: the carrier write is GATED — the DELIVERY is durable either
        # way (the PENDING Task is the durable evidence), so a failed
        # carrier write must not suppress it; but the episode is then
        # UNTRACKED (the 24h age cap cannot fire on it) and the fire must
        # be LOUD about it.  The in-memory clock keeps the cap able to
        # terminate the episode for this adapter's lifetime.
        now_iso = datetime.now(timezone.utc).isoformat()
        if not await self._apply_defer_state(
            count_delta=1,
            count_cap=cap,
            gen_delta=1 if bump_gen else 0,
            reset_count=reset_count,
            set_message_id=message_id,
            set_since=now_iso,
        ):
            # B1: SET-IF-ABSENT — the in-memory clock measures the age of
            # the FIRST failed carrier write and ACCUMULATES from there.  A
            # plain overwrite would re-stamp it on every ~5s re-arm tick,
            # so the visible age would stay below the cap FOREVER and the
            # bound this clock exists to provide would be ZERO.
            self._defer_since_in_memory = self._defer_since_in_memory or now_iso
            logger.error(
                "Scheduler %s: carrier_marker_write_failed — the deferred "
                "delivery's marker could not be persisted; the episode is "
                "UNTRACKED (the 24h age cap is kept alive by the in-memory "
                "clock, whose age ACCUMULATES from the FIRST failed carrier "
                "write, for this adapter's lifetime) — the delivery itself is "
                "durable",
                self.source_id,
            )
        self._record_execution_complete(
            deferred_exec_id, ExecutionStatus.DEFERRED.value, reason
        )
        logger.warning(
            "Scheduler %s: scheduled fire DEFERRED to the pinned caller %s "
            "(status=%s, message_id=%s, generation=%s, reason=%s)",
            self.source_id,
            instance_id,
            instance_status,
            message_id,
            key_gen,
            reason,
        )
        self._fire_outcome = self.FIRE_OUTCOME_DEFERRED_DURABLE
        return self._fire_outcome

    async def _open_episode(self) -> bool:
        """Open (or continue) the NON-DELIVERY episode carrier.

        ``_defer_since`` is SET-IF-ABSENT (the repository's COALESCE
        semantics) at the FIRST non-delivery outcome of ANY kind — defer ·
        re-arm · direct-failure · taskless-enqueue (decisions.md DEC-9's
        ONE enumeration / DEC-22).

        H3(i) — the write is GATED, not swallowed: on failure the episode
        is UNTRACKED (the 24h age cap cannot fire on it), so the caller
        must be LOUD about it.  The IN-MEMORY clock below keeps the cap
        able to fire for the adapter's lifetime (without it a broken
        carrier write would produce an unbounded 5s re-arm spin — the
        reviewer's H3(i) hazard).  The clock is SET-IF-ABSENT (B1): its age
        ACCUMULATES from the FIRST failed carrier write, so the cap really
        can trip on an untracked episode.  Delivery-first: the deferred
        delivery is still enqueued when the enqueue path itself works (a
        different write may succeed), but the fire is never silently
        "tracked".

        Returns:
            ``True`` when the carrier was written, ``False`` when the
            episode is UNTRACKED (the token ``episode_open_failed``).
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        ok = await self._apply_defer_state(set_since=now_iso)
        if not ok:
            # The episode is untracked in the DB — keep an in-memory clock
            # so the age cap can still terminate it (the bound).  B1:
            # SET-IF-ABSENT — the clock's age ACCUMULATES from the FIRST
            # failed carrier write; a plain overwrite would re-stamp it on
            # every re-arm tick and the age would never reach the cap.
            self._defer_since_in_memory = self._defer_since_in_memory or now_iso
            logger.error(
                "Scheduler %s: episode_open_failed — the non-delivery episode "
                "could not be OPENED in the carrier; the episode is UNTRACKED "
                "(the 24h age cap is kept alive by the in-memory clock, whose "
                "age ACCUMULATES from the FIRST failed carrier write, for this "
                "adapter's lifetime)",
                self.source_id,
            )
            return False
        # The DB is authoritative again — the in-memory fallback retires.
        self._defer_since_in_memory = None
        return True

    async def _clear_episode(self) -> bool:
        """Close the episode carrier (a delivered occurrence).

        H3(ii) — GATED, not swallowed: on failure the carrier lingers, so
        the terminal disposition could be re-written on every recurring
        fire.  The idempotency guard in ``_terminate_age_cap_episode``
        (the episode-scoped check before writing the terminal row — W-A)
        keeps that from duplicating rows.

        Returns:
            ``True`` when the carrier was cleared, ``False`` on a write
            failure (the token ``episode_clear_failed``).
        """
        ok = await self._apply_defer_state(
            clear_since=True, clear_message_id=True, reset_count=True
        )
        if not ok:
            logger.error(
                "Scheduler %s: episode_clear_failed — the carrier could not be "
                "CLEARED; the episode lingers (the terminal disposition is "
                "guarded against duplication by the episode-scoped check)",
                self.source_id,
            )
            return False
        self._defer_since_in_memory = None
        return True

    async def _acquire_execution_slot(
        self,
        timeout: float,
        execution_id: str,
        *,
        defer_target: tuple[str, str | None] | None = None,
        scheduled: bool = False,
    ) -> bool:
        """Try to acquire execution semaphore with timeout.

        Returns True if slot acquired, False if timed out.

        R9 (decisions.md DEC-2 / ADR-10): the semaphore-timeout skip fires
        BEFORE the reuse check, so it is reachable for EVERY instance_mode
        (probe P5).  On the SCHEDULED path it therefore has TWO branches:

        1. a deferrable target (reuse-mode with a resolvable mapping) ->
           DEFER to the pinned caller (the identical mechanism as the busy
           branch);
        2. no deferrable target (new_instance / no mapping) -> RE-ARM: the
           execution row is written (start -> complete, ``deferred`` + a
           reason), the one-shot is NEVER consumed, the non-delivery episode
           carrier is opened (DEC-22) and the branch takes the MANDATORY 5s
           sleep (the re-arm cadence — a past-due one-shot re-triggers
           immediately, so without the sleep this is a hot loop).

        The MANUAL trigger twin keeps its immediate-execution semantics
        (DEC-3's disclosed non-goal): it passes no deferrable target and
        keeps the legacy SKIPPED callback.
        """
        if self._execution_semaphore is None:
            logger.error("Scheduler not properly initialized")
            return False

        try:
            await asyncio.wait_for(
                self._execution_semaphore.acquire(),
                timeout=timeout
            )
            logger.debug(f"Acquired execution slot: {execution_id}")
            return True
        except asyncio.TimeoutError:
            if scheduled:
                if defer_target is not None:
                    # Branch 1 — DEFER to the pinned caller.
                    instance_id, instance_status = defer_target
                    await self._defer_delivery(
                        execution_id,
                        instance_id,
                        instance_status,
                        reason="semaphore_timeout: max concurrent executions reached",
                    )
                    return False
                if self._source_repo is not None and self._manager is not None:
                    # Branch 2 — RE-ARM (never consume silently).
                    await self._rearm_after_semaphore_timeout(execution_id)
                    return False
            # Legacy path (the manual twin, or an adapter without the
            # delivery substrate): the historical SKIPPED callback.
            logger.warning(
                f"Skipping execution {execution_id}: max concurrent executions reached "
                f"(running={self._running_executions}, max={self._max_concurrent})"
            )
            if self._execution_callback:
                try:
                    self._execution_callback(
                        execution_id=execution_id,
                        schedule_id=self.source_id,
                        status=ExecutionStatus.SKIPPED.value,
                        instance_id=None,
                        error_message="Max concurrent executions reached",
                    )
                except Exception as e:
                    logger.warning(f"Execution callback error: {e}")
            return False

    async def _rearm_after_semaphore_timeout(self, execution_id: str) -> None:
        """The semaphore no-target branch: RE-ARM (R9 / DEC-2 branch 2).

        The execution row is written (start -> complete) with status
        ``deferred`` and a reason, the one-shot is NEVER consumed, the
        non-delivery episode carrier is opened (DEC-22 — the re-arm leg is
        under the 24h age cap too) and the branch takes the MANDATORY 5s
        sleep (the re-arm cadence).
        """
        rearm_id = f"{_DEFERRED_EXECUTION_ID_PREFIX}{uuid.uuid4()}"
        reason = "semaphore_timeout: max concurrent executions reached; re-armed"
        # W2: the audit-row write failure must not make the fire INVISIBLE
        # (DEC-4 row traceability) — it is recorded as a gap.
        if not self._record_execution_start(rearm_id, None):
            logger.error(
                "Scheduler %s: the re-armed fire's audit row could not be "
                "written — the fire is AUDIT-GAP (DEC-4 row traceability; "
                "rearm_id=%s).",
                self.source_id,
                rearm_id,
            )
        self._record_execution_complete(
            rearm_id, ExecutionStatus.DEFERRED.value, reason
        )
        await self._open_episode()
        self._fire_outcome = self.FIRE_OUTCOME_REARMED
        self._rearm_cadence_slept = True
        logger.warning(
            "Scheduler %s: the execution slot was saturated and no deferrable "
            "target exists — the schedule RE-ARMS (the one-shot is not "
            "consumed; reason=%s)",
            self.source_id,
            reason,
        )
        # The MANDATORY re-arm cadence (mirrors the PAST_LATENESS_CAP
        # pattern): a past-due one-shot returns ``now`` from
        # ``_get_next_trigger_time`` -> without the sleep this is a 0.2 Hz
        # hot loop.
        await asyncio.sleep(SCHEDULER_ERROR_RETRY_S)

    def _resolve_deferrable_target(self) -> tuple[str, str | None] | None:
        """READ-ONLY resolution of the pinned caller (R9 / DEC-2 / W5).

        Resolves the bound instance id WITHOUT re-entering
        ``mapper.get_or_create_instance`` — the mapper's mapping-miss
        branch is the only stranger-spawn surface (architect §3.1).  A
        resolution that would otherwise create or spawn is treated as "no
        deferrable target" -> branch 2 (RE-ARM).
        """
        if self._instance_mode != SchedulerInstanceMode.REUSE_INSTANCE:
            return None
        if not self._source_repo or not self._instance_repo:
            return None
        try:
            mapping = self._source_repo.get_instance_mapping(
                self.source_id, self.source_id
            )
        except Exception as exc:  # noqa: BLE001 — a read failure is not fatal
            logger.warning(
                "Scheduler %s: mapping read failed: %s", self.source_id, exc
            )
            return None
        if not mapping:
            return None
        instance_id = getattr(mapping, "agent_instance_id", None)
        if not instance_id:
            return None
        try:
            instance = self._instance_repo.get(instance_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Scheduler %s: instance read failed for %s: %s",
                self.source_id,
                instance_id,
                exc,
            )
            return None
        if not instance:
            return None
        return instance_id, getattr(instance, "status", None)
    
    async def _execute_run(self, execution_id: str, trigger_type: str) -> None:
        """Shared execution logic for scheduled and manual triggers.
        
        Args:
            execution_id: Unique execution identifier
            trigger_type: "scheduled" or "manual"
        
        Queue routing is ONLY for "scheduled" triggers with project_id configured.
        Manual triggers always use immediate execution (deliberate design).
        """
        self._running_executions += 1
        logger.debug(f"Starting execution {execution_id}, running={self._running_executions}")
        
        try:
            # Call execution callback with triggered status
            if self._execution_callback:
                try:
                    self._execution_callback(
                        execution_id=execution_id,
                        schedule_id=self.source_id,
                        status=ExecutionStatus.TRIGGERED.value,
                        instance_id=None,
                        error_message=None,
                    )
                except Exception as e:
                    logger.warning(f"Execution callback error: {e}")
            
            # Determine instance mode and run number
            run_number: int | None = None
            if self._instance_mode == SchedulerInstanceMode.REUSE_INSTANCE:
                if self._source_repo:
                    run_number = self._source_repo.increment_scheduler_run_counter(self.source_id)
                    if run_number is None:
                        run_number = 1
                else:
                    run_number = 1
                logger.info(f"reuse_instance mode: run_number={run_number} for {self.source_id}")
            
            # Format message based on session mode
            if self._instance_mode == SchedulerInstanceMode.REUSE_INSTANCE and run_number:
                formatted_message = self._format_continuation_message(self._message_content, run_number)
            else:
                formatted_message = self._message_content
            
            # Determine force_new_instance flag
            force_new_instance = self._instance_mode == SchedulerInstanceMode.NEW_INSTANCE
            
            # Build metadata
            metadata = {
                "scheduler": {
                    "execution_id": execution_id,
                    "trigger_type": trigger_type,
                    "trigger_time": datetime.now(self._timezone).isoformat(),
                    "instance_mode": self._instance_mode.value,
                    "run_number": run_number,
                },
                "agent": self._agent,
                "force_new_instance": force_new_instance,
            }
            
            # Add schedule details to metadata for scheduled triggers
            if trigger_type == "scheduled":
                if self._schedule_type == self.SCHEDULE_TYPE_CRON:
                    metadata["scheduler"]["schedule_type"] = self._schedule_type
                    metadata["scheduler"]["cron_expression"] = self._cron_expression
                elif self._schedule_type == self.SCHEDULE_TYPE_INTERVAL:
                    metadata["scheduler"]["schedule_type"] = self._schedule_type
                    metadata["scheduler"]["interval_seconds"] = self._interval_seconds
                elif self._schedule_type == self.SCHEDULE_TYPE_ONE_TIME:
                    metadata["scheduler"]["schedule_type"] = self._schedule_type
                    metadata["scheduler"]["run_at"] = self._run_at.isoformat()
            
            # Route through inline message-Job dispatch (scheduled only).
            # Phase 5 (cutover): the inline path is the only path; the
            # legacy TASK-job poll-loop was removed. ``_project_id`` is
            # still required (it scopes the InstanceMapper lookup).
            #
            # The job-queue route is the ONLY route that produces a DURABLE
            # delivery (MessageQueue + Task + JobItem).  A reuse-mode
            # schedule ALWAYS has a pinned caller to wake, so it takes the
            # job-queue route even without a ``project_id`` — routing it
            # through the immediate path (the registry's ``on_message``
            # handler) hands the alarm to a handler that may never enqueue
            # it.  The MANUAL twin keeps its immediate-execution semantics
            # (DEC-3's disclosed non-goal).
            if (
                trigger_type == "scheduled"
                and self._manager is not None
                and (
                    self._project_id
                    or self._instance_mode == SchedulerInstanceMode.REUSE_INSTANCE
                )
            ):
                await self._route_via_job_queue(execution_id, formatted_message, metadata)
            else:
                await self._execute_immediate(execution_id, formatted_message, metadata)

        except asyncio.CancelledError:
            logger.info(f"Execution cancelled: {execution_id}")
            raise  # finally still runs, semaphore released
        except BoundCallerRefusal as refusal:
            # CRIT-1 (decisions.md DEC-9): the mapper's TYPED refusal is
            # classified, NOT swallowed by the blanket handler below.
            # ``refused_permanent`` is a TERMINAL outcome: the condition is
            # PERMANENT (the bound caller is CONFIRMED gone), so the
            # one-shot IS consumed — keeping the intent alive would be a 5s
            # spin against a dead target, not a safety property.  The
            # end-state (a ``failed`` row carrying the refusal reason +
            # the consumption) is IDENTICAL to the blanket path's; only the
            # vocabulary differs.
            logger.warning(
                "Scheduled fire %s refused: %s — the one-shot is consumed "
                "(outcome=%s)",
                execution_id,
                getattr(refusal, "reason", str(refusal)),
                self.FIRE_OUTCOME_REFUSED_PERMANENT,
            )
            self._fire_outcome = self.FIRE_OUTCOME_REFUSED_PERMANENT
            if self._execution_callback:
                try:
                    self._execution_callback(
                        execution_id=execution_id,
                        schedule_id=self.source_id,
                        status=ExecutionStatus.FAILED.value,
                        instance_id=None,
                        error_message=str(getattr(refusal, "reason", refusal)),
                    )
                except Exception as cb_error:
                    logger.warning(f"Execution callback error: {cb_error}")
            # A terminal disposition CLOSES the non-delivery episode (the
            # carrier clears on a delivered occurrence OR a terminal
            # disposition — decisions.md DEC-9's cap block).
            await self._clear_episode()
        except Exception as e:
            logger.error(f"Failed to execute {trigger_type} message: {execution_id}, error={e}", exc_info=True)
            self._fire_outcome = self.FIRE_OUTCOME_FAILED
            if self._execution_callback:
                try:
                    self._execution_callback(
                        execution_id=execution_id,
                        schedule_id=self.source_id,
                        status=ExecutionStatus.FAILED.value,
                        instance_id=None,
                        error_message=str(e),
                    )
                except Exception as cb_error:
                    logger.warning(f"Execution callback error: {cb_error}")
        finally:
            self._running_executions -= 1
            logger.debug(f"Execution {execution_id} finished, running={self._running_executions}")
            if self._execution_semaphore:
                self._execution_semaphore.release()

    async def _route_via_job_queue(self, execution_id: str, formatted_message: str, metadata: dict) -> None:
        """Route scheduled execution through the inline message-Job path.

        Phase 5 (cutover): the inline path is the ONLY path. Get/create
        the instance via :class:`InstanceMapper`, then enqueue the
        message-Job via :meth:`InstanceManager.enqueue_message_job`.
        This creates a ``JobItem`` mirror alongside the ``Task`` row
        and dispatches inline (no poll-loop wait). The legacy TASK-job
        poll-loop path through :meth:`JobQueueService.enqueue` was
        removed in Phase 5 — messages must always carry a JobItem now
        so the public facade can read both sides of the union.
        """
        try:
            agent_id = self._agent
            if not agent_id:
                agent_id = metadata.get("agent")

            assert agent_id is not None and agent_id != "", "agent_id must be set"
            registry = get_registry()
            resolved_agent_id = registry.resolve_to_id(agent_id)
            if resolved_agent_id:
                agent_id = resolved_agent_id

            assert self._manager is not None, (
                "InstanceManager is required for the inline message-Job dispatch path"
            )

            # Phase 5 (cutover): inline path is the only path. Reuses the
            # same mapper pattern as the external-source chokepoint so
            # the mapping lifecycle (mapping check, stale-mapping
            # recovery, force_new handling) is identical.
            from daemon.sources.mapper import InstanceMapper

            # self._source_repo is SQLModelSourceRepository at
            # runtime (the constructor accepts the abstract
            # SourceRepository type for testability); cast through
            # type ignore since the LSP can't narrow the union.
            mapper = InstanceMapper(self._source_repo, self._manager)  # type: ignore[arg-type]
            force_new = self._instance_mode == SchedulerInstanceMode.NEW_INSTANCE
            instance_id = await mapper.get_or_create_instance(
                source_id=self.source_id,
                external_user_id=self.source_id,
                agent_id=agent_id,
                force_new=force_new,
            )

            # D4 idempotency (architecture §1.2/§1.3): derive a deterministic
            # key from ``self._run_at`` (the configured run_at parsed once in
            # ``__init__`` and re-parsed identically every boot at
            # ``_parse_schedule_config``), and pass it through to
            # ``enqueue_message_job`` so the JobQueueService's partial UNIQUE
            # index collapses any cross-restart or 5s-retry duplicate
            # enqueues into one JobItem. The key MUST be derived from
            # ``self._run_at``, NOT from ``next_trigger`` — the latter
            # returns ``now`` for past-due one-shots
            # (``_get_next_trigger_time`` line ~486–487), which would mutate
            # the key on every adapter cycle AND every boot, defeating the
            # at-most-once guarantee.
            #
            # Cron-path guard: ``_route_via_job_queue`` is shared by cron
            # AND one-shot paths (only gated by ``trigger_type ==
            # "scheduled"``). Recurring fires intentionally mint fresh
            # JobItems — emitting the key unconditionally would collapse
            # all future cron fires into one JobItem → silent schedule
            # death. Gate to one-time only (architecture §1.3).
            enqueue_kwargs: dict = {}
            if self._schedule_type == self.SCHEDULE_TYPE_ONE_TIME and self._run_at is not None:
                enqueue_kwargs["idempotency_key"] = (
                    f"scheduler:{self.source_id}:{self._run_at.isoformat()}"
                )

            result = await self._manager.enqueue_message_job(
                instance_id=instance_id,
                message=formatted_message,
                source="scheduler",
                priority=self._priority,
                images=None,  # scheduler does not currently pass images
                metadata=metadata,
                **enqueue_kwargs,
            )

            logger.info(
                f"Scheduled job enqueued (inline): source={self.source_id}, "
                f"execution_id={execution_id}, instance_id={instance_id[:8]}..., "
                f"job_id={result.job_id}, message_id={result.message_id[:8] if result.message_id else None}"
            )

            if self._execution_callback:
                try:
                    self._execution_callback(
                        execution_id=execution_id,
                        schedule_id=self.source_id,
                        status=ExecutionStatus.QUEUED.value,
                        instance_id=instance_id,
                        error_message=None,
                    )
                except Exception as e:
                    logger.warning(f"Execution callback error: {e}")
        except Exception as e:
            logger.error(f"Failed to queue scheduled job: {execution_id}, error={e}", exc_info=True)
            # Don't call callback here - let _execute_run's outer exception handler
            # call it to avoid double-callback for the same execution_id
            raise

    async def _execute_immediate(self, execution_id: str, formatted_message: str, metadata: dict) -> None:
        """Execute immediately (manual triggers always use this path)."""
        incoming = IncomingMessage(
            external_user_id=self.source_id,
            content=formatted_message,
            source_id=self.source_id,
            metadata=metadata,
            message_type="scheduled",
        )
        
        await self._emit_message(incoming)
        
        logger.info(
            f"Message executed (immediate): source={self.source_id}, "
            f"execution_id={execution_id}, agent={self._agent}"
        )
        
        if self._execution_callback:
            try:
                self._execution_callback(
                    execution_id=execution_id,
                    schedule_id=self.source_id,
                    status=ExecutionStatus.COMPLETED.value,
                    instance_id=self.source_id,
                    error_message=None,
                )
            except Exception as e:
                logger.warning(f"Execution callback error: {e}")
    
    async def _emit_scheduled_message(self) -> None:
        """Emit the scheduled message to the message handler.

        If project_id is configured and JobQueueService is available, routes
        through the job queue. Otherwise, uses immediate execution.

        The BUSY branch no longer DROPS the alarm (commission
        ``scheduler-deferred-alarm-delivery``): the fire becomes a DURABLE
        PENDING delivery to the pinned caller (a PENDING Task + a
        MessageQueue row + a JobItem) and is recorded in
        ``schedule_executions`` as ``deferred``.
        """
        execution_id = str(uuid.uuid4())
        # The per-fire outcome (DEC-9 CRIT-1) — reset for every fire.
        self._fire_outcome = self.FIRE_OUTCOME_DIRECT
        self._rearm_cadence_slept = False

        # R9 / W5: the deferrable target is resolved READ-ONLY at the
        # call site — the mapper is never re-entered (its mapping-miss
        # branch is the only stranger-spawn surface).
        defer_target = self._resolve_deferrable_target()

        # ── THE AGE CAP — THE SINGLE EVALUATION SITE (C1) ────────────────
        # DEC-19: the deferral bounds are checked at FIRE TICKS ONLY (no
        # polling loop).  The breach check is evaluated EXACTLY ONCE per
        # fire, HERE — before the semaphore acquire and before the busy
        # check — because the carrier OPENS at FIVE sites (the deferral,
        # the audit-write failure, the enqueue exception, the taskless
        # enqueue and the semaphore re-arm) and only ONE of them is on the
        # deferral path: the re-arm and the direct-failure legs never
        # re-enter ``_defer_delivery``.  A SECOND evaluation site would
        # duplicate the work and risk a double consumption (the carrier
        # clears on a breach, so a re-entry would no-op at best).
        #
        # The instance id is the PINNED caller when the read-only mapping
        # resolution found one; the re-arm leg has none BY CONSTRUCTION —
        # the resolution has ALREADY run above (its result is passed into
        # ``_acquire_execution_slot``) and that branch is reached only when
        # it returned ``None`` — no deferrable target exists.  The
        # case-3b/c disposition needs no instance.
        _cap, max_age = self._defer_bounds()
        breach = await self._handle_age_cap_breach(
            self._defer_state(),
            max_age,
            defer_target[0] if defer_target else None,
        )
        if breach is not None:
            # The episode terminated visibly — the fire is over (the
            # outcome carries the consumption decision).
            return

        # Try to acquire semaphore with timeout
        if not await self._acquire_execution_slot(
            SCHEDULER_SEMAPHORE_TIMEOUT_S,
            execution_id,
            defer_target=defer_target,
            scheduled=True,
        ):
            return

        semaphore_held = True
        try:
            # Check if mapped instance is still active (for reuse_instance mode)
            if self._instance_mode == SchedulerInstanceMode.REUSE_INSTANCE:
                is_active, instance_id, instance_status = self._is_instance_active()
                if is_active and instance_id:
                    if self._source_repo is not None and self._manager is not None:
                        # DEFERRAL (R1..R7): the busy caller RECEIVES the
                        # alarm as a durable pending delivery — it is never
                        # dropped, and the fire is audited.
                        await self._defer_delivery(
                            execution_id,
                            instance_id,
                            instance_status,
                            reason=(
                                f"busy_caller: instance still {instance_status}"
                            ),
                        )
                        return  # finally still runs, releases semaphore
                    # Legacy path (an adapter without the delivery
                    # substrate): the historical skip.
                    logger.warning(
                        f"Deferred delivery unavailable for scheduled execution "
                        f"{execution_id}: instance {instance_id} is still "
                        f"{instance_status} (reuse_instance mode)"
                    )
                    if self._execution_callback:
                        try:
                            self._execution_callback(
                                execution_id=execution_id,
                                schedule_id=self.source_id,
                                status=ExecutionStatus.SKIPPED.value,
                                instance_id=instance_id,
                                error_message=f"Instance still {instance_status}",
                            )
                        except Exception as e:
                            logger.warning(f"Execution callback error: {e}")
                    return  # finally still runs, releases semaphore

            # Execute the scheduled run (its finally handles semaphore release)
            await self._execute_run(execution_id, "scheduled")
            semaphore_held = False  # _execute_run's finally releases it
            if self._fire_outcome == self.FIRE_OUTCOME_DIRECT:
                # A DELIVERED occurrence closes the non-delivery episode
                # (decisions.md DEC-9's cap block — the carrier clears on a
                # delivered occurrence, never on a rollover).
                await self._clear_episode()
            elif self._fire_outcome == self.FIRE_OUTCOME_FAILED:
                # A direct failure opens the non-delivery episode (the ONE
                # enumeration: defer · re-arm · direct-failure ·
                # taskless-enqueue).
                await self._open_episode()
        finally:
            if semaphore_held and self._execution_semaphore:
                self._execution_semaphore.release()
    
    async def _execute_trigger(self, execution_id: str) -> None:
        """Execute a manual trigger.
        
        Manual triggers ALWAYS use immediate execution (no job queue).
        This is a deliberate design for immediate user feedback.
        
        Args:
            execution_id: Unique ID for this execution
        """
        # Try to acquire semaphore with timeout
        if not await self._acquire_execution_slot(SCHEDULER_MANUAL_SEMAPHORE_TIMEOUT_S, execution_id):
            # Callback already called inside _acquire_execution_slot() on timeout
            return
        
        semaphore_held = True
        try:
            # Check if mapped instance is still active (for reuse_instance mode)
            if self._instance_mode == SchedulerInstanceMode.REUSE_INSTANCE:
                is_active, instance_id, instance_status = self._is_instance_active()
                if is_active and instance_id:
                    logger.info(
                        f"Skipping manual trigger {execution_id}: instance {instance_id} "
                        f"is still {instance_status} (reuse_instance mode)"
                    )
                    if self._execution_callback:
                        try:
                            self._execution_callback(
                                execution_id=execution_id,
                                schedule_id=self.source_id,
                                status=ExecutionStatus.SKIPPED.value,
                                instance_id=instance_id,
                                error_message=f"Instance still {instance_status}",
                            )
                        except Exception as e:
                            logger.warning(f"Execution callback error: {e}")
                    return  # finally still runs, releases semaphore
            
            # Execute the manual trigger (its finally handles semaphore release)
            await self._execute_run(execution_id, "manual")
            semaphore_held = False  # _execute_run's finally releases it
        finally:
            if semaphore_held and self._execution_semaphore:
                self._execution_semaphore.release()
