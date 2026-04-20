"""Instance manager orchestrating all agent instances."""

import uuid
import logging
import asyncio
import re
import time
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

from langgraph.graph.state import CompiledStateGraph
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from .config import Config
from .graph import build_instance_graph
from .loader import PromptCache, load_and_cache_prompt
from .utils import parse_think_tags, serialize_message
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
from .cancellation import (
    CancellationToken, 
    CancellationReason,
    OperationCancelledError
)
from .request_registry import ActiveRequestRegistry
from .compaction import ContextCompactor, CompactionContext

# Worker pool imports (lazy import to avoid circular dependency)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .services.worker_pool import WorkerPool
    from .services.task_processor import TaskProcessor
    from .services.stale_task_recovery import StaleTaskRecovery



logger = logging.getLogger(__name__)

# Error report severity classification
CRITICAL_ERROR_TYPES = frozenset({"max_retries_exceeded", "circuit_breaker_open"})
RECOVERABLE_ERROR_TYPES = frozenset({"watchdog_timeout", "circuit_breaker_open"})

# UUID validation pattern (compiled once at module level)
_UUID_PATTERN = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', re.IGNORECASE)


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
        logger.info(f"SessionManager initialized with async checkpointer at {self._checkpointer_db_path}")

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
        num_workers: int = 4,
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

    def spawn_instance(
        self, 
        agent_id: str,
        instance_id: str | None = None, 
        parent_id: str | None = None,
        project_id: str | None = None,
        instance_name: str | None = None,
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

        Returns:
            The instance_id of the newly created instance.

        Raises:
            ValueError: If max_instances or max_children_per_instance limit is exceeded,
                or if agent_id is not found.
        """
        # Normalize project_id: accept "null" string (from LLM JSON) as None
        if project_id is not None and str(project_id).lower() in ("null", "none", ""):
            project_id = None

        # Resolve agent
        registry = get_registry()
        resolved_agent_id = registry.resolve_to_id(agent_id) or agent_id
        metadata = registry.get(resolved_agent_id)
        if metadata is None:
            raise ValueError(f"Agent not found: {resolved_agent_id}")
        resolved_agent_dir = str(metadata.path)
        
        # Validate instance_id format or auto-generate
        if instance_id is None or not _UUID_PATTERN.match(instance_id):
            if instance_id is not None:
                logger.warning(
                    f"Invalid instance_id format '{instance_id}', auto-generating UUID. "
                    "Instance IDs must be valid UUIDs like '550e8400-e29b-41d4-a716-446655440000'"
                )
            instance_id = str(uuid.uuid4())

        # Check max_instances limit
        current_instance_count = len(self.instances)
        if current_instance_count >= self.config.limits.max_instances:
            raise ValueError(
                f"Max instances limit reached: {self.config.limits.max_instances}"
            )

        # Check max_children_per_instance limit if parent_id is provided
        if parent_id is not None:
            parent_meta = self._instance_repository.get(parent_id)
            if parent_meta and parent_meta.children:
                child_count = len(parent_meta.children)
                if child_count >= self.config.limits.max_children_per_instance:
                    raise ValueError(
                        f"Max children per instance limit reached: "
                        f"{self.config.limits.max_children_per_instance}"
                    )

        # Load and cache prompt using resolved path
        agent_path = Path(resolved_agent_dir)
        system_prompt, token_count = load_and_cache_prompt(resolved_agent_id, agent_path, self.prompt_cache)

        # Create tools with this manager reference
        tools = create_instance_tools(self, instance_id, resolved_agent_id)

        # Build LLM config
        llm_config = {
            "base_url": self.config.llm.base_url,
            "api_key": self.config.llm.api_key,
            "model": self.config.llm.model,
            "model_vision": self.config.llm.model_vision,
            "temperature": self.config.llm.temperature,
            "request_timeout": self.config.llm.request_timeout,
        }

        # Build retry config from queue settings
        retry_config = {
            "transient_attempts": self.config.queue.llm_retry_transient_attempts,
            "timeout_attempts": self.config.queue.llm_retry_timeout_attempts,
        }

        # Build graph config with thread_id for state management
        config = {
            "configurable": {"thread_id": instance_id},
            "recursion_limit": self.config.limits.graph_recursion_limit,
        }

        # Build graph with checkpointer
        graph = build_instance_graph(
            tools=tools,
            checkpointer=self.checkpointer,
            llm_config=llm_config,
            system_prompt=system_prompt,
            retry_config=retry_config,
            compactor=self._compactor,
            graph_config=config,
        )

        # Save metadata to DB using instance repository
        # Include project_id in metadata so child instances don't rely on text extraction
        instance_metadata = {}
        if project_id is not None:
            # Validate project exists before storing (P1)
            project = self._project_repository.get(project_id)
            if project is None:
                raise ValueError(
                    f"Project '{project_id}' not found. "
                    f"Use None if no project context is needed."
                )
            instance_metadata["project_id"] = project_id
        
        # Store instance_name in metadata if provided
        if instance_name is not None:
            instance_metadata["instance_name"] = instance_name
        
        logger.info(f"Spawning instance {instance_id} (agent={resolved_agent_id}, parent={parent_id}, name={instance_name})")
        
        # Create instance in DB
        self._instance_repository.create(
            instance_id=instance_id,
            agent_id=resolved_agent_id,
            agent_dir=resolved_agent_dir,
            parent_id=parent_id,
            metadata=instance_metadata if instance_metadata else None,
        )
        
        # Verify instance was created in DB
        created = self._instance_repository.get(instance_id)
        if created is None:
            logger.error(f"CRITICAL: Instance {instance_id} was NOT persisted to database after create() call!")
        else:
            logger.info(f"Instance {instance_id} created in DB with status={created.status}, parent_id={created.parent_id}")
        
        # Inherit original_source from parent if parent has one (C2: source inheritance during spawn)
        # This ensures grandchildren also get the original telegram source
        if parent_id:
            parent_meta = self._instance_repository.get(parent_id)
            if parent_meta is not None and parent_meta.instance_metadata is not None:
                parent_original_source = parent_meta.instance_metadata.get("original_source")
                if parent_original_source:
                    self._instance_repository.set_metadata(instance_id, "original_source", parent_original_source)
                    logger.info(f"Inherited original_source '{parent_original_source}' from parent {parent_id[:8]}...")
        
        # Update parent's children list and waiting_for counter
        if parent_id:
            with Session(self._engine) as session:
                parent = session.get(Instance, parent_id)
                if parent:
                    # Add child to parent's denormalized children list
                    children_list = json.loads(parent.children) if parent.children else []
                    if instance_id not in children_list:
                        children_list.append(instance_id)
                        parent.children = json.dumps(children_list)
                        logger.info(f"Added child {instance_id} to parent's children list")
                    parent.waiting_for += 1
                    # Update parent status to WAITING_CHILDREN if it was IDLE
                    if parent.status == InstanceStatus.IDLE.value:
                        parent.status = InstanceStatus.WAITING_CHILDREN.value
                        logger.info(f"Parent {parent_id} status changed to WAITING_CHILDREN")
                    session.commit()
                    logger.info(f"Parent {parent_id} updated: children={children_list}, waiting_for={parent.waiting_for}")
                else:
                    logger.warning(f"Parent {parent_id} not found in DB when updating children list for child {instance_id}")
        
        # Store in instances dict
        self.instances[instance_id] = (graph, resolved_agent_dir)

        return instance_id

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
        # Get instance graph (will lazy-load from DB if needed)
        graph = self.get_instance(instance_id)

        # Invoke with message
        config = {
            "configurable": {"thread_id": instance_id},
            "recursion_limit": self.config.limits.graph_recursion_limit,
        }
        
        # Compact context before processing (non-blocking)
        await self._maybe_compact_context(instance_id, graph, config)
        
        result = await graph.ainvoke({"messages": [message]}, config)

        # Extract message data from the current turn
        messages = result.get("messages", [])
        
        if messages:
            # Find where the current turn starts (last HumanMessage from this invoke)
            # We only want to process messages from the current turn, not history
            current_turn_start = 0
            for i, msg in enumerate(messages):
                # HumanMessage is the user's input
                if hasattr(msg, 'type') and msg.type == 'human':
                    current_turn_start = i
            
            # Get messages from current turn only
            current_turn_messages = messages[current_turn_start:]
            
            # Build map of tool_call_id -> output from ToolMessages in current turn
            tool_outputs = {}
            for msg in current_turn_messages:
                if hasattr(msg, 'tool_call_id'):  # It's a ToolMessage
                    tool_outputs[msg.tool_call_id] = msg.content
            
            # Collect all tool_calls from AIMessages in current turn
            all_tool_calls = []
            for msg in current_turn_messages:
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    for tc in msg.tool_calls:
                        # Handle both dict and object formats
                        tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                        output = tool_outputs.get(tc_id)
                        
                        if isinstance(tc, dict):
                            all_tool_calls.append({
                                "id": tc.get("id", ""),
                                "name": tc.get("name", ""),
                                "arguments": tc.get("args", {}),
                                "output": output,
                            })
                        else:
                            all_tool_calls.append({
                                "id": getattr(tc, "id", ""),
                                "name": getattr(tc, "name", ""),
                                "arguments": getattr(tc, "args", {}),
                                "output": output,
                            })
            
            tool_calls = all_tool_calls if all_tool_calls else None
            
            # Find the last AIMessage (the current assistant response) for content and thinking
            last_ai_message = None
            for msg in reversed(messages):
                if hasattr(msg, 'type') and msg.type == 'ai':
                    last_ai_message = msg
                    break
            
            if last_ai_message:
                content = last_ai_message.content or ""
                
                # Extract thinking ONLY from the last AIMessage (for models that support extended thinking)
                thinking = None
                
                # Check direct thinking attribute (some providers)
                if hasattr(last_ai_message, 'thinking') and last_ai_message.thinking:
                    thinking = last_ai_message.thinking
                
                # Check additional_kwargs (most common for OpenAI-compatible proxies like LiteLLM)
                elif hasattr(last_ai_message, 'additional_kwargs'):
                    kwargs = last_ai_message.additional_kwargs or {}
                    if kwargs.get("thinking"):
                        thinking = kwargs["thinking"]
                    elif kwargs.get("reasoning_content"):
                        thinking = kwargs["reasoning_content"]
                
                # Check response_metadata (fallback)
                elif hasattr(last_ai_message, 'response_metadata'):
                    metadata = last_ai_message.response_metadata or {}
                    if metadata.get("thinking"):
                        thinking = metadata["thinking"]
                    elif metadata.get("reasoning_content"):
                        thinking = metadata["reasoning_content"]
                
                # Parse <think/> tags from content
                content, thinking_extracted = parse_think_tags(content)
                
                return MessageResult(
                    content=content,
                    thinking=thinking,
                    thinking_extracted=thinking_extracted,
                    tool_calls=tool_calls,
                )
        return MessageResult(content="")

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
        # Reject new messages during shutdown
        if self.is_shutting_down:
            raise RuntimeError("Manager is shutting down, cannot accept new messages")
        
        import uuid
        from datetime import datetime, timezone
        from sqlmodel import Session
        from .repositories.task.models import Task, TaskType, TaskStatus
        from .repositories.message_queue.models import MessageQueue, MessageType, MessageStatus
        from .repositories.event.models import Event, EventKind
        
        # Determine message type based on source
        if source.startswith("internal_report:"):
            msg_type = MessageType.COMPLETION_REPORT.value
            # System-generated reports use random IDs (not user messages)
            message_id = str(uuid.uuid4())
        elif source.startswith("internal_error_report:"):
            msg_type = MessageType.ERROR_REPORT.value
            # System-generated errors use random IDs (not user messages)
            message_id = str(uuid.uuid4())
        elif source.startswith("internal_agent:"):
            msg_type = MessageType.AGENT.value
            # Agent-to-agent messages use random IDs
            message_id = str(uuid.uuid4())
        else:
            msg_type = MessageType.HUMAN.value
            # User messages use UUID IDs
            message_id = str(uuid.uuid4())
        
        # Log image count if images are provided
        if images:
            logger.info(f"Processing message with {len(images)} image(s)")
        
        with Session(self._engine) as session:
            # 1. Insert the message
            db_message = MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content=message,
                source=source,
                type=msg_type,
                status=MessageStatus.READY.value,
                priority=priority,
                images=images,
                enqueued_at=datetime.now(timezone.utc),
            )
            session.add(db_message)
            
            # 2. Create a task for the worker pool to pick up
            task = Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=instance_id,
                message_id=message_id,
                status=TaskStatus.PENDING.value,
                created_at=datetime.now(timezone.utc),
            )
            session.add(task)
            
            # 3. Update instance status if IDLE (don't override WAITING_CHILDREN, etc.)
            instance = session.get(Instance, instance_id)
            if instance:
                if instance.status == InstanceStatus.IDLE.value:
                    instance.status = InstanceStatus.RUNNING.value
                instance.last_activity_at = datetime.now(timezone.utc)
                instance.version = (instance.version or 1) + 1
            else:
                logger.warning(
                    f"Instance {instance_id} not found in database during enqueue_message. "
                    f"This may indicate the instance was not properly persisted."
                )
            
            # 4. Create event for the new message
            role = "system" if msg_type == MessageType.SYSTEM.value else "user"
            message_data = {
                "message_id": message_id,
                "role": role,
                "content": message,
                "source": source,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            event = Event(
                instance_id=instance_id,
                message_id=message_id,
                kind=EventKind.MESSAGE_RECEIVED.value,
                data=json.dumps(message_data),
                created_at=datetime.now(timezone.utc),
            )
            session.add(event)
            
            session.commit()
        
        # After commit — task is now visible in DB
        if self._worker_pool is not None:
            self._worker_pool.notify_work()
        
        logger.debug(f"Enqueued message {message_id} for instance {instance_id}")
        
        return AsyncMessageResult(
            message_id=message_id,
            instance_id=instance_id,
            status="queued"
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
        graph = self.get_instance(instance_id)
        
        # Create activity callback for this message - use repository for activity updates
        activity_callback = ActivityCallbackHandler(
            self._queue_repository, 
            message_id,
            update_interval_seconds=5.0
        )
        
        # Build callbacks list
        callbacks: list[BaseCallbackHandler] = [activity_callback]
        
        # Add cancellation callback if token provided
        if cancellation_token:
            # Check cancellation before starting
            cancellation_token.check()
            cancellation_callback = CancellationCallbackHandler(
                cancellation_token=cancellation_token
            )
            callbacks.append(cancellation_callback)
        
        config = {
            "configurable": {"thread_id": instance_id},
            "callbacks": callbacks,
            "recursion_limit": self.config.limits.graph_recursion_limit,
        }
        
        # Variables for checkpoint-based streaming
        final_content = ""
        last_ai_message = None
        
        # Determine the effective source for progressive dispatch
        # When processing an internal_report:* or internal_error_report:* message, we need to use the original
        # external source (e.g., telegram:123) instead of the internal report source
        # Note: internal_agent:* is agent-to-agent communication, NOT a completion report
        dispatch_source: str | None = None
        if message_source:
            is_internal_report = (
                message_source.startswith("internal_report:") or
                message_source.startswith("internal_error_report:")
            )
            if is_internal_report:
                # This is an internal message (completion report, error report, etc.)
                # Retrieve the original external source from instance metadata
                instance_meta = self._instance_repository.get(instance_id)
                # Use is not None check because empty dict {} is falsy
                if instance_meta is not None and instance_meta.instance_metadata is not None:
                    dispatch_source = instance_meta.instance_metadata.get("original_source")
                if not dispatch_source:
                    logger.warning(
                        f"No original_source found for instance {instance_id[:8]}... "
                        f"(message_source={message_source})"
                    )
            else:
                # This is an external message - store as original source for future internal reports
                dispatch_source = message_source
                # Store in instance metadata for later retrieval when child completes
                # Write-once guard: only set if not already set
                instance_meta = self._instance_repository.get(instance_id)
                if instance_meta is not None and instance_meta.instance_metadata is not None:
                    current = instance_meta.instance_metadata.get("original_source")
                    if not current:
                        self._instance_repository.set_metadata(instance_id, "original_source", message_source)
                else:
                    # Instance metadata doesn't exist yet, set it directly
                    self._instance_repository.set_metadata(instance_id, "original_source", message_source)
        
        
        # Project context injection for first message only
        # Must happen BEFORE building graph_input
        # Skip injection if:
        # 1. This is a retry (already processed)
        # 2. This is a completion/error report (parent already has context)
        # 3. Project already injected (checked via metadata flag)
        if not is_retry:
            # Determine if this is a completion report or error report
            # These should skip injection because parent already has project context
            is_completion_report = (
                message_source is not None and (
                    message_source.startswith("internal_report:") or
                    message_source.startswith("internal_error_report:")
                )
            )
            
            if is_completion_report:
                # Skip project injection for completion/error reports
                pass
            else:
                # Check if project was already injected (using metadata flag)
                instance_meta = self._instance_repository.get(instance_id)
                project_already_injected = (
                    instance_meta and 
                    instance_meta.instance_metadata and 
                    instance_meta.instance_metadata.get("project_injected")
                )
                
                if project_already_injected:
                    # Already injected, skip
                    pass
                else:
                    # First injection → attempt project injection
                    existing_project_id = None
                    if instance_meta and instance_meta.instance_metadata:
                        existing_project_id = instance_meta.instance_metadata.get("project_id")
                    
                    injection_succeeded = False
                    
                    if existing_project_id:
                        # project_id exists (inherited from parent) → inject context using stored project_id
                        matched_project = self._project_repository.get(existing_project_id)
                        if matched_project:
                            project_context = format_project_context(matched_project)
                            message = project_context + message
                            injection_succeeded = True
                            logger.info(f"Project context injection: using stored project_id '{existing_project_id}' for instance {instance_id[:8]}...")
                    else:
                        # No project_id yet → extract keywords and try to match
                        keywords = extract_project_keywords(message)
                        
                        if keywords:
                            matched_project = self._project_repository.match_by_keywords(keywords)
                            
                            if matched_project:
                                # Log the match
                                logger.info(
                                    f"Project context injection: matched '{matched_project.name}' "
                                    f"from keywords: {keywords[:5]}..."
                                )
                                
                                # Prepend project context to message
                                project_context = format_project_context(matched_project)
                                message = project_context + message
                                injection_succeeded = True
                                
                                # Update instance metadata with project_id
                                self._instance_repository.set_metadata(instance_id, "project_id", matched_project.project_id)
                                
                                logger.debug(f"Injected project context for instance {instance_id[:8]}...")
                    
                    # Mark as injected to prevent re-injection on subsequent messages
                    if injection_succeeded:
                        self._instance_repository.set_metadata(instance_id, "project_injected", True)
        
        # Build input - on retry with checkpoint, resume from None
        if not is_retry:
            await self._maybe_compact_context(instance_id, graph, config)
        
        # Import here to avoid circular imports with langchain_core
        from langchain_core.messages import HumanMessage
        
        if is_retry:
            if await self._has_checkpoint(instance_id):
                logger.info(f"Resuming instance {instance_id[:8]}... from checkpoint (retry #{retry_count})")
                graph_input = None  # LangGraph will resume from checkpoint
            else:
                logger.warning(f"Retry for instance {instance_id[:8]}... but no checkpoint found, re-adding message")
                content = _build_message_content(message, images)
                graph_input = {"messages": [HumanMessage(content=content, id=message_id)]}
        else:
            # First attempt - add message to conversation
            content = _build_message_content(message, images)
            graph_input = {"messages": [HumanMessage(content=content, id=message_id)]}
        
        # Build user message for pre-emit - use multimodal content if images present
        user_msg = HumanMessage(content=_build_message_content(message, images), id=message_id)
        
        user_serialized = serialize_message(user_msg)
        user_serialized["instance_id"] = instance_id
        await self._live_hub.stream_message(
            instance_id=instance_id,
            message=user_serialized,
            event_type="user_message",
            checkpoint_id="user",
        )

        # Reset state for this processing call to prevent unbounded growth
        all_state_messages: list = []
        tool_outputs: dict = {}
        event_index = 0  # Sequence counter for checkpoint_id
        _dispatched_msg_ids: set[str] = set()  # Track dispatched message IDs for dedup

        # Stream through graph execution
        try:
            async with self._llm_semaphore:
                async for event in graph.astream(graph_input, config, stream_mode=["updates"]):
                    # Unpack tuple: (mode, data)
                    if isinstance(event, tuple):
                        mode, data = event
                    else:
                        mode = "updates"
                        data = event
                    
                    if mode == "updates":
                        # Progressive delivery: dispatch AI messages from "agent" node immediately
                        if dispatch_source and self.source_dispatcher:
                            for node_name, node_data in data.items():
                                if node_name == "agent":
                                    node_messages = node_data.get("messages", [])
                                    for msg in node_messages:
                                        # Check if it's an AI message
                                        if not (hasattr(msg, 'type') and msg.type == 'ai'):
                                            continue

                                        # W3: Deduplicate by message ID
                                        msg_id = getattr(msg, 'id', None)
                                        if msg_id and msg_id in _dispatched_msg_ids:
                                            continue
                                        if msg_id:
                                            _dispatched_msg_ids.add(msg_id)

                                        # W2: Handle list content (e.g., [{"type": "text", "text": "..."}])
                                        content = getattr(msg, 'content', None)
                                        if isinstance(content, list):
                                            text_parts = [
                                                b.get("text", "")
                                                for b in content
                                                if isinstance(b, dict) and b.get("text")
                                            ]
                                            content = " ".join(text_parts)

                                        if content and content.strip():
                                            try:
                                                await self.source_dispatcher.dispatch_message(
                                                    source=dispatch_source,
                                                    content=content
                                                )
                                            except Exception as e:
                                                logger.warning(
                                                    f"Progressive dispatch failed for message {message_id[:8]}...: {e}"
                                                )
                        
                        # Accumulate messages from ALL nodes
                        any_new = False
                        for node_name, node_data in data.items():
                            node_messages = node_data.get("messages", [])
                            if node_messages:
                                any_new = True
                                # Key by msg.id to handle modifications
                                msg_index = {m.id: i for i, m in enumerate(all_state_messages) if hasattr(m, 'id')}
                                for m in node_messages:
                                    if hasattr(m, 'id') and m.id in msg_index:
                                        all_state_messages[msg_index[m.id]] = m  # Replace existing
                                    else:
                                        all_state_messages.append(m)
                        
                        if not any_new:
                            continue
                        
                        # Build tool_outputs from ALL messages (including ToolMessages)
                        tool_outputs = {}
                        for m in all_state_messages:
                            if hasattr(m, 'tool_call_id'):
                                tc_id = getattr(m, 'tool_call_id', '')
                                if tc_id:
                                    content = getattr(m, 'content', '') or ''
                                    tool_outputs[tc_id] = str(content) if not isinstance(content, str) else content
                        
                        # Build sequence ID for checkpoint_id
                        sequence_id = f"seq_{event_index}"
                        event_index += 1
                        
                        # Import ToolMessage here to avoid circular imports
                        from langchain_core.messages import ToolMessage
                        
                        # Emit individual messages, preserving original created_at
                        for m in all_state_messages:
                            # Skip ToolMessages — they get baked into tool_calls
                            if isinstance(m, ToolMessage):
                                continue
                            # Skip HumanMessages — already emitted before graph started
                            if hasattr(m, 'type') and m.type == 'human':
                                continue
                            
                            msg_id = getattr(m, 'id', None)
                            msg_serialized = serialize_message(m, tool_outputs)
                            msg_serialized["instance_id"] = instance_id
                            
                            # Preserve original created_at from first emission
                            ts_key = f"{instance_id}:{msg_id}" if msg_id else None
                            if ts_key and ts_key in self._original_timestamps:
                                msg_serialized["created_at"] = self._original_timestamps[ts_key]
                            elif ts_key:
                                self._original_timestamps[ts_key] = msg_serialized["created_at"]
                            
                            # Store content hash for deduplication (skip if content unchanged)
                            if ts_key:
                                content_hash = _compute_message_content_hash(msg_serialized)
                                self._emitted_message_content[ts_key] = content_hash
                            
                            # Emit individually
                            event_type = _get_message_event_type(msg_serialized)
                            await self._live_hub.stream_message(
                                instance_id=instance_id,
                                message=msg_serialized,
                                event_type=event_type,
                                checkpoint_id=sequence_id,
                            )
                        
                        # Track final content and last AI message from streaming
                        for msg in reversed(all_state_messages):
                            if hasattr(msg, 'type') and msg.type == 'ai':
                                if hasattr(msg, 'content'):
                                    final_content = msg.content or ""
                                last_ai_message = msg
                                break
        except Exception as e:
            logger.error(f"Streaming failed for message {message_id}: {e}")
            await self._live_hub.stream_error(
                instance_id=instance_id,
                error={"error": str(e), "stage": "streaming", "message_id": message_id},
            )
            raise

        # Parse <think/> tags from final content
        content, thinking_extracted = parse_think_tags(final_content)
        
        # Extract thinking from last AI message
        thinking = None
        if last_ai_message:
            if hasattr(last_ai_message, 'thinking') and last_ai_message.thinking:
                thinking = last_ai_message.thinking
            elif hasattr(last_ai_message, 'additional_kwargs'):
                kwargs = last_ai_message.additional_kwargs or {}
                thinking = kwargs.get("reasoning_content") or kwargs.get("thinking")
        
        # Build tool_calls from final state
        tool_calls = None
        if last_ai_message and hasattr(last_ai_message, 'tool_calls') and last_ai_message.tool_calls:
            tool_calls = []
            outputs_map = tool_outputs
            for tc in last_ai_message.tool_calls:
                tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                tc_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                tool_calls.append({
                    "id": tc_id,
                    "name": tc_name,
                    "arguments": tc_args,
                    "output": outputs_map.get(tc_id),
                })
        
        return MessageResult(
            content=content,
            thinking=thinking,
            thinking_extracted=thinking_extracted,
            tool_calls=tool_calls,
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
        from langchain_openai import ChatOpenAI
        
        # Get the report prefix
        prefix = self._get_instance_report_prefix(instance_id, agent_id)
        
        # Get instance messages
        messages = await get_instance_messages(self.checkpointer, instance_id)
        
        if not messages:
            return f"{prefix}, bellow is the response: No activity recorded."
        
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
            return f"{prefix}, bellow is the response: No messages to summarize."
        
        conversation = "\n".join(conversation_text)
        
        # Create LLM client for summarization using the same config pattern
        # Filter model_vision from config to avoid noisy LangChain warnings
        llm_config = {
            "base_url": self.config.llm.base_url,
            "api_key": self.config.llm.api_key,
            "model": self.config.llm.model,
            "temperature": 0.3,  # Lower temperature for more focused summaries
            "default_headers": {"x-proxy-app": "ensemble"},
        }
        # Remove model_vision if present (summarization doesn't need vision)
        llm_config = {k: v for k, v in llm_config.items() if k != "model_vision"}
        
        # Import here to use the same pattern as graph.py
        from .graph import ThinkingChatOpenAI
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
            return f"{prefix}, bellow is the response: {summary}"
        except Exception as e:
            logger.warning(f"Failed to summarize instance {instance_id}: {e}")
            # Fallback: count messages and provide basic summary
            return f"{prefix}, bellow is the response: Completed {len(messages)} message(s)."

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
        from sqlmodel import select
        from sqlalchemy import func
        from .repositories.message_queue.models import MessageQueue, MessageStatus
        
        # Check for pending/processing messages for this instance
        # Note: Don't check PROCESSING status for the current message being checked
        # (it's the message that just finished processing)
        pending_count = session.exec(
            select(func.count())
            .select_from(MessageQueue)
            .where(MessageQueue.instance_id == instance_id)
            .where(MessageQueue.status.in_([
                MessageStatus.READY.value,
                # MessageStatus.PROCESSING.value,  # Excluded - we're checking this message
                MessageStatus.RETRYING.value,
            ]))
        ).one()
        
        if pending_count > 0:
            logger.debug(
                f"Instance {instance_id[:8]}... has {pending_count} pending messages, "
                f"skipping completion check"
            )
            return False
        
        # Idempotency: Check if completion report already sent for THIS message
        instance = session.get(Instance, instance_id)
        if instance is None or instance.parent_id is None:
            return False
            
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
            return False
        
        return True

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
        from datetime import datetime, timezone
        from .repositories.message_queue.models import MessageQueue, MessageStatus, MessageType
        from .repositories.task.models import Task, TaskType, TaskStatus
        
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
        from datetime import datetime, timezone
        from sqlalchemy import func, select, text
        from .repositories.message_queue.models import MessageQueue, MessageStatus
        
        parent = session.get(Instance, instance.parent_id)
        if not parent:
            return False, None, None
        
        # Decrement parent's waiting_for counter
        parent.waiting_for = max(0, (parent.waiting_for or 0) - 1)
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
            ).one()
            
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
                parent.status = InstanceStatus.WAITING_CHILDREN.value
                logger.info(
                    f"Parent {parent.instance_id[:8]}... all children done but has {parent_pending} "
                    f"pending messages, status=WAITING_CHILDREN"
                )
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
        from datetime import datetime, timezone
        from .repositories.event.models import Event, EventKind
        
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
        # FIX C3: Fetch content BEFORE transaction — avoid orphaned COMPLETED state
        # Get instance's agent_id for the report
        instance_meta = self._instance_repository.get(instance_id)
        agent_id = instance_meta.agent_id if instance_meta else "agent"
        last_content = await self._get_last_assistant_message(instance_id, agent_id)
        if last_content is None:
            logger.warning(f"No content found for instance {instance_id[:8]}..., skipping completion check")
            return
        
        with Session(self._engine) as session:
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
                    return
                else:
                    # No children - safe to complete immediately
                    logger.debug(f"Instance {instance_id[:8]}... completed (no parent, no children)")
                    await self._publish_instance_lifecycle_event(
                        instance_id=instance_id,
                        status="completed",
                        error=None,
                        parent_id=None,
                    )
                    return
            
            # Idempotency checks
            if not await self._should_send_completion_report(session, instance_id, completed_message_id):
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
            
            # Capture parent_id before session closes (instance will be detached)
            parent_id = instance.parent_id
            
            session.commit()
        
        # Broadcast child completion event asynchronously (using captured parent_id)
        try:
            await self._live_hub.stream_lifecycle(
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
            try:
                await self._publish_instance_lifecycle_event(
                    instance_id=completed_parent_id,
                    status="completed",
                    error=None,
                    parent_id=completed_parent_parent_id,
                )
            except Exception as e:
                logger.warning(f"Failed to publish lifecycle event for completed parent {completed_parent_id[:8]}...: {e}")

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
        from datetime import datetime, timezone
        from sqlalchemy import func, select
        from .repositories.message_queue.models import MessageQueue, MessageStatus
        
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
            
            # Step 3: Atomic DB transaction
            with Session(self._engine) as session:
                # a) Get child instance
                instance = session.get(Instance, instance_id)
                if not instance:
                    return
                
                # b) Set child instance status to ERROR
                instance.status = InstanceStatus.ERROR.value
                instance.updated_at = datetime.now(timezone.utc).isoformat()
                
                # c) Fail associated message if provided
                if message_id:
                    message = session.get(MessageQueue, message_id)
                    if message:
                        message.status = MessageStatus.FAILED.value
                        message.completed_at = datetime.now(timezone.utc).isoformat()
                
                # d) Decrement parent's waiting_for counter
                parent = session.get(Instance, parent_id)
                if parent:
                    parent.waiting_for = max(0, (parent.waiting_for or 0) - 1)
                    parent.last_activity_at = datetime.now(timezone.utc)
                    parent.version = (parent.version or 1) + 1
                    
                    # e) Update parent's children[] cache
                    if parent.children:
                        try:
                            children_list = json.loads(parent.children) if isinstance(parent.children, str) else parent.children
                            if instance_id in children_list:
                                children_list = [c for c in children_list if c != instance_id]
                                parent.children = json.dumps(children_list)
                        except (json.JSONDecodeError, TypeError):
                            logger.warning(f"Failed to parse children JSON for parent {parent_id[:8]}...")
                    
                    # f) Delete from instance_hierarchy
                    session.execute(
                        text("DELETE FROM instance_hierarchy WHERE child_id = :child_id"),
                        {"child_id": instance_id}
                    )
                    
                    # g) Cascade: check if parent can complete after all children done/error
                    # FIX: Removed status restriction - cascade should run whenever waiting_for == 0,
                    # regardless of current status. Mirrors the fix in _update_parent_on_child_complete.
                    if parent.waiting_for == 0 and parent.status != InstanceStatus.COMPLETED.value:
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
                        ).one()
                        
                        if parent_pending == 0:
                            # No pending messages, parent is truly complete
                            parent.status = InstanceStatus.COMPLETED.value
                            parent.updated_at = datetime.now(timezone.utc).isoformat()
                            logger.info(f"Parent {parent.instance_id[:8]}... completed after child error")
                            
                            # Capture parent_id for event publishing (outside transaction)
                            completed_parent_id = parent.instance_id
                            completed_parent_parent_id = parent.parent_id
                            
                            session.commit()
                            
                            # FIX: Publish lifecycle event so JobFeedbackObserver completes the job
                            await self._publish_instance_lifecycle_event(
                                instance_id=completed_parent_id,
                                status="completed",
                                error=None,
                                parent_id=completed_parent_parent_id,
                            )
                        else:
                            # Has pending messages - transition to WAITING_CHILDREN
                            # Parent should wait for its message processing to complete
                            parent.status = InstanceStatus.WAITING_CHILDREN.value
                            parent.updated_at = datetime.now(timezone.utc).isoformat()
                            logger.info(
                                f"Parent {parent.instance_id[:8]}... all children done but has {parent_pending} "
                                f"pending messages, status=WAITING_CHILDREN after child error"
                            )
            
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
            if self._live_hub:
                try:
                    await self._live_hub.stream_lifecycle(
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
        
        messages = await get_instance_messages(self.checkpointer, instance_id)
        
        # Find the last assistant message
        last_assistant_content = None
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if content and content.strip():
                    last_assistant_content = content.strip()
                    break
        
        if last_assistant_content:
            return f"{prefix}, bellow is the response:\n{last_assistant_content}"
        return None

        
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
        # Skip if empty message
        if not message_content or not message_content.strip():
            return
        
        # Check if title already exists
        meta = await asyncio.to_thread(self._instance_repository.get, instance_id)
        if meta and meta.instance_metadata and meta.instance_metadata.get("title"):
            # Title already exists, skip
            logger.debug(f"Title already exists for instance {instance_id}, skipping generation")
            return
        
        from langchain_core.messages import HumanMessage, SystemMessage
        
        # Create LLM client for title generation
        # Use dedicated title model (falls back to main model if not configured)
        # Filter model_vision from config to avoid noisy LangChain warnings
        llm_config = {
            "base_url": self.config.llm.base_url,
            "api_key": self.config.llm.api_key,
            "model": self.config.llm.model_title,
            "temperature": 0.3,  # Lower temperature for more focused titles
        }
        # Remove model_vision if present (title generation doesn't need vision)
        llm_config = {k: v for k, v in llm_config.items() if k != "model_vision"}
        
        # Import here to use the same pattern as graph.py
        from .graph import ThinkingChatOpenAI
        llm = ThinkingChatOpenAI(**llm_config)
        
        title_prompt = f"""Generate a short, descriptive title (3-6 words max) for this user message. The title should summarize what the user is asking about or trying to accomplish.

User message:
{message_content[:500]}

Title:"""
        
        try:
            # One-shot with 30s timeout - title generation is not critical
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    llm.invoke,
                    [SystemMessage(content="You are a helpful assistant that generates concise instance titles."),
                     HumanMessage(content=title_prompt)]
                ),
                timeout=30.0
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
                title = " ".join(text_parts).strip()
            else:
                title = str(content).strip() if content else ""
            
            # Validate and truncate title
            if not title:
                return
            
            # Truncate to reasonable length (100 chars max)
            if len(title) > 100:
                title = title[:97] + "..."
            
            # Store title in instance metadata
            await asyncio.to_thread(self._instance_repository.update_title, instance_id, title)
            logger.info(f"Generated title for instance {instance_id}: {title}")
            # Title updates don't need explicit broadcast - frontend can refresh from instance metadata
            
        except asyncio.TimeoutError:
            logger.warning(f"Timeout generating title for instance {instance_id[:8]}...")
        except Exception as e:
            logger.warning(f"Failed to generate title for instance {instance_id}: {e}")

    def get_queue_stats(self, instance_id: str):
        """Get queue statistics for an instance.
        
        Returns a dict with pending_count, processing_count,
        and oldest_message_age_seconds attributes.
        """
        stats = self._queue_repository.get_stats(instance_id)
        return {
            "pending_count": stats["pending_count"],
            "processing_count": stats["processing_count"],
            "oldest_message_age_seconds": stats["oldest_message_age_seconds"]
        }

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
        return self._request_registry.cancel(message_id, reason)

    def cancel_instance_requests(self, instance_id: str, reason: CancellationReason) -> int:
        """Cancel all active requests for an instance. Returns count of cancelled."""
        message_ids = list(self._request_registry._by_instance.get(instance_id, set()))
        count = 0
        for msg_id in message_ids:
            if self.cancel(msg_id, reason):
                count += 1
        return count

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
        # Get instance metadata BEFORE modifying state (needed for children cascade)
        # Check if _instance_repository exists first (not all configs may have it)
        meta = None
        if hasattr(self, '_instance_repository') and self._instance_repository:
            meta = self._instance_repository.get(instance_id)
        
        # Cascade to children FIRST - terminate all child instances recursively
        if meta and meta.children:
            for child_id in list(meta.children):
                logger.info(f"Cascading terminate to child instance: {child_id[:8]}...")
                await self.terminate_instance(child_id)
        
        # 1. Cancel active requests for this instance
        self._request_registry.cancel_by_instance(instance_id)
        
        # 2. Clean up live hub connections for this instance
        await self._live_hub.cleanup_instance(instance_id)

        # 3. Remove from instances dict
        if instance_id in self.instances:
            del self.instances[instance_id]
        else:
            # Instance not in memory but might still need cleanup (children cascade)
            if meta is None:
                return False

        # 5. Update DB status to terminated using repository
        if hasattr(self, '_instance_repository') and self._instance_repository:
            self._instance_repository.update_status(instance_id, "terminated")

        # 6. Release project lock if JobQueueService is connected (async)
        if self._job_queue_service is not None:
            try:
                released_projects = await self._job_queue_service.release_lock_by_instance(instance_id)
                if released_projects:
                    logger.info(
                        f"Released {len(released_projects)} project lock(s) for instance {instance_id[:8]}...: "
                        f"{released_projects}"
                    )
            except Exception as e:
                logger.warning(f"Failed to release locks for instance {instance_id[:8]}...: {e}")

        # 7. Mark any associated job as failed
        if self._job_queue_service is not None:
            try:
                job = self._job_queue_service.get_job_by_instance_sync(instance_id)
                if job is not None and job.status == "processing":
                    self._job_queue_service.complete_job_sync(
                        job.job_id, success=False, error="Instance terminated",
                        result_summary=None,
                    )
                    # Trigger next pending job for this project
                    if job.project_id:
                        self._job_queue_service.trigger_next_job_sync(job.project_id)
            except Exception as e:
                logger.warning(f"Failed to mark job as failed on terminate: {e}")

        # 8. Publish lifecycle event for terminated instance
        parent_id = meta.parent_id if meta else None
        await self._publish_instance_lifecycle_event(
            instance_id=instance_id,
            status="terminated",
            error=None,
            parent_id=parent_id,
        )

        return True

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
        event_data = {
            "instance_id": instance_id,
            "status": status,
            "error": error,
            "parent_id": parent_id,
        }
        
        try:
            # Publish via EventBus - this broadcasts to global subscribers including
            # JobFeedbackObserver which listens for job completion feedback
            await self._event_bus.create_event(
                instance_id=instance_id,
                kind=EventKind.INSTANCE_LIFECYCLE,
                data=event_data,
            )
            logger.debug(f"Published INSTANCE_LIFECYCLE event for {instance_id[:8]}...: status={status}")
        except Exception as e:
            logger.warning(f"Failed to publish INSTANCE_LIFECYCLE event for {instance_id[:8]}...: {e}")

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
        # Check in-memory cache first
        if instance_id in self.instances:
            graph, _ = self.instances[instance_id]
            return graph

        # Not in memory - check database and restore if found
        meta = self._instance_repository.get(instance_id)
        if meta is None:
            raise KeyError(f"Instance not found: {instance_id}")

        # Instance exists in DB but not in memory - restore it
        return self._restore_instance(instance_id, meta)

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
        # Load and cache prompt
        agent_path = Path(meta.agent_dir)
        system_prompt, token_count = load_and_cache_prompt(meta.agent_id, agent_path, self.prompt_cache)

        # Create tools with this manager reference
        tools = create_instance_tools(self, instance_id, meta.agent_id)

        # Build LLM config
        llm_config = {
            "base_url": self.config.llm.base_url,
            "api_key": self.config.llm.api_key,
            "model": self.config.llm.model,
            "model_vision": self.config.llm.model_vision,
            "temperature": self.config.llm.temperature,
            "request_timeout": self.config.llm.request_timeout,
        }

        # Build retry config from queue settings
        retry_config = {
            "transient_attempts": self.config.queue.llm_retry_transient_attempts,
            "timeout_attempts": self.config.queue.llm_retry_timeout_attempts,
        }

        # Build graph config with thread_id for state management
        config = {
            "configurable": {"thread_id": instance_id},
            "recursion_limit": self.config.limits.graph_recursion_limit,
        }

        # Build graph with checkpointer (will restore state from checkpoints)
        graph = build_instance_graph(
            tools=tools,
            checkpointer=self.checkpointer,
            llm_config=llm_config,
            system_prompt=system_prompt,
            retry_config=retry_config,
            compactor=self._compactor,
            graph_config=config,
        )

        # Store in instances dict
        self.instances[instance_id] = (graph, meta.agent_dir)

        return graph

    def list_instances(self, limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
        """List instances with pagination.

        Args:
            limit: Maximum number of instances to return (default: 20).
            offset: Number of instances to skip (default: 0).

        Returns:
            Tuple of (list of instance info dictionaries, total count).
        """
        instances, total = self._instance_repository.list(limit=limit, offset=offset)
        # Convert Instance objects to dicts for backward compatibility
        return [i.to_dict() for i in instances], total

    def get_instance_info(self, instance_id: str) -> dict:
        """Get information about a specific instance.

        Args:
            instance_id: The ID of the instance.

        Returns:
            Instance metadata dictionary from the database.

        Raises:
            KeyError: If instance is not found.
        """
        meta = self._instance_repository.get(instance_id)
        if meta is None:
            raise KeyError(f"Instance not found: {instance_id}")
        return meta.to_dict()

    async def get_messages(self, instance_id: str) -> list[dict]:
        """Get message history for an instance.

        Args:
            instance_id: The ID of the instance.

        Returns:
            List of message dictionaries from LangGraph checkpoints.

        Raises:
            KeyError: If instance is not found.
        """
        # Verify instance exists
        self.get_instance(instance_id)  # raises KeyError if not found
        
        return await get_instance_messages(self.checkpointer, instance_id)

    def clear_all_instances(self) -> int:
        """Clear all instances from memory and database.

        Returns:
            Number of instances deleted from database.
        """
        # Clear in-memory instances
        self.instances.clear()

        # Clear database instances
        return self._instance_repository.delete_all()
    
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

    def _edit_distance(self, s1: str, s2: str) -> int:
        """Calculate Levenshtein edit distance between two strings.
        
        Args:
            s1: First string.
            s2: Second string.
            
        Returns:
            The minimum number of edit operations (insertions, deletions, substitutions)
            needed to transform s1 into s2.
        """
        if len(s1) < len(s2):
            return self._edit_distance(s2, s1)
        
        if len(s2) == 0:
            return len(s1)
        
        previous_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                # cost is 0 if characters match, 1 otherwise
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        
        return previous_row[-1]

    def find_near_instance(self, instance_id: str, max_distance: int = 2) -> str | None:
        """Find a near-matching instance ID from recent instances.
        
        Searches through recent instances using edit distance to find a close match.
        Matching is case-insensitive.
        
        Args:
            instance_id: The instance ID to find a near match for.
            max_distance: Maximum edit distance threshold (default: 2).
            
        Returns:
            The near-matching instance_id if found, None otherwise.
        """
        # Get recent instances from repository (ordered by recency)
        instances, _ = self._instance_repository.list(limit=100, offset=0)
        
        # Normalize input for case-insensitive comparison
        normalized_input = instance_id.lower()
        
        for instance in instances:
            # Skip if length difference exceeds threshold (quick filter)
            stored_id = instance.instance_id
            if abs(len(stored_id) - len(instance_id)) > max_distance:
                continue
            
            # Case-insensitive edit distance
            distance = self._edit_distance(normalized_input, stored_id.lower())
            if distance <= max_distance:
                return stored_id
        
        return None

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
        if self._shutting_down:
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
        # Use asyncio.to_thread to avoid blocking the event loop with the thread lock
        message_ids = await asyncio.to_thread(self._request_registry.get_all_message_ids)
        
        if message_ids:
            logger.info(f"Cancelling {len(message_ids)} active request(s)...")
            for message_id in message_ids:
                self._request_registry.cancel(message_id, CancellationReason.SHUTDOWN)
    
    async def _wait_for_inflight(self, grace_period: float) -> None:
        """Wait for in-flight processing to finish.
        
        Args:
            grace_period: Maximum seconds to wait.
        """
        start_time = time.monotonic()
        while time.monotonic() - start_time < grace_period:
            # Check if any requests are still active
            with self._request_registry._lock:
                active_requests = len(self._request_registry._requests)
            
            if active_requests == 0:
                logger.debug("All requests completed, proceeding with shutdown")
                break
            
            logger.debug(
                f"Waiting for shutdown: {active_requests} active requests"
            )
            await asyncio.sleep(0.5)

    def get_active_requests(self, instance_id: str) -> list[str]:
        """Get list of active request message IDs for an instance.
        
        Args:
            instance_id: The instance ID to check.
        
        Returns:
            List of message IDs that are currently being processed.
        """
        return self._request_registry.get_active_for_instance(instance_id)
    
    @property
    def is_shutting_down(self) -> bool:
        """Check if shutdown is in progress."""
        return getattr(self, '_shutting_down', False)
