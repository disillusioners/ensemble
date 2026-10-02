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
from typing import TYPE_CHECKING, Callable, Awaitable
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
from daemon.repositories.source.models import ExecutionStatus
from daemon.registry import get_registry
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
        # Force new_instance for one-time schedules
        if scheduler_config.get("run_at"):
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
                    self._is_one_time_executed = True
                    logger.info(f"One-time schedule executed: {self.source_id}")
                    # NEW: Notify completion so adapter can disable itself
                    if self._on_complete_callback:
                        try:
                            self._on_complete_callback(self.source_id, completed=True)
                        except Exception as e:
                            logger.warning(f"on_complete_callback failed: {e}")
                    break

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
    
    async def _acquire_execution_slot(self, timeout: float, execution_id: str) -> bool:
        """Try to acquire execution semaphore with timeout.
        
        Returns True if slot acquired, False if timed out.
        Logs appropriately in both cases.
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
            if trigger_type == "scheduled" and self._project_id:
                await self._route_via_job_queue(execution_id, formatted_message, metadata)
            else:
                await self._execute_immediate(execution_id, formatted_message, metadata)
            
        except asyncio.CancelledError:
            logger.info(f"Execution cancelled: {execution_id}")
            raise  # finally still runs, semaphore released
        except Exception as e:
            logger.error(f"Failed to execute {trigger_type} message: {execution_id}, error={e}", exc_info=True)
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
        """
        execution_id = str(uuid.uuid4())
        
        # Try to acquire semaphore with timeout
        if not await self._acquire_execution_slot(SCHEDULER_SEMAPHORE_TIMEOUT_S, execution_id):
            return
        
        semaphore_held = True
        try:
            # Check if mapped instance is still active (for reuse_instance mode)
            if self._instance_mode == SchedulerInstanceMode.REUSE_INSTANCE:
                is_active, instance_id, instance_status = self._is_instance_active()
                if is_active and instance_id:
                    logger.info(
                        f"Skipping scheduled execution {execution_id}: instance {instance_id} "
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
            
            # Execute the scheduled run (its finally handles semaphore release)
            await self._execute_run(execution_id, "scheduled")
            semaphore_held = False  # _execute_run's finally releases it
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
