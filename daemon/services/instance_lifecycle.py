"""Instance lifecycle service for managing instance creation and termination."""

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from langgraph.graph.state import CompiledStateGraph

from ..cancellation import CancellationReason
from ..compaction import ContextCompactor
from ..registry import get_registry
from ..repositories.instance.models import Instance, InstanceStatus
from ..write_pause_guard import WriteGuardSession
from .cancellation import CancellationService
from .correlation_manager import get_correlation_manager
from .event_publisher import EventPublisherService
from .job_queue_service import DemandState, TERMINAL_CANCEL_STATUSES, TERMINAL_STATUSES
from .project_normalizer import normalize_project_id

if TYPE_CHECKING:
    from ..config import Config
    from ..metadata import AgentMetadata
    from ..repositories.instance.repository import SQLModelInstanceRepository
    from ..repositories.project.repository import SQLModelProjectRepository
    from .job_queue_service import JobQueueService


logger = logging.getLogger(__name__)

# UUID validation pattern (compiled once at module level)
_UUID_PATTERN = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', re.IGNORECASE)


def append_context_key(
    system_prompt: str,
    instance_id: str,
    instance_repository: "SQLModelInstanceRepository",
    parent_id: Optional[str] = None,
) -> str:
    """Append the CONTEXT_KEY (root parent instance ID) to a system prompt.

    Args:
        system_prompt: The base system prompt to append to.
        instance_id: The instance ID to find the root for.
        instance_repository: Repository for tree operations.
        parent_id: Optional parent instance ID. If provided, finds root via parent.

    Returns:
        The system prompt with CONTEXT_KEY section appended.
    """
    # Determine root_id based on whether this is a root or child instance
    if parent_id is None:
        # This IS a root instance
        root_id = instance_id
    else:
        # This is a child instance - find root via parent (which exists in DB)
        root_id = instance_repository.get_tree_root_id(parent_id)
        if root_id is None:
            root_id = parent_id  # Fallback to parent_id if not found

    # Resolve ensemble shared context placeholders
    system_prompt = system_prompt.replace("{{ENSEMBLE_CONTEXT_KEY}}", root_id)

    context_section = f"\n---\n\n## Context Key\n\nCONTEXT_KEY: {root_id}\n"
    return system_prompt + context_section


def append_current_time(system_prompt: str, now: datetime | None = None) -> str:
    """Append current time information to a system prompt.

    Args:
        system_prompt: The base system prompt to append to.
        now: Optional datetime to use (defaults to current UTC time).
            Provide a fixed value for deterministic tests.

    Returns:
        The system prompt with a Current Time section appended.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    iso_time = now.isoformat()
    weekday = now.strftime("%A")
    human_time = now.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
    time_section = (
        f"\n---\n\n## Current Time\n\n"
        f"ISO: {iso_time}\n"
        f"Human: {weekday}, {human_time}\n"
        f"Use the `time` tool for fresh time information when needed."
    )
    return system_prompt + time_section


class InstanceLifecycleService:
    """Service for managing instance lifecycle (spawn, terminate, restore).
    
    Handles:
    - Instance creation (spawn_instance)
    - Instance termination (terminate_instance)
    - Instance lookup (get_instance, get_instance_info)
    - Instance listing (list_instances)
    - Instance clearing (clear_all_instances)
    """

    def __init__(
        self,
        manager: "InstanceManager",
        cancellation_service: "CancellationService",
        events_service: "EventPublisherService | None" = None,
        job_queue_service: "JobQueueService | None" = None,
    ):
        """Initialize the lifecycle service.
        
        Args:
            manager: The InstanceManager facade.
            cancellation_service: Service for cancellation handling.
            events_service: Service for lifecycle event publishing.
            job_queue_service: Optional job queue service for lock management.
        """
        self._manager = manager
        self._cancellation_service = cancellation_service
        self._events_service = events_service
        self._job_queue_service = job_queue_service

    @property
    def _config(self) -> "Config":
        """Access config through manager for test mockability."""
        return self._manager.config

    @property
    def _compactor(self) -> "ContextCompactor | None":
        """Access compactor through manager for test mockability."""
        return self._manager._compactor

    @property
    def _checkpointer(self) -> "Any | None":
        """Access the underlying LangGraph checkpointer (saver) through manager.

        Phase 2 migration: the manager now stores a ``CheckpointerAdapter``;
        services that need the raw saver (passed to ``build_instance_graph``
        as ``checkpointer=...``) reach it via ``raw_saver``.

        Returns ``None`` if the checkpointer has not been initialized yet.
        """
        adapter = self._manager._checkpointer
        return adapter.raw_saver if adapter is not None else None

    def _get_mcp_tool_names(
        self,
        instance_id: str | None = None,
        stored_mcp_tool_names: list[str] | None = None,
    ) -> list[str]:
        """Get MCP tool names for prompt generation.
        
        This extracts tool names from the MCP service without creating the actual
        tool objects. The names are needed for the system prompt to include MCP
        tools in the tool documentation.
        
        Args:
            instance_id: The instance ID to get cached MCP tools for.
                If None, falls back to stored_mcp_tool_names.
            stored_mcp_tool_names: Fallback list from stored instance_metadata.
                Used when cache is unavailable (e.g., restored instances).
        
        Returns:
            List of MCP tool names, or stored_mcp_tool_names if cache miss,
            or empty list if neither available.
        """
        if instance_id is not None:
            try:
                if hasattr(self._manager, '_mcp_service') and self._manager._mcp_service:
                    # Get MCP tools from the service using the instance_id cache
                    mcp_tools = self._manager._mcp_service.get_mcp_tools(instance_id)
                    if mcp_tools:
                        # Extract names
                        return [
                            getattr(t, 'name', None) or getattr(getattr(t, 'func', None), '__name__', None)
                            for t in mcp_tools
                        ] or []
            except Exception as e:
                logger.debug(f"Failed to get MCP tool names from cache: {e}")
        # Fall back to stored metadata (for restored instances where cache may be empty)
        if stored_mcp_tool_names:
            return stored_mcp_tool_names
        return []

    def _build_llm_config(self, metadata: "AgentMetadata | None" = None) -> dict:
        """Build LLM config dict with optional per-agent model override."""
        llm_config = {
            "base_url": self._config.llm.base_url,
            "api_key": self._config.llm.api_key,
            "model": self._config.llm.model,
            "model_vision": self._config.llm.model_vision,
            "temperature": self._config.llm.temperature,
            "request_timeout": self._config.llm.request_timeout,
        }
        if metadata and metadata.llm_model and metadata.llm_model.strip():
            llm_config["model"] = metadata.llm_model.strip()
        return llm_config

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
            project_id: Optional project ID for project context.
            instance_name: Optional short name for the instance.
            invoked_as_tool: If True, marks instance as invoked-as-tool (default: False).

        Returns:
            The instance_id of the newly created instance.

        Raises:
            ValueError: If max_children_per_instance limit is exceeded,
                or if agent_id is not found.
        """
        # Normalize project_id: accept "null"/"none"/""/None as system default
        if project_id is not None:
            project_id = normalize_project_id(project_id)

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

        # Access manager's state dynamically
        instance_repository = self._manager._instance_repository
        project_repository = self._manager._project_repository
        prompt_cache = self._manager.prompt_cache
        
        # Check max_children_per_instance limit if parent_id is provided (root instances skip the check)
        # Use truthy check to handle both None and empty string cases
        if parent_id:
            child_count = instance_repository.count_children(parent_id)
            if child_count >= self._config.limits.max_children_per_instance:
                raise ValueError(
                    f"Max children limit reached for parent {parent_id}: "
                    f"{self._config.limits.max_children_per_instance}"
                )

        # Load MCP tool names for prompt generation (needed before creating tools)
        # This gets the tool names from the MCP service cache (pre-loaded by spawn_instance_with_mcp)
        mcp_tool_names = self._get_mcp_tool_names(instance_id)
        
        # Load and cache prompt using resolved path (pass MCP tool names for category expansion)
        # Import from manager to pick up test patches
        from ..manager import load_and_cache_prompt
        agent_path = Path(resolved_agent_dir)
        system_prompt, token_count = load_and_cache_prompt(resolved_agent_id, agent_path, prompt_cache, mcp_tool_names)

        # Append CONTEXT_KEY (root parent instance ID) to system prompt
        system_prompt = append_context_key(system_prompt, instance_id, instance_repository, parent_id=parent_id)

        # Append current time so the agent has temporal context for the conversation
        system_prompt = append_current_time(system_prompt)

        # Create tools with this manager reference
        # Import from manager to pick up test patches
        from ..manager import create_instance_tools
        tools = create_instance_tools(self._manager, instance_id, resolved_agent_id)

        # Build LLM config
        llm_config = self._build_llm_config(metadata)

        # Build retry config from queue settings
        retry_config = {
            "transient_attempts": self._config.queue.llm_retry_transient_attempts,
            "timeout_attempts": self._config.queue.llm_retry_timeout_attempts,
        }

        # Build graph config with thread_id for state management
        config = {
            "configurable": {"thread_id": instance_id},
            "recursion_limit": self._config.limits.graph_recursion_limit,
        }

        # Build graph with checkpointer
        # Import from manager to pick up test patches
        from ..manager import build_instance_graph
        graph = build_instance_graph(
            tools=tools,
            checkpointer=self._checkpointer,
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
            project = project_repository.get(project_id)
            if project is None:
                raise ValueError(
                    f"Project '{project_id}' not found. "
                    f"Use None if no project context is needed."
                )
            instance_metadata["project_id"] = project_id
        
        # Store instance_name in metadata if provided
        if instance_name is not None:
            instance_metadata["instance_name"] = instance_name
        
        # Mark as invoked-as-tool if requested
        if invoked_as_tool:
            instance_metadata["invoked_as_tool"] = True
        
        # Store MCP tool names for cache key consistency
        if mcp_tool_names:
            instance_metadata["mcp_tool_names"] = mcp_tool_names
        
        logger.info(f"Spawning instance {instance_id} (agent={resolved_agent_id}, parent={parent_id}, name={instance_name})")
        
        # Create instance in DB
        instance_repository.create(
            instance_id=instance_id,
            agent_id=resolved_agent_id,
            agent_dir=resolved_agent_dir,
            parent_id=parent_id,
            metadata=instance_metadata if instance_metadata else None,
            project_id=project_id,
        )
        
        # Verify instance was created in DB
        created = instance_repository.get(instance_id)
        if created is None:
            logger.error(f"CRITICAL: Instance {instance_id} was NOT persisted to database after create() call!")
        else:
            logger.info(f"Instance {instance_id} created in DB with status={created.status}, parent_id={created.parent_id}")
        
        # Inherit original_source from parent if parent has one (C2: source inheritance during spawn)
        # This ensures grandchildren also get the original telegram source
        if parent_id:
            parent_meta = instance_repository.get(parent_id)
            if parent_meta is not None and parent_meta.instance_metadata is not None:
                parent_original_source = parent_meta.instance_metadata.get("original_source")
                if parent_original_source:
                    instance_repository.set_metadata(instance_id, "original_source", parent_original_source)
                    logger.info(f"Inherited original_source '{parent_original_source}' from parent {parent_id[:8]}...")
        
        # Update parent's children list and waiting_for counter
        if parent_id:
            from sqlmodel import Session
            with WriteGuardSession(Session(self._manager.engine), self._manager.write_guard) as session:
                parent = session.get(Instance, parent_id)
                if parent:
                    # Add child to parent's denormalized children list
                    children_list = json.loads(parent.children) if parent.children else []
                    if instance_id not in children_list:
                        children_list.append(instance_id)
                        parent.children = json.dumps(children_list)
                        logger.info(f"Added child {instance_id} to parent's children list")
                    # NOTE: waiting_for is NOT incremented here
                    # Only send_message to a child increments waiting_for
                    # This ensures waiting_for accurately tracks pending work, not just child existence
                    session.commit()
                    logger.info(f"Parent {parent_id} updated: children={children_list}")
                else:
                    logger.warning(f"Parent {parent_id} not found in DB when updating children list for child {instance_id}")
        
        # Store in instances dict
        self._manager.instances[instance_id] = (graph, resolved_agent_dir)

        # Emit status_change event for idle status (fire-and-forget)
        # Use MainLoopBridge.run_async_no_wait to handle thread context safely
        # (sync tools run via run_in_executor which doesn't have an event loop)
        from .main_loop_bridge import MainLoopBridge
        MainLoopBridge.run_async_no_wait(
            self._manager._live_hub.stream_status_change(instance_id, "idle", agent_id=resolved_agent_id)
        )

        # Emit instance_created event:
        # - To parent's stream (if parent exists)
        # - To NotificationBroadcaster (if root-level, no parent)
        instance_data = {
            "instance_id": instance_id,
            "agent_id": resolved_agent_id,
            "parent_id": parent_id,
            "status": "idle",
            "project_id": project_id,
            "created_at": created.created_at if created else None,
            "children": [],
            "title": None,
        }
        if parent_id:
            # Emit to parent's SSE stream
            MainLoopBridge.run_async_no_wait(
                self._manager._live_hub.stream_instance_created(parent_id, instance_data)
            )
        else:
            # Emit to global notification stream for root-level instances
            # (only if NotificationBroadcaster is initialized)
            broadcaster = getattr(self._manager, '_notification_broadcaster', None)
            if broadcaster is not None:
                MainLoopBridge.run_async_no_wait(
                    broadcaster.emit_instance_created(instance_data)
                )

        return instance_id

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
        t0 = time.monotonic()

        # Get instance metadata BEFORE modifying state (needed for children cascade)
        meta = None
        if hasattr(self._manager, '_instance_repository') and self._manager._instance_repository:
            meta = self._manager._instance_repository.get(instance_id)

        # Re-entrancy guard: if already terminated, return early
        if meta and meta.status == InstanceStatus.TERMINATED.value:
            logger.info(f"Instance {instance_id[:8]}... already terminated, skipping")
            return True

        # Cascade to children FIRST - terminate all child instances in parallel.
        # (Parallel because each child may itself unwind an in-flight LLM call;
        # serial cascade would compound to 5s*N worst case.)
        child_ids: list[str] = list(meta.children) if meta and meta.children else []
        if child_ids:
            results = await asyncio.gather(
                *(self.terminate_instance(cid) for cid in child_ids),
                return_exceptions=True,
            )
            # Cascade logs emitted AFTER gather completes (reviewer S2), so the
            # timestamp reflects the actual unwind time, not the dispatch time.
            for cid, result in zip(child_ids, results):
                if isinstance(result, Exception):
                    # Reviewer S1: warn on child termination failures
                    logger.warning(
                        f"Failed to cascade-terminate child instance {cid[:8]}... "
                        f"({type(result).__name__}: {result})"
                    )
                else:
                    logger.info(
                        f"Cascading terminate to child instance: {cid[:8]}... "
                        f"(trigger=DELETE, parent={instance_id[:8]}...)"
                    )
        
        # 1. Cancel active requests for this instance
        self._manager._request_registry.cancel_by_instance(instance_id)

        # 1.5. Cancel any running graph task for this instance, bounded-await unwind.
        # Bounded wait: graph task unwinds when its in-flight LLM call returns or
        # hits the LLM client's socket timeout. We cap so a stuck LLM call doesn't
        # make DELETE hang; the LLM client's timeout is the real backstop.
        # asyncio.shield protects against outer-cancel (e.g., client disconnect)
        # leaking the unwinding graph task — the very problem this fix is closing.
        graph_task = self._manager._graph_tasks.pop(instance_id, None)
        self._manager.release_context_usage_cache(instance_id)
        graph_unwind_ms = 0
        if graph_task and not graph_task.done():
            graph_task.cancel()
            graph_unwind_start = time.monotonic()
            try:
                await asyncio.wait_for(asyncio.shield(graph_task), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(
                    f"Graph task {instance_id[:8]}... did not unwind within 5s; "
                    f"relying on LLM socket timeout to free resources"
                )
            except asyncio.CancelledError:
                logger.debug(f"Graph task {instance_id[:8]}... cancelled during await")
            graph_unwind_ms = int((time.monotonic() - graph_unwind_start) * 1000)
            logger.info(
                f"Cancelled graph task for instance {instance_id[:8]}... "
                f"(unwind_ms={graph_unwind_ms})"
            )

        # 2. Clean up live hub connections for this instance
        await self._manager._live_hub.cleanup_instance(instance_id)

        # 2.5. Close MCP connections for this instance
        if hasattr(self._manager, '_mcp_service') and self._manager._mcp_service:
            try:
                await self._manager._mcp_service.close_connections(instance_id)
            except Exception as e:
                logger.warning(f"MCP cleanup failed for {instance_id[:8]}: {e}")

        # 3. Remove from instances dict
        if instance_id in self._manager.instances:
            del self._manager.instances[instance_id]
        else:
            # Instance not in memory but might still need cleanup (children cascade)
            if meta is None:
                return False

        # 3.5. Clean up job watches for this instance
        if hasattr(self._manager, '_watcher_repo') and self._manager._watcher_repo:
            try:
                removed = self._manager._watcher_repo.remove_all_watches_for_instance(instance_id)
                if removed > 0:
                    logger.info(f"Removed {removed} job watch(es) for terminated instance {instance_id[:8]}...")
            except Exception as e:
                logger.warning(f"Failed to cleanup watches for instance {instance_id[:8]}...: {e}")

        # 5. Update DB status to terminated using repository.
        # Reset waiting_for to 0 to prevent counter divergence on revive.
        # Single atomic write: a crash between status and waiting_for would
        # leave (status=terminated, waiting_for=N>0) and cause rebuild_from_db
        # to over-count on restart.
        if hasattr(self._manager, '_instance_repository') and self._manager._instance_repository:
            self._manager._instance_repository.update(
                instance_id, status="terminated", waiting_for=0
            )

        # 5.5. Emit status_change event
        await self._manager._live_hub.stream_status_change(instance_id, "terminated", agent_id=meta.agent_id if meta else None)

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

        # 7. Mark any associated job as cancelled (no retry)
        if self._job_queue_service is not None:
            try:
                job = self._job_queue_service.get_job_by_instance_sync(instance_id)
                if job is not None and job.status == "processing":
                    self._job_queue_service.complete_job_sync(
                        job.job_id, DemandState.CANCELLED, error="Instance terminated",
                        result_summary=None,
                    )
                    # Trigger next pending job for this project
                    if job.project_id:
                        self._job_queue_service.trigger_next_job_sync(job.project_id)
            except Exception as e:
                logger.warning(f"Failed to mark job as failed on terminate: {e}")

        # Track jobs cancelled in steps 7.5 and 7.6 for the summary log
        jobs_cancelled = 0

        # 7.5. Cancel ALL MESSAGE jobs for this instance
        if self._job_queue_service is not None:
            try:
                message_jobs = self._job_queue_service._repository.find_jobs_by_instance(
                    instance_id, job_type="message"
                )
                for msg_job in message_jobs:
                    await self._job_queue_service.cancel_message_job(msg_job.job_id)
                jobs_cancelled += len(message_jobs)
            except Exception as e:
                logger.warning(f"Failed to cancel MESSAGE jobs on terminate: {e}")

        # 7.6. Cancel ALL remaining jobs for this instance (comprehensive sweep)
        if self._job_queue_service is not None:
            try:
                # Find ALL jobs still associated with this instance (any type, any non-terminal state)
                all_jobs = self._job_queue_service._repository.find_jobs_by_instance(
                    instance_id, job_type=None  # All types
                )
                for remaining_job in all_jobs:
                    # Skip already-terminal states
                    if remaining_job.status in ("completed", "cancelled", "dead_letter"):
                        continue
                    logger.info(
                        f"terminate_instance: cancelling remaining job {remaining_job.job_id[:8]}... "
                        f"(type={remaining_job.job_type}, status={remaining_job.status}) "
                        f"for instance {instance_id[:8]}..."
                    )
                    try:
                        if remaining_job.status == "processing":
                            # Use complete_job() to avoid re-entrancy — cancel_job() on PROCESSING
                            # jobs may trigger terminate_instance() again via _is_instance_alive check
                            await self._job_queue_service.complete_job(
                                remaining_job.job_id,
                                demand_state=DemandState.CANCELLED,
                                error="Instance terminated during cleanup",
                            )
                        else:
                            # PENDING, FAILED — safe to use cancel_job()
                            await self._job_queue_service.cancel_job(remaining_job.job_id)
                        jobs_cancelled += 1
                    except Exception as e:
                        logger.warning(
                            f"terminate_instance: failed to cancel job {remaining_job.job_id[:8]}...: {e}"
                        )
            except Exception as e:
                logger.warning(f"Failed to cleanup remaining jobs for instance {instance_id[:8]}...: {e}")

        # 9. Wake the JobProcessor so it can sweep TERMINATED-instance artifacts
        # immediately rather than waiting up to 30s for the next poll boundary.
        # Safe to call even if the DB writes haven't fully settled — early wakeup
        # is benign (JobProcessor's orphan-check will just see RUNNING and skip,
        # then catch TERMINATED on its next pass).
        # Attribute path: manager → _job_queue_mgmt_service → _dispatch_bus.
        # Set at daemon/api.py:210 (direct assignment, not via setter).
        # NOT self._manager._dispatch_bus — InstanceManager has no such attribute.
        mgmt = getattr(self._manager, '_job_queue_mgmt_service', None)
        bus = getattr(mgmt, '_dispatch_bus', None) if mgmt is not None else None
        if bus is not None:
            try:
                bus.notify_all()
            except Exception as e:
                logger.warning(
                    f"Failed to notify dispatch bus during terminate of {instance_id[:8]}... "
                    f"({type(e).__name__}: {e})"
                )

        # 7.7. Clean up MessageQueue entries for this instance
        if hasattr(self._manager, '_queue_repository') and self._manager._queue_repository:
            try:
                count = self._manager._queue_repository.delete_by_instance(instance_id)
                logger.debug(f"[TRACE] terminate_instance: removed {count} MessageQueue entries for instance {instance_id[:8]}...")
            except Exception as e:
                logger.warning(f"Failed to cleanup MessageQueue entries for instance {instance_id[:8]}...: {e}")

        # 7.8. Clear CorrelationManager state for the terminated instance.
        # Without this, a terminated-and-revived instance would inherit its
        # previous _pending[parent_id] entry — is_complete() would never
        # return True again until daemon restart, wedging the parent
        # permanently. CM cleanup is defensive: a CM failure must NOT fail
        # termination (legacy waiting_for cascade is the graceful-degradation
        # fallback).
        cm = get_correlation_manager()
        if cm is not None:
            try:
                await cm.clear_for_instance(instance_id)
            except Exception as e:
                logger.warning(
                    f"Failed to clear CM state for terminated instance "
                    f"{instance_id[:8]}...: {e}"
                )

        # 8. Publish lifecycle event for terminated instance
        parent_id = meta.parent_id if meta else None
        if self._events_service:
            await self._events_service._publish_instance_lifecycle_event(
                instance_id=instance_id,
                status="terminated",
                error=None,
                parent_id=parent_id,
            )

        # Summary log: surface total duration and unwind cost in one line so the
        # next latency regression is self-explanatory. Matches the [TRACE] style
        # used in daemon/services/job_processor.py and daemon/services/instance_lifecycle.py.
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            f"[TRACE] terminate_instance: {instance_id[:8]}... complete "
            f"(graph_unwind_ms={graph_unwind_ms}, jobs_cancelled={jobs_cancelled}, "
            f"children={len(child_ids)}, duration_ms={duration_ms})"
        )

        return True

    async def pause_instance_cascade(self, instance_id: str) -> dict:
        """Pause an instance and cascade to all children (soft pause).

        Uses tree traversal helpers to find and pause the entire tree.
        Cancels active requests and sets status to paused (resumable).
        Does NOT remove instances from memory or release locks.

        Args:
            instance_id: The ID of the instance to pause.

        Returns dict with:
          - paused_ids: list of all instance IDs that were paused
          - skipped_ids: list of instance IDs that were already paused (skipped)
        """
        repo = self._manager._instance_repository

        # 1. Find root of the tree
        root_id = repo.get_tree_root_id(instance_id)
        if root_id is None:
            # Fall back to instance_id itself if not found
            root_id = instance_id

        # 2. Get ALL node IDs in the tree
        tree_ids = repo.get_tree_ids(root_id)
        if not tree_ids:
            logger.warning(f"No tree found for instance {instance_id[:8]}...")
            return {"paused_ids": [], "skipped_ids": [instance_id]}

        paused_ids: list[str] = []
        skipped_ids: list[str] = []

        # Helper function to pause a single instance (non-recursive)
        def _pause_single(target_id: str, prefetched_meta: Instance | None = None) -> bool:
            """Pause a single instance. Returns True if paused, False if skipped.

            Args:
                target_id: The ID of the instance to pause.
                prefetched_meta: Pre-fetched metadata (avoids redundant DB lookup).
            """
            meta = prefetched_meta or repo.get(target_id)

            if meta is None:
                logger.warning(f"Instance {target_id[:8]}... not found in DB, skipping pause")
                return False

            # Skip if already paused
            if meta.status == InstanceStatus.PAUSED.value:
                logger.info(f"Instance {target_id[:8]}... is already paused, skipping")
                return False

            # 1. Cancel active LLM requests (via cancellation callbacks)
            self._manager._request_registry.cancel_by_instance(
                target_id, CancellationReason.USER_STOPPED
            )

            # 2. Cancel the running graph task (interrupts astream/ainvoke loop)
            # This raises asyncio.CancelledError in the streaming coroutine
            # Use pop() to prevent stale references after cancellation (consistent with terminate_instance)
            graph_task = self._manager._graph_tasks.pop(target_id, None)
            self._manager.release_context_usage_cache(target_id)
            if graph_task and not graph_task.done():
                graph_task.cancel()
                logger.info(f"Cancelled graph task for instance {target_id[:8]}...")

            # 3. Update DB status to paused
            # Reset waiting_for to 0 if instance was waiting for children
            # to prevent deadlock on resume (children are paused too).
            #
            # Phase 4: the pending-children decision now consults the
            # CorrelationManager (authoritative in-memory pending set) when
            # available. ``waiting_for`` is the rebuild cache (ADR-011) and
            # the graceful-degradation fallback. Resetting the cache to 0 on
            # pause is still required for crash recovery consistency — the
            # CM is cleared on daemon restart, so the cache must reflect a
            # safe "no pending children" state until resume re-registers them.
            paused_at = datetime.now(timezone.utc).isoformat()
            cm = get_correlation_manager()
            if cm is not None:
                has_pending_children = cm.get_pending_count(target_id) > 0
            else:
                has_pending_children = bool(
                    getattr(meta, "waiting_for", None) and meta.waiting_for > 0
                )
            if has_pending_children:
                # Pause carve-out (ADR-011): the ``waiting_for=0`` write below
                # looks contradictory (instance DOES have pending children),
                # but it is the documented Phase 4 carve-out. The CorrelationManager
                # is the authoritative source of pending children; the
                # ``waiting_for`` column is a REBUILD-ONLY cache for crash
                # recovery, never read for control flow. Children are also being
                # paused in the cascade above, so no new completions can arrive
                # to decrement it during the paused window. On resume, the
                # child instances re-register with the CM, so the count is
                # re-derived authoritatively from CM — not from the cached
                # ``waiting_for`` value. Resetting the cache to 0 here keeps
                # the DB consistent with the "no completions possible right
                # now" state during the paused window.
                repo.update(
                    target_id,
                    status=InstanceStatus.PAUSED.value,
                    waiting_for=0,
                    paused_at=paused_at,
                )
            else:
                repo.update(
                    target_id,
                    status=InstanceStatus.PAUSED.value,
                    paused_at=paused_at,
                )

            # NOTE: Unlike terminate_instance, we do NOT:
            # - Remove from instances dict (instance stays in memory, resumable)
            # - Release project locks (job continues)
            # - Mark jobs as cancelled
            # - Clean up live hub connections

            logger.info(f"Paused instance {target_id[:8]}...")
            return True

        # 3. Iterate over all nodes in the tree and pause each one
        for node_id in tree_ids:
            try:
                meta = repo.get(node_id)
                if _pause_single(node_id, prefetched_meta=meta):
                    paused_ids.append(node_id)
                    # Emit status_change event for paused status
                    await self._manager._live_hub.stream_status_change(
                        node_id, InstanceStatus.PAUSED.value, agent_id=meta.agent_id if meta else None
                    )
                else:
                    skipped_ids.append(node_id)
            except Exception as e:
                logger.error(f"Failed to pause node {node_id[:8]}...: {e}")
                skipped_ids.append(node_id)

        return {"paused_ids": paused_ids, "skipped_ids": skipped_ids}

    async def resume_instance_cascade(self, instance_id: str) -> dict:
        """Resume an instance and cascade to all children.

        Uses tree traversal helpers to find and resume the entire tree.
        Sets status to RUNNING and clears paused_at.
        Does NOT re-spawn or restart instances - just unpauses them.

        Args:
            instance_id: The ID of the instance to resume.

        Returns dict with:
          - resumed_ids: list of all instance IDs that were resumed
          - skipped_ids: list of instance IDs that were skipped (not paused)
          - target_id: the instance_id that was passed to this method
        """
        repo = self._manager._instance_repository

        # 1. Find root of the tree
        root_id = repo.get_tree_root_id(instance_id)
        if root_id is None:
            # Fall back to instance_id itself if not found
            root_id = instance_id

        # 2. Get ALL node IDs in the tree
        tree_ids = repo.get_tree_ids(root_id)
        if not tree_ids:
            logger.warning(f"No tree found for instance {instance_id[:8]}...")
            return {"resumed_ids": [], "skipped_ids": [instance_id], "target_id": instance_id}

        # 3. Get ancestors of the SELECTED instance (for waiting_for logic)
        ancestor_ids = set(repo.get_ancestor_ids(instance_id))
        is_root_resume = (instance_id == root_id)

        resumed_ids: list[str] = []
        skipped_ids: list[str] = []

        # 4. Iterate over all nodes in the tree and resume each one
        for node_id in tree_ids:
            try:
                meta = repo.get(node_id)

                if meta is None:
                    logger.warning(f"Instance {node_id[:8]}... not found in DB, skipping resume")
                    skipped_ids.append(node_id)
                    continue

                # Skip if not paused (already running or other status)
                if meta.status != InstanceStatus.PAUSED.value:
                    logger.info(f"Instance {node_id[:8]}... is not paused (status={meta.status}), skipping")
                    skipped_ids.append(node_id)
                    continue

                # Determine waiting_for value:
                # - If resuming from root/parent: waiting_for stays 0 for all nodes
                # - If resuming from child: only ANCESTORS get waiting_for = 1
                #
                # Phase 4: this is a WRITE — the rebuild cache (ADR-011) is
                # being re-initialized on resume. The CM is re-populated by
                # the registration paths elsewhere (not here); this sets the
                # initial DB cache so ``rebuild_from_db()`` can recover the
                # parent/child relationship after a restart. Intentionally
                # retained — DO NOT route through the CM API.
                if is_root_resume:
                    waiting_for_value = 0
                else:
                    # Only ancestors get waiting_for = 1, others stay at 0
                    waiting_for_value = 1 if node_id in ancestor_ids else 0

                # Update DB status to running and clear paused_at
                repo.update(
                    node_id,
                    status=InstanceStatus.RUNNING.value,
                    paused_at=None,  # Clear paused_at on resume
                    waiting_for=waiting_for_value,
                )
                logger.info(f"Resumed instance {node_id[:8]}... (waiting_for={waiting_for_value})")
                resumed_ids.append(node_id)

                # Emit status_change event for running status
                await self._manager._live_hub.stream_status_change(
                    node_id, InstanceStatus.RUNNING.value, agent_id=meta.agent_id
                )
            except Exception as e:
                logger.error(f"Failed to resume node {node_id[:8]}...: {e}")
                skipped_ids.append(node_id)

        return {"resumed_ids": resumed_ids, "skipped_ids": skipped_ids, "target_id": instance_id}

    async def get_instance(self, instance_id: str) -> CompiledStateGraph:
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
        # Check in-memory cache first (sync, fast path)
        if instance_id in self._manager.instances:
            graph, _ = self._manager.instances[instance_id]
            return graph

        # Cold-load: ensure MCP tools are preloaded BEFORE restoring
        await self._manager.ensure_mcp_preloaded(instance_id)

        # Now restore from DB
        instance_repository = self._manager._instance_repository
        meta = instance_repository.get(instance_id)
        if meta is None:
            raise KeyError(f"Instance not found: {instance_id}")

        return self._restore_instance(instance_id, meta)

    def _restore_instance(self, instance_id: str, meta: Instance) -> CompiledStateGraph:
        """Restore an instance from database into memory.

        Rebuilds the graph with the same instance_id. The checkpointer will
        restore conversation state from LangGraph's checkpoint tables.

        Args:
            instance_id: The ID of the instance to restore.
            meta: Instance metadata from database.

        Returns:
            The restored CompiledStateGraph instance.
        """
        # Access manager's state dynamically for test compatibility
        instance_repository = self._manager._instance_repository
        project_repository = self._manager._project_repository
        prompt_cache = self._manager.prompt_cache
        
        # Load MCP tool names for prompt generation (prefer cache, fallback to stored)
        stored_mcp = meta.instance_metadata.get("mcp_tool_names") if meta.instance_metadata else None
        mcp_tool_names = self._get_mcp_tool_names(instance_id, stored_mcp)
        
        # Load and cache prompt using resolved path (pass MCP tool names for category expansion)
        # Import from manager to pick up test patches
        from ..manager import load_and_cache_prompt
        agent_path = Path(meta.agent_dir)
        system_prompt, token_count = load_and_cache_prompt(meta.agent_id, agent_path, prompt_cache, mcp_tool_names)

        # Append CONTEXT_KEY (root parent instance ID) to system prompt
        system_prompt = append_context_key(system_prompt, instance_id, instance_repository, parent_id=meta.parent_id)

        # Append current time so the agent has temporal context for the conversation
        system_prompt = append_current_time(system_prompt)

        # Create tools with this manager reference
        # Import from manager to pick up test patches
        from ..manager import create_instance_tools
        tools = create_instance_tools(self._manager, instance_id, meta.agent_id)

        # Build LLM config
        registry = get_registry()
        metadata = registry.get(meta.agent_id)
        if metadata is None:
            raise ValueError(f"Agent not found: {meta.agent_id}")
        llm_config = self._build_llm_config(metadata)

        # Build retry config from queue settings
        retry_config = {
            "transient_attempts": self._config.queue.llm_retry_transient_attempts,
            "timeout_attempts": self._config.queue.llm_retry_timeout_attempts,
        }

        # Build graph config with thread_id for state management
        config = {
            "configurable": {"thread_id": instance_id},
            "recursion_limit": self._config.limits.graph_recursion_limit,
        }

        # Build graph with checkpointer (will restore state from checkpoints)
        # Import from manager to pick up test patches
        from ..manager import build_instance_graph
        graph = build_instance_graph(
            tools=tools,
            checkpointer=self._checkpointer,
            llm_config=llm_config,
            system_prompt=system_prompt,
            retry_config=retry_config,
            compactor=self._compactor,
            graph_config=config,
        )

        # Store in instances dict
        self._manager.instances[instance_id] = (graph, meta.agent_dir)

        return graph

    def list_instances(
        self,
        limit: int = 10,
        offset: int = 0,
        project_id: str | None = None,
        exclude_kb: bool = True,
        include_descendants: bool = False,
    ) -> tuple[list[dict], int]:
        """List instances with pagination.

        When ``include_descendants`` is True, pagination is root-based: only root
        instances (parent_id IS NULL or empty) are counted and paginated, and
        ALL descendants of each root in the current page are loaded via BFS and
        included in the flat result list.

        When ``include_descendants`` is False (default), returns a flat paginated
        list of all matching instances.

        Args:
            limit: Maximum number of root instances to return (default: 10).
                When ``include_descendants=False``, this is the page size of all
                matching instances.
            offset: Number of root instances to skip (default: 0).
            project_id: Filter by project ID (default: None, returns all projects).
            exclude_kb: Exclude KB-related instances (experiencer, kb-importer)
                when True (default: True).
            include_descendants: When True, paginate by root and BFS-load all
                descendants of each root in the current page (default: False).

        Returns:
            Tuple of (list of instance info dictionaries, total count).
        """
        # Access manager's state dynamically
        instance_repository = self._manager._instance_repository

        instances, total = instance_repository.list(
            limit=limit,
            offset=offset,
            project_id=project_id,
            exclude_kb=exclude_kb,
            include_descendants=include_descendants,
        )
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
        # Access manager's state dynamically
        instance_repository = self._manager._instance_repository
        
        meta = instance_repository.get(instance_id)
        if meta is None:
            raise KeyError(f"Instance not found: {instance_id}")
        return meta.to_dict()

    def clear_all_instances(self) -> int:
        """Clear all instances from memory and database.

        Returns:
            Number of instances deleted from database.
        """
        # Clear in-memory instances
        self._manager.instances.clear()

        # Clear database instances
        return self._manager._instance_repository.delete_all()
