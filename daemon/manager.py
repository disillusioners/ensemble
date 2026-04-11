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
from .registry import get_registry

from .repositories.instance.repository import get_agent_name
from .repositories.instance.models import Instance
from .tools import create_instance_tools
from .sources import SourceRegistry, ResponseDispatcher, SourceCleanup
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

# UUID validation pattern (compiled once at module level)
_UUID_PATTERN = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', re.IGNORECASE)

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


# Pattern for parsing <think/> tags
_THINK_PATTERN = re.compile(r'<think[^>]*>(.*?)</think\s*>', re.DOTALL | re.IGNORECASE)


def parse_think_tags(content: str) -> tuple[str, str | None]:
    """Parse <think/> tags from message content.
    
    Extracts thinking content from <think...>...</think tags and removes them
    from the content string. Handles multiple think blocks by combining them
    with newlines.
    
    Args:
        content: The message content potentially containing think tags.
        
    Returns:
        Tuple of (cleaned_content, thinking_extracted) where:
        - cleaned_content: Content with think tags removed
        - thinking_extracted: Combined content from think tags, or None if none found
    """
    think_matches = _THINK_PATTERN.findall(content)
    if think_matches:
        thinking_extracted = '\n'.join(think_matches).strip()
        cleaned_content = _THINK_PATTERN.sub('', content).strip()
        return cleaned_content, thinking_extracted
    return content, None


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
        
        # Development helper: discard all queued messages on startup
        if config.queue.discard_on_startup:
            count = self._queue_repository.clear_all()
            logger.info(f"Discarded {count} messages from queue (discard_on_startup=True)")
        
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
        
        # Create EventBus for ResponseDispatcher (will be updated with real event_repo in prepare)
        from .repositories.event.repository import EventRepository
        _event_repo_for_bus = EventRepository(engine=self._engine)
        self._event_bus = EventBus(event_repo=_event_repo_for_bus)
        
        self.source_dispatcher = ResponseDispatcher(
            event_bus=self._event_bus,
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
        from .repositories.task.repository import TaskRepository
        from .repositories.task.models import Task
        from .repositories.event.repository import EventRepository
        
        task_repo = TaskRepository(engine=self._engine)
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
        )
        
        # Create and start worker pool with timeout/retry config
        self._worker_pool = WorkerPool(
            task_processor=self._task_processor,
            num_workers=num_workers,
            poll_interval=svc.worker_poll_interval,
            timeout_minutes=svc.task_timeout_minutes,
            max_retries=svc.max_task_retries,
            retry_backoff_base=svc.task_retry_backoff_base,
            retry_backoff_max=svc.task_retry_backoff_max,
        )
        self._worker_pool.start()
        
        logger.info(f"Worker pool started with {num_workers} workers (poll_interval={svc.worker_poll_interval}s, timeout={svc.task_timeout_minutes}min)")

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

    async def _complete_job_for_instance(
        self,
        instance_id: str,
        success: bool,
        error: str | None = None,
        result_summary: str | None = None,
    ) -> None:
        """Update job status when instance completes.
        
        Looks up the job associated with this instance and marks it
        as completed or failed based on success parameter.
        Also triggers the next pending job for the same project.
        """
        if self._job_queue_service is None:
            return
        
        try:
            job = await self._job_queue_service.get_job_by_instance(instance_id)
            if job is None:
                return  # No job associated with this instance
            
            if success:
                await self._job_queue_service.complete_job(
                    job.job_id, success=True, error=None,
                    result_summary=result_summary,
                )
            else:
                await self._job_queue_service.complete_job(
                    job.job_id, success=False, error=error or "Instance failed"
                )
            
            # Trigger next pending job for this project
            if job.project_id:
                await self._job_queue_service.trigger_next_job(job.project_id)
                
        except Exception as e:
            logger.warning(f"Failed to update job status for instance {instance_id}: {e}")

    def spawn_instance(
        self, 
        agent_id: str,
        instance_id: str | None = None, 
        parent_id: str | None = None,
        project_id: str | None = None,
    ) -> str:
        """Create a new agent instance.

        Args:
            agent_id: Agent ID (e.g., "coder").
            instance_id: Optional instance ID. Auto-generated if not provided or invalid.
            parent_id: Optional parent instance ID for hierarchical instances.
            project_id: Optional project ID for project context. Use `None` to explicitly
                indicate no project context is needed. If provided, stored in instance
                metadata so child instances don't rely on text extraction.

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
        
        self._instance_repository.create(
            instance_id=instance_id,
            agent_id=resolved_agent_id,
            agent_dir=resolved_agent_dir,
            parent_id=parent_id,
            metadata=instance_metadata if instance_metadata else None,
        )

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
        priority: int = 1
    ) -> AsyncMessageResult:
        """Enqueue a message using the worker pool (DB-backed) path.
        
        This method creates BOTH a MessageQueue entry AND a Task entry atomically
        in a single transaction. Workers poll the task table and process messages.
        
        Args:
            instance_id: The ID of the target instance.
            message: The message content.
            source: Source identifier (e.g., "api", "web", "telegram:user:123").
            priority: Message priority (0=system, 1=user).
        
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
        from .repositories.instance.models import Instance, InstanceStatus
        
        message_id = str(uuid.uuid4())
        
        # Determine message type based on source
        if source.startswith("report:"):
            msg_type = MessageType.COMPLETION_REPORT.value
        elif source.startswith("error_report:"):
            msg_type = MessageType.ERROR_REPORT.value
        elif source.startswith("agent:"):
            msg_type = MessageType.AGENT.value
        else:
            msg_type = MessageType.HUMAN.value
        
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
            
            # 4. Create event for the new message
            event = Event(
                instance_id=instance_id,
                message_id=message_id,
                kind=EventKind.MESSAGE_RECEIVED.value,
                data={"source": source, "priority": priority},
                created_at=datetime.now(timezone.utc),
            )
            session.add(event)
            
            session.commit()
        
        # Broadcast event asynchronously (fire and forget)
        try:
            await self._event_bus.create_message_received_event(
                instance_id=instance_id,
                message_id=message_id,
                content={"source": source, "priority": priority, "content": message}
            )
        except Exception as e:
            logger.warning(f"Failed to broadcast message_received event: {e}")
        
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
        
        # Variables to collect during streaming
        all_tool_calls = []
        tool_call_map = {}  # Track tool calls by ID to match with outputs
        thinking_content = None
        final_content = ""
        
        # Content chunk batching to reduce event rate
        content_buffer = ""
        content_buffer_size = 0
        thinking_buffer = ""  # Accumulate reasoning_content from delta chunks
        CONTENT_BATCH_THRESHOLD = 500  # Flush after 500 characters
        CONTENT_BATCH_TIMEOUT = 0.5  # Or after 500ms (whichever comes first)
        last_content_flush = time.monotonic()  # Initialize to current time
        
        # Thinking event batching to reduce event rate
        THINKING_BATCH_THRESHOLD = 500  # chars
        THINKING_BATCH_TIMEOUT = 0.5   # 500ms
        thinking_buffer_size = 0
        last_thinking_flush = time.monotonic()
        
        # Adaptive batching settings (adjusted based on queue health)
        adaptive_threshold = CONTENT_BATCH_THRESHOLD
        adaptive_timeout = CONTENT_BATCH_TIMEOUT
        adaptive_thinking_threshold = THINKING_BATCH_THRESHOLD
        adaptive_thinking_timeout = THINKING_BATCH_TIMEOUT
        
        # Event counter for monitoring
        event_count = 0
        
        
        # Build input - on retry with checkpoint, resume from None
        if not is_retry:
            await self._maybe_compact_context(instance_id, graph, config)
        
        if is_retry:
            if await self._has_checkpoint(instance_id):
                logger.info(f"Resuming instance {instance_id[:8]}... from checkpoint (retry #{retry_count})")
                graph_input = None  # LangGraph will resume from checkpoint
            else:
                logger.warning(f"Retry for instance {instance_id[:8]}... but no checkpoint found, re-adding message")
                graph_input = {"messages": [message]}
        else:
            # First attempt - add message to conversation
            graph_input = {"messages": [message]}
        
        # Stream through graph execution
        # When using multiple stream modes, events are tuples: (mode, data)
        
        try:
            async with self._llm_semaphore:
                async for event in graph.astream(graph_input, config, stream_mode=["updates", "messages"]):
                    # Unpack tuple: (mode, data)
                    if isinstance(event, tuple):
                        mode, data = event
                    else:
                        # Single mode - treat as updates
                        mode = "updates"
                        data = event
                    
                    if mode == "updates":
                        # Handle node-level updates
                        if "agent" in data:
                            # Agent node completed - could have new thinking or content
                            agent_output = data["agent"]
                            if "messages" in agent_output:
                                latest_msg = agent_output["messages"][-1]
                                if hasattr(latest_msg, 'content'):
                                    final_content = latest_msg.content or ""
                                
                                # Extract thinking from the message
                                if not thinking_content:
                                    if hasattr(latest_msg, 'thinking') and latest_msg.thinking:
                                        thinking_content = latest_msg.thinking
                                    elif hasattr(latest_msg, 'additional_kwargs'):
                                        kwargs = latest_msg.additional_kwargs or {}
                                        thinking_content = kwargs.get("reasoning_content") or kwargs.get("thinking")
                                    
                                    if thinking_content:
                                        # Broadcast thinking event
                                        await self._event_bus.broadcast_streaming_event(
                                            instance_id=instance_id,
                                            event_type="thinking",
                                            data={"content": thinking_content}
                                        )
                                
                                # Track tool calls from AI message for matching
                                if hasattr(latest_msg, 'tool_calls') and latest_msg.tool_calls:
                                    for tc in latest_msg.tool_calls:
                                        tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                                        tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                                        tc_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                                        
                                        # Store for matching with tool call
                                        tool_call_map[tc_id] = {
                                            "name": tc_name,
                                            "args": tc_args,
                                        }
                                        
                                        # Broadcast tool_call event (tool starting)
                                        await self._event_bus.broadcast_streaming_event(
                                            instance_id=instance_id,
                                            event_type="tool_call",
                                            data={
                                                "id": tc_id,
                                                "name": tc_name,
                                                "arguments": tc_args,
                                            }
                                        )
                                
                                elif "tools" in data:
                                    # Tools node completed - tool execution finished
                                    tool_messages = data["tools"].get("messages", [])
                                    for tool_msg in tool_messages:
                                        # Get tool_call_id to match with original call
                                        tool_call_id = getattr(tool_msg, 'tool_call_id', None)
                                        
                                        # Skip if no tool_call_id
                                        if not tool_call_id:
                                            logger.warning(f"Tool message missing tool_call_id: {tool_msg}")
                                            continue
                                        
                                        # Look up original tool call info
                                        original_call = tool_call_map.get(tool_call_id)
                                        
                                        if not original_call:
                                            logger.warning(f"No matching tool call for ID {tool_call_id}, using fallback")
                                            original_call = {"name": getattr(tool_msg, 'name', 'unknown'), "args": {}}
                                        
                                        tool_call_data = {
                                            "id": tool_call_id,
                                            "name": original_call.get("name", getattr(tool_msg, 'name', 'unknown')),
                                            "arguments": original_call.get("args", {}),
                                            "output": getattr(tool_msg, 'content', ""),
                                        }
                                        
                                        # Broadcast tool_complete event
                                        await self._event_bus.broadcast_streaming_event(
                                            instance_id=instance_id,
                                            event_type="tool_complete",
                                            data=tool_call_data
                                        )
                                
                    elif mode == "messages":
                        # Handle token-level streaming with adaptive batching to reduce event rate
                        # data is a tuple: (message_chunk, metadata)
                        if isinstance(data, tuple) and len(data) == 2:
                            chunk, metadata = data
                            if hasattr(chunk, 'content') and chunk.content:
                                content_buffer += chunk.content
                                content_buffer_size += len(chunk.content)
                                event_count += 1
                            
                            # Accumulate reasoning_content from delta chunks (e.g., GLM extended thinking)
                            chunk_reasoning = None

                            # Try 1: additional_kwargs (standard LangChain location for reasoning_content)
                            if hasattr(chunk, 'additional_kwargs'):
                                kwargs = chunk.additional_kwargs or {}
                                chunk_reasoning = kwargs.get("reasoning_content") or kwargs.get("thinking")

                            # Try 2: direct reasoning_content attribute (some LangChain versions)
                            if not chunk_reasoning and hasattr(chunk, 'reasoning_content'):
                                chunk_reasoning = chunk.reasoning_content

                            # Try 3: response_metadata (some provider-specific implementations)
                            if not chunk_reasoning and hasattr(chunk, 'response_metadata'):
                                meta = chunk.response_metadata or {}
                                chunk_reasoning = meta.get("reasoning_content") or meta.get("thinking")

                            # Try 4: content as list (Responses API format: [{"type": "reasoning", "reasoning": "..."}])
                            if not chunk_reasoning and hasattr(chunk, 'content') and isinstance(chunk.content, list):
                                for block in chunk.content:
                                    if isinstance(block, dict):
                                        if block.get("type") == "reasoning":
                                            chunk_reasoning = block.get("reasoning") or block.get("summary_text", "")
                                            break
                                        elif block.get("type") == "reasoning_summary_text":
                                            chunk_reasoning = block.get("text", "")
                                            break

                            if chunk_reasoning:
                                thinking_buffer += chunk_reasoning
                                thinking_buffer_size = len(thinking_buffer)
                                
                                now = time.monotonic()
                                should_flush = (
                                    thinking_buffer_size >= adaptive_thinking_threshold or
                                    (now - last_thinking_flush) >= adaptive_thinking_timeout
                                )
                                
                                if should_flush and thinking_buffer:
                                    await self._event_bus.broadcast_streaming_event(
                                        instance_id=instance_id,
                                        event_type="thinking",
                                        data={"content": thinking_buffer}
                                    )
                                    thinking_buffer = ""
                                    thinking_buffer_size = 0
                                    last_thinking_flush = now
                                
                                # Flush if buffer exceeds threshold OR timeout elapsed
                                now = time.monotonic()
                                should_flush = (
                                    content_buffer_size >= adaptive_threshold or
                                    (now - last_content_flush) >= adaptive_timeout
                                )
                                
                                if should_flush and content_buffer:
                                    await self._event_bus.broadcast_streaming_event(
                                        instance_id=instance_id,
                                        event_type="content_chunk",
                                        data={"chunk": content_buffer}
                                    )
                                    content_buffer = ""
                                    content_buffer_size = 0
                                    last_content_flush = now
                                    
                                    # Adaptive batching: check queue health periodically
                                    if event_count % 20 == 0:
                                        # Get streaming queue stats for adaptive batching
                                        queue = self._event_bus.get_streaming_queue(instance_id)
                                        queue_fill_ratio = queue.qsize() / max(queue.maxsize, 1)
                                        
                                        # Increase batch size when queue is > 50% full
                                        if queue_fill_ratio > 0.5:
                                            adaptive_threshold = min(CONTENT_BATCH_THRESHOLD * 1.5, 2000)  # max 2000
                                            adaptive_timeout = min(CONTENT_BATCH_TIMEOUT * 1.5, 1.0)     # max 1.0s
                                            adaptive_thinking_threshold = min(THINKING_BATCH_THRESHOLD * 3, 2000)  # max 2000
                                            adaptive_thinking_timeout = min(THINKING_BATCH_TIMEOUT * 2, 1.0)     # max 1.0s
                                            if event_count == 20:  # Log once
                                                logger.info(
                                                    f"Queue at {queue_fill_ratio:.0%} capacity, "
                                                    f"increasing batch size for instance {instance_id[:8]}"
                                                )
                                        else:
                                            adaptive_threshold = CONTENT_BATCH_THRESHOLD
                                            adaptive_timeout = CONTENT_BATCH_TIMEOUT
                                            adaptive_thinking_threshold = THINKING_BATCH_THRESHOLD
                                            adaptive_thinking_timeout = THINKING_BATCH_TIMEOUT
        except Exception as e:
            logger.error(f"Streaming failed for message {message_id}: {e}")
            # Broadcast error event
            await self._event_bus.create_error_event(
                instance_id=instance_id,
                error={"error": str(e), "stage": "streaming", "message_id": message_id}
            )
            raise  # Re-raise to let caller handle retry logic
        finally:
            # Flush any remaining content in buffer after streaming ends
            # This runs even on timeout so content already generated is sent to client
            if content_buffer:
                await self._event_bus.broadcast_streaming_event(
                    instance_id=instance_id,
                    event_type="content_chunk",
                    data={"chunk": content_buffer}
                )
                logger.debug(f"Flushed final content chunk batch: {len(content_buffer)} chars")
            
            # Flush any remaining thinking in buffer after streaming ends
            if thinking_buffer:
                await self._event_bus.broadcast_streaming_event(
                    instance_id=instance_id,
                    event_type="thinking",
                    data={"content": thinking_buffer}
                )
                thinking_buffer = ""
        
        # Transfer accumulated thinking from streaming chunks
        if thinking_buffer and not thinking_content:
            thinking_content = thinking_buffer
        
        # After streaming completes, get final result
        # Validate final_result exists
        final_result = await graph.aget_state(config)
        if not final_result:
            logger.error(f"No final state for instance {instance_id} after streaming")
            return MessageResult(content="", tool_calls=None)
        
        messages = final_result.values.get("messages", [])
        
        # Find current turn start (last HumanMessage)
        # Only process messages from current turn to avoid duplicates from history
        current_turn_start = 0
        for i in range(len(messages) - 1, -1, -1):
            if hasattr(messages[i], 'type') and messages[i].type == 'human':
                current_turn_start = i
                break
        
        # Single-pass extraction: tool outputs, tool calls, thinking, and final content
        tool_outputs = {}
        all_tool_calls = []
        last_ai_message = None
        
        for msg in messages[current_turn_start:]:
            # Build tool outputs map
            if hasattr(msg, 'tool_call_id'):
                tool_outputs[msg.tool_call_id] = msg.content
            
            # Extract tool calls
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                    tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    tc_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                    
                    all_tool_calls.append({
                        "id": tc_id,
                        "name": tc_name,
                        "arguments": tc_args,
                        "output": tool_outputs.get(tc_id),
                    })
            
            # Track last AI message for thinking and content
            if hasattr(msg, 'type') and msg.type == 'ai':
                last_ai_message = msg
        
        # Extract thinking from last AI message
        if last_ai_message and not thinking_content:
            if hasattr(last_ai_message, 'thinking') and last_ai_message.thinking:
                thinking_content = last_ai_message.thinking
            elif hasattr(last_ai_message, 'additional_kwargs'):
                kwargs = last_ai_message.additional_kwargs or {}
                thinking_content = kwargs.get("reasoning_content") or kwargs.get("thinking")
        
        # Extract final content from last AI message if not set during streaming
        if last_ai_message and not final_content:
            final_content = last_ai_message.content or ""
        
        # Parse <think/> tags from content
        content, thinking_extracted = parse_think_tags(final_content)
        
        return MessageResult(
            content=content,
            thinking=thinking_content,
            thinking_extracted=thinking_extracted,
            tool_calls=all_tool_calls if all_tool_calls else None,
        )

    async def _summarize_instance(self, instance_id: str, agent_name: str) -> str:
        """Summarize instance messages using LLM.
        
        Args:
            instance_id: The instance ID to summarize.
            agent_name: The name of the agent (e.g., "Coder", "Designer").
            
        Returns:
            Formatted summary string: "{agent_name} has done, bellow is {agent_name} response: {summary}"
        """
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI
        
        # Get instance messages
        messages = await get_instance_messages(self.checkpointer, instance_id)
        
        if not messages:
            return f"{agent_name} has done, bellow is {agent_name} response: No activity recorded."
        
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
            return f"{agent_name} has done, bellow is {agent_name} response: No messages to summarize."
        
        conversation = "\n".join(conversation_text)
        
        # Create LLM client for summarization using the same config pattern
        llm_config = {
            "base_url": self.config.llm.base_url,
            "api_key": self.config.llm.api_key,
            "model": self.config.llm.model,
            "temperature": 0.3,  # Lower temperature for more focused summaries
            "default_headers": {"x-proxy-app": "ensemble"},
        }
        
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
            return f"{agent_name} has done, bellow is {agent_name} response: {summary}"
        except Exception as e:
            logger.warning(f"Failed to summarize instance {instance_id}: {e}")
            # Fallback: count messages and provide basic summary
            return f"{agent_name} has done, bellow is {agent_name} response: Completed {len(messages)} message(s)."

    async def _check_child_completion_v2(self, instance_id: str) -> None:
        """Atomic check if child instance is done and should send completion report.
        
        CRITICAL FIX C3: Content is fetched BEFORE the transaction to avoid
        leaving the instance in COMPLETED state without a report if the fetch fails.
        
        This method handles:
        - Idempotency (won't send duplicate completion reports)
        - Parent's waiting_for counter decrement
        - Parent's children[] cache update (FIX: W6)
        - Cascade: if parent's waiting_for reaches 0, transition parent to RUNNING
        
        Args:
            instance_id: The child instance that may have completed.
        """
        import uuid
        from datetime import datetime, timezone
        from sqlmodel import Session
        from sqlalchemy import func, select, delete as sql_delete
        from .repositories.instance.models import Instance, InstanceStatus
        from .repositories.message_queue.models import MessageQueue, MessageStatus, MessageType
        from .repositories.task.models import Task, TaskType, TaskStatus
        from .repositories.event.models import Event, EventKind
        
        # FIX C3: Fetch content BEFORE transaction — avoid orphaned COMPLETED state
        last_content = await self._get_last_assistant_message(instance_id, agent_name="agent")
        if last_content is None:
            logger.warning(f"No content found for instance {instance_id[:8]}..., skipping completion check")
            return
        
        with Session(self._engine) as session:
            # Get instance metadata
            instance = session.get(Instance, instance_id)
            if instance is None:
                return
            
            # Not a child? Nothing to do
            if instance.parent_id is None:
                logger.debug(f"Instance {instance_id[:8]}... has no parent, skipping completion check")
                return
            
            # Check for pending/processing messages for this instance
            pending_count = session.exec(
                select(func.count())
                .select_from(MessageQueue)
                .where(MessageQueue.instance_id == instance_id)
                .where(MessageQueue.status.in_([
                    MessageStatus.READY.value,
                    MessageStatus.PROCESSING.value,
                    MessageStatus.RETRYING.value,
                ]))
            ).one()
            
            if pending_count > 0:
                logger.debug(
                    f"Instance {instance_id[:8]}... has {pending_count} pending messages, "
                    f"skipping completion check"
                )
                return
            
            # FIX C3 - Idempotency: Check if completion report already sent
            existing_report = session.exec(
                select(MessageQueue)
                .where(MessageQueue.instance_id == instance.parent_id)
                .where(MessageQueue.source == f"report:{instance_id}")
                .where(MessageQueue.status.in_([
                    MessageStatus.READY.value,
                    MessageStatus.PROCESSING.value,
                    MessageStatus.COMPLETED.value,
                ]))
            ).first()
            
            if existing_report is not None:
                logger.debug(
                    f"Completion report already queued for child {instance_id[:8]}..., "
                    f"skipping duplicate"
                )
                return
            
            # ATOMIC: Instance completed — create completion report for parent
            logger.info(f"Instance {instance_id[:8]}... completed, sending report to parent {instance.parent_id[:8]}...")
            
            # Update child instance status to COMPLETED
            instance.status = InstanceStatus.COMPLETED.value
            instance.updated_at = datetime.now(timezone.utc).isoformat()
            instance.last_activity_at = datetime.now(timezone.utc)
            instance.version = (instance.version or 1) + 1
            
            # Create completion report message for parent
            report_message_id = str(uuid.uuid4())
            report_message = MessageQueue(
                message_id=report_message_id,
                instance_id=instance.parent_id,
                content=last_content,  # Already fetched before transaction
                source=f"report:{instance_id}",
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
            
            # Get parent instance
            parent = session.get(Instance, instance.parent_id)
            if parent:
                # Decrement parent's waiting_for counter
                parent.waiting_for = max(0, (parent.waiting_for or 0) - 1)
                parent.last_activity_at = datetime.now(timezone.utc)
                parent.version = (parent.version or 1) + 1
                
                # FIX W6: Update parent's children[] denormalized cache
                # Note: instance_hierarchy is the canonical source; we update the cache here
                if parent.children:
                    try:
                        import json
                        children_list = json.loads(parent.children) if isinstance(parent.children, str) else parent.children
                        if instance_id in children_list:
                            children_list = [c for c in children_list if c != instance_id]
                            parent.children = json.dumps(children_list)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(f"Failed to parse children JSON for parent {instance.parent_id[:8]}...")
                
                # Update instance_hierarchy junction table (canonical source)
                session.exec(
                    sql_delete(Instance.__table__)
                    .where(Instance.instance_id == instance_id)
                )
                # Actually use proper delete
                session.execute(
                    text("DELETE FROM instance_hierarchy WHERE child_id = :child_id"),
                    {"child_id": instance_id}
                )
                
                # Cascade check: if parent is WAITING_CHILDREN and waiting_for is 0, transition to RUNNING
                if (parent.waiting_for == 0 and 
                    parent.status == InstanceStatus.WAITING_CHILDREN.value):
                    
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
                        logger.info(f"Parent {parent.instance_id[:8]}... completed after all children done")
                    else:
                        # Has pending messages, transition to RUNNING to process them
                        parent.status = InstanceStatus.RUNNING.value
                        logger.info(
                            f"Parent {parent.instance_id[:8]}... has {parent_pending} pending messages, "
                            f"transitioning to RUNNING"
                        )
            
            # Create completion event
            completion_event = Event(
                instance_id=instance_id,
                kind=EventKind.INSTANCE_COMPLETED.value,
                data={
                    "parent_id": instance.parent_id,
                    "report_message_id": report_message_id,
                },
                created_at=datetime.now(timezone.utc),
            )
            session.add(completion_event)
            
            # Also create event for parent about child completion
            parent_event = Event(
                instance_id=instance.parent_id,
                message_id=report_message_id,
                kind=EventKind.CHILD_COMPLETED.value,
                data={
                    "child_instance_id": instance_id,
                    "waiting_for_remaining": (parent.waiting_for - 1) if parent else 0,
                },
                created_at=datetime.now(timezone.utc),
            )
            session.add(parent_event)
            
            session.commit()
        
        # Broadcast child completion event asynchronously
        try:
            await self._event_bus.create_child_completed_event(
                instance_id=instance.parent_id,
                child_id=instance_id,
            )
        except Exception as e:
            logger.warning(f"Failed to broadcast child completion event: {e}")

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
        - Watchdog timeout
        - Circuit breaker opened
        - Unhandled exception
        
        Args:
            instance_id: The child instance ID that has failed.
            error: The error message describing what went wrong.
            error_type: Category of error (e.g., "max_retries", "timeout", "circuit_breaker").
            message_id: Optional message ID that triggered the error.
        """
        try:
            # Prevent duplicate error reports - check if we already sent one
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
                        if existing_msg.source == f"error_report:{instance_id}":
                            logger.debug(f"Error report already queued for instance {instance_id[:8]}..., skipping duplicate")
                            return
            
            # Get instance metadata
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
            
            # Truncate error to prevent massive messages
            truncated_error = error[:2000] if len(error) > 2000 else error
            
            # Determine severity based on error type
            severity = "critical" if error_type in ["max_retries_exceeded", "circuit_breaker_open"] else "warning"
            
            # Format error report message
            error_report = f"⚠️ {agent_name} encountered an error:\n\n**Error Type:** {error_type}\n**Severity:** {severity}\n**Details:** {truncated_error}"
            
            # Enqueue error report message to parent using repository
            msg = await asyncio.to_thread(
                self._queue_repository.enqueue,
                instance_id=parent_id,
                content=error_report,
                source=f"error_report:{instance_id}",
                priority=1,  # Normal priority
                message_metadata={
                    "type": "error_report", 
                    "child_instance_id": instance_id,
                    "error_type": error_type,
                    "error": truncated_error,
                    "original_message_id": message_id,
                    "severity": severity,
                    "recoverable": error_type in ["watchdog_timeout", "circuit_breaker_open"],
                }
            )
            report_message_id = msg.message_id
            
            # Broadcast error report event
            await self._event_bus.create_child_failed_event(
                instance_id=parent_id,
                child_id=instance_id,
                error={
                    "type": "error_report",
                    "child_instance_id": instance_id,
                    "agent_name": agent_name,
                    "error_type": error_type,
                    "error": truncated_error,
                    "original_message_id": message_id,
                    "severity": severity,
                }
            )
            
            logger.info(f"Sent error report from {agent_name} ({instance_id[:8]}...) to parent ({parent_id[:8]}...)")
            
        except Exception as e:
            logger.error(
                f"Failed to send error report for instance {instance_id[:8]}...: {e}. "
                f"Original error was: {error_type}: {error[:200]}"
            )

    async def _get_last_assistant_message(self, instance_id: str, agent_name: str) -> str | None:
        """Get the last assistant message from instance history.
        
        This is the default/simple approach for completion reports - just
        pass the agent's last response to the parent.
        
        Args:
            instance_id: The instance ID to get message from.
            agent_name: The name of the agent (e.g., "Coder", "Designer").
            
        Returns:
            Formatted string: "{agent_name} has done: {last_message}"
        """
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
            return f"{agent_name} has done, bellow is {agent_name} response:\n{last_assistant_content}"
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
        llm_config = {
            "base_url": self.config.llm.base_url,
            "api_key": self.config.llm.api_key,
            "model": self.config.llm.model_title,
            "temperature": 0.3,  # Lower temperature for more focused titles
        }
        
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

    def terminate_instance(self, instance_id: str) -> bool:
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
                self.terminate_instance(child_id)
        
        # 1. Cancel active requests for this instance
        self._request_registry.cancel_by_instance(instance_id)
        
        # 2. Clean up event bus for this instance
        self._event_bus.cleanup_instance(instance_id)

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

        # 6. Release project lock if JobQueueService is connected
        if self._job_queue_service is not None:
            try:
                released_projects = self._job_queue_service.release_locks_by_instance_sync(instance_id)
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

        return True

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
