"""Scheduler adapter for triggering agents on schedule."""

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
from daemon.models import SchedulerInstanceMode, InstanceStatus
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
    
    def __init__(
        self,
        config: SourceConfig,
        on_message: Callable[[IncomingMessage], Awaitable[None]],
        execution_callback: Callable | None = None,
        on_complete_callback: Callable[[str, bool], None] | None = None,
        job_queue_service: "JobQueueService" | None = None,
        source_repo: "SourceRepositoryType" | None = None,
        instance_repo: "SQLModelInstanceRepository" | None = None,
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
        """
        super().__init__(config, on_message)
        self._execution_callback = execution_callback
        self._on_complete_callback = on_complete_callback  # NEW
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
        
        # Timezone configuration
        timezone_str = scheduler_config.get("timezone", "UTC")
        try:
            self._timezone = ZoneInfo(timezone_str)
        except KeyError:
            logger.warning(f"Unknown timezone '{timezone_str}', defaulting to UTC")
            self._timezone = ZoneInfo("UTC")
        
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
    
    def _get_next_trigger_time(self) -> datetime | None:
        """Calculate next trigger time based on schedule type.
        
        Returns:
            datetime of next trigger, or None if no more triggers
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
            # If run_at is in the past, trigger now
            run_at = self._run_at
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=self._timezone)
            
            if run_at <= now:
                return now  # Trigger now
            return run_at
        
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
            
            # Route through JobQueueService if project_id configured (scheduled only)
            if trigger_type == "scheduled" and self._project_id and self._job_queue_service:
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
        """Route execution through job queue (scheduled triggers only)."""
        try:
            agent_id = self._agent
            if not agent_id:
                agent_id = metadata.get("agent")
            
            assert agent_id is not None and agent_id != "", "agent_id must be set"
            registry = get_registry()
            resolved_agent_id = registry.resolve_to_id(agent_id)
            if resolved_agent_id:
                agent_id = resolved_agent_id
            
            job_item = await self._job_queue_service.enqueue(
                agent_id=agent_id,
                message=formatted_message,
                source="scheduler",
                project_id=self._project_id,
                priority=self._priority,
                metadata=metadata,
            )
            
            logger.info(
                f"Scheduled job queued: source={self.source_id}, "
                f"execution_id={execution_id}, job_id={job_item.job_id}, "
                f"status={job_item.status}"
            )
            
            if self._execution_callback:
                try:
                    self._execution_callback(
                        execution_id=execution_id,
                        schedule_id=self.source_id,
                        status=ExecutionStatus.QUEUED.value,
                        instance_id=job_item.instance_id,
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
