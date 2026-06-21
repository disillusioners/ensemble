"""Instance lifecycle service for managing instance creation and termination."""

import asyncio
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Optional

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import bindparam, select, text
from sqlmodel import Session

from ..cancellation import CancellationReason
from ..compaction import ContextCompactor
from ..registry import get_registry
from ..repositories.instance.models import Instance, InstanceHierarchy, InstanceStatus
from ..write_pause_guard import WriteGuardSession
from .cancellation import CancellationService
from .correlation_manager import get_correlation_manager
from .dependency_bus import get_dependency_bus
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


def _is_dependency_bus_enabled(manager: "InstanceManager") -> bool:
    """Read the ``use_dependency_bus`` flag from the manager config.

    Module-level helper (not a method) so the gated call sites in
    :meth:`InstanceLifecycleService.pause_instance_cascade` and
    :meth:`InstanceLifecycleService.terminate_instance` can read the
    flag without depending on the lifecycle service being constructed
    (mirrors the module-level helper in
    ``daemon/services/error_reporting.py``). Defensive ``getattr``
    chain so test mocks that bypass ``InstanceManager.__init__``
    (e.g. ``MagicMock()`` without explicit ``config``) don't crash.
    Default is False (Phase D feature flag OFF = legacy CM path is
    active), matching the config field's default.

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


async def _cancel_bus_watchers_for(manager: "InstanceManager", instance_id: str, op: str) -> None:
    """Cancel PENDING DependencyBus watchers targeting ``instance_id``.

    Called from :meth:`InstanceLifecycleService.pause_instance_cascade`
    and :meth:`InstanceLifecycleService.terminate_instance` after the
    DB status transition has committed. Cancels PENDING watchers so
    an in-flight child task does not deliver a FollowUp onto a
    paused/terminated parent. No-op when the flag is OFF or the bus
    singleton is missing (graceful degradation — the CM path is the
    authoritative completion mechanism in that case).

    Args:
        manager: The InstanceManager facade (used to read the flag).
        instance_id: The parent instance ID whose watchers should be
            cancelled.
        op: One of ``"pause"`` / ``"terminate"`` — used in the log
            line for traceability.
    """
    if not _is_dependency_bus_enabled(manager):
        return
    bus = get_dependency_bus()
    if bus is None:
        logger.debug(
            f"instance_lifecycle.{op}: use_dependency_bus=ON but bus "
            f"singleton is None — skipping cancel_for_target "
            f"(target={instance_id[:8]}...)"
        )
        return
    try:
        cancelled = await bus.cancel_for_target(instance_id)
        if cancelled > 0:
            logger.info(
                f"instance_lifecycle.{op}: cancelled {cancelled} "
                f"dependency watcher(s) for {instance_id[:8]}..."
            )
    except Exception as e:
        logger.warning(
            f"instance_lifecycle.{op}: bus.cancel_for_target failed "
            f"for {instance_id[:8]}... ({type(e).__name__}: {e})"
        )


# ── Outbox NamedTuples (WriteGuardSession extraction) ──────────────────────
# The sync ``_*_db_sync`` helpers return these so the async callers can fire
# post-commit side effects (SSE / CompletionRegistry / lifecycle event / CM
# resolve hook / job-processor notify) on the event loop AFTER commit.
# Keeping all data needed for side effects in the NamedTuple prevents the
# "NameError after extraction" regression documented in H10.

class _TerminateResult(NamedTuple):
    """Outbox payload from ``_terminate_instance_db_sync`` (H10 fix).

    Carries everything the async caller needs to fire post-commit side
    effects for ``terminate_instance``:

      * ``skip`` — True means no row was updated (already terminal or
        missing). Caller short-circuits without firing side effects.
      * ``parent_id`` / ``agent_id`` — captured from the instance row
        before commit (instance is detached after commit).
      * ``message_jobs_cancelled`` / ``all_jobs_cancelled`` — counters
        for the [TRACE] summary log so the line matches the pre-fix
        shape (job_queue sweep results land in the same call site).
      * ``message_queue_removed`` — count of MessageQueue rows deleted
        for the [TRACE] summary log.

    The H10 fix consolidates the 10+ transaction writes into a single
    ``WriteGuardSession`` (status / waiting_for / job cancel /
    MessageQueue delete) so a crash mid-cascade cannot orphan jobs or
    leave zombie state.
    """

    skip: bool
    parent_id: str | None
    agent_id: str | None
    message_jobs_cancelled: int
    all_jobs_cancelled: int
    message_queue_removed: int


class _SpawnResult(NamedTuple):
    """Outbox payload from ``_spawn_instance_db_sync`` (M8 fix).

    ``created_at`` is captured from the row before commit so the async
    caller can include it in the ``stream_instance_created`` SSE event
    (the instance is detached after the session closes).
    """

    created: bool
    parent_id: str | None
    agent_id: str | None
    project_id: str | None
    created_at: str | None
    inherited_source: bool  # True if we set ``original_source`` from parent


class _CascadeUpdateResult(NamedTuple):
    """Outbox payload from ``_pause_cascade_db_sync`` / ``_resume_cascade_db_sync`` (L14).

    Carries the resolved per-instance metadata so the async caller can
    decide whether to emit a ``status_change`` SSE event and which
    ``agent_id`` to attach (the instance is detached after commit).

    L14 collapses N+1 per-tree-node UPDATEs into ONE ``UPDATE ... WHERE
    instance_id IN (...)`` statement, eliminating the crash window where
    half the tree was paused/resumed and the other half was still in
    the pre-cascade status.
    """

    updated_ids: list[str]        # IDs that were updated (skipped excluded)
    skipped_ids: list[str]        # IDs that were already in target status
    agent_ids_by_instance: dict[str, str | None]
    waiting_for_by_instance: dict[str, int]

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

        # M8 fix: child creation + parent source inheritance + initial
        # ``created_at`` capture all run inside ONE ``WriteGuardSession``
        # transaction. The pre-fix implementation called three separate
        # repository methods (create / get-parent / set_metadata), each
        # with its own session — a crash between the parent get and the
        # ``set_metadata`` left the child visible without its inherited
        # ``original_source`` (the audit inconsistency flagged in the
        # H10 plan).
        #
        # ``_spawn_instance_db_sync`` returns the captured ``created_at``
        # / ``agent_id`` / ``project_id`` / ``parent_id`` / inheritance
        # flag the async caller (or the sync public method) needs to fire
        # the ``stream_instance_created`` SSE event AFTER the commit.
        # Following the H10 outbox pattern from
        # ``child_reports._ChildCompletionDbResult`` —
        # ``job_feedback_observer._InstanceFinalizeResult``, we never
        # touch the row after the session closes (it's detached post-
        # commit).
        agent_name = ""
        try:
            from ..repositories.instance.repository import get_agent_name as _gan
            agent_name = _gan(resolved_agent_dir)
        except Exception:
            agent_name = resolved_agent_id

        spawn_result = self._spawn_instance_db_sync(
            self._manager.engine,
            self._manager.write_guard,
            instance_id=instance_id,
            resolved_agent_id=resolved_agent_id,
            resolved_agent_dir=resolved_agent_dir,
            agent_name=agent_name,
            parent_id=parent_id,
            project_id=project_id,
            instance_metadata=instance_metadata,
        )

        if spawn_result.inherited_source:
            logger.info(
                f"Inherited original_source from parent {parent_id[:8]}... "
                f"during spawn of {instance_id[:8]}..."
            )

        # NOTE: We no longer mutate ``parent.children`` (JSON cache) here.
        # The ``instance_hierarchy`` junction table is the canonical
        # source of parent-child relationships — _enrich_instance() in
        # daemon/repositories/instance/repository.py loads children
        # from it on every read. Writes to the JSON cache were doubly
        # broken (RMW races + overridden on read) and persistently
        # useless (no code ever reads the corrupted value). See C10.
        #
        # The junction table row is inserted by ``_spawn_instance_db_sync``
        # above. waiting_for is also NOT incremented here — only
        # send_message to a child increments it (that's what makes the
        # count accurate: it tracks pending work, not just child existence).

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
        # Uses ``spawn_result.created_at`` captured BEFORE the session
        # closed (the row is detached after commit; cannot re-read).
        instance_data = {
            "instance_id": instance_id,
            "agent_id": spawn_result.agent_id or resolved_agent_id,
            "parent_id": spawn_result.parent_id,
            "status": "idle",
            "project_id": spawn_result.project_id,
            "created_at": spawn_result.created_at,
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

        H10 fix: the DB write portion (status + waiting_for + job cancel +
        message_queue delete + job_locks release + instance_hierarchy cleanup)
        runs inside a SINGLE ``WriteGuardSession`` transaction via
        ``_terminate_instance_db_sync``, called through ``asyncio.to_thread``
        so ``session.commit()`` cannot wedge the event loop. All post-commit
        side effects (SSE / CompletionRegistry / lifecycle event / CM cleanup
        / dispatch-bus notify / MCP cleanup / project-lock release / watcher
        cleanup) fire AFTER the commit on the event loop.

        Crash safety: a mid-cascade SIGKILL leaves the DB in a consistent
        state — either all the rows are updated/deleted (one transaction) or
        none are (the rollback on session close). Pre-fix, the cascade
        spanned 10+ independent transactions and a crash could orphan jobs,
        leak locks, or leave a half-terminated instance. See H10 in the
        remediation plan.

        Args:
            instance_id: The ID of the instance to terminate.

        Returns:
            True if termination was successful, False if instance was not found.
        """
        t0 = time.monotonic()

        # Get instance metadata BEFORE modifying state (needed for children cascade).
        # This is a sync DB read but is wrapped defensively because the
        # repository may not exist on partial mock setups (tests).
        meta = None
        if hasattr(self._manager, '_instance_repository') and self._manager._instance_repository:
            meta = self._manager._instance_repository.get(instance_id)

        # Re-entrancy guard: if already terminated, return early.
        # NOTE: We re-read the status INSIDE the WriteGuardSession in the sync
        # helper below; this pre-read is the fast-path short-circuit. The
        # helper's own re-check is the authoritative guard for re-entry races.
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

        # ─── Pre-DB side effects (in-memory cleanup) ────────────────────────────
        # These mutate in-memory state only and must run BEFORE the DB commit
        # so the "instance is gone" view is consistent for any observer that
        # races the WriteGuardSession commit.

        # 1. Cancel active requests for this instance.
        self._manager._request_registry.cancel_by_instance(instance_id)

        # 1.5. Cancel any running graph task for this instance, bounded-await
        # unwind. The graph task may take a few seconds to honor cancellation
        # (LLM socket drain) but the daemon must not hang on DELETE.
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

        # 2. Clean up live hub connections for this instance.
        await self._manager._live_hub.cleanup_instance(instance_id)

        # 2.5. Close MCP connections for this instance (async, no DB write).
        if hasattr(self._manager, '_mcp_service') and self._manager._mcp_service:
            try:
                await self._manager._mcp_service.close_connections(instance_id)
            except Exception as e:
                logger.warning(f"MCP cleanup failed for {instance_id[:8]}: {e}")

        # 3. Remove from in-memory instances dict.
        if instance_id in self._manager.instances:
            del self._manager.instances[instance_id]
        else:
            # Instance not in memory but might still need cleanup (children cascade).
            if meta is None:
                return False

        # 3.5. Clean up job watches for this instance (best-effort).
        if hasattr(self._manager, '_watcher_repo') and self._manager._watcher_repo:
            try:
                removed = self._manager._watcher_repo.remove_all_watches_for_instance(instance_id)
                if removed > 0:
                    logger.info(f"Removed {removed} job watch(es) for terminated instance {instance_id[:8]}...")
            except Exception as e:
                logger.warning(f"Failed to cleanup watches for instance {instance_id[:8]}...: {e}")

        # ─── Pre-fetch data needed for the DB write AND post-commit side effects ──
        # H10 design: the sync DB helper runs ``session.commit()`` on a worker
        # thread and returns a ``_TerminateResult`` NamedTuple carrying the
        # captured parent_id / agent_id / counters. Anything the post-commit
        # side effects need (lifecycle event publish, SSE) is captured here
        # BEFORE we hand off to the worker thread — once the session closes,
        # the row is detached and we cannot re-read it.
        #
        # meta may be None for in-memory-only cleanup paths; the helper handles
        # that case with a fresh row read inside its own session.

        # ─── Run the SINGLE-TRANSACTION DB cascade on a worker thread ────────
        # ``asyncio.to_thread`` keeps ``session.commit()`` off the event loop
        # so SQLite WAL write contention cannot deadlock the daemon (mirrors
        # the H15 / _finalize_job pattern in job_feedback_observer.py and the
        # _process_child_completion pattern in child_reports.py).
        db_result = await asyncio.to_thread(
            self._terminate_instance_db_sync,
            self._manager.engine,
            self._manager.write_guard,
            instance_id,
        )

        if db_result.skip:
            # Helper already logged; row was missing or already terminal.
            # Re-entrancy guard re-discovered here — safe to no-op the
            # post-commit side effects.
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                f"[TRACE] terminate_instance: {instance_id[:8]}... skipped "
                f"(row missing or already terminal; graph_unwind_ms={graph_unwind_ms}, "
                f"jobs_cancelled=0, children={len(child_ids)}, duration_ms={duration_ms})"
            )
            return True

        parent_id = db_result.parent_id
        agent_id = db_result.agent_id
        message_jobs_cancelled = db_result.message_jobs_cancelled
        all_jobs_cancelled = db_result.all_jobs_cancelled
        message_queue_removed = db_result.message_queue_removed
        # Total cancelled jobs (message + remaining sweep) for the summary log.
        jobs_cancelled = message_jobs_cancelled + all_jobs_cancelled

        # ─── Post-commit outbox: fire side effects on the event loop ──────────
        # All of these run AFTER the WriteGuardSession committed, so any
        # subscriber (SSE client, watcher, completion consumer) sees a DB
        # state consistent with the side-effect payload.

        # 5.5. Emit status_change SSE event for the terminated instance.
        try:
            await self._manager._live_hub.stream_status_change(
                instance_id, "terminated", agent_id=agent_id
            )
        except Exception as e:
            logger.warning(
                f"terminate_instance: status_change SSE emit failed for "
                f"{instance_id[:8]}...: {e}"
            )

        # 6. Release project lock if JobQueueService is connected.
        if self._job_queue_service is not None:
            try:
                released_projects = await self._job_queue_service.release_lock_by_instance(instance_id)
                if released_projects:
                    logger.info(
                        f"Released {len(released_projects)} project lock(s) for instance "
                        f"{instance_id[:8]}...: {released_projects}"
                    )
            except Exception as e:
                logger.warning(f"Failed to release locks for instance {instance_id[:8]}...: {e}")

        # 7.5/7.6. Cancel remaining MESSAGE and non-PROCESSING jobs.
        # These are best-effort async cancels. The DB cancel for the
        # PROCESSING job is already in the helper; this loop only handles
        # the per-job notify path that the helper did NOT do (the helper
        # bulk-updates job rows but does not call cancel_job per job).
        #
        # Why this is safe AFTER commit: the DB cancel already happened;
        # the only thing this loop does is fire the per-job side effects
        # (notify_watchers etc.). A crash between the helper's commit and
        # this loop leaves the rows terminal but un-notified — recoverable
        # by the next job_processor poll.
        if self._job_queue_service is not None:
            try:
                message_jobs = self._job_queue_service._repository.find_jobs_by_instance(
                    instance_id, job_type="message"
                )
                for msg_job in message_jobs:
                    try:
                        await self._job_queue_service.cancel_message_job(msg_job.job_id)
                    except Exception as e:
                        logger.warning(
                            f"Failed to cancel MESSAGE job {msg_job.job_id[:8]}... "
                            f"on terminate: {e}"
                        )
            except Exception as e:
                logger.warning(f"Failed to enumerate MESSAGE jobs on terminate: {e}")

            try:
                all_jobs = self._job_queue_service._repository.find_jobs_by_instance(
                    instance_id, job_type=None
                )
                for remaining_job in all_jobs:
                    if remaining_job.status in ("completed", "cancelled", "dead_letter"):
                        continue
                    try:
                        if remaining_job.status == "processing":
                            # Defensive: the helper should have already
                            # transitioned PROCESSING jobs to CANCELLED in
                            # the same transaction. complete_job() here is
                            # idempotent — atomic_transition will no-op on
                            # already-terminal rows.
                            await self._job_queue_service.complete_job(
                                remaining_job.job_id,
                                demand_state=DemandState.CANCELLED,
                                error="Instance terminated during cleanup",
                            )
                        else:
                            # PENDING / FAILED — safe to use cancel_job().
                            await self._job_queue_service.cancel_job(remaining_job.job_id)
                    except Exception as e:
                        logger.warning(
                            f"terminate_instance: failed to cancel job "
                            f"{remaining_job.job_id[:8]}...: {e}"
                        )
            except Exception as e:
                logger.warning(f"Failed to cleanup remaining jobs for instance {instance_id[:8]}...: {e}")

            # Trigger the next pending job for the project so the queue
            # doesn't stall (mirrors the original step 7 follow-up).
            try:
                processing_job = self._job_queue_service.get_job_by_instance_sync(instance_id)
                if processing_job and processing_job.project_id:
                    self._job_queue_service.trigger_next_job_sync(processing_job.project_id)
            except Exception as e:
                logger.debug(
                    f"trigger_next_job_sync after terminate of "
                    f"{instance_id[:8]}... failed: {e}"
                )

        # 9. Wake the JobProcessor so it can sweep TERMINATED-instance artifacts
        # immediately rather than waiting up to 30s for the next poll boundary.
        # Safe to call after commit — JobProcessor's orphan-check will see
        # TERMINATED and reclaim resources promptly.
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

        # 8. Publish lifecycle event for terminated instance.
        if self._events_service:
            try:
                await self._events_service._publish_instance_lifecycle_event(
                    instance_id=instance_id,
                    status="terminated",
                    error=None,
                    parent_id=parent_id,
                )
            except Exception as e:
                logger.warning(
                    f"Failed to publish lifecycle event for terminated instance "
                    f"{instance_id[:8]}...: {e}"
                )

        # 8.5. Cancel PENDING DependencyBus watchers targeting the
        # terminated instance. Done AFTER the DB cascade + lifecycle
        # event so any subscriber that races us sees consistent DB
        # state. Without this, an in-flight child task would deliver
        # a FollowUp onto a dead parent — the bus's
        # ``cancel_for_target`` transitions the watcher rows to
        # CANCELLED so the child's terminal event no-ops on the
        # cancel path. Flag-gated; no-op when
        # ``use_dependency_bus=False`` (the CM ``clear_for_instance``
        # call above is the authoritative cleanup in that case).
        await _cancel_bus_watchers_for(
            self._manager, instance_id, "terminate_instance"
        )

        # Summary log: surface total duration and unwind cost in one line so the
        # next latency regression is self-explanatory. Matches the [TRACE] style
        # used in daemon/services/job_processor.py and daemon/services/instance_lifecycle.py.
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            f"[TRACE] terminate_instance: {instance_id[:8]}... complete "
            f"(graph_unwind_ms={graph_unwind_ms}, jobs_cancelled={jobs_cancelled}, "
            f"children={len(child_ids)}, duration_ms={duration_ms}, "
            f"msgq_removed={message_queue_removed})"
        )

        return True

    async def pause_instance_cascade(self, instance_id: str) -> dict:
        """Pause an instance and cascade to all children (soft pause).

        Uses tree traversal helpers to find and pause the entire tree.
        Cancels active requests and sets status to paused (resumable).
        Does NOT remove instances from memory or release locks.

        L14 fix: per-tree-node ``repo.update(...)`` calls are batched
        into a SINGLE ``UPDATE ... WHERE instance_id IN (...)`` statement
        via ``_pause_cascade_db_sync``. Pre-fix the cascade loop issued
        one UPDATE per node (N+1 transactions for an N-node tree); a
        crash mid-loop left half the tree paused and half running. L14
        collapses all node updates into ONE transaction so a crash
        either pauses the entire tree or none of it.

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

        paused_at_iso = datetime.now(timezone.utc).isoformat()

        # L14: pre-classify which nodes should be paused (filter out
        # already-paused / not-found nodes). The sync DB helper does
        # NOT make per-node decisions — the caller classifies once
        # and the helper writes all eligible nodes in ONE batched
        # UPDATE.
        paused_instances_data: list[tuple[str, str | None, int]] = []
        skipped_ids: list[str] = []

        # Pre-fetch the CorrelationManager once — needed for the
        # waiting_for carve-out decision (parent has pending children
        # → reset cache to 0).
        cm = get_correlation_manager()

        # A6 gate: read the legacy ``waiting_for`` cascade flag (kill
        # switch). When ON, the pause cascade resets ``waiting_for=0``
        # for paused nodes (legacy behavior — fixes deadlocks where
        # a paused parent would never resume because
        # ``waiting_for > 0`` blocked the resume SQL's status guard).
        # When OFF (default), the CM is the SOLE completion authority
        # and the cascade preserves the existing ``waiting_for``
        # value. The CM re-registers correlations via
        # ``rebuild_from_db()`` after a restart and via the standard
        # ``register_message_send`` / ``resolve_response`` hooks on
        # resume — see ``docs/configuration/completion-flags.md``.
        use_legacy_cascade = bool(
            self._config.job_system.use_legacy_waiting_for_cascade
        )

        for node_id in tree_ids:
            try:
                meta = repo.get(node_id)

                if meta is None:
                    logger.warning(f"Instance {node_id[:8]}... not found in DB, skipping pause")
                    skipped_ids.append(node_id)
                    continue

                # Skip if already paused
                if meta.status == InstanceStatus.PAUSED.value:
                    logger.info(f"Instance {node_id[:8]}... is already paused, skipping")
                    skipped_ids.append(node_id)
                    continue

                # 1. Cancel active LLM requests (via cancellation callbacks)
                self._manager._request_registry.cancel_by_instance(
                    node_id, CancellationReason.USER_STOPPED
                )

                # 2. Cancel the running graph task (interrupts astream/ainvoke loop)
                # This raises asyncio.CancelledError in the streaming coroutine
                # Use pop() to prevent stale references after cancellation (consistent with terminate_instance)
                graph_task = self._manager._graph_tasks.pop(node_id, None)
                self._manager.release_context_usage_cache(node_id)
                if graph_task and not graph_task.done():
                    graph_task.cancel()
                    logger.info(f"Cancelled graph task for instance {node_id[:8]}...")

                # 3. Resolve waiting_for reset decision.
                # Phase 4 carve-out (legacy only): when the
                # CorrelationManager has pending children for this
                # parent, reset ``waiting_for`` to 0 (the CM is
                # authoritative; the cache is rebuild-only). When the
                # A6 flag is OFF, the cascade preserves the existing
                # value — CM owns the count and the DB column is
                # rebuild-only (ADR-011).
                if use_legacy_cascade:
                    if cm is not None:
                        has_pending_children = cm.get_pending_count(node_id) > 0
                    else:
                        has_pending_children = bool(
                            getattr(meta, "waiting_for", None) and meta.waiting_for > 0
                        )
                    waiting_for_value = 0 if has_pending_children else (meta.waiting_for or 0)
                else:
                    # CM-authoritative path: preserve the existing
                    # counter. The sync helper will omit the
                    # ``waiting_for=0`` clause from the UPDATE so the
                    # DB value is untouched.
                    waiting_for_value = meta.waiting_for or 0

                # L14: capture data for the batched UPDATE; the actual
                # write happens once in the sync helper below.
                paused_instances_data.append(
                    (node_id, meta.agent_id, waiting_for_value)
                )

                logger.info(f"Pausing instance {node_id[:8]}...")

            except Exception as e:
                logger.error(f"Failed to pause node {node_id[:8]}...: {e}")
                skipped_ids.append(node_id)

        # Single batched UPDATE — L14 transaction-boundary fix.
        # A6: pass ``use_legacy_cascade`` so the helper can decide
        # whether to include ``waiting_for=0`` in the SQL SET clause.
        db_result = await asyncio.to_thread(
            self._pause_cascade_db_sync,
            self._manager.engine,
            self._manager.write_guard,
            tree_ids=tree_ids,
            paused_at_iso=paused_at_iso,
            paused_instances_data=paused_instances_data,
            use_legacy_cascade=use_legacy_cascade,
        )

        # Post-commit side effects: SSE status_change per paused node.
        paused_ids = db_result.updated_ids
        agent_ids_by_instance = db_result.agent_ids_by_instance
        for node_id in paused_ids:
            try:
                await self._manager._live_hub.stream_status_change(
                    node_id,
                    InstanceStatus.PAUSED.value,
                    agent_id=agent_ids_by_instance.get(node_id),
                )
            except Exception as e:
                logger.warning(
                    f"pause_instance_cascade: status_change SSE emit failed "
                    f"for {node_id[:8]}...: {e}"
                )

        # NOTE: Unlike terminate_instance, we do NOT:
        # - Remove from instances dict (instance stays in memory, resumable)
        # - Release project locks (job continues)
        # - Mark jobs as cancelled
        # - Clean up live hub connections

        # Combine the helper's updated_ids (== nodes we wrote to) with the
        # skipped_ids the caller collected above (already-paused / not-found).
        result = {"paused_ids": paused_ids, "skipped_ids": skipped_ids}

        # Cancel PENDING DependencyBus watchers targeting the paused
        # root. Pausing a parent must not allow in-flight child tasks
        # to deliver a FollowUp into a paused parent. Per-node
        # cancellation would over-count (the same FollowUp may target
        # multiple paused nodes — but in practice a FollowUp is
        # keyed to a single parent), so we cancel once for the root.
        # The bus is flag-gated; no-op when ``use_dependency_bus=False``.
        await _cancel_bus_watchers_for(self._manager, root_id, "pause_instance_cascade")

        return result

    async def resume_instance_cascade(self, instance_id: str) -> dict:
        """Resume an instance and cascade to all children.

        Uses tree traversal helpers to find and resume the entire tree.
        Sets status to RUNNING and clears paused_at.
        Does NOT re-spawn or restart instances - just unpauses them.

        L14 fix: per-tree-node ``repo.update(...)`` calls are batched
        into a SINGLE ``UPDATE ... WHERE instance_id IN (...)`` statement
        via ``_resume_cascade_db_sync`` (followed by a small ancestor-
        only UPDATE for the ``waiting_for=1`` carve-out). Pre-fix the
        cascade loop issued one UPDATE per node; L14 collapses them
        so a crash either resumes the entire tree or none of it.

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

        # L14: pre-classify which nodes are eligible for resume (must
        # be in PAUSED status). Already-running nodes are skipped.
        resumable_ids: list[str] = []
        skipped_ids: list[str] = []
        agent_ids_by_instance: dict[str, str | None] = {}

        # A6 gate: read the legacy ``waiting_for`` cascade flag (kill
        # switch). When ON, the resume cascade resets ``waiting_for=0``
        # for resumed nodes and bumps ``waiting_for=1`` for ancestor
        # nodes (legacy behavior — keeps the SQL status guard +
        # waiting_for invariant consistent). When OFF (default), the
        # CM is the SOLE completion authority and the cascade
        # preserves the existing ``waiting_for`` value. The CM
        # re-registers correlations via ``rebuild_from_db()`` after a
        # restart and via the standard ``register_message_send`` /
        # ``resolve_response`` hooks on resume — see
        # ``docs/configuration/completion-flags.md``.
        use_legacy_cascade = bool(
            self._config.job_system.use_legacy_waiting_for_cascade
        )

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

                resumable_ids.append(node_id)
                agent_ids_by_instance[node_id] = meta.agent_id

            except Exception as e:
                logger.error(f"Failed to resume node {node_id[:8]}...: {e}")
                skipped_ids.append(node_id)

        # Single batched UPDATE — L14 transaction-boundary fix.
        # The helper issues (a) one UPDATE that flips status +
        # paused_at + waiting_for=0 for all eligible nodes, then
        # (b) one follow-up UPDATE for the ancestor ``waiting_for=1``
        # carve-out when resuming from a non-root node. Both UPDATEs
        # commit atomically.
        #
        # A6: pass ``use_legacy_cascade`` so the helper can gate the
        # ``waiting_for`` reset clauses behind the kill switch.
        if resumable_ids:
            db_result = await asyncio.to_thread(
                self._resume_cascade_db_sync,
                self._manager.engine,
                self._manager.write_guard,
                tree_ids=resumable_ids,
                ancestor_ids=ancestor_ids,
                is_root_resume=is_root_resume,
                use_legacy_cascade=use_legacy_cascade,
            )
            waiting_for_by_instance = db_result.waiting_for_by_instance
            resumed_ids = db_result.updated_ids
        else:
            waiting_for_by_instance = {}
            resumed_ids = []

        # Post-commit side effects: SSE status_change per resumed node.
        for node_id in resumed_ids:
            try:
                await self._manager._live_hub.stream_status_change(
                    node_id,
                    InstanceStatus.RUNNING.value,
                    agent_id=agent_ids_by_instance.get(node_id),
                )
            except Exception as e:
                logger.warning(
                    f"resume_instance_cascade: status_change SSE emit failed "
                    f"for {node_id[:8]}...: {e}"
                )
            wf = waiting_for_by_instance.get(node_id, 0)
            logger.info(f"Resumed instance {node_id[:8]}... (waiting_for={wf})")

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

    # =================================================================
    # Sync DB helpers — H10/M8/M9/L14 transaction-boundary fixes
    # =================================================================
    # These ``_*_db_sync`` methods perform ALL DB writes inside a single
    # ``WriteGuardSession`` transaction (via ``asyncio.to_thread`` from the
    # async callers). They are the established pattern in this codebase:
    # child_reports.py:_process_child_completion_db_sync and
    # job_feedback_observer.py:_finalize_job_db_sync / _finalize_instance_db_sync.
    #
    # Returns ``_XxxResult`` NamedTuples carrying all data the async caller
    # needs to fire post-commit side effects (SSE / CompletionRegistry /
    # lifecycle event / CM cleanup / dispatch-bus notify). NamedTuple fields
    # capture post-commit values BEFORE the session closes, since the row
    # becomes detached after ``session.commit()``.

    def _terminate_instance_db_sync(
        self,
        engine,
        write_guard,
        instance_id: str,
    ) -> _TerminateResult:
        """Sync DB half of ``terminate_instance`` (H10 fix).

        Runs in a worker thread via ``asyncio.to_thread``. Performs ALL
        DB writes for the terminate cascade inside ONE
        ``WriteGuardSession`` transaction:

          1. Re-read the instance row (authoritative re-entrancy guard;
             the async caller already short-circuited on a fast-path read
             but a concurrent writer could have raced us).
          2. UPDATE ``instances`` SET status='terminated', waiting_for=0,
             version+=1, updated_at=now. Single-statement atomic — a
             crash mid-UPDATE rolls back via ``WriteGuardSession.__exit__``.
          3. SELECT ``job_queue_items`` WHERE instance_id = :id AND
             status IN (PROCESSING, PENDING, FAILED) — the jobs to cancel.
          4. For the single PROCESSING job (if any), issue the in-session
             ``UPDATE job_queue_items SET status='cancelled' ... WHERE
             job_id=:id AND status='processing' RETURNING project_id`` so
             the project trigger-next-job logic still has the project_id.
          5. For PENDING / FAILED jobs, bulk-cancel via
             ``UPDATE job_queue_items SET status='cancelled' ... WHERE
             instance_id=:id AND status IN ('pending', 'failed')``.
          6. DELETE ``job_locks`` WHERE instance_id=:id (lock release).
          7. DELETE ``message_queue`` WHERE instance_id=:id.
          8. DELETE ``instance_hierarchy`` rows where this instance is the
             parent (so future tree traversals don't include the dead
             children). The child rows themselves stay (so audit logs /
             completion reports still resolve); only the parent link is
             removed.
          9. COMMIT — all-or-nothing.

        ``WriteGuardSession`` is the shutdown gate. It is NOT a mutex: a
        concurrent ``pause_writes()`` will block here until our commit
        completes (via ``_drain_event.wait()`` in the gate). This is the
        desired behavior — the migration entry point can safely swap the
        engine after we drain.

        Returns ``_TerminateResult`` with everything the async caller
        needs for post-commit side effects:

          * ``skip=True`` — row missing OR already terminal (idempotency
            guard). Caller short-circuits WITHOUT firing any side
            effects. Re-entry safety: this is the authoritative guard
            for terminate re-entrancy, replacing the old fast-path-only
            check.
          * ``parent_id`` / ``agent_id`` — captured from the row before
            commit (row is detached after).
          * Counter fields (message_jobs_cancelled, all_jobs_cancelled,
            message_queue_removed) for the [TRACE] summary log.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with WriteGuardSession(Session(engine), write_guard) as session:
            instance = session.get(Instance, instance_id)
            if instance is None:
                logger.debug(
                    f"terminate_instance: instance {instance_id[:8]}... not "
                    f"found in DB, skipping (sync helper)"
                )
                return _TerminateResult(
                    skip=True,
                    parent_id=None,
                    agent_id=None,
                    message_jobs_cancelled=0,
                    all_jobs_cancelled=0,
                    message_queue_removed=0,
                )
            if instance.status == InstanceStatus.TERMINATED.value:
                # Re-entrancy guard re-discovered here. The async caller
                # already short-circuited on the fast-path pre-read, but
                # a concurrent terminate could have raced us between that
                # read and this one. Idempotent no-op.
                logger.debug(
                    f"terminate_instance: instance {instance_id[:8]}... already "
                    f"terminated (sync helper re-entrancy guard)"
                )
                return _TerminateResult(
                    skip=True,
                    parent_id=instance.parent_id,
                    agent_id=instance.agent_id,
                    message_jobs_cancelled=0,
                    all_jobs_cancelled=0,
                    message_queue_removed=0,
                )

            # Capture fields needed for post-commit side effects BEFORE
            # we mutate the row. Row is detached after commit.
            parent_id = instance.parent_id
            agent_id = instance.agent_id

            # ── Step 1: atomic instance UPDATE (status + waiting_for) ──
            # Single-statement update keeps the (status, waiting_for)
            # pair atomic — pre-fix this was a 2-write sequence that
            # could leave (status=terminated, waiting_for=N>0) on crash.
            session.execute(
                text(
                    "UPDATE instances "
                    "SET status = 'terminated', "
                    "    waiting_for = 0, "
                    "    updated_at = :now, "
                    "    version = COALESCE(version, 1) + 1 "
                    "WHERE instance_id = :iid"
                ),
                {"iid": instance_id, "now": now_iso},
            )

            # ── Step 2: cancel jobs in the SAME transaction ──
            # Imported lazily to keep the module-level import surface
            # small and avoid circular-import risk through the job_queue
            # service.
            from ..repositories.job_queue.models import JobItem

            # Find all non-terminal jobs for this instance.
            non_terminal_statuses = ("processing", "pending", "failed")
            jobs = list(
                session.exec(
                    select(JobItem.job_id, JobItem.status, JobItem.project_id)
                    .where(JobItem.instance_id == instance_id)
                    .where(JobItem.status.in_(non_terminal_statuses))
                )
            )

            message_jobs_cancelled = 0
            all_jobs_cancelled = 0
            cancelled_project_ids: set[str] = set()

            if jobs:
                processing_job_ids = [j for j in jobs if j.status == "processing"]
                non_processing_job_ids = [j for j in jobs if j.status != "processing"]

                # PROCESSING → CANCELLED with the canonical transition.
                # We issue the atomic UPDATE with a status guard so a
                # concurrent finalizer (CM callback) that already moved
                # the job to COMPLETED/FAILED sees rowcount=0 and we
                # no-op. The JobItem.version_id_col additionally appends
                # ``AND version = :expected`` on the ORM path; the Core
                # UPDATE below is even safer — no version check, but the
                # ``status='processing'`` predicate is the guard.
                completed_at = now_iso
                cancelled_at = now_iso
                if processing_job_ids:
                    session.execute(
                        text(
                            "UPDATE job_queue_items "
                            "SET status = 'cancelled', "
                            "    cancelled_at = :cancelled_at, "
                            "    completed_at = :completed_at, "
                            "    error_message = :err, "
                            "    result_summary = NULL "
                            "WHERE job_id IN :job_ids "
                            "  AND status = 'processing'"
                        ).bindparams(
                            bindparam("job_ids", expanding=True),
                        ),
                        {
                            "job_ids": [j.job_id for j in processing_job_ids],
                            "cancelled_at": cancelled_at,
                            "completed_at": completed_at,
                            "err": "Instance terminated",
                        },
                    )
                    # Capture the project_id of the processing job for
                    # the trigger-next-job follow-up. The async caller
                    # does the actual trigger (we cannot reach the
                    # dispatch bus from this sync helper).
                    for j in processing_job_ids:
                        if j.project_id:
                            cancelled_project_ids.add(j.project_id)

                # PENDING / FAILED → CANCELLED (idempotent — these
                # statuses can also flip to CANCELLED directly).
                if non_processing_job_ids:
                    session.execute(
                        text(
                            "UPDATE job_queue_items "
                            "SET status = 'cancelled', "
                            "    cancelled_at = :cancelled_at, "
                            "    error_message = COALESCE(error_message, :err) "
                            "WHERE job_id IN :job_ids "
                            "  AND status IN ('pending', 'failed')"
                        ).bindparams(
                            bindparam("job_ids", expanding=True),
                        ),
                        {
                            "job_ids": [j.job_id for j in non_processing_job_ids],
                            "cancelled_at": cancelled_at,
                            "err": "Instance terminated",
                        },
                    )

                # message-job-style count: count from the just-cancelled
                # set where job_type='message'. We don't have the type
                # on hand here, so the async caller will recount via
                # ``find_jobs_by_instance(job_type='message')`` for the
                # post-commit side effects. For the [TRACE] log we
                # approximate by counting the pre-update set that had
                # ``job_type='message'`` (loaded here for accuracy).
                message_job_rows = list(
                    session.exec(
                        select(JobItem.job_id)
                        .where(JobItem.instance_id == instance_id)
                        .where(JobItem.job_type == "message")
                        .where(JobItem.status.in_(non_terminal_statuses))
                    )
                )
                message_jobs_cancelled = len(message_job_rows)
                all_jobs_cancelled = len(jobs)

            # ── Step 3: delete ``job_locks`` rows for this instance ──
            from ..repositories.job_queue.models import JobLock

            session.execute(
                text("DELETE FROM job_locks WHERE instance_id = :iid"),
                {"iid": instance_id},
            )

            # ── Step 4: delete ``message_queue`` rows for this instance ──
            from ..repositories.message_queue.models import MessageQueue

            msgq_result = session.execute(
                text("DELETE FROM message_queue WHERE instance_id = :iid"),
                {"iid": instance_id},
            )
            message_queue_removed = (
                msgq_result.rowcount if msgq_result.rowcount is not None else 0
            )

            # ── Step 5: clean up ``instance_hierarchy`` rows where this ──
            # instance is the parent. We keep the child rows themselves
            # so audit / completion-report lookups still resolve, but
            # remove the parent→child links so future tree traversals
            # don't see the dead subtree. The child rows are orphaned
            # intentionally — they will be reaped by a separate GC sweep.
            session.execute(
                text("DELETE FROM instance_hierarchy WHERE parent_id = :iid"),
                {"iid": instance_id},
            )

            # ── COMMIT ── atomic across all 5 steps above.
            session.commit()

            return _TerminateResult(
                skip=False,
                parent_id=parent_id,
                agent_id=agent_id,
                message_jobs_cancelled=message_jobs_cancelled,
                all_jobs_cancelled=all_jobs_cancelled,
                message_queue_removed=message_queue_removed,
            )

    def _spawn_instance_db_sync(
        self,
        engine,
        write_guard,
        *,
        instance_id: str,
        resolved_agent_id: str,
        resolved_agent_dir: str,
        agent_name: str,
        parent_id: str | None,
        project_id: str | None,
        instance_metadata: dict[str, Any],
    ) -> _SpawnResult:
        """Sync DB half of ``spawn_instance`` (M8 fix).

        Runs in the caller's thread (sync). Performs ALL DB writes for the
        spawn inside ONE ``WriteGuardSession`` transaction:

          1. SELECT parent (if parent_id is set) for source inheritance.
          2. INSERT INTO instances.
          3. INSERT INTO instance_hierarchy (if parent_id is set).
          4. If parent has ``original_source`` metadata, append it to the
             child's instance_metadata via the dialect-aware
             ``jsonb_set`` / ``json_set`` UPDATE — atomic with the
             INSERTs so the child is never visible without its inherited
             source.
          5. COMMIT — atomic.

        Pre-fix, the cascade was three separate transactions:

          (a) ``instance_repository.create()`` — own session
          (b) ``instance_repository.get(parent_id)`` — own session
          (c) ``instance_repository.set_metadata(original_source)`` — own session

        A crash between (b) and (c) left a child instance visible without
        its inherited ``original_source``. M8 collapses these into one
        transaction so the child is either fully created (with inherited
        source) or not created at all.

        The ``instance_hierarchy`` insert was already inside the
        ``create()`` call's session (see repository.py:144-150), so it
        moves with us into the unified session for free.

        ``WriteGuardSession`` is the shutdown gate; see
        :meth:`_terminate_instance_db_sync` for the long-form contract.

        Returns ``_SpawnResult`` carrying the captured ``created_at``
        and parent / agent / project IDs the async caller (or the sync
        public method) needs to fire ``stream_instance_created`` SSE.
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        with WriteGuardSession(Session(engine), write_guard) as session:
            # Step 1: parent lookup for source inheritance. Done INSIDE
            # the same session so we see a consistent snapshot.
            inherited_source: str | None = None
            if parent_id:
                parent_row = session.get(Instance, parent_id)
                if parent_row is not None and parent_row.instance_metadata:
                    inherited_source = parent_row.instance_metadata.get(
                        "original_source"
                    )

            # Merge inherited source into the metadata dict (in-memory).
            # The dialect-aware atomic metadata write below handles the
            # JSON write for us; we only need to pass the merged dict.
            effective_metadata = dict(instance_metadata or {})
            if inherited_source and "original_source" not in effective_metadata:
                effective_metadata["original_source"] = inherited_source

            # Step 2: INSERT INTO instances. Use the ORM ``add`` so the
            # SQLModel ``version_id_col`` machinery auto-emits the
            # initial version=1 — matches the pre-fix behavior.
            new_instance = Instance(
                instance_id=instance_id,
                project_id=project_id,
                agent_id=resolved_agent_id,
                agent_dir=resolved_agent_dir,
                agent_name=agent_name,
                parent_id=parent_id,
                status=InstanceStatus.IDLE.value,
                instance_metadata=effective_metadata,
                children="[]",
                waiting_for=0,
                version=1,
                created_at=now_iso,
                updated_at=now_iso,
            )
            session.add(new_instance)

            # Step 3: hierarchy insert (mirrors repository.py:144-150).
            if parent_id is not None:
                session.add(
                    InstanceHierarchy(
                        parent_id=parent_id,
                        child_id=instance_id,
                        created_at=now_iso,
                    )
                )

            # Step 4: COMMIT. The dialect-aware metadata write that
            # ``set_metadata`` does (jsonb_set / json_set) is unnecessary
            # because we already passed ``effective_metadata`` to the
            # Instance constructor — SQLAlchemy serializes the dict to
            # the JSON column on flush. If a future caller needs to
            # patch a single key atomically post-insert, use the
            # existing ``set_metadata`` repository method which has its
            # own dialect-aware UPDATE.
            session.commit()
            session.refresh(new_instance)

            return _SpawnResult(
                created=True,
                parent_id=parent_id,
                agent_id=resolved_agent_id,
                project_id=project_id,
                created_at=new_instance.created_at,
                inherited_source=bool(inherited_source),
            )

    def _pause_cascade_db_sync(
        self,
        engine,
        write_guard,
        *,
        tree_ids: list[str],
        paused_at_iso: str,
        paused_instances_data: list[tuple[str, str | None, int]],
        use_legacy_cascade: bool = False,
    ) -> _CascadeUpdateResult:
        """Sync DB half of ``pause_instance_cascade`` (L14 fix).

        Runs in the caller's thread (sync). Performs the per-tree-node
        pause updates in ONE batched ``UPDATE ... WHERE instance_id IN
        (...)`` statement instead of N+1 per-node updates.

        Pre-fix, the cascade loop called ``repo.update(node_id, ...)``
        for every node — N separate transactions. A crash mid-loop left
        half the tree paused and half running (zombie / split-brain state).
        L14 collapses the N updates into a single ``UPDATE`` so a crash
        either pauses the entire tree or none of it.

        A6 gate: ``use_legacy_cascade`` controls whether the
        ``waiting_for = 0`` clause is included in the SQL SET clause.

          * ``True`` (kill switch): the legacy behavior is preserved —
            every paused node has its ``waiting_for`` reset to 0 so
            the status guard on resume (``status = 'paused'``) does
            not deadlock against a parent with in-flight children.
          * ``False`` (default, CM-authoritative): the
            ``waiting_for = 0`` clause is omitted and the existing
            DB value is preserved. The CorrelationManager is the SOLE
            completion authority; the ``waiting_for`` column is
            rebuild-only cache (ADR-011).

        Args:
            engine: The shared SQLAlchemy engine.
            write_guard: The shared WritePauseGuard.
            tree_ids: All node IDs in the tree (from
                ``repo.get_tree_ids(root_id)``).
            paused_at_iso: ISO-8601 timestamp for the paused_at column.
            paused_instances_data: List of ``(instance_id, agent_id,
                waiting_for)`` tuples for nodes that should be paused.
                The caller pre-filters out already-paused nodes (skip
                behavior) and pre-classifies the waiting_for reset
                (parent carve-out vs. simple pause).
            use_legacy_cascade: A6 kill switch. When True, the
                ``waiting_for = 0`` SET clause is included in the
                batched UPDATE (legacy path). When False (default),
                the clause is omitted and the existing ``waiting_for``
                value is preserved (CM-authoritative path).

        Returns:
            ``_CascadeUpdateResult`` with the list of updated IDs and
            their captured ``agent_id`` / ``waiting_for`` so the async
            caller can fire ``stream_status_change`` SSE per node.
        """
        if not paused_instances_data:
            return _CascadeUpdateResult(
                updated_ids=[],
                skipped_ids=[],
                agent_ids_by_instance={},
                waiting_for_by_instance={},
            )

        updated_ids = [iid for iid, _agent, _wf in paused_instances_data]
        agent_ids_by_instance = {
            iid: agent for iid, agent, _wf in paused_instances_data
        }
        waiting_for_by_instance = {
            iid: wf for iid, _agent, wf in paused_instances_data
        }

        with WriteGuardSession(Session(engine), write_guard) as session:
            # L14 fix: single batched UPDATE. The ``tree_ids`` list is
            # expanded into the ``IN`` clause via SQLAlchemy's
            # ``expanding=True`` parameter. SQLite and PostgreSQL both
            # accept the expanded IN list.
            #
            # F03 status guard: mirror the resume helper's predicate
            # pattern so a concurrent pause/resume that already flipped
            # the status is a no-op on that row (rowcount drops). Only
            # non-terminal, non-paused states are eligible for pause
            # — pausing a terminal row would lose the terminal write.
            #
            # A6: build the SET clause dynamically so the
            # ``waiting_for = 0`` reset is gated behind the
            # ``use_legacy_cascade`` flag. When OFF (default), the
            # clause is omitted and the existing value is preserved.
            set_clauses = [
                "status = :paused_status",
                "paused_at = :paused_at",
                "updated_at = :paused_at",
            ]
            if use_legacy_cascade:
                # Legacy path: reset waiting_for to 0. The position
                # in the SET clause list (after status, before
                # paused_at) is preserved for code-review readability
                # of the diff.
                set_clauses.insert(1, "waiting_for = 0")
            set_clause_sql = ",\n                    ".join(set_clauses)
            session.execute(
                text(
                    f"UPDATE instances "
                    f"SET {set_clause_sql} "
                    f"WHERE instance_id IN :tree_ids "
                    f"  AND status IN (:running_status, :idle_status, :waiting_children_status)"
                ).bindparams(
                    bindparam("tree_ids", expanding=True),
                ),
                {
                    "paused_status": InstanceStatus.PAUSED.value,
                    "paused_at": paused_at_iso,
                    "tree_ids": updated_ids,
                    "running_status": InstanceStatus.RUNNING.value,
                    "idle_status": InstanceStatus.IDLE.value,
                    "waiting_children_status": InstanceStatus.WAITING_CHILDREN.value,
                },
            )
            session.commit()

        # Skipped = nodes that were already paused (filtered out by the
        # caller before passing to this helper). We re-derive from
        # ``tree_ids`` minus ``updated_ids``.
        skipped_ids = [iid for iid in tree_ids if iid not in set(updated_ids)]

        return _CascadeUpdateResult(
            updated_ids=updated_ids,
            skipped_ids=skipped_ids,
            agent_ids_by_instance=agent_ids_by_instance,
            waiting_for_by_instance=waiting_for_by_instance,
        )

    def _resume_cascade_db_sync(
        self,
        engine,
        write_guard,
        *,
        tree_ids: list[str],
        ancestor_ids: set[str],
        is_root_resume: bool,
        use_legacy_cascade: bool = False,
    ) -> _CascadeUpdateResult:
        """Sync DB half of ``resume_instance_cascade`` (L14 fix).

        Runs in the caller's thread (sync). Performs the per-tree-node
        resume updates in ONE batched ``UPDATE ... WHERE instance_id IN
        (...)`` statement instead of N+1 per-node updates.

        The batched UPDATE sets:

          * ``status='running'``
          * ``paused_at=NULL`` (clears the paused timestamp)
          * ``waiting_for=0`` for all nodes (legacy only — gated by A6)
          * then a follow-up UPDATE bumps ``waiting_for`` to 1 for the
            ancestor nodes when resuming from a child (legacy only —
            gated by A6).

        Splitting the WRITE into two UPDATEs (instead of one with a
        CASE-WHEN on an IN list) keeps the SQL portable across SQLite
        and PostgreSQL — CASE-WHEN with IN-list semantics is dialect-
        sensitive, while a plain ``WHERE instance_id IN :ancestor_ids``
        with expanding bind params works the same on both.

        A6 gate: ``use_legacy_cascade`` controls whether the
        ``waiting_for`` reset clauses are included.

          * ``True`` (kill switch): the legacy behavior is preserved —
            every resumed node has its ``waiting_for`` reset to 0
            and ancestor nodes get ``waiting_for=1`` (for the
            "resumed from a child, parent is waiting on me" state).
          * ``False`` (default, CM-authoritative): both
            ``waiting_for`` clauses are omitted and the existing DB
            value is preserved. The CorrelationManager is the SOLE
            completion authority; the ``waiting_for`` column is
            rebuild-only cache (ADR-011).

        Returns ``_CascadeUpdateResult`` with the updated IDs and their
        waiting_for values so the async caller can fire
        ``stream_status_change`` SSE per node.
        """
        # The caller pre-filters out nodes that are not in PAUSED status
        # (skip behavior). The set we get here is the union of nodes
        # that are actually paused.
        if not tree_ids:
            return _CascadeUpdateResult(
                updated_ids=[],
                skipped_ids=[],
                agent_ids_by_instance={},
                waiting_for_by_instance={},
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        with WriteGuardSession(Session(engine), write_guard) as session:
            # Single batched UPDATE: status + paused_at + (legacy)
            # waiting_for=0 for all nodes that are currently paused.
            # The ``status = 'paused'`` predicate is the guard so a
            # concurrent pause/resume that already flipped the status
            # is a no-op on that row (rowcount drops).
            #
            # A6: build the SET clause dynamically so the
            # ``waiting_for = 0`` reset is gated behind the
            # ``use_legacy_cascade`` flag. When OFF (default), the
            # clause is omitted and the existing value is preserved.
            set_clauses = [
                "status = :running_status",
                "paused_at = NULL",
                "updated_at = :now",
            ]
            if use_legacy_cascade:
                # Legacy path: reset waiting_for to 0. Inserted
                # between status and paused_at to keep the diff
                # readable.
                set_clauses.insert(1, "waiting_for = 0")
            set_clause_sql = ",\n                    ".join(set_clauses)
            session.execute(
                text(
                    f"UPDATE instances "
                    f"SET {set_clause_sql} "
                    f"WHERE instance_id IN :tree_ids "
                    f"  AND status = :paused_status"
                ).bindparams(
                    bindparam("tree_ids", expanding=True),
                ),
                {
                    "running_status": InstanceStatus.RUNNING.value,
                    "paused_status": InstanceStatus.PAUSED.value,
                    "now": now_iso,
                    "tree_ids": tree_ids,
                },
            )

            # Follow-up: bump waiting_for=1 for the ancestor chain
            # when resuming from a non-root. Only fires when
            # ``is_root_resume`` is False (root resume keeps
            # ``waiting_for=0`` for everyone, matching the pre-fix
            # behavior at line 887-888 of the original code).
            #
            # A6: gate the ancestor bump behind ``use_legacy_cascade``
            # as well — the CM-authoritative path preserves the
            # existing ``waiting_for`` value on the ancestor nodes.
            ancestor_bump_ids: list[str] = []
            if use_legacy_cascade and not is_root_resume and ancestor_ids:
                ancestor_bump_ids = [iid for iid in ancestor_ids if iid in set(tree_ids)]
                if ancestor_bump_ids:
                    session.execute(
                        text(
                            "UPDATE instances "
                            "SET waiting_for = 1, "
                            "    updated_at = :now "
                            "WHERE instance_id IN :ancestor_ids"
                        ).bindparams(
                            bindparam("ancestor_ids", expanding=True),
                        ),
                        {
                            "now": now_iso,
                            "ancestor_ids": ancestor_bump_ids,
                        },
                    )
            session.commit()

        # Capture per-node waiting_for for the SSE emit on the event
        # loop. When the A6 legacy flag is OFF, every node keeps its
        # existing ``waiting_for`` value (we don't know it here — the
        # caller logs the value it captured BEFORE the helper ran).
        # We fall back to the previously-captured value in the
        # ``waiting_for_by_instance`` dict so the log line stays
        # meaningful.
        waiting_for_by_instance: dict[str, int] = {}
        for iid in tree_ids:
            if use_legacy_cascade and not is_root_resume and iid in ancestor_ids:
                waiting_for_by_instance[iid] = 1
            else:
                # A6: when the legacy flag is OFF, the helper did
                # not touch ``waiting_for``; the log line shows 0 as
                # a neutral placeholder (the actual value is in the
                # DB and visible to CM via ``get_pending_count``).
                waiting_for_by_instance[iid] = 0

        return _CascadeUpdateResult(
            updated_ids=list(tree_ids),
            skipped_ids=[],
            agent_ids_by_instance={},  # caller pre-fetches for SSE
            waiting_for_by_instance=waiting_for_by_instance,
        )
