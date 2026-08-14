"""Instance manager orchestrating all agent instances."""

import sys
import uuid
import logging
import asyncio
import re
import time
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from langgraph.graph.state import CompiledStateGraph
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from .config import Config
from .ensemble_config import EnsembleConfig
from .graph import build_instance_graph
from .loader import PromptCache, load_and_cache_prompt  # re-exported: instance_lifecycle does `from ..manager import load_and_cache_prompt` and tests patch `daemon.manager.load_and_cache_prompt`
from .utils import parse_think_tags, serialize_message, find_near_instance, DEFAULT_FUZZY_MATCH_DISTANCE  # noqa: F401  # re-exported for backward compat with tests/tools
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
    create_mcp_server_repository,
    create_infra_repository,
    create_shared_meta_kv_repository,
    create_skill_repository,
    create_blueprint_repository,
    create_blueprint_embedding_repository,
    create_blueprint_pending_repository,
    create_skill_lineage_repository,
    create_skill_embedding_repository,
    create_skill_usage_repository,
    create_skill_trigger_repository,
    create_skill_ab_test_repository,
    create_skill_bank_repository,
    ReportInjectionRepository,
    InstanceUiPrefsRepository,
)
from .repositories.task.repository import TaskRepository
from .registry import get_registry
from .mcp.builtin_servers import get_registry as get_mcp_registry, is_builtin_disabled
from .mcp.warmup_pool import McpWarmupPool, get_mcp_warmup_pool
from .mcp import warmup_pool as _warmup_pool_module
from .mcp.config import McpStdioConfig
from .opencode import OpenCodeSessionRegistry, create_opencode_session_repository

from .repositories.instance.repository import get_agent_name
from .repositories.instance.models import Instance, InstanceStatus
from .repositories.message_queue.models import MessageQueue, MessageStatus, MessageType
from .repositories.task.models import Task, TaskType, TaskStatus
from .repositories.event.models import Event, EventKind
from .repositories.db_connection.models import DbConnectionConfig
from .repositories.shared_meta_kv.models import SharedMetaKV
from sqlmodel import Session
from sqlalchemy import text, select
from .tools import create_instance_tools
from .sources import SourceRegistry, ResponseDispatcher, SourceCleanup
from .services.live_event_hub import LiveEventHub
from .services.event_bus import EventBus
from .services.job_queue_service import DemandState
from .services.dependency_bus import get_dependency_bus
from .services.instance_lifecycle import InstanceLifecycleService
from .services.instance_messaging import InstanceMessagingService
from .services.messaging_types import AsyncMessageResult  # re-exported for `from daemon.manager import AsyncMessageResult`
from .services.child_reports import ChildReportsService
from .services.error_reporting import ErrorReportingService
from .services.cancellation import CancellationService
from .services.title_generation import TitleGenerationService
from .services.event_publisher import EventPublisherService
from .services.skill_embedding_service import SkillEmbeddingService
from .services.skill_store_service import SkillStoreService
from .services.skill_search_service import SkillSearchService
from .services.blueprint_matcher import BlueprintMatcher
from .services.blueprint_rate_limiter import BlueprintRateLimiter
from .services.blueprint_write_service import BlueprintWriteService
from .services.skill_injection_service import SkillInjectionService
from .services.skill_metrics_service import SkillMetricsService
from .services.skill_evolution_service import SkillEvolutionService
from .services.skill_job_dispatcher import SkillJobDispatcher
from .services.skill_trigger_engine import SkillTriggerEngine
from .services.skill_trigger_seed import seed_default_triggers
from .services.skill_seed_service import SkillSeedService
from .services.skill_clone_service import SkillCloneService
from .services.maintenance import MaintenanceService, CheckpointCleanupJob
from .services.todo_manager import TodoManager
from .services.question_manager import QuestionManager
from .cancellation import (
    CancellationToken,
    CancellationTokenSource,
    CancellationReason,
    OperationCancelledError
)
from .request_registry import ActiveRequestRegistry
from .compaction import ContextCompactor
from .constants import WORKER_POOL_SIZE
from .write_pause_guard import WritePauseGuard

# Worker pool imports (lazy import to avoid circular dependency)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .services.worker_pool import WorkerPool
    from .services.task_processor import TaskProcessor
    from .services.stale_task_recovery import StaleTaskRecovery
    from .services.mcp_service import McpService



logger = logging.getLogger(__name__)

# TTL for releasing in-memory graphs for non-active cached instances (in hours)
INSTANCE_CACHE_TTL_HOURS = 24


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
        Event type string: "user_message" | "assistant_message" | "thinking"
            | "tool_call" | "tool_result"
    """
    if msg.get("role") == "user":
        return "user_message"
    if msg.get("role") == "tool":
        return "tool_result"
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


class InstanceManager:
    """Manages all agent instances, their graphs, and lifecycle."""

    def __init__(
        self,
        config: Config,
        ensemble_config: EnsembleConfig | None = None,
        credential_manager: "CredentialManager | None" = None,
    ):
        """Initialize the instance manager.

        Args:
            config: Configuration object with LLM, limits, and persistence settings.
            ensemble_config: Optional EnsembleConfig controlling database backend
                selection. When None or is_sqlite, the existing SQLite engine
                creation path is used (backward compatible). When is_postgres,
                ``create_postgres_engine`` is used instead.
            credential_manager: Optional shared :class:`CredentialManager` used
                by the database tool layer to decrypt connection credentials at
                query time. Injected from ``app.state.credential_manager`` in
                production (N5: shared singleton, not per-instance); falls back
                to constructing a fresh one for tests that build ``InstanceManager``
                directly.
        """
        self.config = config
        self._ensemble_config = ensemble_config
        self.db_path = Path(config.persistence.db_path)
        self._checkpointer: Any = None  # CheckpointerAdapter — set by initialize()
        self._loop: asyncio.AbstractEventLoop | None = None  # Set during initialize()
        self.prompt_cache = PromptCache()

        # Write-pause gate for Phase 3 SQLite→PostgreSQL hot-swap.
        # Services/tools open sessions via WriteGuardSession which consults
        # this guard; pause_writes() blocks new sessions and drains the
        # in-flight ones so the migration can swap engines safely.
        self._write_guard = WritePauseGuard()

        # Initialize context compactor
        if self.config.compaction.enabled:
            # ``base_url_backup`` is threaded through the compactor's
            # llm_config for HA-failover coverage of this secondary LLM
            # invocation; the actual failover wiring here is a follow-up
            # (see FailoverController note in
            # daemon/llm_error_classifier.py). The primary retry path
            # lives in ``build_instance_llms`` (daemon/graph.py) and is
            # fully wired.
            self._compactor = ContextCompactor(
                config=self.config.compaction,
                llm_config={
                    "base_url": self.config.llm.base_url,
                    "base_url_backup": self.config.llm.base_url_backup,
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

        # Last context-usage token count broadcast per instance. Used to
        # suppress redundant context_usage SSE events (see
        # ``InstanceMessaging._emit_context_usage``).
        self._last_context_usage: dict[str, int] = {}

        # LLM concurrency setting
        self._llm_semaphore = asyncio.Semaphore(config.limits.llm_concurrency)

        # Create ONE shared database engine for all repositories
        # This prevents database lock contention when multiple components
        # (watchdog thread, async processors, etc.) access the same SQLite file
        #
        # Database backend is selected by ensemble_config:
        #   - ensemble_config.is_postgres  → sync PostgreSQL engine via psycopg
        #                                     (Phase 2 will introduce async sessions)
        #   - ensemble_config.is_sqlite (or None) → existing sync SQLite path
        if self._ensemble_config is not None and self._ensemble_config.is_postgres:
            from .repositories.factory import create_postgres_engine
            self._engine = create_postgres_engine(self._ensemble_config)
        else:
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

        # Postgres-specific schema evolution. create_all() only creates
        # tables that don't exist — it does NOT add columns to existing
        # tables, and the migration runner skips non-SQLite engines. So
        # for production Postgres we explicitly add columns that
        # newer code depends on.
        #
        # Currently this only adds task.last_heartbeat_at (the
        # per-task liveness signal for StaleTaskRecovery — see
        # docs/bugs/child-completion-report-lost-under-concurrent-task-processing.md
        # §9.1 and the per-instance guard follow-up). The IF NOT EXISTS
        # clauses make the call idempotent — safe to re-run on every
        # startup.
        if self._ensemble_config is not None and self._ensemble_config.is_postgres:
            self._ensure_postgres_columns()
            # Phase 4: drop the legacy completion-state columns on
            # PostgreSQL. The SQLite migration runner NO-OPs on
            # PostgreSQL (runner.py lines 446-448), so the equivalent
            # ALTER TABLE ... DROP COLUMN IF EXISTS statements are
            # executed here. Idempotent via IF EXISTS.
            self._ensure_postgres_drop_legacy_columns()
            # ── Phase 5: drop the seven legacy job_queue_items columns ──
            # (status, started_at, completed_at, result_summary,
            # error_message, cancelled_at, failed_at). admission_state
            # is the sole authority after Phase 4 cleanup.
            #
            # Phase 5 Batch 2 activated: JobItem SQLModel no longer
            # maps the seven legacy columns (see
            # daemon/repositories/job_queue/models.py — the
            # ``status`` field and the six timing/result columns
            # have been removed; the ``status`` indexes are gone).
            # The migration runner is a NO-OP on PostgreSQL, so the
            # equivalent ALTER TABLE ... DROP COLUMN IF EXISTS
            # statements run here at startup via
            # ``_ensure_postgres_drop_admission_legacy()``. See its
            # docstring for idempotency and lock-cost notes.
            #
            # The legacy ``JobStatus`` enum was removed in Phase 7b; callers
            # now use inline status string literals or the
            # ``_ADMISSION_TO_LEGACY_STATUS`` map. Activating the
            # column drop here is safe because those callers either
            # (a) interpolate the legacy value strings into a
            # ``status`` parameter that is read-only after the drop
            # (DEAD column, missing on PG) — handled by the PG
            # helper's IF EXISTS clauses — or (b) were migrated in
            # Phase 7b. The smoke tests in Task 5 confirm the helper
            # activates without ORM-level errors against a fresh
            # database; the production readers are out of scope for
            # this batch.
            self._ensure_postgres_drop_admission_legacy()

        # ── Database Tool Category (Phase 2) ──────────────────────────────
        # ConnectionPoolManager is a shared singleton at the manager level (C3)
        # so N instances share M connection pools instead of proliferating them.
        # Both the repository and the pool manager need access to the engine,
        # which is why this block sits here, after engine/migrations/columns.
        # Imports are inline to avoid circular dependencies at module load time.
        from .sources.credentials import CredentialManager
        if credential_manager is None:
            credential_manager = CredentialManager()
        self._credential_manager = credential_manager

        from .repositories.db_connection.repository import DbConnectionRepository
        self._db_connection_repository = DbConnectionRepository(self._engine)

        from .services.db_pool_manager import ConnectionPoolManager
        self._db_pool_manager = ConnectionPoolManager(
            self._db_connection_repository,
            self._credential_manager,
        )

        # ── Infra asset repository (shared singleton) ────────────────
        # One repository at the manager level, bound to the shared
        # engine — every instance and every tool call goes through
        # this single object (C3: prevents per-instance engine
        # allocation / lock contention). Tables are created by the
        # MigrationRunner via the infra-info migration, so
        # ``create_tables=False`` here. The default type registry
        # (``server``, ``k8s_cluster``, ``datacenter``, …) is
        # seeded idempotently on every startup by
        # :meth:`_bootstrap_infra_types` (called below with
        # try/except) — the inline ``bootstrap_default_types()``
        # call that used to live here was the C1 bug (it bypassed
        # the fault-tolerance wrap).
        self._infra_repository = create_infra_repository(
            engine=self._engine,
            create_tables=False,
        )

        # ── Shared Context Metadata repository (shared singleton) ──
        # One repository at the manager level, bound to the shared
        # engine (C3 — same singleton rationale as ``_infra_repository``).
        # Table is created by ``SQLModel.metadata.create_all()`` at
        # startup — no migration file required. ``create_tables=False``
        # here matches the ``_infra_repository`` wiring immediately
        # above. The model was imported at module level so it is
        # registered with ``SQLModel.metadata`` before ``create_all()``
        # runs.
        self._shared_meta_kv_repo = create_shared_meta_kv_repository(
            engine=self._engine,
            create_tables=False,
        )

        # NEW: Message queue repository for SQLModel-based operations
        self._queue_repository = create_message_queue_repository(engine=self._engine, create_tables=False)

        # Report-injection queue repository. Backs the DB-backed, queued,
        # persistent report-delivery path (the deadlock fix for
        # parent-waits-for-child when the parent holds its graph turn
        # open). Shares the engine with the other repos. The
        # ``report_injections`` table is created by
        # ``SQLModel.metadata.create_all()`` (model registered via
        # ``daemon/repositories/__init__.py``).
        self._report_injection_repo = ReportInjectionRepository(engine=self._engine)

        # Instance UI preferences (pin + color tag). Global-scope table
        # keyed by instance_id; merge happens at the API router layer so
        # the repository stays generic. Shares the engine with the
        # other repos. The ``instance_ui_prefs`` table is created by
        # ``SQLModel.metadata.create_all()`` (model registered via
        # ``daemon/repositories/__init__.py``).
        self._instance_ui_prefs_repo = InstanceUiPrefsRepository(engine=self._engine)

        # Fast-path hint set for the report-injection drain: holds the
        # parent instance ids that have at least one PENDING
        # ``report_injections`` row. Bumped post-commit in
        # ``child_reports._dispatch_post_commit_side_effects`` and
        # discarded by ``ReportInjectionSlot.drain`` once a DB drain
        # confirms empty. Best-effort (the DB claim is the source of
        # truth) — lets the per-LLM-call drain skip the DB round-trip
        # for instances with no pending reports.
        self._report_injection_pending: set[str] = set()
        
        # discard_on_startup: safe "backlog clear". Clears only unstarted
        # / terminal work (PENDING tasks + their messages); RUNNING and
        # PAUSED tasks (and the messages backing them) are preserved so:
        #   * a paused instance still blocks system_defer_queue after
        #     restart (the defer idle gate reads the task table), and
        #   * a paused instance can still be resumed after restart
        #     (its backing message survives), and
        #   * StaleTaskRecovery can still find in-flight RUNNING tasks.
        # The previous implementation wiped the task + message tables
        # unconditionally, which orphaned paused instances on restart.
        if config.queue.discard_on_startup:
            msg_count = self._queue_repository.clear_all(preserve_in_flight=True)
            logger.info(
                f"Cleared {msg_count} backlog message(s) "
                f"(discard_on_startup=backlog-clear; in-flight/paused preserved)"
            )

            # Also discard backlog tasks (linked to messages)
            task_repo = TaskRepository(
                engine=self._engine,
                on_pending_task=lambda: self._worker_pool.notify_work() if self._worker_pool else None
            )
            task_count = task_repo.clear_all(preserve_in_flight=True)
            logger.info(
                f"Cleared {task_count} backlog task(s) "
                f"(discard_on_startup=backlog-clear; running/paused preserved)"
            )
        
        # NEW: Request registry for cancellation support
        self._request_registry = ActiveRequestRegistry()

        # NEW: RAM injection queue for user message injection feature.
        # Maps instance_id → list of {"content": str, "timestamp": str}
        # entries (FIFO queue). Multiple pending messages can accumulate for
        # the same instance; the agent_node consumes ALL of them on its next
        # LLM call, in oldest-first order.
        # RAM-only: no DB persistence. The injected HumanMessage IS persisted
        # to checkpoint via C2 (agent_node returns both [injected..., response]).
        # Threaded into the LangGraph agent node via factory closure (C1).
        # NOTE: This queue is mutated only by synchronous helpers in this class.
        # All callers (HTTP router and agent_node) run on the asyncio event loop.
        # The mutation methods contain no internal ``await``, so cooperative
        # single-thread asyncio gives them atomicity. Do NOT call these from a
        # thread pool (e.g. a sync FastAPI ``def`` endpoint) without first
        # adding an asyncio.Lock.
        self._pending_injections: dict[str, list[dict[str, str]]] = {}

        # Per-instance consecutive-call counter for ``get_instance_info`` throttling.
        # Reset on any non-gii tool/message — see ``ToolThrottleSlot`` in graph.py.
        self._gii_throttle: dict[str, int] = {}

        # Per-instance loop-breaker state for the general hallucination detector.
        # Each entry: ``{"count": int, "last_summary": str, "last_repair_at": str}``.
        # RAM-only (no DB persistence) — follows the ``_gii_throttle`` /
        # ``_pending_injections`` pattern. A restarted session starts fresh.
        # Cleanup follows the same 5-path pattern as ``_gii_throttle``; Phase 3
        # wires those paths up.
        self._loop_breaker_state: dict[str, dict] = {}

        # Context Injection Restructure — Phase 3 (B2 fix): per-instance
        # cache of the LAST ``(injection_text, injected_skill_ids)`` tuple
        # produced by :meth:`SkillInjectionService.inject_skills`. The
        # messaging path writes this on first attempt; the ``agent_node``
        # ``ContextSlot.assemble()`` reads it so a retry of the same user
        # message reuses the same matched skills (otherwise the retry
        # would either re-run the search or, after the refactor, lose
        # skills entirely — see plan risk register B3).
        #
        # Mirrors the ``_pending_injections`` indirection pattern so the
        # messaging path (which holds the compiled graph but no slot
        # reference) can hand the result to the ``agent_node``-side slot
        # through the manager. Stored as ``None`` for "search ran but
        # yielded no injection" so the retry short-circuits without
        # re-running the search; absent key means "first attempt never
        # ran the search" (retry should re-run).
        self._context_skill_results: dict[
            str, tuple[str | None, list[str]] | None
        ] = {}

        # NEW: per-instance user-message counter for ``skill_search_interval``
        # gating. Mirrors ``_context_skill_results`` — same lifetime, same
        # defensive cleanup in :meth:`_cleanup_instance_state`, the TTL sweep,
        # and the dead-task branch. The messaging path calls
        # :meth:`get_and_increment_skill_search_count` on every new user
        # message to decide whether the expensive skill search should run,
        # then :meth:`reset_skill_search_count` immediately after a real
        # search so the next ``interval - 1`` messages can reuse the cache.
        self._skill_search_message_counts: dict[str, int] = {}

        # NEW: per-instance marker — the last ``_context_skill_results``
        # write came from an EXPLICIT ``load_skill`` (``<meta>`` tag) path,
        # not an auto-search. The ``skill_search_interval`` gate checks this
        # so an explicit load does NOT feed the interval cache — the next
        # ordinary message must run a fresh auto-search, not reuse the
        # explicit result. Cleared in the same 3 cleanup sites as
        # ``_context_skill_results`` and ``_skill_search_message_counts``.
        self._explicit_skill_loaded: set[str] = set()

        # NEW: EventBus for hybrid event delivery (DB + streaming)

        # NEW: Source repository for source config and session mapping management
        # Must be created before SourceRegistry
        self._source_repository = create_source_repository(engine=self._engine, create_tables=False)

        # NEW: MCP Server repository for MCP server configuration storage
        self._mcp_server_repository = create_mcp_server_repository(engine=self._engine, create_tables=False)

        # Skill Bank — standalone user-facing CRUD (NOT gated by skill_evolution)
        self._skill_bank_repo = create_skill_bank_repository(
            engine=self._engine, create_tables=False
        )

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

        # Per-instance in-memory todo state for the todo tool surface
        self._todo_manager = TodoManager()

        # Per-instance in-memory question-pack state for the question tool
        # surface. QuestionManager mirrors TodoManager's threading.Lock +
        # dict-keyed-by-instance_id pattern. Question packs live for the
        # lifetime of the daemon process — there is no DB persistence.
        self._question_manager = QuestionManager()

        # Per-instance "pause requested by the question tool" flag. The
        # conditional post-tools edge in ``daemon.graph.build_instance_graph``
        # reads this flag; when True, the graph routes to
        # ``question_pause_node`` which sets the deferred-pause marker (see
        # below) and clears the flag in its ``finally`` block. Cleared on
        # terminate / release / hard-delete via ``_cleanup_instance_state``.
        self._question_pause_requested: dict[str, bool] = {}

        # C2 fix — per-instance "deferred question pause" marker.
        # ``question_pause_node`` (which runs INSIDE the graph task) sets
        # this marker; the actual ``pause_instance_cascade`` invocation
        # runs from the post-graph completion path in
        # ``daemon.services.instance_messaging`` AFTER ``_graph_tasks`` is
        # popped, so there is no graph task to self-cancel. The
        # conditional-edge flag above decides whether the graph routes to
        # ``question_pause_node`` at all; this set carries the result
        # forward to the post-graph code that performs the cascade.
        self._deferred_question_pause: set[str] = set()

        # Watchover — per-instance watchover enabled flag is read from
        # ``instance_metadata`` JSONB (DB-backed, no in-memory dict needed).
        # This in-memory set carries the deferred termination marker from
        # ``watchover_terminate_node`` (which runs INSIDE the graph task) to
        # the post-graph completion path, mirroring the C2-safe deferred
        # pattern used by ``_deferred_question_pause`` above. The cascade
        # MUST NOT run inside the graph task (self-cancel / torn-state bug).
        # The post-graph consumer runs in ``instance_messaging.py``
        # (``_drain_deferred_watchover_terminate``).
        self._deferred_watchover_terminate: set[str] = set()

        # Watchover activation/deactivation service (Phase 3, T3.4-T3.6).
        # Holds no I/O state — it delegates everything to the manager
        # facade below. Constructed here so the router can call
        # ``manager.enable_watchover_lifecycle(...)`` /
        # ``manager.disable_watchover_lifecycle(...)``.
        from .services.watchover_service import WatchoverService

        self._watchover_service = WatchoverService(self)

        self.source_dispatcher = ResponseDispatcher(
            registry=self.source_registry,
            subscriber_id="response_dispatcher"
        )
        self._source_cleanup: SourceCleanup | None = None

        # NEW: Project repository for project context injection
        # Using the new repository layer with proper transaction management
        self._project_repository = create_project_repository(engine=self._engine, create_tables=False)

        # Skill Evolution Phase 2: skill repositories (Phase 1 schema already created via SQLModel.metadata.create_all)
        if self.config.skill_evolution is not None:
            self._skill_repo = create_skill_repository(engine=self._engine, create_tables=False)
            self._skill_lineage_repo = create_skill_lineage_repository(engine=self._engine, create_tables=False)
            self._skill_embedding_repo = create_skill_embedding_repository(engine=self._engine, create_tables=False)
        else:
            self._skill_repo = None
            self._skill_lineage_repo = None
            self._skill_embedding_repo = None

        # Project Blueprint: blueprint repository (Phase 1 schema created via SQLModel.metadata.create_all)
        self._blueprint_repo = create_blueprint_repository(engine=self._engine, create_tables=False)
        # Phase 2 / C3: pending-experience queue repository. The
        # ``project_blueprint_pending_updates`` table is auto-created
        # by ``SQLModel.metadata.create_all`` at line 439 because the
        # model is registered via the import above. The repo itself
        # is constructed regardless of any flag — the smart-scan
        # trigger (see Item 4) checks ``get_pending_count`` on every
        # nudge, so the repo must be available for read-only consumers
        # even when the blueprinter is disabled.
        self._blueprint_pending_repo = create_blueprint_pending_repository(
            engine=self._engine, create_tables=False,
        )

        # C7 (Phase 3): BlueprintTriggerCoordinator — the single
        # chokepoint for ALL blueprint build enqueuing. Lease is
        # persisted as project metadata. ``_job_queue_service`` is
        # late-bound via ``set_job_queue_service()`` below because it
        # is not constructed yet at this point in the init sequence.
        from .services.blueprint_trigger_coordinator import (
            BlueprintTriggerCoordinator,
        )
        self._blueprint_trigger_coordinator = BlueprintTriggerCoordinator(
            project_repository=self._project_repository,
            job_queue_service=None,
        )

        # G7 (C6): one-core-per-project DB constraint. Called here — AFTER
        # ``self._blueprint_repo`` and ``self._project_repository`` are
        # assigned — so the auto-dedup pre-flight can reach them. The
        # helper itself catches and logs errors so a constraint failure
        # cannot crash startup; the app-level UX guard in
        # ``BlueprintRepository.create`` is the safety net.
        self._ensure_blueprint_g7_unique_index()
        # Backfill: opt in existing projects that already have blueprints
        self._backfill_blueprint_active()

        # ── Project Blueprint: embedding repo + service (G4 fix) ───────
        # INDEPENDENT of skill_evolution. Operates on the same
        # ``project_blueprint_triggers`` table, but is constructed
        # whenever a blueprint embedding model is configured.
        # skill_evolution is NOT a prerequisite.
        _blueprint_embedding_configured = (
            getattr(self.config.blueprint, "embedding_model", None) is not None
        )
        if _blueprint_embedding_configured:
            self._blueprint_embedding_repo = create_blueprint_embedding_repository(
                engine=self._engine, create_tables=False,
            )
            # ``base_url_backup`` is threaded through the blueprint
            # embedding service's llm_config for HA-failover coverage of
            # this secondary LLM invocation; the actual failover wiring
            # here is a follow-up (see FailoverController note in
            # daemon/llm_error_classifier.py). The primary retry path
            # lives in ``build_instance_llms`` (daemon/graph.py) and is
            # fully wired.
            blueprint_llm_config: dict[str, Any] = {
                "base_url": self.config.llm.base_url,
                "base_url_backup": self.config.llm.base_url_backup,
                "api_key": self.config.llm.api_key,
                "model": self.config.llm.model,
                "model_vision": self.config.llm.model_vision,
                "temperature": self.config.llm.temperature,
                "request_timeout": self.config.llm.request_timeout,
            }
            self._blueprint_embedding_service = SkillEmbeddingService(
                config=self.config.blueprint,  # BlueprintConfig, NOT skill_evolution
                embedding_repo=self._blueprint_embedding_repo,
                llm_config=blueprint_llm_config,
            )
        else:
            self._blueprint_embedding_repo = None
            self._blueprint_embedding_service = None
        # NOTE: ``BlueprintMatcher`` construction is deferred to after the
        # skill_evolution block below (it lives there historically); we
        # just gate it on the blueprint embedding service, not the skill
        # service. See the ``BlueprintMatcher`` block ~line 940.

        # Keep backward compatible name for tools
        self.project_store = self._project_repository

        # ── OpenCode session integration (separate engine) ──────────────────
        # Dedicated engine for opencode sessions — separate file at
        # {data_dir}/opencode_sessions.db (per Critical Note: separate persistence).
        # Uses create_engine_from_config consistent with the main engine above
        # (handles SQLite pragmas: WAL mode, busy_timeout, foreign_keys=ON,
        # check_same_thread=False automatically).
        # Table created via __table__.create() inside the factory — creates
        # ONLY the opencode_sessions table, NOT all ensemble tables.
        opencode_db_path = self.data_dir / "opencode_sessions.db"
        self._opencode_engine = create_engine_from_config(
            DatabaseConfig.sqlite(db_path=str(opencode_db_path))
        )
        self._opencode_session_repository = create_opencode_session_repository(self._opencode_engine)
        self._opencode_registry = OpenCodeSessionRegistry(
            repository=self._opencode_session_repository,
        )
        logger.info(f"OpenCode session registry initialized at {opencode_db_path}")

        # NEW: Optional JobQueueService reference (set via set_job_queue_service)
        self._job_queue_service: Any = None
        self._job_queue_mgmt_service: Any = None
        self._dead_letter_service: Any = None
        # Phase 5: SkillJobDispatcher — constructed by
        # ``set_job_queue_service`` once the JobQueueService and its
        # ``_queue_repo`` are reachable. Initialized to None so
        # ``getattr(manager, '_skill_job_dispatcher', None)`` calls
        # succeed during the window between ``__init__`` and
        # ``set_job_queue_service``.
        self._skill_job_dispatcher: Any = None

        # NEW: Optional notification broadcaster (set via set_notification_broadcaster)
        self._notification_broadcaster: Any = None

        # Worker pool for message queue redesign
        self._worker_pool: WorkerPool | None = None
        self._task_processor: TaskProcessor | None = None
        self._stale_recovery: StaleTaskRecovery | None = None

        # Execution Gate: the single owner of graph.astream per
        # thread_id. Now a per-process asyncio.Lock (see
        # ``daemon/services/execution_gate.py``) — the two physical
        # dispatchers (MessageJobHandler and ProcessMessageProcessor)
        # can never run astream concurrently for the same instance
        # because all callers funnel through MainLoopBridge onto the
        # main event loop. Replaces the previous DB-backed lease
        # (``instance_execution_leases`` table) which is no longer
        # used at runtime.
        from .services.execution_gate import ExecutionGateService
        self._execution_gate = ExecutionGateService()

        # Shutdown flag for graceful shutdown
        self._shutting_down = False

        # Background tasks for cleanup operations (tracked for cancellation on shutdown)
        self._background_tasks: list[asyncio.Task] = []

        # Warm-up pool background task reference
        self._warmup_task: asyncio.Task | None = None

        # Maintenance service for periodic cleanup tasks
        self._maintenance_service: MaintenanceService | None = None

        # Bootstrap built-in MCP servers
        self._bootstrap_builtin_servers()

        # Bootstrap default infra asset types (Phase 1.5). Seeds the
        # global ``infra_asset_types`` registry with the 9 built-in
        # type definitions on first run, and upserts in place on
        # subsequent startups so schema drift propagates safely.
        self._bootstrap_infra_types()

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

        # MCP service for managing MCP tool lifecycle
        from .services.mcp_service import McpService
        self._mcp_service = McpService(manager=self)

        # Skill Evolution services (Phase 2 — wire through to manager facade)
        if self.config.skill_evolution is not None:
            # ``base_url_backup`` is threaded through the skill evolution
            # services' llm_config for HA-failover coverage of these
            # secondary LLM invocations; the actual failover wiring here
            # is a follow-up (see FailoverController note in
            # daemon/llm_error_classifier.py). The primary retry path
            # lives in ``build_instance_llms`` (daemon/graph.py) and is
            # fully wired.
            skill_llm_config: dict[str, Any] = {
                "base_url": self.config.llm.base_url,
                "base_url_backup": self.config.llm.base_url_backup,
                "api_key": self.config.llm.api_key,
                "model": self.config.llm.model,
                "model_vision": self.config.llm.model_vision,
                "temperature": self.config.llm.temperature,
                "request_timeout": self.config.llm.request_timeout,
            }
            self._skill_embedding_service = SkillEmbeddingService(
                config=self.config.skill_evolution,
                embedding_repo=self._skill_embedding_repo,
                llm_config=skill_llm_config,
            )
            self._skill_store_service = SkillStoreService(
                skill_repo=self._skill_repo,
                lineage_repo=self._skill_lineage_repo,
                embedding_service=self._skill_embedding_service,
            )
            self._skill_search_service = SkillSearchService(
                skill_repo=self._skill_repo,
                embedding_repo=self._skill_embedding_repo,
                embedding_service=self._skill_embedding_service,
                llm_config=skill_llm_config,
                config=self.config.skill_evolution,
            )
        else:
            self._skill_embedding_service = None
            self._skill_store_service = None
            self._skill_search_service = None

        # Project Blueprint: matching engine (BM25 + vector fusion).
        # G4 fix: construct whenever the BLUEPRINT embedding service is
        # available, REGARDLESS of whether skill_evolution is configured.
        # ``self._blueprint_matcher`` is no longer gated on
        # ``self._skill_embedding_service is not None`` (rev 1 mistake —
        # it coupled blueprints to skill_evolution).
        if self._blueprint_embedding_service is not None:
            self._blueprint_matcher = BlueprintMatcher(
                repository=self._blueprint_repo,
                embedding_service=self._blueprint_embedding_service,
                config=self.config.blueprint,
            )
        else:
            self._blueprint_matcher = None
            logger.info(
                "BlueprintMatcher not initialized — "
                "BLUEPRINT_EMBEDDING_MODEL not set"
            )

        # Project Blueprint Phase 4: rate limiter + circuit breaker for
        # the blueprinter agent. Caps revisions per hour per project and
        # trips a circuit breaker after N consecutive failures.
        # In-process only — no persistence (state resets on restart,
        # acceptable for a background maintenance agent).
        self._blueprint_rate_limiter = BlueprintRateLimiter()

        # Skill Evolution Phase 4: Tier 0 metrics recorder + Tier 1
        # trigger engine. The four new repositories (``usage``,
        # ``trigger``, ``ab_test``) and the two services
        # (``_skill_metrics_service``, ``_skill_trigger_engine``) are
        # only created when ``skill_evolution`` is configured — same
        # graceful-disabled pattern as the Phase 2 services above.
        # The metrics service also needs ``_instance_repository`` so
        # it can read/clear the ``last_injected_skill_ids`` metadata
        # key the Phase 3 injection service stamps onto instances.
        #
        # The CAPTURED-flow eligibility check (Phase 5) needs the
        # evolution service and a resolver that maps ``agent_id`` to
        # ``AgentMetadata`` (for the ``skill_injection`` gate). The
        # evolution service depends on the metrics service (for
        # ``get_ab_comparison_stats``) and the metrics service
        # depends on the evolution service (for ``check_and_capture``)
        # — a constructor-level cycle. We break it by:
        #
        #   1. Constructing the metrics service with
        #      ``evolution_service=None`` and the registry-backed
        #      ``agent_id_resolver``.
        #   2. Constructing the evolution service with
        #      ``metrics_service=self._skill_metrics_service``.
        #   3. Wiring the back-reference via
        #      ``set_evolution_service``.
        if self.config.skill_evolution is not None:
            self._skill_usage_repo = create_skill_usage_repository(
                engine=self._engine, create_tables=False
            )
            self._skill_trigger_repo = create_skill_trigger_repository(
                engine=self._engine, create_tables=False
            )
            self._skill_ab_test_repo = create_skill_ab_test_repository(
                engine=self._engine, create_tables=False
            )

            # Registry-backed resolver. Returns ``AgentMetadata | None``
            # for the given ``agent_id``. Used by the CAPTURED check
            # to gate on ``skill_injection``. Wrapped in a closure so
            # the metrics service stays decoupled from the registry
            # module (and trivially mockable in tests).
            def _resolve_agent_meta(
                agent_id: str,
                version_tag: str | None = None,
                instance_id: str | None = None,
            ) -> Any:
                # C1 fix: prefer the versioned meta when ``version_tag``
                # is supplied so the ``skill_injection`` gate matches
                # the caller's resolved variant. Falls back to the
                # base (resolved) meta when the tag is missing or the
                # version is unknown — same ``get_version() or
                # get_resolved()`` fallback pattern used by
                # ``_apply_tool_filter`` and
                # ``_check_team_membership``.
                #
                # W2 fix: when ``version_tag`` is not supplied but
                # ``instance_id`` is, look up the instance to read its
                # bound ``agent_tag`` so v2/etc. callers gate on their
                # v2 ``skill_injection`` flag rather than the base
                # one. ``record_task_completion`` now passes
                # ``instance_id`` so the resolver is version-aware
                # without changing ``SkillMetricsService.record_task_completion``'s
                # public signature.
                registry = get_registry()
                resolved_tag = version_tag
                if resolved_tag is None and instance_id is not None:
                    try:
                        _instance = self._instance_repository.get(
                            instance_id
                        )
                        if _instance is not None:
                            resolved_tag = getattr(
                                _instance, "agent_tag", None
                            )
                    except Exception as _resolve_exc:  # pragma: no cover
                        logger.debug(
                            f"_resolve_agent_meta: instance lookup "
                            f"failed for {instance_id[:8]}...: "
                            f"{_resolve_exc}"
                        )
                return (
                    registry.get_version(agent_id, resolved_tag)
                    or registry.get_resolved(agent_id)
                )

            self._skill_metrics_service = SkillMetricsService(
                usage_repo=self._skill_usage_repo,
                skill_repo=self._skill_repo,
                trigger_repo=self._skill_trigger_repo,
                ab_test_repo=self._skill_ab_test_repo,
                config=self.config.skill_evolution,
                instance_repo=self._instance_repository,
                evolution_service=None,  # back-ref set below
                agent_id_resolver=_resolve_agent_meta,
            )
            self._skill_trigger_engine = SkillTriggerEngine(
                trigger_repo=self._skill_trigger_repo,
                metrics_service=self._skill_metrics_service,
            )

            # Skill Evolution Phase 3: injection service (depends
            # on both the Phase 2 ``_skill_search_service`` and the
            # Phase 4 ``_skill_ab_test_repo`` so it's initialized
            # here, after both blocks). Renders the search results
            # into an injectable ``HumanMessage`` body and handles
            # deterministic A/B variant selection.
            self._skill_injection_service = SkillInjectionService(
                search_service=self._skill_search_service,
                config=self.config.skill_evolution,
                ab_test_repo=self._skill_ab_test_repo,
                skill_repo=self._skill_repo,
            )

            # Skill Evolution Phase 5: evolution service (Tier 2/3
            # analysis, evolution, CAPTURED gate, A/B resolution).
            # Constructed AFTER the metrics service so it can hold a
            # back-reference; the metrics service then receives the
            # evolution service via ``set_evolution_service`` to close
            # the loop. ``llm_config`` reuses the same dict pattern as
            # the Phase 2 services above.
            self._skill_evolution_service = SkillEvolutionService(
                skill_repo=self._skill_repo,
                lineage_repo=self._skill_lineage_repo,
                usage_repo=self._skill_usage_repo,
                embedding_service=self._skill_embedding_service,
                metrics_service=self._skill_metrics_service,
                ab_test_repo=self._skill_ab_test_repo,
                config=self.config.skill_evolution,
                llm_config=skill_llm_config,
            )
            self._skill_metrics_service.set_evolution_service(
                self._skill_evolution_service
            )
            # Phase 4: SkillCloneService — bridges skill_bank templates
            # to project-scoped skills. Used by the injection pipeline
            # to clone-on-miss and by Phase 5's auto-load builder
            # (``_build_auto_load_block`` in context_messages) to
            # materialize the auto-load set.
            self._skill_clone_service = SkillCloneService(
                skill_repo=self._skill_repo,
                skill_bank_repo=self._skill_bank_repo,
                embedding_service=self._skill_embedding_service,
            )
            # Phase 4 (W1 fix): wire the clone service back into the
            # injection service so ``inject_explicit_skill`` can use
            # the clone-on-miss path. This must come AFTER
            # ``_skill_clone_service`` is assigned (it's the dependency)
            # but is otherwise order-free — the injection service
            # uses the clone service lazily on the next inject call,
            # not at this point. Guarded with ``hasattr`` so test
            # managers that skip Phase 3 init don't crash here.
            if getattr(self, "_skill_injection_service", None) is not None:
                self._skill_injection_service.set_clone_service(
                    self._skill_clone_service
                )
        else:
            self._skill_usage_repo = None
            self._skill_trigger_repo = None
            self._skill_ab_test_repo = None
            self._skill_metrics_service = None
            self._skill_trigger_engine = None
            self._skill_injection_service = None
            self._skill_evolution_service = None
            self._skill_clone_service = None  # Phase 4: clone-on-miss service
            # ``_skill_job_dispatcher`` is initialized in
            # ``set_job_queue_service`` — guard against the rare
            # case where that setter is never called so attribute
            # lookups (``getattr(manager, '_skill_job_dispatcher',
            # None)``) still see ``None`` rather than ``AttributeError``.
            self._skill_job_dispatcher = None

        # Initialize MCP warm-up pool (non-blocking background warmup)
        self._init_warmup_pool()

    def get_blueprint_write_service(
        self,
        project_id: str,
    ) -> BlueprintWriteService:
        """Factory for the canonical write boundary (C5).

        Returns a :class:`BlueprintWriteService` bound to ``project_id``
        and the manager's blueprint subsystem. Used by the REST router,
        the blueprinter tools, and (in Phase 3) the admission
        coordinator. ALL blueprint writes route through this service so
        the five invariants (rate-limit, embed-before-commit, revision
        capture, atomic publish unit, rate-limit record) are enforced on
        every path.
        """
        return BlueprintWriteService(
            repository=self._blueprint_repo,
            embedding_repository=self._blueprint_embedding_repo,
            embedding_service=self._blueprint_embedding_service,
            rate_limiter=self._blueprint_rate_limiter,
            config=self.config.blueprint,
            project_id=project_id,
            manager=self,  # for save-plan metadata + future history hooks
        )

    def _bootstrap_builtin_servers(self) -> None:
        """Bootstrap built-in MCP servers on daemon startup.

        For each registered built-in server definition:
        1. Check if server is disabled via MCP_DISABLE_BUILT_IN_{NAME} env var
        2. If disabled:
           - If DB record exists → deactivate it (set is_active=False)
           - If no DB record → skip (don't create)
        3. If not disabled:
           - If no DB record → create with is_active=True
           - If DB record exists and is_builtin=True:
             - If schema version differs → update config (preserve is_active)
             - If is_active=False (previously disabled) → reactivate (set is_active=True)
             - Otherwise → no-op (idempotent)
           - If DB record exists and is_builtin=False → log warning, skip

        Fault-tolerant: per-server try/except, logs errors and continues.
        Idempotent: safe to run multiple times.
        """
        registry = get_mcp_registry()
        definitions = registry.get_all()
        registered_names = {d.name for d in definitions}
        if not definitions:
            logger.info("No built-in MCP servers registered")
            self._deactivate_orphaned_builtin_servers(registered_names)
            return

        logger.info(f"Bootstrapping {len(definitions)} built-in MCP servers...")

        for definition in definitions:
            try:
                # Check if server is disabled via env var (FIRST)
                if is_builtin_disabled(definition.name):
                    existing = self._mcp_server_repository.get_mcp_server_by_name(definition.name)
                    if existing is None:
                        logger.info(f"Built-in MCP server '{definition.name}' disabled (MCP_DISABLE_BUILT_IN_{definition.name.upper()}), skipping creation")
                        continue
                    elif existing.is_builtin:
                        self._mcp_server_repository.update_mcp_server(existing.id, is_active=False)
                        logger.info(f"Built-in MCP server '{definition.name}' disabled (MCP_DISABLE_BUILT_IN_{definition.name.upper()}), deactivated existing record")
                    else:
                        logger.warning(f"Skipping built-in MCP server '{definition.name}': a user-created server with this name already exists")
                    continue

                # Module availability pre-check — runs AFTER disable so user
                # intent wins. If a DB record exists for a now-unavailable
                # builtin, the disable path above already deactivated it; we
                # just need to NOT create a fresh record.
                if not definition.is_available():
                    pkg = definition.required_package
                    if pkg is not None:
                        logger.info(
                            f"Builtin '{definition.name}' skipped — package "
                            f"'{pkg}' not installed "
                            f"(pip install {pkg})"
                        )
                    else:
                        logger.info(
                            f"Builtin '{definition.name}' skipped — "
                            f"missing environment configuration "
                            f"(see server definition docs)"
                        )
                    continue

                default_config = definition.build_config({})
                schema_dicts = definition.get_config_schema()
                schema_version = definition.schema_version

                existing = self._mcp_server_repository.get_mcp_server_by_name(definition.name)

                if existing is None:
                    # Create new built-in server
                    self._mcp_server_repository.create_mcp_server(
                        name=definition.name,
                        description=definition.description,
                        config=default_config,
                        is_builtin=True,
                        config_schema=schema_dicts,
                        config_schema_version=schema_version,
                    )
                    logger.info(f"Created built-in MCP server: {definition.name}")
                elif existing.is_builtin:
                    # Check if previously disabled (is_active=False) and reactivate
                    if not existing.is_active:
                        self._mcp_server_repository.update_mcp_server(
                            existing.id,
                            is_active=True,
                        )
                        logger.info(f"Reactivated built-in MCP server: {definition.name}")
                    # Check schema version drift
                    if existing.config_schema_version != schema_version:
                        self._mcp_server_repository.update_mcp_server(
                            existing.id,
                            config=default_config,  # refresh stale config with rebuilt defaults
                            config_schema=schema_dicts,
                            config_schema_version=schema_version,
                        )
                        logger.warning(
                            "Built-in MCP server '%s' config reset to defaults due to schema version change (%s → %s)",
                            definition.name,
                            existing.config_schema_version,
                            schema_version,
                        )
                    else:
                        logger.debug(f"Built-in MCP server already exists: {definition.name}")
                else:
                    # User-created server with same name — skip
                    logger.warning(
                        f"Skipping built-in MCP server '{definition.name}': "
                        f"a user-created server with this name already exists"
                    )

            except Exception as e:
                logger.error(f"Failed to bootstrap built-in MCP server '{definition.name}': {e}")
                continue  # Fault-tolerant: continue with other servers

        self._deactivate_orphaned_builtin_servers(registered_names)

        logger.info("Built-in MCP server bootstrap complete")

    def _deactivate_orphaned_builtin_servers(self, registered_names: set[str]) -> None:
        """Deactivate built-in MCP server DB rows no longer in the registry.

        When a builtin server definition is removed from the codebase
        (e.g. ``openspace`` was deleted alongside its integration), the
        bootstrap loop above never visits it, leaving a stale ``is_active=True``
        row that the warmup pool and schema discovery then try to spawn.
        This sweep deactivates those orphans so the rest of the system
        treats them like any other inactive server.

        Idempotent: only touches rows that are both ``is_builtin=True`` and
        still active. User-created servers (``is_builtin=False``) are never
        touched, so an operator re-adding a builtin name as a custom server
        is safe.

        Args:
            registered_names: Names of builtins currently registered in the
                ``BuiltinServerRegistry``.
        """
        try:
            all_builtin_rows = self._mcp_server_repository.list_mcp_servers(
                is_active=True, is_builtin=True, limit=500
            )
        except Exception as e:
            logger.warning(
                f"Failed to scan for orphaned built-in MCP servers: {e}"
            )
            return

        orphaned = [
            row for row in all_builtin_rows
            if row.name not in registered_names
        ]
        for row in orphaned:
            self._mcp_server_repository.update_mcp_server(row.id, is_active=False)
            logger.info(
                f"Deactivated orphaned built-in MCP server "
                f"'{row.name}' (definition removed from registry)"
            )

    def _bootstrap_infra_types(self) -> None:
        """Seed default infra asset type definitions on daemon startup.

        Delegates to
        :meth:`SQLModelInfraRepository.bootstrap_default_types` which
        idempotently upserts the 9 built-in types from
        :data:`~daemon.repositories.infra.types.INFRA_TYPE_DEFINITIONS`
        (Phase 1.5 of the infra info storage design). On a fresh
        database all 9 are inserted; on subsequent startups the
        upsert path bumps ``updated_at`` so schema drift between
        daemon versions propagates.

        Fault-tolerant: a failure here is logged but does not block
        daemon startup — the rest of the system can still run, and
        the missing types can be re-seeded by calling
        ``bootstrap_default_types`` again from a maintenance path
        (e.g. a CLI tool) once the underlying issue is fixed.
        """
        try:
            result = self._infra_repository.bootstrap_default_types()
        except Exception as e:
            logger.error(
                f"Failed to bootstrap default infra asset types: {e}. "
                f"The daemon will continue without them — investigate "
                f"and re-run the bootstrap once the root cause is fixed."
            )
            return

        if result.new_count > 0:
            logger.info(
                f"Seeded {result.new_count} new infra asset types "
                f"({result.updated_count} updated) on startup"
            )
        else:
            logger.debug(
                f"Infra asset types already registered: "
                f"{len(result.registered)} total, {result.updated_count} updated"
            )

    def _init_warmup_pool(self) -> None:
        """Initialize and warm up the MCP connection pool.

        Registers all built-in STDIO servers with the pool and starts background
        warmup. Does NOT block startup — warmup runs as a background task.

        Skips servers that are inactive in the database (either disabled via
        env var or manually deactivated).
        """
        if not self.config.mcp_pool.enabled:
            logger.info("MCP warm-up pool disabled by config")
            return

        # Build pool with config-injected tool_call_timeout, then register it as
        # the module-level singleton so other call sites (e.g. _warmup_and_report,
        # _drain_warmup_pool) observe the same instance.
        pool = McpWarmupPool(tool_call_timeout=self.config.mcp_pool.tool_call_timeout)
        _warmup_pool_module._mcp_warmup_pool = pool

        registry = get_mcp_registry()

        for definition in registry.get_all():
            name = definition.name

            # Skip servers disabled via env var (e.g.
            # ``MCP_DISABLE_BUILT_IN_WEBFETCH=true``). When disabled,
            # ``_bootstrap_builtin_servers`` does NOT create a DB record,
            # so the existing "is_active=False" check below would not
            # catch it — we have to consult ``is_builtin_disabled`` first.
            if is_builtin_disabled(name):
                logger.info(
                    f"MCP server '{name}' disabled (env var), "
                    f"skipping warmup pool registration"
                )
                continue

            # Module availability pre-check (DEBUG here, INFO at bootstrap).
            # Bootstrap is the canonical "user can act on this" event with
            # actionable install hint; warmup is downstream of bootstrap so the
            # INFO already fired there — DEBUG here avoids a duplicate notice
            # in the operator's log.
            if not definition.is_available():
                logger.debug(
                    f"MCP server '{name}' unavailable (module not installed), "
                    f"skipping warmup pool registration"
                )
                continue

            config_dict = definition.build_config({})
            if config_dict.get("transport") != "stdio":
                continue

            # Skip inactive servers (disabled via env var or manually deactivated)
            existing = self._mcp_server_repository.get_mcp_server_by_name(name)
            if existing is not None and not existing.is_active:
                logger.debug(f"Skipping warmup for inactive MCP server: {name}")
                continue

            pool_size = self.config.mcp_pool.servers.get(
                name, self.config.mcp_pool.default_pool_size
            )
            stdio_config = McpStdioConfig(**config_dict)
            # Per-server timeout override. Built-in servers that run
            # long-running tools (e.g. an agent-execution tool that may
            # run for several minutes) opt in by overriding
            # ``tool_call_timeout`` on their definition; servers without
            # an override return ``None`` and fall back to the pool-wide
            # default. ``getattr`` (not direct attribute access) keeps
            # this resilient if a third-party definition subclasses the
            # ABC without re-declaring the property.
            server_timeout = getattr(definition, "tool_call_timeout", None)
            pool.register_server(
                name,
                stdio_config,
                pool_size=pool_size,
                tool_call_timeout=server_timeout,
            )

        # Wire pool into MCP service for use during tool execution
        self._mcp_service.set_warmup_pool(pool)

        # Schedule warmup in background — do not block startup
        # Note: This uses asyncio.get_running_loop() which must be called from an async context.
        # If called during __init__ (sync), we defer to initialize() instead.
        try:
            loop = asyncio.get_running_loop()
            # Store task for tracking
            self._warmup_task = loop.create_task(self._warmup_and_report())
            logger.info(
                f"MCP warm-up pool initialized: {len(pool._configs)} server(s) registered, "
                f"warmup running in background"
            )
        except RuntimeError:
            # No running loop (during __init__), defer warmup to initialize()
            logger.debug("Deferring MCP warm-up pool to initialize()")

    async def _warmup_and_report(self) -> None:
        """Background task to warm up pool and start health checks.

        After the pool itself is warm, eagerly prime the MCP service's
        in-memory schema cache for every active server. This moves the
        per-server cold-discovery cost (npx/uvx subprocess + list_tools
        RPC) out of the first user-initiated ``spawn_instance`` path so
        the very first instance after startup is fast, not just the
        second and later ones.
        """
        pool = get_mcp_warmup_pool()
        try:
            await pool.warmup()
            status = pool.get_status()
            logger.info(f"MCP warm-up pool ready: {status}")
            pool.start_health_check(self.config.mcp_pool.health_check_interval)
        except Exception as e:
            logger.warning(f"MCP warm-up pool warmup failed: {e}")
            return

        # Eagerly prime the schema cache so the first instance spawn is
        # an in-memory hit. Best-effort; failures are logged and don't
        # block startup.
        try:
            await self._mcp_service.eager_warm_schemas()
        except Exception as e:
            logger.warning(f"MCP schema eager warm failed: {e}")

    async def _drain_warmup_pool(self) -> None:
        """Drain the MCP warm-up pool during shutdown."""
        pool = get_mcp_warmup_pool()
        try:
            await asyncio.wait_for(pool.drain(), timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning("MCP pool drain timed out, forcing cleanup")
        except Exception as e:
            logger.warning(f"Error draining MCP pool: {e}")

    @property
    def checkpointer(self):
        """Get the checkpointer adapter instance.

        The checkpointer is created lazily on first access and but it must be initialized explicitly via initialize().

        Returns:
            ``CheckpointerAdapter`` wrapping either an ``AsyncSqliteSaver``
            (SQLite backend) or an ``AsyncPostgresSaver`` (PostgreSQL backend).
            Use ``.raw_saver`` to access the underlying LangGraph saver for
            ``aget`` / ``aput`` / ``alist`` operations.
        """
        return self._checkpointer

    @property
    def engine(self):
        """Public read-only access to the database engine.

        Returns:
            The shared SQLAlchemy Engine instance used by all repositories.
        """
        return self._engine

    @property
    def db_connection_repository(self):
        """Public read-only access to the DB-connection registry repository.

        Used by the database tool layer (``create_db_tools``) and HTTP routes
        to manage ``DbConnectionConfig`` rows. Constructed in ``__init__`` with
        the shared engine; no credential decryption happens here — that's
        the responsibility of the tool layer (matches the
        ``routers/sources.py`` encryption pattern).
        """
        return self._db_connection_repository

    @property
    def db_pool_manager(self):
        """Public read-only access to the shared :class:`ConnectionPoolManager`.

        This is the singleton pool owner for all DB-connection tool calls.
        Kept at the manager level (C3 fix) so N instances share M pools
        rather than multiplying them. Disposed in ``shutdown()`` —
        see the ``dispose_db_pools`` step.
        """
        return self._db_pool_manager

    @property
    def infra_repository(self):
        """Public read-only access to the shared :class:`SQLModelInfraRepository`.

        Used by the infrastructure tool layer
        (:func:`daemon.tools.infra.create_infra_tools`) to manage
        ``InfraAsset`` / ``InfraAssetType`` / ``InfraAssetHistory`` rows.
        Constructed once in ``__init__`` with the shared engine so all
        instances share one repository bound to the same engine —
        preventing per-instance engine allocation and lock contention
        (C3). The default type registry is seeded by
        :meth:`_bootstrap_infra_types` (with try/except fault
        tolerance) later in ``__init__``.

        There is exactly one source of truth for this repository
        (C1 fix) — the ``_infra_repository`` attribute set in
        ``__init__``.
        """
        return self._infra_repository

    @property
    def shared_meta_kv_repo(self) -> "SharedMetaKVRepository":
        """Public read-only access to the shared :class:`SharedMetaKVRepository`.

        Used by the Shared Meta KV layer to read/write
        rows in the ``shared_context_metadata`` table (table name kept
        for backwards compatibility). Constructed once in
        ``__init__`` with the shared engine so all callers share one
        repository bound to the same engine — preventing per-call
        engine allocation and lock contention (C3, matching the
        ``infra_repository`` wiring immediately above).
        """
        return self._shared_meta_kv_repo

    @property
    def credential_manager(self):
        """Public read-only access to the shared :class:`CredentialManager`.

        Injected from ``app.state.credential_manager`` in production so all
        long-lived components share one Fernet key handle; falls back to a
        freshly constructed one for tests that build ``InstanceManager``
        directly without going through the FastAPI lifespan.
        """
        return self._credential_manager

    @property
    def ensemble_config(self) -> "EnsembleConfig | None":
        """Public read-only access to the ``EnsembleConfig`` selecting the DB backend.

        Returns:
            The :class:`EnsembleConfig` instance passed to ``__init__``, or
            ``None`` if the manager was constructed without one (legacy
            SQLite-only call sites). Used by services that need to
            introspect the current backend (e.g. :class:`MigrationWorker`
            checking ``is_sqlite`` before kicking off a hot-swap) and by
            HTTP endpoints that report the active backend in ``/health``.
        """
        return self._ensemble_config

    @property
    def data_dir(self) -> "Path":
        """Directory containing the SQLite database files and ``ensemble.json``.

        The migration worker uses this when rewriting ``ensemble.json`` so
        the file lands next to the database files (where the lifespan
        code in ``api.py`` originally loaded it from). Computed from
        ``self.db_path.parent`` because the SQLite path config is the
        only data-directory anchor the manager has after construction.
        """
        return self.db_path.parent

    @property
    def opencode_registry(self) -> "OpenCodeSessionRegistry":
        """Public read-only access to the opencode session registry.

        Used by ``daemon/tools/external_opencode.py`` to access session
        state from agent tool calls.
        """
        return self._opencode_registry

    @property
    def write_guard(self) -> WritePauseGuard:
        """Public read-only access to the write-pause guard.

        Services / tools that open a ``Session`` directly (the 6 sites
        being migrated in Phase 3) wrap it in
        ``WriteGuardSession(Session(engine), manager.write_guard)`` so
        the migration entry point can drain in-flight writes before
        swapping the underlying engine.

        Returns:
            The shared :class:`WritePauseGuard` instance owned by this
            manager.
        """
        return self._write_guard

    @property
    def execution_gate(self) -> "ExecutionGateService":
        """Public read-only access to the Execution Gate.

        The Execution Gate is the single owner of ``graph.astream``
        per ``thread_id`` (== ``instance_id``). Both dispatchers
        (MessageJobHandler on the JobQueue side, ProcessMessageProcessor
        on the WorkerPool side) call ``gate.run(...)`` to acquire the
        per-instance lock before driving the langgraph thread. The
        lock blocks concurrent callers on the same event loop; there
        is no contention return path.

        Always available after ``__init__`` completes.

        Returns:
            The :class:`ExecutionGateService` instance owned by this
            manager.
        """
        return self._execution_gate

    @property
    def is_write_paused(self) -> bool:
        """Return ``True`` if ``pause_writes()`` is currently in effect.

        The migration entry point (and tests) can poll this to
        coordinate other shutdown / recovery actions.
        """
        return self._write_guard.is_write_paused

    def pause_writes(self) -> None:
        """Block new writes and wait for in-flight writes to drain.

        Phase 3 of the SQLite→PostgreSQL migration calls this before
        swapping the underlying engine so no in-flight write lands on
        a half-migrated database. ``resume_writes()`` must be called
        once the new engine is in place.

        Blocks the calling thread until the in-flight write counter
        reaches zero. Safe to call from any thread; uses
        ``threading`` primitives only (no ``asyncio`` locks) because
        the codebase runs sync ``Session`` work inside
        ``asyncio.to_thread`` workers.
        """
        self._write_guard.pause_writes()

    def resume_writes(self) -> None:
        """Re-allow new writes after a migration.

        Counterpart to :meth:`pause_writes`. Wakes any thread that
        was blocked trying to open a new session and returns the
        guard to its default "writes allowed" state.
        """
        self._write_guard.resume_writes()

    async def initialize(self) -> None:
        """Initialize the checkpointer adapter.

        Must be called after SessionManager construction, typically in the FastAPI
        lifespan startup. This ensures the checkpointer is created within
        an async context.

        The backend is selected from ``self._ensemble_config``:

        - ``is_postgres`` → builds a ``PostgresCheckpointerAdapter`` via
          ``create_postgres_checkpointer`` (no file path; the PostgreSQL
          checkpointer connects to the configured DSN).
        - ``is_sqlite`` (default) → builds a ``SqliteCheckpointerAdapter`` wrapping
          an ``AsyncSqliteSaver`` at the path recorded in
          ``ensemble_config.sqlite.checkpoints_db``.

        Note: The checkpointer uses a separate database connection from the
        main application database to avoid SQLite lock contention. For
        PostgreSQL, the ``instances.db`` and ``checkpoints.db`` live in the
        same PostgreSQL server but are managed by independent connections.
        """
        self._loop = asyncio.get_running_loop()
        # ``get_checkpointer`` dispatches on ensemble_config.is_postgres and
        # returns the appropriate ``CheckpointerAdapter``. ``self._ensemble_config``
        # is guaranteed to be set by the lifespan (api.py) before ``initialize()``
        # is called, but fall back to a default SQLite config for safety so
        # tests calling ``manager.initialize()`` directly still work.
        if self._ensemble_config is not None:
            self._checkpointer = await get_checkpointer(self._ensemble_config)
        else:
            from daemon.ensemble_config import EnsembleConfig
            self._checkpointer = await get_checkpointer(EnsembleConfig())
        # NEW: Set event loop for CompletionRegistry (thread-safe notification)
        self._completion_registry.set_event_loop(self._loop)
        # NEW: Schedule periodic stale cleanup (every 10 minutes)
        self._background_tasks.append(asyncio.create_task(self._cleanup_stale_completions()))
        # NEW: Schedule periodic cleanup of paused instances exceeding TTL
        self._background_tasks.append(asyncio.create_task(self._cleanup_cached_instances()))
        # FIX: W3 — Wire deferred warmup (deferred from __init__ because no running loop)
        if self.config.mcp_pool.enabled and self._warmup_task is None:
            self._warmup_task = asyncio.create_task(self._warmup_and_report())
            logger.debug("MCP warmup task started from initialize()")

        # Eagerly warm tool metadata + tiktoken encoder in the background
        # so the first ``spawn_instance`` after startup doesn't pay the
        # one-time cost of importing ~10 tool modules, instantiating dummy
        # tool closures, scanning their docstrings, and importing tiktoken.
        # These are CPU-only and safe to defer; ``_ensure_tool_metadata_populated``
        # and the encoder itself are idempotent.
        self._background_tasks.append(
            asyncio.create_task(self._eager_warm_loader_caches())
        )

        # Initialize maintenance service with checkpoint cleanup
        self._maintenance_service = MaintenanceService(
            check_interval_minutes=self.config.persistence.maintenance_check_interval_minutes
        )
        self._maintenance_service.set_job_queue_service(self._job_queue_service)
        self._maintenance_service.set_request_registry(self._request_registry._requests)
        # NOTE: set_task_repository() is wired in setup_worker_pool() AFTER
        # self._task_repo is assigned (line ~2438). initialize() runs before
        # setup_worker_pool() per daemon/api.py startup order, so calling it
        # here would raise AttributeError.

        # Register checkpoint cleanup job
        checkpoint_cleanup = CheckpointCleanupJob(
            config=self.config.persistence,
            checkpointer=self._checkpointer,
            instance_repo=self._instance_repository,
            ui_prefs_repo=self._instance_ui_prefs_repo,
            on_instance_deleted=self._release_cached_instance,
        )
        self._maintenance_service.register(
            "checkpoint_cleanup",
            self.config.persistence.checkpoint_cleanup_interval,
            checkpoint_cleanup.execute,
        )

        # Skill Evolution Phase 4: seed the default Tier 1 trigger
        # rules + register the periodic metric-scan maintenance job.
        # Both are guarded by the ``skill_evolution`` config flag so a
        # daemon without the feature keeps its current behavior. The
        # seeding call is idempotent (matches by name) and the scan
        # handler is also tolerant of a missing trigger engine (it
        # no-ops gracefully).
        if self.config.skill_evolution is not None:
            try:
                inserted = await seed_default_triggers(
                    self._skill_trigger_repo, project_id=None
                )
                logger.info(
                    f"Skill trigger seed (Phase 4): {inserted} new "
                    f"default triggers inserted"
                )
            except Exception as seed_exc:
                logger.warning(
                    f"Skill trigger seed failed (Phase 4): {seed_exc}"
                )

            self._maintenance_service.register(
                "skill_metric_scan",
                self.config.skill_evolution.metric_scan_interval_hours,
                self._run_skill_metric_scan,
            )

            # Belt-and-suspenders: sweep stale pending usage records that
            # escaped finalization (counter crash, kill -9 mid-record, etc.).
            # Mirrors the ``skill_metric_scan`` registration above; uses the
            # same interval knob so operators have one dial to tune. The
            # wrapper ``_run_skill_orphan_sweep`` lives next to
            # ``_run_skill_metric_scan`` so all skill-evolution maintenance
            # callbacks sit together.
            self._maintenance_service.register(
                "skill_orphan_sweep",
                self.config.skill_evolution.metric_scan_interval_hours,
                self._run_skill_orphan_sweep,
            )

        # ── Project Blueprint Phase 3: daily scan ─────────────────────
        # The daily scan is the daemon-side counterpart to manual
        # ``/rebuild`` / ``/update`` triggers — same chokepoint
        # (BlueprintTriggerCoordinator) — but driven by the system
        # clock and gated by the ``auto_rebuild_enabled`` feature flag
        # (default OFF). Crashed blueprinter jobs are cleaned up by
        # ``reconcile_on_startup()`` at the next daemon restart.
        if (
            self._blueprint_repo is not None
            and self._blueprint_pending_repo is not None
            and getattr(self, "_blueprint_trigger_coordinator", None) is not None
        ):
            from .services.blueprint_scan_service import BlueprintScanService
            self._blueprint_scan_service = BlueprintScanService(
                blueprint_repo=self._blueprint_repo,
                pending_repo=self._blueprint_pending_repo,
                coordinator=self._blueprint_trigger_coordinator,
                config=self.config.blueprint,
                project_repository=self._project_repository,
            )
            # Load persisted last_run so the scan doesn't fire
            # immediately on every restart. The timestamp lives in
            # project metadata KV on the system default project — see
            # BlueprintScanService.execute() for the writer side.
            from datetime import datetime
            _SCAN_LAST_RUN_KEY = "blueprint_scan_last_run"
            _SYSTEM_DEFAULT_PID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"
            last_run_dt: datetime | None = None
            if self._project_repository is not None:
                try:
                    last_run_str = self._project_repository.get_metadata(
                        _SYSTEM_DEFAULT_PID, _SCAN_LAST_RUN_KEY,
                    )
                    if last_run_str:
                        last_run_dt = datetime.fromisoformat(last_run_str)
                except Exception:
                    # Corrupt or unreadable value — fall through with
                    # None so the scan still runs. The next execute()
                    # will overwrite the bad value.
                    pass
            self._maintenance_service.register(
                "blueprint_daily_scan",
                min_interval_hours=5.0,
                execute_fn=self._blueprint_scan_service.execute,
                last_run=last_run_dt,
            )

        await self._maintenance_service.start()

        # ── Skill Bank seeding (Phase 3: versioned templates) ──────────
        # Scans agents/*/skill-set.yaml (legacy .md fallback) +
        # skills-template/ and populates skill_bank. Idempotent via
        # version guard (W4). NOT gated by skill_evolution — the Skill
        # Bank is standalone infrastructure. Soft-fail: any error is
        # logged and swallowed so startup never crashes.
        #
        # Path resolution mirrors the convention used by
        # :func:`daemon.registry.get_registry` and
        # ``daemon.tools.agent_mother.BASE_DIR``:
        #   * frozen (PyInstaller prod binary) → ``Path(sys.executable).parent``
        #     → the install dir whose ``agents/`` is copied there by
        #     ``make install`` (Makefile line ~149). The bundled
        #     ``__file__`` is inside the ephemeral ``_MEIxxxx`` archive
        #     which has no ``agents/`` subdir, so the source-relative
        #     join would silently miss every manifest and leave
        #     ``skill_bank`` empty (the prod bug).
        #   * dev / packaged interpreter → ``Path(__file__).parent.parent``
        #     → the repo root, where ``agents/`` lives next to ``daemon/``.
        try:
            if getattr(sys, 'frozen', False):
                agents_base = Path(sys.executable).parent / "agents"
            else:
                agents_base = Path(__file__).parent.parent / "agents"
            seed_service = SkillSeedService(
                skill_bank_repo=self._skill_bank_repo,
                agents_dir=agents_base,
            )
            seed_result = await asyncio.to_thread(seed_service.seed_all)
            if seed_result["new"] > 0 or seed_result["updated"] > 0:
                logger.info(f"Skill bank seeding (Phase 3): {seed_result}")
            else:
                logger.debug(
                    f"Skill bank seeding (Phase 3): {seed_result}"
                )
        except Exception as seed_exc:
            logger.warning(f"Skill bank seeding (Phase 3) failed: {seed_exc}")

        # ── Recover opencode sessions on startup ───────────────────────────
        # Loads all persisted sessions from the dedicated opencode DB and
        # starts their background state-machine loops. Must happen after
        # the engine is ready but before agents can use the tools.
        #
        # DISABLED: loading all sessions on startup causes memory bloat.
        # Sessions are now loaded lazily on-demand via load_session_into_memory().
        # Uncomment below to re-enable recovery on startup.
        # try:
        #     recovered = await self._opencode_registry.recover_from_registry()
        #     logger.info(f"Recovered {recovered} opencode session(s) from registry")
        # except Exception as exc:
        #     logger.warning(f"Failed to recover opencode sessions: {exc}")

        if self._ensemble_config is not None and self._ensemble_config.is_postgres:
            pg = self._ensemble_config.postgres
            logger.info(
                f"SessionManager initialized with PostgreSQL checkpointer "
                f"({pg.host}:{pg.port}/{pg.db})"
            )
        else:
            logger.info(
                "SessionManager initialized with async checkpointer"
            )

    async def _eager_warm_loader_caches(self) -> None:
        """Pre-populate one-time loader state at startup.

        On the first ``load_and_cache_prompt`` call, ``loader.py``:

        1. Calls ``_ensure_tool_metadata_populated`` (loader.py:27) which
           imports ~10 tool modules, instantiates dummy tool closures,
           and runs ``scan_tools_for_full_docs`` on them.
        2. Calls ``estimate_tokens`` (loader.py:436) which imports
           ``tiktoken`` and resolves the ``cl100k_base`` encoder.

        Both are pure CPU work with no network or DB I/O. Doing them in
        a background task at startup moves ~hundreds of ms of
        first-instance latency off the user-facing spawn path.
        """
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._warm_loader_caches_sync)
        except Exception as e:
            logger.warning(f"eager_warm_loader_caches failed: {e}")

    @staticmethod
    def _warm_loader_caches_sync() -> None:
        from .loader import _ensure_tool_metadata_populated, estimate_tokens

        _ensure_tool_metadata_populated()
        # Touch the encoder so ``import tiktoken`` and
        # ``tiktoken.get_encoding('cl100k_base')`` complete at startup.
        estimate_tokens("warmup")
        logger.debug("Eager-warmed tool metadata + tiktoken encoder")

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

    # =========================================================================
    # Phase 3 / User Message Injection: RAM queue helpers (W1, S1)
    # =========================================================================
    # The injection queue is a RAM-only FIFO per instance used to hold
    # pending user messages that the LangGraph agent_node will pull + clear
    # on its next LLM invocation. See:
    #   * .agents/shared/planning/user-msg-injection/phase3-plan.md
    #
    # Threading contract (Phase 3 depends on this surface):
    #   * set_injection(iid, content)      — APPEND; multi-entry queue
    #   * get_injection(iid)                — peek; returns LIST (or None)
    #   * get_injection_count(iid)          — count; returns int (0 if none)
    #   * clear_injection(iid)              — pop; returns LIST (or None)
    #   * _cleanup_instance_state(iid)      — centralized cleanup used by all
    #                                         lifecycle paths (W1).
    #
    # The queue is RAM-only; the injected HumanMessages themselves ARE
    # persisted to the LangGraph checkpoint via the agent_node returning
    # ALL of them (C2) so crash recovery still preserves every user turn.

    _INJECTION_TTL_SECONDS = 3600  # 1h — orphaned sweep window (S1)

    def set_injection(self, instance_id: str, content: str) -> dict[str, str]:
        """Append a pending user message to the RAM injection queue.

        Append-list semantics: a second ``set_injection`` for the same
        ``instance_id`` appends to the queue rather than replacing the
        existing entry. The agent_node consumes ALL queued messages on
        its next LLM call, in FIFO order (oldest first).

        Args:
            instance_id: Target instance.
            content: The user message text to inject on the next LLM call.

        Returns:
            The newly appended entry as ``{"content": str, "timestamp": str}``.
        """
        entry = {
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        queue = self._pending_injections.get(instance_id)
        if queue is None:
            queue = []
            self._pending_injections[instance_id] = queue
        queue.append(entry)
        logger.info(
            f"[Injection] Appended pending message for instance "
            f"{instance_id[:8]}... (len={len(content)}, queue_depth={len(queue)})"
        )
        return entry

    def get_injection(self, instance_id: str) -> list[dict[str, str]] | None:
        """Return the pending injection queue for ``instance_id``, or None.

        Returns the entire list of pending entries (oldest first) so the
        agent_node can consume them in FIFO order. Returns ``None`` when
        no injection is queued.

        Does NOT clear the queue — consumption is a separate step so the
        caller can decide when to clear (typically right before the LLM
        call completes, after capturing the messages for the return value).

        Args:
            instance_id: Target instance.

        Returns:
            A list of ``{"content", "timestamp"}`` dicts, or ``None`` when
            no pending injection exists.
        """
        queue = self._pending_injections.get(instance_id)
        if not queue:
            return None
        return list(queue)  # defensive copy so callers can't mutate internal state

    def get_injection_count(self, instance_id: str) -> int:
        """Return the number of pending injections for ``instance_id``.

        Returns ``0`` when no injection is queued. Used by the API and
        SSE code paths to surface the queue depth to the frontend (e.g.
        for a "N messages queued" indicator) without paying for the full
        list copy.

        Args:
            instance_id: Target instance.

        Returns:
            Count of pending injections (0 if none).
        """
        queue = self._pending_injections.get(instance_id)
        return len(queue) if queue else 0

    def clear_injection(self, instance_id: str) -> list[dict[str, str]] | None:
        """Pop and return a defensive copy of the pending injection queue, or None.

        Returns the entire list of pending entries (oldest first) so the
        caller can forward them to SSE / cleanup paths. Returns ``None``
        when no injection is queued (safe to call when nothing pending).

        Used by lifecycle pause/terminate/clear paths (W1) and by the
        agent_node's consume step in :func:`daemon.graph.create_agent_node`.

        Returns:
            A defensive copy of the cleared list of
            ``{"content", "timestamp"}`` dicts, or ``None`` when nothing
            was queued.
        """
        queue = self._pending_injections.pop(instance_id, None)
        if queue is None:
            return None
        return list(queue)

    # ------------------------------------------------------------------
    # Context Injection Restructure — Phase 3 (B2 fix)
    # ------------------------------------------------------------------
    # ``_context_skill_results`` is the per-instance cache of the
    # LAST skill-search result, written by the messaging path on the
    # first attempt of a message and read by ``ContextSlot.assemble()``
    # inside ``agent_node`` so retries can reuse the same matched
    # skills without re-running the search (or losing them entirely,
    # which would be the B3 bug). The indirection through the manager
    # mirrors :meth:`set_injection` / :meth:`get_injection` so the
    # messaging path (which holds only the compiled graph) can hand
    # the result to the slot without a direct reference.
    #
    # Cleanup mirrors :meth:`clear_injection` — every per-instance
    # cleanup site (``_cleanup_instance_state``, the TTL sweep, the
    # cancel-graph-task done-branch) also drops the entry here.

    def set_context_skill_result(
        self,
        instance_id: str,
        result: tuple[str | None, list[str]] | None,
    ) -> None:
        """Cache the latest skill-search result for ``instance_id``.

        Stores the ``(injection_text, injected_skill_ids)`` tuple
        returned by
        :meth:`SkillInjectionService.inject_skills` (or ``None`` for
        "search ran but yielded no injection") so a retry of the
        same user message can reuse the same matched skills
        instead of re-running the search. The cache is per-message,
        overwritten on every new first-attempt injection — there is
        no list of historical results, just the latest one (B3 fix).

        Args:
            instance_id: Target instance.
            result: The tuple returned by the skill injector, or
                ``None`` for "search ran but yielded nothing". The
                caller is responsible for having awaited the search
                before calling this — the method does not run any
                new search itself.
        """
        self._context_skill_results[instance_id] = result

    def get_context_skill_result(
        self,
        instance_id: str,
    ) -> tuple[str | None, list[str]] | None:
        """Return the cached skill-search result for ``instance_id``.

        Returns the ``(injection_text, injected_skill_ids)`` tuple
        cached by :meth:`set_context_skill_result`, or ``None`` when
        no entry has been stored.

        ``None`` is ambiguous in this implementation:

        * the key was never set (search never ran), **or**
        * the key was explicitly set to ``None`` (search ran but
          yielded no injectable skills).

        Both cases are returned as ``None`` because the underlying
        ``dict.get()`` cannot distinguish them — and the B3 fix in
        ``assemble_context_messages`` treats them identically
        (re-run the search), so callers do not need to disambiguate.
        If a future caller genuinely needs the distinction, they
        should check ``instance_id in self._context_skill_results``
        directly and only fall back to ``.get()`` for the value.

        Callers that DO need to react differently to the two
        "result present" shapes (B3):

        * ``(text, ids)`` with non-empty ``ids`` → reuse directly.
        * ``(None, [])`` or ``(None, [...])`` with empty content →
          search already ran and yielded no injection; do NOT
          re-run.

        Args:
            instance_id: Target instance.

        Returns:
            The cached tuple, or ``None`` when the key is absent
            or explicitly stored as ``None``.
        """
        return self._context_skill_results.get(instance_id)

    def get_and_increment_skill_search_count(self, instance_id: str) -> int:
        """Return the current per-instance message count, then increment.

        Tracks how many user messages have been processed for
        ``instance_id`` since the last skill-search ran. The gating
        logic in :mod:`daemon.services.instance_messaging` calls this
        on every new user message: if the returned (pre-increment)
        count is below ``skill_search_interval - 1`` AND a
        cached result exists, the search is skipped and the cached
        result is reused; otherwise a fresh search runs.

        Returns the count BEFORE incrementing, so the caller can
        compare ``count < interval - 1`` directly. The first call for a
        fresh instance returns ``0`` (no messages counted yet) then
        stores ``1``.

        Reset to ``0`` by :meth:`reset_skill_search_count` whenever a
        real search runs. Cleaned up alongside ``_context_skill_results``
        in :meth:`_cleanup_instance_state`, the TTL sweep, and the
        dead-task branch.

        Args:
            instance_id: Target instance.

        Returns:
            The message count before this increment (``0`` for a
            fresh instance).
        """
        current = self._skill_search_message_counts.get(instance_id, 0)
        self._skill_search_message_counts[instance_id] = current + 1
        return current

    def reset_skill_search_count(self, instance_id: str) -> None:
        """Reset the per-instance message counter to ``0``.

        Called by the search-gating logic immediately AFTER a real
        skill search runs, so the next ``interval`` messages can
        reuse the fresh result. Idempotent and defensive — safe to
        call when no count exists yet.

        Args:
            instance_id: Target instance.
        """
        self._skill_search_message_counts[instance_id] = 0

    # ------------------------------------------------------------------
    # Explicit-load marker (Phase 4b / W1 fix)
    # ------------------------------------------------------------------
    # The explicit ``load_skill`` (``<meta>`` tag) path writes to the
    # same ``_context_skill_results`` cache that the auto-search path
    # uses. If left unchecked, the interval gate would treat an
    # explicit-load result as a valid auto-search cache hit and skip
    # the next ordinary message's search — wrong. A separate cache
    # dict would force dual-writes through ``assemble_context_messages``
    # (which reads the cache for the retry/reuse contract). A marker
    # set is the minimal correct separation: explicit-load sets it,
    # auto-search clears it, the gate consults it.

    def mark_explicit_skill_loaded(self, instance_id: str) -> None:
        """Mark that the last skill-result cache write was an explicit load.

        Called by the ``<meta>``-tag ``load_skill`` path AFTER writing to
        ``_context_skill_results``. The ``skill_search_interval`` gate
        reads :meth:`was_explicit_skill_loaded` so an explicit load does
        NOT satisfy the interval cache — the next ordinary message runs
        a fresh auto-search even if it falls within the interval window.

        Idempotent — safe to call multiple times. Cleared in the same 3
        cleanup sites alongside ``_context_skill_results`` and
        ``_skill_search_message_counts``.

        Args:
            instance_id: Target instance.
        """
        self._explicit_skill_loaded.add(instance_id)

    def clear_explicit_skill_loaded(self, instance_id: str) -> None:
        """Clear the explicit-load marker for ``instance_id``.

        Called by the auto-search path after a fresh search runs, so
        the interval cache is valid again for subsequent messages.
        Defensive — safe when the marker was never set.

        Args:
            instance_id: Target instance.
        """
        self._explicit_skill_loaded.discard(instance_id)

    def was_explicit_skill_loaded(self, instance_id: str) -> bool:
        """Return True if the last cache write was an explicit ``load_skill``.

        Consulted by the ``skill_search_interval`` gate. When True,
        the gate forces a fresh auto-search on the next ordinary
        message rather than reusing the explicit-load result.

        Args:
            instance_id: Target instance.

        Returns:
            ``True`` when :meth:`mark_explicit_skill_loaded` was the
            most recent cache writer for ``instance_id`` and the
            marker has not been cleared.
        """
        return instance_id in self._explicit_skill_loaded

    # ------------------------------------------------------------------
    # Question pause-requested flag (Phase 1 / question tool)
    # ------------------------------------------------------------------
    # The ``question`` tool sets this flag before returning. The
    # conditional post-tools edge in ``daemon.graph.build_instance_graph``
    # reads it on the way out of the tools node and routes to
    # ``question_pause_node``, which sets the deferred-pause marker (see
    # :attr:`_deferred_question_pause` below) and clears the flag in its
    # ``finally`` block. The actual ``pause_instance_cascade`` runs from
    # the post-graph completion path in
    # ``daemon.services.instance_messaging`` AFTER the graph task has been
    # popped (C2 torn-state fix). The dict keyed by ``instance_id`` keeps
    # each instance's flag isolated — there is no cross-instance coupling.

    def set_question_pause_requested(self, instance_id: str) -> None:
        """Mark that the ``question`` tool has requested a pause.

        Called by the ``question`` tool immediately after storing the
        pack and emitting the SSE event. Read by
        :func:`daemon.graph.create_post_tools_router` on every post-tools
        edge evaluation — the flag's value at the time of evaluation is
        what determines routing, not when the flag was set.

        Args:
            instance_id: Owning instance identifier.
        """
        self._question_pause_requested[instance_id] = True

    def is_question_pause_requested(self, instance_id: str) -> bool:
        """Return True if a question-initiated pause is pending.

        Returns ``False`` (not ``None``) for unknown instance_ids so the
        caller can use the result as a boolean directly without a
        ``None`` check.

        Args:
            instance_id: Owning instance identifier.

        Returns:
            ``True`` when the flag is set; ``False`` otherwise.
        """
        return self._question_pause_requested.get(instance_id, False)

    def clear_question_pause_requested(self, instance_id: str) -> None:
        """Drop the pause-requested flag for ``instance_id``.

        Called from ``question_pause_node``'s ``finally`` block (every
        execution path — success, CancelledError, or exception) and from
        ``_cleanup_instance_state`` for terminate / release / hard-delete
        paths. Safe to call when the flag is unset.

        Args:
            instance_id: Owning instance identifier.
        """
        self._question_pause_requested.pop(instance_id, None)

    # ------------------------------------------------------------------
    # C2 fix — Deferred question pause (Solution A)
    # ------------------------------------------------------------------
    # ``question_pause_node`` runs INSIDE the graph task stored at
    # ``_graph_tasks[instance_id]``. It cannot call
    # ``pause_instance_cascade`` directly because the cascade pops the
    # task and calls ``task.cancel()`` on it — which raises
    # ``CancelledError`` at the next ``await`` inside the cascade,
    # interrupting its batched DB write (C2 torn-state bug).
    #
    # Instead, the node sets a deferred-pause marker on the manager;
    # the actual cascade runs from the post-graph completion path in
    # ``daemon.services.instance_messaging`` AFTER ``_graph_tasks`` is
    # popped, so there is no graph task to self-cancel. The DB write
    # then proceeds cleanly and the instance transitions to PAUSED.

    def set_deferred_question_pause(self, instance_id: str) -> None:
        """Mark that ``question_pause_node`` ran and a pause should be applied.

        Called from inside the graph task. The actual
        ``pause_instance_cascade`` invocation runs from the post-graph
        completion path (see ``daemon.services.instance_messaging``) via
        :meth:`pop_deferred_question_pause`. The in-graph task cannot
        call the cascade directly because ``task.cancel()`` would
        interrupt the cascade's DB transaction with ``CancelledError``
        (C2 torn-state bug).

        Args:
            instance_id: Owning instance identifier.
        """
        self._deferred_question_pause.add(instance_id)

    def has_deferred_question_pause(self, instance_id: str) -> bool:
        """Check whether ``instance_id`` has a pending deferred-pause marker.

        Unlike :meth:`pop_deferred_question_pause`, this does NOT remove the
        marker. Used by the cascade callers in
        ``daemon.services.instance_messaging`` to keep the marker alive
        across the ``pause_instance_cascade`` execution so source-side
        Task guards (Phase 1 in ``child_reports._process_child_completion_db_sync``
        and Phase 2 in ``instance_messaging._prepare_enqueued_message``)
        see the marker during the cascade's DB-commit window. Closes C1
        (marker-lifetime-doesn't-cover-cascade-execution-window).

        Pairing contract: callers MUST follow ``has`` → ``await
        pause_instance_cascade`` → ``pop_deferred_question_pause`` (in a
        ``finally``) so the marker covers the full window. The pop is
        idempotent (``set.discard``) so a redundant call is harmless.

        Args:
            instance_id: Owning instance identifier.

        Returns:
            ``True`` if a deferred-pause marker is currently set;
            ``False`` otherwise.
        """
        return instance_id in self._deferred_question_pause

    def pop_deferred_question_pause(self, instance_id: str) -> bool:
        """Pop the deferred-pause marker for ``instance_id``.

        Called from the post-graph completion path AFTER ``_graph_tasks``
        has been popped for this instance. Returns ``True`` if a marker
        was set and the caller should now invoke
        ``pause_instance_cascade``; ``False`` otherwise. Atomic check-
        and-remove so a concurrent resume / retry path observes a
        consistent view (the marker is either present for the caller or
        absent — never re-fired by a later code path that lost the
        race).

        .. note::
           Since the C1 fix (C1 marker-lifetime race), this is paired with
           :meth:`has_deferred_question_pause`: the post-graph callback
           peeks with ``has`` BEFORE awaiting the cascade and pops with
           this method AFTER the cascade's ``finally`` block, so the
           marker covers the full cascade-execution window (in-memory
           cancel + DB commit to PAUSED). The old "pop before cascade"
           ordering is gone; the new contract is "peek → cascade → pop".

        Args:
            instance_id: Owning instance identifier.

        Returns:
            ``True`` if a deferred pause was pending; ``False``
            otherwise.
        """
        if instance_id not in self._deferred_question_pause:
            return False
        self._deferred_question_pause.discard(instance_id)
        return True

    # =========================================================================
    # Watchover accessors (Phase 1 — core graph interception).
    #
    # Watchover inserts a ``watchover_check`` node between ``agent`` and
    # ``tools``. The per-instance enabled flag is read from the instance's
    # ``instance_metadata`` JSONB (``watchover_enabled`` key); the deferred
    # termination marker uses the same C2-safe pattern as
    # ``_deferred_question_pause`` — the graph node sets the marker, the
    # cascade runs from the post-graph completion path.
    # =========================================================================

    def is_watchover_enabled(self, instance_id: str) -> bool:
        """Check ``instance_metadata`` JSONB for the ``watchover_enabled`` flag.

        Returns ``False`` if the instance is unknown, the metadata is
        missing, or the flag is absent — so unwatched instances are
        zero-cost on the hot path (the caller already short-circuits via
        the global ``WATCHOVER_ENABLED`` kill-switch before reaching here).

        Args:
            instance_id: Owning instance identifier.

        Returns:
            ``True`` only when the instance has
            ``instance_metadata["watchover_enabled"] == True``.
        """
        try:
            instance = self._instance_repository.get(instance_id)
            if instance is None or not instance.instance_metadata:
                return False
            return bool(instance.instance_metadata.get("watchover_enabled", False))
        except Exception as exc:
            # M3: surface programming errors instead of silently swallowing.
            # The broad ``except Exception`` is kept (this is a hot-path
            # method that must not raise), but the warning makes genuine
            # bugs visible — a swallowed ``AttributeError`` from a model
            # schema change used to disappear without a trace.
            logger.warning(
                f"is_watchover_enabled failed for {instance_id[:8]}...: "
                f"{type(exc).__name__}: {exc}",
                exc_info=True,
            )
            return False

    def set_metadata_many(
        self, instance_id: str, updates: dict[str, Any]
    ) -> "Instance | None":
        """Thin delegator to :meth:`InstanceRepository.set_metadata_many`.

        Atomically writes multiple ``instance_metadata`` keys in ONE
        SQL statement (single UPDATE with a nested ``jsonb_set`` /
        ``json_set`` chain). Prevents torn-state on partial crash or
        concurrent read mid-activation. See the repository method for
        the dialect-aware implementation.

        Args:
            instance_id: Owning instance identifier.
            updates: Mapping of top-level JSON key to JSON-serialisable
                value. Must be non-empty.

        Returns:
            The refreshed enriched ``Instance``, or ``None`` if the
            instance does not exist.
        """
        return self._instance_repository.set_metadata_many(instance_id, updates)

    def enable_watchover(
        self,
        instance_id: str,
        *,
        requirement: str | None = None,
        context: str | None = None,
        refresh_interval: int | None = None,
    ) -> "Instance | None":
        """Atomically enable watchover for ``instance_id``.

        Phase 3 (T3.3) + Phase 5 (T5.4). Writes the full watchover config
        in a SINGLE atomic ``set_metadata_many`` call so a crash mid-write
        cannot leave the instance half-configured (e.g. enabled=True but
        context still empty). Defaults ``denial_count`` to 0; tolerates
        ``None`` ``context`` / ``requirement``.

        Phase 5 (T5.4) additions — context freshness tracking:

          * ``watchover_context_turn``: 0 — the per-check turn counter,
            reset to 0 at activation so the first watchover check sees a
            fresh context (the context was just built).
          * ``watchover_context_refresh_interval``: ``refresh_interval``
            or the ``WATCHOVER_CONTEXT_REFRESH_INTERVAL`` env var or 20
            (every 20 checks). The watchover check node reads this to
            decide when the context snapshot is stale and needs a
            lightweight refresh (Open Question #2 — default 20 so the
            expensive LLM-built guardrail persists across many turns;
            refreshing every 20 checks (not every 1) lets the builder
            context survive while still catching truly stale contexts).

        The keys set are:

        * ``watchover_enabled``: True
        * ``watchover_context``: ``context`` (may be ``None``)
        * ``watchover_requirement``: ``requirement`` (may be ``None``)
        * ``watchover_context_turn``: 0 (T5.4)
        * ``watchover_context_refresh_interval``: N (T5.4, default 20)

        Args:
            instance_id: Owning instance identifier.
            requirement: User-supplied requirement string the watcher
                uses as the Allow/Deny prompt. Optional — may be ``None``.
            context: The constructed ``watchover_context`` (compaction
                summary or raw-tail fallback). Optional — may be ``None``.
            refresh_interval: How many watchover checks may elapse
                before the context snapshot is considered stale and
                refreshed (T5.4). Defaults to the
                ``WATCHOVER_CONTEXT_REFRESH_INTERVAL`` env var, or 20
                (every 20 checks) when unset. With the LLM builder the
                guardrail is expensive to rebuild, so the default lets
                it survive many turns; C1 fix moved it from 1 to 20.

        Returns:
            The refreshed enriched ``Instance``, or ``None`` if the
            instance does not exist.
        """
        # T5.4 — resolve the refresh interval. Explicit kwarg wins,
        # then env var, then default 20 (every 20 checks). Floor at 1.
        # C1 fix: was 1 (every check) — caused the LLM-built guardrail
        # to be replaced by raw-tail after a single check, defeating
        # the builder. 20 lets the expensive builder context survive
        # while still catching truly stale contexts.
        import os

        if refresh_interval is None:
            env_val = os.environ.get("WATCHOVER_CONTEXT_REFRESH_INTERVAL")
            if env_val is not None:
                try:
                    refresh_interval = int(env_val)
                except ValueError:
                    refresh_interval = 20
            else:
                refresh_interval = 20
        if refresh_interval < 1:
            refresh_interval = 1

        updates: dict[str, Any] = {
            "watchover_enabled": True,
            # watchover_denial_count is NOT persisted here — the live
            # runtime counter lives in LangGraph state
            # (state["watchover_denial_count"]); instance_metadata copy
            # was dead code. The InstanceInfo API field still reads
            # from LangGraph state for the frontend.
            # T5.4 — context freshness tracking keys.
            "watchover_context_turn": 0,
            "watchover_context_refresh_interval": refresh_interval,
        }
        if context is not None:
            updates["watchover_context"] = context
        if requirement is not None:
            updates["watchover_requirement"] = requirement
        return self.set_metadata_many(instance_id, updates)

    def disable_watchover(self, instance_id: str) -> "Instance | None":
        """Atomically disable watchover for ``instance_id``.

        Phase 3 (T3.3). Clears ``watchover_enabled`` only — the existing
        ``watchover_context`` / ``watchover_requirement`` are kept for
        audit so an operator can see what the watcher was guarding.

        Args:
            instance_id: Owning instance identifier.

        Returns:
            The refreshed enriched ``Instance``, or ``None`` if the
            instance does not exist.
        """
        return self.set_metadata_many(
            instance_id, {"watchover_enabled": False}
        )

    def get_watchover_context(self, instance_id: str) -> str | None:
        """Read the ``watchover_context`` key from ``instance_metadata``.

        Phase 3 (T3.3). Defensive read — returns ``None`` when the
        instance is missing, the metadata dict is missing/empty, or the
        key is absent. Never raises on a transient repository error
        (returns ``None`` and logs at debug).

        Args:
            instance_id: Owning instance identifier.

        Returns:
            The stored context string, or ``None``.
        """
        try:
            instance = self._instance_repository.get(instance_id)
            if instance is None or not instance.instance_metadata:
                return None
            value = instance.instance_metadata.get("watchover_context")
            if value is None:
                return None
            return str(value)
        except Exception:
            logger.debug(
                "get_watchover_context(%s): repo error; returning None",
                instance_id,
                exc_info=True,
            )
            return None

    async def wait_for_instance_quiescent(
        self, instance_id: str, timeout: float = 30.0
    ) -> bool:
        """Wait until the instance has no in-flight graph task.

        Phase 3 / Watchover (T3.5b, 2026-08-05). Best-effort barrier
        that blocks activation until any current graph run for this
        instance finishes, so the watchover_context snapshot is taken
        against a quiescent LangGraph state. Implements FR-28 / NFR-15
        at the **graph-boundary** level (in-flight limitation still
        documented per LD-4).

        Implementation: polls ``self._graph_tasks.get(instance_id)``
        and ``await``s the task with a bounded timeout. If no task is
        present (or the task is already done) the method returns
        immediately. Never raises — a timeout returns ``False`` and
        logs so the caller can proceed and surface a soft warning
        rather than deadlock the operator.

        Args:
            instance_id: Owning instance identifier.
            timeout: Maximum seconds to wait. Default 30s. ``<= 0``
                means "do not wait" (return ``True`` if no task,
                ``False`` if task exists).

        Returns:
            ``True`` if the instance is quiescent within ``timeout``,
            ``False`` if a task is still running when the timeout
            expires (best-effort).
        """
        task = self._graph_tasks.get(instance_id)
        if task is None or task.done():
            logger.debug(
                "wait_for_instance_quiescent(%s): already quiescent (no task)",
                instance_id,
            )
            return True

        if timeout <= 0:
            logger.debug(
                "wait_for_instance_quiescent(%s): task in flight and timeout<=0; returning False",
                instance_id,
            )
            return False

        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            logger.info(
                "wait_for_instance_quiescent(%s): task completed within %.1fs",
                instance_id,
                timeout,
            )
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "wait_for_instance_quiescent(%s): timed out after %.1fs; "
                "task still in flight — proceeding anyway (LD-4 in-flight "
                "limitation may apply)",
                instance_id,
                timeout,
            )
            return False
        except Exception as exc:
            # Best-effort — never block activation on an unexpected error.
            logger.warning(
                "wait_for_instance_quiescent(%s): unexpected error during wait: %s",
                instance_id,
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # T3.5 / T3.6 — activation & deactivation lifecycle (facade)
    # ------------------------------------------------------------------

    async def enable_watchover_lifecycle(
        self,
        instance_id: str,
        *,
        requirement: str | None = None,
        user_context: str | None = None,
        resume_message: str | None = None,
        next_command: str | None = None,
    ) -> dict[str, Any]:
        """Orchestrate watchover activation: pause → context → flag → resume.

        Phase 3 (T3.5) facade. Thin wrapper over
        :meth:`WatchoverService.activate_watchover` — kept here so the
        router endpoint has a stable manager-level API independent of
        the service class name.

        Args:
            instance_id: Owning instance identifier.
            requirement: Operator-supplied requirement string.
            user_context: Optional pre-built context string. Production
                callers pass ``None`` and let the service build the
                context via ``ContextCompactor``; tests pass a fixture.
            resume_message: Optional custom message to deliver to the
                target instance on the post-activation resume. The
                target receives ``resume_message or "continue"``;
                cascade children resume silently with ``"resume"``.
                ``None`` (default) → target gets ``"continue"``.
                Ignored on the terminal-state path (the service
                dispatches ``next_command`` instead).
            next_command: Optional next command to enqueue as a new
                message AFTER enabling watchover on the terminal-state
                activation path. The service uses this ONLY when the
                instance is in a terminal/idle state (NOT running);
                running instances use the pause → resume flow with
                ``resume_message``. ``None`` (default) → no extra
                message is enqueued.

        Returns:
            Dict with ``instance_id``, ``watchover_enabled``,
            ``context_length``, ``quiescent``.

        Raises:
            KeyError: When the instance is not found.
            Exception: When the activation sequence fails (after
                rollback has cleared any partial state).
        """
        return await self._watchover_service.activate_watchover(
            instance_id,
            requirement=requirement,
            user_context=user_context,
            resume_message=resume_message,
            next_command=next_command,
        )

    async def disable_watchover_lifecycle(self, instance_id: str) -> dict[str, Any]:
        """Orchestrate watchover deactivation: pause → clear flag → resume.

        Phase 3 (T3.6) facade. Thin wrapper over
        :meth:`WatchoverService.deactivate_watchover`.

        Args:
            instance_id: Owning instance identifier.

        Returns:
            Dict with ``instance_id`` and ``watchover_enabled=False``.

        Raises:
            KeyError: When the instance is not found.
            Exception: When the deactivation sequence fails.
        """
        return await self._watchover_service.deactivate_watchover(instance_id)

    def set_deferred_watchover_terminate(self, instance_id: str) -> None:
        """Mark ``instance_id`` for watchover termination (C2-safe deferred marker).

        Called from ``watchover_terminate_node`` INSIDE the graph task. The
        intended termination cascade runs from the post-graph completion path
        AFTER ``_graph_tasks`` is popped — mirroring the
        ``_deferred_question_pause`` contract so there is no self-cancel
        torn-state bug. The marker is consumed by
        :meth:`InstanceMessagingService._drain_deferred_watchover_terminate`
        in the post-graph completion path (``send_message`` and
        ``_process_message_with_tracking`` ``finally`` blocks).

        Args:
            instance_id: Owning instance identifier.
        """
        self._deferred_watchover_terminate.add(instance_id)

    def is_watchover_terminate_requested(self, instance_id: str) -> bool:
        """Check whether a deferred watchover termination is pending.

        Args:
            instance_id: Owning instance identifier.

        Returns:
            ``True`` if the marker is set; ``False`` otherwise.
        """
        return instance_id in self._deferred_watchover_terminate

    def clear_watchover_terminate_requested(self, instance_id: str) -> None:
        """Discard the deferred watchover termination marker for ``instance_id``.

        Called from the post-graph completion path after the cascade has
        been processed, and from ``_cleanup_instance_state`` to prevent a
        stale marker from leaking to a fresh instance that reuses the id.
        Idempotent (``set.discard``).
        """
        self._deferred_watchover_terminate.discard(instance_id)

    def bump_gii_throttle(self, instance_id: str) -> int:
        """Increment the consecutive ``get_instance_info`` call counter.

        Returns the new count after the increment. The counter is reset
        whenever the agent invokes any other tool or sends a non-gii
        message — see :meth:`reset_gii_throttle`.
        """
        count = self._gii_throttle.get(instance_id, 0) + 1
        self._gii_throttle[instance_id] = count
        return count

    def reset_gii_throttle(self, instance_id: str) -> None:
        """Clear the consecutive-call counter for ``instance_id``."""
        self._gii_throttle.pop(instance_id, None)

    def get_gii_throttle_count(self, instance_id: str) -> int:
        """Return the current consecutive-call count (0 if unset)."""
        return self._gii_throttle.get(instance_id, 0)

    def record_loop_repair(self, instance_id: str, summary: str) -> int:
        """Record a repair event for ``instance_id``.

        Increments the per-instance repair counter, stores ``summary`` as the
        last repair summary, and stamps ``last_repair_at`` with the current
        UTC time. Returns the new repair count.

        Args:
            instance_id: Target instance.
            summary: Short description of what was repaired (used by
                ``REPAIR_SUMMARIZATION_PROMPT``-style summaries).

        Returns:
            The new repair count after incrementing.
        """
        state = self._loop_breaker_state.get(instance_id, {"count": 0})
        state["count"] = state.get("count", 0) + 1
        state["last_summary"] = summary
        state["last_repair_at"] = datetime.now(timezone.utc).isoformat()
        self._loop_breaker_state[instance_id] = state
        return state["count"]

    def reset_loop_breaker(self, instance_id: str) -> None:
        """Clear loop-breaker state for ``instance_id`` (no-op when unset).

        Called when no loop is currently detected — the agent made progress,
        so we reset the per-instance counter so the next genuine loop starts
        from ``count=1``.
        """
        self._loop_breaker_state.pop(instance_id, None)

    def get_loop_repair_count(self, instance_id: str) -> int:
        """Return the current repair count (0 if unset)."""
        return self._loop_breaker_state.get(instance_id, {}).get("count", 0)

    def _cleanup_instance_state(self, instance_id: str) -> dict | None:
        """Centralized per-instance in-memory state cleanup (W1).

        Pops from ``_graph_tasks`` and ``_pending_injections``, and releases
        the per-instance context usage cache, in a single call. Use this from
        any new cleanup path so the three resources cannot drift out of sync.

        The returned dict carries the cleared values so callers (e.g. the
        pause-cascade path) can forward them to SSE without needing a second
        round-trip through the manager. Shape::

            {
                "graph_task": asyncio.Task | None,
                "cleared_injection": list[dict] | None,
                "context_usage_cleared": bool,
            }

        ``cleared_injection`` is the full FIFO queue (oldest first) — multiple
        pending messages can be queued for the same instance under
        Phase 3 append-list semantics (was a single dict in Phase 1).

        Note: ``_request_registry`` cancellation is intentionally NOT handled
        here — the cancellation reason differs per call site
        (USER_STOPPED vs SESSION_TERMINATED, etc.), so call sites keep that
        call inline with the appropriate reason. Centralizing it here would
        require threading a reason arg through every cleanup path without
        adding value, and the dict above does not include a cancellation
        handle because :meth:`RequestRegistry.cancel_by_instance` is
        fire-and-forget (it emits a ``CancellationToken`` signal).

        Args:
            instance_id: Target instance.

        Returns:
            Cleared items dict (see above) for caller-side forwarding.
        """
        task = self._graph_tasks.pop(instance_id, None)
        cleared_injection = self._pending_injections.pop(instance_id, None)
        # Pop the gii throttle entry too — without this cleanup the dict
        # grows unbounded for long-lived daemons that process many short-
        # lived instances (each termination leaks one entry).
        self._gii_throttle.pop(instance_id, None)
        # Memory-leak fix: drop the per-instance loop-breaker state too.
        # Follows the same 5-path cleanup pattern as ``_gii_throttle`` —
        # centralizing in ``_cleanup_instance_state`` is the single source
        # of truth for the 4 lifecycle callers that route through here.
        self._loop_breaker_state.pop(instance_id, None)
        # Context Injection Restructure — Phase 3 (B2 fix): drop the
        # per-instance cached skill-search result. Without this the
        # dict leaks one entry per terminated instance, and a future
        # instance that reused this id could inherit a stale result.
        # ``getattr`` keeps the cleanup defensive against test stubs
        # that build a minimal ``InstanceManager`` without going
        # through the full ``__init__`` (a handful of unit tests in
        # ``test_question_graph`` etc. call this path with a hand-
        # rolled manager).
        _ctx_skill_results = getattr(
            self, "_context_skill_results", None
        )
        if _ctx_skill_results is not None:
            _ctx_skill_results.pop(instance_id, None)
        # skill_search_interval (Phase 4b): drop the per-instance
        # message counter that gates the cached skill-search result.
        # Mirrors the ``_context_skill_results`` cleanup above — same
        # defensive ``getattr`` for hand-rolled test stubs.
        _skill_search_counts = getattr(
            self, "_skill_search_message_counts", None
        )
        if _skill_search_counts is not None:
            _skill_search_counts.pop(instance_id, None)
        # W1 fix: drop the explicit-load marker alongside the
        # counter — same lifetime, same defensive ``getattr`` pattern.
        _explicit_loaded = getattr(
            self, "_explicit_skill_loaded", None
        )
        if _explicit_loaded is not None:
            _explicit_loaded.discard(instance_id)
        # SSE message-tracking dicts leak fix: ``_original_timestamps``
        # and ``_emitted_message_content`` are keyed by ``{instance_id}:{...}``
        # (msg_id for normal messages, ``context:{...}`` for the persistent
        # context HumanMessages emitted via the SSE user_message path in
        # ``InstanceMessagingService._process_message_with_tracking``).
        # They cannot be popped with the ``instance_id`` key directly — we
        # must scan for the prefix and drop the matching entries. Without
        # this, both dicts grow unbounded for long-lived daemons that
        # process many short-lived instances (the original CapFix 2026-07-29
        # for the user_message SSE path explicitly missed this cleanup,
        # so a fresh instance termination left the keys in place for the
        # lifetime of the process). Build the key list BEFORE the pop
        # loop to avoid ``RuntimeError: dictionary changed size during
        # iteration`` (mirrors the SYNCHRONOUSITY INVARIANT documented
        # on ``_cleanup_stale_injections``).
        _emitted_content = getattr(
            self, "_emitted_message_content", None
        )
        if isinstance(_emitted_content, dict) and _emitted_content:
            _prefix = f"{instance_id}:"
            _stale_emitted_keys = [
                k for k in _emitted_content if k.startswith(_prefix)
            ]
            for _k in _stale_emitted_keys:
                _emitted_content.pop(_k, None)
        _original_ts = getattr(self, "_original_timestamps", None)
        if isinstance(_original_ts, dict) and _original_ts:
            _prefix = f"{instance_id}:"
            _stale_ts_keys = [
                k for k in _original_ts if k.startswith(_prefix)
            ]
            for _k in _stale_ts_keys:
                _original_ts.pop(_k, None)
        self.release_context_usage_cache(instance_id)
        # Question-tool cleanup (F5): drop any pending QuestionPack and the
        # pause-requested flag so the dicts cannot grow unbounded across many
        # short-lived instances. The pause flag in particular must be
        # cleared on terminate — otherwise a future instance that re-uses
        # the same id (unlikely but possible across daemon restarts) would
        # inherit a stuck "pause requested" state and immediately re-pause
        # on the first tool call.
        self._question_manager.clear_question_pack(instance_id)
        self.clear_question_pause_requested(instance_id)
        # C2 fix — drop the deferred-pause marker so a fresh instance that
        # reuses this id (e.g., after daemon restart, or a manual hard-
        # delete + recreate) cannot inherit a stuck "pause pending" state
        # that would silently trigger ``pause_instance_cascade`` on the
        # next graph completion for the new instance.
        self._deferred_question_pause.discard(instance_id)
        # Watchover — same C2-safe cleanup: drop any deferred termination
        # marker so a fresh instance reusing this id cannot inherit a stuck
        # "terminate pending" state.
        self._deferred_watchover_terminate.discard(instance_id)
        # Note: request_registry.cancel_by_instance() is called separately
        # by the lifecycle callers because the cancellation reason differs
        # per call site (USER_STOPPED vs SESSION_TERMINATED). Centralizing
        # that here would require a reason arg and propagate churn without
        # value — leave the call site to keep its existing reason.
        return {
            "graph_task": task,
            "cleared_injection": cleared_injection,
            "context_usage_cleared": True,
        }

    def _cleanup_stale_injections(self, ttl_seconds: int | None = None) -> int:
        """Drop injection queues older than ``ttl_seconds`` (S1).

        Runs once per :meth:`_cleanup_cached_instances` cycle (every ~10
        minutes). Sweeps injections that escaped per-instance cleanup —
        typical cause is an instance stuck in ``WAITING_CHILDREN`` that
        never advanced to a clean terminate/pause. The 1-hour window is
        long enough that an active in-progress injection is never swept
        out from under the agent_node, but short enough that stranded
        entries don't accumulate across the daemon lifetime.

        For the FIFO queue, an instance is considered stale when the
        OLDEST entry (the head of the queue) is older than the TTL — all
        younger entries are swept together. That matches the original
        single-slot semantics: the age is defined by the most-stale
        pending message.

        SYNCHRONOUSITY INVARIANT: this method MUST remain fully
        synchronous — no ``await`` may be inserted between the
        ``stale: list[str] = []`` build loop and the subsequent
        ``self._pending_injections.pop(iid, None)`` pop loop. The
        two-loop pattern (build-then-pop) is only safe because the dict
        is not mutated between iterations; an ``await`` between the loops
        would allow a concurrent ``set_injection`` to either land a
        message we then drop (lost message) or land in the to-be-swept
        set and leak past the pop (drained entry). If this method ever
        needs async behavior, refactor to a single
        ``items()`` + ``pop()`` pass under ``copy()`` of the keys.
        See agents-ensemble deep review 2026-07-22 (oracle HAZARD 3).

        Args:
            ttl_seconds: Override for tests; defaults to
                :data:`_INJECTION_TTL_SECONDS` (1h).

        Returns:
            Number of stale queues removed.
        """
        ttl = ttl_seconds if ttl_seconds is not None else self._INJECTION_TTL_SECONDS
        if ttl <= 0:
            return 0
        now = datetime.now(timezone.utc)
        stale: list[str] = []
        for iid, queue in self._pending_injections.items():
            # Empty queue should not be tracked — drop it.
            if not queue:
                stale.append(iid)
                continue
            # Use the oldest entry (head of queue) to decide staleness.
            head = queue[0]
            ts_raw = head.get("timestamp")
            if not ts_raw:
                stale.append(iid)
                continue
            try:
                ts = datetime.fromisoformat(ts_raw)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                # Unparseable — treat as stale so it can't accumulate
                # forever. Logs at debug to avoid noise.
                logger.debug(
                    f"Injection TTL sweep: unparseable timestamp for "
                    f"instance {iid[:8]}..., treating as stale"
                )
                stale.append(iid)
                continue
            if (now - ts).total_seconds() > ttl:
                stale.append(iid)

        for iid in stale:
            self._pending_injections.pop(iid, None)
            # Context Injection Restructure — Phase 3 (B2 fix): piggy-
            # back on the existing TTL sweep so a stranded skill result
            # for the same stale instance is cleared in lockstep. The
            # value carries no timestamp of its own, so we rely on the
            # parent ``_pending_injections`` entry's age as the proxy.
            # ``getattr`` keeps the cleanup defensive against test
            # stubs that skip the full ``__init__``.
            _ctx_skill_results = getattr(
                self, "_context_skill_results", None
            )
            if _ctx_skill_results is not None:
                _ctx_skill_results.pop(iid, None)
            # skill_search_interval (Phase 4b): drop the per-instance
            # message counter alongside the cached skill-search result.
            _skill_search_counts = getattr(
                self, "_skill_search_message_counts", None
            )
            if _skill_search_counts is not None:
                _skill_search_counts.pop(iid, None)
            # W1 fix: drop the explicit-load marker alongside the
            # counter — same defensive ``getattr`` pattern.
            _explicit_loaded = getattr(
                self, "_explicit_skill_loaded", None
            )
            if _explicit_loaded is not None:
                _explicit_loaded.discard(iid)

        if stale:
            logger.info(
                f"Injection TTL sweep: dropped {len(stale)} stale queue(s) "
                f"(>{ttl}s old)"
            )
        return len(stale)

    def _release_cached_instance(self, instance_id: str) -> None:
        """Release in-memory graph for a cached/non-active instance after TTL expires.

        This removes the graph from memory while keeping the database record intact.
        The instance can be "hot resumed" if under TTL (graph still in memory),
        or "cold resumed" if over TTL (graph reloaded from checkpoint on next use).

        Args:
            instance_id: The ID of the cached instance to release.
        """
        # W1: Centralized per-instance state cleanup pulls the graph task,
        # any pending injection, and the context-usage cache entry in one go.
        self._cleanup_instance_state(instance_id)

        # Remove from instances dict if present
        if instance_id in self.instances:
            del self.instances[instance_id]
            logger.info(f"Released in-memory graph for cached instance {instance_id[:8]}...")

        # Cancel any active requests (shouldn't exist for paused instance but safety first)
        # Using SESSION_TERMINATED since this is a TTL-based eviction - the session
        # is being terminated due to inactivity/paused duration exceeding the limit
        self._request_registry.cancel_by_instance(
            instance_id,
            CancellationReason.SESSION_TERMINATED
        )

    def release_context_usage_cache(self, instance_id: str) -> None:
        """Drop the per-instance context-usage dedup entry.

        Called whenever an instance is released, terminated, paused, or
        otherwise cleaned up so the dedup map doesn't grow without
        bound over a long-lived daemon. Mirrors the lifetime of
        ``_graph_tasks``.
        """
        self._last_context_usage.pop(instance_id, None)

    async def _run_skill_orphan_sweep(self) -> None:
        """Periodic sweep of stale ``selected=True`` usage records.

        Belt-and-suspenders maintenance job for the Phase 4 metrics
        pipeline. The completion-time counter bumps in
        :meth:`SkillMetricsService.record_task_completion` can leave
        "pending" usage rows behind when a daemon is killed mid-write
        or the async task crashes before finalization. This sweep
        finds any :class:`SkillUsageRecord` rows older than 24h that
        are still in the pending state (``selected=True``,
        ``feedback_applied=False``, ``superseded=False``, no
        iterations) and flips them to ``superseded=True`` so they
        stop skewing the completion-rate aggregation.

        The actual sweep logic lives on
        :meth:`SkillMetricsService.sweep_orphaned_skill_records`; this
        wrapper only guards the ``_skill_metrics_service is None``
        edge case (partial-init during early boot or in tests that
        don't wire the metrics service).

        Registered with :class:`MaintenanceService` so the
        ``_is_idle`` gate keeps it from running while there's
        in-flight work. ``max_age_hours=24`` matches the orphan
        threshold used by the metrics service internally — anything
        older than 24h and still pending is, by definition, an
        artifact.
        """
        svc = getattr(self, "_skill_metrics_service", None)
        if svc is None:
            logger.debug(
                "_run_skill_orphan_sweep: no skill_metrics_service "
                "wired — skipping"
            )
            return
        try:
            swept = await svc.sweep_orphaned_skill_records()
            if swept > 0:
                logger.info(
                    f"_run_skill_orphan_sweep: finalized {swept} "
                    f"orphan usage record(s)"
                )
        except Exception as exc:
            # Soft-fail: a broken sweep must never break the
            # maintenance loop. The next cycle retries cleanly.
            logger.warning(
                f"_run_skill_orphan_sweep failed: {exc}"
            )

    async def _run_skill_metric_scan(self) -> None:
        """Periodic Phase 4 trigger scan — enqueues analysis jobs.

        Registered with :class:`MaintenanceService` so the
        ``_is_idle`` gate keeps it from running while there's
        in-flight work. Each registered project is processed
        independently so a misconfigured project can't poison the
        others; failures are isolated per-project and logged at
        WARNING.

        Steps per project:

        1. Run ``trigger_engine.evaluate_all(project_id)`` — returns
           the list of flagged skills for that scope.
        2. For each flagged skill, enqueue a downstream job on the
           project's ``system_parallel_queue``:

           * ``trigger_action == "analyze"`` → ``job_type='skill_analysis'``
           * ``trigger_action == "evolve_fix"`` → ``job_type='skill_evolution'``
             with ``evolution_type='FIX'`` in the metadata.

        All enqueues resolve the queue via
        ``JobQueueRepository.get_by_name(project_id, 'system_parallel_queue')`` —
        this is the constraint from the Phase 4 plan ("All job
        enqueues MUST use ``system_parallel_queue``"). The message
        carried on the JobItem is a short human-readable summary so
        downstream handlers (Phase 5) can route without having to
        re-derive the trigger context.

        Soft-fails on every failure mode: missing trigger engine,
        missing queue repo, missing job repo, missing trigger scan
        config. The maintenance loop will retry on the next cycle.
        """
        if getattr(self, "_skill_trigger_engine", None) is None:
            logger.debug(
                "_run_skill_metric_scan: skill evolution not "
                "configured — skipping"
            )
            return

        # Locate the projects. ``list_projects(status="active")`` keeps
        # the scan proportional to live projects; a future enhancement
        # could widen this to per-project opt-in toggles.
        try:
            project_repo = getattr(self, "_project_repository", None)
            if project_repo is None:
                logger.debug(
                    "_run_skill_metric_scan: no project_repository "
                    "wired — skipping"
                )
                return

            projects = await asyncio.to_thread(
                project_repo.list_projects, status="active", limit=1000
            )
        except Exception as exc:
            logger.warning(
                f"_run_skill_metric_scan: failed to list projects: "
                f"{exc}"
            )
            return

        # Resolve the queue + job repos from the JobQueueService.
        job_queue_service = getattr(self, "_job_queue_service", None)
        if job_queue_service is None:
            logger.debug(
                "_run_skill_metric_scan: no job_queue_service — "
                "skipping"
            )
            return
        queue_repo = getattr(job_queue_service, "_queue_repo", None)
        job_repo = getattr(job_queue_service, "_repository", None)
        if queue_repo is None or job_repo is None:
            logger.debug(
                "_run_skill_metric_scan: queue/job repos missing — "
                "skipping"
            )
            return

        trigger_engine = self._skill_trigger_engine

        for project in projects or []:
            project_id = getattr(project, "project_id", None) or getattr(
                project, "id", None
            )
            if not project_id:
                continue
            try:
                flagged = await trigger_engine.evaluate_all(project_id)
            except Exception as exc:
                logger.warning(
                    f"_run_skill_metric_scan: evaluate_all failed "
                    f"for project {project_id}: {exc}"
                )
                continue
            if not flagged:
                continue

            # Resolve the project's parallel queue once for the batch.
            try:
                parallel_queue = await asyncio.to_thread(
                    queue_repo.get_by_name,
                    project_id,
                    "system_parallel_queue",
                )
            except Exception as exc:
                logger.warning(
                    f"_run_skill_metric_scan: failed to resolve "
                    f"system_parallel_queue for project "
                    f"{project_id}: {exc}"
                )
                continue
            if parallel_queue is None:
                logger.warning(
                    f"_run_skill_metric_scan: project {project_id} "
                    f"has no system_parallel_queue — skipping enqueue"
                )
                continue

            for item in flagged:
                try:
                    action = item.get("trigger_action")
                    skill_id = item.get("skill_id")
                    if not skill_id or not action:
                        continue
                    payload = {
                        "skill_id": skill_id,
                        "reason": item.get("reason", ""),
                        "stats": item.get("stats", {}),
                        "trigger_name": item.get("trigger_name", ""),
                    }
                    if action == "analyze":
                        job_type = "skill_analysis"
                    elif action == "evolve_fix":
                        job_type = "skill_evolution"
                        payload["evolution_type"] = "FIX"
                    else:
                        # Unknown action — surface as analysis so the
                        # downstream pipeline can decide.
                        job_type = "skill_analysis"

                    # Prefer the SkillJobDispatcher (Phase 5) — it's
                    # the single front-door for skill-evolution jobs
                    # and enforces the parallel-queue routing rule.
                    # Fall back to direct ``job_repo.create`` when the
                    # dispatcher hasn't been wired yet (tests, early
                    # boot) — the legacy code path is preserved here
                    # so the existing Phase 4 tests keep working.
                    dispatcher = getattr(
                        self, "_skill_job_dispatcher", None
                    )
                    if dispatcher is not None:
                        if action == "analyze":
                            await dispatcher.enqueue_analysis(
                                project_id=project_id,
                                skill_id=skill_id,
                                reason=payload.get("reason", ""),
                                stats=payload.get("stats", {}),
                            )
                        elif action == "evolve_fix":
                            await dispatcher.enqueue_evolution(
                                project_id=project_id,
                                skill_id=skill_id,
                                evolution_type="FIX",
                                direction=payload.get("reason", ""),
                            )
                        else:
                            # Unknown action — surface as analysis so
                            # the downstream pipeline can decide.
                            await dispatcher.enqueue_analysis(
                                project_id=project_id,
                                skill_id=skill_id,
                                reason=f"unknown action: {action}",
                                stats=payload.get("stats", {}),
                            )
                    else:
                        # Fallback: create the JobItem directly. Used
                        # when the dispatcher isn't wired (e.g. during
                        # tests or before ``set_job_queue_service``
                        # runs).
                        logger.warning(
                            "_run_skill_metric_scan: dispatcher "
                            "unavailable, falling back to direct "
                            "job_repo.create"
                        )
                        message = (
                            f"[skill_metric_scan] {action} "
                            f"skill={skill_id} reason={payload['reason']}"
                        )
                        await asyncio.to_thread(
                            job_repo.create,
                            agent_id="skill-keeper",
                            agent_dir="agents/skill-keeper",
                            message=message,
                            source="skill_metric_scan",
                            project_id=project_id,
                            priority=4,
                            job_metadata=payload,
                            queue_id=parallel_queue.queue_id,
                            idempotency_key=None,
                            job_type=job_type,
                            instance_id=None,
                            max_retries=2,
                        )
                except Exception as exc:
                    logger.warning(
                        f"_run_skill_metric_scan: enqueue failed for "
                        f"skill {item.get('skill_id', '?')} on project "
                        f"{project_id}: {exc}"
                    )
    
    async def _cleanup_cached_instances(self) -> None:
        """Background task to release in-memory graphs for non-active cached instances exceeding TTL.
        
        Cleans up instances in terminal/inactive states: COMPLETED, ERROR, TERMINATED, FAILED, PAUSED.
        Only affects in-memory cache — database records remain intact.
        """
        while not self._shutting_down:
            try:
                await asyncio.sleep(600)  # Every 10 minutes
                if not self._instance_repository:
                    continue
                
                # Non-active states to clean up
                non_active_statuses = [
                    InstanceStatus.COMPLETED.value,
                    InstanceStatus.ERROR.value,
                    InstanceStatus.TERMINATED.value,
                    InstanceStatus.FAILED.value,
                    InstanceStatus.PAUSED.value,
                ]
                
                now = datetime.now(timezone.utc)
                ttl_seconds = INSTANCE_CACHE_TTL_HOURS * 3600
                released_count = 0
                
                # Query each non-active status
                for status in non_active_statuses:
                    instances, _ = self._instance_repository.list(status=status)
                    
                    for instance in instances:
                        # Only release if graph is in memory
                        if instance.instance_id not in self.instances:
                            continue
                        
                        # Use paused_at for PAUSED, updated_at for all others
                        if status == InstanceStatus.PAUSED.value:
                            timestamp_str = instance.paused_at or instance.updated_at
                        else:
                            timestamp_str = instance.updated_at
                        
                        # Skip if timestamp is missing
                        if not timestamp_str:
                            continue
                        
                        # Parse the timestamp
                        try:
                            timestamp = datetime.fromisoformat(timestamp_str)
                        except (ValueError, TypeError):
                            logger.warning(
                                f"Invalid timestamp for cached instance {instance.instance_id[:8]}..., skipping"
                            )
                            continue
                        
                        if (now - timestamp).total_seconds() > ttl_seconds:
                            self._release_cached_instance(instance.instance_id)
                            released_count += 1
                
                if released_count > 0:
                    logger.info(
                        f"Released {released_count} cached instance(s) exceeding "
                        f"{INSTANCE_CACHE_TTL_HOURS}h TTL"
                    )

                # Evict idle opencode session managers (1h TTL)
                if self._opencode_registry is not None:
                    try:
                        evicted = await self._opencode_registry.evict_idle_sessions(ttl_seconds=3600)
                        if evicted > 0:
                            logger.info(f"Evicted {evicted} idle opencode session managers")
                    except Exception as e:
                        logger.warning(f"Failed to evict idle opencode sessions: {e}")

                # S1: TTL sweep orphaned injection queues. Runs in the same
                # 10-minute cadence as the cached-instance cleanup so
                # stranded ``_pending_injections`` entries (typical cause:
                # WAITING_CHILDREN instance that never advanced) can't
                # accumulate over a long-lived daemon.
                try:
                    self._cleanup_stale_injections()
                except Exception as e:
                    logger.warning(f"Injection TTL sweep failed: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Cached instance cleanup failed: {e}")

    def set_job_queue_service(self, service: Any) -> None:
        """Set the JobQueueService reference.

        This is called by api.py after both SessionManager and JobQueueService
        are created during application startup. The service is also wired into
        the SourceRegistry so that SchedulerAdapter can route jobs through the
        job queue when project_id is configured.

        Phase 5 also constructs the :class:`SkillJobDispatcher` here —
        it requires both ``job_service`` (for ``enqueue()``) and the
        ``JobQueueRepository`` (for ``system_parallel_queue``
        resolution), and the repository is only reachable via the
        JobQueueService. ``_job_queue_service`` is ``None`` during
        ``__init__`` so we cannot construct the dispatcher there.

        Args:
            service: The JobQueueService instance to use for lock management.
        """
        self._job_queue_service = service
        # Wire JobQueueService into SourceRegistry for scheduler queue routing (Task 5.4)
        if hasattr(self, 'source_registry') and self.source_registry:
            self.source_registry._job_queue_service = service
            logger.info("JobQueueService wired into SourceRegistry for scheduler routing")

        # Wire SkillJobDispatcher (Phase 5) — the single front-door
        # for skill-evolution JobItems. Defensive: a missing
        # ``_queue_repo`` attribute on the service (shouldn't
        # happen in production, but defensive against test doubles)
        # leaves the dispatcher unset so callers fall back to the
        # "not yet initialized" soft-fail path.
        queue_repo = getattr(service, "_queue_repo", None) if service is not None else None
        if queue_repo is None:
            logger.warning(
                "SkillJobDispatcher not wired: JobQueueService has no "
                "_queue_repo attribute"
            )
            self._skill_job_dispatcher = None
        else:
            self._skill_job_dispatcher = SkillJobDispatcher(
                job_service=service,
                queue_repo=queue_repo,
            )
            logger.info("SkillJobDispatcher wired to manager")

        # Wire the metrics service's job dispatcher handle so the
        # CAPTURED flow (Phase 5) can actually enqueue capture jobs
        # instead of just computing eligibility. The metrics service
        # is only present when ``skill_evolution`` is configured, so
        # guard with getattr.
        metrics = getattr(self, "_skill_metrics_service", None)
        if metrics is not None and self._skill_job_dispatcher is not None:
            metrics.set_job_dispatcher(self._skill_job_dispatcher)

        # Wire the BlueprintTriggerCoordinator (C7) so it can probe
        # job status during startup reconciliation. Defensive getattr
        # because the coordinator may not exist on test doubles that
        # bypass the normal __init__ flow.
        coordinator = getattr(self, "_blueprint_trigger_coordinator", None)
        if coordinator is not None and service is not None:
            coordinator.set_job_queue_service(service)
            # C2: queue-aware reconciliation must run only after the service is wired.
            try:
                asyncio.ensure_future(coordinator.reconcile_on_startup())
            except Exception:
                logger.warning("Blueprint lease reconcile scheduling failed", exc_info=True)

        # Wire the BlueprintScanService so it can enqueue blueprinter jobs
        # after a coordinator claim. Defensive getattr: the scan service is
        # only constructed when blueprint auto-rebuild is configured, so test
        # doubles that bypass __init__ may not have the attribute.
        scan_service = getattr(self, "_blueprint_scan_service", None)
        if scan_service is not None and service is not None:
            scan_service.set_job_queue_service(service)

        logger.info("JobQueueService connected to SessionManager")

    def set_job_feedback_observer(self, observer: Any) -> None:
        """Set the JobFeedbackObserver reference.

        Wired by ``daemon/api.py`` during FastAPI lifespan startup, AFTER
        the observer is constructed. The observer is the sole owner of
        ``_finalize_job`` (the PROCESSING → COMPLETED/FAILED terminal
        transition path).

        Why the manager needs this: ``ChildReportsService`` runs as part
        of the dependency-bus path. When the bus fires the last watcher
        for a parent, the bus must explicitly re-trigger
        ``_finalize_job`` via the observer (there is no separate
        callback mechanism that would do this automatically). ``ChildReportsService`` reaches the observer through
        ``getattr(self._manager, "_job_feedback_observer", None)`` — a
        defensive lookup that gracefully no-ops in unit tests where the
        observer is not wired.

        Args:
            observer: The JobFeedbackObserver instance. Stored on
                ``self._job_feedback_observer`` for service-level access.
        """
        self._job_feedback_observer = observer
        logger.info("JobFeedbackObserver wired into InstanceManager")

    def set_job_queue_mgmt_service(self, service: Any) -> None:
        """Set the job queue management service.
        
        Args:
            service: The JobQueueMgmtService instance.
        """
        self._job_queue_mgmt_service = service
        logger.info("JobQueueMgmtService connected to SessionManager")

    def set_notification_broadcaster(self, broadcaster: Any) -> None:
        """Set the NotificationBroadcaster reference.

        This is called by api.py after both InstanceManager and NotificationBroadcaster
        are created during application startup. Uses a stored reference instead of
        accessing app.state to avoid circular import issues in tests.

        Args:
            broadcaster: The NotificationBroadcaster instance.
        """
        self._notification_broadcaster = broadcaster
        logger.info("NotificationBroadcaster connected to InstanceManager")

    def set_dead_letter_service(self, service: Any) -> None:
        """Set the dead letter service.

        Args:
            service: The DeadLetterService instance.
        """
        self._dead_letter_service = service
        logger.info("DeadLetterService connected to InstanceManager")

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

    def _on_stale_task_cancelled_and_retried(
        self, cancelled_task_id: int, retry_task_id: int, origin: str
    ) -> None:
        """Bridge from StaleTaskRecovery thread to ``DependencyBus.cancel_for_source``.

        Called on the recovery thread when a stale task is force-cancelled
        and a retry is scheduled. Without this notification, the bus's
        PENDING watchers keyed on the cancelled ``source_task_id`` stay
        PENDING forever — the retry's natural completion fires
        ``emit_terminal`` for its OWN task id and cannot match the
        original watcher. The parent would remain in
        ``waiting_children`` indefinitely (production incident 2026-06-26,
        instance 06f500af stuck for hours despite all 8 children
        completing successfully).

        The bus is async and the recovery thread is a plain
        ``threading.Thread`` — ``MainLoopBridge.run_async_no_wait`` hops
        to the asyncio event loop, mirroring the existing
        ``_on_stale_task_permanent_failure`` bridge above.

        This is a thin sync wrapper around the shared
        :func:`daemon.services.dependency_bus.cancel_bus_watchers_for_task_async`
        helper — both ``StaleTaskRecovery`` and ``WorkerPool`` route
        through it so the two callsites cannot drift in log format or
        error handling. The ``origin`` tag distinguishes the call site in
        the bus log line.

        Args:
            cancelled_task_id: The id of the task that was just cancelled
                by the recovery action. Bus watchers against this id are
                transitioned to CANCELLED.
            retry_task_id: The id of the newly-scheduled retry task. Not
                used by the bus but logged for traceability.
            origin: Short tag identifying the recovery sub-flow
                (``"stale_recovery"``, ``"worker_cancelled"``,
                ``"startup_stale_running"``, ``"startup_orphan_cancelled"``).
        """
        from .services.dependency_bus import cancel_bus_watchers_for_task_async
        from .services.main_loop_bridge import MainLoopBridge

        MainLoopBridge.run_async_no_wait(
            cancel_bus_watchers_for_task_async(
                cancelled_task_id=cancelled_task_id,
                retry_task_id=retry_task_id,
                origin=origin,
            )
        )

    def _ensure_postgres_columns(self) -> None:
        """Idempotent Postgres schema evolution.

        ``SQLModel.metadata.create_all`` only creates tables that don't
        exist; it does not add columns to existing tables. The migration
        runner (run_pending_migrations) skips non-SQLite engines. So for
        production Postgres we explicitly add columns that newer code
        depends on. The ``IF NOT EXISTS`` clauses make this safe to
        re-run on every startup.

        Currently:
        - task.last_heartbeat_at: per-task liveness signal for
          StaleTaskRecovery (Option 1 of the per-instance guard
          follow-up). Without this column, the recovery predicate
          fails (Postgres rejects the query) and the daemon is
          stuck. See
          docs/bugs/child-completion-report-lost-under-concurrent-task-processing.md
          §9.1 and the per-instance guard follow-up.
        - idx_task_running_heartbeat: partial index used by the
          recovery predicate; keeps stale-task lookups O(log n)
          even as completed/old rows accumulate.
        - task.work_id + idx_task_work_id (Phase 1 Batch 2,
          2026-06-27): stable cross-system work identifier (UUID4
          string) so the virtual job resolver can correlate a Task
          row with a corresponding JobItem row without depending on
          the integer primary key. The column is declared
          ``unique=True, nullable=False`` on the Task SQLModel, so
          fresh Postgres databases get column + index via
          ``SQLModel.metadata.create_all()``; existing databases
          need the ADD COLUMN + backfill (gen_random_uuid) +
          CREATE UNIQUE INDEX + SET NOT NULL chain. The SQLite
          counterpart lives in
          ``daemon/migrations/versions/20260627_000001_virtual_job_work_id.sql``.
          See feature/virtual-job-management-surface.
        - job_watchers.job_id FK drop (Phase 2 Batch 1, 2026-06-27):
          the FOREIGN KEY constraint from ``job_watchers.job_id`` to
          ``job_queue_items.job_id`` is dropped so the column can
          hold a virtual ``work_id`` (a UUID4 string that may not
          have a matching ``job_queue_items`` row — tasks-only work).
          SQLite uses a table-rebuild pattern; here on PostgreSQL we
          issue a single DROP CONSTRAINT. **This is the ONE DROP
          statement in an otherwise ADD-only method.** The IF
          EXISTS guard makes it idempotent (a no-op once the
          constraint is already gone). The SQLite counterpart lives
          in
          ``daemon/migrations/versions/20260627_000002_drop_job_watchers_fk.sql``.
        - task.is_deferred (Phase 3 Part B1, 2026-06-27): defer-queue
          lane marker. When True the row belongs to the defer-queue
          lane and the worker pool's idle gate holds it until every
          non-defer queue is empty. Mirrors the
          ``last_heartbeat_at`` / ``work_id`` pattern: ADD COLUMN
          (idempotent via ``IF NOT EXISTS``) plus a CREATE INDEX for
          the defer-gate predicate (also ``IF NOT EXISTS``). Fresh
          PostgreSQL databases get the column + index automatically
          from ``SQLModel.metadata.create_all()`` because the
          ``is_deferred`` field is declared with ``index=True`` on
          the Task SQLModel. Existing databases need the explicit
          statements here because the .sql migration runner is a
          NO-OP on PG. SQLite counterpart lives in
          ``daemon/migrations/versions/20260627_000003_task_is_deferred.sql``.
          See feature/virtual-job-management-surface.
        - task.suspension_reason and task.resume_target_turn_id
          (Increment 4 turn-reconciler migration, 2026-08-01): nullable
          turn-suspension handles. ``suspension_reason`` records one of
          ``awaiting_answer``, ``awaiting_children``, or
          ``paused_external``; ``resume_target_turn_id`` stores the
          authoritative target ``Task.work_id``. Existing PostgreSQL
          databases receive both columns through the idempotent ALTER
          statements below, then the composite
          ``idx_task_resume_target`` index on
          ``(resume_target_turn_id, suspension_reason)``. The legacy
          paused-row backfill runs after the columns and index are
          present, setting ``paused_external`` and ``work_id`` wherever
          ``status='paused'`` and ``suspension_reason IS NULL``. Fresh
          databases get the fields and index from
          ``SQLModel.metadata.create_all()``. The SQLite counterpart,
          including the guarded ALTERs and the same idempotent backfill,
          lives in
          ``daemon/migrations/versions/20260801_000001_task_turn_handles.sql``.
        - job_queue_items.admission_state (Phase 2, 2026-06-28): the
          queue-proxy admission column. Dual-writes with ``status``
          in every write site; ``status`` becomes a write-only
          mirror and is dropped in Phase 5. SQLite counterpart lives
          in ``daemon/migrations/versions/20260628_000001_job_admission_state.sql``.
          See feature/job-as-queue-proxy.
        - ``idx_job_queue_admission_state`` (Phase 2, 2026-06-28):
          index supporting the future ``WHERE admission_state IN
          ('queued', 'active')`` predicates used by the work-resolver
          sweep. Mirrors ``idx_job_queue_status`` for the new column.
        - Constraint triggers (Phase 2, 2026-06-28, plan §8.7.1): the
          ``job_queue_items_active_lock_guard`` and
          ``job_locks_active_guard`` deferred CONSTRAINT TRIGGERs
          enforce the ``active ⇔ JobLock row`` invariant at COMMIT.
          First use of ``CREATE CONSTRAINT TRIGGER`` in the codebase
          (no precedent). Idempotent via CREATE OR REPLACE FUNCTION
          (functions) and DROP TRIGGER IF EXISTS + CREATE CONSTRAINT
          TRIGGER (triggers).
        - idx_infra_assets_attributes_gin /
          idx_infra_assets_relationships_gin: GIN indexes on the
          JSONB ``attributes`` and ``relationships`` columns of
          ``infra_assets``. Defined in
          ``daemon/repositories/infra/models.py`` via
          ``Index(..., postgresql_using='gin')`` in
          ``__table_args__``, so they are created on fresh Postgres
          databases by ``SQLModel.metadata.create_all``. They are
          NOT created on existing Postgres databases (because
          ``create_all`` is a no-op for tables that already exist
          and the migration runner is SQLite-only), so we create
          them here for parity. SQLite does not support GIN and
          is never routed through this method (gated by the
          ``is_postgres`` check at the call site).
        - JSON → JSONB column conversion (Phase 1, 2026-06-20):
          PL/pgSQL DO block that idempotently converts the 17
          ``Column(JSON)`` columns that were retyped to
          ``JSONBType`` at the model level. See the DO block
          comment for the rewrite-cost and invalid-JSON warnings.

        When a new column needs this treatment: add the IF NOT EXISTS
        ALTER + (optional) CREATE INDEX here. Do NOT add raw
        "ALTER TABLE" without IF NOT EXISTS — that breaks re-runs
        on databases that already have the column.

        Failure semantics: this method does NOT catch exceptions. If
        any statement fails (permission denied, connection lost, SQL
        syntax error, table missing), the exception propagates and
        startup aborts. Better to fail loudly at startup than to
        continue and crash on the first query that references the
        missing column. ``IF NOT EXISTS`` makes the idempotent case
        a no-op, so there's nothing to swallow.
        """
        from sqlalchemy import text

        statements = [
            # task.last_heartbeat_at
            "ALTER TABLE task ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMP",
            # Partial index on RUNNING tasks for the recovery predicate
            (
                "CREATE INDEX IF NOT EXISTS idx_task_running_heartbeat "
                "ON task(last_heartbeat_at) WHERE status = 'running'"
            ),
            # source_configs.autostart: whether a source auto-starts on boot
            "ALTER TABLE source_configs ADD COLUMN IF NOT EXISTS autostart BOOLEAN DEFAULT TRUE",
            # instance_ui_prefs.icon_tag: optional UI icon for existing prefs tables
            "ALTER TABLE instance_ui_prefs ADD COLUMN IF NOT EXISTS icon_tag VARCHAR",
            # instances.agent_tag: agent version tag for directory-suffix versioning
            "ALTER TABLE instances ADD COLUMN IF NOT EXISTS agent_tag VARCHAR",
            # instance_execution_leases: the Execution Gate's per-instance
            # lease table. SQLite gets it via the .sql migration at
            # ``daemon/migrations/versions/20260614_000002_create_instance_execution_leases.sql``;
            # on Postgres we create it inline here because the
            # migration runner is SQLite-only. **If you change the
            # schema, update BOTH definitions.**
            (
                "CREATE TABLE IF NOT EXISTS instance_execution_leases ("
                "instance_id TEXT PRIMARY KEY, "
                "holder_id TEXT NOT NULL, "
                "holder_kind TEXT NOT NULL "
                "CHECK(holder_kind IN ('message_job', 'task', 'resume')), "
                "acquired_at TIMESTAMP NOT NULL, "
                "heartbeat_at TIMESTAMP NOT NULL, "
                "process_id INTEGER)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_lease_holder_id "
                "ON instance_execution_leases(holder_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_lease_holder_kind "
                "ON instance_execution_leases(holder_kind)"
            ),
            # GIN indexes on infra_assets JSONB columns. These are
            # declared on the model via ``Index(..., postgresql_using='gin')``
            # in ``__table_args__`` and are emitted by
            # ``SQLModel.metadata.create_all`` on fresh databases. On
            # existing Postgres databases the tables are already
            # present so ``create_all`` is a no-op for them; we create
            # the indexes here so production Postgres gets the same
            # JSONB containment / path-query performance as fresh
            # databases. The model index names match these exactly;
            # see ``daemon/repositories/infra/models.py``.
            (
                "CREATE INDEX IF NOT EXISTS idx_infra_assets_attributes_gin "
                "ON infra_assets USING gin (attributes)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_infra_assets_relationships_gin "
                "ON infra_assets USING gin (relationships)"
            ),
            # ── Concurrency-remediation migrations (2026-06-19) ──────────
            # These .sql migration files are skipped by the runner on PostgreSQL
            # (runner.py lines 446-448). The runner is intentionally a NO-OP for
            # non-SQLite: PostgreSQL schema evolution is handled here via
            # _ensure_postgres_columns() and fresh DBs get everything via
            # SQLModel.metadata.create_all(). See the DUAL-DRIVER NOTES in each
            # .sql file for the exact equivalent statements.
            #
            # STEP 1: Add missing columns (safe to re-run: IF NOT EXISTS)
            # task.version: optimistic lock counter for ORM-flushed commits
            "ALTER TABLE task ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 0",
            # job_queue_items.version: same, plus partial unique index refinement
            "ALTER TABLE job_queue_items ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 0",
            # infra_assets.version: M5 optimistic locking (NOT DEFAULT 1, unlike Task)
            "ALTER TABLE infra_assets ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1",
            # job_locks.lock_slot: atomic slot-claim via INSERT ON CONFLICT DO NOTHING
            "ALTER TABLE job_locks ADD COLUMN IF NOT EXISTS lock_slot INTEGER NOT NULL DEFAULT 0",
            # STEP 2: Deduplicate pre-existing duplicate rows BEFORE creating
            # UNIQUE indexes. Uses PostgreSQL ctid (physical row address).
            # These are safe on dev databases where duplicates are acceptable.
            (
                "DELETE FROM instance_mappings "
                "WHERE ctid NOT IN ("
                "SELECT max(ctid) FROM instance_mappings "
                "GROUP BY source_id, external_user_id)"
            ),
            (
                "DELETE FROM job_watchers "
                "WHERE ctid NOT IN ("
                "SELECT max(ctid) FROM job_watchers GROUP BY job_id, instance_id)"
            ),
            (
                "DELETE FROM projects "
                "WHERE ctid NOT IN ("
                "SELECT max(ctid) FROM projects GROUP BY name)"
            ),
            # STEP 3: Add UNIQUE indexes (IF NOT EXISTS = idempotent)
            # C5: atomic acquire_queue_lock via slot claim
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_job_locks_slot "
                "ON job_locks(project_id, queue_id, lock_slot)"
            ),
            # C9: atomic create_instance_mapping via upsert
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_instance_mappings_source_user "
                "ON instance_mappings(source_id, external_user_id)"
            ),
            # C7: atomic create_job_watcher via upsert
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_job_watchers_job_instance "
                "ON job_watchers(job_id, instance_id)"
            ),
            # C8 + H14: atomic project create/update via upsert
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_name "
                "ON projects(name)"
            ),
            # STEP 4: Refine idempotency partial unique index (20260619_120000).
            # The old index predicate WHERE idempotency_key IS NOT NULL blocked
            # soft-delete → recreate with same key. New predicate adds deleted_at.
            "DROP INDEX IF EXISTS idx_job_idempotency",
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_job_idempotency "
                "ON job_queue_items(idempotency_key) "
                "WHERE idempotency_key IS NOT NULL AND deleted_at IS NULL"
            ),
            # ── JSON → JSONB column migration (Phase 1, 2026-06-20) ─────────
            # NOTE: create_all() runs FIRST (creating jsonb columns on fresh
            # DBs via JSONBType), THEN this hook runs (converting json→jsonb on
            # existing DBs). Fresh DBs skip this DO block since the WHERE
            # data_type='json' filter matches nothing.
            #
            # json→jsonb is a FULL TABLE REWRITE with ACCESS EXCLUSIVE lock. For
            # large tables it may take seconds. Only runs once — idempotent on
            # subsequent startups (WHERE data_type='json' filter excludes
            # already-converted columns).
            #
            # WARNING: Invalid JSON data in any of these columns will cause the
            # ALTER TYPE to fail and block daemon startup. Backup the database
            # before the first migration run. The per-column EXCEPTION handler
            # inside the LOOP identifies the failing column in its error message.
            (
                "DO $$\n"
                "DECLARE\n"
                "    r RECORD;\n"
                "BEGIN\n"
                "    FOR r IN\n"
                "        SELECT table_name, column_name\n"
                "        FROM information_schema.columns\n"
                "        WHERE table_schema = 'public'\n"
                "          AND data_type = 'json'\n"
                "          AND (table_name, column_name) IN (\n"
                "              ('source_configs','config'),\n"
                "              ('instance_mappings','mapping_metadata'),\n"
                "              ('project_metadata_records','meta_value'),\n"
                "              ('projects','related_directories'),\n"
                "              ('projects','metadata'),\n"
                "              ('projects','relationships'),\n"
                "              ('project_history','entry_metadata'),\n"
                "              ('job_queue_items','metadata'),\n"
                "              ('dead_letter_items','metadata'),\n"
                "              ('job_watchers','watch_events'),\n"
                "              ('instances','metadata'),\n"
                "              ('message_queue','metadata'),\n"
                "              ('message_queue','images'),\n"
                "              ('mcp_servers','config'),\n"
                "              ('mcp_servers','config_schema'),\n"
                "              ('opencode_sessions','latest_response'),\n"
                "              ('opencode_sessions','questions')\n"
                "          )\n"
                "    LOOP\n"
                "        BEGIN\n"
                "            EXECUTE format(\n"
                "                'ALTER TABLE %I ALTER COLUMN %I TYPE jsonb USING %I::jsonb',\n"
                "                r.table_name, r.column_name, r.column_name\n"
                "            );\n"
                "        EXCEPTION WHEN OTHERS THEN\n"
                "            RAISE EXCEPTION 'jsonb migration failed for %.%: %',\n"
                "                r.table_name, r.column_name, SQLERRM;\n"
                "        END;\n"
                "    END LOOP;\n"
                "END $$;"
            ),
            # ── Dependency Bus enqueued_at marker (2026-06-21) ────
            # The Dependency Bus crash-recovery contract requires an
            # ``enqueued_at`` column on ``dependency_watchers`` to distinguish
            # "FIRED and enqueued" from "FIRED and crashed". The .sql migration
            # at ``daemon/migrations/versions/20260621_000003_add_enqueued_at.sql``
            # is skipped by the runner on PostgreSQL (runner.py lines 446-448),
            # so we add the column here for parity with fresh databases where
            # ``SQLModel.metadata.create_all()`` creates it from the model.
            # The Python field ``DependencyWatcher.enqueued_at`` is
            # ``str | None`` (ISO-8601 timestamp), nullable=True, default=None,
            # so the column is nullable TEXT — matches the existing .sql
            # migration which uses ``ALTER TABLE ... ADD COLUMN enqueued_at TEXT``.
            "ALTER TABLE dependency_watchers ADD COLUMN IF NOT EXISTS enqueued_at TEXT",
            # ── Virtual Job Work ID (Phase 1 Batch 2, 2026-06-27) ──────────
            # Phase 1 of feature/virtual-job-management-surface. The Task
            # table gets a stable cross-system work identifier (UUID4
            # string) so the virtual job resolver can correlate a Task
            # row with a corresponding JobItem row (or a logical work
            # unit that spans both) without depending on the integer
            # primary key. The SQLite path lives in
            # ``daemon/migrations/versions/20260627_000001_virtual_job_work_id.sql``;
            # this block is the PostgreSQL counterpart for existing
            # production databases (the .sql runner is a NO-OP on PG).
            # Fresh Postgres databases get the column + unique index via
            # ``SQLModel.metadata.create_all()`` from
            # ``Task.__table_args__`` / the work_id Field declaration.
            #
            # Order matters: ADD COLUMN (nullable) → backfill → unique
            # index → SET NOT NULL. The NOT NULL constraint can only be
            # added AFTER backfill guarantees no NULLs remain. The
            # IF NOT EXISTS clauses keep every statement idempotent on
            # re-run (this method runs on every PG startup).
            "ALTER TABLE task ADD COLUMN IF NOT EXISTS work_id TEXT",
            # Backfill historical rows with a real UUID4 so future
            # writes (which all go through uuid.uuid4()) don't collide.
            # ``gen_random_uuid()`` is built-in to PostgreSQL 13+ and
            # does NOT require the pgcrypto extension; this statement
            # works on every supported PostgreSQL deployment.
            "UPDATE task SET work_id = gen_random_uuid()::text WHERE work_id IS NULL",
            # Unique index matches the name used by the SQLite
            # migration so both paths converge on the same index.
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_task_work_id ON task(work_id)",
            # Promote to NOT NULL now that backfill is complete.
            "ALTER TABLE task ALTER COLUMN work_id SET NOT NULL",
            # ── Drop job_watchers.job_id FK (Phase 2 Batch 1, 2026-06-27) ──
            # The ONE DROP statement in this otherwise ADD-only method.
            # The default PostgreSQL auto-generated FK constraint name is
            # ``<table>_<column>_fkey`` (i.e. ``job_watchers_job_id_fkey``).
            # IF EXISTS keeps it idempotent: a no-op once the constraint
            # is already gone (re-run after first apply). Fresh Postgres
            # databases that get here after the model change has landed
            # will also no-op because ``SQLModel.metadata.create_all()``
            # creates the table without the FK in the first place.
            # SQLite path: ``daemon/migrations/versions/20260627_000002_drop_job_watchers_fk.sql``
            # (table-rebuild, since SQLite cannot DROP CONSTRAINT).
            "ALTER TABLE job_watchers DROP CONSTRAINT IF EXISTS job_watchers_job_id_fkey",
            # ── Defer Queue marker (Phase 3 Part B1, 2026-06-27) ──────
            # ``task.is_deferred``: defer-queue lane marker. Boolean,
            # NOT NULL DEFAULT false so existing rows backfill cleanly
            # (every pre-migration task is non-deferred). Matches the
            # SQLite ``ALTER TABLE task ADD COLUMN is_deferred BOOLEAN
            # DEFAULT 0 NOT NULL`` in
            # ``daemon/migrations/versions/20260627_000003_task_is_deferred.sql``.
            # Both dialects use the same logical default (false ↔ 0)
            # so a freshly-added column reads back as ``False`` from
            # the ORM regardless of which backend created it.
            "ALTER TABLE task ADD COLUMN IF NOT EXISTS is_deferred BOOLEAN NOT NULL DEFAULT false",
            # Plain index on is_deferred matching the model's
            # ``index=True``. The defer-queue idle-gate predicate
            # filters on ``WHERE status='pending' AND is_deferred=...``
            # every claim cycle, so an index keeps it O(log n) as the
            # task table grows. IF NOT EXISTS makes this a no-op on
            # re-run and on fresh databases where create_all already
            # created it from the model.
            "CREATE INDEX IF NOT EXISTS ix_task_is_deferred ON task(is_deferred)",
            # ── Background Queue marker (Phase 3 Part B2, 2026-06-27) ───
            # ``task.is_background``: background-queue lane marker.
            # Boolean, NOT NULL DEFAULT false so existing rows backfill
            # cleanly (every pre-migration task is non-background).
            # Matches the SQLite ``ALTER TABLE task ADD COLUMN
            # is_background BOOLEAN DEFAULT 0 NOT NULL`` in
            # ``daemon/migrations/versions/20260627_000004_task_is_background.sql``.
            # Both dialects use the same logical default (false ↔ 0)
            # so a freshly-added column reads back as ``False`` from
            # the ORM regardless of which backend created it. Mirrors
            # the ``is_deferred`` pattern exactly.
            "ALTER TABLE task ADD COLUMN IF NOT EXISTS is_background BOOLEAN NOT NULL DEFAULT false",
            # Plain index on is_background matching the model's
            # ``index=True``. The background-queue idle-gate predicate
            # filters on ``WHERE status='pending' AND is_background=...``
            # every claim cycle, so an index keeps it O(log n) as the
            # task table grows. IF NOT EXISTS makes this a no-op on
            # re-run and on fresh databases where create_all already
            # created it from the model.
            "CREATE INDEX IF NOT EXISTS ix_task_is_background ON task(is_background)",
            # ── Turn suspension handles (Increment 4, 2026-08-01) ─────
            # Explicit resume routing stores why a Task is suspended and
            # which authoritative ``work_id`` should be resumed. Both
            # columns are nullable and intentionally have no individual
            # indexes; the composite index matches the handle lookup.
            # Order matters: add both columns before creating the index and
            # before backfilling legacy paused rows. Every operation is
            # idempotent, and the SQLite counterpart lives in
            # ``20260801_000001_task_turn_handles.sql``.
            "ALTER TABLE task ADD COLUMN IF NOT EXISTS suspension_reason VARCHAR",
            "ALTER TABLE task ADD COLUMN IF NOT EXISTS resume_target_turn_id VARCHAR",
            (
                "CREATE INDEX IF NOT EXISTS idx_task_resume_target "
                "ON task (resume_target_turn_id, suspension_reason)"
            ),
            (
                "UPDATE task SET suspension_reason = 'paused_external', "
                "resume_target_turn_id = work_id WHERE status = 'paused' "
                "AND suspension_reason IS NULL"
            ),
            # ── Phase 2 admission_state column (Job-as-Queue-Proxy) ──
            # Adds the ``admission_state`` column to ``job_queue_items``
            # alongside the existing ``status`` column. Dual-write in
            # Phase 2; ``status`` becomes a write-only mirror, then
            # both columns are dropped/replaced in Phase 5. SQLite
            # gets the column + backfill + index via
            # ``daemon/migrations/versions/20260628_000001_job_admission_state.sql``
            # (the .sql runner is a NO-OP on PG, so we mirror the
            # statements here for parity with fresh databases where
            # ``SQLModel.metadata.create_all()`` creates the column
            # from the model field). Fresh PG databases also pick up
            # the column from the model automatically.
            #
            # The backfill is idempotent via ``AND admission_state =
            # 'queued'`` guards — only rows still at the default get
            # updated, so re-running does not clobber rows already
            # written by the dual-write code path.
            "ALTER TABLE job_queue_items ADD COLUMN IF NOT EXISTS admission_state TEXT NOT NULL DEFAULT 'queued'",
            # ``failed_at`` is retained as the live retry marker (Phase 5
            # deviation; JobRetryEngine reads ``job.failed_at is None``).
            # The Phase 5 drop helper intentionally does NOT drop it, but
            # live PG databases created/migrated before the model re-added
            # it would otherwise be missing the column and crash any SELECT
            # that projects ``JobItem.failed_at``. ADD COLUMN IF NOT EXISTS
            # is a no-op on fresh databases where ``create_all`` already
            # created the column from the model.
            "ALTER TABLE job_queue_items ADD COLUMN IF NOT EXISTS failed_at TEXT",
            # Phase 7c: terminal_reason discriminator. Records HOW the
            # job terminated when ``admission_state='done'`` (one of
            # ``"completed"`` / ``"failed"`` / ``"cancelled"`` /
            # ``"aborted"``); NULL for non-terminal rows. The Phase 5
            # column drop collapsed the 7-state legacy ``status`` onto
            # a 4-value ``admission_state``, which made cancelled /
            # failed / completed indistinguishable from the queue
            # side. ``terminal_reason`` restores the discrimination
            # for the resolver read path (``work_resolver._job_to_record``).
            # Nullable, no default — pre-7c rows backfill as NULL and the
            # resolver falls back to the ``admission_state`` map for
            # backward compatibility. ADD COLUMN IF NOT EXISTS is a
            # no-op on fresh databases where ``create_all`` already
            # created the column from the model.
            "ALTER TABLE job_queue_items ADD COLUMN IF NOT EXISTS terminal_reason TEXT",
            # F1: agent_tag column for retry-time version recovery (Agent Versioning Phase 3).
            # Stores the agent version tag that was in effect when the job was
            # originally enqueued, so retry_job() can re-enqueue into the
            # versioned agent_dir instead of silently downgrading to base.
            # Matches ``daemon/repositories/job_queue/models.py::JobItem.agent_tag``
            # (default=None, nullable=True). No index — agent_tag is read-after-write
            # only, not used in predicates.
            "ALTER TABLE job_queue_items ADD COLUMN IF NOT EXISTS agent_tag VARCHAR",
            # NOTE: the four backfill UPDATE statements that reference
            # the legacy ``status`` column were moved out of the main
            # ``statements`` list below — on PostgreSQL databases where
            # Phase 5 has already dropped ``status`` the raw UPDATEs raise
            # ``sqlalchemy.exc.ProgrammingError`` (``UndefinedColumn``).
            # They are re-run in a guarded block after the main transaction
            # so legacy schemas continue to backfill and post-Phase-5
            # schemas no-op silently. See ``legacy_status_backfill`` below.
            "CREATE INDEX IF NOT EXISTS idx_job_queue_admission_state ON job_queue_items(admission_state)",
            # Phase 7c: sparse index on ``terminal_reason`` for the
            # work-resolver's ``WHERE terminal_reason = ...`` predicates
            # used to disambiguate ``admission_state='done'`` rows by cause.
            # IF NOT EXISTS makes this idempotent on re-run and on fresh
            # databases where ``create_all`` already created the index.
            "CREATE INDEX IF NOT EXISTS idx_job_queue_terminal_reason ON job_queue_items(terminal_reason)",
            # ── Phase 2 invariant triggers (plan §8.7.1) ─────────────
            # First CONSTRAINT TRIGGER / DEFERRABLE usage in the
            # codebase (no precedent). Enforces the
            # ``admission_state='active' ⇔ JobLock row exists``
            # invariant at COMMIT, independent of which application
            # code path ran — so it catches a missing-release path the
            # application helper cannot. Deferred (not immediate) so
            # the ``start_job`` acquire-then-set-active ordering inside
            # one transaction does not false-fire.
            #
            # The trigger FUNCTIONS use CREATE OR REPLACE FUNCTION for
            # idempotency. The CONSTRAINT TRIGGERS need DROP + CREATE
            # because CREATE CONSTRAINT TRIGGER has no OR REPLACE
            # form. The SQL is verbatim from plan §8.7.1.
            "CREATE OR REPLACE FUNCTION job_queue_items_active_lock_guard() RETURNS TRIGGER AS $$ BEGIN IF NEW.admission_state = 'active' AND NEW.job_type != 'message' THEN IF NOT EXISTS (SELECT 1 FROM job_locks WHERE instance_id = NEW.instance_id) THEN RAISE EXCEPTION 'admission_state=active requires a job_locks row (instance_id=%)', NEW.instance_id USING ERRCODE = 'integrity_constraint_violation'; END IF; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql",
            "CREATE OR REPLACE FUNCTION job_locks_active_guard() RETURNS TRIGGER AS $$ BEGIN IF NOT EXISTS (SELECT 1 FROM job_queue_items WHERE instance_id = NEW.instance_id AND admission_state = 'active' AND deleted_at IS NULL) THEN RAISE EXCEPTION 'job_locks row requires admission_state=active (instance_id=%)', NEW.instance_id USING ERRCODE = 'integrity_constraint_violation'; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql",
            "DROP TRIGGER IF EXISTS trg_job_queue_items_active_lock_guard ON job_queue_items",
            "DROP TRIGGER IF EXISTS trg_job_locks_active_guard ON job_locks",
            "CREATE CONSTRAINT TRIGGER trg_job_queue_items_active_lock_guard AFTER INSERT OR UPDATE OF admission_state ON job_queue_items DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION job_queue_items_active_lock_guard()",
            "CREATE CONSTRAINT TRIGGER trg_job_locks_active_guard AFTER INSERT OR UPDATE ON job_locks DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION job_locks_active_guard()",
            # ── Skill Evolution System tables (Phase 1, 2026-07-10) ─────
            # 6 new tables backing the SkillSearchService / SkillInjectionService
            # / SkillLifecycleService architecture. SQLite counterpart lives in
            # ``daemon/migrations/versions/20260710_000001_create_skill_tables.sql``;
            # the .sql runner is a NO-OP on PG (runner.py lines 446-448), so we
            # create the tables here for parity with fresh databases where
            # ``SQLModel.metadata.create_all()`` picks them up from
            # ``daemon/repositories/skill/models.py``. Fresh PG databases also
            # get the indexes automatically from the model ``__table_args__``.
            #
            # Type differences from SQLite: BOOLEAN (vs INTEGER 0/1),
            # TIMESTAMPTZ DEFAULT NOW() (vs TEXT ISO-8601), JSONB for JSON
            # columns (vs SQLite JSON). Embeddings are stored as JSONB arrays
            # of floats — NOT BYTEA / pickle / numpy — so they can be indexed
            # and queried with pgvector-style operators if needed later.
            #
            # PRIMARY KEY columns are TEXT (NOT UUID type) per the dual-driver
            # convention; models generate UUID4 strings via uuid.uuid4().
            # skills table
            (
                "CREATE TABLE IF NOT EXISTS skills ("
                "id TEXT PRIMARY KEY, "
                "project_id TEXT, "
                "name TEXT NOT NULL, "
                "description TEXT NOT NULL DEFAULT '', "
                "content TEXT NOT NULL, "
                "category TEXT NOT NULL DEFAULT 'workflow', "
                "is_active BOOLEAN NOT NULL DEFAULT TRUE, "
                "status TEXT NOT NULL DEFAULT 'active', "
                "lineage_origin TEXT NOT NULL DEFAULT 'imported', "
                "generation INTEGER NOT NULL DEFAULT 0, "
                "ab_test_group TEXT, "
                "total_selections INTEGER NOT NULL DEFAULT 0, "
                "total_applied INTEGER NOT NULL DEFAULT 0, "
                "total_completions INTEGER NOT NULL DEFAULT 0, "
                "total_fallbacks INTEGER NOT NULL DEFAULT 0, "
                "consecutive_failures INTEGER NOT NULL DEFAULT 0, "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL, "
                "last_used_at TEXT"
                ")"
            ),
            "CREATE INDEX IF NOT EXISTS idx_skills_project ON skills(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_skills_active ON skills(is_active)",
            "CREATE INDEX IF NOT EXISTS idx_skills_ab_group ON skills(ab_test_group)",
            # skill_lineage table
            (
                "CREATE TABLE IF NOT EXISTS skill_lineage ("
                "skill_id TEXT NOT NULL, "
                "parent_skill_id TEXT NOT NULL, "
                "change_summary TEXT NOT NULL DEFAULT '', "
                "content_diff TEXT NOT NULL DEFAULT '', "
                "created_at TEXT NOT NULL, "
                "PRIMARY KEY (skill_id, parent_skill_id)"
                ")"
            ),
            # skill_usage_records table
            (
                "CREATE TABLE IF NOT EXISTS skill_usage_records ("
                "id TEXT PRIMARY KEY, "
                "skill_id TEXT NOT NULL, "
                "project_id TEXT NOT NULL, "
                "instance_id TEXT NOT NULL, "
                "agent_id TEXT NOT NULL, "
                "task_message TEXT, "
                "selected BOOLEAN NOT NULL DEFAULT FALSE, "
                "applied BOOLEAN NOT NULL DEFAULT FALSE, "
                "task_succeeded BOOLEAN NOT NULL DEFAULT FALSE, "
                "iterations INTEGER NOT NULL DEFAULT 0, "
                "duration_seconds INTEGER NOT NULL DEFAULT 0, "
                "fallback BOOLEAN NOT NULL DEFAULT FALSE, "
                "feedback_applied BOOLEAN, "
                "feedback_note TEXT, "
                "created_at TEXT NOT NULL"
                ")"
            ),
            "CREATE INDEX IF NOT EXISTS idx_skill_usage_skill ON skill_usage_records(skill_id)",
            "CREATE INDEX IF NOT EXISTS idx_skill_usage_instance ON skill_usage_records(instance_id)",
            "CREATE INDEX IF NOT EXISTS idx_skill_usage_applied ON skill_usage_records(instance_id, feedback_applied)",
            # skill_triggers table
            (
                "CREATE TABLE IF NOT EXISTS skill_triggers ("
                "id TEXT PRIMARY KEY, "
                "project_id TEXT, "
                "name TEXT NOT NULL, "
                "condition_type TEXT NOT NULL, "
                "condition_json JSONB NOT NULL DEFAULT '{}', "
                "action TEXT NOT NULL, "
                "is_enabled BOOLEAN NOT NULL DEFAULT TRUE, "
                "created_at TEXT NOT NULL"
                ")"
            ),
            # skill_embeddings table
            (
                "CREATE TABLE IF NOT EXISTS skill_embeddings ("
                "id TEXT PRIMARY KEY, "
                "skill_id TEXT NOT NULL, "
                "trigger_query TEXT NOT NULL, "
                "embedding JSONB NOT NULL, "
                "created_at TEXT NOT NULL"
                ")"
            ),
            "CREATE INDEX IF NOT EXISTS idx_skill_embeddings_skill ON skill_embeddings(skill_id)",
            # skill_ab_tests table
            (
                "CREATE TABLE IF NOT EXISTS skill_ab_tests ("
                "id TEXT PRIMARY KEY, "
                "ab_test_group TEXT NOT NULL, "
                "skill_id_old TEXT NOT NULL, "
                "skill_id_new TEXT NOT NULL, "
                "extension_count INTEGER NOT NULL DEFAULT 0, "
                "comparisons INTEGER NOT NULL DEFAULT 0, "
                "created_at TEXT NOT NULL, "
                "resolved_at TEXT, "
                "winner_skill_id TEXT"
                ")"
            ),
            "CREATE INDEX IF NOT EXISTS idx_skill_ab_tests_group ON skill_ab_tests(ab_test_group)",
            # ── Skill Bank table (isolated user CRUD, not skill evolution) ────
            (
                "CREATE TABLE IF NOT EXISTS skill_bank ("
                "id TEXT PRIMARY KEY, "
                "project_id TEXT, "
                "name TEXT NOT NULL, "
                "description TEXT NOT NULL DEFAULT '', "
                "content TEXT NOT NULL, "
                "category TEXT NOT NULL DEFAULT 'workflow', "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL"
                ")"
            ),
            "CREATE INDEX IF NOT EXISTS ix_skill_bank_project_id ON skill_bank(project_id)",
            # ── Skill Bank template versioning + agent_id + auto_load (2026-07-14) ──
            # Phase 2 of tester-skill-evolution. The skill_bank table gains
            # three columns backing template versioning (for stale-bank refresh
            # detection), per-agent template scoping, and the auto_load flag
            # that propagates from the skill-set.yaml (legacy .md) source of truth into
            # cloned skills. SQLite counterpart lives in
            # ``daemon/migrations/versions/20260714_000003_skill_bank_new_columns.sql``;
            # the .sql runner is a NO-OP on PG (runner.py lines 446-448), so
            # we mirror the statements here for parity with fresh databases
            # where ``SQLModel.metadata.create_all()`` creates the columns
            # from the SkillBankItem model field declarations.
            #
            # Type differences from SQLite: TEXT NOT NULL DEFAULT '1.0.0'
            # matches SQLite exactly (TEXT in both). BOOLEAN NOT NULL DEFAULT
            # false vs INTEGER NOT NULL DEFAULT 0 — both read back as ``False``
            # from the ORM regardless of which backend created the column.
            "ALTER TABLE skill_bank ADD COLUMN IF NOT EXISTS template_version TEXT NOT NULL DEFAULT '1.0.0'",
            "ALTER TABLE skill_bank ADD COLUMN IF NOT EXISTS agent_id TEXT",
            "ALTER TABLE skill_bank ADD COLUMN IF NOT EXISTS auto_load BOOLEAN NOT NULL DEFAULT false",
            "CREATE INDEX IF NOT EXISTS ix_skill_bank_agent_id ON skill_bank(agent_id)",
            # ── Skills auto_load + source_skill_bank_id (2026-07-14) ─────
            # Phase 2 of tester-skill-evolution. The skills (evolution)
            # table gains two columns: ``auto_load`` is the clone-side
            # counterpart of the skill_bank auto_load flag (controls whether
            # the skill is loaded into the system prompt before every
            # task vs on-demand), and ``source_skill_bank_id`` is a soft FK
            # back to the skill_bank template this row was cloned from
            # (NULL for manually-created or evolved skills — soft FK only,
            # never enforced at the DB level). SQLite counterpart lives in
            # ``daemon/migrations/versions/20260714_000004_skills_new_columns.sql``.
            "ALTER TABLE skills ADD COLUMN IF NOT EXISTS auto_load BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE skills ADD COLUMN IF NOT EXISTS source_skill_bank_id TEXT",
            "CREATE INDEX IF NOT EXISTS ix_skills_auto_load ON skills(auto_load)",
            # ── Skill usage records: ab_test_group + superseded (2026-07-15) ──
            # Phase: Skill-worker milestone prerequisite. Two columns on the
            # ``skill_usage_records`` table backing per-worker A/B period
            # isolation (``ab_test_group``, nullable TEXT — NULL means "not
            # under test") and worker-reuse audit (``superseded``, BOOLEAN
            # NOT NULL DEFAULT false — set to true when the row was
            # superseded by a fresh skill binding for the same worker).
            # Two indexes support the per-group aggregation and the
            # time-bucketed completion-rate rollup queries. SQLite counterpart
            # lives in
            # ``daemon/migrations/versions/20260715_000001_skill_usage_new_columns.sql``.
            # Fresh PG databases pick up the columns + indexes from
            # ``SQLModel.metadata.create_all()`` via the SkillUsageRecord
            # model ``__table_args__`` declarations; existing PG databases
            # rely on these statements because the .sql migration runner is
            # a NO-OP on PostgreSQL (runner.py lines 446-448).
            "ALTER TABLE skill_usage_records ADD COLUMN IF NOT EXISTS ab_test_group TEXT",
            "ALTER TABLE skill_usage_records ADD COLUMN IF NOT EXISTS superseded BOOLEAN NOT NULL DEFAULT false",
            "CREATE INDEX IF NOT EXISTS ix_skill_usage_records_ab_group ON skill_usage_records(ab_test_group)",
            "CREATE INDEX IF NOT EXISTS ix_skill_usage_records_skill_created ON skill_usage_records(skill_id, created_at)",
            # ── Skill usage records: feedback_usefulness + feedback_improvement (2026-07-21) ──
            # Phase: skill_feedback usefulness + improvement scoring. Two new
            # columns on the ``skill_usage_records`` table backing the
            # upgraded ``skill_feedback`` tool: ``feedback_usefulness`` is an
            # INTEGER holding the agent-judged quality score 1-10 (NULL =
            # not recorded — preserved by the absence of a DEFAULT so
            # existing rows don't get a corrupt "rated 0" signal), and
            # ``feedback_improvement`` is a TEXT column for actionable
            # suggestions about the skill content itself (distinct from
            # ``feedback_note`` which is the general context observation).
            # Together they feed the skill-keeper evolution loop and the
            # per-skill usefulness rollup. SQLite counterpart lives in
            # ``daemon/migrations/versions/20260721_000001_skill_usage_feedback_columns.sql``.
            # Fresh PG databases pick up the columns from
            # ``SQLModel.metadata.create_all()`` via the SkillUsageRecord
            # model field declarations; existing PG databases rely on
            # these statements because the .sql migration runner is a
            # NO-OP on PostgreSQL (runner.py lines 446-448).
            "ALTER TABLE skill_usage_records ADD COLUMN IF NOT EXISTS feedback_usefulness INTEGER",
            "ALTER TABLE skill_usage_records ADD COLUMN IF NOT EXISTS feedback_improvement TEXT",
            # ── Widen job_queues.queue_type CHECK constraint (2026-07-14) ──
            # The job_queues.queue_type column must accept 'defer' and
            # 'background' values in addition to 'fifo' and 'parallel' so
            # system_defer_queue and system_background_queue provisioning
            # can succeed (both queue types were added in Phase 3 of the
            # job-as-queue-proxy refactor but the original 2026-04-09
            # migration only declared 'fifo' and 'parallel'). The JobQueue
            # SQLModel already declares the wider constraint
            # (``CheckConstraint("queue_type IN ('fifo', 'parallel',
            # 'defer', 'background')", name="ck_job_queues_queue_type")``)
            # so fresh PG databases created by
            # ``SQLModel.metadata.create_all()`` get the wider constraint
            # automatically; existing PG databases need an explicit drop +
            # re-add here because the .sql migration runner is a NO-OP on
            # PostgreSQL (runner.py lines 446-448).
            #
            # The SQLite path lives in
            # ``daemon/migrations/versions/20260714_000001_widen_job_queue_type_constraint.sql``.
            # Both statements are idempotent via DROP CONSTRAINT IF EXISTS
            # (no-op once the wider constraint is already in place) and
            # the constraint name matches the model's CheckConstraint
            # name so subsequent re-runs converge on a single state.
            "ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS ck_job_queues_queue_type",
            "ALTER TABLE job_queues ADD CONSTRAINT ck_job_queues_queue_type CHECK (queue_type IN ('fifo', 'parallel', 'defer', 'background'))",
            # ── Idle-gate deadlock task-flag backfill (2026-08-10) ─────
            # SQLite counterpart: 20260810_000001_fix_idle_gate_stuck_task_flags.sql.
            # Backfills ``task.is_deferred`` and ``task.is_background``
            # for tasks whose linked JobItem sits on a defer / background
            # queue but whose task flag was never stamped (the pre-fix
            # bug: enqueue_message_job forwarded the caller's flags
            # verbatim, so a defer/background queue's task often carried
            # ``is_deferred=False`` / ``is_background=False``). The
            # Task-side and Job-side idle-gate predicates then counted
            # the offending task as non-deferred / non-background work
            # and the queue's JobItems never got activated — permanent
            # deadlock. The two UPDATE statements are kept byte-identical
            # to the SQLite migration so the two paths converge on the
            # same final state. Both are guarded by the ``is_deferred =
            # FALSE`` / ``is_background = FALSE`` predicates so they are
            # idempotent on re-run (already-correct rows are skipped).
            (
                "UPDATE task SET is_deferred = TRUE "
                "WHERE task.is_deferred = FALSE "
                "AND EXISTS ("
                "SELECT 1 FROM job_queue_items ji "
                "JOIN job_queues q ON ji.queue_id = q.queue_id "
                "WHERE ji.job_id = task.work_id "
                "AND q.queue_type = 'defer' "
                "AND ji.deleted_at IS NULL"
                ")"
            ),
            (
                "UPDATE task SET is_background = TRUE "
                "WHERE task.is_background = FALSE "
                "AND EXISTS ("
                "SELECT 1 FROM job_queue_items ji "
                "JOIN job_queues q ON ji.queue_id = q.queue_id "
                "WHERE ji.job_id = task.work_id "
                "AND q.queue_type = 'background' "
                "AND ji.deleted_at IS NULL"
                ")"
            ),
            # ── Reconcile stuck tasks with terminal JobItems (2026-08-11) ──
            # SQLite counterpart: 20260811_000001_reconcile_stuck_tasks_with_terminal_jobitems.sql.
            # Cancels Task rows stuck in 'paused'/'pending' when their linked
            # JobItem (task.work_id = job_queue_items.job_id) has already
            # transitioned to a terminal admission_state ('done'/'dead'). The
            # Task is never finalized by the JobItem terminal transition, so
            # it blocks the defer/background idle-gate indefinitely. The UPDATE
            # uses the portable ``WHERE EXISTS`` subquery form (ANSI, works on
            # both drivers) and is idempotent: the ``status IN ('paused',
            # 'pending')`` guard means a second run (or a re-run after the
            # Phase 1 reconciliation code lands) matches 0 rows. The SET
            # clause mirrors the runtime reconciliation
            # (``reconcile_terminal_task`` /
            # ``batch_reconcile_bad_state_tasks``) which cancels with
            # ``cancel_requested=1`` and stamps
            # ``cancel_requested_at``/``completed_at``. The statement is
            # byte-identical to the SQLite .sql migration so both paths
            # converge on the same final state.
            (
                "UPDATE task "
                "SET status = 'cancelled', "
                "    cancel_requested = TRUE, "
                "    cancel_requested_at = CURRENT_TIMESTAMP, "
                "    completed_at = CURRENT_TIMESTAMP "
                "WHERE status IN ('paused', 'pending') "
                "AND EXISTS ("
                "    SELECT 1 FROM job_queue_items ji "
                "    WHERE ji.job_id = task.work_id "
                "      AND ji.admission_state IN ('done', 'dead') "
                "      AND ji.deleted_at IS NULL"
                ");"
            ),
        ]
        with self._engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))

        # Backfill UPDATEs that reference the legacy ``status`` column.
        # Phase 5 of the Job-as-Queue-Proxy refactor dropped
        # ``job_queue_items.status``; on databases that have already
        # applied that drop, running ``UPDATE … WHERE status = …``
        # raises ``sqlalchemy.exc.ProgrammingError`` (``UndefinedColumn``).
        # We catch that specific failure and log at DEBUG so legacy
        # databases continue to backfill while post-Phase-5 databases
        # silently skip the now-meaningless statements.
        #
        # Each statement gets its OWN transaction (a fresh
        # ``with self._engine.begin() as conn:`` per loop iteration)
        # because a failed UPDATE in PostgreSQL leaves the surrounding
        # transaction in ``InFailedSqlTransaction`` state — subsequent
        # statements in the same ``.begin()`` block would then raise
        # ``InternalError`` regardless of which SQL they run. Per-statement
        # transactions give each UPDATE its own commit-or-rollback scope.
        import sqlalchemy.exc

        legacy_status_backfill = [
            "UPDATE job_queue_items SET admission_state = 'queued' WHERE status = 'pending' AND admission_state = 'queued'",
            "UPDATE job_queue_items SET admission_state = 'active' WHERE status IN ('processing', 'paused') AND admission_state = 'queued'",
            # Phase 7c: populate ``terminal_reason`` alongside
            # ``admission_state='done'`` so legacy rows that survive
            # the column drop carry the discriminator the Phase 7c
            # ``JobResponse`` resolver reads. The status→reason
            # mapping mirrors ``_derive_terminal_reason``.
            "UPDATE job_queue_items SET admission_state = 'done', terminal_reason = status WHERE status = 'completed' AND admission_state = 'queued'",
            "UPDATE job_queue_items SET admission_state = 'done', terminal_reason = status WHERE status = 'failed' AND admission_state = 'queued'",
            "UPDATE job_queue_items SET admission_state = 'done', terminal_reason = status WHERE status = 'cancelled' AND admission_state = 'queued'",
            "UPDATE job_queue_items SET admission_state = 'dead' WHERE status = 'dead_letter' AND admission_state = 'queued'",
        ]
        for stmt in legacy_status_backfill:
            try:
                with self._engine.begin() as conn:
                    conn.execute(text(stmt))
            except sqlalchemy.exc.ProgrammingError as status_err:
                err_msg = str(status_err).lower()
                if "does not exist" in err_msg or "undefinedcolumn" in err_msg:
                    logger.debug(
                        "Legacy `status` column already dropped; "
                        "skipping backfill statement: %s",
                        stmt[:80],
                    )
                else:
                    raise
            except sqlalchemy.exc.InternalError as status_err:
                # ``InFailedSqlTransaction`` from psycopg arrives as
                # ``InternalError`` (not ``ProgrammingError``) when a
                # prior statement in this batch aborted the transaction.
                # Treat the same as ``UndefinedColumn`` — the legacy
                # ``status`` column is gone, so none of these UPDATEs can
                # run on post-Phase-5 databases. Logged once at DEBUG.
                err_msg = str(status_err).lower()
                if (
                    "does not exist" in err_msg
                    or "undefinedcolumn" in err_msg
                    or "infailedsqltransaction" in err_msg
                    or "current transaction is aborted" in err_msg
                ):
                    logger.debug(
                        "Legacy `status` column already dropped; "
                        "skipping backfill statement: %s",
                        stmt[:80],
                    )
                else:
                    raise

        # ── Phase 7c (continued) / Bug F3: backfill NULL terminal_reason ──
        # Bug F3 fix (Phase 2 of defer-seam bugfix, 2026-06-30): the
        # ``list_work(status="completed")`` / ``"failed"`` / ``"cancelled"``
        # filters now consult ``terminal_reason`` on the
        # ``admission_state='done'`` path. Rows that pre-date the F3
        # fix may still have ``terminal_reason IS NULL`` even though
        # they are in a terminal state — those rows would silently
        # vanish from the F3 status filter (only ``completed`` would
        # see them via the ``OR terminal_reason IS NULL`` clause; the
        # ``failed`` and ``cancelled`` filters would drop them
        # entirely).
        #
        # Backfill rule (per F3 spec):
        #   * If ``error_message IS NOT NULL AND error_message != ''``:
        #     set ``terminal_reason = 'failed'`` (the row had a
        #     non-empty legacy error message, so it almost certainly
        #     terminated via the FAILED path).
        #   * Otherwise: set ``terminal_reason = 'completed'`` (the
        #     default for NULL ``terminal_reason`` rows per the legacy
        #     ``done → completed`` map in
        #     ``_ADMISSION_TO_LEGACY_STATUS``).
        #
        # Both statements are gated on ``admission_state='done' AND
        # terminal_reason IS NULL`` so re-runs are idempotent — once a
        # row has its ``terminal_reason`` populated, subsequent runs
        # skip it.
        #
        # On Phase 5+ databases ``error_message`` was DROPPED — the
        # first UPDATE would raise ``UndefinedColumn``. The same
        # try/except pattern used for the ``legacy_status_backfill``
        # above catches that and silently skips the statement so
        # Phase 5+ databases still backfill NULLs to ``completed``
        # (the safe default — better to surface under ``completed``
        # than to vanish entirely). On pre-Phase-5 databases the
        # error-message-aware UPDATE runs and stamps ``failed`` where
        # the legacy error_message was non-empty.
        terminal_reason_backfill = [
            # Pre-Phase-5 legacy backfill: rows with a non-empty
            # legacy ``error_message`` are stamped as 'failed'. The
            # ``error_message`` column is dropped by Phase 5; the
            # try/except below silently skips this UPDATE on Phase 5+
            # databases so the second UPDATE (default to 'completed')
            # is the sole backfill.
            "UPDATE job_queue_items SET terminal_reason = 'failed' "
            "WHERE admission_state = 'done' AND terminal_reason IS NULL "
            "AND error_message IS NOT NULL AND error_message != ''",
            # Phase 5+ safe-default backfill: any remaining NULLs
            # (rows that survived the Phase 5 drop without ever having
            # ``terminal_reason`` populated) are stamped as
            # 'completed'. This matches the lossy legacy ``done →
            # completed`` mapping the resolver uses for rows that
            # don't have ``terminal_reason`` set.
            "UPDATE job_queue_items SET terminal_reason = 'completed' "
            "WHERE admission_state = 'done' AND terminal_reason IS NULL",
        ]
        for stmt in terminal_reason_backfill:
            try:
                with self._engine.begin() as conn:
                    conn.execute(text(stmt))
            except sqlalchemy.exc.ProgrammingError as tr_err:
                # Only swallow UndefinedColumn errors raised against the
                # ``error_message`` column specifically — the substring
                # ``"does not exist"`` is too broad (it also matches
                # "relation does not exist", "type does not exist",
                # etc.) and would silently mask unrelated schema drift.
                # ``error_message`` was dropped by Phase 5, so this
                # branch fires on Phase 5+ databases; pre-Phase-5
                # schemas succeed without entering the except block.
                #
                # NOTE: ``InFailedSqlTransaction`` is unreachable here
                # because each statement runs in its own connection /
                # transaction (``with self._engine.begin() as conn``
                # opens a fresh transaction per loop iteration) — a
                # failure in one iteration cannot poison the next.
                err_msg = str(tr_err).lower()
                if "column" in err_msg and (
                    "does not exist" in err_msg or "undefinedcolumn" in err_msg
                ):
                    logger.debug(
                        "Legacy `error_message` column already dropped "
                        "(Phase 5+); skipping terminal_reason backfill "
                        "statement: %s",
                        stmt[:80],
                    )
                else:
                    raise

    def _ensure_postgres_drop_legacy_columns(self) -> None:
        """Drop the legacy completion-state columns on PostgreSQL.

        The SQLite migration ``20260621_000002_drop_legacy_completion_columns.sql``
        drops the same columns. On PostgreSQL the migration runner is
        a NO-OP (runner.py lines 446-448), so the equivalent
        ``ALTER TABLE ... DROP COLUMN IF EXISTS`` statements run here
        at startup. ``IF EXISTS`` keeps the call idempotent.

        Does NOT touch ``instance_hierarchy`` — that table is still live.
        """
        from sqlalchemy import text

        statements = [
            # Phase 4: legacy counter column dropped.
            "ALTER TABLE instances DROP COLUMN IF EXISTS waiting_for",
            # Phase 4: the legacy denormalized ``children`` JSON cache
            # column was dropped. ``instance_hierarchy`` is the canonical
            # source of child IDs.
            "ALTER TABLE instances DROP COLUMN IF EXISTS children",
        ]
        with self._engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))
        logger.debug(
            "Phase 4 column drop: ``waiting_for`` and ``children`` removed (or absent) on PostgreSQL"
        )

    def _ensure_postgres_drop_admission_legacy(self) -> None:
        """Drop the six legacy ``job_queue_items`` columns on PostgreSQL.

        Phase 5 of the Job-as-Queue-Proxy migration. After Phase 4
        cleanup (commit 4eb1758a), ``admission_state`` is the sole
        authority and these columns are dead artifacts:

          * ``status``          (frozen at INSERT default 'pending')
          * ``started_at``      * ``completed_at``
          * ``result_summary``  * ``error_message``
          * ``cancelled_at``

        ``failed_at`` is retained — the retry engine reads it as the
        live retry marker (Phase 5 deviation; drop deferred to a
        future batch).

        Three legacy indexes referencing ``status`` are dropped first
        (the index depends on the column, so order matters). The
        replacement index ``idx_job_queue_admission_state`` is NOT
        touched.

        The SQLite migration
        ``20260628_000002_drop_job_queue_legacy_columns.sql`` drops the
        same objects. On PostgreSQL the migration runner is a NO-OP, so
        the equivalent ``DROP`` statements run here at startup.

        **Idempotency**: every statement uses ``IF EXISTS`` and each is
        wrapped in its own ``try/except`` so that a column or index
        that is already gone (e.g. second startup, or fresh database
        where ``create_all`` never created it) is a silent no-op.

        **Activation gate**: This method MUST NOT be called until the
        JobItem SQLModel (daemon/repositories/job_queue/models.py) no
        longer maps these six columns and all production reads have
        been converted to ``admission_state``. See the call-site
        comment in :meth:`_run_startup_migrations`.
        """
        from sqlalchemy import text

        # Drop indexes first — an index on a column must be dropped
        # before the column itself.
        index_statements = [
            "DROP INDEX IF EXISTS idx_job_queue_status",
            "DROP INDEX IF EXISTS idx_job_queue_items_status_type_instance",
            "DROP INDEX IF EXISTS idx_job_queue_items_project_status_deleted",
        ]
        # Then the six legacy columns (``failed_at`` retained).
        column_statements = [
            "ALTER TABLE job_queue_items DROP COLUMN IF EXISTS status",
            "ALTER TABLE job_queue_items DROP COLUMN IF EXISTS started_at",
            "ALTER TABLE job_queue_items DROP COLUMN IF EXISTS completed_at",
            "ALTER TABLE job_queue_items DROP COLUMN IF EXISTS result_summary",
            "ALTER TABLE job_queue_items DROP COLUMN IF EXISTS error_message",
            "ALTER TABLE job_queue_items DROP COLUMN IF EXISTS cancelled_at",
        ]
        with self._engine.begin() as conn:
            for stmt in index_statements:
                try:
                    conn.execute(text(stmt))
                except Exception as exc:  # noqa: BLE001 — idempotent
                    logger.debug("Phase 5 index drop skipped (%s): %s", stmt, exc)
            for stmt in column_statements:
                try:
                    conn.execute(text(stmt))
                except Exception as exc:  # noqa: BLE001 — idempotent
                    logger.debug("Phase 5 column drop skipped (%s): %s", stmt, exc)
        logger.debug(
            "Phase 5 column drop: seven legacy job_queue_items columns "
            "removed (or absent) on PostgreSQL"
        )

    def _ensure_blueprint_g7_unique_index(self) -> None:
        """C6 / G7: enforce one core per project via a partial unique index.

        Phase 2 of the Project Blueprint subsystem. The DB-level
        partial unique index is the PRIMARY enforcement mechanism —
        the app-level UX guard in :meth:`BlueprintRepository.create`
        is convenience only. The index is created via raw DDL
        (NOT a ``.sql`` migration, which the migration runner
        silently skips on PostgreSQL) so it works on both
        SQLite and PostgreSQL.

        Steps
        -----

        1. **Auto-dedup pre-flight** — for every project, scan for
           duplicate active cores and soft-disable every copy except
           the newest (``auto_dedup_cores``). This is required
           BEFORE the unique index can be created on existing
           databases with duplicates.
        2. **Create the partial unique index** — ``WHERE kind =
           'core' AND is_active``. Bare ``is_active`` is portable
           across drivers:

           * SQLite stores ``bool`` as INTEGER 0/1; bare ``is_active``
             is a truthy check (0 is false, 1 is true).
           * PostgreSQL uses a genuine BOOLEAN column; bare
             ``is_active`` is a boolean expression.

           Do NOT use ``is_active = 1`` — PostgreSQL has no
           int→boolean implicit cast and will raise
           ``operator does not exist: boolean = integer``.

           ``IF NOT EXISTS`` makes the call idempotent.

        Failure semantics
        -----------------

        Wrapped in ``try/except Exception`` so that a constraint
        failure (e.g. driver inconsistency, dead rows) does not
        crash startup. The app-level UX guard in
        :meth:`BlueprintRepository.create` still catches the
        double-core case at the API boundary; the DB index is the
        belt-and-braces enforcement.
        """
        try:
            # Step 1: Auto-dedup pre-flight (LEADER DECISION #2).
            # ``_blueprint_repo`` is constructed earlier in __init__.
            if self._blueprint_repo is not None:
                project_repo = getattr(self, "_project_repository", None)
                if project_repo is not None:
                    try:
                        projects = project_repo.list_projects(limit=10000)
                    except Exception as exc:  # noqa: BLE001 — best-effort
                        logger.debug(
                            "G7 auto-dedup: project list unavailable, "
                            "skipping pre-flight: %s", exc,
                        )
                        projects = []
                    for project in projects:
                        try:
                            self._blueprint_repo.auto_dedup_cores(
                                project.project_id
                            )
                        except Exception as exc:  # noqa: BLE001 — per-project scope
                            logger.warning(
                                "G7 auto-dedup failed for project %s: %s",
                                project.project_id, exc,
                            )

            # Step 2: Create the partial unique index.
            # SQLite stores booleans as INTEGER (0/1); bare ``is_active``
            # is truthy. PostgreSQL uses genuine BOOLEAN; bare
            # ``is_active`` is a boolean expression. Both drivers
            # evaluate ``WHERE is_active`` correctly. Do NOT use
            # ``= 1`` — PostgreSQL has no int→boolean implicit cast
            # and will raise ``operator does not exist: boolean = integer``.
            ddl = (
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_blueprint_one_core "
                "ON project_blueprints (project_id) "
                "WHERE kind = 'core' AND is_active"
            )
            with self._engine.begin() as conn:
                conn.execute(text(ddl))
            logger.info(
                "G7: created partial unique index ux_blueprint_one_core "
                "(one core per project, active rows)"
            )
        except Exception as exc:  # noqa: BLE001 — never crash startup
            logger.error(
                "G7 partial unique index creation failed: %s. "
                "App-level UX guard in create() is still active.",
                exc, exc_info=True,
            )

    def _backfill_blueprint_active(self) -> None:
        """One-time startup backfill: opt in projects that already have blueprints.

        When the per-project ``blueprint_active`` gate was introduced, existing
        projects with blueprints would have been silently disabled (default =
        absent = false). This backfill runs on every startup and sets
        ``blueprint_active=true`` for any project that has at least one active
        blueprint row but no metadata key yet.

        Idempotent: if the key already exists (true or false), it is NOT
        overwritten — the operator's explicit choice is respected.
        """
        from daemon.constants import BLUEPRINT_ACTIVE_METADATA_KEY

        try:
            bp_repo = getattr(self, "_blueprint_repo", None)
            project_repo = getattr(self, "_project_repository", None)
            if bp_repo is None or project_repo is None:
                return

            projects = project_repo.list_projects(limit=10_000)
            backfilled = 0
            for project in projects:
                pid = getattr(project, "project_id", None) or getattr(project, "id", None)
                if not pid:
                    continue
                # Skip the system default project
                if getattr(project, "name", None) == "__system_default__":
                    continue
                try:
                    # Check if project already has the metadata key
                    existing = project_repo.get_metadata(pid, BLUEPRINT_ACTIVE_METADATA_KEY)
                    if existing is not None:
                        continue  # Key exists — respect the operator's choice
                    # Check if project has any active blueprints
                    blueprints = bp_repo.list_by_project(pid, active_only=True)
                    if blueprints:
                        project_repo.set_metadata(pid, BLUEPRINT_ACTIVE_METADATA_KEY, True)
                        backfilled += 1
                        logger.info(
                            "Blueprint backfill: opted in project %s "
                            "(has %d active blueprint(s))",
                            pid, len(blueprints),
                        )
                except Exception as exc:  # noqa: BLE001 — per-project isolation
                    logger.warning(
                        "Blueprint backfill: failed for project %s: %s",
                        pid, exc,
                    )
            if backfilled > 0:
                logger.info(
                    "Blueprint backfill: opted in %d project(s) with existing blueprints",
                    backfilled,
                )
        except Exception as exc:  # noqa: BLE001 — never crash startup
            logger.warning(
                "Blueprint backfill: startup scan failed: %s", exc, exc_info=True,
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
        # Expose on the manager so cross-dispatcher handlers
        # (``MessageJobHandler._find_running_task_for_instance``)
        # can read the repo without reaching into a private local.
        self._task_repo = task_repo
        # Wire the maintenance service's task repository here (after
        # ``self._task_repo`` is assigned) so the shared idle probe can use
        # ``TaskRepository.has_active_non_deferred_work``. Calling this in
        # ``initialize()`` would crash because ``self._task_repo`` is only
        # assigned later in ``setup_worker_pool()`` per daemon/api.py.
        self._maintenance_service.set_task_repository(self._task_repo)
        event_repo = EventRepository(engine=self._engine)

        # Backfill last_heartbeat_at for any RUNNING tasks that lack one
        # (legacy rows or in-flight tasks surviving a restart). Without
        # this, the recovery service would flag every surviving RUNNING
        # task as stale within stale_task_recovery_threshold_minutes of
        # the new deploy. Best-effort: the recovery predicate falls back
        # to started_at, so a failed backfill is a recoverable degraded
        # state, not a crash.
        try:
            backfilled = task_repo.backfill_heartbeats()
            if backfilled:
                logger.info(
                    f"Backfilled last_heartbeat_at for {backfilled} in-flight tasks"
                )
        except Exception as e:
            logger.warning(f"Startup backfill of last_heartbeat_at failed: {e}")
        
        # Get shorthand for services config
        svc = self.config.services
        
        # Run startup crash recovery with config values
        # NOTE: threshold_minutes is sourced from stale_task_recovery_threshold_minutes
        # (separate from task_timeout_minutes) so that sibling tasks blocked by
        # Fix B's per-instance guard are unblocked within ~5 minutes of a worker
        # crash, not the much longer task_timeout_minutes.
        stale_recovery = StaleTaskRecovery(
            task_repository=task_repo,
            message_repository=self._queue_repository,
            event_repository=event_repo,
            threshold_minutes=svc.stale_task_recovery_threshold_minutes,
            check_interval_seconds=svc.stale_task_recovery_interval,
            cancel_grace_seconds=svc.stale_task_cancel_grace_seconds,
            max_retries=svc.max_task_retries,
            retry_backoff_base=svc.task_retry_backoff_base,
            retry_backoff_max=svc.task_retry_backoff_max,
            on_task_permanently_failed=self._on_stale_task_permanent_failure,
            on_task_cancelled_and_retried=self._on_stale_task_cancelled_and_retried,
            instance_manager=self,
        )
        # FIX: C3 — Assign BEFORE calling recover_on_startup() so _stale_recovery is set
        # even if recover_on_startup() raises an exception
        self._stale_recovery = stale_recovery
        stale_recovery.recover_on_startup()
        # FIX: C2 — Start periodic background recovery thread
        stale_recovery.start()

        # Execution Gate: stale-lease recovery is performed by the
        # async lifespan in ``daemon/api.py`` BEFORE this method runs,
        # so the very first ``gate.run`` after startup is guaranteed
        # to see a clean state. We deliberately do NOT call the sync
        # wrapper here — it would be fire-and-forget under the
        # running event loop and would leave up to a 5-minute window
        # where the first ``gate.run`` could contend against a stale
        # lease from a crashed prior process.
        
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
            heartbeat_interval_seconds=svc.task_heartbeat_interval_seconds,
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
        model: str | None = None,
        version_tag: str | None = None,
        source_type: str | None = None,
    ) -> tuple[str, str | None]:
        """Create a new agent instance.

        Args:
            agent_id: Agent ID (e.g., "developer").
            instance_id: Optional instance ID. Auto-generated if not provided or invalid.
            parent_id: Optional parent instance ID for hierarchical instances.
            project_id: Optional project ID for project context. Use `None` to explicitly
                indicate no project context is needed. If provided, stored in instance
                metadata so child instances don't rely on text extraction.
            instance_name: Optional short name for the instance (e.g., 'create-feature-a').
                Used in completion reports to identify the task.
            invoked_as_tool: If True, marks instance as invoked-as-tool (default: False).
            model: Optional LLM model override for this instance. If provided and
                allowed by config.llm.allowed_models (exact match, case-insensitive),
                takes the HIGHEST priority — above meta.json's llm_model and the
                env OPENAI_MODEL. If the list is non-empty and this model is not
                allowed, the override is silently ignored and the default model
                is used.
            version_tag: Optional agent version tag. ``None`` selects the base agent.
            source_type: Optional chat platform type (e.g. ``"discord"``,
                ``"slack"``, ``"telegram"``). When set, stored in
                ``instance_metadata`` so the platform-context system-prompt
                appender can inject formatting rules into root instances.
                No-op for child instances (parent_id set).

        Returns:
            A ``(instance_id, validated_model_override)`` tuple. ``validated_model_override``
            is the model value that was actually applied as the spawn-time override
            (after silent fallback to ``None`` when the caller-supplied model was
            rejected). The lifecycle service performs the single authoritative
            validation; callers MUST NOT re-run ``_resolve_model_override`` on the
            same input — the two calls could disagree under a mid-flight
            ``allowed_models`` mutation.

        Raises:
            ValueError: If max_children_per_instance limit is exceeded,
                or if agent_id is not found.
        """
        return self._lifecycle_service.spawn_instance(
            agent_id=agent_id,
            instance_id=instance_id,
            parent_id=parent_id,
            project_id=project_id,
            instance_name=instance_name,
            invoked_as_tool=invoked_as_tool,
            model=model,
            version_tag=version_tag,
            source_type=source_type,
        )

    async def ensure_mcp_preloaded(
        self,
        instance_id: str,
        *,
        agent_id: str | None = None,
        version_tag: str | None = None,
    ) -> None:
        """Ensure MCP tools are preloaded for an instance.

        Preloads if the instance is not in memory OR if it's in memory but lacks
        cached MCP tools (e.g., restored by router without preload). Safe if
        _mcp_service doesn't exist yet.

        This method is idempotent — safe to call multiple times for the same instance.

        Args:
            instance_id: The instance to preload MCP tools for.
            agent_id: Optional base agent identifier. When provided,
                the matching ``AgentMetadata`` is resolved at preload
                time so per-agent ``mcp_full_access`` opt-outs are
                honored. ``None`` is allowed: ``ensure_mcp_preloaded``
                runs on the cold-restore path (``instance_lifecycle.get_instance``)
                BEFORE ``spawn_instance_with_mcp`` has fired, so the
                instance row's ``agent_id`` / ``agent_tag`` columns are
                the only identity hint available. The best-effort
                fallback below reads them from the repo and forwards
                to ``preload_mcp_tools``. Any failure leaves
                ``agent_id=None`` → strip stays applied (fail closed).
            version_tag: Optional agent version tag. Forwarded to
                ``preload_mcp_tools``; ``None`` selects base.
        """
        # Skip if instance already loaded with MCP tools cached — no need to preload
        if instance_id in self.instances:
            if self._mcp_service:
                cached = self._mcp_service.get_mcp_tools(instance_id)
                if cached:
                    return  # Has tools — truly no need to preload
            else:
                return  # No MCP service — nothing to preload

        # Skip if MCP service not initialized
        if not hasattr(self, '_mcp_service') or not self._mcp_service:
            return

        # Best-effort identity fallback for the cold-restore path. The
        # caller (typically ``get_instance``) supplies only
        # ``instance_id``, so ``agent_id`` may be None here. Reading
        # the instance row yields the canonical ``agent_id`` /
        # ``agent_tag`` already chosen at spawn time, which is the
        # right input for ``mcp_full_access`` resolution. A repo read
        # failure leaves ``agent_id=None`` → strip stays applied.
        if agent_id is None and getattr(self, "_instance_repository", None):
            try:
                row = self._instance_repository.get(instance_id)
                if row is not None:
                    agent_id = getattr(row, "agent_id", None)
                    if version_tag is None:
                        version_tag = getattr(row, "agent_tag", None)
            except Exception as _e:
                logger.debug(
                    f"ensure_mcp_preloaded: instance repo lookup "
                    f"failed for {instance_id[:8]}: {_e}"
                )

        try:
            await self._mcp_service.preload_mcp_tools(
                instance_id,
                agent_id=agent_id,
                version_tag=version_tag,
            )
        except Exception as e:
            logger.warning(f"MCP preload failed for {instance_id[:8]}: {e}")

    async def spawn_instance_with_mcp(
        self,
        *,
        instance_id: str,
        version_tag: str | None = None,
        agent_id: str | None = None,
        **kwargs,
    ) -> str:
        """Async spawn with MCP preload and cleanup on failure.

        1. Preloads MCP tools
        2. Calls sync spawn_instance()
        3. On spawn failure, cleans up MCP connections

        Args:
            instance_id: The pre-generated instance ID.
            version_tag: Optional agent version tag. ``None`` selects the base agent.
            agent_id: Optional base agent identifier. When provided it
                is forwarded to ``ensure_mcp_preloaded`` so the
                ``mcp_full_access`` opt-out is honored BEFORE the
                instance row exists in the DB. Keyword-only binding
                means ``agent_id`` always lands on this param — it
                never actually arrives inside ``**kwargs``; the
                in-code kwargs fallback is a defensive no-op.
            **kwargs: Passed to spawn_instance(). Existing call sites
                pass ``agent_id`` here as a kwarg — both forms are
                honored.

        Returns:
            The instance_id.

        Raises:
            Whatever spawn_instance() raises.
        """
        # ``agent_id`` may arrive via the explicit param OR via
        # ``**kwargs`` (legacy callers like job_processor.py pass it
        # as a kwarg to ``spawn_instance``). The explicit param wins;
        # fall back to kwargs for backward compatibility. Once bound
        # to the explicit param, Python does NOT carry ``agent_id``
        # into ``**kwargs``, so we re-inject it into the
        # ``spawn_instance`` call below to preserve the legacy
        # behavior every existing caller depends on.
        effective_agent_id = agent_id if agent_id is not None else kwargs.get("agent_id")
        await self.ensure_mcp_preloaded(
            instance_id,
            agent_id=effective_agent_id,
            version_tag=version_tag,
        )

        try:
            # Unpack the (instance_id, validated_model_override) tuple —
            # we only propagate the instance_id here. The validated override
            # is consumed by callers that need a fallback notice (the
            # ``spawn_instance`` tool layer); the wrapper contract remains
            # ``-> str`` for downstream HTTP / API consumers.
            instance_id, _validated_model_override = self.spawn_instance(
                instance_id=instance_id,
                version_tag=version_tag,
                agent_id=effective_agent_id,
                **kwargs,
            )
            return instance_id
        except Exception:
            # Clean up MCP connections on spawn failure
            if hasattr(self, '_mcp_service') and self._mcp_service:
                try:
                    await self._mcp_service.close_connections(instance_id)
                except Exception as cleanup_err:
                    logger.warning(f"MCP cleanup after spawn failure failed: {cleanup_err}")
            raise

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
        metadata: dict[str, Any] | None = None,
        *,
        is_deferred: bool = False,
        is_background: bool = False,
        work_id: str | None = None,
    ) -> AsyncMessageResult:
        """Enqueue a message WITHOUT a JobItem mirror (internal-only path).

        This is the raw ``MessageQueue`` + ``Task`` path used ONLY by
        internal callers (reports, nudges, ``[JOB_EVENT]`` delivery,
        compaction, ``invoke_and_wait``, system messages). All public /
        external entry points (HTTP POST /messages, external source
        chokepoint, agent ``send_message`` tool, ``job_continue`` tool,
        scheduler, PAUSED cascade-resume) must call
        :meth:`enqueue_message_job` instead so the public facade can
        read a JobItem + Task pair.

        Behavior is byte-identical to the legacy D13 single-writer
        contract:

          1. ``MessageQueue`` + ``Task`` rows are written in a single
             transaction.
          2. The WorkerPool is notified to claim the Task.

        ``is_deferred`` (Phase 3 Part B1, 2026-06-27): keyword-only
        marker forwarded to the underlying
        ``InstanceMessagingService.enqueue_message``. When True, the
        created Task row is stamped ``is_deferred=True`` and the worker
        pool's idle gate holds the task until every non-defer queue is
        empty. Default False preserves the prior behaviour for every
        caller that does not opt in.

        ``is_background`` (Phase 3 background seam, 2026-07-14):
        keyword-only marker forwarded to the underlying
        ``InstanceMessagingService.enqueue_message``. When True, the
        created Task row is stamped ``is_background=True`` and the
        worker pool's idle gate holds the task until every non-
        deferred, non-background lane system-wide is empty. Default
        False preserves the prior behaviour for every caller that does
        not opt in (HTTP route, telegram, scheduler, internal reports).
        Independent of ``is_deferred`` — a task may be either, both, or
        neither (e.g. ``is_deferred=True, is_background=False`` for a
        defer-queued message, ``is_deferred=False, is_background=True``
        for a background-queued message, both False for a normal
        foreground message).

        Args:
            instance_id: The ID of the target instance.
            message: The message content.
            source: Source identifier (e.g., "api", "web", "telegram:user:123").
            priority: Message priority (0=system, 1=user).
            images: Optional list of base64-encoded images for vision messages.
            metadata: Optional metadata dictionary (e.g., {"resume_mode": True}).
            is_deferred: See above.
            is_background: See above.

        Returns:
            AsyncMessageResult with message_id, instance_id, status, and
            ``job_id`` populated as ``str(task_id)`` (adapter for the
            removed ``JobItem.job_id``).
        """
        return await self._messaging_service.enqueue_message(
            instance_id=instance_id,
            message=message,
            source=source,
            priority=priority,
            images=images,
            metadata=metadata,
            is_deferred=is_deferred,
            is_background=is_background,
            work_id=work_id,
        )

    async def enqueue_message_job(
        self,
        instance_id: str,
        message: str,
        source: str = "api",
        priority: int = 1,
        images: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        is_deferred: bool = False,
        is_background: bool = False,
        queue_id: str | None = None,
    ) -> AsyncMessageResult:
        """POC variant of :meth:`enqueue_message` that also creates a JobItem mirror.

        Wraps :meth:`InstanceMessagingService.enqueue_message_job`. See that
        method for the full contract — Task row remains the authoritative
        dispatch primitive; the JobItem is the informational mirror that
        the WorkResolver facade can read.

        Args:
            instance_id: Target instance ID.
            message: User content.
            source: Source tag (e.g. ``"api"``, ``"telegram:user:1"``).
            priority: 0=system, 1=user (matches ``enqueue_message``).
            images: Optional base64 images for vision messages.
            metadata: Optional metadata dict.
            is_deferred: Forwarded to ``enqueue_message_job`` — stamps
                ``Task.is_deferred=True``.
            is_background: Forwarded to ``enqueue_message_job`` — stamps
                ``Task.is_background=True`` so the dispatcher routes the
                work onto the background queue instead of the foreground
                message lane.

        Returns:
            ``AsyncMessageResult`` with ``message_id``, ``instance_id``,
            ``status="queued"``, and ``job_id`` populated as the shared
            UUID4 (Task.work_id == JobItem.job_id).
        """
        return await self._messaging_service.enqueue_message_job(
            instance_id=instance_id,
            message=message,
            source=source,
            priority=priority,
            images=images,
            metadata=metadata,
            is_deferred=is_deferred,
            is_background=is_background,
            queue_id=queue_id,
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
        silent: bool = False,  # If True, skip message injection during checkpoint resume
        task_context: str | None = None,  # Pre-formatted task context from send_message(context=...)
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
            images: Optional list of base64-encoded images for multimodal messages.
            silent: If True, resume from checkpoint without injecting any message.
            task_context: Optional pre-formatted ``[SYSTEM CONTEXT: Task Context]``
                block propagated from the parent's ``send_message(context=...)``
                call via ``message_metadata``. Threaded through to the
                messaging service which injects it as a HumanMessage BEFORE
                the task message on first attempt.

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
            silent=silent,
            task_context=task_context,
        )

    def _get_instance_report_prefix(self, instance_id: str, agent_id: str) -> str:
        """Get formatted prefix for instance completion reports.
        
        Args:
            instance_id: The instance ID.
            agent_id: The agent ID.
        
        Returns:
            Formatted prefix like "Developer agent (id=xxx) has done" or
            "Developer agent (name=create-feature-a, id=xxx) has done"
        """
        return self._child_reports_service._get_instance_report_prefix(instance_id, agent_id)

    async def _summarize_instance(self, instance_id: str, agent_id: str) -> str:
        """Summarize instance messages using LLM.
        
        Args:
            instance_id: The instance ID to summarize.
            agent_id: The agent ID (e.g., "developer", "leader").
            
        Returns:
            Formatted summary string with instance info.
        """
        return await self._child_reports_service._summarize_instance(instance_id, agent_id)

    async def _should_send_completion_report(self, session, instance_id: str, completed_message_id: str) -> bool:
        """Check if completion report should be sent (idempotency checks).
        
        Performs two checks to ensure we do not send duplicate completion reports:
        1. No pending messages (PROCESSING, RETRYING) for the instance
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

    async def _update_parent_on_child_complete(self, session, instance) -> tuple[bool, str | None, str | None]:
        """Update parent state when child completes.

        Handles:
        - Delete from instance_hierarchy table
        - Cascade: transition parent when child reports come in

        Args:
            session: Database session.
            instance: The child Instance object.

        Returns:
            Tuple of (transitioned_to_running, completed_parent_id, completed_parent_parent_id).
        """
        return await self._child_reports_service._update_parent_on_child_complete(session, instance)

    async def _create_completion_events(
        self,
        session,
        instance_id: str,
        parent_id: str,
        report_message_id: str,
        pending_for_parent: int,
    ) -> tuple[Event, Event]:
        """Create completion events for child and parent.

        Args:
            session: Database session.
            instance_id: The child instance ID.
            parent_id: The parent instance ID.
            report_message_id: The report message ID for the parent event.
            pending_for_parent: PENDING-watcher count for the parent
                (from the DependencyBus).

        Returns:
            Tuple of (completion_event, parent_event).
        """
        return await self._child_reports_service._create_completion_events(
            session, instance_id, parent_id, report_message_id, pending_for_parent
        )

    async def _process_child_completion_and_notify_parent(self, instance_id: str, completed_message_id: str) -> None:
        """Check if child instance is done and send completion report to parent.

        CRITICAL FIX C3: Content is fetched BEFORE the transaction to avoid
        leaving the instance in COMPLETED state without a report if the fetch fails.

        This method handles:
        - Idempotency per-message (won't send duplicate reports for same message)
        - Cascade: if parent has no more pending children, transition parent to RUNNING

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

    async def _get_last_assistant_message(
        self,
        instance_id: str,
        agent_id: str,
        *,
        skip_repair: bool = False,
    ) -> str | None:
        """Get the last assistant message from instance history.

        This is the default/simple approach for completion reports - just
        pass the agent's last response to the parent.

        Args:
            instance_id: The instance ID to get message from.
            agent_id: The agent ID (e.g., "developer", "leader"). Also
                drives the exclusion check in the raw call: when
                ``agent_id`` is in ``report_repair.repair_excluded_agents``
                (default: ``{"wanderer", "explorer"}``), repair is
                skipped.
            skip_repair: When True, propagate to the raw call so the
                truncation check + LLM repair + combine fallback are
                all skipped. Used by interim paths (e.g.,
                ``_emit_in_progress``) that can fire on a non-terminal
                turn and must not double-repair alongside the terminal
                completion path.

        Returns:
            Formatted string with instance info and last message.
        """
        return await self._child_reports_service._get_last_assistant_message(
            instance_id,
            agent_id,
            skip_repair=skip_repair,
        )

    async def _get_last_assistant_message_raw(
        self,
        instance_id: str,
        *,
        skip_repair: bool = False,
        agent_id: str | None = None,
    ) -> str | None:
        """Get the raw last assistant message content (no formatting).

        Returns just the actual agent response content, matching the format
        used by MessageJobHandler when setting result_summary=result.content.

        Args:
            instance_id: The instance ID to get message from.
            skip_repair: When True, return the raw last content immediately
                after fetching — skip the truncation check, LLM repair,
                and combine fallback entirely. Used by the interim
                ``_emit_in_progress`` path to prevent double-repair
                with the terminal completion path.
            agent_id: Optional agent ID for the exclusion check. When
                ``agent_id`` is in
                ``report_repair.repair_excluded_agents`` (default:
                ``{"wanderer", "explorer"}``), repair is skipped.
                ``None`` means "unknown agent" — repair runs (safe default).

        Returns:
            The raw assistant message content, or None if not found.
        """
        return await self._child_reports_service._get_last_assistant_message_raw(
            instance_id,
            skip_repair=skip_repair,
            agent_id=agent_id,
        )

        
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

    async def get_queue_stats(self, instance_id: str):
        """Get queue statistics for an instance.

        Returns a dict with pending_count, processing_count,
        and oldest_message_age_seconds attributes.
        """
        return await self._messaging_service.get_queue_stats(instance_id)

    async def _has_checkpoint(self, instance_id: str) -> bool:
        """Check if a checkpoint exists for this instance.

        Args:
            instance_id: The instance ID to check.

        Returns:
            True if checkpoint exists, False otherwise.
        """
        try:
            config = {"configurable": {"thread_id": instance_id}}
            state = await self.checkpointer.raw_saver.aget(config)
            result = state is not None
            channel_values = state.get("channel_values", {}) if state else {}
            msg_count = len(channel_values.get("messages", []))
            logger.info(f"[RESUME] instance={instance_id[:8]} has_checkpoint={result}, msg_count={msg_count}")
            return result
        except Exception as e:
            logger.info(f"[RESUME] instance={instance_id[:8]} has_checkpoint=False, exception={type(e).__name__}")
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
            state = await self.checkpointer.raw_saver.aget(config)
            if state:
                channel_values = state.get("channel_values", {})
                messages = channel_values.get("messages", [])
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
            instance_id: The ID of the instance whose graph task should be cancelled.

        Returns:
            True if a task was found and cancelled, False otherwise.
        """
        # Prefer the Execution Gate's task registry: the gate is the
        # canonical owner of any in-flight ``graph.astream`` call (any
        # path that goes through ``gate.run`` registers there). Fall
        # back to the legacy ``_graph_tasks`` dict for paths that
        # have not yet been migrated (e.g. the synchronous
        # ``send_message`` ``graph.ainvoke`` path in
        # ``InstanceMessagingService.send_message``).
        try:
            gate_cancelled = False
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Sync caller; schedule the coroutine and check
                    # locally for any tracked task.
                    fut = asyncio.run_coroutine_threadsafe(
                        self._execution_gate.cancel_instance_execution(instance_id),
                        loop,
                    )
                    try:
                        gate_cancelled = fut.result(timeout=0.1)
                    except Exception:
                        gate_cancelled = False
            except RuntimeError:
                pass
        except Exception:
            gate_cancelled = False

        task = self._graph_tasks.get(instance_id)
        if task is None and not gate_cancelled:
            logger.debug(f"No graph task to cancel for instance {instance_id[:8]}...")
            return False

        if task is not None and task.done():
            logger.debug(f"Graph task already done for instance {instance_id[:8]}...")
            del self._graph_tasks[instance_id]
            # Memory-leak fix: drop the per-instance get_instance_info
            # throttle counter alongside the dead-task cleanup. Without
            # this the dict leaks one entry per cancelled instance.
            self._gii_throttle.pop(instance_id, None)
            # Memory-leak fix: drop the per-instance loop-breaker state
            # alongside the dead-task cleanup. Same pattern as
            # ``_gii_throttle`` above (cancel_graph_task done-branch).
            self._loop_breaker_state.pop(instance_id, None)
            # Context Injection Restructure — Phase 3 (B2 fix): drop the
            # cached skill-search result alongside the dead-task cleanup
            # so the dead-task branch mirrors ``_cleanup_instance_state``.
            # ``getattr`` keeps the cleanup defensive against test
            # stubs that skip the full ``__init__``.
            _ctx_skill_results = getattr(
                self, "_context_skill_results", None
            )
            if _ctx_skill_results is not None:
                _ctx_skill_results.pop(instance_id, None)
            # skill_search_interval (Phase 4b): drop the per-instance
            # message counter alongside the cached skill-search result.
            _skill_search_counts = getattr(
                self, "_skill_search_message_counts", None
            )
            if _skill_search_counts is not None:
                _skill_search_counts.pop(instance_id, None)
            # W1 fix: drop the explicit-load marker alongside the
            # counter — same defensive ``getattr`` pattern.
            _explicit_loaded = getattr(
                self, "_explicit_skill_loaded", None
            )
            if _explicit_loaded is not None:
                _explicit_loaded.discard(instance_id)
            return gate_cancelled

        if task is not None:
            logger.info(f"Cancelling graph task for instance {instance_id[:8]}...")
            task.cancel()
        return True

    async def terminate_instance(
        self, instance_id: str, terminal_reason: str = "aborted"
    ) -> bool:
        """Terminate an instance.

        This method performs comprehensive cleanup:
        1. Cancels active requests for the instance
        2. Cascades to children - terminates all child instances first
        3. Releases project lock if this instance holds one (via JobQueueService)
        4. Cleans up instance state and resources

        Args:
            instance_id: The ID of the instance to terminate.
            terminal_reason: Phase 2 (TD-3/TD-4). Discriminator that
                distinguishes a watchover 3-strike termination
                (``"watchover_terminated"``) from a user-initiated delete
                / parent-terminate cascade (``"aborted"``). Persisted on
                the JobItem ``terminal_reason`` column. Defaults to
                ``"aborted"`` for backward compatibility with all
                existing call sites.

        Returns:
            True if termination was successful, False if instance was not found.
        """
        return await self._lifecycle_service.terminate_instance(
            instance_id, terminal_reason=terminal_reason
        )

    async def hard_delete_instance(self, instance_id: str) -> dict[str, Any]:
        """Hard-delete an instance tree from both DBs.

        Composes :meth:`InstanceLifecycleService.hard_delete_instance`:

        1. Snapshot the tree (root + all descendants).
        2. Run the standard :meth:`terminate_instance` cascade — in-memory
           cleanup, status transition, job-state transitions.
        3. FK-safe DB cascade (``job_locks`` → ``job_queue_items`` →
           ``job_watchers`` → ``tasks`` → ``events`` → ``message_queue``
           → ``instance_hierarchy`` → ``instances``).
        4. Sweep ``checkpoints.db`` threads via the
           ``CheckpointerAdapter``.

        This is a destructive operator call. Use it from admin/cleanup
        paths only — the DELETE endpoint exposes it via the
        ``?hard_delete=true`` query parameter.

        Args:
            instance_id: Root of the tree to delete.

        Returns:
            Dict summarising the deletion — see
            :meth:`InstanceLifecycleService.hard_delete_instance` for the
            shape.
        """
        return await self._lifecycle_service.hard_delete_instance(instance_id)

    async def pause_instance_cascade(
        self,
        instance_id: str,
        *,
        suspension_reason: str | None = None,
    ) -> dict:
        """Pause an instance and cascade to all children (soft pause).

        Recursively pauses the target instance and all its descendants.
        Cancels active requests and sets status to paused (resumable).
        Does NOT remove instances from memory or release locks.

        Args:
            instance_id: The ID of the instance to pause.
            suspension_reason: Optional reason persisted on suspended task
                turns. ``None`` preserves the lifecycle service's existing
                ``paused_external`` default.

        Returns:
            Dict with:
              - paused_ids: list of all instance IDs that were paused
              - skipped_ids: list of instance IDs that were already paused (skipped)
        """
        return await self._lifecycle_service.pause_instance_cascade(
            instance_id,
            suspension_reason=suspension_reason,
        )

    async def resume_instance_cascade(self, instance_id: str) -> dict:
        """Resume an instance and cascade to all children.

        Recursively resumes the target instance and all its descendants.
        Sets status to RUNNING and clears paused_at.
        Does NOT re-spawn or restart instances - just unpauses them.

        Args:
            instance_id: The ID of the instance to resume.

        Returns:
            Dict with:
              - resumed_ids: list of all instance IDs that were resumed
              - skipped_ids: list of instance IDs that were skipped (not paused)
        """
        return await self._lifecycle_service.resume_instance_cascade(instance_id)

    async def resume_processing_job(
        self,
        instance_id: str,
        message: str = "resume",
        silent: bool = False,
        images: list[str] | None = None,
    ) -> dict | None:
        """Resume a paused instance via an explicit suspension handle.

        Phase 4 (Increment 4, 2026-08-01). The previous root-vs-child
        routing was inference-based
        (``find_paused_or_running_by_instance`` plus the Bug-A
        ``find_resume_root_candidate_by_active_job`` fallback); both
        have been removed. The explicit
        ``suspension_reason`` / ``resume_target_turn_id`` handle
        persisted by :class:`SuspendTurn` at SUSPEND_TURN time is now
        the authoritative routing input.

        Four explicit outcomes per §9.3:

          * ``answer_gate_existing_turn`` — an explicit
            ``suspension_reason='awaiting_answer'`` handle was
            resolved; the persisted ``resume_target_turn_id`` is the
            authoritative work_id a later ``ResumeTurn`` reattaches
            to. The answer payload is delivered onto the existing
            turn; no new Task or JobItem is created (§9.1 steps 4-7).
          * ``report_or_external_resume`` — the pause-cascade
            selector (:meth:`TaskRepository.find_paused_or_cancellable_turn`)
            resolved an explicit PAUSED/RUNNING turn. The turn's
            own ``work_id`` is the resume point. Includes the
            pause-during-``process_report`` regression case from
            Bug A.
          * ``internal_child_noop`` — ``silent=True`` with no
            handle. Legitimate silent child cascade where the parent
            owns the actual work and the child does not need a new
            message. Preserved per §9.3.
          * ``invalid_or_missing_handle`` — no answer-gate handle,
            no paused turn, ``silent=False``. Log a warning and
            return ``None``. Do NOT fabricate a Task via
            ``enqueue_message``; the answer-gate
            ``source="cascade_resume"`` fallback has been removed
            per §9.4.

        §9.4 (narrow removal): the previous
        ``enqueue_message(source="cascade_resume")`` answer-gate
        fallback is gone. Legitimate non-answer internal
        orchestration that does not flow through this method is
        unaffected — only the answer-gate path is removed. The
        ``message_source="cascade_resume"`` label inside
        ``_resume_processing_background`` (the graph driver call)
        is the resume flow's own message tag and stays.

        ``old_job_id`` is derived from the explicit handle's
        ``work_id`` (the suspended Task's ``work_id`` for
        ``report_or_external_resume``, the persisted
        ``resume_target_turn_id`` for ``answer_gate_existing_turn``).
        The consumer in ``_resume_processing_background`` resolves
        it back via ``_task_repo.get_by_work_id(old_job_id)``.

        Structured route logging (§9.3) emits
        ``route_outcome``, ``suspension_reason``, ``handle_work_id``,
        and ``target_work_id`` so operators can identify the
        selected turn and outcome without answer payload exposure.

        Args:
            instance_id: The instance ID.
            message: The resume message text (ignored when
                ``silent=True``).
            silent: If True, resume from checkpoint without
                injecting a new message.
            images: Optional list of base64-encoded images for
                multimodal content.

        Returns dict with result info (``instance_id``, ``job_id``,
        ``message_id``, ``status``), or ``None`` for the
        ``invalid_or_missing_handle`` outcome.
        """
        logger.info(
            f"[RESUME] instance={instance_id[:8]} resume_processing_job "
            f"called, message={repr(message)}, silent={silent}"
        )

        # 1. Check for explicit answer-gate suspension (§9.1).
        #    The selector is keyed on the persisted
        #    ``suspension_reason='awaiting_answer'`` handle set by
        #    SuspendTurn at question-pause time. The handle's
        #    ``resume_target_turn_id`` is the authoritative work_id
        #    a later ResumeTurn should reattach to. The instance_id
        #    alone is enough — answers map to instances, then the
        #    handle provides uniqueness (§8.1).
        try:
            suspended_turn = await asyncio.to_thread(
                self._task_repo.find_suspended_turn_for_answer,
                instance_id,
            )
        except ValueError as exc:
            # §8.1 ambiguity: more than one row matched. Surface as
            # a logged invariant violation; route to the invalid
            # handle path so we never silently fabricate a Task.
            logger.error(
                f"[RESUME] instance={instance_id[:8]} "
                f"find_suspended_turn_for_answer invariant "
                f"violation: {exc}"
            )
            suspended_turn = None

        if suspended_turn is not None:
            # answer_gate_existing_turn: explicit awaiting_answer
            # handle resolved. Resume via the target work_id; do NOT
            # create a new Task or JobItem, and do NOT call
            # enqueue_message (§9.1 steps 4-7, §9.4).
            return await self._schedule_explicit_handle_resume(
                instance_id=instance_id,
                message=message,
                silent=silent,
                images=images,
                target_work_id=suspended_turn.resume_target_turn_id,
                selected_suspension_reason=suspended_turn.suspension_reason,
                handle_work_id=suspended_turn.work_id,
                route_outcome="answer_gate_existing_turn",
            )

        # 2. Check for paused/cancellable turn (§9.2). This is the
        #    pause-cascade selector — PAUSED/RUNNING PROCESS_MESSAGE
        #    or PROCESS_REPORT rows that the named transitions may
        #    legally act upon. The pause-during-report-turn
        #    scenario lands here, surfacing the in-flight
        #    PROCESS_REPORT row as a root candidate.
        try:
            paused_turn = await asyncio.to_thread(
                self._task_repo.find_paused_or_cancellable_turn,
                instance_id,
            )
        except ValueError as exc:
            # §8.2: one-running-turn-per-instance invariant
            # violation. Surface the inconsistency in logs; treat
            # as no handle. We do NOT fall through to enqueue
            # (the answer-gate fallback that used to live here has
            # been removed per §9.4).
            logger.error(
                f"[RESUME] instance={instance_id[:8]} "
                f"find_paused_or_cancellable_turn invariant "
                f"violation: {exc}"
            )
            paused_turn = None

        if paused_turn is not None:
            # report_or_external_resume: explicit non-answer
            # suspended turn. Resume the existing turn's work_id
            # (the turn itself is the resume point — no separate
            # target indirection is required here).
            return await self._schedule_explicit_handle_resume(
                instance_id=instance_id,
                message=message,
                silent=silent,
                images=images,
                target_work_id=paused_turn.work_id,
                selected_suspension_reason=paused_turn.suspension_reason,
                handle_work_id=paused_turn.work_id,
                route_outcome="report_or_external_resume",
            )

        # 3. No suspension handle and no paused turn.
        if silent:
            # internal_child_noop: legitimate silent child cascade
            # where the parent owns the actual work and the child
            # does not need a new message. Preserved per §9.3.
            logger.info(
                f"[RESUME] instance={instance_id[:8]} "
                f"route_outcome=internal_child_noop "
                f"silent=True — skipping message enqueue"
            )
            return {
                "instance_id": instance_id,
                "job_id": None,
                "message_id": None,
                "status": "silent_resume",
            }

        # invalid_or_missing_handle: no answer-gate handle, no
        # paused turn, silent=False. Log a warning and return
        # None. Do NOT fabricate a Task. Do NOT call
        # enqueue_message(source="cascade_resume") — the
        # answer-gate cascade_resume fallback is removed per §9.4.
        # The previous "child instance path" fall-through that
        # enqueued a fresh message via WorkerPool and returned
        # status="queued" is gone; an absent handle is now a
        # routing error, not a routing default.
        logger.warning(
            f"[RESUME] instance={instance_id[:8]} "
            f"route_outcome=invalid_or_missing_handle — "
            f"no suspended or paused turn found"
        )
        return None

    async def _schedule_explicit_handle_resume(
        self,
        *,
        instance_id: str,
        message: str,
        silent: bool,
        images: list[str] | None,
        target_work_id: str,
        selected_suspension_reason: str | None,
        handle_work_id: str,
        route_outcome: str,
    ) -> dict:
        """Schedule graph resume against an explicit suspension handle.

        Phase 4 (Increment 4, 2026-08-01). Shared by the
        ``answer_gate_existing_turn`` and ``report_or_external_resume``
        routes — the difference between them is the SELECTOR that
        produced the handle, not the schedule / cleanup logic.

        The flow (matches the previous root-resume path):

          1. Deduplicate against any in-flight resume in
             ``_graph_tasks[instance_id]`` (W4 case 5 invariant).
          2. Clean stale ``MessageQueue`` rows (PENDING /
             PROCESSING / RETRYING) for the instance — the
             antiphantom-race guard prevents a fresh PROCESS_REPORT
             claim from being phantom-completed.
          3. Schedule ``_resume_processing_background`` against
             ``target_work_id`` (the work_id a later ResumeTurn
             reattaches to).

        Args:
            instance_id: The instance being resumed.
            message: The resume message text (ignored when
                ``silent=True``).
            silent: If True, skip message injection during checkpoint
                resume.
            images: Optional list of base64-encoded images for
                multimodal content.
            target_work_id: The work_id a later ``ResumeTurn``
                reattaches to. For ``answer_gate_existing_turn``,
                this is the suspended Task's persisted
                ``resume_target_turn_id`` (the authoritative
                target). For ``report_or_external_resume``, this
                is the suspended Task's own ``work_id`` (it IS
                the resume point).
            selected_suspension_reason: The ``suspension_reason``
                recorded on the selected handle, for structured
                logging. ``None`` when the cascade backfill set
                ``paused_external`` legacy-style without a
                recorded reason (the repo method already returns
                the column value, which may be None for a
                legacy row).
            handle_work_id: The work_id of the selected handle
                row itself (for log correlation).
            route_outcome: The semantically named outcome for
                structured logging
                (``answer_gate_existing_turn`` or
                ``report_or_external_resume``).
        """
        logger.info(
            f"[RESUME] instance={instance_id[:8]} "
            f"route_outcome={route_outcome} "
            f"suspension_reason={selected_suspension_reason} "
            f"handle_work_id={handle_work_id} "
            f"target_work_id={target_work_id}"
        )

        # Deduplication: prevent multiple concurrent resume tasks
        # for the same instance. If a resume is already in flight,
        # return ``already_resuming`` rather than start a second
        # graph turn (W4 case 5 invariant).
        graph_task = self._graph_tasks.get(instance_id)
        if graph_task and not graph_task.done():
            logger.warning(
                f"Resume already in progress for {instance_id[:8]}"
            )
            return {
                "instance_id": instance_id,
                "job_id": target_work_id,
                "message_id": None,
                "status": "already_resuming",
            }

        # 1. Clean stale MessageQueue entries (PENDING, PROCESSING,
        #    RETRYING). These are stale entries from the previous
        #    processing attempt.
        try:
            # Use list() with instance_id filter, then filter by
            # status in Python.
            all_messages = await asyncio.to_thread(
                self._queue_repository.list,
                instance_id=instance_id,
            )
            pending_messages = [
                msg for msg in all_messages
                if msg.status in (
                    MessageStatus.PENDING.value,
                    MessageStatus.PROCESSING.value,
                    MessageStatus.RETRYING.value,
                )
            ]
            completed_count = 0
            skipped_phantom_count = 0
            for msg in pending_messages:
                if msg.status in (
                    MessageStatus.PROCESSING.value,
                    MessageStatus.RETRYING.value,
                ):
                    # ANTIPHANTOM-RACE-FIX (Root Cause B — PRIMARY FIX):
                    # Look up the corresponding task BEFORE marking
                    # this PROCESSING/RETRYING message as COMPLETED.
                    # The race: after ``_resume_cascade_db_sync``
                    # lifted the pause and woke the WorkerPool, a
                    # freshly-claimed PROCESS_REPORT task (status
                    # RUNNING) may have transitioned its message
                    # READY → PROCESSING just before this cleanup
                    # runs. Marking such a message COMPLETED would
                    # "phantom-complete" it and the subsequent
                    # ``cancel_task`` would kill the worker's
                    # in-flight LLM call, stranding the parent (no
                    # lifecycle event emitted).
                    #
                    # Safe to clean up the message ONLY when the
                    # task is in a terminal/stale state — i.e. no
                    # worker will (or can) deliver it. Task
                    # statuses that mean "safe to mark message
                    # COMPLETED + cancel task":
                    #   • PAUSED  — cascade hadn't reached it yet
                    #     (defensive)
                    #   • CANCELLED — cascade already cancelled it
                    #     (PAUSED→CANCELLED)
                    #   • COMPLETED / FAILED — task finished; message
                    #     is orphan
                    # statuses that mean "we CANCEL the task +
                    # complete the message so resume is the sole
                    # driver":
                    #   • PENDING  — Phase 4b/4c: the resume cascade
                    #     transitioned the Task ``PAUSED → PENDING``.
                    #     If we leave it alone the WorkerPool will
                    #     re-claim it and race
                    #     ``_resume_processing_background`` on the
                    #     same checkpoint, corrupting LangGraph
                    #     state via the add_messages reducer's
                    #     project-context-message replacement. Cancel
                    #     the PENDING task AND complete the orphan
                    #     message_queue row so resume is the sole
                    #     driver (matches the pre-migration CANCELLED
                    #     handling below).
                    #   • RUNNING  — worker is actively driving
                    #     ``graph.astream``; skip — completing the
                    #     message here would kill the in-flight LLM
                    #     call or skip natural delivery.
                    # No task row at all → defensive: do NOT touch
                    # orphan messages.
                    try:
                        stale_task = await asyncio.to_thread(
                            self._task_repo.get_by_message, msg.message_id
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to look up task for message "
                            f"{msg.message_id[:8]}...; skipping cleanup "
                            f"(phantom-completion guard): {e}"
                        )
                        continue

                    if stale_task is None:
                        # Defensive: no task row → don't touch
                        # orphan messages.
                        logger.info(
                            f"[RESUME] skipping message "
                            f"{msg.message_id[:8]}... — no task "
                            f"found (phantom-completion guard)"
                        )
                        skipped_phantom_count += 1
                        continue

                    if stale_task.status == TaskStatus.RUNNING.value:
                        # Worker has already claimed and is actively
                        # driving ``graph.astream``. Marking the
                        # message COMPLETED here would kill the
                        # in-flight LLM call or skip natural delivery.
                        logger.info(
                            f"[RESUME] skipping message "
                            f"{msg.message_id[:8]}... — task "
                            f"{stale_task.id} status=RUNNING is "
                            f"worker-driven (phantom-completion guard)"
                        )
                        skipped_phantom_count += 1
                        continue

                    if stale_task.status == TaskStatus.PENDING.value:
                        # Phase 4b/4c (2026-08-12, pause/resume redesign):
                        # the resume cascade transitions the Task
                        # ``PAUSED → PENDING`` (was
                        # ``PAUSED → CANCELLED`` pre-migration). The
                        # Task is therefore LIVE here, and the
                        # WorkerPool would otherwise re-claim it and
                        # race ``_resume_processing_background`` on
                        # the same checkpoint — corrupting the
                        # LangGraph state via the add_messages
                        # reducer's project-context-message
                        # replacement. Cancel the PENDING task AND
                        # complete the orphan message_queue row so
                        # the resume path is the sole driver
                        # (matching the pre-migration behavior for
                        # CANCELLED tasks at line 6320-6363).
                        try:
                            await asyncio.to_thread(
                                self._queue_repository.complete,
                                msg.message_id,
                            )
                            completed_count += 1
                            logger.info(
                                f"Completed orphan message entry "
                                f"{msg.message_id[:8]}... for resume "
                                f"(paired PENDING task {stale_task.id})"
                            )
                        except Exception as complete_err:
                            logger.warning(
                                f"Failed to complete orphan message "
                                f"{msg.message_id[:8]}...: "
                                f"{complete_err}"
                            )
                        try:
                            await asyncio.to_thread(
                                self._task_repo.cancel_task,
                                stale_task.id,
                                "Superseded by resume_processing_job graph driver",
                            )
                            logger.info(
                                f"[RESUME] cancelled stale PENDING "
                                f"task {stale_task.id} (message "
                                f"{msg.message_id[:8]}...) — graph "
                                f"driving owned by "
                                f"resume_processing_job"
                            )
                        except Exception as cancel_err:
                            logger.warning(
                                f"Failed to cancel PENDING task "
                                f"{stale_task.id} for message "
                                f"{msg.message_id[:8]}...: "
                                f"{cancel_err}"
                            )
                        continue

                    # stale_task.status is PAUSED / CANCELLED /
                    # COMPLETED / FAILED — the task will not deliver
                    # this message, so it is safe to mark
                    # COMPLETED and cancel the task.
                    try:
                        await asyncio.to_thread(
                            self._queue_repository.complete, msg.message_id
                        )
                        completed_count += 1
                        logger.info(
                            f"Completed stale message entry "
                            f"{msg.message_id[:8]}... for resume"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to complete stale message "
                            f"{msg.message_id[:8]}...: {e}"
                        )
                    # Cancel the WorkerPool task that drives this
                    # message so it is NOT re-armed/re-claimed on
                    # resume. Phase 4b/4c: ``_resume_cascade_db_sync``
                    # transitions PAUSED tasks ``PAUSED → PENDING``
                    # (was ``PAUSED → CANCELLED`` pre-migration);
                    # the PENDING-task branch above is the new
                    # entry into this cancel path. Without
                    # cancelling here, the re-claimed
                    # ``process_message`` task would re-drive the
                    # graph a SECOND time (a duplicate turn that
                    # races with ``_resume_processing_background``
                    # and corrupts the checkpoint — the
                    # add_messages reducer replaces the
                    # project-context message with a bare
                    # re-injection of the same ID).
                    try:
                        await asyncio.to_thread(
                            self._task_repo.cancel_task,
                            stale_task.id,
                            "Superseded by resume_processing_job graph driver",
                        )
                        logger.info(
                            f"[RESUME] cancelled stale task "
                            f"{stale_task.id} (message "
                            f"{msg.message_id[:8]}..., prior status="
                            f"{stale_task.status}) — graph driving "
                            f"owned by resume_processing_job"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to cancel stale task for "
                            f"message {msg.message_id[:8]}...: {e}"
                        )
                elif msg.status == MessageStatus.PENDING.value:
                    logger.info(
                        f"Preserving PENDING message "
                        f"{msg.message_id[:8]}... for post-resume "
                        f"delivery"
                    )
            pending_count = sum(
                1 for msg in pending_messages
                if msg.status == MessageStatus.PENDING.value
            )
            if (
                completed_count > 0
                or pending_count > 0
                or skipped_phantom_count > 0
            ):
                logger.info(
                    f"[RESUME] instance={instance_id[:8]} cleaned "
                    f"{completed_count} stale PROCESSING/RETRYING "
                    f"messages, preserved {pending_count} PENDING "
                    f"messages, skipped {skipped_phantom_count} "
                    f"phantom-completion guards (active worker)"
                )
        except Exception as e:
            logger.warning(
                f"Failed to find/complete stale messages for "
                f"{instance_id[:8]}...: {e}"
            )

        # 2. Create a fresh message_id for tracking (not enqueued,
        #    just for internal tracking).
        message_id = str(uuid.uuid4())

        # 3. Return immediately — processing happens in the
        #    background task. This allows the HTTP response to
        #    return fast while the LLM processes asynchronously.
        logger.info(
            f"[RESUME] instance={instance_id[:8]} scheduling "
            f"background processing against target_work_id="
            f"{target_work_id}"
        )

        # W4: Register with the request registry BEFORE creating
        # the task so ``pause_instance_cascade`` (which calls
        # ``_request_registry.cancel_by_instance`` cooperatively)
        # can interrupt the in-flight LLM streaming via the
        # CancellationToken rather than killing the asyncio task
        # abruptly. The registry returns a
        # CancellationTokenSource; we thread ``.token`` into the
        # background task and unregister in the outermost finally
        # block.
        cancellation_source = self._request_registry.register(
            message_id=message_id,
            instance_id=instance_id,
        )
        task = asyncio.create_task(
            self._resume_processing_background(
                instance_id=instance_id,
                message=message if not silent else "",
                message_id=message_id,
                old_job_id=target_work_id,
                silent=silent,
                images=images,
                cancellation_token=cancellation_source.token,
            )
        )
        self._graph_tasks[instance_id] = task

        return {
            "instance_id": instance_id,
            "job_id": target_work_id,
            "message_id": message_id,
            "status": "resuming",
        }

    async def _resume_processing_background(
        self,
        instance_id: str,
        message: str,
        message_id: str,
        old_job_id: str,
        silent: bool,
        images: list[str] | None,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        """Background task for resumed processing.

        Runs _process_message_with_tracking, handles completion/child notification,
        and completes the job. This runs asynchronously after the HTTP response
        has already been sent.

        The ``_process_message_with_tracking`` call is wrapped in the
        ``ExecutionGate`` so the resume path cannot race with a sibling
        dispatcher (WorkerPool task or MESSAGE job) on the same
        checkpoint. Under the asyncio.Lock gate the second caller blocks
        on the same event loop until the holder releases; there is no
        contention return path.

        Phase 4b/4c (2026-08-12, pause/resume redesign): the resume
        cascade now transitions the Task ``PAUSED → PENDING`` (was
        ``PAUSED → CANCELLED`` pre-migration). The Task stays live
        throughout the pause/resume cycle, so this background task
        drives the graph turn via ``_process_message_with_tracking``
        with ``is_retry=True`` (reload the LangGraph checkpoint under
        the same ``work_id``). The WorkerPool may ALSO claim the
        PENDING task — the per-instance guard (``status='running'``)
        ensures only ONE driver runs at a time per instance, but the
        cleanup logic in ``_schedule_explicit_handle_resume`` may
        cancel PENDING tasks to prevent duplicate-turn races. The
        Task's terminal transition is owned by the natural
        ``complete_task`` / ``fail_task`` flow.

        Phase 1 (2026-06-27, Virtual Job Management Surface): ``old_job_id``
        is now the Task's stable ``work_id`` (UUID4 string) — the same
        identifier ``AsyncMessageResult.job_id`` carries. It is passed
        through to ``_process_resume_finalize`` and ultimately
        ``_finalize_job_db_sync`` as the logical ``job_id`` — when there
        is no ``JobItem`` for the message (the post-D13 norm),
        ``_finalize_job_db_sync`` accepts ``job_id=None`` and skips Step
        1 (JobItem UPDATE) while still running Steps 2+3 (instance
        status + lock release).

        Args:
            instance_id: The instance ID.
            message: The resume message text.
            message_id: The internal tracking message ID.
            old_job_id: The Task's stable ``work_id`` (UUID4 string);
                Phase 1 (Virtual Job Management Surface); pre-D13 this
                was a ``JobItem.job_id``, Phase 2.5 it was the int Task
                ``id``.
            silent: If True, resume from checkpoint without injecting a new message.
            images: Optional list of base64-encoded images for multimodal content.
            cancellation_token: Optional token for cooperative cancellation.
                Passed through to ``_process_message_with_tracking`` so
                ``pause_instance_cascade`` (via ``_request_registry``)
                can interrupt LLM streaming cooperatively rather than
                abruptly via ``task.cancel()``. See W4.
        """
        from .services.job_queue_service import DemandState

        # W3: Wrap the entire body in try/finally so the per-instance
        # cleanup (``_graph_tasks`` + ``_request_registry.unregister``)
        # runs on EVERY exit path: clean completion or unhandled exception.
        try:
            # 1. Acquire ExecutionGate lock before driving graph.astream.
            #    Race #5 fix: without this, a concurrent /resume call (or a
            #    WorkerPool / JobQueue dispatch) would race on the langgraph
            #    checkpoint and corrupt it.
            async def _do_process():
                return await self._process_message_with_tracking(
                    instance_id=instance_id,
                    message=message,
                    message_id=message_id,
                    cancellation_token=cancellation_token,  # W4: enables cooperative pause
                    is_retry=True,  # Triggers checkpoint resume
                    retry_count=0,
                    message_source="cascade_resume",
                    silent=silent,  # Pass through silent flag
                    images=images,
                )

            gate_outcome: Any = None
            gate_raised: BaseException | None = None
            try:
                gate_outcome = await self._execution_gate.run(
                    instance_id=instance_id,
                    holder_id=f"resume:{message_id}",
                    # Same kind label as the other message-driven
                    # dispatcher paths; the asyncio.Lock gate ignores
                    # ``holder_kind`` so any stable string works.
                    holder_kind="message_job",
                    work_fn=_do_process,
                )
            except BaseException as e:  # noqa: BLE001 - surfaced via gate_raised below
                gate_raised = e

            # 2. The gate either returned a clean result or raised an
            #    exception. Re-raise inside the existing try/except
            #    block below so the unified error handler (job FAILED,
            #    instance ERROR) runs.
            result = gate_outcome

            try:
                if gate_raised is not None:
                    raise gate_raised

                logger.info(f"[RESUME] instance={instance_id[:8]} background processing completed successfully")

                # 2. Process child completion and notify parent
                try:
                    await self._process_child_completion_and_notify_parent(instance_id, message_id)
                except Exception as e:
                    logger.warning(f"Failed to process child completion for {instance_id[:8]}...: {e}")

                # 3. Phase 3 (pause/resume redesign, 2026-06-25) — C1 fix:
                # the deterministic finalize trigger replaces the old
                # direct ``complete_job()`` call.
                #
                # The pre-Phase 3 code had TWO bugs:
                #   1. TOCTOU: ``bus.count_pending_for_target()`` was
                #      called OUTSIDE any transaction, then a direct
                #      ``complete_job(COMPLETED)`` was issued. A child
                #      report landing between the check and the write
                #      caused a premature completion.
                #   2. No-op gap (C1): if the graph turn was a no-op
                #      (no lifecycle event fired), the
                #      ``complete_job`` branch was never reached, and
                #      ``_process_event``'s lifecycle-event filter
                #      (``status IN (COMPLETED, ERROR)``) short-circuited
                #      — the job stayed PROCESSING forever.
                #
                # The new ``_process_resume_finalize`` method:
                #   * validates the bus is initialized (A9 hard-error
                #     carries forward — raises RuntimeError if bus is None)
                #   * looks up the active PROCESSING job
                #     (no-op if already finalized by a racing event)
                #   * pre-checks bus pending (NON-AUTHORITATIVE
                #     optimization — emits in_progress and defers)
                #   * reuses ``_finalize_job`` for the actual
                #     transition (same path as ``_process_event``)
                #
                # This fires on EVERY graph turn (including no-ops) so
                # the no-op-gap bug is closed: even a no-op resume
                # produces a terminal transition.

                # A9: the bus lookup is inside _process_resume_finalize
                # and raises hard if None. We do NOT pre-check the bus
                # here so the A9 invariant is enforced in exactly one
                # place (the observer method).
                try:
                    instance = await asyncio.to_thread(
                        self._instance_repository.get, instance_id
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to look up instance for resume finalize "
                        f"{instance_id[:8]}...: {e}"
                    )
                    instance = None

                if instance is None:
                    # Defensive: instance vanished mid-resume (e.g. a
                    # concurrent terminate). Skip finalize — the
                    # terminate path owns the terminal transition.
                    logger.warning(
                        f"[RESUME] instance={instance_id[:8]} not found "
                        f"in DB, skipping finalize"
                    )
                    return

                if self._job_feedback_observer is not None:
                    await self._job_feedback_observer._process_resume_finalize(
                        instance_id=instance_id,
                        job_id=old_job_id,
                        result_summary=(
                            result.content if result else None
                        ),
                    )
                else:
                    # Hard-error fallback (Phase 3 review, W3 fix): the
                    # observer must be wired before resume processing.
                    # The legacy direct ``complete_job`` path was the
                    # exact TOCTOU bug Phase 3 eliminates — silently
                    # regressing to it on a misconfigured fixture would
                    # re-open the premature-finalization window. Fail
                    # loudly so the wiring is fixed rather than
                    # masked.
                    raise RuntimeError(
                        "JobFeedbackObserver required for resume finalize — "
                        "observer must be wired before resume processing"
                    )

                # 5. Task lifecycle is now owned by the WorkerPool re-claim path.
                #
                # Phase 4b/4c (2026-08-12, pause/resume redesign) — the
                # resume path no longer calls ``complete_task()`` on the
                # original paused task. The Task stays live throughout the
                # pause/resume cycle and the resume cascade transitions
                # it ``PAUSED → PENDING`` (was ``PAUSED → CANCELLED``
                # pre-migration). The lifecycle is:
                #
                #   1. Pause: ``task`` RUNNING → PAUSED (Phase 2, in
                #      ``_pause_cascade_db_sync``).
                #   2. Resume: ``task`` PAUSED → PENDING (Phase 4b/4c,
                #      in ``_resume_cascade_db_sync``).
                #   3. WorkerPool: ``task`` PENDING → RUNNING via
                #      ``claim_pending_task`` (per-instance guard now
                #      passes because the instance is RUNNING).
                #   4. Worker: ``task`` RUNNING → COMPLETED/FAILED via
                #      ``complete_task`` / ``fail_task`` (after the
                #      graph turn finishes).
                #
                # The pre-Phase 4b/4c code completed the task here so the
                # per-instance guard released for the bus-fired child
                # completion report. With the new state machine, the
                # WorkerPool re-claim is the canonical release path —
                # completing the task here would race with the re-claim
                # and potentially flip a PENDING task to COMPLETED
                # before a worker can pick it up.
                #
                # The follow-up Tasks (e.g. the bus-fired child
                # completion report) are now claimable as soon as the
                # ``task`` row leaves PAUSED, which the new
                # ``_resume_cascade_db_sync`` does atomically with the
                # instance + job transitions.

            except Exception as e:
                logger.error(f"[RESUME] instance={instance_id[:8]} background processing failed: {type(e).__name__}: {e}")
                # Mark the Task as FAILED on failure. Phase 1 (Virtual
                # Job Management Surface): ``old_job_id`` is now the
                # Task's stable ``work_id`` (UUID4 string), not the int
                # primary key — the int() parse was always ValueError so
                # this safety net was dead code. Look the Task up by
                # ``work_id`` and fail it by ``id`` so the per-instance
                # guard releases — otherwise the next ``job_continue``
                # call is blocked by ``has_instance_busy`` (which counts
                # PENDING + RUNNING + PAUSED tasks for the instance).
                #
                # Phase 4b/4c (2026-08-12, pause/resume redesign):
                # post-resume the Task is ``pending`` (was
                # ``cancelled`` pre-migration). The ``fail_task``
                # ``WHERE status = 'running'`` guard means the call
                # is a no-op if the WorkerPool has already claimed
                # and completed the Task (the typical race outcome);
                # if the WorkerPool claimed but the graph turn has
                # not yet finished, ``fail_task`` will also no-op
                # and the WorkerPool will complete naturally — in
                # both cases the next ``job_continue`` is unblocked
                # once the WorkerPool marks the Task terminal.
                #
                # W1 (2026-08-12, concurrency-gate review): the
                # ``fail_task`` ``status='running'`` guard does NOT
                # match a PENDING Task (post-resume). When the
                # resume background task fails before the WorkerPool
                # claims, ``fail_task`` returns ``None`` and the
                # Task stays PENDING — ``has_instance_busy`` keeps
                # returning ``True`` forever, permanently blocking
                # the instance and ``job_continue``. The fallback
                # below calls ``cancel_task`` (whose broader
                # ``WHERE status IN (running, pending, paused)``
                # guard at ``repository.py:3158-3187`` matches
                # PENDING) to force the Task terminal so the
                # instance unblocks. The WorkerPool's natural
                # completion path remains the preferred outcome —
                # this fallback only fires when resume failed
                # before any worker picked the Task up.
                failed_task = None
                try:
                    task = await asyncio.to_thread(
                        self._task_repo.get_by_work_id, old_job_id
                    )
                    if task is not None:
                        failed_task = await asyncio.to_thread(
                            self._task_repo.fail_task,
                            task.id,
                            f"Resume failed: {e}",
                        )
                    else:
                        logger.debug(
                            f"[RESUME] no task found for work_id "
                            f"{old_job_id!r}, skipping fail_task"
                        )
                except Exception:
                    pass

                # Phase 4b/4c (W1 fix): if ``fail_task`` returned
                # ``None`` the Task was not RUNNING (post-resume it
                # is PENDING — the ``fail_task``
                # ``WHERE status = 'running'`` guard does not match
                # PENDING). Fall back to ``cancel_task`` which
                # handles PENDING/PRESSED tasks via a broader inline
                # UPDATE guard. Without this fallback the Task stays
                # non-terminal and ``has_instance_busy`` returns
                # ``True`` forever, permanently blocking the
                # instance.
                if failed_task is None and task is not None:
                    try:
                        cancelled_task = await asyncio.to_thread(
                            self._task_repo.cancel_task,
                            task.id,
                            f"Resume failed (task was not RUNNING): {e}",
                        )
                        if cancelled_task is not None:
                            failed_task = cancelled_task
                            logger.info(
                                f"[RESUME] instance={instance_id[:8]} "
                                f"fail_task no-op'd (task was PENDING), "
                                f"fell back to cancel_task"
                            )
                    except Exception:
                        pass

                # Phase 2 Batch 2 — fire watcher notification only if
                # the atomic fail_task returned non-None (i.e. we won
                # the status=running guard race). The resume path runs
                # on the async lifespan, so we await the notifier
                # directly instead of bridging through MainLoopBridge.
                if failed_task is not None:
                    try:
                        from daemon.services.work_notifier import (
                            notify_work_watchers,
                        )
                        await notify_work_watchers(
                            work_id=failed_task.work_id,
                            status="failed",
                            error=f"Resume failed: {e}",
                            instance_manager=self,
                            work_resolver=getattr(
                                self, "_work_resolver", None
                            ),
                            watcher_repo=getattr(
                                self, "_watcher_repo", None
                            ),
                        )
                    except Exception as notify_err:
                        logger.debug(
                            f"[RESUME] notify_work_watchers failed for "
                            f"work_id={old_job_id[:8]}...: "
                            f"{type(notify_err).__name__}: {notify_err}"
                        )

                # Update instance status to ERROR
                try:
                    self._instance_repository.update_instance(
                        instance_id,
                        status=InstanceStatus.ERROR.value,
                    )
                    logger.info(f"[RESUME] instance={instance_id[:8]} status set to ERROR")
                except Exception:
                    pass
        finally:
            # W3: Per-instance cleanup runs exactly once — on every
            # exit path of this background task. Without this, an
            # unhandled exception or a cancellation would leave a stale
            # ``_graph_tasks[instance_id]`` entry behind, blocking the
            # next resume call (which short-circuits to
            # ``"already_resuming"`` because ``existing_task`` is not
            # done).
            try:
                self._graph_tasks.pop(instance_id, None)
            except Exception as cleanup_err:
                logger.warning(
                    f"[RESUME] failed to pop _graph_tasks entry for "
                    f"{instance_id[:8]}...: {type(cleanup_err).__name__}: "
                    f"{cleanup_err}"
                )
            # W4: Unregister from the request registry so the CTS
            # created by ``resume_processing_job`` is released. Runs
            # independently of the ``_graph_tasks`` pop above so the
            # CTS is released on every exit path, not only when the
            # pop fails. If the registry isn't present (some test
            # doubles skip it), swallow the AttributeError so the
            # cleanup path never raises.
            try:
                self._request_registry.unregister(message_id)
            except AttributeError:
                pass
            except Exception as unreg_err:
                logger.warning(
                    f"[RESUME] failed to unregister request "
                    f"{message_id[:8]}...: "
                    f"{type(unreg_err).__name__}: {unreg_err}"
                )

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
        return await self._lifecycle_service.get_instance(instance_id)

    async def _restore_instance(self, instance_id: str, meta: "Instance") -> CompiledStateGraph:
        """Restore an instance from database into memory.

        Rebuilds the graph with the same instance_id. The checkpointer will
        restore conversation state from LangGraph's checkpoint tables.

        Args:
            instance_id: The ID of the instance to restore.
            meta: Instance metadata from database.

        Returns:
            The restored CompiledStateGraph instance.
        """
        return await self._lifecycle_service._restore_instance(instance_id, meta)

    def list_instances(
        self,
        limit: int = 10,
        offset: int = 0,
        project_id: str | None = None,
        exclude_kb: bool = True,
        include_descendants: bool = False,
        search: str | None = None,
    ) -> tuple[list[dict], int]:
        """List instances with pagination.

        When ``include_descendants`` is True, pagination is root-based: only root
        instances (parent_id IS NULL or empty) are counted and paginated, and
        ALL descendants of each root in the current page are loaded via BFS and
        included in the flat result list.

        Args:
            limit: Maximum number of root instances to return (default: 10).
                When ``include_descendants=False``, this is the page size of all
                matching instances.
            offset: Number of root instances to skip (default: 0).
            project_id: Filter by project ID (optional).
            exclude_kb: Exclude KB-related instances (experiencer, kb-importer)
                when True (default: True).
            include_descendants: When True, paginate by root and BFS-load all
                descendants of each root in the current page (default: False).
            search: Optional case-insensitive substring filter against
                ``instance_metadata.title``, ``agent_name``, and ``agent_id``
                (default: None).

        Returns:
            Tuple of (list of instance info dictionaries, total count).
        """
        return self._lifecycle_service.list_instances(
            limit=limit,
            offset=offset,
            project_id=project_id,
            exclude_kb=exclude_kb,
            include_descendants=include_descendants,
            search=search,
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

    async def close_checkpointer(self) -> None:
        """Close the checkpointer adapter and release its underlying connections.

        Safe to call when ``self._checkpointer`` is ``None`` (e.g. the
        manager was constructed but ``initialize()`` was never awaited, or
        initialization failed). Delegates the close to the adapter's
        ``close()`` method, which is implemented for both SQLite and
        PostgreSQL backends.

        Idempotent: subsequent calls are no-ops because the adapter
        implementations are themselves defensive against missing conn
        attributes after first close.

        The checkpointer is closed *after* the maintenance service has
        stopped, so the in-flight checkpoint cleanup job is not
        interrupted while it is reading or deleting from the checkpoint
        database.
        """
        if not getattr(self, '_checkpointer', None):
            return
        try:
            await self._checkpointer.close()
            logger.info("Checkpointer adapter closed")
        except Exception as e:
            # Don't let close errors derail the rest of shutdown — the
            # underlying connections will be released by the interpreter
            # exit regardless.
            logger.warning(f"Error closing checkpointer adapter: {e}")
    
    async def shutdown(self, grace_period: float = 10.0) -> None:
        """Gracefully shutdown all manager components in order.
        
        This implements an ordered shutdown sequence:
        1. Set _shutting_down flag to reject new messages
        2. Stop accepting new messages via source registry
        3. Cancel active LLM streams via request registry
        4. Wait for in-flight processing to finish (grace period)
        5. Shutdown worker pool
        6. Shutdown event bus
        7. Shutdown maintenance service (stops the checkpoint cleanup job)
        8. Close the checkpointer adapter (SQLite/PostgreSQL connections)
        9. Drain MCP warm-up pool
        10. Shutdown MCP service (close all connections)
        11. Clean up resources (dispose database engine)
        
        Each step is wrapped in its own try/except so failures don't skip subsequent steps.
        
        Args:
            grace_period: Maximum seconds to wait for in-flight processing (default: 10s).
        """
        if self.is_shutting_down:
            logger.debug("Shutdown already in progress, skipping")
            return
        
        self._shutting_down = True
        logger.info("Starting graceful shutdown...")
        
        # FIX: C2 — Cancel warmup task if still running
        if self._warmup_task and not self._warmup_task.done():
            self._warmup_task.cancel()
            try:
                await self._warmup_task
            except asyncio.CancelledError:
                pass
        
        # Cancel all background cleanup tasks first
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
        self._background_tasks.clear()
        
        # Phase 1 (2026-07-19): kill all background processes BEFORE
        # tearing down workers / sources. Best-effort — failures must
        # never block the rest of shutdown. ``cleanup_all`` snapshots
        # per-instance buckets and calls ``cleanup_instance`` on each,
        # which is idempotent (atomic bucket pop). See
        # ``daemon/tools/proc_tools.py`` for the limitations
        # (``setsid`` orphans, crash-recovery leak).
        try:
            from daemon.tools.proc_tools import get_background_process_manager

            cleaned = await get_background_process_manager().cleanup_all()
            if cleaned:
                logger.info(
                    f"shutdown: killed background processes in "
                    f"{cleaned} instance(s)"
                )
        except Exception as e:
            logger.warning(
                f"shutdown: proc cleanup_all failed: "
                f"{type(e).__name__}: {e}"
            )

        # Phase 2 (2026-07-19): bash cleanup_all alongside proc cleanup_all.
        # Best-effort. See BashProcessRegistry docstring for known
        # limitations (``setsid`` orphans, crash-recovery leak).
        try:
            from daemon.tools.bash import get_bash_process_registry

            bash_killed = await get_bash_process_registry().cleanup_all()
            if bash_killed:
                logger.info(
                    f"shutdown: killed bash processes: {bash_killed}"
                )
        except Exception as e:
            logger.warning(
                f"shutdown: bash cleanup_all failed: "
                f"{type(e).__name__}: {e}"
            )

        steps = [
            ("stop_sources", self.stop_sources(timeout=grace_period)),
            ("cancel_active_requests", self._cancel_all_active_requests()),
            ("wait_inflight", self._wait_for_inflight(grace_period)),
            ("shutdown_worker_pool", asyncio.to_thread(self.shutdown_worker_pool)),
            ("shutdown_event_bus", self._event_bus.shutdown()),
            ("shutdown_maintenance_service", self._maintenance_service.stop() if self._maintenance_service else asyncio.sleep(0)),
            ("dispose_db_pools", self._db_pool_manager.dispose_all() if hasattr(self, '_db_pool_manager') else asyncio.sleep(0)),
            ("close_checkpointer", self.close_checkpointer()),
            ("drain_mcp_pool", self._drain_warmup_pool()),
            ("shutdown_mcp_service", self._mcp_service.close_all_connections()),
            ("shutdown_opencode_registry", self._shutdown_opencode_registry()),
        ]
        
        for name, step_coro in steps:
            try:
                await step_coro
            except Exception as e:
                logger.error(f"Error during shutdown step '{name}': {e}", exc_info=True)
        
        # Clean up resources (database disposal) - also resilient
        try:
            logger.info("Cleaning up resources...")
            self.cleanup()
        except Exception as e:
            logger.error(f"Error during shutdown step 'cleanup': {e}", exc_info=True)
        
        logger.info("Graceful shutdown complete")
    
    async def _cancel_all_active_requests(self) -> None:
        """Cancel all active requests in the registry with SHUTDOWN reason."""
        return await self._cancellation_service._cancel_all_active_requests()

    async def _shutdown_opencode_registry(self) -> None:
        """Shutdown the opencode session registry during daemon shutdown.

        Stops all running session managers, clears the in-memory map, and
        disposes the dedicated opencode engine to release file handles and
        finalize the SQLite WAL checkpoint. Errors are logged but not raised
        so the rest of the shutdown sequence can continue.
        """
        if hasattr(self, "_opencode_registry") and self._opencode_registry:
            try:
                await self._opencode_registry.shutdown()
            except Exception as exc:
                logger.warning(f"Error during opencode registry shutdown: {exc}")

        # Dispose the dedicated opencode engine to release resources.
        # create_engine_from_config returns a sync Engine, so dispose() is sync.
        # On SQLite this releases file handles and lets the WAL checkpoint
        # finalize, avoiding "database is locked" on the next start.
        # On PostgreSQL this returns pooled connections to the pool.
        if hasattr(self, "_opencode_engine") and self._opencode_engine:
            try:
                self._opencode_engine.dispose()
            except Exception as exc:
                logger.warning(f"Error disposing opencode engine: {exc}")
    
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
