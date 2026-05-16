"""Instance manager orchestrating all agent instances."""

import uuid
import logging
import asyncio
import re
import time
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from .config import Config
from .graph import build_instance_graph
from .loader import PromptCache, load_and_cache_prompt
from .utils import parse_think_tags, serialize_message, find_near_instance, DEFAULT_FUZZY_MATCH_DISTANCE  # noqa: F401
from .persistence import (
    get_instance_messages,
    get_checkpointer,
)
from .repositories import (
    SQLModelInstanceRepository,
    SQLModelProjectRepository,
    SQLModelSourceRepository,
    SQLModelMessageQueueRepository,
    DatabaseConfig,
    create_engine_from_config,
    create_project_repository,
    create_instance_repository,
    create_source_repository,
    create_message_queue_repository,
)
from .repositories.task.repository import TaskRepository
from .registry import get_registry

from .repositories.instance.repository import get_agent_name
from .repositories.instance.models import Instance, InstanceStatus
from .repositories.message_queue.models import MessageQueue, MessageStatus, MessageType
from .repositories.task.models import Task, TaskType, TaskStatus
from .repositories.event.models import Event, EventKind
from sqlmodel import Session
from sqlalchemy import text
from .tools import create_instance_tools
from .sources import SourceRegistry, ResponseDispatcher, SourceCleanup
from .services.live_event_hub import LiveEventHub
from .services.event_bus import EventBus
from .services.job_queue_service import DemandState
from .services.instance_lifecycle import InstanceLifecycleService
from .services.instance_messaging import InstanceMessagingService
from .services.child_reports import ChildReportsService
from .services.error_reporting import ErrorReportingService
from .services.cancellation import CancellationService
from .services.title_generation import TitleGenerationService
from .services.event_publisher import EventPublisherService
from .cancellation import (
    CancellationToken, 
    CancellationReason,
    OperationCancelledError
)
from .request_registry import ActiveRequestRegistry
from .compaction import ContextCompactor, CompactionContext
from .constants import WORKER_POOL_SIZE

# Worker pool imports (lazy import to avoid circular dependency)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .services.worker_pool import WorkerPool
    from .services.task_processor import TaskProcessor
    from .services.stale_task_recovery import StaleTaskRecovery



logger = logging.getLogger(__name__)

# TTL for releasing in-memory graph for paused instances (in minutes)
PAUSED_INSTANCE_TTL_MINUTES = 30


def _build_message_content(message: str, images: list[str] | None) -> str | list:
    """Build multimodal content array for messages with optional images.
    
    Args:
        message: The text content of the message.
        images: Optional list of base64 image data URIs.
        
    Returns:
        String message if no images, otherwise list with text and image_url blocks.
    """
    if images:
        content = [{"type": "text", "text": message}]
        for img in images:
            content.append({"type": "image_url", "image_url": {"url": img}})
        return content
    return message


# Patterns for extracting project keywords from messages
# Use [a-zA-Z][\w-]* to match identifiers starting with a letter (not partial words)
# Use (?!\w) to ensure identifier is not followed by another word character
_PROJECT_PATTERNS = [
    r'\b([a-zA-Z][\w-]*)(?!\w)\s+(?:project|prj|proj)',           # "abc project", "abc prj"
    r'\b([a-zA-Z][\w-]*)(?!\w)\s+(?:system|sys)',                  # "abc system", "abc sys"
    r'\b([a-zA-Z][\w-]*)(?!\w)\s+(?:app|application)',            # "abc app", "abc application"
    r'\b([a-zA-Z][\w-]*)(?!\w)\s+(?:service|svc)',                 # "abc service", "abc svc"
    r'\b([a-zA-Z][\w-]*)(?!\w)\s+(?:module|mod)',                 # "abc module", "abc mod"
    r'(?:project|prj|proj)\s+\b([a-zA-Z][\w-]*)(?!\w)',            # "project abc", "prj abc"
    r'(?:the\s+)?\b([a-zA-Z][\w-]*)(?!\w)\s+(?:repo|repository)', # "abc repo", "the abc repository"
]
_PROJECT_REGEX = re.compile('|'.join(_PROJECT_PATTERNS), re.IGNORECASE)


def extract_project_keywords(message: str) -> list[str]:
    """Extract potential project name keywords from a message.
    
    Looks for patterns like "X project", "X system", "X prj", etc.
    Also includes capitalized words that might be project names.
    
    Args:
        message: The user message to extract keywords from.
    
    Returns:
        List of potential project name keywords.
    """
    keywords = set()
    
    # Extract from patterns
    matches = _PROJECT_REGEX.findall(message)
    for match in matches:
        # match can be a tuple from groups, filter out empty strings
        for word in (match if isinstance(match, tuple) else (match,)):
            if word and len(word) > 1:  # Skip single chars
                keywords.add(word)
    
    # Extract capitalized words (potential proper nouns/project names)
    # Match words starting with uppercase followed by lowercase/numbers
    cap_pattern = r'\b([A-Z][a-z0-9]+)\b'
    cap_matches = re.findall(cap_pattern, message)
    for word in cap_matches:
        if len(word) > 2:  # Skip short words like "The", "For", etc.
            keywords.add(word)
    
    return list(keywords)


def format_project_context(project) -> str:
    """Format project info as context block for prepending to message.
    
    Args:
        project: ProjectData instance from repository.
    
    Returns:
        Formatted string with project JSON info.
    """
    import json
    
    # ProjectData has to_dict() method
    project_dict = project.to_dict() if hasattr(project, 'to_dict') else vars(project)
    return f"""## Related Project

```json
{json.dumps(project_dict, indent=2)}
```

"""


class ActivityCallbackHandler(BaseCallbackHandler):
    """Callback to update message activity during LLM/graph execution.
    
    This ensures long-running tasks are not incorrectly marked as "stuck"
    by the worker pool health checks, as long as there's recent activity.
    """
    
    def __init__(self, queue_repository, message_id: str, update_interval_seconds: float = 5.0):
        """Initialize with SQLModelMessageQueueRepository.
        
        Args:
            queue_repository: The message queue repository for activity updates.
            message_id: The message ID to update activity for.
            update_interval_seconds: Minimum seconds between activity updates.
        """
        self.queue_repository = queue_repository
        self.message_id = message_id
        self.update_interval = update_interval_seconds
        self._last_update = time.monotonic()
    
    def _maybe_update(self) -> None:
        """Throttled activity update to avoid excessive DB writes."""
        now = time.monotonic()
        if now - self._last_update >= self.update_interval:
            try:
                self.queue_repository.update_activity(self.message_id)
            except Exception as e:
                logger.warning(f"Failed to update activity for {self.message_id}: {e}")
            self._last_update = now
    
    def on_llm_start(self, serialized, prompts, **kwargs) -> None:
        self._maybe_update()
    
    def on_llm_new_token(self, token: str, **kwargs) -> None:
        self._maybe_update()
    
    def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        self._maybe_update()
    
    def on_tool_start(self, serialized, input_str, **kwargs) -> None:
        self._maybe_update()
    
    def on_tool_end(self, output, **kwargs) -> None:
        self._maybe_update()
    
    def on_chain_start(self, serialized, inputs, **kwargs) -> None:
        self._maybe_update()
    
    def on_chain_end(self, outputs, **kwargs) -> None:
        self._maybe_update()


class CancellationCallbackHandler(BaseCallbackHandler):
    """Callback that checks for cancellation at key points during LLM execution."""
    
    def __init__(
        self, 
        cancellation_token: CancellationToken,
        check_interval_tokens: int = 10
    ):
        self._token = cancellation_token
        self._check_interval = check_interval_tokens
        self._token_count = 0
    
    def _check_cancellation(self) -> None:
        """Check for cancellation and raise if cancelled."""
        self._token.check()
    
    def on_llm_start(self, serialized, prompts, **kwargs) -> None:
        """Check cancellation before LLM call."""
        self._check_cancellation()
    
    def on_llm_new_token(self, token: str, **kwargs) -> None:
        """Check cancellation periodically during streaming."""
        self._token_count += 1
        if self._token_count % self._check_interval == 0:
            self._check_cancellation()
    
    def on_tool_start(self, serialized, input_str, **kwargs) -> None:
        """Check cancellation before tool execution."""
        self._check_cancellation()
    
    def on_chain_start(self, serialized, inputs, **kwargs) -> None:
        """Check cancellation before chain step."""
        self._check_cancellation()


def _get_message_event_type(msg: dict) -> str:
    """Determine event type based on message content.

    Args:
        msg: Serialized message dict

    Returns:
        Event type string: "user_message" | "assistant_message" | "thinking" | "tool_call"
    """
    if msg.get("role") == "user":
        return "user_message"
    if msg.get("tool_calls"):
        return "tool_call"
    if msg.get("thinking") or msg.get("thinking_extracted"):
        return "thinking"
    return "assistant_message"


def _compute_message_content_hash(msg: dict) -> str:
    """Compute a hash of the key content fields for deduplication.

    Args:
        msg: Serialized message dict

    Returns:
        A string hash representing the message content
    """
    import hashlib
    import json

    # Key fields that matter for content comparison
    content_parts = {
        "content": msg.get("content"),
        "tool_calls": msg.get("tool_calls"),
        "role": msg.get("role"),
    }
    # Normalize: sort keys and remove None values for consistent hashing
    content_str = json.dumps(content_parts, sort_keys=True, default=str)
    return hashlib.md5(content_str.encode()).hexdigest()[:16]


@dataclass
class MessageResult:
    """Result of sending a message to an instance."""
    content: str
    thinking: str | None = None
    thinking_extracted: str | None = None  # Extracted from <think/> tags in content
    tool_calls: list[dict[str, Any]] | None = None


@dataclass
class AsyncMessageResult:
    """Result of async message enqueue."""
    message_id: str
    instance_id: str
    status: str = "queued"


class InstanceManager:
    """Manages all agent instances, their graphs, and lifecycle."""

    def __init__(self, config: Config):
        """Initialize the instance manager.

        Args:
            config: Configuration object with LLM, limits, and persistence settings.
        """
        self.config = config
        self.db_path = Path(config.persistence.db_path)
        self._checkpointer = None  # Lazy init - call await manager.initialize() to set
        self._checkpointer_db_path = Path(config.persistence.checkpointer_db_path)
        self._loop: asyncio.AbstractEventLoop | None = None  # Set during initialize()
        self.prompt_cache = PromptCache()

        # Initialize context compactor
        if self.config.compaction.enabled:
            self._compactor = ContextCompactor(
                config=self.config.compaction,
                llm_config={
                    "base_url": self.config.llm.base_url,
                    "api_key": self.config.llm.api_key,
                    "model": self.config.llm.model,
                    "model_vision": self.config.llm.model_vision,
                    "temperature": self.config.llm.temperature,
                    "request_timeout": self.config.llm.request_timeout,
                },
            )
            logger.info(
                f"Context compaction enabled: threshold={self.config.compaction.threshold}, "
                f"recent_window={self.config.compaction.recent_message_window}, "
                f"min_window={self.config.compaction.min_recent_window}"
            )
        else:
            self._compactor = None

        # Maps instance_id to tuple of (graph, agent_dir)
        self.instances: dict[str, tuple[CompiledStateGraph, str]] = {}

        # Maps instance_id to the asyncio.Task currently running the graph for that instance
        # Used to cancel graph execution when stop is called
        self._graph_tasks: dict[str, asyncio.Task] = {}

        # Maps (instance_id, msg_id) to the created_at timestamp from first emission.
        # Persists across _process_message_internal calls so re-emitted messages
        # keep their original timestamp instead of getting a fresh one.
        self._original_timestamps: dict[str, str] = {}

        # Maps (instance_id, msg_id) to a content hash for deduplication.
        # Used to detect if a message was updated between streaming and final state.
        self._emitted_message_content: dict[str, str] = {}

        # LLM concurrency setting
        self._llm_semaphore = asyncio.Semaphore(config.limits.llm_concurrency)

        # Create ONE shared database engine for all repositories
        # This prevents database lock contention when multiple components
        # (watchdog thread, async processors, etc.) access the same SQLite file
        db_config = DatabaseConfig.sqlite(db_path=str(self.db_path))
        self._engine = create_engine_from_config(db_config)
        
        # Create tables once for all repositories
        from sqlmodel import SQLModel
        
        # Import SchemaMigration to register it with SQLModel.metadata
        # This ensures the schema_migrations table is created
        from .migrations.models import SchemaMigration
        
        SQLModel.metadata.create_all(self._engine)
        
        # Run file-based migrations using MigrationRunner
        from .migrations.runner import MigrationRunner
        migration_runner = MigrationRunner(self._engine)
        applied = migration_runner.run_pending_migrations()
        if applied:
            logger.info(f"Applied {len(applied)} migrations: {applied}")

        # NEW: Message queue repository for SQLModel-based operations
        self._queue_repository = create_message_queue_repository(engine=self._engine, create_tables=False)
        
        # Development helper: discard all queued messages and tasks on startup
        if config.queue.discard_on_startup:
            msg_count = self._queue_repository.clear_all()
            logger.info(f"Discarded {msg_count} messages from queue (discard_on_startup=True)")
            
            # Also discard tasks (linked to messages)
            task_repo = TaskRepository(
                engine=self._engine,
                on_pending_task=lambda: self._worker_pool.notify_work() if self._worker_pool else None
            )
            task_count = task_repo.clear_all()
            logger.info(f"Discarded {task_count} tasks (discard_on_startup=True)")
        
        # NEW: Request registry for cancellation support
        self._request_registry = ActiveRequestRegistry()
        
        # NEW: EventBus for hybrid event delivery (DB + streaming)

        # NEW: Source repository for source config and session mapping management
        # Must be created before SourceRegistry
        self._source_repository = create_source_repository(engine=self._engine, create_tables=False)

        # NEW: Session repository for session management
        # Must be created before SourceRegistry for scheduler session mode
        self._instance_repository = create_instance_repository(engine=self._engine, create_tables=False)

        # NEW: Pluggable message sources system
        self.source_registry = SourceRegistry(
            source_repo=self._source_repository,
            manager=self,
            instance_repo=self._instance_repository,
        )
        
        # Create LiveEventHub for live-only SSE streaming
        self._live_hub = LiveEventHub()
        
        # Create EventBus for lifecycle event broadcasting to global subscribers
        # JobFeedbackObserver subscribes to this for job completion feedback
        from .repositories.event.repository import EventRepository
        event_repo = EventRepository(engine=self._engine)
        self._event_bus = EventBus(event_repo=event_repo)
        
        self.source_dispatcher = ResponseDispatcher(
            registry=self.source_registry,
            subscriber_id="response_dispatcher"
        )
        self._source_cleanup: SourceCleanup | None = None

        # NEW: Project repository for project context injection
        # Using the new repository layer with proper transaction management
        self._project_repository = create_project_repository(engine=self._engine, create_tables=False)
        # Keep backward compatible name for tools
        self.project_store = self._project_repository

        # NEW: Optional JobQueueService reference (set via set_job_queue_service)
        self._job_queue_service: Any = None
        self._job_queue_mgmt_service: Any = None
        self._dead_letter_service: Any = None

        # Worker pool for message queue redesign
        self._worker_pool: WorkerPool | None = None
        self._task_processor: TaskProcessor | None = None
        self._stale_recovery: StaleTaskRecovery | None = None

        # Shutdown flag for graceful shutdown
        self._shutting_down = False

        # ── Initialize Services ──────────────────────────────────────────────────
        # Services are initialized after all internal state is set up.
        # They receive references to the manager facade and required state.
        # Repositories/config are accessed through the manager facade so that tests
        # can mock manager._instance_repository and services see the mocks.

        # Cancellation service (no deps on other services)
        self._cancellation_service = CancellationService(
            manager=self,
        )

        # Event publisher service (no deps on other services)
        self._events_service = EventPublisherService(
            manager=self,
        )

        # Title generation service (depends on config, instance_repository via manager)
        self._title_gen_service = TitleGenerationService(
            manager=self,
            logger=logger,  # Pass manager's logger so tests can mock it
        )

        # Child reports service (depends on config, checkpointer, instance_repository via manager)
        self._child_reports_service = ChildReportsService(
            manager=self,
            events_service=self._events_service,
        )

        # Error reporting service (depends on config, repositories via manager)
        self._error_reporting_service = ErrorReportingService(
            manager=self,
            events_service=self._events_service,
        )

        # Instance messaging service (depends on many services)
        self._messaging_service = InstanceMessagingService(
            manager=self,
            cancellation_service=self._cancellation_service,
            child_reports_service=self._child_reports_service,
            events_service=self._events_service,
        )

        # Instance lifecycle service (depends on many services, including messaging)
        self._lifecycle_service = InstanceLifecycleService(
            manager=self,
            cancellation_service=self._cancellation_service,
            events_service=self._events_service,
            job_queue_service=self._job_queue_service,
        )

        # Update messaging service with lifecycle service reference
        # (lifecycle service needs messaging for some operations)
        # Note: This is done after both are created to avoid circular issues

        # NEW: CompletionRegistry for synchronous agent invoke-and-wait
        from .services.completion_registry import get_completion_registry
        self._completion_registry = get_completion_registry()

    @property
    def checkpointer(self):
        """Get the async checkpointer instance.
        
        The checkpointer is created lazily on first access and but it must be initialized explicitly via initialize().
        
        Returns:
            AsyncSqliteSaver checkpointer.
        """
        return self._checkpointer
    
    async def initialize(self) -> None:
        """Initialize the async checkpointer.
        
        Must be called after SessionManager construction, typically in the FastAPI
        lifespan startup. This ensures the async checkpointer is created within
        an async context.
        
        Note: The checkpointer uses a separate database file from the main
        application database to avoid SQLite lock contention.
        """
        self._loop = asyncio.get_running_loop()
        self._checkpointer = await get_checkpointer(self._checkpointer_db_path)
        # NEW: Set event loop for CompletionRegistry (thread-safe notification)
        self._completion_registry.set_event_loop(self._loop)
        # NEW: Schedule periodic stale cleanup (every 10 minutes)
        asyncio.create_task(self._cleanup_stale_completions())
        # NEW: Schedule periodic cleanup of paused instances exceeding TTL
        asyncio.create_task(self._cleanup_paused_instances())
        logger.info(f"SessionManager initialized with async checkpointer at {self._checkpointer_db_path}")

    async def _cleanup_stale_completions(self) -> None:
        """Background task to periodically clean stale CompletionRegistry entries."""
        while not self._shutting_down:
            try:
                await asyncio.sleep(600)  # Every 10 minutes
                self._completion_registry.cleanup_stale()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Stale completion cleanup failed: {e}")

    def release_paused_instance(self, instance_id: str) -> None:
        """Release in-memory graph for a paused instance after TTL expires.
        
        This removes the graph from memory while keeping the database record intact.
        The instance can be "hot resumed" if under TTL (graph still in memory),
        or "cold resumed" if over TTL (graph reloaded from checkpoint on next use).
        
        Args:
            instance_id: The ID of the paused instance to release.
        """
        # Remove from instances dict if present
        if instance_id in self.instances:
            del self.instances[instance_id]
            logger.info(f"Released in-memory graph for paused instance {instance_id[:8]}...")
        
        # Cancel any lingering graph task
        task = self._graph_tasks.pop(instance_id, None)
        if task is not None and not task.done():
            task.cancel()
            logger.debug(f"Cancelled lingering graph task for {instance_id[:8]}...")
        
        # Cancel any active requests (shouldn't exist for paused instance but safety first)
        self._request_registry.cancel_by_instance(
            instance_id, 
            CancellationReason.SESSION_TERMINATED
        )
    
    async def _cleanup_paused_instances(self) -> None:
        """Background task to release in-memory graphs for paused instances exceeding TTL."""
        while not self._shutting_down:
            try:
                await asyncio.sleep(600)  # Every 10 minutes
                if not self._instance_repository:
                    continue
                
                # List paused instances
                paused_instances, _ = self._instance_repository.list(status=InstanceStatus.PAUSED.value)
                now = datetime.utcnow()
                
                released_count = 0
                for instance in paused_instances:
                    # Only release if graph is in memory
                    if instance.instance_id not in self.instances:
                        continue
                    
                    # Skip if updated_at is missing or invalid
                    if not instance.updated_at:
                        continue
                    
                    # Parse the pause timestamp from updated_at
                    try:
                        paused_at = datetime.fromisoformat(instance.updated_at)
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid updated_at for paused instance {instance.instance_id[:8]}..., skipping")
                        continue
                    
                    ttl_seconds = PAUSED_INSTANCE_TTL_MINUTES * 60
                    
                    if (now - paused_at).total_seconds() > ttl_seconds:
                        self.release_paused_instance(instance.instance_id)
                        released_count += 1
                
                if released_count > 0:
                    logger.info(f"Released {released_count} paused instance(s) exceeding {PAUSED_INSTANCE_TTL_MINUTES}min TTL")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Paused instance cleanup failed: {e}")

    def set_job_queue_service(self, service: Any) -> None:
        """Set the JobQueueService reference.
        
        This is called by api.py after both SessionManager and JobQueueService
        are created during application startup. The service is also wired into
        the SourceRegistry so that SchedulerAdapter can route jobs through the
        job queue when project_id is configured.
        
        Args:
            service: The JobQueueService instance to use for lock management.
        """
        self._job_queue_service = service
        # Wire JobQueueService into SourceRegistry for scheduler queue routing (Task 5.4)
        if hasattr(self, 'source_registry') and self.source_registry:
            self.source_registry._job_queue_service = service
            logger.info("JobQueueService wired into SourceRegistry for scheduler routing")
        logger.info("JobQueueService connected to SessionManager")

    def set_job_queue_mgmt_service(self, service: Any) -> None:
        """Set the job queue management service.
        
        Args:
            service: The JobQueueMgmtService instance.
        """
        self._job_queue_mgmt_service = service
        logger.info("JobQueueMgmtService connected to SessionManager")

    def set_dead_letter_service(self, service: Any) -> None:
        """Set the dead letter service.
        
        Args:
            service: The DeadLetterService instance.
        """
        self._dead_letter_service = service
        logger.info("DeadLetterService connected to SessionManager")

    def _on_stale_task_permanent_failure(self, instance_id: str, error: str, message_id: str | None) -> None:
        """Bridge from StaleTaskRecovery thread to InstanceManager._send_error_report.
        
        Called on the recovery thread when a task permanently fails.
        Uses MainLoopBridge to safely invoke the async _send_error_report method.
        
        Args:
            instance_id: The instance ID that had the stale task.
            error: The error message describing the failure.
            message_id: Optional message ID associated with the task.
        """
        from .services.main_loop_bridge import MainLoopBridge
        MainLoopBridge.run_async_no_wait(
            self._send_error_report(
                instance_id=instance_id,
                error=error,
                error_type="stale_task_failure",
                message_id=message_id,
            )
        )

    def setup_worker_pool(
        self,
        num_workers: int = WORKER_POOL_SIZE,
    ) -> None:
        """Set up the worker pool for message processing.
        
        This should be called after initialize() and before start_sources().
        
        Args:
            num_workers: Number of worker threads.
        """
        import os
        
        # Check feature flag from environment
        env_flag = os.environ.get("USE_WORKER_POOL", "").lower()
        if env_flag in ("false", "0", "no"):
            logger.info("Worker pool disabled (USE_WORKER_POOL=false)")
            return
        
        from .services.main_loop_bridge import MainLoopBridge
        from .services.worker_pool import WorkerPool
        from .services.task_processor import TaskProcessor
        from .services.stale_task_recovery import StaleTaskRecovery
        
        # Set the main loop reference for thread-safe async calls
        if self._loop is not None:
            MainLoopBridge.set_loop(self._loop)
        
        # Create repositories (use existing engine)
        from .repositories.task.models import Task
        from .repositories.event.repository import EventRepository
        
        task_repo = TaskRepository(
            engine=self._engine,
            on_pending_task=lambda: self._worker_pool.notify_work() if self._worker_pool else None
        )
        event_repo = EventRepository(engine=self._engine)
        
        # Get shorthand for services config
        svc = self.config.services
        
        # Run startup crash recovery with config values
        stale_recovery = StaleTaskRecovery(
            task_repository=task_repo,
            message_repository=self._queue_repository,
            event_repository=event_repo,
            threshold_minutes=int(svc.task_timeout_minutes),
            check_interval_seconds=svc.stale_task_recovery_interval,
            cancel_grace_seconds=svc.stale_task_cancel_grace_seconds,
            max_retries=svc.max_task_retries,
            retry_backoff_base=svc.task_retry_backoff_base,
            retry_backoff_max=svc.task_retry_backoff_max,
            on_task_permanently_failed=self._on_stale_task_permanent_failure,
        )
        # FIX: C3 — Assign BEFORE calling recover_on_startup() so _stale_recovery is set
        # even if recover_on_startup() raises an exception
        self._stale_recovery = stale_recovery
        stale_recovery.recover_on_startup()
        # FIX: C2 — Start periodic background recovery thread
        stale_recovery.start()
        
        # Create task processor with manager reference
        self._task_processor = TaskProcessor(
            task_repo=task_repo,
            instance_manager=self,
            event_repo=event_repo,
            graph_timeout_minutes=svc.graph_timeout_minutes,
            source_dispatcher=self.source_dispatcher,
        )
        
        # Create and start worker pool with timeout/retry config
        self._worker_pool = WorkerPool(
            task_processor=self._task_processor,
            num_workers=num_workers,
            timeout_minutes=svc.task_timeout_minutes,
            max_retries=svc.max_task_retries,
            retry_backoff_base=svc.task_retry_backoff_base,
            retry_backoff_max=svc.task_retry_backoff_max,
        )
        self._worker_pool.start()
        
        # NEW: Expose pool size for CompletionRegistry invoke semaphore
        self._worker_pool_size = num_workers
        logger.info(f"Worker pool started with {num_workers} workers (timeout={svc.task_timeout_minutes}min)")

    def shutdown_worker_pool(self) -> None:
        """Shut down the worker pool gracefully."""
        if self._worker_pool is not None:
            self._worker_pool.stop()
            self._worker_pool = None
            logger.info("Worker pool stopped")
        
        if self._stale_recovery is not None:
            self._stale_recovery.stop()
            self._stale_recovery = None
            logger.info("Stale task recovery stopped")

    # ── Lifecycle Service Delegations ─────────────────────────────────────────────

    def spawn_instance(
        self, 
        agent_id: str,
        instance_id: str | None = None, 
        parent_id: str | None = None,
        project_id: str | None = None,
        instance_name: str | None = None,
        invoked_as_tool: bool = False,
    ) -> str:
        """Create a new agent instance.

        Args:
            agent_id: Agent ID (e.g., "coder").
            instance_id: Optional instance ID. Auto-generated if not provided or invalid.
            parent_id: Optional parent instance ID for hierarchical instances.
            project_id: Optional project ID for project context. Use `None` to explicitly
                indicate no project context is needed. If provided, stored in instance
                metadata so child instances don't rely on text extraction.
            instance_name: Optional short name for the instance (e.g., 'create-feature-a').
                Used in completion reports to identify the task.
            invoked_as_tool: If True, marks instance as invoked-as-tool (default: False).

        Returns:
            The instance_id of the newly created instance.

        Raises:
            ValueError: If max_instances or max_children_per_instance limit is exceeded,
                or if agent_id is not found.
        """
        return self._lifecycle_service.spawn_instance(
            agent_id=agent_id,
            instance_id=instance_id,
            parent_id=parent_id,
            project_id=project_id,
            instance_name=instance_name,
            invoked_as_tool=invoked_as_tool,
        )

    async def send_message(self, instance_id: str, message: str) -> MessageResult:
        """Send a message to an instance and get the response.

        Args:
            instance_id: The ID of the instance to send the message to.
            message: The message content to send.

        Returns:
            MessageResult with content, thinking, and tool_calls.

        Raises:
            KeyError: If instance_id is not found.
        """
        return await self._messaging_service.send_message(instance_id, message)

    async def enqueue_message(
        self, 
        instance_id: str, 
        message: str, 
        source: str = "api",
        priority: int = 1,
        images: list[str] | None = None,
    ) -> AsyncMessageResult:
        """Enqueue a message using the worker pool (DB-backed) path.
        
        This method creates BOTH a MessageQueue entry AND a Task entry atomically
        in a single transaction. Workers poll the task table and process messages.
        
        Args:
            instance_id: The ID of the target instance.
            message: The message content.
            source: Source identifier (e.g., "api", "web", "telegram:user:123").
            priority: Message priority (0=system, 1=user).
            images: Optional list of base64-encoded images for vision messages.
        
        Returns:
            AsyncMessageResult with message_id and status.
        """
        return await self._messaging_service.enqueue_message(
            instance_id=instance_id,
            message=message,
            source=source,
            priority=priority,
            images=images,
        )

    async def _process_message_with_tracking(
        self, 
        instance_id: str, 
        message: str,
        message_id: str,
        cancellation_token: CancellationToken | None = None,
        is_retry: bool = False,
        retry_count: int = 0,  # FIX: C3 — new parameter
        message_source: str | None = None,  # Source of message (e.g., "internal_agent:xxx", "api", "telegram:xxx")
        images: list[str] | None = None,  # Images for multimodal messages
    ) -> MessageResult:
        """Process message with activity tracking and cancellation support.
        
        On retry, resumes from checkpoint instead of re-sending message
        to prevent duplicate execution.
        
        Args:
            instance_id: The instance ID.
            message: The message content.
            message_id: The queue message ID.
            cancellation_token: Optional token to check for cancellation.
            is_retry: If True, attempt to resume from checkpoint.
            message_source: Source of the message (e.g., "agent:xxx", "api", "telegram:xxx").
                Used to skip project injection for internal agent messages.
            images: Optional list of base64-encoded images for multimodal content.

        Returns:
            MessageResult with response data.
            
        Raises:
            OperationCancelledError: If cancellation is requested.
        """
        return await self._messaging_service._process_message_with_tracking(
            instance_id=instance_id,
            message=message,
            message_id=message_id,
            cancellation_token=cancellation_token,
            is_retry=is_retry,
            retry_count=retry_count,
            message_source=message_source,
            images=images,
        )

    def _get_instance_report_prefix(self, instance_id: str, agent_id: str) -> str:
        """Get formatted prefix for instance completion reports.
        
        Args:
            instance_id: The instance ID.
            agent_id: The agent ID.
        
        Returns:
            Formatted prefix like "Coder agent (id=xxx) has done" or
            "Coder agent (name=create-feature-a, id=xxx) has done"
        """
        return self._child_reports_service._get_instance_report_prefix(instance_id, agent_id)

    async def _summarize_instance(self, instance_id: str, agent_id: str) -> str:
        """Summarize instance messages using LLM.
        
        Args:
            instance_id: The instance ID to summarize.
            agent_id: The agent ID (e.g., "coder", "leader").
            
        Returns:
            Formatted summary string with instance info.
        """
        return await self._child_reports_service._summarize_instance(instance_id, agent_id)

    async def _should_send_completion_report(self, session, instance_id: str, completed_message_id: str) -> bool:
        """Check if completion report should be sent (idempotency checks).
        
        Performs two checks to ensure we do not send duplicate completion reports:
        1. No pending messages (READY, RETRYING) for the instance
        2. No existing completion report for this specific message
        
        The idempotency key includes the message_id so each message completion
        generates a unique report (allowing multiple completions from the same child).
        
        Args:
            session: Database session.
            instance_id: The child instance ID to check.
            completed_message_id: The message ID that just completed.
            
        Returns:
            True if should proceed with sending report, False to skip.
        """
        return await self._child_reports_service._should_send_completion_report(
            session, instance_id, completed_message_id
        )

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
        
        The report source includes the message_id so each completion is unique,
        allowing multiple reports from the same child for different messages.
        
        Args:
            session: Database session.
            instance: The child Instance object.
            last_content: The content to include in the report (fetched before transaction).
            completed_message_id: The message ID that completed (for unique report source).
            
        Returns:
            Tuple of (report_message, report_task, report_message_id).
        """
        return await self._child_reports_service._create_completion_report(
            session, instance, last_content, completed_message_id
        )

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
        return await self._child_reports_service._update_parent_on_child_complete(session, instance)
        
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
        return await self._child_reports_service._create_completion_events(
            session, instance_id, parent_id, report_message_id, waiting_for_remaining
        )

    async def _process_child_completion_and_notify_parent(self, instance_id: str, completed_message_id: str) -> None:
        """Check if child instance is done and send completion report to parent.
        
        CRITICAL FIX C3: Content is fetched BEFORE the transaction to avoid
        leaving the instance in COMPLETED state without a report if the fetch fails.
        
        This method handles:
        - Idempotency per-message (won't send duplicate reports for same message)
        - Parent's waiting_for counter decrement
        - Parent's children[] cache update (FIX: W6)
        - Cascade: if parent's waiting_for reaches 0, transition parent to RUNNING
        
        Args:
            instance_id: The child instance that completed.
            completed_message_id: The message ID that just completed (for idempotency).
        """
        return await self._child_reports_service._process_child_completion_and_notify_parent(
            instance_id, completed_message_id
        )

    async def _send_error_report(
        self, 
        instance_id: str, 
        error: str,
        error_type: str = "execution_error",
        message_id: str | None = None
    ) -> None:
        """Send error report to parent instance when child fails permanently.
        
        Called when a child instance encounters an unrecoverable error:
        - Max retries exceeded
        - Stale task recovery failure
        - Cancellation (shutdown, user request)
        - Circuit breaker opened (via CircuitOpenError)
        - Unhandled exception
        
        This method:
        - Checks for duplicate error reports (idempotency)
        - Fetches metadata outside transaction
        - Performs atomic DB update: child status, message status, parent counter/cache,
          hierarchy deletion, and parent cascade
        - Enqueues error report message to parent
        - Broadcasts child_failed SSE event
        
        Args:
            instance_id: The child instance ID that has failed.
            error: The error message describing what went wrong.
            error_type: Category of error (e.g., "max_retries", "timeout", "circuit_breaker").
            message_id: Optional message ID that triggered the error.
        """
        return await self._error_reporting_service._send_error_report(
            instance_id=instance_id,
            error=error,
            error_type=error_type,
            message_id=message_id,
        )

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
        return await self._child_reports_service._get_last_assistant_message(instance_id, agent_id)

        
    async def _generate_and_broadcast_title(
        self, instance_id: str, message_content: str
    ) -> None:
        """Generate instance title asynchronously and broadcast the update.
        
        This runs as a fire-and-forget task to avoid delaying the 'completed' event.
        Errors are logged but not retried - title generation is best-effort.
        
        Args:
            instance_id: The instance to generate title for
            message_content: The message content to base the title on
        """
        return await self._title_gen_service._generate_and_broadcast_title(
            instance_id, message_content
        )

    def get_queue_stats(self, instance_id: str):
        """Get queue statistics for an instance.
        
        Returns a dict with pending_count, processing_count,
        and oldest_message_age_seconds attributes.
        """
        return self._messaging_service.get_queue_stats(instance_id)

    def _get_system_prompt_tokens(self, instance_id: str) -> int:
        """Get the cached system prompt token count for an instance's agent.

        Args:
            instance_id: The instance ID.

        Returns:
            The number of tokens in the system prompt, or 0 if not found.
        """
        try:
            meta = self._instance_repository.get(instance_id)
            if not meta:
                return 0
            # Get cached token count from prompt cache using agent_id
            cache_key = meta.agent_id
            cached = self.prompt_cache.get(cache_key)
            if cached is not None:
                _, token_count = cached
                return token_count
            return 0
        except Exception:
            return 0

    async def _maybe_compact_context(
        self,
        instance_id: str,
        graph: CompiledStateGraph,
        config: dict[str, Any],
    ) -> None:
        """Conditionally compact instance context if threshold is exceeded.
        
        Compaction is non-blocking - failures are logged but never interrupt processing.
        
        Args:
            instance_id: The instance ID to potentially compact.
            graph: The compiled state graph for the instance.
            config: The LangGraph config dict with configurable thread_id.
        """
        if self._compactor is None:
            return
        
        try:
            # Get current state
            state = await graph.aget_state(config)
            if not state:
                return
            
            messages = state.values.get('messages', [])
            system_prompt_tokens = self._get_system_prompt_tokens(instance_id)
            last_compacted_at = state.values.get('compacted_at')
            
            # Build compaction context
            context = CompactionContext(
                messages=messages,
                system_prompt_tokens=system_prompt_tokens,
                model_name=self.config.llm.model,
                config=self.config.compaction,
                llm_config={
                    "base_url": self.config.llm.base_url,
                    "api_key": self.config.llm.api_key,
                    "model": self.config.llm.model,
                    "model_vision": self.config.llm.model_vision,
                    "temperature": self.config.llm.temperature,
                    "request_timeout": self.config.llm.request_timeout,
                },
                last_compacted_at=last_compacted_at,
            )
            
            # Compact state
            result = await self._compactor.compact_state(context)
            
            if result is None or result.replacement_messages is None:
                return
            
            messages_before = len(messages)
            messages_after = len(result.replacement_messages)
            tokens_before = result.tokens_before
            tokens_saved = result.tokens_saved
            
            # Update graph state with compacted messages
            await graph.aupdate_state(
                config,
                {'messages': result.replacement_messages},
                as_node='agent'
            )
            
            # Update compaction timestamp if available
            if result.compacted_at:
                await graph.aupdate_state(
                    config,
                    {'compacted_at': result.compacted_at},
                    as_node='agent'
                )
            
            # Log compaction result
            log_parts = [
                f"[Compaction] instance={instance_id[:8]}...",
                f"compaction_type={result.compaction_type}",
                f"messages_before={messages_before}",
                f"messages_after={messages_after}",
                f"tokens_before={tokens_before}",
                f"tokens_after={result.tokens_after}",
                f"tokens_saved={tokens_saved}",
            ]
            if result.summarization_error:
                log_parts.append(f"WARNING: summarization_error={result.summarization_error}")
            
            logger.info(" ".join(log_parts))
            
        except Exception as e:
            logger.warning(f"[Compaction] Failed to compact context for {instance_id[:8]}...: {e}")

    async def _has_checkpoint(self, instance_id: str) -> bool:
        """Check if a checkpoint exists for this instance.
        
        Args:
            instance_id: The instance ID to check.
            
        Returns:
            True if checkpoint exists, False otherwise.
        """
        try:
            config = {"configurable": {"thread_id": instance_id}}
            # Get the current state from async checkpointer
            state = await self.checkpointer.aget(config)
            return state is not None
        except Exception:
            return False

    async def _get_message_count(self, instance_id: str) -> int:
        """Get the number of messages in the instance's checkpoint/state.
        
        Args:
            instance_id: The instance ID to check.
            
        Returns:
            Number of messages in the current state.
        """
        try:
            config = {"configurable": {"thread_id": instance_id}}
            state = await self.checkpointer.aget(config)
            if state and state.values:
                messages = state.values.get("messages", [])
                return len(messages) if messages else 0
            return 0
        except Exception:
            return 0

    def cancel(self, message_id: str, reason: CancellationReason) -> bool:
        """Request cancellation of a specific message.
        
        Args:
            message_id: The message ID to cancel.
            reason: The cancellation reason.
        
        Returns:
            True if cancellation was signalled, False if not found.
        """
        return self._cancellation_service.cancel(message_id, reason)

    def cancel_instance_requests(self, instance_id: str, reason: CancellationReason) -> int:
        """Cancel all active requests for an instance. Returns count of cancelled."""
        return self._cancellation_service.cancel_instance_requests(instance_id, reason)

    def cancel_graph_task(self, instance_id: str) -> bool:
        """Cancel the running graph task for an instance.

        This sends asyncio.CancelledError to interrupt the streaming loop.
        Does NOT remove the instance from memory (unlike terminate).

        Args:
            instance_id: The instance whose graph task should be cancelled.

        Returns:
            True if a task was found and cancelled, False otherwise.
        """
        task = self._graph_tasks.get(instance_id)
        if task is None:
            logger.debug(f"No graph task to cancel for instance {instance_id[:8]}...")
            return False

        if task.done():
            logger.debug(f"Graph task already done for instance {instance_id[:8]}...")
            del self._graph_tasks[instance_id]
            return False

        logger.info(f"Cancelling graph task for instance {instance_id[:8]}...")
        task.cancel()
        return True

    async def terminate_instance(self, instance_id: str) -> bool:
        """Terminate an instance.

        This method performs comprehensive cleanup:
        1. Cancels active requests for the instance
        2. Cascades to children - terminates all child instances first
        3. Releases project lock if this instance holds one (via JobQueueService)
        4. Cleans up instance state and resources

        Args:
            instance_id: The ID of the instance to terminate.

        Returns:
            True if termination was successful, False if instance was not found.
        """
        return await self._lifecycle_service.terminate_instance(instance_id)

    async def pause_instance_cascade(self, instance_id: str) -> dict:
        """Pause an instance and cascade to all children (soft pause).

        Recursively pauses the target instance and all its descendants.
        Cancels active requests and sets status to paused (resumable).
        Does NOT remove instances from memory or release locks.

        Args:
            instance_id: The ID of the instance to pause.

        Returns:
            Dict with:
              - paused_ids: list of all instance IDs that were paused
              - skipped_ids: list of instance IDs that were already paused (skipped)
        """
        return await self._lifecycle_service.pause_instance_cascade(instance_id)

    async def _publish_instance_lifecycle_event(
        self,
        instance_id: str,
        status: str,
        error: str | None = None,
        parent_id: str | None = None,
    ) -> None:
        """Publish an instance lifecycle event via the EventBus.
        
        Lifecycle events signal important state transitions: completed, terminated, error.
        This method publishes to EventBus so JobFeedbackObserver (which subscribes via
        subscribe_all) receives the events for job completion feedback.
        
        Args:
            instance_id: The instance ID.
            status: Lifecycle status ("completed", "terminated", "error").
            error: Optional error message for error status.
            parent_id: Optional parent instance ID.
        """
        await self._events_service._publish_instance_lifecycle_event(
            instance_id=instance_id,
            status=status,
            error=error,
            parent_id=parent_id,
        )

    def get_instance(self, instance_id: str) -> CompiledStateGraph:
        """Get an instance graph.

        Uses database as source of truth. If instance exists in DB but not in memory,
        it will be restored (lazy loading).

        Args:
            instance_id: The ID of the instance.

        Returns:
            The CompiledStateGraph instance for the instance.

        Raises:
            KeyError: If instance_id is not found in database.
        """
        return self._lifecycle_service.get_instance(instance_id)

    def _restore_instance(self, instance_id: str, meta: "Instance") -> CompiledStateGraph:
        """Restore an instance from database into memory.

        Rebuilds the graph with the same instance_id. The checkpointer will
        restore conversation state from LangGraph's checkpoint tables.

        Args:
            instance_id: The ID of the instance to restore.
            meta: Instance metadata from database.

        Returns:
            The restored CompiledStateGraph instance.
        """
        return self._lifecycle_service._restore_instance(instance_id, meta)

    def list_instances(
        self, limit: int = 20, offset: int = 0, project_id: str | None = None
    ) -> tuple[list[dict], int]:
        """List instances with pagination.

        Args:
            limit: Maximum number of instances to return (default: 20).
            offset: Number of instances to skip (default: 0).
            project_id: Filter by project ID (optional).

        Returns:
            Tuple of (list of instance info dictionaries, total count).
        """
        return self._lifecycle_service.list_instances(
            limit=limit, offset=offset, project_id=project_id
        )

    def get_instance_info(self, instance_id: str) -> dict:
        """Get information about a specific instance.

        Args:
            instance_id: The ID of the instance.

        Returns:
            Instance metadata dictionary from the database.

        Raises:
            KeyError: If instance is not found.
        """
        return self._lifecycle_service.get_instance_info(instance_id)

    async def get_messages(self, instance_id: str) -> list[dict]:
        """Get message history for an instance.

        Args:
            instance_id: The ID of the instance.

        Returns:
            List of message dictionaries from LangGraph checkpoints.

        Raises:
            KeyError: If instance is not found.
        """
        return await self._messaging_service.get_messages(instance_id)

    def clear_all_instances(self) -> int:
        """Clear all instances from memory and database.

        Returns:
            Number of instances deleted from database.
        """
        return self._lifecycle_service.clear_all_instances()
    
    async def start_sources(self) -> None:
        """Start the pluggable message sources system.
        
        This initializes:
        - SourceRegistry: Loads and starts all enabled adapters from DB
        - ResponseDispatcher: Listens for completed events to route responses
        - SourceCleanup: Periodic cleanup of old processed messages and mappings
        """
        # Start cleanup job
        self._source_cleanup = SourceCleanup(self._source_repository)
        self._source_cleanup.start()
        
        # Start the dispatcher (listens for completed events)
        await self.source_dispatcher.start()
        
        # Start all enabled adapters from database
        await self.source_registry.start_all()
        
        logger.info("Message sources system started")
    
    async def stop_sources(self, timeout: float = 30.0) -> None:
        """Stop the pluggable message sources system gracefully.
        
        Args:
            timeout: Maximum seconds to wait for pending responses.
        """
        # Stop dispatcher first (drain pending responses)
        await self.source_dispatcher.stop(timeout=timeout)
        
        # Stop all adapters
        await self.source_registry.stop_all()
        
        # Stop cleanup job
        if self._source_cleanup:
            await self._source_cleanup.stop()
        
        logger.info("Message sources system stopped")
    
    def get_source_registry(self) -> SourceRegistry:
        """Get the source registry for adapter management."""
        return self.source_registry

    def find_near_instance(self, instance_id: str, max_distance: int = DEFAULT_FUZZY_MATCH_DISTANCE) -> list[str]:
        """Find all near-matching instance IDs from recent instances.

        Searches through the most recent 50 instances using edit distance to find all close matches.
        Matching is case-insensitive. Results are sorted by edit distance (closest first).

        Args:
            instance_id: The instance ID to find a near match for.
            max_distance: Maximum edit distance threshold (default: DEFAULT_FUZZY_MATCH_DISTANCE).

        Returns:
            List of all near-matching instance_ids sorted by edit distance (closest first),
            or empty list if no matches found.
        """
        # Get recent instances from repository (ordered by recency)
        instances, _ = self._instance_repository.list(limit=50, offset=0)

        return find_near_instance(instance_id, instances, max_distance)

    def cleanup(self) -> None:
        """Cleanup resources including database connections.
        
        Note: Assumes shutdown() has already been called and stopped the watchdog.
        """
        # Dispose the shared engine to close all connections in the pool
        if hasattr(self, '_engine') and self._engine:
            self._engine.dispose()
            logger.info("Database engine disposed")
    
    async def shutdown(self, grace_period: float = 10.0) -> None:
        """Gracefully shutdown all manager components in order.
        
        This implements an ordered shutdown sequence:
        1. Set _shutting_down flag to reject new messages
        2. Stop accepting new messages via source registry
        3. Cancel active LLM streams via request registry
        4. Wait for in-flight processing to finish (grace period)
        5. Shutdown worker pool
        6. Clean up resources
        
        Each step is wrapped in its own try/except so failures don't skip subsequent steps.
        
        Args:
            grace_period: Maximum seconds to wait for in-flight processing (default: 10s).
        """
        if self.is_shutting_down:
            logger.debug("Shutdown already in progress, skipping")
            return
        
        self._shutting_down = True
        logger.info("Starting graceful shutdown...")
        
        steps = [
            ("stop_sources", self.stop_sources(timeout=grace_period)),
            ("cancel_active_requests", self._cancel_all_active_requests()),
            ("wait_inflight", self._wait_for_inflight(grace_period)),
            ("shutdown_worker_pool", asyncio.to_thread(self.shutdown_worker_pool)),
            ("shutdown_event_bus", self._event_bus.shutdown()),
        ]
        
        for name, step_coro in steps:
            try:
                await step_coro
            except Exception as e:
                logger.error(f"Error during shutdown step '{name}': {e}", exc_info=True)
        
        # Clean up resources (existing cleanup method) - also resilient
        try:
            logger.info("Step 6/6: Cleaning up resources...")
            self.cleanup()
        except Exception as e:
            logger.error(f"Error during shutdown step 'cleanup': {e}", exc_info=True)
        
        logger.info("Graceful shutdown complete")
    
    async def _cancel_all_active_requests(self) -> None:
        """Cancel all active requests in the registry with SHUTDOWN reason."""
        return await self._cancellation_service._cancel_all_active_requests()
    
    async def _wait_for_inflight(self, grace_period: float) -> None:
        """Wait for in-flight processing to finish.
        
        Args:
            grace_period: Maximum seconds to wait.
        """
        return await self._cancellation_service._wait_for_inflight(grace_period)

    def get_active_requests(self, instance_id: str) -> list[str]:
        """Get list of active request message IDs for an instance.
        
        Args:
            instance_id: The instance ID to check.
        
        Returns:
            List of message IDs that are currently being processed.
        """
        return self._cancellation_service.get_active_requests(instance_id)
    
    @property
    def is_shutting_down(self) -> bool:
        """Check if shutdown is in progress."""
        return self._cancellation_service.is_shutting_down
